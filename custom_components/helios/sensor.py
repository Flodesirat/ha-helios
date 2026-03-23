"""Sensor entities exposed by Energy Optimizer."""
from __future__ import annotations

import time as _time
from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.util import slugify

from .const import (
    DOMAIN,
    DEVICE_TYPE_APPLIANCE, APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_READY, APPLIANCE_STATE_PREPARING,
    DEVICE_TYPE_POOL,
)
from .coordinator import EnergyOptimizerCoordinator

# Appliance state → published state
_APPLIANCE_STATE_MAP = {
    APPLIANCE_STATE_RUNNING:   "en_route",
    APPLIANCE_STATE_READY:     "en_attente",
    APPLIANCE_STATE_PREPARING: "en_attente",
}
_APPLIANCE_STATE_DEFAULT = "stop"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EnergyOptimizerSurplusSensor(coordinator, entry),
        EnergyOptimizerScoreSensor(coordinator, entry),
        EnergyOptimizerBatteryActionSensor(coordinator, entry),
        EnergyOptimizerPVPowerSensor(coordinator, entry),
        EnergyOptimizerGridPowerSensor(coordinator, entry),
        EnergyOptimizerHousePowerSensor(coordinator, entry),
        EnergyOptimizerWeightsSensor(coordinator, entry),
        EnergyOptimizerBatterySocLevelSensor(coordinator, entry),
        EnergyOptimizerBatteryPowerSensor(coordinator, entry),
    ]
    entities.append(EnergyOptimizerBaseLoadSensor(coordinator, entry))
    for device in coordinator.device_manager.devices:
        entities.append(DevicePowerSensor(coordinator, entry, device))
        if device.device_type == DEVICE_TYPE_APPLIANCE:
            entities.append(ApplianceStateSensor(coordinator, entry, device))
        if device.device_type == DEVICE_TYPE_POOL:
            entities.append(PoolFiltrationRequiredSensor(coordinator, entry, device))
            entities.append(PoolFiltrationDoneSensor(coordinator, entry, device))
            entities.append(PoolForceRemainingSensor(coordinator, entry, device))
    async_add_entities(entities)


class _BaseEOSensor(CoordinatorEntity, SensorEntity):
    """Base class for all EO sensor entities."""

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Helios",
            "manufacturer": "Community",
            "model": "Helios",
            "entry_type": "service",
        }


class EnergyOptimizerSurplusSensor(_BaseEOSensor):
    """Reports available PV surplus in Watts."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_pv_surplus"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_surplus_pv"

    @property
    def native_value(self) -> float:
        return self.coordinator.surplus_w


class EnergyOptimizerScoreSensor(_BaseEOSensor):
    """Reports the global optimization score [0..1]."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_global_score"
    _attr_native_unit_of_measurement = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_global_score"

    @property
    def native_value(self) -> float:
        return self.coordinator.global_score

    @property
    def extra_state_attributes(self):
        return {
            "tempo_color": self.coordinator.tempo_color,
            "battery_soc": self.coordinator.battery_soc,
            "mode": self.coordinator.mode,
        }


class EnergyOptimizerBatteryActionSensor(_BaseEOSensor):
    """Reports current battery action: charge | discharge | reserve | idle."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery_action"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_battery_action"

    @property
    def native_value(self) -> str:
        return self.coordinator.battery_action


class EnergyOptimizerPVPowerSensor(_BaseEOSensor):
    """Reports total PV production in Watts."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_pv_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_pv_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.pv_power_w


class EnergyOptimizerGridPowerSensor(_BaseEOSensor):
    """Reports grid power in Watts (positive = import, negative = export)."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_grid_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_grid_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.grid_power_w


class EnergyOptimizerHousePowerSensor(_BaseEOSensor):
    """Reports total house consumption in Watts."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_house_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_house_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.house_power_w


class EnergyOptimizerWeightsSensor(_BaseEOSensor):
    """Exposes the scoring weights applied by the daily optimizer.

    State    : dispatch threshold [0..1]
    Attributes: w_surplus, w_tempo, w_soc, w_forecast, last_optimized (ISO timestamp)
    """

    _attr_has_entity_name = True
    _attr_translation_key = "eo_optimizer_weights"
    _attr_native_unit_of_measurement = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_optimizer_weights"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.dispatch_threshold, 3)

    @property
    def extra_state_attributes(self) -> dict:
        eng = self.coordinator.scoring_engine
        return {
            "w_surplus":      round(eng.w_surplus,  3),
            "w_tempo":        round(eng.w_tempo,    3),
            "w_soc":          round(eng.w_soc,      3),
            "w_forecast":     round(eng.w_forecast, 3),
            "last_optimized": self.coordinator.optimizer_last_run,
        }


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


class EnergyOptimizerBatteryPowerSensor(_BaseEOSensor):
    """Reports current battery power in W. Negative = charging, positive = discharging."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_battery_power"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.battery_power_w


class EnergyOptimizerBatterySocLevelSensor(_BaseEOSensor):
    """Reports a textual label for the battery SOC level."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery_soc_level"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_battery_soc_level"

    @property
    def native_value(self) -> str | None:
        return _soc_level_label(self.coordinator.battery_soc)

    @property
    def extra_state_attributes(self) -> dict:
        return {"battery_soc": self.coordinator.battery_soc}


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
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_base_load_profile"

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


class DevicePowerSensor(_BaseEOSensor):
    """Reports the current power draw of a Helios-controlled device.

    Returns device.power_w when the device is ON (controlled by Helios or manually),
    0.0 otherwise.  This lets users build a total-devices power sum in HA and
    helps evaluate the real base load (house_power − sum_of_devices).
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry)
        self._device = device
        slug = slugify(device.name)
        self._attr_unique_id = f"{entry.entry_id}_device_{slug}_power"
        self._attr_translation_key = "eo_device_power"
        self._attr_translation_placeholders = {"name": device.name}

    @property
    def native_value(self) -> float:
        return float(self._device.power_w) if self._device.is_on else 0.0

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "rated_power_w": self._device.power_w,
            "is_on": self._device.is_on,
            "manual_mode": self._device.manual_mode,
        }


class ApplianceStateSensor(_BaseEOSensor):
    """Reports the Helios control state of an appliance: stop | en_attente | en_route."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry)
        self._device = device
        slug = slugify(device.name)
        self._attr_translation_key = "eo_appliance_state"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_appliance_{slug}_state"

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    @property
    def native_value(self) -> str:
        return _APPLIANCE_STATE_MAP.get(self._device.appliance_state, _APPLIANCE_STATE_DEFAULT)

    @property
    def extra_state_attributes(self) -> dict:
        return {"internal_state": self._device.appliance_state}


class _BasePoolSensor(_BaseEOSensor):
    """Base class for pool filtration sensors."""

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry)
        self._device = device
        self._slug   = slugify(device.name)

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id  # set by subclass


class PoolFiltrationRequiredSensor(_BasePoolSensor):
    """Total filtration time required today (from the configured entity), in minutes."""

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_translation_key = "eo_pool_filtration_required"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_pool_{self._slug}_required"

    @property
    def native_value(self) -> float | None:
        # Prefer the 05:00 snapshot — that's what the optimizer actually uses.
        snapshot = self._device.pool_required_minutes_today
        if snapshot is not None:
            return round(snapshot, 1)
        # Before 05:00: fall back to live value for display only.
        entity_id = self._device.pool_filtration_entity
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return round(float(state.state) * 60, 1)  # hours → minutes
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict:
        return {"snapshot_taken": self._device.pool_required_minutes_today is not None}


class PoolFiltrationDoneSensor(_BasePoolSensor):
    """Filtration time already completed today, in minutes."""

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_translation_key = "eo_pool_filtration_done"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_pool_{self._slug}_done"

    @property
    def native_value(self) -> float:
        return round(self._device.pool_daily_run_minutes, 1)


class PoolForceRemainingSensor(_BasePoolSensor):
    """Minutes remaining in pool force mode (0 when not active)."""

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_translation_key = "eo_pool_force_remaining"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_pool_{self._slug}_force_remaining"

    @property
    def native_value(self) -> float:
        fu = self._device.pool_force_until
        if fu is None:
            return 0.0
        return round(max(0.0, (fu - _time.time()) / 60), 1)

    @property
    def extra_state_attributes(self) -> dict:
        iu = self._device.pool_inhibit_until
        inhibit_remaining = 0.0 if iu is None else round(max(0.0, (iu - _time.time()) / 60), 1)
        return {"inhibit_remaining_min": inhibit_remaining}
