"""Daily morning forecast — runs at 05:00 to simulate the day ahead.

Produces a :class:`ForecastResult` stored on ``coordinator.forecast_data``.
No weight optimisation is performed; the fixed scoring engine is used as-is.
"""
from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
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
    TEMPO_COLORS, normalize_tempo_color,
    CONF_DEVICE_NAME, CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_TYPE,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
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

from .simulation.engine import SimConfig, async_run as _sim_async_run

if TYPE_CHECKING:
    from .coordinator import EnergyOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)

_BASE_LOAD_PATH       = pathlib.Path(__file__).parent / "simulation" / "config" / "base_load.json"
_APPLIANCE_SCHED_PATH = pathlib.Path(__file__).parent / "simulation" / "config" / "appliance_schedule.json"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ForecastResult:
    """Daily forecast produced by the morning simulation."""
    forecast_pv_kwh: float
    forecast_consumption_kwh: float
    forecast_import_kwh: float
    forecast_export_kwh: float
    forecast_self_consumption_pct: float
    forecast_self_sufficiency_pct: float
    forecast_cost: float
    forecast_savings: float
    last_forecast: str          # ISO timestamp


# ---------------------------------------------------------------------------
# Seasonal helpers
# ---------------------------------------------------------------------------

# Theoretical clear-sky daily production (kWh) per season for a 1 kWp system at ~47°N
_SEASONAL_CLEAR_KWH_PER_KWP: dict[str, float] = {
    "winter": 2.0,
    "spring": 4.5,
    "summer": 6.0,
    "autumn": 3.0,
}


def season_from_date(d: date | None = None) -> str:
    """Return 'winter' | 'spring' | 'summer' | 'autumn' from a calendar date."""
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
    appliance_schedule: dict[str, float] | None = None,
) -> tuple[list, list]:
    """Convert HA config-entry device dicts to (SimDevice, ManagedDevice) pairs.

    Returns a tuple (sim_devices, managed_devices). Both lists are parallel —
    sim_devices[i] and managed_devices[i] refer to the same physical device.

    Args:
        devices_config: Device dicts from the config entry.
        global_cfg: Global config dict (used for off-peak slot parsing in ManagedDevice).
        hass: Optional HomeAssistant instance; when provided, reads current physical
            state (WH temp, EV SOC) from HA to seed the simulation accurately.
        appliance_schedule: Pre-loaded schedule dict {device_name: ready_at_hour}.
            Must be loaded outside the event loop and passed in to avoid blocking I/O.
    """
    from .simulation.devices import SimDevice, apply_appliance_schedule
    from .managed_device import ManagedDevice

    _appliance_schedule: dict[str, float] = appliance_schedule or {}

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

        elif dev_type == "appliance":
            from .const import CONF_APPLIANCE_CYCLE_DURATION_MINUTES, CONF_APPLIANCE_DEADLINE_SLOTS, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES, DEFAULT_APPLIANCE_DEADLINE_SLOTS
            cycle_min = d.get(CONF_APPLIANCE_CYCLE_DURATION_MINUTES, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES)
            sd_kwargs.update(
                appliance_cycle_duration_minutes=int(cycle_min),
                device_type="appliance",
            )

        sim_dev = SimDevice(**sd_kwargs)

        # ---- Build ManagedDevice (real dispatch logic) ----
        managed_dev = ManagedDevice(d, gcfg)
        # Seed pool state from simulation initial values.
        if dev_type == DEVICE_TYPE_POOL and sim_dev.pool_required_min is not None:
            managed_dev.pool_required_minutes_today = sim_dev.pool_required_min
            managed_dev.pool_last_date = date.today()

        sim_devices.append(sim_dev)
        managed_devices.append(managed_dev)

    if _appliance_schedule:
        apply_appliance_schedule(sim_devices, _appliance_schedule)

    return sim_devices, managed_devices


# ---------------------------------------------------------------------------
# Main forecast routine
# ---------------------------------------------------------------------------

async def async_run_daily_forecast(
    hass: HomeAssistant,
    coordinator: "EnergyOptimizerCoordinator",
) -> None:
    """Simulate the day ahead and store a :class:`ForecastResult` on the coordinator.

    Called automatically at 05:00 every day by coordinator.py.
    No grid search — runs a single simulation with the current configuration.
    """
    _LOGGER.info("Helios daily forecast: starting morning simulation")

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
        theoretical_kwh = _SEASONAL_CLEAR_KWH_PER_KWP[season] * peak_pv_w / 1000.0
        cloud = cloud_from_forecast(forecast_kwh, theoretical_kwh)
        _LOGGER.debug(
            "Helios forecast: forecast=%.1f kWh, theoretical=%.1f kWh → cloud=%s",
            forecast_kwh, theoretical_kwh, cloud,
        )
    else:
        cloud = "clear"
        _LOGGER.debug("Helios forecast: no forecast entity, using clear-sky profile for season=%s", season)

    # ---- Tempo color for the coming day ----
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
            _LOGGER.debug("Helios forecast: tempo=%s (raw=%s, from %s)", tempo_color, state.state, entity_id)
            break
    else:
        _LOGGER.debug("Helios forecast: no valid tempo entity found, defaulting to 'blue'")

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

    # ---- Load appliance schedule (blocking I/O — run in executor) ----
    from .simulation.devices import load_appliance_schedule
    _appliance_schedule: dict[str, float] = {}
    try:
        _appliance_schedule = await hass.async_add_executor_job(
            load_appliance_schedule, _APPLIANCE_SCHED_PATH
        )
        _LOGGER.debug("Helios forecast: appliance schedule loaded — %s", _appliance_schedule)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Helios forecast: no appliance schedule (%s)", exc)

    # ---- Pre-read physical state from HA (async context) ----
    sim_devices, managed_devices = ha_devices_to_sim(
        devices_config, global_cfg=cfg, hass=hass, appliance_schedule=_appliance_schedule
    )
    _LOGGER.debug("Helios forecast: %d devices mapped for simulation", len(sim_devices))

    # ---- Base load profile (EMA or static file) ----
    ema_enabled = cfg.get(CONF_EMA_ENABLED, DEFAULT_EMA_ENABLED)
    learner = coordinator.consumption_learner
    base_load_fn = None
    if ema_enabled and learner.profile is not None:
        base_load_fn = learner.as_base_load_fn()
        _LOGGER.debug(
            "Helios forecast: using EMA base load profile (samples=%d)",
            learner.sample_count,
        )
    else:
        from .simulation.profiles import load_base_load_from_json
        try:
            base_load_fn = await hass.async_add_executor_job(
                load_base_load_from_json, str(_BASE_LOAD_PATH)
            )
            if not ema_enabled:
                _LOGGER.debug("Helios forecast: EMA disabled — using static base_load.json")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Helios forecast: could not load base_load.json (%s), using default profile", exc)

    # ---- Build SimConfig ----
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

    # ---- Run simulation ----
    try:
        sim_result = await _sim_async_run(sim_cfg, sim_devices, managed_devices=managed_devices)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Helios daily forecast: simulation failed: %s", exc)
        return

    # ---- Build ForecastResult ----
    now_iso = datetime.now(timezone.utc).isoformat()
    forecast = ForecastResult(
        forecast_pv_kwh=round(sim_result.e_pv_kwh, 2),
        forecast_consumption_kwh=round(sim_result.e_load_kwh, 2),
        forecast_import_kwh=round(sim_result.e_grid_import_kwh, 2),
        forecast_export_kwh=round(sim_result.e_grid_export_kwh, 2),
        forecast_self_consumption_pct=round(sim_result.autoconsumption_rate * 100, 1),
        forecast_self_sufficiency_pct=round(sim_result.self_sufficiency_rate * 100, 1),
        forecast_cost=round(sim_result.cost_eur, 2),
        forecast_savings=round(sim_result.savings_eur, 2),
        last_forecast=now_iso,
    )

    # ---- Store results on coordinator ----
    coordinator.forecast_data = forecast
    coordinator.optimizer_last_run = now_iso
    coordinator.optimizer_context = {
        "season": season,
        "cloud": cloud,
        "tempo": tempo_color,
        "bat_soc_start": bat_soc_start,
        "forecast_kwh": forecast_kwh,
        "peak_pv_w": peak_pv_w,
        "ema_sample_count": coordinator.consumption_learner.sample_count,
    }

    _LOGGER.info(
        "Helios daily forecast: PV=%.1f kWh, autoconsumption=%.1f%%, import=%.1f kWh, cost=%.2f €",
        forecast.forecast_pv_kwh,
        forecast.forecast_self_consumption_pct,
        forecast.forecast_import_kwh,
        forecast.forecast_cost,
    )
