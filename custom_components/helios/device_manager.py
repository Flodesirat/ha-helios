"""Device manager — full dispatch engine with per-device scoring."""
from __future__ import annotations

import logging
import time as time_mod
from collections import deque
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    # Device types
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC,
    DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_POOL,
    # Common device config
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_DEVICE_INTERRUPTIBLE, CONF_DEVICE_MUST_RUN_DAILY, CONF_DEVICE_DEADLINE,
    CONF_DEVICE_WEIGHT_PRIORITY, CONF_DEVICE_WEIGHT_FIT, CONF_DEVICE_WEIGHT_URGENCY,
    # EV
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_EV_DEPARTURE_TIME, CONF_EV_MIN_CHARGE_POWER_W, CONF_EV_BATTERY_CAPACITY_WH,
    CONF_EV_CHARGE_START_SCRIPT, CONF_EV_CHARGE_STOP_SCRIPT,
    # Water heater
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN, CONF_WH_TEMP_MIN_ENTITY,
    CONF_WH_POWER_ENTITY, CONF_WH_OFF_PEAK_HYSTERESIS_K,
    # HVAC
    CONF_HVAC_TEMP_ENTITY, CONF_HVAC_SETPOINT_ENTITY,
    CONF_HVAC_MODE, CONF_HVAC_HYSTERESIS_K, CONF_HVAC_MIN_OFF_MINUTES,
    HVAC_MODE_HEAT, HVAC_MODE_COOL,
    # Pool
    CONF_POOL_FILTRATION_ENTITY, CONF_POOL_SPLIT_SESSIONS,
    # Appliance
    CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_PREPARE_SCRIPT,
    CONF_APPLIANCE_START_SCRIPT, CONF_APPLIANCE_POWER_ENTITY,
    CONF_APPLIANCE_POWER_THRESHOLD_W, CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    APPLIANCE_STATE_IDLE, APPLIANCE_STATE_READY, APPLIANCE_STATE_PREPARING,
    APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_DONE,
    # Off-peak slots
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
    # General
    CONF_SCAN_INTERVAL_MINUTES, CONF_DISPATCH_THRESHOLD,
    # Battery reserve (used in dispatch guard)
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    TEMPO_RED,
    # Defaults
    DEFAULT_DEVICE_PRIORITY, DEFAULT_DEVICE_MIN_ON_MINUTES,
    DEFAULT_ALLOWED_START, DEFAULT_ALLOWED_END,
    DEFAULT_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_URGENCY,
    DEFAULT_EV_SOC_TARGET, DEFAULT_EV_MIN_CHARGE_POWER_W,
    DEFAULT_WH_TEMP_TARGET, DEFAULT_WH_TEMP_MIN, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K,
    DEFAULT_HVAC_HYSTERESIS_K, DEFAULT_HVAC_MIN_OFF_MINUTES, HVAC_MODE_HEAT,
    DEFAULT_POOL_SPLIT_SESSIONS,
    DEFAULT_APPLIANCE_POWER_THRESHOLD_W, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES,
    DEFAULT_SCAN_INTERVAL, DEFAULT_DISPATCH_THRESHOLD,
    STORAGE_KEY, STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# How long (seconds) power must stay below threshold to confirm cycle ended
_APPLIANCE_LOW_POWER_CONFIRM_S = 180

# Pool must_run: only force filtration in the last N hours of the day.
# Before this window the day is still open — solar may yet provide the needed energy.
_POOL_MUST_RUN_WINDOW_H = 8  # hours before midnight (default: fires after 16:00)


def _parse_time(value: str | None) -> time | None:
    """Parse a 'HH:MM' or 'HH:MM:SS' string into a time object, return None on failure."""
    if not value:
        return None
    try:
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, AttributeError, IndexError):
        return None


def _parse_off_peak_slots(cfg: dict) -> list[tuple[time, time]]:
    """Return a list of (start, end) time pairs for off-peak slots (0, 1, or 2 entries)."""
    slots = []
    for start_key, end_key in (
        (CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END),
        (CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END),
    ):
        start = _parse_time(cfg.get(start_key))
        end   = _parse_time(cfg.get(end_key))
        if start is not None and end is not None:
            slots.append((start, end))
    return slots


def _is_in_slot(now: time, start: time, end: time) -> bool:
    """Return True if *now* falls within [start, end), handling midnight crossing."""
    if start <= end:
        return start <= now < end
    # Crosses midnight: e.g. 22:00 → 06:00
    return now >= start or now < end


class ManagedDevice:
    """Represents one controllable device with its full configuration."""

    def __init__(self, config: dict[str, Any], global_cfg: dict[str, Any] | None = None) -> None:
        # ---- Common ----
        self.name: str          = config[CONF_DEVICE_NAME]
        self.device_type: str   = config[CONF_DEVICE_TYPE]
        self.switch_entity: str | None = config.get(CONF_DEVICE_SWITCH_ENTITY)
        self.power_w: float     = float(config.get(CONF_DEVICE_POWER_W, 0))
        self.priority: int      = int(config.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY))
        self.min_on_minutes: int = int(config.get(CONF_DEVICE_MIN_ON_MINUTES, DEFAULT_DEVICE_MIN_ON_MINUTES))
        self.allowed_start: str = config.get(CONF_DEVICE_ALLOWED_START, DEFAULT_ALLOWED_START)
        self.allowed_end: str   = config.get(CONF_DEVICE_ALLOWED_END,   DEFAULT_ALLOWED_END)
        self.must_run_daily: bool = bool(config.get(CONF_DEVICE_MUST_RUN_DAILY, False))
        self.deadline: str | None = config.get(CONF_DEVICE_DEADLINE)

        # Interruptible is derived from device type (explicit override allowed)
        _interruptible_default = (self.device_type != DEVICE_TYPE_APPLIANCE)
        self.interruptible: bool = bool(config.get(CONF_DEVICE_INTERRUPTIBLE, _interruptible_default))

        # ---- Per-device dispatch weights ----
        self.w_priority: float = float(config.get(CONF_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_PRIORITY))
        self.w_fit: float      = float(config.get(CONF_DEVICE_WEIGHT_FIT,      DEFAULT_DEVICE_WEIGHT_FIT))
        self.w_urgency: float  = float(config.get(CONF_DEVICE_WEIGHT_URGENCY,  DEFAULT_DEVICE_WEIGHT_URGENCY))

        # ---- EV ----
        self.ev_soc_entity: str | None       = config.get(CONF_EV_SOC_ENTITY)
        self.ev_plugged_entity: str | None   = config.get(CONF_EV_PLUGGED_ENTITY)
        self.ev_soc_target: float            = float(config.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET))
        self.ev_departure_time: str | None   = config.get(CONF_EV_DEPARTURE_TIME)
        self.ev_min_charge_power_w: float    = float(config.get(CONF_EV_MIN_CHARGE_POWER_W, DEFAULT_EV_MIN_CHARGE_POWER_W))
        self.ev_battery_capacity_wh: float | None = (
            float(config[CONF_EV_BATTERY_CAPACITY_WH]) if config.get(CONF_EV_BATTERY_CAPACITY_WH) else None
        )
        self.ev_charge_start_script: str | None = config.get(CONF_EV_CHARGE_START_SCRIPT)
        self.ev_charge_stop_script: str | None  = config.get(CONF_EV_CHARGE_STOP_SCRIPT)

        # ---- Water heater ----
        self.wh_temp_entity: str | None     = config.get(CONF_WH_TEMP_ENTITY)
        self.wh_temp_target: float          = float(config.get(CONF_WH_TEMP_TARGET, DEFAULT_WH_TEMP_TARGET))
        self.wh_temp_min: float             = float(config.get(CONF_WH_TEMP_MIN,    DEFAULT_WH_TEMP_MIN))
        self.wh_temp_min_entity: str | None = config.get(CONF_WH_TEMP_MIN_ENTITY)
        self.wh_power_entity: str | None    = config.get(CONF_WH_POWER_ENTITY)
        self.wh_off_peak_hysteresis_k: float = float(
            config.get(CONF_WH_OFF_PEAK_HYSTERESIS_K, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K)
        )

        # ---- Off-peak slots (from global config) ----
        gcfg = global_cfg or {}
        self._off_peak_slots: list[tuple[time, time]] = _parse_off_peak_slots(gcfg)

        # ---- HVAC ----
        self.hvac_temp_entity: str | None     = config.get(CONF_HVAC_TEMP_ENTITY)
        self.hvac_setpoint_entity: str | None = config.get(CONF_HVAC_SETPOINT_ENTITY)
        self.hvac_mode: str                   = config.get(CONF_HVAC_MODE, HVAC_MODE_HEAT)
        self.hvac_hysteresis_k: float         = float(config.get(CONF_HVAC_HYSTERESIS_K, DEFAULT_HVAC_HYSTERESIS_K))
        self.hvac_min_off_minutes: int        = int(config.get(CONF_HVAC_MIN_OFF_MINUTES, DEFAULT_HVAC_MIN_OFF_MINUTES))

        # ---- Pool ----
        self.pool_filtration_entity: str | None = config.get(CONF_POOL_FILTRATION_ENTITY)
        self.pool_split_sessions: bool          = bool(config.get(CONF_POOL_SPLIT_SESSIONS, DEFAULT_POOL_SPLIT_SESSIONS))

        # ---- Appliance ----
        self.appliance_ready_entity: str | None   = config.get(CONF_APPLIANCE_READY_ENTITY)
        self.appliance_prepare_script: str | None = config.get(CONF_APPLIANCE_PREPARE_SCRIPT)
        self.appliance_start_script: str | None   = config.get(CONF_APPLIANCE_START_SCRIPT)
        self.appliance_power_entity: str | None   = config.get(CONF_APPLIANCE_POWER_ENTITY)
        self.appliance_power_threshold_w: float   = float(config.get(
            CONF_APPLIANCE_POWER_THRESHOLD_W, DEFAULT_APPLIANCE_POWER_THRESHOLD_W
        ))
        self.appliance_cycle_duration_minutes: int = int(config.get(
            CONF_APPLIANCE_CYCLE_DURATION_MINUTES, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES
        ))

        # ---- Runtime state ----
        self.is_on: bool                      = False
        self.turned_on_at: float | None       = None  # epoch seconds
        self.turned_off_at: float | None      = None
        self.manual_mode: bool                = False  # True → Helios hands off entirely

        # Pool — daily run tracking (persisted externally)
        self.pool_daily_run_minutes: float       = 0.0
        self.pool_last_date: date | None         = None
        # Snapshot of required filtration minutes taken at 05:00 — stable all day
        self.pool_required_minutes_today: float | None = None

        # EV — manual plugged state (used when no ev_plugged_entity is configured)
        self.ev_plugged_manual: bool           = False

        # Pool — force mode (set by PoolForceSwitch entity)
        self.pool_force_until: float | None    = None   # epoch seconds — forced ON
        self.pool_inhibit_until: float | None  = None   # epoch seconds — forced OFF (optimizer blocked)
        self.pool_force_duration_h: float      = 2.0    # currently selected duration

        # Appliance state machine
        self.appliance_state: str             = APPLIANCE_STATE_IDLE
        self.appliance_cycle_start: float | None = None
        self.appliance_low_power_since: float | None = None

    # ------------------------------------------------------------------
    # Allowed time window
    # ------------------------------------------------------------------
    def is_in_allowed_window(self, now: time) -> bool:
        """True if *now* falls within [allowed_start, allowed_end]."""
        start = _parse_time(self.allowed_start)
        end   = _parse_time(self.allowed_end)
        if start is None or end is None:
            return True

        if start <= end:
            return start <= now <= end
        # Overnight window (e.g. 22:00–06:00)
        return now >= start or now <= end

    # ------------------------------------------------------------------
    # Actual power — uses measured entity when available, else nominal
    # ------------------------------------------------------------------
    def actual_power_w(self, hass: HomeAssistant) -> float:
        """Return current power draw in W.

        For water heaters a power entity can be configured: the heating
        element shuts off internally when temperature is reached, so the
        measured value can be 0 W even while the switch is ON.  Using the
        real reading avoids over-estimating the dispatch budget.
        All other device types fall back to the nominal power_w.
        """
        if self.device_type == DEVICE_TYPE_WATER_HEATER and self.wh_power_entity:
            return self._state_float(hass, self.wh_power_entity)
        return self.power_w

    # ------------------------------------------------------------------
    # Off-peak detection (water heater)
    # ------------------------------------------------------------------
    def _is_off_peak(self, now: time) -> bool:
        """Return True if *now* falls in any configured off-peak slot."""
        return any(_is_in_slot(now, s, e) for s, e in self._off_peak_slots)

    def _minutes_to_off_peak_end(self, now: time) -> float | None:
        """Return minutes remaining until the end of the current off-peak slot.

        Works purely with time-of-day arithmetic (no datetime objects) so it
        is safe to call with a plain time value obtained from datetime.now().time().
        Returns None if *now* is not inside any off-peak slot.
        Handles midnight-crossing slots (e.g. 22:00 → 06:00).
        """
        def _mins(t: time) -> int:
            return t.hour * 60 + t.minute

        now_m = _mins(now)
        for start, end in self._off_peak_slots:
            if not _is_in_slot(now, start, end):
                continue
            end_m = _mins(end)
            if start <= end:
                # Same-day slot
                return float(end_m - now_m)
            # Midnight-crossing slot (e.g. 22:00 → 06:00)
            if now >= start:
                # Evening side: wrap around midnight
                return float((24 * 60 - now_m) + end_m)
            # Morning side
            return float(end_m - now_m)
        return None

    def _wh_off_peak_min(self, hass: HomeAssistant) -> float:
        """Minimum temperature to reach during off-peak hours.

        Reads the configured entity; falls back to the static legionella floor.
        """
        if self.wh_temp_min_entity:
            val = self._state_float(hass, self.wh_temp_min_entity, fallback=self.wh_temp_min)
            return val
        return self.wh_temp_min

    # ------------------------------------------------------------------
    # Satisfaction — has the device reached its target?
    # ------------------------------------------------------------------
    def is_satisfied(self, hass: HomeAssistant) -> bool:
        if self.device_type == DEVICE_TYPE_EV:
            if self.ev_plugged_entity:
                plugged = self._state_bool(hass, self.ev_plugged_entity, fallback=True)
            else:
                plugged = self.ev_plugged_manual
            if not plugged:
                return True  # car not plugged → nothing to do
            return self._state_float(hass, self.ev_soc_entity) >= self.ev_soc_target

        if self.device_type == DEVICE_TYPE_WATER_HEATER:
            temp = self._state_float(hass, self.wh_temp_entity)
            if self._is_off_peak(datetime.now().time()):
                # During off-peak: satisfied when the off-peak minimum is reached.
                # must_run_now() forced us here; once the target is met we stop.
                return temp >= self._wh_off_peak_min(hass)
            return temp >= self.wh_temp_target

        if self.device_type == DEVICE_TYPE_HVAC:
            current  = self._state_float(hass, self.hvac_temp_entity)
            setpoint = self._state_float(hass, self.hvac_setpoint_entity)
            if self.hvac_mode == HVAC_MODE_HEAT:
                return current >= setpoint - self.hvac_hysteresis_k
            return current <= setpoint + self.hvac_hysteresis_k  # cool

        if self.device_type == DEVICE_TYPE_POOL:
            return self.pool_daily_run_minutes >= self._pool_required_minutes(hass)

        if self.device_type == DEVICE_TYPE_APPLIANCE:
            # Appliance is "satisfied" when it's not waiting to run
            return self.appliance_state in (APPLIANCE_STATE_IDLE, APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_DONE)

        return False

    # ------------------------------------------------------------------
    # Must-run override — ignore score, turn on unconditionally
    # ------------------------------------------------------------------
    def must_run_now(self, hass: HomeAssistant) -> bool:
        if self.device_type == DEVICE_TYPE_WATER_HEATER:
            temp = self._state_float(hass, self.wh_temp_entity)
            # Safety: always force on below the static legionella floor.
            if temp < self.wh_temp_min:
                return True
            # Off-peak: force on only when temperature is significantly below the target.
            # A hysteresis band prevents repeated short cycles when temp hovers near the minimum.
            # Trigger threshold = off_peak_min − hysteresis_k  (default 3 °C).
            now = datetime.now().time()
            if self._is_off_peak(now):
                minutes_left = self._minutes_to_off_peak_end(now)
                # Don't start if there's less than min_on_minutes remaining in the
                # off-peak slot: the heater would spill into peak hours.
                if minutes_left is not None and minutes_left < self.min_on_minutes:
                    return False
                return temp < self._wh_off_peak_min(hass) - self.wh_off_peak_hysteresis_k
            return False

        if self.device_type == DEVICE_TYPE_POOL:
            # Only considered in the last _POOL_MUST_RUN_WINDOW_H hours of the day.
            # Earlier, solar production may still cover the deficit naturally.
            now      = datetime.now()
            midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
            minutes_left = (midnight - now).total_seconds() / 60
            if minutes_left > _POOL_MUST_RUN_WINDOW_H * 60:
                return False
            required_m = self._pool_required_minutes(hass)
            deficit_m  = max(0.0, required_m - self.pool_daily_run_minutes)
            if deficit_m <= 0:
                return False
            return deficit_m >= minutes_left

        return False

    # ------------------------------------------------------------------
    # Urgency modifier [0..1] — how urgent is it to run this device now?
    # ------------------------------------------------------------------
    def urgency_modifier(self, hass: HomeAssistant) -> float:
        if self.device_type == DEVICE_TYPE_EV:
            soc = self._state_float(hass, self.ev_soc_entity, fallback=self.ev_soc_target)
            soc_deficit      = max(0.0, self.ev_soc_target - soc) / max(self.ev_soc_target, 1)
            departure_urgency = self._deadline_urgency(
                self.ev_departure_time,
                energy_wh=self.ev_battery_capacity_wh,
                power_w=self.power_w or 3700,
            )
            return min(1.0, 0.6 * soc_deficit + 0.4 * departure_urgency)

        if self.device_type == DEVICE_TYPE_WATER_HEATER:
            temp = self._state_float(hass, self.wh_temp_entity)
            now  = datetime.now().time()
            if self._is_off_peak(now):
                # Off-peak: urgency is based on distance to the off-peak minimum.
                off_peak_min = self._wh_off_peak_min(hass)
                temp_range   = max(self.wh_temp_target - off_peak_min, 1.0)
                deficit      = max(0.0, off_peak_min - temp)
                return min(1.0, deficit / temp_range)
            # On-peak: urgency rises as temperature drops toward the minimum.
            temp_range = max(self.wh_temp_target - self.wh_temp_min, 1.0)
            deficit    = max(0.0, self.wh_temp_target - temp)
            return min(1.0, deficit / temp_range)

        if self.device_type == DEVICE_TYPE_HVAC:
            current  = self._state_float(hass, self.hvac_temp_entity)
            setpoint = self._state_float(hass, self.hvac_setpoint_entity)
            deviation = abs(setpoint - current)
            return min(1.0, deviation / 3.0)  # max urgency at 3 °C off-target

        if self.device_type == DEVICE_TYPE_POOL:
            required_m = self._pool_required_minutes(hass)
            if required_m <= 0:
                return 0.0
            deficit_m = max(0.0, required_m - self.pool_daily_run_minutes)
            now      = datetime.now()
            midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
            minutes_left = max(1.0, (midnight - now).total_seconds() / 60)
            return min(1.0, deficit_m / minutes_left)

        if self.device_type == DEVICE_TYPE_APPLIANCE:
            if self.appliance_state != APPLIANCE_STATE_PREPARING:
                return 0.0
            energy_wh = (
                (self.appliance_cycle_duration_minutes or DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES)
                / 60 * self.power_w
            )
            return self._deadline_urgency(self.deadline, energy_wh=energy_wh, power_w=self.power_w)

        return 0.5  # neutral for unknown types

    # ------------------------------------------------------------------
    # Fit score [0..1] — how well does device power match available surplus?
    # ------------------------------------------------------------------
    @staticmethod
    def compute_fit_score(
        device_power_w: float,
        surplus_w: float,
        bat_available_w: float,
    ) -> float:
        """
        Zone 1 — device ≤ surplus (solar covers it, no battery needed):
            fit = device_power / surplus   [0..1]
            → rewards devices that absorb more of the available solar

        Zone 2 — surplus < device ≤ surplus + bat_available (battery helps):
            fit = 1.0 − 0.4 × (battery_fraction)   [0.6..1.0]
            → acceptable, penalised proportionally to battery usage

        Zone 3 — device > surplus + bat_available (grid import needed):
            fit = 0.4 × (1 − grid_fraction)   [0..0.4]
            → heavily penalised, only selected as last resort
        """
        if device_power_w <= 0:
            return 0.0
        effective = surplus_w + bat_available_w
        if effective <= 0:
            return 0.0

        if device_power_w <= surplus_w:
            return device_power_w / max(surplus_w, 1.0)

        if device_power_w <= effective:
            bat_fraction = (device_power_w - surplus_w) / max(bat_available_w, 1.0)
            return 1.0 - 0.4 * bat_fraction

        grid_import   = device_power_w - effective
        grid_fraction = grid_import / device_power_w
        return max(0.0, 0.4 * (1.0 - grid_fraction))

    # ------------------------------------------------------------------
    # Composite effective score [0..1]
    # ------------------------------------------------------------------
    def effective_score(
        self,
        hass: HomeAssistant,
        surplus_w: float,
        bat_available_w: float,
    ) -> float:
        priority_score = self.priority / 10.0
        fit     = self.compute_fit_score(self.power_w, surplus_w, bat_available_w)
        urgency = self.urgency_modifier(hass)

        total_w = self.w_priority + self.w_fit + self.w_urgency
        if total_w <= 0:
            return 0.0

        return (
            self.w_priority * priority_score
            + self.w_fit     * fit
            + self.w_urgency * urgency
        ) / total_w

    # ------------------------------------------------------------------
    # Pool helpers
    # ------------------------------------------------------------------
    def update_pool_run_time(self, scan_interval_minutes: float, today: date) -> None:
        """Called each coordinator cycle. Resets counter only at date change."""
        if self.pool_last_date != today:
            self.pool_daily_run_minutes = 0.0
            self.pool_last_date = today
            self.pool_required_minutes_today = None  # re-captured at 05:00
        if self.is_on:
            self.pool_daily_run_minutes += scan_interval_minutes

    def try_capture_pool_required(self, hass: HomeAssistant, current_hour: int) -> None:
        """Snapshot the required filtration minutes once at 05:00 (or on restart if already ≥ 05:00).

        Reading the entity live would cause ON/OFF oscillations as the pool
        temperature—and thus the required hours—evolves during the day.
        """
        if self.pool_required_minutes_today is not None:
            return  # already captured today
        if current_hour < 5:
            return  # wait until 05:00
        raw = self._pool_required_minutes_live(hass)
        if raw <= 0.0:
            return  # entity not ready yet — try again next cycle
        self.pool_required_minutes_today = raw
        _LOGGER.info(
            "Pool '%s': captured daily required filtration = %.1f min at %02d:xx",
            self.name, raw, current_hour,
        )

    def _pool_required_minutes_live(self, hass: HomeAssistant) -> float:
        """Read the filtration entity directly (only used for the 05:00 snapshot)."""
        if not self.pool_filtration_entity:
            return 0.0
        state = hass.states.get(self.pool_filtration_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return 0.0
        try:
            return float(state.state) * 60.0  # hours → minutes
        except ValueError:
            return 0.0

    def _pool_required_minutes(self, hass: HomeAssistant) -> float:
        """Return the stable daily snapshot, or live value if snapshot not yet taken."""
        if self.pool_required_minutes_today is not None:
            return self.pool_required_minutes_today
        # Before 05:00 or entity unavailable at 05:00: use live value so the
        # force-mode path still works, but dispatch won't run (score = 0 at night).
        return self._pool_required_minutes_live(hass)

    # ------------------------------------------------------------------
    # Deadline / departure urgency helper
    # ------------------------------------------------------------------
    def _deadline_urgency(
        self,
        deadline_str: str | None,
        energy_wh: float | None,
        power_w: float,
    ) -> float:
        """Returns [0..1] — rises as deadline approaches.

        0.3 baseline when no deadline; 1.0 when no time left.
        """
        if not deadline_str:
            return 0.3

        try:
            h, m = map(int, deadline_str.split(":"))
            now         = datetime.now()
            deadline_dt = datetime.combine(now.date(), time(h, m))
            if deadline_dt <= now:
                deadline_dt += timedelta(days=1)  # already passed today → tomorrow

            seconds_left = (deadline_dt - now).total_seconds()

            if energy_wh and power_w:
                seconds_needed = (energy_wh / power_w) * 3600
            elif self.appliance_cycle_duration_minutes:
                seconds_needed = self.appliance_cycle_duration_minutes * 60
            else:
                seconds_needed = 3600  # 1 h fallback

            margin = seconds_left - seconds_needed
            if margin <= 0:
                return 1.0
            if margin < 3_600:       # less than 1 h margin
                return 0.8
            if margin < 10_800:      # less than 3 h margin — ramp 0.3→0.8
                return 0.3 + 0.5 * (1.0 - margin / 10_800)
            return 0.3

        except (ValueError, AttributeError):
            return 0.3

    # ------------------------------------------------------------------
    # HA state readers
    # ------------------------------------------------------------------
    @staticmethod
    def _state_float(
        hass: HomeAssistant,
        entity_id: str | None,
        fallback: float = 0.0,
    ) -> float:
        if not entity_id:
            return fallback
        s = hass.states.get(entity_id)
        if s is None or s.state in ("unavailable", "unknown"):
            return fallback
        try:
            return float(s.state)
        except ValueError:
            return fallback

    @staticmethod
    def _state_bool(
        hass: HomeAssistant,
        entity_id: str | None,
        fallback: bool = True,
    ) -> bool:
        if not entity_id:
            return fallback
        s = hass.states.get(entity_id)
        if s is None:
            return fallback
        return s.state in ("on", "true", "home", "connected", "plugged_in", "1")


# ===========================================================================
# DeviceManager
# ===========================================================================

class DeviceManager:
    """Orchestrates all managed devices: scoring, dispatch, state machines."""

    def __init__(
        self,
        hass: HomeAssistant,
        devices_config: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> None:
        self.devices: list[ManagedDevice] = [ManagedDevice(c, config) for c in devices_config]
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._scan_interval: float = float(config.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL))
        self._dispatch_threshold: float = float(config.get(CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD))
        # Decision log — rolling 24 h, max 500 entries
        self.decision_log: deque[dict] = deque(maxlen=100)

    # ------------------------------------------------------------------
    # Startup — restore persisted pool run data
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Load pool daily run counters from HA storage."""
        data: dict = await self._store.async_load() or {}
        today = date.today()
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL:
                continue
            stored = data.get(device.name, {})
            stored_date_str: str | None = stored.get("date")
            if stored_date_str:
                try:
                    stored_date = date.fromisoformat(stored_date_str)
                    if stored_date == today:
                        device.pool_daily_run_minutes = float(stored.get("minutes", 0.0))
                        device.pool_last_date = today
                        required = stored.get("required_minutes")
                        if required is not None:
                            device.pool_required_minutes_today = float(required)
                        _LOGGER.debug(
                            "Pool '%s': restored %.1f min done, %.1f min required for today",
                            device.name, device.pool_daily_run_minutes,
                            device.pool_required_minutes_today or 0.0,
                        )
                except ValueError:
                    pass

    async def _async_save_pool_data(self) -> None:
        """Persist pool daily run counters (called after each update)."""
        data: dict = {}
        for device in self.devices:
            if device.device_type == DEVICE_TYPE_POOL:
                data[device.name] = {
                    "date": (device.pool_last_date or date.today()).isoformat(),
                    "minutes": device.pool_daily_run_minutes,
                    "required_minutes": device.pool_required_minutes_today,
                }
        if data:
            await self._store.async_save(data)

    # ------------------------------------------------------------------
    # Main dispatch loop — called each coordinator cycle
    # ------------------------------------------------------------------
    async def async_dispatch(
        self,
        hass: HomeAssistant,
        score_input: dict[str, Any],
    ) -> None:
        global_score:       float       = score_input.get("global_score",       0.0)
        surplus_w:          float       = score_input.get("surplus_w",          0.0)
        bat_available_w:    float       = score_input.get("bat_available_w",    0.0)
        dispatch_threshold: float       = score_input.get("dispatch_threshold", self._dispatch_threshold)
        battery_soc:        float | None = score_input.get("battery_soc")
        configured_allowance_w: float   = float(score_input.get("grid_allowance_w", 250.0))
        pv_power_w:         float       = score_input.get("pv_power_w",         0.0)
        house_power_w:      float       = score_input.get("house_power_w",      0.0)
        tempo_color:        str | None  = score_input.get("tempo_color")
        soc_reserve_rouge:  float       = float(score_input.get("soc_reserve_rouge", DEFAULT_BATTERY_SOC_RESERVE_ROUGE))

        # Red-day strict mode: when SOC is below the battery reserve, do not
        # activate NEW devices unless they fit within the PV surplus alone.
        # Already-ON devices are not affected — we don't cut them off mid-cycle.
        _red_strict = (
            tempo_color == TEMPO_RED
            and battery_soc is not None
            and battery_soc < soc_reserve_rouge
        )

        # Base context injected into every decision log entry
        _base_ctx: dict = {
            "battery_soc": battery_soc,
            "pv_w":        round(pv_power_w),
            "house_w":     round(house_power_w),
        }

        # Mode "Pleine" (SOC ≥ 96 %) : autoriser un léger tirage réseau pour
        # décharger la batterie avant qu'elle atteigne 100 % et perde en efficacité.
        grid_allowance_w: float = configured_allowance_w if (battery_soc is not None and battery_soc >= 96.0) else 0.0
        if grid_allowance_w:
            _LOGGER.info(
                "Dispatch: SOC=%.0f%% (Pleine) — tolérance réseau +%.0fW activée",
                battery_soc, grid_allowance_w,
            )
        today  = date.today()
        now    = datetime.now().time()
        now_ts = time_mod.time()

        # ---- Update pool run counters (always, including during force mode) ----
        pool_changed = False
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL or device.manual_mode:
                continue
            before_minutes = device.pool_daily_run_minutes
            before_required = device.pool_required_minutes_today
            device.update_pool_run_time(self._scan_interval, today)
            device.try_capture_pool_required(hass, now.hour)
            if (device.pool_daily_run_minutes != before_minutes
                    or device.pool_required_minutes_today != before_required):
                pool_changed = True
        if pool_changed:
            await self._async_save_pool_data()

        # ---- Pool force ON: maintain / expire ----
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL or device.pool_force_until is None or device.manual_mode:
                continue
            if now_ts < device.pool_force_until:
                if not device.is_on:
                    await self._async_set_switch(hass, device, True, reason="force_mode", context=_base_ctx)
            else:
                device.pool_force_until = None
                _LOGGER.info("Pool '%s': force mode expired", device.name)

        # ---- Pool inhibit: ensure off / expire ----
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL or device.pool_inhibit_until is None or device.manual_mode:
                continue
            if now_ts < device.pool_inhibit_until:
                if device.is_on:
                    await self._async_set_switch(hass, device, False, reason="inhibit_mode", context=_base_ctx)
            else:
                device.pool_inhibit_until = None
                _LOGGER.info("Pool '%s': inhibit mode expired", device.name)

        def _helios_manages(device: ManagedDevice) -> bool:
            """False if Helios must not touch this device (manual mode, or pool locked)."""
            if device.manual_mode:
                return False
            if device.device_type == DEVICE_TYPE_POOL:
                if device.pool_force_until is not None and now_ts < device.pool_force_until:
                    return False
                if device.pool_inhibit_until is not None and now_ts < device.pool_inhibit_until:
                    return False
            return True

        # ---- Collect must-run overrides (skip devices Helios doesn't manage) ----
        must_run = {d for d in self.devices if d.must_run_now(hass) and _helios_manages(d)}

        # ---- Réserve zone (SOC ≤ 20 %): suppress non-safety overrides ----
        # In this zone the battery is critically low.  The water heater legionella
        # protection is a genuine safety override (health risk); pool filtration is
        # not — its urgency is already reflected in urgency_modifier().
        if battery_soc is not None and battery_soc <= 20.0 and must_run:
            suppressed = {d for d in must_run if d.device_type != DEVICE_TYPE_WATER_HEATER}
            if suppressed:
                _LOGGER.warning(
                    "Dispatch: SOC=%.0f%% (Réserve) — must_run supprimé pour: %s",
                    battery_soc,
                    ", ".join(d.name for d in suppressed),
                )
            must_run -= suppressed

        # ---- Gate: skip normal dispatch if global score too low ----
        if global_score < dispatch_threshold and not must_run:
            for device in self.devices:
                if device.device_type == DEVICE_TYPE_APPLIANCE:
                    # State machine always runs so IDLE→READY→RUNNING transitions
                    # are not blocked by a low global score.
                    await self._async_handle_appliance(
                        hass, device, global_score, surplus_w, bat_available_w
                    )
                    continue
                if not _helios_manages(device):
                    continue  # manual / force / inhibit — hands off
                if device.is_on and device.interruptible:
                    satisfied = device.is_satisfied(hass)
                    if satisfied or self._min_on_elapsed(device):
                        reason = "satisfied" if satisfied else "score_too_low"
                        await self._async_set_switch(hass, device, False, reason=reason, context=_base_ctx)
            return

        # ---- Priority preemption for PREPARING appliances ----
        # If a high-priority appliance is ready to start (score+fit or urgency)
        # but can't fit because lower-priority interruptible devices are running,
        # turn off those devices to free budget within this cycle.
        preparing_apps = [
            d for d in self.devices
            if d.device_type == DEVICE_TYPE_APPLIANCE
            and d.appliance_state == APPLIANCE_STATE_PREPARING
            and _helios_manages(d)
        ]
        for app in sorted(preparing_apps, key=lambda d: d.priority, reverse=True):
            urgency = app.urgency_modifier(hass)
            fit = ManagedDevice.compute_fit_score(app.power_w, surplus_w, bat_available_w)
            # Conditions to start are already met — no preemption needed
            if (global_score >= 0.4 and fit >= 0.3) or urgency >= 0.8:
                continue
            # Score not high enough regardless of budget — skip
            if global_score < 0.4 and urgency < 0.8:
                continue
            # Find lower-priority ON interruptible non-appliance devices
            candidates = sorted(
                [
                    d for d in self.devices
                    if d.device_type != DEVICE_TYPE_APPLIANCE
                    and d.is_on
                    and d.interruptible
                    and d.priority < app.priority
                    and _helios_manages(d)
                    and self._min_on_elapsed(d)
                ],
                key=lambda d: d.priority,  # Preempt lowest priority first
            )
            freed_w = 0.0
            to_preempt: list[ManagedDevice] = []
            for c in candidates:
                freed_w += c.actual_power_w(hass)
                to_preempt.append(c)
                if ManagedDevice.compute_fit_score(
                    app.power_w, surplus_w + freed_w, bat_available_w
                ) >= 0.3:
                    break
            else:
                continue  # Can't free enough budget even with all candidates
            for c in to_preempt:
                _LOGGER.info(
                    "Dispatch: preempting '%s' (priority=%d) to start appliance '%s' (priority=%d)",
                    c.name, c.priority, app.name, app.priority,
                )
                await self._async_set_switch(
                    hass, c, False,
                    reason="preempted",
                    context={**_base_ctx, "preempted_by": app.name},
                )
            surplus_w += freed_w  # Make freed budget visible to appliance state machine

        # ---- Score all eligible devices ----
        scored: list[tuple[float, ManagedDevice]] = []

        for device in self.devices:
            # Devices not under Helios control are skipped entirely
            if not _helios_manages(device):
                continue

            # Appliance state machine is handled separately
            if device.device_type == DEVICE_TYPE_APPLIANCE:
                await self._async_handle_appliance(
                    hass, device, global_score, surplus_w, bat_available_w
                )
                continue

            # Must-run override → bypass allowed window and force on immediately.
            # Safety overrides (legionella, off-peak HC heating) must not be blocked
            # by a misconfigured or too-narrow allowed window.
            if device in must_run:
                if not device.is_on:
                    await self._async_set_switch(hass, device, True, reason="must_run", context=_base_ctx)
                continue

            # Outside allowed window → turn off
            if not device.is_in_allowed_window(now):
                if device.is_on and device.interruptible and self._min_on_elapsed(device):
                    await self._async_set_switch(hass, device, False, reason="outside_window", context=_base_ctx)
                continue

            # Already satisfied → turn off immediately (reaching target is always a valid stop)
            if device.is_satisfied(hass):
                if device.is_on and device.interruptible:
                    await self._async_set_switch(hass, device, False, reason="satisfied", context=_base_ctx)
                continue

            score = device.effective_score(hass, surplus_w, bat_available_w)
            scored.append((score, device))

        # ---- Greedy allocation (highest score first) ----
        scored.sort(key=lambda x: x[0], reverse=True)

        # Add back the power of currently-ON Helios devices: house_w already
        # includes their consumption, so surplus_w is already reduced by their
        # load. Without this correction, each cycle they would compete against
        # their own consumption and get turned off spuriously.
        helios_on_w = sum(d.actual_power_w(hass) for d in self.devices if d.is_on and _helios_manages(d))
        remaining = surplus_w + bat_available_w + grid_allowance_w + helios_on_w

        for score, device in scored:
            # For fit calculation, add back this device's actual draw if already ON
            # so it doesn't penalise itself when re-evaluated each cycle.
            # Use actual_power_w: a water heater whose thermostat has cut (0 W actual)
            # must not inflate fit_surplus with its nominal power.
            fit_surplus = surplus_w + (device.actual_power_w(hass) if device.is_on else 0)
            fit = ManagedDevice.compute_fit_score(device.power_w, fit_surplus, bat_available_w)

            # Skip if fit is negligible (would import too much from grid)
            if fit < 0.1:
                if device.is_on and device.interruptible and self._min_on_elapsed(device):
                    await self._async_set_switch(hass, device, False, reason="fit_negligible", context=_base_ctx)
                continue

            if device.power_w <= remaining:
                # Red-day strict guard: on red days below battery reserve, only
                # activate NEW devices that fit within the PV surplus alone.
                # This prevents the physical battery from being drained to power
                # devices on expensive red days when the reserve is not met.
                if not device.is_on and _red_strict and device.power_w > surplus_w:
                    _LOGGER.debug(
                        "Dispatch: '%s' blocked — red day strict mode "
                        "(SOC=%.0f%% < reserve=%.0f%%, power=%dW > surplus=%dW)",
                        device.name, battery_soc, soc_reserve_rouge,
                        device.power_w, surplus_w,
                    )
                    continue
                remaining -= device.power_w
                if not device.is_on:
                    await self._async_set_switch(
                        hass, device, True,
                        reason="dispatch",
                        context={
                            **_base_ctx,
                            "global_score":    round(global_score, 3),
                            "surplus_w":       round(surplus_w),
                            "bat_available_w": round(bat_available_w),
                            "fit":             round(fit, 3),
                        },
                    )
            else:
                if device.is_on and device.interruptible and self._min_on_elapsed(device):
                    await self._async_set_switch(
                        hass, device, False,
                        reason="no_budget",
                        context={
                            **_base_ctx,
                            "power_w":     device.power_w,
                            "remaining_w": round(remaining),
                        },
                    )

    # ------------------------------------------------------------------
    # Appliance state machine
    # ------------------------------------------------------------------
    async def _async_handle_appliance(
        self,
        hass: HomeAssistant,
        device: ManagedDevice,
        global_score: float,
        surplus_w: float,
        bat_available_w: float,
    ) -> None:
        now_ts = time_mod.time()

        if device.appliance_state == APPLIANCE_STATE_IDLE:
            # Watch ready entity — when user activates the switch, launch prepare
            # script immediately and wait for Helios to pick the right start time.
            ready = ManagedDevice._state_bool(hass, device.appliance_ready_entity, fallback=False)
            if ready:
                device.appliance_state = APPLIANCE_STATE_PREPARING
                _LOGGER.info("Appliance '%s': preparing — waiting for optimal start window", device.name)
                if device.appliance_prepare_script:
                    await hass.services.async_call(
                        "script", "turn_on",
                        {"entity_id": device.appliance_prepare_script},
                        blocking=False,
                    )
            return

        if device.appliance_state == APPLIANCE_STATE_PREPARING:
            fit     = ManagedDevice.compute_fit_score(device.power_w, surplus_w, bat_available_w)
            urgency = device.urgency_modifier(hass)

            should_start = (
                (global_score >= 0.4 and fit >= 0.3)
                or urgency >= 0.8   # deadline imminent → start regardless of surplus
            )
            if not should_start:
                return

            # Transition: PREPARING → RUNNING
            _LOGGER.info("Appliance '%s': starting (score=%.2f fit=%.2f urgency=%.2f)",
                         device.name, global_score, fit, urgency)

            if not device.appliance_start_script:
                _LOGGER.warning(
                    "Appliance '%s': no start_script configured — "
                    "cycle will be tracked but nothing will actually start",
                    device.name,
                )

            if device.appliance_start_script:
                await hass.services.async_call(
                    "script", "turn_on",
                    {"entity_id": device.appliance_start_script},
                    blocking=False,
                )

            device.appliance_state     = APPLIANCE_STATE_RUNNING
            device.appliance_cycle_start = now_ts
            device.is_on               = True
            return

        if device.appliance_state == APPLIANCE_STATE_RUNNING:
            done = False

            if device.appliance_power_entity:
                # Primary: detect power drop
                power = ManagedDevice._state_float(hass, device.appliance_power_entity)
                if power < device.appliance_power_threshold_w:
                    if device.appliance_low_power_since is None:
                        device.appliance_low_power_since = now_ts
                    elif now_ts - device.appliance_low_power_since >= _APPLIANCE_LOW_POWER_CONFIRM_S:
                        done = True
                else:
                    device.appliance_low_power_since = None
            elif device.appliance_cycle_start is not None:
                # Fallback: elapsed time
                elapsed_m = (now_ts - device.appliance_cycle_start) / 60
                done = elapsed_m >= device.appliance_cycle_duration_minutes

            if done:
                device.appliance_state          = APPLIANCE_STATE_DONE
                device.is_on                    = False
                device.appliance_cycle_start    = None
                device.appliance_low_power_since = None
                _LOGGER.info("Appliance '%s': cycle complete", device.name)
                if device.appliance_ready_entity:
                    await hass.services.async_call(
                        "input_boolean", "turn_off",
                        {"entity_id": device.appliance_ready_entity},
                        blocking=False,
                    )
            return

        if device.appliance_state == APPLIANCE_STATE_DONE:
            device.appliance_state = APPLIANCE_STATE_IDLE

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _min_on_elapsed(self, device: ManagedDevice) -> bool:
        """True if the device has been on long enough to allow turning it off."""
        if device.turned_on_at is None:
            return True
        elapsed_m = (time_mod.time() - device.turned_on_at) / 60
        return elapsed_m >= device.min_on_minutes

    async def _async_set_switch(
        self,
        hass: HomeAssistant,
        device: ManagedDevice,
        on: bool,
        reason: str = "",
        context: dict | None = None,
    ) -> None:
        entry: dict = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "device": device.name,
            "action": "on" if on else "off",
            "reason": reason or "unknown",
        }
        if context:
            entry.update(context)
        self.decision_log.append(entry)
        if device.device_type == DEVICE_TYPE_EV:
            script = device.ev_charge_start_script if on else device.ev_charge_stop_script
            if script:
                await hass.services.async_call(
                    "script", "turn_on",
                    {"entity_id": script},
                    blocking=False,
                )
            elif device.switch_entity:
                await hass.services.async_call(
                    "homeassistant",
                    "turn_on" if on else "turn_off",
                    {"entity_id": device.switch_entity},
                    blocking=False,
                )
        elif device.switch_entity:
            await hass.services.async_call(
                "homeassistant",
                "turn_on" if on else "turn_off",
                {"entity_id": device.switch_entity},
                blocking=False,
            )
        device.is_on = on
        if on:
            device.turned_on_at  = time_mod.time()
        else:
            device.turned_off_at = time_mod.time()
        _LOGGER.debug("Device '%s' → %s", device.name, "ON" if on else "OFF")
