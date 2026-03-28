"""Simulation loop: dispatch, battery, energy accounting."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, date as _date
from pathlib import Path
from typing import Callable

_TARIFF_JSON = Path(__file__).parent / "config" / "tariff.json"

from .profiles import pv_power_w, base_load_w, tempo_color, Season, CloudCover
from .devices import SimDevice, default_devices
from custom_components.helios.scoring_engine import ScoringEngine as _ScoringEngine
from custom_components.helios.managed_device import ManagedDevice as _ManagedDevice


STEP_MINUTES = 5
STEPS_PER_DAY = 24 * 60 // STEP_MINUTES  # 288


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------

@dataclass
class Tariff:
    """EDF Tempo tariff — prices in €/kWh, HC window defined by hc_start/hc_end.

    Use ``Tariff.default()`` to load the bundled config/tariff.json,
    or ``Tariff.from_json(path)`` for a custom file.
    """
    blue_hc: float
    blue_hp: float
    white_hc: float
    white_hp: float
    red_hc: float
    red_hp: float
    hc_start: float   # HC begins at this hour
    hc_end: float     # HC ends at this hour

    @classmethod
    def default(cls) -> "Tariff":
        """Load from the bundled simulation/config/tariff.json."""
        return cls.from_json(_TARIFF_JSON)

    @classmethod
    def from_json(cls, path: str | Path) -> "Tariff":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            blue_hc=d["blue"]["hc"],   blue_hp=d["blue"]["hp"],
            white_hc=d["white"]["hc"], white_hp=d["white"]["hp"],
            red_hc=d["red"]["hc"],     red_hp=d["red"]["hp"],
            hc_start=d["hc_start"],
            hc_end=d["hc_end"],
        )

    def price(self, hour: float, tempo: str) -> float:
        """Return €/kWh for the given hour and Tempo day color."""
        is_hc = hour >= self.hc_start or hour < self.hc_end
        slot = "hc" if is_hc else "hp"
        return getattr(self, f"{tempo}_{slot}")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(
    surplus_w: float,
    tempo: str,
    soc: float | None,
    forecast_kwh: float | None,
    engine: _ScoringEngine,
) -> float:
    return engine.compute({
        "surplus_w":    surplus_w,
        "tempo_color":  tempo,
        "battery_soc":  soc,
        "forecast_kwh": forecast_kwh,
    })


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _effective_score(
    dev: SimDevice,
    managed: _ManagedDevice | None,
    surplus_w: float,
    bat_available_w: float,
    sim_now: datetime,
) -> float:
    """Compute effective score using real ManagedDevice logic when available."""
    if managed is not None:
        reader = dev.make_state_reader()
        return managed.effective_score(reader, surplus_w, bat_available_w, now=sim_now)
    fit = _ManagedDevice.compute_fit_score(dev.power_w, surplus_w, bat_available_w)
    urg = _urgency_inline(dev)
    pri = dev.priority / 10.0
    total_w = dev.w_priority + dev.w_fit + dev.w_urgency
    return (dev.w_priority * pri + dev.w_fit * fit + dev.w_urgency * urg) / max(total_w, 1e-6)


def _is_satisfied(dev: SimDevice, managed: _ManagedDevice | None, sim_now: datetime) -> bool:
    """Return True if device has reached its target."""
    if managed is not None:
        reader = dev.make_state_reader()
        return managed.is_satisfied(reader, now=sim_now)
    return dev.satisfied()


def _must_run(dev: SimDevice, managed: _ManagedDevice | None, sim_now: datetime) -> bool:
    """Return True if device must run regardless of score."""
    if managed is not None:
        reader = dev.make_state_reader()
        return managed.must_run_now(reader, now=sim_now)
    return False


def _urgency_inline(device: SimDevice) -> float:
    """Urgency fallback when ManagedDevice not available."""
    if device.run_quota_h is not None:
        remaining_quota = max(0.0, device.run_quota_h - device.run_today_h)
        return min(1.0, remaining_quota / max(device.run_quota_h, 0.01))
    if device.must_run_daily:
        return 0.5
    return 0.3


def dispatch(
    devices: list[SimDevice],
    hour: float,
    surplus_w: float,
    bat_available_w: float,
    global_score: float,
    threshold: float,
    managed_devices: list[_ManagedDevice | None] | None = None,
    sim_now: datetime | None = None,
) -> None:
    """Greedy dispatch — turns devices on/off in-place.

    When *managed_devices* is provided (parallel list to *devices*), uses the
    real ManagedDevice logic (is_satisfied, must_run_now, urgency_modifier,
    effective_score) instead of the simplified inline fallbacks.
    """
    _now = sim_now or datetime.now()
    managed = managed_devices or [None] * len(devices)

    # ---- Must-run overrides (forced on regardless of score) ----
    for dev, mgd in zip(devices, managed):
        if _must_run(dev, mgd, _now):
            dev.turn_on()

    # ---- Turn off devices that are no longer eligible ----
    for dev, mgd in zip(devices, managed):
        if not dev.active:
            continue
        if _must_run(dev, mgd, _now):
            continue  # must-run devices stay on
        if not dev.in_window(hour):
            dev.turn_off()
            continue
        if _is_satisfied(dev, mgd, _now) and dev.min_on_respected():
            dev.turn_off()
            continue
        if global_score < threshold and dev.min_on_respected():
            dev.turn_off()

    # ---- Rank eligible devices ----
    candidates: list[tuple[float, SimDevice, _ManagedDevice | None]] = []
    for dev, mgd in zip(devices, managed):
        if dev.active or _is_satisfied(dev, mgd, _now) or not dev.in_window(hour):
            continue
        eff = _effective_score(dev, mgd, surplus_w, bat_available_w, _now)
        candidates.append((eff, dev, mgd))

    candidates.sort(key=lambda x: x[0], reverse=True)

    remaining = surplus_w + bat_available_w
    for eff_score, dev, mgd in candidates:
        if global_score < threshold and not _must_run(dev, mgd, _now):
            break
        if dev.power_w <= remaining * 1.10:   # 10 % tolerance
            dev.turn_on()
            remaining -= dev.power_w


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    hour: float
    pv_w: float
    base_w: float
    devices_w: float
    total_load_w: float
    surplus_w: float
    grid_w: float         # >0 = import, <0 = export
    bat_soc: float
    bat_action: str
    bat_w: float              # >0 = charging, <0 = discharging, 0 = idle
    score: float
    active_devices: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------

@dataclass
class SimConfig:
    season: Season = "summer"
    cloud: CloudCover = "clear"
    peak_pv_w: float = 4000.0
    tempo: str = "blue"
    bat_soc_start: float = 50.0
    bat_enabled: bool = True
    bat_capacity_kwh: float = 10.0
    bat_max_charge_w: float = 2000.0
    bat_max_discharge_w: float = 2000.0
    bat_soc_min: float = 20.0
    bat_soc_max: float = 95.0
    bat_efficiency: float = 0.75       # round-trip efficiency (0–1)
    bat_discharge_start: float = 6.0   # hour before which battery never discharges
    dispatch_threshold: float = 0.30
    forecast_noise: float = 0.15    # std-dev of forecast error (0=perfect, 0.15=±15%)
    base_load_noise: float = 0.0   # day-level multiplicative Gaussian noise on base load
    base_load_fn: Callable[[float], float] | None = None
    tariff: Tariff = field(default_factory=Tariff.default)
    scoring: dict = field(default_factory=lambda: {
        "weight_pv_surplus": 0.4,
        "weight_tempo": 0.3,
        "weight_battery_soc": 0.2,
        "weight_forecast": 0.1,
    })


@dataclass
class SimResult:
    steps: list[StepResult]
    devices: list[SimDevice]
    e_pv_kwh: float
    e_load_kwh: float
    e_self_consumed_kwh: float
    e_grid_import_kwh: float
    e_grid_export_kwh: float
    bat_soc_end: float
    cost_eur: float = 0.0
    cost_no_pv_eur: float = 0.0
    decision_log: list[dict] = field(default_factory=list)

    @property
    def savings_eur(self) -> float:
        return self.cost_no_pv_eur - self.cost_eur

    @property
    def autoconsumption_rate(self) -> float:
        return self.e_self_consumed_kwh / max(self.e_pv_kwh, 1e-6)

    @property
    def self_sufficiency_rate(self) -> float:
        return self.e_self_consumed_kwh / max(self.e_load_kwh, 1e-6)


def run(
    cfg: SimConfig,
    devices: list[SimDevice] | None = None,
    managed_devices: list[_ManagedDevice | None] | None = None,
    sim_date: _date | None = None,
) -> SimResult:
    """Run a full-day simulation.

    Args:
        cfg: Simulation configuration.
        devices: SimDevice list (energy accounting + physical state).
        managed_devices: Optional parallel list of ManagedDevice instances.
            When provided, uses the real dispatch logic (is_satisfied,
            must_run_now, urgency_modifier) instead of the inline fallbacks.
        sim_date: Date to use for time-of-day calculations (default: today).
    """
    if devices is None:
        devices = default_devices()
    _sim_date = sim_date or _date.today()

    step_h = STEP_MINUTES / 60.0
    bat_soc = cfg.bat_soc_start
    _base_load = cfg.base_load_fn or base_load_w

    # Pre-compute remaining forecast at each step (clear-sky, deterministic).
    # Mirrors a real "remaining production today" entity: integral of PV from
    # current step to end of day, using the deterministic profile (no cloud noise).
    _pv_clear = [
        pv_power_w(i * step_h, cfg.season, "clear", cfg.peak_pv_w)
        for i in range(STEPS_PER_DAY)
    ]
    # Suffix sums in kWh (step i → remaining from step i onward)
    _forecast_table: list[float] = [0.0] * STEPS_PER_DAY
    acc = 0.0
    for i in range(STEPS_PER_DAY - 1, -1, -1):
        _forecast_table[i] = acc
        acc += _pv_clear[i] * step_h / 1000.0

    # Apply a day-level random error: one correlated multiplier for the whole day
    # (mirrors real forecast behaviour: systematic bias that may be revised mid-day)
    if cfg.forecast_noise > 0.0:
        error = random.gauss(1.0, cfg.forecast_noise)
        error = max(0.4, min(1.6, error))   # clamp to ±60%
        _forecast_table = [v * error for v in _forecast_table]

    # Apply a day-level random multiplicative error on base load.
    # Models uncertainty in household consumption profile (presence, behaviour…).
    _bl_mult = 1.0
    if cfg.base_load_noise > 0.0:
        _bl_mult = random.gauss(1.0, cfg.base_load_noise)
        _bl_mult = max(0.5, min(1.8, _bl_mult))

    _engine = _ScoringEngine(cfg.scoring)

    e_pv = e_load = e_self = e_import = e_export = cost = cost_no_pv = 0.0
    steps: list[StepResult] = []
    decision_log: list[dict] = []

    for i in range(STEPS_PER_DAY):
        hour = i * step_h
        pv_w = pv_power_w(hour, cfg.season, cfg.cloud, cfg.peak_pv_w)
        base_w = _base_load(hour) * _bl_mult
        t_color = tempo_color(hour, cfg.tempo)

        # ---- Battery: estimate discharge available for dispatch ----
        # (self-consumption: battery can cover deficit to avoid grid import)
        bat_can_discharge = cfg.bat_enabled and bat_soc > cfg.bat_soc_min and hour >= cfg.bat_discharge_start
        bat_available_w = 0.0
        if bat_can_discharge:
            surplus_base = pv_w - base_w
            if surplus_base < 0:
                bat_available_w = min(abs(surplus_base), cfg.bat_max_discharge_w)

        # ---- Score ----
        # Use base surplus (PV - base load, without Helios devices) so that
        # currently-ON devices do not penalise their own score each cycle.
        surplus_w = max(0.0, pv_w - base_w)
        global_score = _score(
            surplus_w, t_color,
            bat_soc if cfg.bat_enabled else None,
            _forecast_table[i],
            _engine,
        )

        # ---- Snapshot active states before dispatch (for decision log) ----
        _before = {d.name: d.active for d in devices}

        # ---- Dispatch ----
        h = int(hour)
        m = min(int(round((hour - h) * 60)), 59)
        sim_now = datetime(_sim_date.year, _sim_date.month, _sim_date.day, h, m)
        dispatch(
            devices, hour, pv_w - base_w, bat_available_w, global_score, cfg.dispatch_threshold,
            managed_devices=managed_devices,
            sim_now=sim_now,
        )

        # ---- Record state changes ----
        ts = f"{h:02d}:{m:02d}"
        for d in devices:
            was_on = _before[d.name]
            is_on  = d.active
            if was_on != is_on:
                decision_log.append({
                    "ts":    ts,
                    "device": d.name,
                    "action": "on" if is_on else "off",
                    "score":  round(global_score, 3),
                    "pv_w":  round(pv_w),
                    "surplus_w": round(surplus_w),
                    "bat_soc": round(bat_soc, 1),
                })

        # ---- Power accounting ----
        devices_w = sum(d.power_w for d in devices if d.active)
        total_load_w = base_w + devices_w
        net_w = pv_w - total_load_w                     # >0 = surplus, <0 = deficit
        grid_w = -net_w                                 # >0 = import from grid

        # ---- Battery self-consumption decision (based on real net) ----
        bat_action = "idle"
        bat_w = 0.0
        if cfg.bat_enabled:
            if net_w > 100 and bat_soc < cfg.bat_soc_max:
                # PV surplus after all loads → charge battery
                charge_w = min(net_w * 0.9, cfg.bat_max_charge_w)
                bat_soc = min(cfg.bat_soc_max, bat_soc + charge_w * cfg.bat_efficiency * step_h / (cfg.bat_capacity_kwh * 10))
                bat_action = "charge"
                bat_w = charge_w
                grid_w = -(net_w - charge_w)            # battery absorbs surplus
            elif net_w < -100 and bat_can_discharge:
                # Deficit → discharge battery to avoid grid import
                discharge_w = min(abs(net_w) * 0.9, cfg.bat_max_discharge_w)
                bat_soc = max(cfg.bat_soc_min, bat_soc - (discharge_w / cfg.bat_efficiency) * step_h / (cfg.bat_capacity_kwh * 10))
                bat_action = "discharge"
                bat_w = -discharge_w
                grid_w = -(net_w + discharge_w)         # battery covers part of deficit

        # ---- Device tick ----
        for dev in devices:
            dev.tick(STEP_MINUTES, pv_w, total_load_w)

        # ---- Energy accumulators ----
        e_pv   += pv_w * step_h / 1000
        e_load += total_load_w * step_h / 1000
        # Direct self-consumption: PV covering loads without going through the battery.
        direct_self_w = min(pv_w, total_load_w)
        # Battery self-consumption: PV that charges the battery is self-consumed, but
        # only the energy that survives the round-trip (η at charge, η at discharge)
        # counts as useful. The rest (1 − η²) is wasted as heat.
        bat_self_w = bat_w * (cfg.bat_efficiency ** 2) if bat_action == "charge" else 0.0
        e_self += (direct_self_w + bat_self_w) * step_h / 1000
        step_price = cfg.tariff.price(hour, cfg.tempo)
        if grid_w > 0:
            kwh_imported = grid_w * step_h / 1000
            e_import += kwh_imported
            cost += kwh_imported * step_price
        else:
            e_export += abs(grid_w) * step_h / 1000
        cost_no_pv += total_load_w * step_h / 1000 * step_price

        steps.append(StepResult(
            hour=hour,
            pv_w=pv_w,
            base_w=base_w,
            devices_w=devices_w,
            total_load_w=total_load_w,
            surplus_w=net_w,
            grid_w=grid_w,
            bat_soc=bat_soc,
            bat_action=bat_action,
            bat_w=bat_w,
            score=global_score,
            active_devices=[d.name for d in devices if d.active],
        ))

    return SimResult(
        steps=steps,
        devices=devices,
        e_pv_kwh=e_pv,
        e_load_kwh=e_load,
        e_self_consumed_kwh=e_self,
        e_grid_import_kwh=e_import,
        e_grid_export_kwh=e_export,
        bat_soc_end=bat_soc,
        cost_eur=cost,
        cost_no_pv_eur=cost_no_pv,
        decision_log=decision_log,
    )
