"""Diagnostics support for Helios Energy Optimizer."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EnergyOptimizerCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return diagnostics for a config entry."""
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    dm = coordinator.device_manager
    eng = coordinator.scoring_engine

    current_state = {
        "mode": coordinator.mode,
        "global_score": coordinator.global_score,
        "dispatch_threshold": coordinator.dispatch_threshold,
        "surplus_w": coordinator.surplus_w,
        "pv_power_w": coordinator.pv_power_w,
        "grid_power_w": coordinator.grid_power_w,
        "house_power_w": coordinator.house_power_w,
        "bat_available_w": coordinator.bat_available_w,
        "battery_soc": coordinator.battery_soc,
        "battery_action": coordinator.battery_action,
        "tempo_color": coordinator.tempo_color,
        "forecast_kwh": coordinator.forecast_kwh,
        "grid_allowance_w": coordinator.grid_allowance_w,
        "scoring_weights": {
            "surplus":  round(eng.w_surplus,  3),
            "tempo":    round(eng.w_tempo,    3),
            "soc":      round(eng.w_soc,      3),
            "forecast": round(eng.w_forecast, 3),
        },
        "devices": [
            {
                "name":        d.name,
                "type":        d.device_type,
                "is_on":       d.is_on,
                "manual_mode": d.manual_mode,
                "priority":    d.priority,
                "power_w":     d.power_w,
            }
            for d in dm.devices
        ],
    }

    optimizer = {
        "last_run":        coordinator.optimizer_last_run,
        "context":         coordinator.optimizer_context,
        "chosen":          coordinator.optimizer_chosen,
        "top20":           coordinator.optimizer_top20,
        "chosen_schedule": coordinator.optimizer_chosen_schedule,
    }

    learner = coordinator.consumption_learner
    profile = learner.profile  # snapshot — list[float] | None
    if profile is not None:
        # 24 hourly averages (mean of the 12 slots per hour)
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
        "optimizer":         optimizer,
        "base_load_profile": base_load_profile,
        "decision_log":      list(dm.decision_log),
    }
