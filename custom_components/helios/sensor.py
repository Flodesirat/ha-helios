"""Sensor entities exposed by Energy Optimizer."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EnergyOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EnergyOptimizerSurplusSensor(coordinator, entry),
        EnergyOptimizerScoreSensor(coordinator, entry),
        EnergyOptimizerBatteryActionSensor(coordinator, entry),
    ])


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
