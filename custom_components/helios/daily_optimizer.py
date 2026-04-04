"""Daily morning optimizer — runs at 05:00 to find optimal scoring weights for the day.

Uses the simulation engine to grid-search scoring weights based on:
- Current season (derived from date)
- Cloud cover inferred from the forecast entity (or seasonal average as fallback)
- HA device configuration mapped to SimDevice objects
- Real battery parameters from config

Results are applied to the ScoringEngine and dispatch threshold for the day.
"""
from __future__ import annotations

import copy
import logging
import math
import pathlib
from dataclasses import replace as _dc_replace
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICES,
    CONF_EMA_ENABLED, DEFAULT_EMA_ENABLED,
    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY,
    CONF_FORECAST_ENTITY,
    CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W,
    CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA,
    CONF_BASE_LOAD_NOISE, DEFAULT_BASE_LOAD_NOISE,
    CONF_OPTIMIZER_N_RUNS, DEFAULT_OPTIMIZER_N_RUNS,
    CONF_RISK_LAMBDA, DEFAULT_RISK_LAMBDA,
    TEMPO_COLORS, normalize_tempo_color,
    CONF_DEVICE_NAME, CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_TYPE,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_DEVICE_MUST_RUN_DAILY,
    CONF_DEVICE_WEIGHT_PRIORITY, CONF_DEVICE_WEIGHT_FIT, CONF_DEVICE_WEIGHT_URGENCY,
    DEFAULT_DEVICE_PRIORITY, DEFAULT_DEVICE_MIN_ON_MINUTES,
    DEFAULT_ALLOWED_START, DEFAULT_ALLOWED_END,
    DEFAULT_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_URGENCY,
    DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_POOL,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
    CONF_WH_TEMP_MIN_ENTITY, CONF_WH_OFF_PEAK_HYSTERESIS_K,
    DEFAULT_WH_TEMP_TARGET, DEFAULT_WH_TEMP_MIN, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K,
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY, DEFAULT_EV_SOC_TARGET,
    CONF_POOL_FILTRATION_ENTITY,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
)

if TYPE_CHECKING:
    from .coordinator import EnergyOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)

_BASE_LOAD_PATH = pathlib.Path(__file__).parent / "simulation" / "config" / "base_load.json"

# ---------------------------------------------------------------------------
# Seasonal helpers
# ---------------------------------------------------------------------------

# Theoretical clear-sky daily production (kWh) per season for a 1 kWp system at ~47°N
# Used as fallback when no forecast entity is available.
_SEASONAL_CLEAR_KWH_PER_KWP: dict[str, float] = {
    "winter": 2.0,
    "spring": 4.5,
    "summer": 6.0,
    "autumn": 3.0,
}

# Hours at which "remaining forecast" equals roughly total daily production (i.e. pre-sunrise)
# Used to compute the cloud ratio when forecast is available.
_SEASON_SUNRISE: dict[str, float] = {
    "winter": 8.0,
    "spring": 6.5,
    "summer": 5.5,
    "autumn": 7.5,
}


def season_from_date(d: date | None = None) -> str:
    """Return 'winter' | 'spring' | 'summer' | 'autumn' from a calendar date.

    Uses meteorological seasons (December→winter, March→spring, …).
    """
    if d is None:
        d = date.today()
    month = d.month
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def cloud_from_forecast(forecast_kwh: float, theoretical_kwh: float) -> str:
    """Infer cloud cover category from forecast / theoretical production ratio.

    ratio >= 0.75  → "clear"
    0.45 ≤ ratio < 0.75 → "partly_cloudy"
    ratio < 0.45        → "cloudy"
    """
    if theoretical_kwh <= 0:
        return "clear"
    ratio = forecast_kwh / theoretical_kwh
    if ratio >= 0.75:
        return "clear"
    if ratio >= 0.45:
        return "partly_cloudy"
    return "cloudy"


# ---------------------------------------------------------------------------
# HA device config → SimDevice
# ---------------------------------------------------------------------------

def ha_devices_to_sim(
    devices_config: list[dict],
    global_cfg: dict | None = None,
    hass=None,
) -> tuple[list, list]:
    """Convert HA config-entry device dicts to (SimDevice, ManagedDevice) pairs.

    Returns a tuple (sim_devices, managed_devices). Both lists are parallel —
    sim_devices[i] and managed_devices[i] refer to the same physical device.

    Args:
        devices_config: Device dicts from the config entry.
        global_cfg: Global config dict (used for off-peak slot parsing in ManagedDevice).
        hass: Optional HomeAssistant instance; when provided, reads current physical
            state (WH temp, EV SOC) from HA to seed the simulation accurately.
    """
    from .simulation.devices import SimDevice
    from .managed_device import ManagedDevice

    def _t(v: str, default: str) -> float:
        """Parse 'HH:MM' time string to decimal hours, or return default."""
        try:
            h, m = v.split(":")
            return int(h) + int(m) / 60.0
        except (ValueError, AttributeError):
            pass
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
        h, m = default.split(":")
        return int(h) + int(m) / 60.0

    def _read_float(entity_id: str | None, default: float) -> float:
        if not entity_id or hass is None:
            return default
        state = hass.states.get(entity_id)
        if state and state.state not in ("unavailable", "unknown"):
            try:
                return float(state.state)
            except ValueError:
                pass
        return default

    sim_devices = []
    managed_devices = []
    gcfg = global_cfg or {}

    for d in devices_config:
        power_w = float(d.get(CONF_DEVICE_POWER_W, 0))
        if power_w <= 0:
            continue

        dev_type = d.get(CONF_DEVICE_TYPE, "generic")

        # ---- Build SimDevice with physical state ----
        sd_kwargs: dict = dict(
            name=d.get(CONF_DEVICE_NAME, "unknown"),
            power_w=power_w,
            device_type=dev_type,
            allowed_start=_t(d.get(CONF_DEVICE_ALLOWED_START, DEFAULT_ALLOWED_START), DEFAULT_ALLOWED_START),
            allowed_end=_t(d.get(CONF_DEVICE_ALLOWED_END, DEFAULT_ALLOWED_END), DEFAULT_ALLOWED_END),
            priority=int(d.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY)),
            min_on_minutes=float(d.get(CONF_DEVICE_MIN_ON_MINUTES, DEFAULT_DEVICE_MIN_ON_MINUTES)),
            must_run_daily=bool(d.get(CONF_DEVICE_MUST_RUN_DAILY, False)),
            w_priority=float(d.get(CONF_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_PRIORITY)),
            w_fit=float(d.get(CONF_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_FIT)),
            w_urgency=float(d.get(CONF_DEVICE_WEIGHT_URGENCY, DEFAULT_DEVICE_WEIGHT_URGENCY)),
        )

        if dev_type == DEVICE_TYPE_WATER_HEATER:
            wh_temp_entity = d.get(CONF_WH_TEMP_ENTITY)
            wh_temp_default = float(d.get(CONF_WH_TEMP_TARGET, DEFAULT_WH_TEMP_TARGET)) - 5.0
            sd_kwargs.update(
                wh_temp=_read_float(wh_temp_entity, wh_temp_default),
                wh_temp_target=float(d.get(CONF_WH_TEMP_TARGET, DEFAULT_WH_TEMP_TARGET)),
                wh_temp_min=float(d.get(CONF_WH_TEMP_MIN, DEFAULT_WH_TEMP_MIN)),
                wh_off_peak_hysteresis_k=float(d.get(CONF_WH_OFF_PEAK_HYSTERESIS_K, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K)),
                wh_temp_entity=wh_temp_entity,
                wh_temp_min_entity=d.get(CONF_WH_TEMP_MIN_ENTITY),
            )

        elif dev_type == DEVICE_TYPE_EV:
            ev_soc_entity = d.get(CONF_EV_SOC_ENTITY)
            sd_kwargs.update(
                ev_soc=_read_float(ev_soc_entity, 50.0),
                ev_soc_target=float(d.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET)),
                ev_plugged=True,  # assume plugged in simulation
                ev_soc_entity=ev_soc_entity,
                ev_plugged_entity=d.get(CONF_EV_PLUGGED_ENTITY),
            )

        elif dev_type == DEVICE_TYPE_POOL:
            pool_ent = d.get(CONF_POOL_FILTRATION_ENTITY)
            pool_h = _read_float(pool_ent, 0.0)
            sd_kwargs.update(
                run_quota_h=pool_h if pool_h > 0 else None,
                pool_required_min=pool_h * 60.0 if pool_h > 0 else None,
                pool_filtration_entity=pool_ent,
            )

        sim_dev = SimDevice(**sd_kwargs)

        # ---- Build ManagedDevice (real dispatch logic) ----
        managed_dev = ManagedDevice(d, gcfg)
        # Seed pool state from simulation initial values.
        # Also set pool_last_date = today so update_pool_run_time doesn't treat
        # the first simulation step as a "new day" and reset pool_required_minutes_today.
        if dev_type == DEVICE_TYPE_POOL and sim_dev.pool_required_min is not None:
            managed_dev.pool_required_minutes_today = sim_dev.pool_required_min
            managed_dev.pool_last_date = date.today()

        sim_devices.append(sim_dev)
        managed_devices.append(managed_dev)

    return sim_devices, managed_devices


# ---------------------------------------------------------------------------
# Main optimization routine
# ---------------------------------------------------------------------------

async def async_run_daily_optimization(
    hass: HomeAssistant,
    coordinator: "EnergyOptimizerCoordinator",
) -> None:
    """Grid-search optimal scoring weights and apply them to the coordinator.

    Runs in an executor to avoid blocking the event loop during the grid search.
    Called automatically at 05:00 every day by coordinator.py.
    """
    _LOGGER.info("Helios daily optimizer: starting morning optimization")

    cfg = {**coordinator.entry.data, **coordinator.entry.options}
    peak_pv_w = float(cfg.get(CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W))

    # ---- Season from today's date ----
    season = season_from_date(date.today())

    # ---- Cloud cover: infer from forecast entity or use seasonal average ----
    forecast_kwh: float | None = None
    if cfg.get(CONF_FORECAST_ENTITY):
        state = hass.states.get(cfg[CONF_FORECAST_ENTITY])
        if state and state.state not in ("unavailable", "unknown"):
            try:
                forecast_kwh = float(state.state)
            except ValueError:
                pass

    if forecast_kwh is not None:
        # Compute theoretical daily production for this season + configured peak PV
        # (clear-sky kWh/kWp × peak_pv_w / 1000)
        theoretical_kwh = _SEASONAL_CLEAR_KWH_PER_KWP[season] * peak_pv_w / 1000.0
        cloud = cloud_from_forecast(forecast_kwh, theoretical_kwh)
        _LOGGER.debug(
            "Helios optimizer: forecast=%.1f kWh, theoretical=%.1f kWh → cloud=%s",
            forecast_kwh, theoretical_kwh, cloud,
        )
    else:
        # No forecast available → use deterministic "clear" profile with seasonal average
        cloud = "clear"
        _LOGGER.debug("Helios optimizer: no forecast entity, using clear-sky profile for season=%s", season)

    # ---- Tempo color for the coming day ----
    # Before 06:00 (HC period, HP not yet started): the "next color" entity reflects
    # today's upcoming HP period — use it in priority.
    # From 06:00 onwards: HP is active, so "couleur actuelle" is the authoritative source.
    now_hour = datetime.now().hour
    if now_hour < 6:
        entity_priority = (CONF_TEMPO_NEXT_COLOR_ENTITY, CONF_TEMPO_COLOR_ENTITY)
    else:
        entity_priority = (CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY)

    tempo_color = "blue"
    for entity_key in entity_priority:
        entity_id = cfg.get(entity_key)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        normalized = normalize_tempo_color(state.state if state else None)
        if normalized:
            tempo_color = normalized
            _LOGGER.debug("Helios optimizer: tempo=%s (raw=%s, from %s)", tempo_color, state.state, entity_id)
            break
    else:
        _LOGGER.debug("Helios optimizer: no valid tempo entity found, defaulting to 'blue'")

    # ---- Battery parameters ----
    battery_enabled = cfg.get(CONF_BATTERY_ENABLED, False)
    bat_soc_start = 50.0
    if battery_enabled and cfg.get(CONF_BATTERY_SOC_ENTITY):
        state = hass.states.get(cfg[CONF_BATTERY_SOC_ENTITY])
        if state and state.state not in ("unavailable", "unknown"):
            try:
                bat_soc_start = float(state.state)
            except ValueError:
                pass

    bat_capacity_kwh    = float(cfg.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH))
    bat_max_charge_w    = float(cfg.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 2000.0))
    bat_max_discharge_w = float(cfg.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W, 2000.0))
    bat_soc_min         = float(cfg.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
    bat_soc_max         = float(cfg.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX))

    # ---- Device list from HA config ----
    devices_config = cfg.get(CONF_DEVICES, [])

    # ---- Pre-read physical state from HA (async context, before executor) ----
    # This seeds the simulation with real WH temperature and EV SOC at 05:00.
    initial_sim_devices, initial_managed_devices = ha_devices_to_sim(
        devices_config, global_cfg=cfg, hass=hass
    )
    _LOGGER.debug(
        "Helios optimizer: %d devices mapped for simulation",
        len(initial_sim_devices),
    )

    # ---- Run optimization + capture schedule in executor ----
    def _run_optimization():
        try:
            from .simulation.engine import SimConfig, run as sim_run
            from .simulation.optimizer import optimize
            from .simulation.profiles import load_base_load_from_json
        except ImportError as exc:
            _LOGGER.error("Helios optimizer: simulation module not available: %s", exc)
            return None

        # Use the EMA-learned profile when available and enabled;
        # fall back to static base_load.json otherwise.
        ema_enabled = cfg.get(CONF_EMA_ENABLED, DEFAULT_EMA_ENABLED)
        learner = coordinator.consumption_learner
        if ema_enabled and learner.profile is not None:
            base_load_fn = learner.as_base_load_fn()
            _LOGGER.debug(
                "Helios optimizer: using EMA base load profile (samples=%d)",
                learner.sample_count,
            )
        else:
            try:
                base_load_fn = load_base_load_from_json(str(_BASE_LOAD_PATH))
                if not ema_enabled:
                    _LOGGER.debug("Helios optimizer: EMA disabled — using static base_load.json")
            except Exception as exc:
                base_load_fn = None
                _LOGGER.warning(
                    "Helios optimizer: could not load base_load.json (%s), using default profile", exc
                )

        sim_cfg = SimConfig(
            season=season,
            cloud=cloud,
            peak_pv_w=peak_pv_w,
            tempo=tempo_color,
            bat_enabled=battery_enabled,
            bat_soc_start=bat_soc_start,
            bat_capacity_kwh=bat_capacity_kwh,
            bat_max_charge_w=bat_max_charge_w,
            bat_max_discharge_w=bat_max_discharge_w,
            bat_soc_min=bat_soc_min,
            bat_soc_max=bat_soc_max,
            forecast_noise=0.0,
            base_load_fn=base_load_fn,
        )

        def _devices_fn():
            # Return fresh (sim_devices, managed_devices) pairs each run.
            # SimDevice has mutable runtime state, so we need a fresh copy per run.
            return (
                copy.deepcopy(initial_sim_devices),
                copy.deepcopy(initial_managed_devices),
            )

        objective_alpha  = float(cfg.get(CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA))
        base_load_noise  = float(cfg.get(CONF_BASE_LOAD_NOISE, DEFAULT_BASE_LOAD_NOISE))
        optimizer_n_runs = int(cfg.get(CONF_OPTIMIZER_N_RUNS, DEFAULT_OPTIMIZER_N_RUNS))
        risk_lambda      = float(cfg.get(CONF_RISK_LAMBDA, DEFAULT_RISK_LAMBDA))
        results = optimize(
            sim_cfg,
            _devices_fn,
            objective_alpha=objective_alpha,
            n_runs=optimizer_n_runs,
            risk_lambda=risk_lambda,
            base_load_noise=base_load_noise,
            progress=False,
        )
        if not results:
            return None

        # Re-run simulation with chosen config to capture the hourly schedule
        best = results[0]
        best_cfg = _dc_replace(
            sim_cfg,
            scoring={
                "weight_pv_surplus":  best.w_surplus,
                "weight_tempo":       best.w_tempo,
                "weight_battery_soc": best.w_soc,
                "weight_forecast":    best.w_forecast,
            },
            dispatch_threshold=best.threshold,
        )
        best_sim_devices, best_managed_devices = _devices_fn()
        sim_result = sim_run(best_cfg, best_sim_devices, managed_devices=best_managed_devices)

        # Aggregate 5-min steps into 24 hourly entries
        steps_per_hour = 12
        hourly: list[dict] = []
        for h in range(24):
            s_slice = sim_result.steps[h * steps_per_hour:(h + 1) * steps_per_hour]
            if not s_slice:
                continue
            active: set[str] = set()
            bat_counts: dict[str, int] = {}
            for s in s_slice:
                active.update(s.active_devices)
                bat_counts[s.bat_action] = bat_counts.get(s.bat_action, 0) + 1
            dominant_bat = max(bat_counts, key=bat_counts.get)
            n = len(s_slice)
            hourly.append({
                "hour": f"{h:02d}:00",
                "pv_w":      round(sum(s.pv_w for s in s_slice) / n),
                "base_w":    round(sum(s.base_w for s in s_slice) / n),
                "devices_w": round(sum(s.devices_w for s in s_slice) / n),
                "surplus_w": round(sum(s.surplus_w for s in s_slice) / n),
                "grid_w":    round(sum(s.grid_w for s in s_slice) / n),
                "bat_soc":   round(sum(s.bat_soc for s in s_slice) / n, 1),
                "bat_action": dominant_bat,
                "score":     round(sum(s.score for s in s_slice) / n, 3),
                "active_devices": sorted(active),
            })

        return results, hourly

    payload = await hass.async_add_executor_job(_run_optimization)

    if not payload:
        _LOGGER.warning("Helios optimizer: no results — keeping previous weights")
        return

    results, hourly_schedule = payload
    best = results[0]
    _LOGGER.info(
        "Helios optimizer: best config — surplus=%.0f%% tempo=%.0f%% soc=%.0f%% "
        "forecast=%.0f%% threshold=%.0f%% (objective=%.3f)",
        best.w_surplus * 100, best.w_tempo * 100, best.w_soc * 100,
        best.w_forecast * 100, best.threshold * 100, best.objective,
    )

    # ---- Apply results to coordinator ----
    new_scoring = {
        "weight_pv_surplus":  best.w_surplus,
        "weight_tempo":       best.w_tempo,
        "weight_battery_soc": best.w_soc,
        "weight_forecast":    best.w_forecast,
    }
    coordinator.scoring_engine.update_weights(new_scoring)
    coordinator.dispatch_threshold = best.threshold
    coordinator.optimizer_last_run = datetime.now(timezone.utc).isoformat()

    # ---- Store diagnostics data ----
    coordinator.optimizer_context = {
        "season": season,
        "cloud": cloud,
        "tempo": tempo_color,
        "bat_soc_start": bat_soc_start,
        "forecast_kwh": forecast_kwh,
        "peak_pv_w": peak_pv_w,
        "objective_alpha": float(cfg.get(CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA)),
        "ema_sample_count": coordinator.consumption_learner.sample_count,
    }
    coordinator.optimizer_chosen = {
        "rank": 1,
        "w_surplus":      round(best.w_surplus, 3),
        "w_tempo":        round(best.w_tempo, 3),
        "w_soc":          round(best.w_soc, 3),
        "w_forecast":     round(best.w_forecast, 3),
        "threshold":      round(best.threshold, 3),
        "autoconsumption": round(best.autoconsumption, 4),
        "savings_rate":   round(best.savings_rate, 4),
        "cost_eur":       round(best.cost_eur, 4),
        "objective":      round(best.objective, 4),
        "obj_mean":       round(best.obj_mean, 4),
        "obj_std":        round(best.obj_std, 4),
    }
    coordinator.optimizer_top20 = [
        {
            "rank":          i + 1,
            "w_surplus":     round(r.w_surplus, 3),
            "w_tempo":       round(r.w_tempo, 3),
            "w_soc":         round(r.w_soc, 3),
            "w_forecast":    round(r.w_forecast, 3),
            "threshold":     round(r.threshold, 3),
            "autoconsumption": round(r.autoconsumption, 4),
            "savings_rate":  round(r.savings_rate, 4),
            "cost_eur":      round(r.cost_eur, 4),
            "objective":     round(r.objective, 4),
            "obj_mean":      round(r.obj_mean, 4),
            "obj_std":       round(r.obj_std, 4),
        }
        for i, r in enumerate(results[:20])
    ]
    coordinator.optimizer_chosen_schedule = hourly_schedule
    _LOGGER.info("Helios optimizer: weights, threshold, and diagnostics applied for today")

    await coordinator.async_save_optimizer_state()
