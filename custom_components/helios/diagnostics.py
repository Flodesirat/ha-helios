"""Diagnostics support for Helios Energy Optimizer."""
from __future__ import annotations

import time as time_mod
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
    # Sources
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY, CONF_FORECAST_ENTITY,
    CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
    # Battery
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_CHARGE_SCRIPT, CONF_BATTERY_AUTOCONSUM_SCRIPT,
    CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN,
    CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    # Strategy
    CONF_MODE, DEFAULT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD,
    CONF_GRID_ALLOWANCE_W, DEFAULT_GRID_ALLOWANCE_W,
    CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA,
    CONF_EMA_ALPHA, DEFAULT_EMA_ALPHA,
    CONF_BASE_LOAD_NOISE, DEFAULT_BASE_LOAD_NOISE,
    CONF_OPTIMIZER_N_RUNS, DEFAULT_OPTIMIZER_N_RUNS,
    CONF_RISK_LAMBDA, DEFAULT_RISK_LAMBDA,
    # Scoring weights
    CONF_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_PV_SURPLUS,
    CONF_WEIGHT_TEMPO, DEFAULT_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_BATTERY_SOC,
    CONF_WEIGHT_FORECAST, DEFAULT_WEIGHT_FORECAST,
    MODE_AUTO,
)
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice


def _ts_iso(epoch: float | None) -> str | None:
    """Convert epoch seconds to ISO string, or None."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch).isoformat()


def _device_diag(device: ManagedDevice, hass: HomeAssistant, now_time, surplus_w: float, bat_available_w: float) -> dict:
    """Build a rich diagnostic snapshot for one device."""
    reader = ManagedDevice._make_ha_reader(hass)
    measured_w = device.actual_power_w(reader)
    base = {
        "name":                device.name,
        "type":                device.device_type,
        "is_on":               device.is_on,
        "manual_mode":         device.manual_mode,
        "priority":            device.priority,
        "power_w":             device.power_w,
        "actual_power_w":      measured_w if device.is_on else 0.0,
        "is_satisfied":        device.is_satisfied(reader),
        "is_in_allowed_window": device.is_in_allowed_window(now_time),
        "fit_score":           round(ManagedDevice.compute_fit_score(measured_w if device.is_on else device.power_w, surplus_w, bat_available_w), 3),
        "effective_score":     round(device.effective_score(reader, surplus_w, bat_available_w), 3),
        "turned_on_at":        _ts_iso(device.turned_on_at),
        "turned_off_at":       _ts_iso(device.turned_off_at),
        "interruptible":       device.interruptible,
        "allowed_start":       device.allowed_start,
        "allowed_end":         device.allowed_end,
    }

    if device.device_type == DEVICE_TYPE_EV:
        base["ev"] = {
            "soc":       ManagedDevice._state_float(reader, device.ev_soc_entity) if device.ev_soc_entity else None,
            "soc_target": device.ev_soc_target,
            "plugged":   ManagedDevice._state_bool(reader, device.ev_plugged_entity, fallback=True) if device.ev_plugged_entity else device.ev_plugged_manual,
        }

    if device.device_type == DEVICE_TYPE_WATER_HEATER:
        base["water_heater"] = {
            "temp":              ManagedDevice._state_float(reader, device.wh_temp_entity) if device.wh_temp_entity else None,
            "temp_target":       device.wh_temp_target,
            "temp_min":          device.wh_temp_min,
            "temp_min_entity":   ManagedDevice._state_float(reader, device.wh_temp_min_entity) if device.wh_temp_min_entity else None,
            "off_peak_hysteresis_k": device.wh_off_peak_hysteresis_k,
        }

    if device.device_type == DEVICE_TYPE_POOL:
        now_ts = time_mod.time()
        base["pool"] = {
            "daily_run_minutes":    round(device.pool_daily_run_minutes, 1),
            "required_minutes_today": device.pool_required_minutes_today,
            "force_until":          _ts_iso(device.pool_force_until),
            "inhibit_until":        _ts_iso(device.pool_inhibit_until),
            "is_forced":            device.pool_force_until is not None and now_ts < device.pool_force_until,
            "is_inhibited":         device.pool_inhibit_until is not None and now_ts < device.pool_inhibit_until,
        }

    if device.device_type == DEVICE_TYPE_APPLIANCE:
        base["appliance"] = {
            "state":          device.appliance_state,
            "cycle_start":    _ts_iso(device.appliance_cycle_start),
        }

    return base


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return diagnostics for a config entry."""
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    dm = coordinator.device_manager
    eng = coordinator.scoring_engine

    # Score breakdown
    score_input = coordinator._build_score_input()
    f_surplus  = round(eng._score_surplus(score_input.get("surplus_w", 0.0)), 3)
    f_tempo    = round(eng._score_tempo(score_input.get("tempo_color")), 3)
    f_soc      = round(eng._score_soc(score_input.get("battery_soc")), 3)
    f_forecast = round(eng._score_forecast(score_input), 3)

    current_state = {
        "mode":               coordinator.mode,
        "global_score":       coordinator.global_score,
        "dispatch_threshold": coordinator.dispatch_threshold,
        "surplus_w":          coordinator.surplus_w,
        "pv_power_w":         coordinator.pv_power_w,
        "grid_power_w":       coordinator.grid_power_w,
        "house_power_w":      coordinator.house_power_w,
        "bat_available_w":    coordinator.bat_available_w,
        "battery_soc":        coordinator.battery_soc,
        "battery_power_w":    coordinator.battery_power_w,
        "battery_action":     coordinator.battery_action,
        "tempo_color":        coordinator.tempo_color,
        "tempo_next_color":   coordinator.tempo_next_color,
        "forecast_kwh":       coordinator.forecast_kwh,
        "grid_allowance_w":   coordinator.grid_allowance_w,
        "score_breakdown": {
            "f_surplus":  f_surplus,
            "f_tempo":    f_tempo,
            "f_soc":      f_soc,
            "f_forecast": f_forecast,
        },
        "scoring_weights": {
            "surplus":  round(eng.w_surplus,  3),
            "tempo":    round(eng.w_tempo,    3),
            "soc":      round(eng.w_soc,      3),
            "forecast": round(eng.w_forecast, 3),
        },
    }

    now_time = datetime.now().time()
    current_state["devices"] = [
        _device_diag(d, hass, now_time, coordinator.surplus_w, coordinator.bat_available_w)
        for d in dm.devices
    ]

    optimizer = {
        "last_run":        coordinator.optimizer_last_run,
        "context":         coordinator.optimizer_context,
        "chosen":          coordinator.optimizer_chosen,
        "top20":           coordinator.optimizer_top20,
        "chosen_schedule": coordinator.optimizer_chosen_schedule,
    }

    cfg = coordinator._cfg
    configuration = {
        "sources": {
            "pv_power_entity":        cfg.get(CONF_PV_POWER_ENTITY),
            "grid_power_entity":      cfg.get(CONF_GRID_POWER_ENTITY),
            "house_power_entity":     cfg.get(CONF_HOUSE_POWER_ENTITY),
            "tempo_color_entity":     cfg.get(CONF_TEMPO_COLOR_ENTITY),
            "tempo_next_color_entity": cfg.get(CONF_TEMPO_NEXT_COLOR_ENTITY),
            "forecast_entity":        cfg.get(CONF_FORECAST_ENTITY),
            "peak_pv_w":              cfg.get(CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W),
            "off_peak_1_start":       cfg.get(CONF_OFF_PEAK_1_START),
            "off_peak_1_end":         cfg.get(CONF_OFF_PEAK_1_END),
            "off_peak_2_start":       cfg.get(CONF_OFF_PEAK_2_START),
            "off_peak_2_end":         cfg.get(CONF_OFF_PEAK_2_END),
        },
        "battery": {
            "enabled":             cfg.get(CONF_BATTERY_ENABLED, False),
            "soc_entity":          cfg.get(CONF_BATTERY_SOC_ENTITY),
            "power_entity":        cfg.get(CONF_BATTERY_POWER_ENTITY),
            "charge_script":       cfg.get(CONF_BATTERY_CHARGE_SCRIPT),
            "autoconsum_script":   cfg.get(CONF_BATTERY_AUTOCONSUM_SCRIPT),
            "capacity_kwh":        cfg.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH),
            "soc_min":             cfg.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN),
            "soc_max":             cfg.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX),
            "soc_reserve_rouge":   cfg.get(CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE),
            "max_charge_power_w":  cfg.get(CONF_BATTERY_MAX_CHARGE_POWER_W),
            "max_discharge_power_w": cfg.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W),
        },
        "strategy": {
            "mode":                 cfg.get(CONF_MODE, MODE_AUTO),
            "scan_interval_minutes": cfg.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL),
            "dispatch_threshold":   cfg.get(CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD),
            "grid_allowance_w":     cfg.get(CONF_GRID_ALLOWANCE_W, DEFAULT_GRID_ALLOWANCE_W),
            "optimizer_alpha":      cfg.get(CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA),
            "ema_alpha":            cfg.get(CONF_EMA_ALPHA, DEFAULT_EMA_ALPHA),
            "base_load_noise":      cfg.get(CONF_BASE_LOAD_NOISE, DEFAULT_BASE_LOAD_NOISE),
            "optimizer_n_runs":     cfg.get(CONF_OPTIMIZER_N_RUNS, DEFAULT_OPTIMIZER_N_RUNS),
            "risk_lambda":          cfg.get(CONF_RISK_LAMBDA, DEFAULT_RISK_LAMBDA),
            "weight_pv_surplus":    cfg.get(CONF_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_PV_SURPLUS),
            "weight_tempo":         cfg.get(CONF_WEIGHT_TEMPO, DEFAULT_WEIGHT_TEMPO),
            "weight_battery_soc":   cfg.get(CONF_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_BATTERY_SOC),
            "weight_forecast":      cfg.get(CONF_WEIGHT_FORECAST, DEFAULT_WEIGHT_FORECAST),
        },
    }

    learner = coordinator.consumption_learner
    profile = learner.profile  # snapshot — list[float] | None
    if profile is not None:
        hourly_w = [
            round(sum(profile[h * 12:(h + 1) * 12]) / 12, 1)
            for h in range(24)
        ]
        base_load_profile = {
            "sample_count": learner.sample_count,
            "hourly_w": [
                {"hour": f"{h:02d}:00", "w": hourly_w[h]} for h in range(24)
            ],
            "profile_288": [round(v, 1) for v in profile],
        }
    else:
        base_load_profile = {"sample_count": 0, "hourly_w": [], "profile_288": []}

    return {
        "current_state":     current_state,
        "configuration":     configuration,
        "optimizer":         optimizer,
        "base_load_profile": base_load_profile,
        "decision_log":      list(dm.decision_log),
    }
