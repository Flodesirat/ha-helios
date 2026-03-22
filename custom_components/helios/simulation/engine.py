"""Simulation loop: dispatch, battery, energy accounting."""
from __future__ import annotations

import json
import random
import sys
import os
from dataclasses import dataclass, field
from typing import Callable

# Allow running from repo root or simulation/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .profiles import pv_power_w, base_load_w, tempo_color, Season, CloudCover
from .devices import SimDevice, default_devices

# Use the real ScoringEngine when available
try:
    from custom_components.helios.scoring_engine import ScoringEngine as _RealEngine
    _HAS_ENGINE = True
except ImportError:
    _HAS_ENGINE = False


STEP_MINUTES = 5
STEPS_PER_DAY = 24 * 60 // STEP_MINUTES  # 288


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------

@dataclass
class Tariff:
    """EDF Tempo tariff — prices in €/kWh, HC window defined by hc_start/hc_end."""
    blue_hc: float = 0.1325
    blue_hp: float = 0.1612
    white_hc: float = 0.1499
    white_hp: float = 0.1871
    red_hc: float = 0.1575
    red_hp: float = 0.7060
    hc_start: float = 22.0   # HC begins at 22h
    hc_end: float = 6.0      # HC ends at 6h

    @staticmethod
    def from_json(path: str) -> "Tariff":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return Tariff(
            blue_hc=d["blue"]["hc"],   blue_hp=d["blue"]["hp"],
            white_hc=d["white"]["hc"], white_hp=d["white"]["hp"],
            red_hc=d["red"]["hc"],     red_hp=d["red"]["hp"],
            hc_start=d.get("hc_start", 22.0),
            hc_end=d.get("hc_end", 6.0),
        )

    def price(self, hour: float, tempo: str) -> float:
        """Return €/kWh for the given hour and Tempo day color."""
        is_hc = hour >= self.hc_start or hour < self.hc_end
        slot = "hc" if is_hc else "hp"
        return getattr(self, f"{tempo}_{slot}")


# ---------------------------------------------------------------------------
# Scoring (wraps real engine or inline fallback)
# ---------------------------------------------------------------------------

def _score(
    surplus_w: float,
    tempo: str,
    soc: float | None,
    config: dict,
    forecast_kwh: float | None = None,
) -> float:
    if _HAS_ENGINE:
        eng = _RealEngine(config)
        return eng.compute({
            "surplus_w":   surplus_w,
            "tempo_color": tempo,
            "battery_soc": soc,
            "forecast_kwh": forecast_kwh,
        })
    # Inline fallback — mirrors ScoringEngine logic
    s_surplus = min(1.0, surplus_w / 500.0) if surplus_w > 0 else 0.0
    s_tempo = {"blue": 1.0, "white": 0.5, "red": 0.0}.get(tempo, 0.5)
    if soc is None:
        s_soc = 0.5
    elif soc <= 15:
        s_soc = 0.0
    elif soc <= 40:
        s_soc = (soc - 15) / 25.0
    elif soc <= 60:
        s_soc = 1.0
    elif soc <= 85:
        s_soc = 1.0 - 0.5 * (soc - 60) / 25.0
    else:
        s_soc = 0.5

    if forecast_kwh is None or forecast_kwh <= 0.0:
        s_forecast = 0.5
    elif forecast_kwh <= 2.0:
        s_forecast = 0.5 + 0.3 * (forecast_kwh / 2.0)
    elif forecast_kwh <= 5.0:
        s_forecast = 0.8 - 0.4 * (forecast_kwh - 2.0) / 3.0
    elif forecast_kwh <= 10.0:
        s_forecast = 0.4 - 0.2 * (forecast_kwh - 5.0) / 5.0
    else:
        s_forecast = 0.2

    w_s = config.get("weight_pv_surplus", 0.4)
    w_t = config.get("weight_tempo", 0.3)
    w_b = config.get("weight_battery_soc", 0.2)
    w_f = config.get("weight_forecast", 0.1)
    return w_s * s_surplus + w_t * s_tempo + w_b * s_soc + w_f * s_forecast


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _fit_score(device: SimDevice, surplus_w: float, bat_available_w: float) -> float:
    """Zone 1 / 2 / 3 fit score (mirrors device_manager logic)."""
    effective = surplus_w + bat_available_w
    if surplus_w <= 0:
        return 0.0
    if device.power_w <= surplus_w:
        return device.power_w / max(surplus_w, 1.0)
    if device.power_w <= effective:
        bat_fraction = (device.power_w - surplus_w) / max(bat_available_w, 1.0)
        return 1.0 - 0.4 * bat_fraction
    grid_fraction = (device.power_w - effective) / max(device.power_w, 1.0)
    return 0.4 * (1.0 - grid_fraction)


def _urgency(device: SimDevice) -> float:
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
) -> None:
    """Greedy dispatch — turns devices on/off in-place."""
    # Turn off devices that are no longer eligible
    for dev in devices:
        if not dev.active:
            continue
        if not dev.in_window(hour):
            dev.turn_off()
            continue
        if dev.satisfied() and dev.min_on_respected():
            dev.turn_off()
            continue
        if global_score < threshold and dev.min_on_respected():
            dev.turn_off()

    # Rank eligible devices
    candidates: list[tuple[float, SimDevice]] = []
    for dev in devices:
        if dev.active or dev.satisfied() or not dev.in_window(hour):
            continue
        fit = _fit_score(dev, surplus_w, bat_available_w)
        urg = _urgency(dev)
        pri = dev.priority / 10.0
        eff = (dev.w_priority * pri + dev.w_fit * fit + dev.w_urgency * urg)
        candidates.append((eff, dev))

    candidates.sort(key=lambda x: x[0], reverse=True)

    remaining = surplus_w + bat_available_w
    for eff_score, dev in candidates:
        if global_score < threshold:
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
    tariff: Tariff = field(default_factory=Tariff)
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

    @property
    def savings_eur(self) -> float:
        return self.cost_no_pv_eur - self.cost_eur

    @property
    def autoconsumption_rate(self) -> float:
        return self.e_self_consumed_kwh / max(self.e_pv_kwh, 1e-6)

    @property
    def self_sufficiency_rate(self) -> float:
        return self.e_self_consumed_kwh / max(self.e_load_kwh, 1e-6)


def run(cfg: SimConfig, devices: list[SimDevice] | None = None) -> SimResult:
    if devices is None:
        devices = default_devices()

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

    e_pv = e_load = e_self = e_import = e_export = cost = cost_no_pv = 0.0
    steps: list[StepResult] = []

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
        current_devices_w = sum(d.power_w for d in devices if d.active)
        surplus_w = max(0.0, pv_w - base_w - current_devices_w)
        global_score = _score(
            surplus_w, t_color,
            bat_soc if cfg.bat_enabled else None,
            cfg.scoring,
            forecast_kwh=_forecast_table[i],
        )

        # ---- Dispatch ----
        dispatch(devices, hour, pv_w - base_w, bat_available_w, global_score, cfg.dispatch_threshold)

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
        e_self += min(pv_w, total_load_w) * step_h / 1000
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
    )
