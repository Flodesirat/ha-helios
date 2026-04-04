"""DataUpdateCoordinator — orchestrates all polling and decision logic."""
from __future__ import annotations

import logging
import time as _time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_EMA_ENABLED, DEFAULT_EMA_ENABLED,
    CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL,
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY, CONF_FORECAST_ENTITY,
    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_DEVICES, CONF_MODE, CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD,
    CONF_GRID_ALLOWANCE_W, DEFAULT_GRID_ALLOWANCE_W,
    CONF_EMA_ALPHA, DEFAULT_EMA_ALPHA,
    MODE_AUTO, MODE_OFF,
    BATTERY_ACTION_AUTOCONSOMMATION,
    normalize_tempo_color,
    CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN,
    TEMPO_RED,
    STORAGE_KEY_OPTIMIZER, STORAGE_VERSION,
)
from .scoring_engine import ScoringEngine
from .battery_strategy import BatteryStrategy
from .consumption_learner import ConsumptionLearner
from .device_manager import DeviceManager
from .managed_device import ManagedDevice
from .daily_optimizer import async_run_daily_optimization

_LOGGER = logging.getLogger(__name__)


class EnergyOptimizerCoordinator(DataUpdateCoordinator):
    """Central coordinator: reads sensors → scores → decisions → actions."""

    @property
    def config(self) -> dict:
        """Effective config: entry.data merged with entry.options (options win).

        The initial config flow writes to entry.data; the options flow writes to
        entry.options.  Always reading the merged dict ensures reconfigured values
        are picked up without requiring a full reinstall.
        """
        return {**self.entry.data, **self.entry.options}

    @property
    def _cfg(self) -> dict:
        return self.config

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        cfg = self._cfg
        interval = cfg.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.scoring_engine    = ScoringEngine(cfg)
        self.battery_strategy  = BatteryStrategy(cfg)
        devices = cfg.get(CONF_DEVICES, [])
        self.device_manager    = DeviceManager(hass, devices, cfg)
        self.device_manager._coordinator = self
        ema_alpha = float(cfg.get(CONF_EMA_ALPHA, DEFAULT_EMA_ALPHA))
        self.consumption_learner = ConsumptionLearner(hass, entry.entry_id, alpha=ema_alpha)
        self._optimizer_store  = Store(hass, STORAGE_VERSION, STORAGE_KEY_OPTIMIZER)
        self.dispatch_threshold: float = float(
            cfg.get(CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD)
        )
        self.grid_allowance_w: float = float(
            cfg.get(CONF_GRID_ALLOWANCE_W, DEFAULT_GRID_ALLOWANCE_W)
        )

        # Startup guard: skip dispatch until entities have had time to stabilise.
        # The first HA update cycle fires immediately at load, before entities are
        # available. We wait one full scan interval (min 5 min) before dispatching.
        _warmup = max(5, interval)
        self._dispatch_ready_at: float = _time.monotonic() + _warmup * 60

        # Latest computed state — exposed to sensor/switch entities
        self.pv_power_w:      float       = 0.0
        self.grid_power_w:    float       = 0.0
        self.house_power_w:   float       = 0.0
        self.surplus_w:         float       = 0.0
        self.virtual_surplus_w: float       = 0.0
        self.bat_available_w:   float       = 0.0
        self.battery_soc:     float | None = None
        self.battery_power_w: float | None = None  # negative=charge, positive=discharge
        self.tempo_color:      str | None  = None
        self.tempo_next_color: str | None  = None
        self.global_score:    float       = 0.0
        self.battery_action:  str         = BATTERY_ACTION_AUTOCONSOMMATION
        self.forecast_kwh:       float | None = None
        self.mode:               str         = cfg.get(CONF_MODE, MODE_AUTO)
        self.optimizer_last_run: str | None  = None   # ISO timestamp set by daily_optimizer
        self.optimizer_context:          dict              = {}
        self.optimizer_top20:            list[dict]        = []
        self.optimizer_chosen:           dict              = {}
        self.optimizer_chosen_schedule:  list[dict]        = []

        # Daily optimizer — scheduled at 05:00 every morning
        self._unsub_daily_opt = async_track_time_change(
            hass,
            self._async_daily_optimize,
            hour=5,
            minute=0,
            second=0,
        )

    # ------------------------------------------------------------------
    # Daily optimizer callback
    # ------------------------------------------------------------------
    async def _async_daily_optimize(self, now) -> None:  # noqa: ANN001
        """Triggered at 05:00 every morning to recompute optimal scoring weights."""
        try:
            await async_run_daily_optimization(self.hass, self)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Helios daily optimizer failed: %s", err)

    def async_unload(self) -> None:
        """Cancel recurring scheduler and appliance listeners when the entry is unloaded."""
        if self._unsub_daily_opt:
            self._unsub_daily_opt()
            self._unsub_daily_opt = None
        self.device_manager.async_unload()

    # ------------------------------------------------------------------
    # Optimizer state persistence
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Restore persisted optimizer state (weights, threshold, diagnostics)."""
        data: dict = await self._optimizer_store.async_load() or {}
        if not data:
            return

        scoring = data.get("scoring")
        if scoring:
            self.scoring_engine.update_weights(scoring)
            _LOGGER.debug("Helios: restored optimizer scoring weights from storage")

        threshold = data.get("dispatch_threshold")
        if threshold is not None:
            self.dispatch_threshold = float(threshold)
            _LOGGER.debug("Helios: restored dispatch_threshold=%.2f from storage", self.dispatch_threshold)

        self.optimizer_last_run         = data.get("optimizer_last_run")
        self.optimizer_context          = data.get("optimizer_context") or {}
        self.optimizer_chosen           = data.get("optimizer_chosen") or {}
        self.optimizer_top20            = data.get("optimizer_top20") or []
        self.optimizer_chosen_schedule  = data.get("optimizer_chosen_schedule") or []

        if self.optimizer_last_run:
            _LOGGER.info(
                "Helios: optimizer state restored (last run: %s)", self.optimizer_last_run
            )

    async def async_save_optimizer_state(self) -> None:
        """Persist current optimizer results so they survive a restart."""
        await self._optimizer_store.async_save({
            "optimizer_last_run":        self.optimizer_last_run,
            "scoring":                   self.scoring_engine.get_weights(),
            "dispatch_threshold":        self.dispatch_threshold,
            "optimizer_context":         self.optimizer_context,
            "optimizer_chosen":          self.optimizer_chosen,
            "optimizer_top20":           self.optimizer_top20,
            "optimizer_chosen_schedule": self.optimizer_chosen_schedule,
        })

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

            # Skip scoring and dispatch until entities have stabilised after startup.
            if _time.monotonic() < self._dispatch_ready_at:
                _LOGGER.debug(
                    "Helios: warmup period — sensors read but dispatch skipped "
                    "(%.0f s remaining)",
                    self._dispatch_ready_at - _time.monotonic(),
                )
                return self._snapshot()

            score_input = self._build_score_input()
            self.virtual_surplus_w = score_input.get("surplus_w", 0.0)
            self.global_score = self.scoring_engine.compute(score_input)

            if self._cfg.get(CONF_BATTERY_ENABLED):
                self.battery_action = self.battery_strategy.decide(score_input)
                await self.battery_strategy.async_apply(self.hass, self.battery_action)

            if self.mode == MODE_AUTO:
                dispatch_input = {
                    **score_input,
                    "global_score":       self.global_score,
                    "bat_available_w":    self.bat_available_w,
                    "dispatch_threshold": self.dispatch_threshold,
                    "grid_allowance_w":   self.grid_allowance_w,
                    "house_power_w":      self.house_power_w,
                    "soc_reserve_rouge":  float(self._cfg.get(
                        CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE
                    )),
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

        cfg = self._cfg
        battery_enabled = cfg.get(CONF_BATTERY_ENABLED, False)

        return {
            "pv_power_w":   _float(cfg.get(CONF_PV_POWER_ENTITY)),
            "grid_power_w": _float(cfg.get(CONF_GRID_POWER_ENTITY)),
            "house_power_w": _float(cfg.get(CONF_HOUSE_POWER_ENTITY)),
            "battery_soc":   _float(cfg.get(CONF_BATTERY_SOC_ENTITY)) if battery_enabled else None,
            "battery_power_w": _float(cfg.get(CONF_BATTERY_POWER_ENTITY)) if battery_enabled else None,
            "tempo_color":      normalize_tempo_color(_str(cfg.get(CONF_TEMPO_COLOR_ENTITY))),
            "tempo_next_color": normalize_tempo_color(_str(cfg.get(CONF_TEMPO_NEXT_COLOR_ENTITY))),
            "forecast_kwh": _float(entity) if (entity := cfg.get(CONF_FORECAST_ENTITY)) else None,
        }

    def _update_state(self, raw: dict[str, Any]) -> None:
        self.pv_power_w    = raw["pv_power_w"]
        self.grid_power_w  = raw["grid_power_w"]
        self.house_power_w = raw["house_power_w"]
        self.battery_soc     = raw["battery_soc"]
        self.battery_power_w = raw["battery_power_w"]
        self.tempo_color      = raw["tempo_color"]
        self.tempo_next_color = raw["tempo_next_color"]
        self.forecast_kwh  = raw["forecast_kwh"]
        # Surplus = PV production − house consumption (floored at 0)
        self.surplus_w     = max(0.0, self.pv_power_w - self.house_power_w)
        # Battery discharge headroom available for device dispatch
        self.bat_available_w = self._compute_bat_available_w()

        # EMA update: net base load = house_w − currently-active Helios devices.
        # Use actual_power_w so a water heater whose internal thermostat has cut
        # (switch ON but 0 W draw) doesn't distort the base load estimate.
        if self._cfg.get(CONF_EMA_ENABLED, DEFAULT_EMA_ENABLED):
            reader = ManagedDevice._make_ha_reader(self.hass)
            helios_devices_w = sum(
                d.actual_power_w(reader) for d in self.device_manager.devices if d.is_on
            )
            net_base_w = self.house_power_w - helios_devices_w
            now = dt_util.now()
            slot = (now.hour * 60 + now.minute) // 5
            self.consumption_learner.update(slot, net_base_w)
            self.consumption_learner.schedule_save()

    def _compute_bat_available_w(self) -> float:
        """Estimate how much power the battery can contribute to device loads.

        Based on usable SOC above the reserve threshold, capped by the
        inverter's configured max discharge power.
        """
        cfg = self._cfg
        if not cfg.get(CONF_BATTERY_ENABLED):
            return 0.0
        soc = self.battery_soc
        if soc is None:
            return 0.0

        # On red days protect the battery above soc_reserve_rouge.
        # On blue/white days use the normal soc_min floor so the full
        # usable capacity is available for dispatch.
        if self.tempo_color == TEMPO_RED:
            soc_floor = cfg.get(CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE)
        else:
            soc_floor = cfg.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN)

        if soc <= soc_floor:
            return 0.0

        capacity_kwh    = cfg.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        max_discharge_w = cfg.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W, 0.0)

        # Usable energy above floor, assuming ≤ 2 h discharge window → W
        usable_fraction = (soc - soc_floor) / 100.0
        energy_based_w  = usable_fraction * capacity_kwh * 500  # kWh × 500 → W over 2 h

        capacity_w = min(energy_based_w, max_discharge_w) if max_discharge_w > 0 else energy_based_w

        # Deduct power the battery is already discharging to the house so we
        # don't double-count headroom that is already consumed.
        current_discharge_w = max(0.0, self.battery_power_w or 0.0)
        return max(0.0, capacity_w - current_discharge_w)

    def _build_score_input(self) -> dict[str, Any]:
        # Virtual surplus: add back the power of Helios-managed devices currently ON.
        # Without this correction, active devices inflate house_w → deflate surplus_w →
        # score drops below threshold → gate block turns them off → chattering.
        reader = ManagedDevice._make_ha_reader(self.hass)
        helios_on_w = sum(
            d.actual_power_w(reader)
            for d in self.device_manager.devices
            if d.is_on
        )
        virtual_surplus_w = max(0.0, self.surplus_w + helios_on_w)
        return {
            "pv_power_w":       self.pv_power_w,
            "surplus_w":        virtual_surplus_w,
            "grid_power_w":     self.grid_power_w,
            "battery_soc":      self.battery_soc,
            "tempo_color":      self.tempo_color,
            "tempo_next_color": self.tempo_next_color,
            "forecast_kwh":     self.forecast_kwh,
            "hour":             dt_util.now().hour,
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
            "forecast_kwh":    self.forecast_kwh,
            "global_score":    self.global_score,
            "battery_action":  self.battery_action,
            "mode":            self.mode,
        }
