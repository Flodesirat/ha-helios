"""Sensor entities exposed by Energy Optimizer."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.util import slugify

from .const import DOMAIN, DEVICE_TYPE_APPLIANCE, APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_READY, APPLIANCE_STATE_PREPARING
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
    ]
    entities += [
        ApplianceStateSensor(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type == DEVICE_TYPE_APPLIANCE
    ]
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
            "name": "Energy Optimizer",
            "manufacturer": "Community",
            "model": "Energy Optimizer",
            "entry_type": "service",
        }


class EnergyOptimizerSurplusSensor(_BaseEOSensor):
    """Reports available PV surplus in Watts."""

    _attr_name = "EO PV surplus"
    _attr_unique_id_suffix = "surplus_pv"
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

    _attr_name = "EO global score"
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

    _attr_name = "EO battery action"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_battery_action"

    @property
    def native_value(self) -> str:
        return self.coordinator.battery_action


class EnergyOptimizerPVPowerSensor(_BaseEOSensor):
    """Reports total PV production in Watts."""

    _attr_name = "EO PV power"
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

    _attr_name = "EO grid power"
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

    _attr_name = "EO house power"
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

    _attr_name = "EO optimizer weights"
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


class ApplianceStateSensor(_BaseEOSensor):
    """Reports the Helios control state of an appliance: stop | en_attente | en_route."""

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry, device) -> None:
        super().__init__(coordinator, entry)
        self._device = device
        slug = slugify(device.name)
        self._attr_name      = f"EO {device.name} état"
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
