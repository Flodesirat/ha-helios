"""Simulation loop: dispatch, battery, energy accounting."""
from __future__ import annotations

import asyncio
import json
import random
import time as _time_stdlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime as _RealDatetime, date as _date
from pathlib import Path
from typing import Callable

_TARIFF_JSON = Path(__file__).parent / "config" / "tariff.json"

from .profiles import pv_power_w, base_load_w, tempo_color, Season, CloudCover
from .devices import SimDevice, default_devices
from .sim_hass import SimHass
from custom_components.helios.scoring_engine import ScoringEngine as _ScoringEngine
from custom_components.helios.managed_device import ManagedDevice as _ManagedDevice
from custom_components.helios.device_manager import DeviceManager as _DeviceManager


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
# Simulation infra: no-op store + datetime proxy for async_dispatch
# ---------------------------------------------------------------------------

class _SimNoOpStore:
    """Stub for DeviceManager._store — no persistence needed in simulation."""
    async def async_save(self, data: dict) -> None:
        pass

    async def async_load(self) -> dict:
        return {}


class _SimDatetimeProxy:
    """Proxy for ``datetime`` that redirects ``now()`` to a simulated time.

    Installed on the ``device_manager`` and ``managed_device`` module namespaces
    for the duration of the simulation so that allowed-window checks and
    must_run_now logic operate on simulated wall-clock time rather than real time.
    """

    def __init__(self) -> None:
        self._sim_now: _RealDatetime | None = None

    def set_now(self, dt: _RealDatetime) -> None:
        self._sim_now = dt

    # ---- datetime API surface used by device_manager / managed_device ----

    def now(self, tz=None) -> _RealDatetime:
        return self._sim_now if self._sim_now is not None else _RealDatetime.now(tz)

    def combine(self, *args, **kwargs) -> _RealDatetime:
        return _RealDatetime.combine(*args, **kwargs)

    def __call__(self, *args, **kwargs) -> _RealDatetime:
        return _RealDatetime(*args, **kwargs)

    def fromisoformat(self, s: str) -> _RealDatetime:
        return _RealDatetime.fromisoformat(s)


_sim_dt_proxy = _SimDatetimeProxy()


def _build_sim_device_manager(
    managed_devices: list[_ManagedDevice],
    cfg: "SimConfig",
) -> _DeviceManager:
    """Create a DeviceManager bypassing the normal __init__ (which needs hass/Store)."""
    dm = object.__new__(_DeviceManager)
    dm.devices = managed_devices
    dm._dispatch_threshold = cfg.dispatch_threshold
    dm._scan_interval = float(STEP_MINUTES)
    dm.decision_log = deque(maxlen=1000)
    dm.remaining_w = 0.0
    dm._store = _SimNoOpStore()
    dm._coordinator = None
    dm._hass = None
    dm._unsub_ready_listeners = []
    return dm


def _managed_from_sim(sim_devices: list[SimDevice]) -> list[_ManagedDevice]:
    """Create ManagedDevice instances from SimDevice objects."""
    result: list[_ManagedDevice] = []
    for sd in sim_devices:
        md = _ManagedDevice(sd.to_managed_config(), {})
        # Pre-seed pool required minutes so the 05:00 capture is not needed
        if sd.pool_required_min is not None:
            md.pool_required_minutes_today = sd.pool_required_min
        # EV: mirror plugged state
        if sd.ev_plugged_entity is None:
            md.ev_plugged_manual = sd.ev_plugged
        result.append(md)
    return result


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
# Main simulation config / result
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


# ---------------------------------------------------------------------------
# Async simulation (uses real DeviceManager.async_dispatch)
# ---------------------------------------------------------------------------

async def async_run(
    cfg: SimConfig,
    devices: list[SimDevice] | None = None,
    managed_devices: list[_ManagedDevice | None] | None = None,
    sim_date: _date | None = None,
) -> SimResult:
    """Run a full-day simulation using the real DeviceManager.async_dispatch logic.

    This mirrors what the live coordinator does each 5-min scan cycle, including:
    SOC gate, overcommit detection, must_run overrides, and the greedy allocation loop.

    Args:
        cfg:             Simulation configuration.
        devices:         SimDevice list (energy accounting + physical state).
        managed_devices: Optional parallel ManagedDevice list.  When omitted they
                         are auto-created via SimDevice.to_managed_config().
        sim_date:        Date to use for simulated time (default: today).
    """
    if devices is None:
        devices = default_devices()
    _sim_date = sim_date or _date.today()

    # Build ManagedDevice instances if not provided
    if managed_devices is None:
        managed_devices = _managed_from_sim(devices)

    # Build DeviceManager (bypasses __init__ to avoid hass / Store dependency)
    dm = _build_sim_device_manager(managed_devices, cfg)

    step_h = STEP_MINUTES / 60.0
    bat_soc = cfg.bat_soc_start
    _base_load = cfg.base_load_fn or base_load_w

    # Pre-compute forecast table (deterministic clear-sky, suffix sums in kWh)
    _pv_clear = [
        pv_power_w(i * step_h, cfg.season, "clear", cfg.peak_pv_w)
        for i in range(STEPS_PER_DAY)
    ]
    _forecast_table: list[float] = [0.0] * STEPS_PER_DAY
    acc = 0.0
    for i in range(STEPS_PER_DAY - 1, -1, -1):
        _forecast_table[i] = acc
        acc += _pv_clear[i] * step_h / 1000.0

    if cfg.forecast_noise > 0.0:
        error = random.gauss(1.0, cfg.forecast_noise)
        error = max(0.4, min(1.6, error))
        _forecast_table = [v * error for v in _forecast_table]

    _bl_mult = 1.0
    if cfg.base_load_noise > 0.0:
        _bl_mult = random.gauss(1.0, cfg.base_load_noise)
        _bl_mult = max(0.5, min(1.8, _bl_mult))

    _engine = _ScoringEngine(cfg.scoring)

    e_pv = e_load = e_self = e_import = e_export = cost = cost_no_pv = 0.0
    steps: list[StepResult] = []
    decision_log: list[dict] = []

    # Install simulated-time patches for the duration of the simulation.
    # Both device_manager and managed_device import `datetime` from stdlib;
    # replacing the module attribute redirects datetime.now() → simulated time.
    import custom_components.helios.device_manager as _dm_mod
    import custom_components.helios.managed_device as _md_mod
    _dm_mod.datetime = _sim_dt_proxy   # type: ignore[attr-defined]
    _md_mod.datetime = _sim_dt_proxy   # type: ignore[attr-defined]
    _orig_time = _time_stdlib.time

    try:
        for i in range(STEPS_PER_DAY):
            hour = i * step_h
            h = int(hour)
            m = min(int(round((hour - h) * 60)), 59)
            sim_now = _RealDatetime(_sim_date.year, _sim_date.month, _sim_date.day, h, m)

            pv_w = pv_power_w(hour, cfg.season, cfg.cloud, cfg.peak_pv_w)
            base_w = _base_load(hour) * _bl_mult
            t_color = tempo_color(hour, cfg.tempo)

            # Battery discharge headroom
            bat_can_discharge = (
                cfg.bat_enabled and bat_soc > cfg.bat_soc_min and hour >= cfg.bat_discharge_start
            )
            bat_available_w = 0.0
            if bat_can_discharge:
                surplus_base = pv_w - base_w
                if surplus_base < 0:
                    bat_available_w = min(abs(surplus_base), cfg.bat_max_discharge_w)

            # Virtual surplus (PV − base load, without Helios devices)
            # Mirrors coordinator._build_score_input: already adds back helios_on_w
            # because base_w doesn't include them.
            surplus_w = max(0.0, pv_w - base_w)

            global_score = _score(
                surplus_w, t_color,
                bat_soc if cfg.bat_enabled else None,
                _forecast_table[i],
                _engine,
            )

            # Real surplus before dispatch (PV − base − currently-ON devices)
            # Used by DeviceManager for the overcommit check.
            on_w = sum(md.power_w for md in managed_devices if md.is_on)
            real_surplus_w = max(0.0, pv_w - base_w - on_w)

            # Build SimHass from current physical state of all sim devices
            state_dict: dict[str, str] = {}
            for sd in devices:
                state_dict.update(sd.make_state_dict())
            sim_hass = SimHass(state_dict)

            score_input: dict = {
                "global_score":       global_score,
                "surplus_w":          surplus_w,
                "real_surplus_w":     real_surplus_w,
                "bat_available_w":    bat_available_w,
                "dispatch_threshold": cfg.dispatch_threshold,
                "battery_soc":        bat_soc if cfg.bat_enabled else None,
                "tempo_color":        t_color,
                "pv_power_w":         pv_w,
                "house_power_w":      base_w,
                "soc_max":            cfg.bat_soc_max,
                "soc_min":            cfg.bat_soc_min,
                "grid_allowance_w":   (
                    250.0 if cfg.bat_enabled and bat_soc >= cfg.bat_soc_max else 0.0
                ),
                "soc_reserve_rouge":  80.0,
                "forecast_kwh":       _forecast_table[i],
            }

            # Patch time.time so _min_on_elapsed() and pool run-time work correctly.
            # Simulated epoch: minutes since midnight × 60.
            step_epoch = float(i * STEP_MINUTES * 60)
            _time_stdlib.time = lambda _e=step_epoch: _e
            _sim_dt_proxy.set_now(sim_now)

            dm_log_len = len(dm.decision_log)
            await dm.async_dispatch(sim_hass, score_input)

            # Restore time.time immediately after dispatch
            _time_stdlib.time = _orig_time

            # Sync active state: managed device is the source of truth after dispatch
            for sd, md in zip(devices, managed_devices):
                sd.active = md.is_on

            # Collect new decision-log entries from this step
            ts = f"{h:02d}:{m:02d}"
            for entry in list(dm.decision_log)[dm_log_len:]:
                decision_log.append({
                    "ts":       ts,
                    "device":   entry["device"],
                    "action":   entry["action"],
                    "score":    entry.get("global_score", round(global_score, 3)),
                    "pv_w":     entry.get("pv_w", round(pv_w)),
                    "surplus_w": entry.get("surplus_w", round(surplus_w)),
                    "bat_soc":  round(bat_soc, 1),
                })

            # Power accounting
            devices_w = sum(sd.power_w for sd in devices if sd.active)
            total_load_w = base_w + devices_w
            net_w = pv_w - total_load_w
            grid_w = -net_w

            # Battery self-consumption decision
            bat_action = "idle"
            bat_w = 0.0
            if cfg.bat_enabled:
                if net_w > 100 and bat_soc < cfg.bat_soc_max:
                    charge_w = min(net_w * 0.9, cfg.bat_max_charge_w)
                    bat_soc = min(
                        cfg.bat_soc_max,
                        bat_soc + charge_w * cfg.bat_efficiency * step_h / (cfg.bat_capacity_kwh * 10),
                    )
                    bat_action = "charge"
                    bat_w = charge_w
                    grid_w = -(net_w - charge_w)
                elif net_w < -100 and bat_can_discharge:
                    discharge_w = min(abs(net_w) * 0.9, cfg.bat_max_discharge_w)
                    bat_soc = max(
                        cfg.bat_soc_min,
                        bat_soc - (discharge_w / cfg.bat_efficiency) * step_h / (cfg.bat_capacity_kwh * 10),
                    )
                    bat_action = "discharge"
                    bat_w = -discharge_w
                    grid_w = -(net_w + discharge_w)

            # Device tick (energy accounting + physical state)
            for dev in devices:
                dev.tick(STEP_MINUTES, pv_w, total_load_w)

            # Energy accumulators
            e_pv   += pv_w * step_h / 1000
            e_load += total_load_w * step_h / 1000
            direct_self_w = min(pv_w, total_load_w)
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
                active_devices=[sd.name for sd in devices if sd.active],
            ))

    finally:
        # Always restore the patched symbols
        _time_stdlib.time = _orig_time
        _dm_mod.datetime = _RealDatetime   # type: ignore[attr-defined]
        _md_mod.datetime = _RealDatetime   # type: ignore[attr-defined]

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


# ---------------------------------------------------------------------------
# Sync wrapper (called from executor threads — daily_optimizer, optimizer)
# ---------------------------------------------------------------------------

def run(
    cfg: SimConfig,
    devices: list[SimDevice] | None = None,
    managed_devices: list[_ManagedDevice | None] | None = None,
    sim_date: _date | None = None,
) -> SimResult:
    """Synchronous wrapper around async_run().

    Works in both executor threads (daily_optimizer._run_optimization, no running
    loop) and in async contexts (e.g. test stubs that call the function directly):
    detects a running event loop and offloads to a fresh thread in that case.
    """
    import concurrent.futures

    def _in_thread() -> SimResult:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(async_run(cfg, devices, managed_devices, sim_date))
        finally:
            loop.close()

    try:
        asyncio.get_running_loop()
        # Called from inside an event loop (test stub, etc.) — use a worker thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_in_thread).result()
    except RuntimeError:
        # No running event loop — typical executor-thread path
        return _in_thread()
