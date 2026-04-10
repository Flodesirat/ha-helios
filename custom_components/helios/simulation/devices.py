"""Device model for the day simulation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.helios.managed_device import StateReader


@dataclass
class SimDevice:
    """A controllable device in the simulation."""

    name: str
    power_w: float

    # Scheduling
    allowed_start: float = 0.0    # hour
    allowed_end: float = 24.0     # hour
    priority: int = 5             # 1–10
    min_on_minutes: float = 0.0

    # Optional constraints
    run_quota_h: float | None = None   # pool: daily run-time target (hours)
    must_run_daily: bool = False       # water heater: legionella safety

    # Scoring weights (must sum ≈ 1.0)
    w_priority: float = 0.30
    w_fit: float = 0.40
    w_urgency: float = 0.30

    # ---- Physical state for real-model dispatch ----
    # Device type — set to match ManagedDevice.device_type
    device_type: str = "generic"

    # Water heater physical state
    # wh_temp starts at the real temperature (read from HA at 05:00).
    # The simulation tracks it: heats up when ON, cools slowly when OFF.
    wh_temp: float | None = None            # °C, None = not tracked
    wh_temp_target: float = 60.0            # °C
    wh_temp_min: float = 45.0              # °C — legionella floor
    wh_off_peak_hysteresis_k: float = 3.0  # °C — must_run_now trigger band
    wh_temp_entity: str | None = None      # entity ID used by ManagedDevice
    wh_temp_min_entity: str | None = None

    # EV physical state
    ev_soc: float | None = None       # %, None = not tracked
    ev_soc_target: float = 80.0       # %
    ev_plugged: bool = True           # True = always plugged in simulation
    ev_soc_entity: str | None = None
    ev_plugged_entity: str | None = None

    # Pool: required filtration minutes for the day (snapshot from HA at 05:00)
    pool_required_min: float | None = None   # minutes; None = use run_quota_h
    pool_filtration_entity: str | None = None

    # Appliance state machine
    appliance_cycle_duration_minutes: int | None = None  # None → ManagedDevice default (120 min)
    appliance_ready_at_start: bool = False    # True → pre-set ManagedDevice to PREPARING at t=0
    appliance_ready_at_hour: float | None = None  # Set to PREPARING when sim hour >= this value

    # ---- Runtime state (reset each simulation run) ----
    active: bool = field(default=False, init=False)
    _on_minutes: float = field(default=0.0, init=False, repr=False)   # minutes spent ON this cycle
    run_today_h: float = field(default=0.0, init=False)
    energy_kwh: float = field(default=0.0, init=False)
    energy_from_pv_kwh: float = field(default=0.0, init=False)

    # ---- Queries ----
    def in_window(self, hour: float) -> bool:
        if self.allowed_end > self.allowed_start:
            return self.allowed_start <= hour < self.allowed_end
        # Overnight window (e.g. 22h → 6h)
        return hour >= self.allowed_start or hour < self.allowed_end

    def satisfied(self) -> bool:
        """Simplified satisfaction — used as fallback when no ManagedDevice."""
        if self.wh_temp is not None:
            return self.wh_temp >= self.wh_temp_target
        if self.ev_soc is not None:
            return self.ev_soc >= self.ev_soc_target
        if self.run_quota_h is not None:
            return self.run_today_h >= self.run_quota_h
        return False

    def min_on_respected(self) -> bool:
        """True when the device has been ON long enough to allow turn-off."""
        return self._on_minutes >= self.min_on_minutes

    # ---- State transitions ----
    def turn_on(self) -> None:
        if not self.active:
            self.active = True
            self._on_minutes = 0.0

    def turn_off(self) -> None:
        self.active = False
        self._on_minutes = 0.0

    def tick(self, step_minutes: float, pv_w: float, total_load_w: float) -> None:
        """Advance one simulation step (energy accounting + physical state)."""
        if self.active:
            self._on_minutes += step_minutes
            step_h = step_minutes / 60.0
            self.run_today_h += step_h
            e = self.power_w * step_h / 1000.0
            self.energy_kwh += e
            pv_share = min(pv_w, total_load_w) / max(total_load_w, 1.0)
            self.energy_from_pv_kwh += e * pv_share

        # Physical state updates (independent of active/inactive)
        self._tick_physical(step_minutes)

    def _tick_physical(self, step_minutes: float) -> None:
        """Update physical state (temperature, SOC) based on current active state."""
        step_h = step_minutes / 60.0

        # Water heater: heating model
        # Heat rate: power / (volume * cp) where cp = 1.163 Wh/(L·K)
        # Estimate tank volume from power (200 L at 2000 W is typical)
        if self.wh_temp is not None:
            tank_l = max(50.0, min(300.0, self.power_w / 10.0))
            cp = 1.163  # Wh/(L·K)
            if self.active:
                heat_rate = self.power_w / (tank_l * cp)  # °C/h
                self.wh_temp = min(self.wh_temp_target + 5.0, self.wh_temp + heat_rate * step_h)
            else:
                # Thermal losses ~0.5 °C/h (approximate for a well-insulated tank)
                self.wh_temp = max(15.0, self.wh_temp - 0.5 * step_h)

        # EV: charging model (simplified: full power when active)
        if self.ev_soc is not None and self.ev_plugged:
            if self.active:
                # energy_wh = power_w * step_h, convert to % SOC
                # Assume EV capacity from power: typical 3700 W → 50 kWh, 7400 W → 80 kWh
                ev_capacity_kwh = max(20.0, self.power_w / 74.0)  # rough heuristic
                delta_soc = (self.power_w * step_h / 1000.0) / ev_capacity_kwh * 100.0
                self.ev_soc = min(100.0, self.ev_soc + delta_soc)

    def make_state_dict(self) -> "dict[str, str]":
        """Build a {entity_id: state_string} dict for SimHass.

        Includes all physical-state entities that ManagedDevice methods read via
        a StateReader.  Used both by make_state_reader() and by the async_dispatch
        simulation path (SimHass).
        """
        state: dict[str, str] = {}
        if self.wh_temp is not None and self.wh_temp_entity:
            state[self.wh_temp_entity] = str(self.wh_temp)
        if self.wh_temp_min_entity:
            state[self.wh_temp_min_entity] = str(self.wh_temp_min)
        if self.ev_soc is not None and self.ev_soc_entity:
            state[self.ev_soc_entity] = str(self.ev_soc)
        if self.ev_plugged_entity:
            state[self.ev_plugged_entity] = "on" if self.ev_plugged else "off"
        if self.pool_filtration_entity and self.pool_required_min is not None:
            # Filtration entity is in hours (ManagedDevice converts h → min internally)
            state[self.pool_filtration_entity] = str(self.pool_required_min / 60.0)
        return state

    def make_state_reader(self) -> "StateReader":
        """Build a StateReader for this device's entities (used by ManagedDevice methods).

        Returns a callable mapping entity_id → current state string, reading from
        this SimDevice's physical state fields.
        """
        state = self.make_state_dict()
        return lambda eid: state.get(eid)

    def to_managed_config(self) -> dict:
        """Build a config dict compatible with ManagedDevice.__init__.

        Maps SimDevice fields to the const keys expected by ManagedDevice so that
        the real dispatch logic (is_satisfied, must_run_now, urgency_modifier, …)
        operates correctly on this simulated device.
        """
        from custom_components.helios.const import (
            CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_POWER_W,
            CONF_DEVICE_PRIORITY, CONF_DEVICE_MIN_ON_MINUTES,
            CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
            CONF_DEVICE_MUST_RUN_DAILY,
            CONF_DEVICE_WEIGHT_PRIORITY, CONF_DEVICE_WEIGHT_FIT, CONF_DEVICE_WEIGHT_URGENCY,
            CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
            CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
            CONF_WH_TEMP_MIN_ENTITY, CONF_WH_OFF_PEAK_HYSTERESIS_K,
            CONF_POOL_FILTRATION_ENTITY,
            CONF_APPLIANCE_CYCLE_DURATION_MINUTES, CONF_APPLIANCE_START_SCRIPT,
            DEVICE_TYPE_APPLIANCE,
        )

        def _h2hm(h: float) -> str:
            """Convert decimal hours to 'HH:MM' string; clamp at 23:59."""
            if h >= 24.0:
                return "23:59"
            hh = int(h)
            mm = round((h - hh) * 60)
            if mm >= 60:
                hh, mm = hh + 1, 0
            return f"{hh:02d}:{mm:02d}"

        cfg: dict = {
            CONF_DEVICE_NAME:            self.name,
            CONF_DEVICE_TYPE:            self.device_type,
            CONF_DEVICE_POWER_W:         self.power_w,
            CONF_DEVICE_PRIORITY:        self.priority,
            CONF_DEVICE_MIN_ON_MINUTES:  self.min_on_minutes,
            CONF_DEVICE_ALLOWED_START:   _h2hm(self.allowed_start),
            CONF_DEVICE_ALLOWED_END:     _h2hm(self.allowed_end),
            CONF_DEVICE_MUST_RUN_DAILY:  self.must_run_daily,
            CONF_DEVICE_WEIGHT_PRIORITY: self.w_priority,
            CONF_DEVICE_WEIGHT_FIT:      self.w_fit,
            CONF_DEVICE_WEIGHT_URGENCY:  self.w_urgency,
        }
        # Water heater
        if self.wh_temp_entity:
            cfg[CONF_WH_TEMP_ENTITY] = self.wh_temp_entity
        cfg[CONF_WH_TEMP_TARGET]           = self.wh_temp_target
        cfg[CONF_WH_TEMP_MIN]              = self.wh_temp_min
        if self.wh_temp_min_entity:
            cfg[CONF_WH_TEMP_MIN_ENTITY]   = self.wh_temp_min_entity
        cfg[CONF_WH_OFF_PEAK_HYSTERESIS_K] = self.wh_off_peak_hysteresis_k
        # EV
        if self.ev_soc_entity:
            cfg[CONF_EV_SOC_ENTITY]      = self.ev_soc_entity
        cfg[CONF_EV_SOC_TARGET]          = self.ev_soc_target
        if self.ev_plugged_entity:
            cfg[CONF_EV_PLUGGED_ENTITY]  = self.ev_plugged_entity
        # Pool
        if self.pool_filtration_entity:
            cfg[CONF_POOL_FILTRATION_ENTITY] = self.pool_filtration_entity
        # Appliance — dummy start script suppresses the "no start_script" warning;
        # SimHass.services.async_call is a no-op so nothing actually runs.
        if self.device_type == DEVICE_TYPE_APPLIANCE:
            cfg[CONF_APPLIANCE_START_SCRIPT] = "script.sim_noop"
        if self.appliance_cycle_duration_minutes is not None:
            cfg[CONF_APPLIANCE_CYCLE_DURATION_MINUTES] = self.appliance_cycle_duration_minutes
        return cfg


# ---------------------------------------------------------------------------
# Load from JSON / Default device set
# ---------------------------------------------------------------------------

def load_appliance_schedule(path: str) -> dict[str, float]:
    """Load appliance schedule JSON → dict {device_name: ready_at_hour}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {entry["name"]: float(entry["ready_at_hour"]) for entry in data if "name" in entry and "ready_at_hour" in entry}


def apply_appliance_schedule(devices: list[SimDevice], schedule: dict[str, float]) -> None:
    """Inject ready_at_hour into appliance SimDevices from a schedule dict (in-place)."""
    for dev in devices:
        if dev.device_type == "appliance" and dev.name in schedule:
            dev.appliance_ready_at_hour = schedule[dev.name]


def load_devices_from_json(path: str) -> list[SimDevice]:
    """Load a device list from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    devices = []
    for d in data:
        device_type = str(d.get("device_type", "generic"))
        run_quota_h = float(d["run_quota_h"]) if "run_quota_h" in d else None
        # For pool devices, derive pool_required_min from run_quota_h so that
        # ManagedDevice.is_satisfied() (pool type) stops the pump once the quota is reached.
        pool_required_min: float | None = None
        if device_type == "pool" and run_quota_h is not None:
            pool_required_min = run_quota_h * 60.0
        cycle_min = d.get("appliance_cycle_duration_minutes")
        devices.append(SimDevice(
            name=d["name"],
            power_w=float(d["power_w"]),
            allowed_start=float(d.get("allowed_start", 0.0)),
            allowed_end=float(d.get("allowed_end", 24.0)),
            priority=int(d.get("priority", 5)),
            min_on_minutes=float(d.get("min_on_minutes", 0.0)),
            run_quota_h=run_quota_h,
            must_run_daily=bool(d.get("must_run_daily", False)),
            device_type=device_type,
            pool_required_min=pool_required_min,
            appliance_cycle_duration_minutes=int(cycle_min) if cycle_min is not None else None,
            appliance_ready_at_start=bool(d.get("appliance_ready_at_start", False)),
            appliance_ready_at_hour=float(h) if (h := d.get("appliance_ready_at_hour")) is not None else None,
            w_priority=float(d.get("w_priority", 0.30)),
            w_fit=float(d.get("w_fit", 0.40)),
            w_urgency=float(d.get("w_urgency", 0.30)),
        ))
    return devices


def default_devices() -> list[SimDevice]:
    return [
        SimDevice(
            name="Chauffe-eau",
            power_w=2000,
            allowed_start=8.0,
            allowed_end=18.0,
            priority=8,
            min_on_minutes=30,
            must_run_daily=True,
        ),
        SimDevice(
            name="Pompe piscine",
            power_w=800,
            allowed_start=8.0,
            allowed_end=20.0,
            priority=6,
            min_on_minutes=60,
            run_quota_h=5.0,
        ),
        SimDevice(
            name="Lave-vaisselle",
            power_w=1200,
            allowed_start=10.0,
            allowed_end=17.0,
            priority=4,
            min_on_minutes=90,
        ),
        SimDevice(
            name="Charge VE",
            power_w=3700,
            allowed_start=8.0,
            allowed_end=20.0,
            priority=7,
            min_on_minutes=120,
        ),
    ]
