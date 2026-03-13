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
    CONF_DEVICES, CONF_MODE, MODE_AUTO, MODE_OFF,
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
        self.scoring_engine = ScoringEngine(entry.data)
        self.battery_strategy = BatteryStrategy(entry.data)
        self.device_manager = DeviceManager(hass, entry.data.get(CONF_DEVICES, []))

        # Latest computed state — exposed to sensor/switch entities
        self.pv_power_w: float = 0.0
        self.grid_power_w: float = 0.0
        self.house_power_w: float = 0.0
        self.surplus_w: float = 0.0
        self.battery_soc: float | None = None
        self.tempo_color: str | None = None
        self.global_score: float = 0.0
        self.battery_action: str = "idle"   # "charge" | "discharge" | "idle" | "reserve"
        self.mode: str = entry.data.get(CONF_MODE, MODE_AUTO)

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
                await self.device_manager.async_dispatch(self.hass, score_input)

            return self._snapshot()

        except Exception as err:
            raise UpdateFailed(f"Energy Optimizer update failed: {err}") from err

    # ------------------------------------------------------------------
    # Sensor reading
    # ------------------------------------------------------------------
    async def _read_sensors(self) -> dict[str, Any]:
        """Read all configured input entities from hass state machine."""
        def _state_float(entity_id: str | None) -> float:
            if not entity_id:
                return 0.0
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                return 0.0
            try:
                return float(state.state)
            except ValueError:
                return 0.0

        def _state_str(entity_id: str | None) -> str | None:
            if not entity_id:
                return None
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                return None
            return state.state

        cfg = self.entry.data
        return {
            "pv_power_w": _state_float(cfg.get(CONF_PV_POWER_ENTITY)),
            "grid_power_w": _state_float(cfg.get(CONF_GRID_POWER_ENTITY)),
            "house_power_w": _state_float(cfg.get(CONF_HOUSE_POWER_ENTITY)),
            "battery_soc": _state_float(cfg.get(CONF_BATTERY_SOC_ENTITY))
                           if cfg.get(CONF_BATTERY_ENABLED) else None,
            "tempo_color": _state_str(cfg.get(CONF_TEMPO_COLOR_ENTITY)),
        }

    def _update_state(self, raw: dict[str, Any]) -> None:
        self.pv_power_w = raw["pv_power_w"]
        self.grid_power_w = raw["grid_power_w"]
        self.house_power_w = raw["house_power_w"]
        self.battery_soc = raw["battery_soc"]
        self.tempo_color = raw["tempo_color"]
        # Surplus = production − house consumption (positive = available)
        self.surplus_w = max(0.0, self.pv_power_w - self.house_power_w)

    def _build_score_input(self) -> dict[str, Any]:
        return {
            "pv_power_w": self.pv_power_w,
            "surplus_w": self.surplus_w,
            "grid_power_w": self.grid_power_w,
            "battery_soc": self.battery_soc,
            "tempo_color": self.tempo_color,
        }

    def _snapshot(self) -> dict[str, Any]:
        return {
            "pv_power_w": self.pv_power_w,
            "surplus_w": self.surplus_w,
            "battery_soc": self.battery_soc,
            "tempo_color": self.tempo_color,
            "global_score": self.global_score,
            "battery_action": self.battery_action,
            "mode": self.mode,
        }
