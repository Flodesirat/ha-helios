"""Sensor entities exposed by Energy Optimizer."""
from __future__ import annotations

import time as _time
from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    DEVICE_TYPE_APPLIANCE, APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_PREPARING,
    DEVICE_TYPE_POOL, DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER,
    CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W,
    CONF_GRID_SUBSCRIPTION_W, DEFAULT_GRID_SUBSCRIPTION_W,
    CONF_BATTERY_ENABLED,
)
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EnergyOptimizerSurplusSensor(coordinator, entry),
        EnergyOptimizerScoreSensor(coordinator, entry),
        EnergyOptimizerBatterySensor(coordinator, entry),
        EnergyOptimizerTempoNextColorSensor(coordinator, entry),
        EnergyOptimizerPVPowerSensor(coordinator, entry),
        EnergyOptimizerGridPowerSensor(coordinator, entry),
        EnergyOptimizerHousePowerSensor(coordinator, entry),
        EnergyOptimizerBaseLoadSensor(coordinator, entry),
    ]
    for device in coordinator.device_manager.devices:
        entities.append(DeviceStateSensor(coordinator, entry, device))
    async_add_entities(entities)


class _BaseEOSensor(CoordinatorEntity, SensorEntity):
    """Base class for all EO sensor entities."""

    _unique_suffix: str  # Set as class attribute in each subclass

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self._unique_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )


class EnergyOptimizerSurplusSensor(_BaseEOSensor):
    """Reports available PV surplus in Watts."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_pv_surplus"
    suggested_object_id = "pv_surplus"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unique_suffix = "surplus_pv"

    @property
    def native_value(self) -> float:
        return self.coordinator.surplus_w


class EnergyOptimizerScoreSensor(_BaseEOSensor):
    """Reports the global optimization score [0..1]."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_global_score"
    suggested_object_id = "global_score"
    _attr_native_unit_of_measurement = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unique_suffix = "global_score"

    @property
    def native_value(self) -> float:
        return self.coordinator.global_score

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        eng = c.scoring_engine
        score_input = c._build_score_input()
        return {
            # Score breakdown — explains *why* the score is what it is
            "f_surplus":  round(eng._score_surplus(score_input.get("surplus_w", 0.0), score_input.get("battery_soc")), 3),
            "f_tempo":    round(eng._score_tempo(score_input.get("tempo_color")), 3),
            "f_soc":      round(eng._score_soc(score_input.get("battery_soc")), 3),
            "f_solar": round(eng._score_solar(score_input), 3),
            # Scoring weights (can be tuned by daily optimizer)
            "w_surplus":  round(eng.w_surplus,  3),
            "w_tempo":    round(eng.w_tempo,    3),
            "w_soc":      round(eng.w_soc,      3),
            "w_solar": round(eng.w_solar, 3),
            # Dispatch context
            "dispatch_threshold": round(c.dispatch_threshold, 3),
            "last_optimized":     c.optimizer_last_run,
            # Raw inputs
            "tempo_color": c.tempo_color,
            "battery_soc": c.battery_soc,
            "enabled":     c.enabled,
            # Dispatch budget (used by the Lovelace card)
            "surplus_w":         round(c.surplus_w),
            "virtual_surplus_w": round(c.virtual_surplus_w),
            "bat_available_w":   round(c.bat_available_w),
            "remaining_w":       round(c.device_manager.remaining_w),
            # Installation parameters (used by the Lovelace card)
            "peak_pv_w":          int(c.config.get(CONF_PEAK_PV_W,          DEFAULT_PEAK_PV_W)),
            "grid_subscription_w": int(c.config.get(CONF_GRID_SUBSCRIPTION_W, DEFAULT_GRID_SUBSCRIPTION_W)),
        }


class EnergyOptimizerBatterySensor(_BaseEOSensor):
    """Single battery entity — state is the current action, attributes hold all battery data."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery"
    suggested_object_id = "battery"
    _unique_suffix = "battery"

    @property
    def native_value(self) -> str:
        return self.coordinator.battery_action

    @property
    def extra_state_attributes(self) -> dict:
        c = self.coordinator
        soc = c.battery_soc
        return {
            "battery_enabled": bool(c.config.get(CONF_BATTERY_ENABLED, False)),
            "soc":             soc,
            "soc_level":       _soc_level_label(soc),
            "power_w":         c.battery_power_w,
            "available_w":     c.bat_available_w,
        }


class EnergyOptimizerTempoNextColorSensor(_BaseEOSensor):
    """Reports tomorrow's Tempo color (normalized to blue/white/red)."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_tempo_next_color"
    suggested_object_id = "tempo_next_color"
    _unique_suffix = "tempo_next_color"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.tempo_next_color


class EnergyOptimizerPVPowerSensor(_BaseEOSensor):
    """Reports total PV production in Watts."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_pv_power"
    suggested_object_id = "pv_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unique_suffix = "pv_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.pv_power_w


class EnergyOptimizerGridPowerSensor(_BaseEOSensor):
    """Reports grid power in Watts (positive = import, negative = export)."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_grid_power"
    suggested_object_id = "grid_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unique_suffix = "grid_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.grid_power_w


class EnergyOptimizerHousePowerSensor(_BaseEOSensor):
    """Reports total house consumption in Watts."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_house_power"
    suggested_object_id = "house_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unique_suffix = "house_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.house_power_w


def _soc_level_label(soc: float | None) -> str | None:
    if soc is None:
        return None
    if soc <= 20:
        return "Réserve"
    if soc <= 50:
        return "Basse"
    if soc <= 75:
        return "Optimale"
    if soc <= 90:
        return "Haute"
    if soc <= 95:
        return "Très haute"
    return "Pleine"


class EnergyOptimizerBaseLoadSensor(_BaseEOSensor):
    """Exposes the EMA-learned base load profile.

    State    : current slot's learned base load value in W.
    Attributes:
        sample_count  — total EMA updates received since last cold start.
        hourly_w      — list of 24 dicts {"hour": "HH:00", "w": float}
                        (mean of the 12 five-minute slots per hour).
                        Ready to feed an ApexCharts card.
    """

    _attr_translation_key = "eo_base_load_profile"
    _attr_has_entity_name = True
    suggested_object_id = "base_load_profile"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unique_suffix = "base_load_profile"

    @property
    def native_value(self) -> float | None:
        profile = self.coordinator.consumption_learner.profile
        if profile is None:
            return None
        now = datetime.now()
        slot = (now.hour * 60 + now.minute) // 5
        return round(profile[slot % 288], 1)

    @property
    def extra_state_attributes(self) -> dict:
        learner = self.coordinator.consumption_learner
        profile = learner.profile
        if profile is None:
            return {"sample_count": 0, "hourly_w": []}
        hourly_w = [
            {"hour": f"{h:02d}:00", "w": round(sum(profile[h * 12:(h + 1) * 12]) / 12, 1)}
            for h in range(24)
        ]
        return {
            "sample_count": learner.sample_count,
            "hourly_w": hourly_w,
        }


class DeviceStateSensor(_BaseEOSensor):
    """Primary state entity for a Helios-managed device.

    State  : "running" | "waiting" | "off"
    Attributes: all device data — power, score, type-specific info.
    """

    _attr_has_entity_name = True
    _unique_suffix = "device_state"  # placeholder; overwritten per-instance below

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        slug = slugify(device.name)
        self._unique_suffix = f"device_state_{slug}"
        super().__init__(coordinator, entry)
        self._device = device
        self._attr_translation_key = "eo_device_state"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_suggested_object_id = f"{slug}"

    @property
    def native_value(self) -> str:
        d = self._device
        if d.device_type == DEVICE_TYPE_APPLIANCE:
            if d.appliance_state == APPLIANCE_STATE_RUNNING:
                return "running"
            if d.appliance_state == APPLIANCE_STATE_PREPARING:
                return "waiting"
            return "off"
        return "running" if d.is_on else "off"

    @property
    def extra_state_attributes(self) -> dict:
        d = self._device
        reader = ManagedDevice._make_ha_reader(self.coordinator.hass)
        attrs: dict = {
            "device_name":          d.name,
            "device_type":          d.device_type,
            "device_priority":      d.priority,
            "is_on":                d.is_on,
            "manual_mode":          d.manual_mode,
            "power_w":              float(d.actual_power_w(reader)) if d.is_on else 0.0,
            "last_effective_score": d.last_effective_score,
            "last_priority_score":  d.last_priority_score,
            "last_fit":             d.last_fit,
            "last_urgency":         d.last_urgency,
            "last_decision_reason": d.last_decision_reason,
            "allowed_start":        d.allowed_start,
            "allowed_end":          d.allowed_end,
            "daily_on_minutes":     round(d.daily_on_minutes, 1),
        }
        if d.device_type == DEVICE_TYPE_APPLIANCE:
            attrs["appliance_state"]        = d.appliance_state
            attrs["appliance_ready_entity"] = d.appliance_ready_entity
        elif d.device_type == DEVICE_TYPE_WATER_HEATER:
            attrs["wh_temp_target"] = d.wh_temp_target
            if d.wh_temp_entity:
                s = self.coordinator.hass.states.get(d.wh_temp_entity)
                if s and s.state not in ("unavailable", "unknown"):
                    try:
                        attrs["temperature"] = float(s.state)
                    except ValueError:
                        pass
        elif d.device_type == DEVICE_TYPE_EV:
            attrs["ev_soc_entity"]     = d.ev_soc_entity
            attrs["ev_plugged_entity"] = d.ev_plugged_entity
            if d.ev_soc_entity:
                s = self.coordinator.hass.states.get(d.ev_soc_entity)
                if s and s.state not in ("unavailable", "unknown"):
                    try:
                        attrs["soc"] = float(s.state)
                    except ValueError:
                        pass
            if d.ev_plugged_entity:
                s = self.coordinator.hass.states.get(d.ev_plugged_entity)
                if s:
                    attrs["plugged"] = s.state == "on"
        elif d.device_type == DEVICE_TYPE_POOL:
            attrs["filtration_done_min"]     = round(d.pool_daily_run_minutes, 1)
            attrs["filtration_required_min"] = round(d.pool_required_minutes_today or 0.0, 1)
            fu = d.pool_force_until
            attrs["force_remaining_min"]     = round(max(0.0, (fu - _time.time()) / 60), 1) if fu else 0.0
        return attrs
