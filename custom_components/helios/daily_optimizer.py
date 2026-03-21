"""Daily morning optimizer — runs at 05:00 to find optimal scoring weights for the day.

Uses the simulation engine to grid-search scoring weights based on:
- Current season (derived from date)
- Cloud cover inferred from the forecast entity (or seasonal average as fallback)
- HA device configuration mapped to SimDevice objects
- Real battery parameters from config

Results are applied to the ScoringEngine and dispatch threshold for the day.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICES,
    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY,
    CONF_FORECAST_ENTITY,
    CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W,
    CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA,
    TEMPO_COLORS,
    CONF_DEVICE_NAME, CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_DEVICE_MUST_RUN_DAILY,
    CONF_DEVICE_WEIGHT_PRIORITY, CONF_DEVICE_WEIGHT_FIT, CONF_DEVICE_WEIGHT_URGENCY,
    DEFAULT_DEVICE_PRIORITY, DEFAULT_DEVICE_MIN_ON_MINUTES,
    DEFAULT_ALLOWED_START, DEFAULT_ALLOWED_END,
    DEFAULT_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_URGENCY,
)

if TYPE_CHECKING:
    from .coordinator import EnergyOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)

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

def ha_devices_to_sim(devices_config: list[dict]) -> list:
    """Convert HA config-entry device dicts to SimDevice objects for the simulation."""
    # Import here to avoid circular dependency at module load time
    from .simulation.devices import SimDevice

    def _t(v: str, default: str) -> float:
        """Parse 'HH:MM' time string to decimal hours, or return default."""
        try:
            h, m = v.split(":")
            return int(h) + int(m) / 60.0
        except Exception:
            pass
        try:
            return float(v)
        except Exception:
            pass
        h, m = default.split(":")
        return int(h) + int(m) / 60.0

    result = []
    for d in devices_config:
        power_w = float(d.get(CONF_DEVICE_POWER_W, 0))
        if power_w <= 0:
            continue
        result.append(SimDevice(
            name=d.get(CONF_DEVICE_NAME, "unknown"),
            power_w=power_w,
            allowed_start=_t(d.get(CONF_DEVICE_ALLOWED_START, DEFAULT_ALLOWED_START), DEFAULT_ALLOWED_START),
            allowed_end=_t(d.get(CONF_DEVICE_ALLOWED_END, DEFAULT_ALLOWED_END), DEFAULT_ALLOWED_END),
            priority=int(d.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY)),
            min_on_minutes=float(d.get(CONF_DEVICE_MIN_ON_MINUTES, DEFAULT_DEVICE_MIN_ON_MINUTES)),
            must_run_daily=bool(d.get(CONF_DEVICE_MUST_RUN_DAILY, False)),
            w_priority=float(d.get(CONF_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_PRIORITY)),
            w_fit=float(d.get(CONF_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_FIT)),
            w_urgency=float(d.get(CONF_DEVICE_WEIGHT_URGENCY, DEFAULT_DEVICE_WEIGHT_URGENCY)),
        ))
    return result


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

    cfg = coordinator.entry.data
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
    # At 05:00 we are still in HC (22h–6h). The HP period starts at 6h, so the color
    # that matters for today's optimization is the "next color" entity when available
    # (some Tempo integrations expose separate "couleur aujourd'hui" / "couleur demain").
    # Fallback: use the regular tempo color entity, then default to "blue".
    tempo_color = "blue"
    for entity_key in (CONF_TEMPO_NEXT_COLOR_ENTITY, CONF_TEMPO_COLOR_ENTITY):
        entity_id = cfg.get(entity_key)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state and state.state in TEMPO_COLORS:
            tempo_color = state.state
            _LOGGER.debug("Helios optimizer: tempo=%s (from %s)", tempo_color, entity_id)
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

    # ---- Run the optimizer in the executor (CPU-bound grid search) ----
    def _run_optimization():
        try:
            from .simulation.engine import SimConfig
            from .simulation.optimizer import optimize
            from .simulation.profiles import load_base_load_from_json
        except ImportError as exc:
            _LOGGER.error("Helios optimizer: simulation module not available: %s", exc)
            return None

        import pathlib
        _base_load_path = pathlib.Path(__file__).parent / "simulation" / "config" / "base_load.json"
        try:
            base_load_fn = load_base_load_from_json(str(_base_load_path))
            _LOGGER.debug("Helios optimizer: using base_load.json from %s", _base_load_path)
        except Exception as exc:
            base_load_fn = None
            _LOGGER.warning("Helios optimizer: could not load base_load.json (%s), using default profile", exc)

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
            forecast_noise=0.0,   # deterministic at 5am (forecast already known)
            base_load_fn=base_load_fn,
        )

        def _devices_fn():
            return ha_devices_to_sim(devices_config)

        objective_alpha = float(cfg.get(CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA))
        return optimize(
            sim_cfg,
            _devices_fn,
            objective_alpha=objective_alpha,
            n_runs=1,
            progress=False,
        )

    results = await hass.async_add_executor_job(_run_optimization)

    if not results:
        _LOGGER.warning("Helios optimizer: no results — keeping previous weights")
        return

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
    _LOGGER.info("Helios optimizer: weights and threshold applied for today")
