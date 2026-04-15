"""ManagedDevice — per-device configuration, satisfaction checks, and scoring logic.

This module is independent of DeviceManager and can be imported from both
the live HA dispatch loop and the simulation engine without pulling in the
full orchestration layer.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant

from .const import (
    # Device types
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC,
    DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_POOL,
    # Common device config
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_POWER_ENTITY, CONF_DEVICE_PRIORITY,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_DEVICE_INTERRUPTIBLE, CONF_DEVICE_DEADLINE,
    # EV
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_EV_DEPARTURE_TIME, CONF_EV_MIN_CHARGE_POWER_W, CONF_EV_BATTERY_CAPACITY_WH,
    CONF_EV_CHARGE_START_SCRIPT, CONF_EV_CHARGE_STOP_SCRIPT,
    # Water heater
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN, CONF_WH_TEMP_MIN_ENTITY,
    CONF_WH_OFF_PEAK_HYSTERESIS_K,
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
    CONF_APPLIANCE_DEADLINE_SLOTS,
    APPLIANCE_STATE_IDLE, APPLIANCE_STATE_PREPARING,
    APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_DONE,
    # Off-peak slots
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
    # Battery
    CONF_BATTERY_PRIORITY, CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_RESERVE_ROUGE, CONF_BATTERY_MAX_CHARGE_POWER_W,
    # Defaults
    DEFAULT_DEVICE_PRIORITY, DEFAULT_DEVICE_MIN_ON_MINUTES,
    DEFAULT_ALLOWED_START, DEFAULT_ALLOWED_END,
    DEFAULT_EV_SOC_TARGET, DEFAULT_EV_MIN_CHARGE_POWER_W,
    DEFAULT_WH_TEMP_TARGET, DEFAULT_WH_TEMP_MIN, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K,
    DEFAULT_HVAC_HYSTERESIS_K, DEFAULT_HVAC_MIN_OFF_MINUTES,
    DEFAULT_POOL_SPLIT_SESSIONS,
    DEFAULT_APPLIANCE_POWER_THRESHOLD_W, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES,
    DEFAULT_APPLIANCE_DEADLINE_SLOTS,
    DEFAULT_BATTERY_PRIORITY, DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    TEMPO_RED,
)

_LOGGER = logging.getLogger(__name__)

# Pool must_run: only force filtration in the last N hours of the day.
# Before this window the day is still open — solar may yet provide the needed energy.
_POOL_MUST_RUN_WINDOW_H = 8  # hours before midnight (default: fires after 16:00)

# A StateReader reads a HA entity state and returns its raw string value,
# or None if the entity is unavailable / unknown / missing.
# Used to decouple pure decision logic from the HA runtime so the same
# methods can be called from the simulation without a real hass instance.
StateReader = Callable[[str], str | None]


def _parse_time(value: str | None) -> time | None:
    """Parse a 'HH:MM' or 'HH:MM:SS' string into a time object, return None on failure."""
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except (ValueError, AttributeError):
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
        self.power_w: float          = float(config.get(CONF_DEVICE_POWER_W, 0))
        self.power_entity: str | None = config.get(CONF_DEVICE_POWER_ENTITY)
        self.priority: int           = int(config.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY))
        self.min_on_minutes: int = int(config.get(CONF_DEVICE_MIN_ON_MINUTES, DEFAULT_DEVICE_MIN_ON_MINUTES))
        self.allowed_start: str = config.get(CONF_DEVICE_ALLOWED_START, DEFAULT_ALLOWED_START)
        self.allowed_end: str   = config.get(CONF_DEVICE_ALLOWED_END,   DEFAULT_ALLOWED_END)
        # Pre-parsed for efficiency (called every dispatch cycle per device)
        self._allowed_start_t: time | None = _parse_time(self.allowed_start)
        self._allowed_end_t: time | None   = _parse_time(self.allowed_end)
        self.deadline: str | None = config.get(CONF_DEVICE_DEADLINE)

        # Interruptible is derived from device type (explicit override allowed)
        _interruptible_default = (self.device_type != DEVICE_TYPE_APPLIANCE)
        self.interruptible: bool = bool(config.get(CONF_DEVICE_INTERRUPTIBLE, _interruptible_default))

        # ---- EV ----
        self.ev_soc_entity: str | None       = config.get(CONF_EV_SOC_ENTITY)
        self.ev_plugged_entity: str | None   = config.get(CONF_EV_PLUGGED_ENTITY)
        self.ev_soc_target: float            = float(config.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET))
        self.ev_departure_time: str | None   = config.get(CONF_EV_DEPARTURE_TIME)
        self.ev_min_charge_power_w: float    = float(config.get(CONF_EV_MIN_CHARGE_POWER_W, DEFAULT_EV_MIN_CHARGE_POWER_W))
        self.ev_battery_capacity_wh: float | None = (
            float(v) if (v := config.get(CONF_EV_BATTERY_CAPACITY_WH)) else None
        )
        self.ev_charge_start_script: str | None = config.get(CONF_EV_CHARGE_START_SCRIPT)
        self.ev_charge_stop_script: str | None  = config.get(CONF_EV_CHARGE_STOP_SCRIPT)

        # ---- Water heater ----
        self.wh_temp_entity: str | None     = config.get(CONF_WH_TEMP_ENTITY)
        self.wh_temp_target: float          = float(config.get(CONF_WH_TEMP_TARGET, DEFAULT_WH_TEMP_TARGET))
        self.wh_temp_min: float             = float(config.get(CONF_WH_TEMP_MIN,    DEFAULT_WH_TEMP_MIN))
        self.wh_temp_min_entity: str | None = config.get(CONF_WH_TEMP_MIN_ENTITY)
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
        # Deadline slots: comma-separated "HH:MM" strings, e.g. "12:00,18:00"
        raw_slots = config.get(CONF_APPLIANCE_DEADLINE_SLOTS, DEFAULT_APPLIANCE_DEADLINE_SLOTS)
        self.appliance_deadline_slots: list[time] = [
            t for s in (raw_slots or "").split(",")
            if (t := _parse_time(s.strip())) is not None
        ]

        # ---- Runtime state ----
        self.is_on: bool                      = False
        self.turned_on_at: float | None       = None  # epoch seconds
        self.turned_off_at: float | None      = None
        self.manual_mode: bool                = False  # True → Helios hands off entirely

        # Diagnostics — updated every dispatch cycle, exposed via switch extra_state_attributes
        self.last_effective_score: float      = 0.0
        self.last_decision_reason: str        = ""
        self.last_reason: str                 = ""
        self.last_priority_score: float       = 0.0
        self.last_fit: float                  = 0.0
        self.last_urgency: float              = 0.0

        # Generic — daily on-time tracking (all device types)
        self.daily_on_minutes: float             = 0.0
        self._daily_last_date: date | None       = None

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
        # Auto-computed deadline set when the appliance transitions to PREPARING.
        # Before noon → 12:00 ; afternoon → 18:00 ; evening → midnight.
        self.appliance_deadline_dt: datetime | None = None

    # ------------------------------------------------------------------
    # Allowed time window
    # ------------------------------------------------------------------
    def is_in_allowed_window(self, now: time) -> bool:
        """True if *now* falls within [allowed_start, allowed_end]."""
        start = self._allowed_start_t
        end   = self._allowed_end_t
        if start is None or end is None:
            return True
        if start <= end:
            return start <= now <= end
        # Overnight window (e.g. 22:00–06:00)
        return now >= start or now <= end

    # ------------------------------------------------------------------
    # Actual power — uses measured entity when available, else nominal
    # ------------------------------------------------------------------
    def actual_power_w(self, reader: StateReader) -> float:
        """Return current power draw in W.

        Priority:
        1. Generic device_power_entity (common to all types) — smart plug / sensor.
        2. appliance_power_entity for appliances.
        3. Fallback: nominal power_w from configuration.

        Using the measured value avoids over-estimating the dispatch budget when
        the internal thermostat has cut (water heater) or when actual draw differs
        from the nominal (partial EV charge, variable pump speed, etc.).
        """
        if self.power_entity:
            return self._state_float(reader, self.power_entity)
        if self.device_type == DEVICE_TYPE_APPLIANCE and self.appliance_power_entity:
            return self._state_float(reader, self.appliance_power_entity)
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

    def _wh_off_peak_min(self, reader: StateReader) -> float:
        """Minimum temperature to reach during off-peak hours.

        Reads the configured entity; falls back to the static legionella floor.
        """
        if self.wh_temp_min_entity:
            return self._state_float(reader, self.wh_temp_min_entity, fallback=self.wh_temp_min)
        return self.wh_temp_min

    # ------------------------------------------------------------------
    # Satisfaction — has the device reached its target?
    # ------------------------------------------------------------------
    def is_satisfied(self, reader: StateReader, now: datetime | None = None) -> bool:
        if self.device_type == DEVICE_TYPE_EV:
            if self.ev_plugged_entity:
                plugged = self._state_bool(reader, self.ev_plugged_entity, fallback=True)
            else:
                plugged = self.ev_plugged_manual
            if not plugged:
                return True  # car not plugged → nothing to do
            return self._state_float(reader, self.ev_soc_entity) >= self.ev_soc_target

        if self.device_type == DEVICE_TYPE_WATER_HEATER:
            raw = reader(self.wh_temp_entity) if self.wh_temp_entity else None
            if raw is None or raw in ("unavailable", "unknown"):
                return False  # Unknown temp → never claim satisfied; keep device running.
            temp = float(raw)
            if self._is_off_peak((now or datetime.now()).time()):
                # During off-peak: satisfied when the off-peak minimum is reached.
                # must_run_now() forced us here; once the target is met we stop.
                return temp >= self._wh_off_peak_min(reader)
            return temp >= self.wh_temp_target

        if self.device_type == DEVICE_TYPE_HVAC:
            current  = self._state_float(reader, self.hvac_temp_entity)
            setpoint = self._state_float(reader, self.hvac_setpoint_entity)
            if self.hvac_mode == HVAC_MODE_HEAT:
                return current >= setpoint - self.hvac_hysteresis_k
            return current <= setpoint + self.hvac_hysteresis_k  # cool

        if self.device_type == DEVICE_TYPE_POOL:
            return self.pool_daily_run_minutes >= self._pool_required_minutes(reader)

        if self.device_type == DEVICE_TYPE_APPLIANCE:
            # Appliance is "satisfied" when it's not waiting to run
            return self.appliance_state in (APPLIANCE_STATE_IDLE, APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_DONE)

        return False

    # ------------------------------------------------------------------
    # Must-run override — replaced by urgency >= 1.0
    # Kept as compatibility shim; DeviceManager uses urgency directly.
    # ------------------------------------------------------------------
    def must_run_now(self, reader: StateReader, now: datetime | None = None) -> bool:
        return self.urgency_modifier(reader, now=now) >= 1.0

    # ------------------------------------------------------------------
    # Urgency modifier [0..1] — how urgent is it to run this device now?
    # ------------------------------------------------------------------
    def urgency_modifier(self, reader: StateReader, now: datetime | None = None) -> float:
        if self.device_type == DEVICE_TYPE_EV:
            soc = self._state_float(reader, self.ev_soc_entity, fallback=self.ev_soc_target)
            soc_deficit      = max(0.0, self.ev_soc_target - soc) / max(self.ev_soc_target, 1)
            departure_urgency = self._deadline_urgency(
                self.ev_departure_time,
                energy_wh=self.ev_battery_capacity_wh,
                power_w=self.power_w or 3700,
            )
            return min(1.0, 0.6 * soc_deficit + 0.4 * departure_urgency)

        if self.device_type == DEVICE_TYPE_WATER_HEATER:
            raw = reader(self.wh_temp_entity) if self.wh_temp_entity else None
            if raw is None or raw in ("unavailable", "unknown"):
                # Sensor unavailable: preserve current state — don't start if off,
                # keep running if on (avoids cutting a mid-cycle heating sequence).
                return 1.0 if self.is_on else 0.0
            temp = float(raw)
            # Safety floor: below legionella minimum → always urgent regardless of time
            if temp < self.wh_temp_min:
                return 1.0
            _now_t = (now or datetime.now()).time()
            if self._is_off_peak(_now_t):
                # Off-peak: urgency is based on distance to the off-peak minimum.
                off_peak_min = self._wh_off_peak_min(reader)
                temp_range   = max(self.wh_temp_target - off_peak_min, 1.0)
                deficit      = max(0.0, off_peak_min - temp)
                return min(1.0, deficit / temp_range)
            # On-peak: urgency rises as temperature drops toward the minimum.
            temp_range = max(self.wh_temp_target - self.wh_temp_min, 1.0)
            deficit    = max(0.0, self.wh_temp_target - temp)
            return min(1.0, deficit / temp_range)

        if self.device_type == DEVICE_TYPE_HVAC:
            current  = self._state_float(reader, self.hvac_temp_entity)
            setpoint = self._state_float(reader, self.hvac_setpoint_entity)
            deviation = abs(setpoint - current)
            return min(1.0, deviation / 3.0)  # max urgency at 3 °C off-target

        if self.device_type == DEVICE_TYPE_POOL:
            required_m = self._pool_required_minutes(reader)
            if required_m <= 0:
                return 0.0
            deficit_m = max(0.0, required_m - self.pool_daily_run_minutes)
            _now = now or datetime.now()
            minutes_left = max(1.0, (self._pool_deadline_dt(_now) - _now).total_seconds() / 60)
            return min(1.0, deficit_m / minutes_left)

        if self.device_type == DEVICE_TYPE_APPLIANCE:
            if self.appliance_state != APPLIANCE_STATE_PREPARING:
                return 0.0
            cycle_s = (
                (self.appliance_cycle_duration_minutes or DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES)
                * 60
            )
            deadline_dt = self.appliance_deadline_dt
            if deadline_dt is not None:
                return self._deadline_urgency_dt(deadline_dt, seconds_needed=cycle_s, now=now)
            # Fallback: user-configured deadline string (legacy / override)
            energy_wh = cycle_s / 3600 * self.power_w
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
        grid_allowance_w: float = 0.0,
        tempo_red: bool = False,
    ) -> float:
        """
        Zone 1 — device ≤ surplus (solar covers it, no battery needed):
            fit = device_power / surplus   [0..1]
            → rewards devices that absorb more of the available solar

        Zone 2 — surplus < device ≤ surplus + bat_available (battery helps):
            fit = 1.0 − 0.6 × (battery_fraction)   [0.4..1.0]
            → acceptable, penalised proportionally to battery usage

        Zone 3 — device > surplus + bat_available (grid import needed):
            - tempo_red=True → fit = 0 (any grid import is forbidden)
            - grid_import > grid_allowance_w → fit = 0
            - otherwise → fit = 0.4 × (1 − grid_import / grid_allowance_w)   [0..0.4]
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
            return 1.0 - 0.6 * bat_fraction

        # Zone 3 — grid import required
        grid_import = device_power_w - effective
        if tempo_red or grid_allowance_w <= 0 or grid_import > grid_allowance_w:
            return 0.0
        return 0.4 * (1.0 - grid_import / grid_allowance_w)

    # ------------------------------------------------------------------
    # Composite effective score [0..1]
    # Fixed formula — no configurable weights.
    # With urgency (all types except generic): 0.4×priority/10 + 0.3×fit + 0.3×urgency
    # Without urgency (generic):               0.5×priority/10 + 0.5×fit
    # ------------------------------------------------------------------
    def effective_score(
        self,
        reader: StateReader,
        surplus_w: float,
        bat_available_w: float,
        grid_allowance_w: float = 0.0,
        tempo_red: bool = False,
        now: datetime | None = None,
    ) -> float:
        priority_score = self.priority / 10.0
        fit     = self.compute_fit_score(self.power_w, surplus_w, bat_available_w, grid_allowance_w, tempo_red)
        urgency = self.urgency_modifier(reader, now=now)

        self.last_priority_score = round(priority_score, 3)
        self.last_fit            = round(fit, 3)
        self.last_urgency        = round(urgency, 3)

        # Generic devices have no intrinsic urgency — redistribute weight to priority/fit
        if self.device_type not in (
            DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC,
            DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
        ):
            return 0.5 * priority_score + 0.5 * fit

        return 0.4 * priority_score + 0.3 * fit + 0.3 * urgency

    # ------------------------------------------------------------------
    # Generic daily on-time
    # ------------------------------------------------------------------
    def update_daily_on_time(self, scan_interval_minutes: float, today: date) -> None:
        """Increment daily_on_minutes when device is on; reset at midnight."""
        if self._daily_last_date != today:
            self.daily_on_minutes = 0.0
            self._daily_last_date = today
        if self.is_on:
            self.daily_on_minutes += scan_interval_minutes

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

    def try_capture_pool_required(self, reader: StateReader, current_hour: int) -> None:
        """Snapshot the required filtration minutes once at 05:00 (or on restart if already ≥ 05:00).

        Reading the entity live would cause ON/OFF oscillations as the pool
        temperature—and thus the required hours—evolves during the day.
        """
        if self.pool_required_minutes_today is not None:
            return  # already captured today
        if current_hour < 5:
            return  # wait until 05:00
        raw = self._pool_required_minutes_live(reader)
        if raw <= 0.0:
            return  # entity not ready yet — try again next cycle
        self.pool_required_minutes_today = raw
        _LOGGER.info(
            "Pool '%s': captured daily required filtration = %.1f min at %02d:xx",
            self.name, raw, current_hour,
        )

    def _pool_deadline_dt(self, now: datetime) -> datetime:
        """Return the datetime of the pool's daily deadline (allowed_end or midnight).

        This is the latest moment the pool can still be running, used by must_run_now
        and urgency_modifier to measure how much time is left in the day.
        Falls back to tomorrow's midnight when no allowed_end is configured.
        """
        end_t = self._allowed_end_t
        if end_t is not None:
            candidate = datetime.combine(now.date(), end_t)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate
        return datetime.combine(now.date() + timedelta(days=1), time(0, 0))

    def _pool_required_minutes_live(self, reader: StateReader) -> float:
        """Read the filtration entity directly (only used for the 05:00 snapshot)."""
        raw = self._state_float(reader, self.pool_filtration_entity)
        return raw * 60.0  # hours → minutes

    def _pool_required_minutes(self, reader: StateReader) -> float:
        """Return the stable daily snapshot, or live value if snapshot not yet taken."""
        if self.pool_required_minutes_today is not None:
            return self.pool_required_minutes_today
        # Before 05:00 or entity unavailable at 05:00: use live value so the
        # force-mode path still works, but dispatch won't run (score = 0 at night).
        return self._pool_required_minutes_live(reader)

    # ------------------------------------------------------------------
    # Deadline / departure urgency helper
    # ------------------------------------------------------------------
    def _compute_auto_deadline(self, now: datetime) -> datetime:
        """Auto-deadline based on configured slots (default: "12:00,18:00").

        Picks the first slot still in the future relative to *now*.
        If all slots have passed, falls back to midnight.
        """
        now_t = now.time().replace(second=0, microsecond=0)
        for slot in sorted(self.appliance_deadline_slots):
            if slot > now_t:
                return now.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def _deadline_urgency_dt(
        self,
        deadline_dt: datetime,
        seconds_needed: float,
        now: datetime | None = None,
    ) -> float:
        """Urgency ramp [0..1] from a concrete deadline datetime.

        margin = temps restant − durée du cycle
        ≤ 0       → 1.0  (plus assez de temps, démarrage forcé)
        < 1 h     → 0.8  (démarrage prioritaire)
        < 3 h     → rampe linéaire 0.3 → 0.8
        ≥ 3 h     → 0.3  (baseline)
        """
        _now = now or datetime.now()
        seconds_left = (deadline_dt - _now).total_seconds()
        margin = seconds_left - seconds_needed
        if margin <= 0:
            return 1.0
        if margin < 3_600:
            return 0.8
        if margin < 10_800:
            return 0.3 + 0.5 * (1.0 - margin / 10_800)
        return 0.3

    def _deadline_urgency(
        self,
        deadline_str: str | None,
        energy_wh: float | None,
        power_w: float,
    ) -> float:
        """Returns [0..1] — rises as deadline approaches.

        0.3 baseline when no deadline; 1.0 when no time left.
        Used for EV departure time and legacy appliance deadline strings.
        """
        parsed = _parse_time(deadline_str)
        if not deadline_str or parsed is None:
            return 0.3

        now         = datetime.now()
        deadline_dt = datetime.combine(now.date(), parsed)
        if deadline_dt <= now:
            deadline_dt += timedelta(days=1)  # already passed today → tomorrow

        if energy_wh is not None and power_w:
            seconds_needed = (energy_wh / power_w) * 3600
        elif self.appliance_cycle_duration_minutes:
            seconds_needed = self.appliance_cycle_duration_minutes * 60
        else:
            seconds_needed = 3600  # 1 h fallback

        return self._deadline_urgency_dt(deadline_dt, seconds_needed, now=now)

    # ------------------------------------------------------------------
    # State readers — decoupled from HomeAssistant via StateReader callable
    # ------------------------------------------------------------------
    @staticmethod
    def _make_ha_reader(hass: HomeAssistant) -> StateReader:
        """Build a StateReader from a live HomeAssistant instance."""
        def reader(entity_id: str) -> str | None:
            s = hass.states.get(entity_id)
            return s.state if s is not None else None
        return reader

    @staticmethod
    def _state_float(
        reader: StateReader,
        entity_id: str | None,
        fallback: float = 0.0,
    ) -> float:
        if not entity_id:
            return fallback
        raw = reader(entity_id)
        if raw is None or raw in ("unavailable", "unknown"):
            return fallback
        try:
            return float(raw)
        except ValueError:
            return fallback

    @staticmethod
    def _state_bool(
        reader: StateReader,
        entity_id: str | None,
        fallback: bool = True,
    ) -> bool:
        if not entity_id:
            return fallback
        raw = reader(entity_id)
        if raw is None or raw in ("unavailable", "unknown"):
            return fallback
        return raw in ("on", "true", "home", "connected", "plugged_in", "1")


# ---------------------------------------------------------------------------
# BatteryDevice — represents the home battery in the dispatch loop
# ---------------------------------------------------------------------------

class BatteryDevice:
    """Represents the home battery as a dispatch candidate.

    Unlike ManagedDevice, BatteryDevice does not flip a physical switch.
    Its role is to reserve budget in the dispatch loop so that the BMS
    (which charges the battery autonomously) is accounted for correctly.
    The fit is always 1.0; urgency and power_w drive its position in the
    sorted candidate list.
    """

    def __init__(self, config: dict[str, Any], manual_entity: str | None = None) -> None:
        self.name: str              = "battery"
        self.priority: int          = int(config.get(CONF_BATTERY_PRIORITY, DEFAULT_BATTERY_PRIORITY))
        self._soc_min: float        = float(config.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
        self._soc_max: float        = float(config.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX))
        self._soc_min_rouge: float  = float(config.get(CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE))
        self._charge_max_w: float   = float(config.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 0.0))
        self._manual_entity: str | None = manual_entity

        # Runtime state — updated each dispatch cycle via update()
        self._soc: float            = 0.0
        self._tempo_red: bool       = False

        # Interface fields (shared with ManagedDevice)
        self.fit: float             = 1.0
        self.is_on: bool            = False
        self.manual_mode: bool      = False
        self.last_reason: str       = ""
        self.last_effective_score: float = 0.0
        self.min_on_remaining_s: float   = 0.0
        # BatteryDevice has no min_on concept — turned_on_at always None so
        # _min_on_elapsed() returns True immediately (no constraint to enforce).
        self.turned_on_at: float | None = None

    def update(self, soc: float | None, tempo_red: bool) -> None:
        """Update runtime state before each dispatch cycle."""
        self._soc        = soc if soc is not None else 0.0
        self._tempo_red  = tempo_red

    @property
    def soc_min_jour(self) -> float:
        """Effective minimum SOC for today: higher on red Tempo days."""
        return self._soc_min_rouge if self._tempo_red else self._soc_min

    @property
    def urgency(self) -> float:
        """[0..1] — 1.0 when SOC < soc_min_jour, 0.0 when SOC ≥ soc_max."""
        soc_min = self.soc_min_jour
        soc_max = self._soc_max
        if self._soc < soc_min:
            return 1.0
        if self._soc >= soc_max:
            return 0.0
        denom = max(soc_max - soc_min, 1.0)
        return (soc_max - self._soc) / denom

    @property
    def power_w(self) -> float:
        """Charge power demand (W) — proportional to urgency."""
        soc_min = self.soc_min_jour
        soc_max = self._soc_max
        if self._soc <= soc_min:
            return self._charge_max_w
        if self._soc >= soc_max:
            return 0.0
        denom = max(soc_max - soc_min, 1.0)
        return self._charge_max_w * (soc_max - self._soc) / denom

    @property
    def satisfied(self) -> bool:
        """True when SOC has reached soc_max."""
        return self._soc >= self._soc_max

    @property
    def effective_score(self) -> float:
        """0.4×priority/10 + 0.3×fit(=1.0) + 0.3×urgency."""
        return 0.4 * (self.priority / 10.0) + 0.3 * 1.0 + 0.3 * self.urgency

    def is_manual(self, reader: StateReader) -> bool:
        """True when manual_mode is set or the helios_battery_manual switch is on."""
        if self.manual_mode:
            return True
        if not self._manual_entity:
            return False
        raw = reader(self._manual_entity)
        if raw is None or raw in ("unavailable", "unknown"):
            return False
        return raw in ("on", "true", "1")
