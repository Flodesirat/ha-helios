"""DataUpdateCoordinator — orchestrates all polling and decision logic."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL,
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_TEMPO_COLOR_ENTITY,
    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_DEVICES, CONF_MODE, MODE_AUTO, MODE_OFF,
    BATTERY_ACTION_AUTOCONSOMMATION,
)
from .scoring_engine import ScoringEngine
from .battery_strategy import BatteryStrategy
from .device_manager import DeviceManager

_LOGGER = logging.getLogger(__name__)


class EnergyOptimizerCoordinator(DataUpdateCoordinator):
    """Central coordinator: reads sensors → scores → decisions → actions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        interval = entry.data.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.scoring_engine   = ScoringEngine(entry.data)
        self.battery_strategy = BatteryStrategy(entry.data)
        devices = entry.options.get(CONF_DEVICES, entry.data.get(CONF_DEVICES, []))
        self.device_manager   = DeviceManager(hass, devices, entry.data)

        # Latest computed state — exposed to sensor/switch entities
        self.pv_power_w:      float       = 0.0
        self.grid_power_w:    float       = 0.0
        self.house_power_w:   float       = 0.0
        self.surplus_w:       float       = 0.0
        self.bat_available_w: float       = 0.0
        self.battery_soc:     float | None = None
        self.tempo_color:     str | None  = None
        self.global_score:    float       = 0.0
        self.battery_action:  str         = BATTERY_ACTION_AUTOCONSOMMATION
        self.mode:            str         = entry.data.get(CONF_MODE, MODE_AUTO)

    # ------------------------------------------------------------------
    # Main update cycle
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, Any]:
        """Called every scan_interval. Read → score → act."""
        try:
            raw = await self._read_sensors()
            self._update_state(raw)

            if self.mode == MODE_OFF:
                return self._snapshot()

            score_input = self._build_score_input()
            self.global_score = self.scoring_engine.compute(score_input)

            if self.entry.data.get(CONF_BATTERY_ENABLED):
                self.battery_action = self.battery_strategy.decide(score_input)
                await self.battery_strategy.async_apply(self.hass, self.battery_action)

            if self.mode == MODE_AUTO:
                dispatch_input = {
                    **score_input,
                    "global_score":    self.global_score,
                    "bat_available_w": self.bat_available_w,
                }
                await self.device_manager.async_dispatch(self.hass, dispatch_input)

            return self._snapshot()

        except Exception as err:
            raise UpdateFailed(f"Helios update failed: {err}") from err

    # ------------------------------------------------------------------
    # Sensor reading
    # ------------------------------------------------------------------
    async def _read_sensors(self) -> dict[str, Any]:
        """Read all configured input entities from hass state machine."""
        def _float(entity_id: str | None) -> float:
            if not entity_id:
                return 0.0
            s = self.hass.states.get(entity_id)
            if s is None or s.state in ("unavailable", "unknown"):
                return 0.0
            try:
                return float(s.state)
            except ValueError:
                return 0.0

        def _str(entity_id: str | None) -> str | None:
            if not entity_id:
                return None
            s = self.hass.states.get(entity_id)
            if s is None or s.state in ("unavailable", "unknown"):
                return None
            return s.state

        cfg = self.entry.data
        battery_enabled = cfg.get(CONF_BATTERY_ENABLED, False)

        return {
            "pv_power_w":   _float(cfg.get(CONF_PV_POWER_ENTITY)),
            "grid_power_w": _float(cfg.get(CONF_GRID_POWER_ENTITY)),
            "house_power_w": _float(cfg.get(CONF_HOUSE_POWER_ENTITY)),
            "battery_soc":  _float(cfg.get(CONF_BATTERY_SOC_ENTITY)) if battery_enabled else None,
            "tempo_color":  _str(cfg.get(CONF_TEMPO_COLOR_ENTITY)),
        }

    def _update_state(self, raw: dict[str, Any]) -> None:
        self.pv_power_w    = raw["pv_power_w"]
        self.grid_power_w  = raw["grid_power_w"]
        self.house_power_w = raw["house_power_w"]
        self.battery_soc   = raw["battery_soc"]
        self.tempo_color   = raw["tempo_color"]
        # Surplus = PV production − house consumption (floored at 0)
        self.surplus_w     = max(0.0, self.pv_power_w - self.house_power_w)
        # Battery discharge headroom available for device dispatch
        self.bat_available_w = self._compute_bat_available_w()

    def _compute_bat_available_w(self) -> float:
        """Estimate how much power the battery can contribute to device loads.

        Based on usable SOC above the reserve threshold, capped by the
        inverter's configured max discharge power.
        """
        cfg = self.entry.data
        if not cfg.get(CONF_BATTERY_ENABLED):
            return 0.0
        soc = self.battery_soc
        if soc is None:
            return 0.0

        soc_reserve  = cfg.get(CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE)
        if soc <= soc_reserve:
            return 0.0

        capacity_kwh    = cfg.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        max_discharge_w = cfg.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W, 0.0)

        # Usable energy above reserve, assuming ≤ 2 h discharge window → W
        usable_fraction = (soc - soc_reserve) / 100.0
        energy_based_w  = usable_fraction * capacity_kwh * 500  # kWh × 500 → W over 2 h

        if max_discharge_w > 0:
            return min(energy_based_w, max_discharge_w)
        return energy_based_w

    def _build_score_input(self) -> dict[str, Any]:
        return {
            "pv_power_w":    self.pv_power_w,
            "surplus_w":     self.surplus_w,
            "grid_power_w":  self.grid_power_w,
            "battery_soc":   self.battery_soc,
            "tempo_color":   self.tempo_color,
        }

    def _snapshot(self) -> dict[str, Any]:
        return {
            "pv_power_w":      self.pv_power_w,
            "grid_power_w":    self.grid_power_w,
            "house_power_w":   self.house_power_w,
            "surplus_w":       self.surplus_w,
            "bat_available_w": self.bat_available_w,
            "battery_soc":     self.battery_soc,
            "tempo_color":     self.tempo_color,
            "global_score":    self.global_score,
            "battery_action":  self.battery_action,
            "mode":            self.mode,
        }
