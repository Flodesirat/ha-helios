"""Select entities — force duration (devices, battery) and battery force direction."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DOMAIN, DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC,
    CONF_BATTERY_ENABLED, BATTERY_FORCE_DIRECTIONS,
)
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice

_DURATIONS: dict[str, float] = {
    "15min": 0.25,
    "30min": 0.50,
    "1h":    1.0,
    "2h":    2.0,
    "4h":    4.0,
    "8h":    8.0,
    "12h":  12.0,
    "24h":  24.0,
}
_DEFAULT_OPTION = "2h"

_FORCEABLE_TYPES = {DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [
        ForceDurationSelect(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type in _FORCEABLE_TYPES
    ]
    if coordinator.config.get(CONF_BATTERY_ENABLED) and coordinator.device_manager.battery_device is not None:
        entities += [
            BatteryForceDirectionSelect(coordinator, entry),
            BatteryForceDurationSelect(coordinator, entry),
        ]
    async_add_entities(entities)


class ForceDurationSelect(CoordinatorEntity, SelectEntity):
    """Select the force duration for a device."""

    _attr_options = list(_DURATIONS.keys())
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator)
        self._entry  = entry
        self._device = device
        slug = slugify(device.name)
        self._is_pool = device.device_type == DEVICE_TYPE_POOL
        self._attr_translation_placeholders = {"name": device.name}
        if self._is_pool:
            self._attr_translation_key  = "eo_pool_force_duration"
            self._attr_unique_id        = f"{entry.entry_id}_pool_{slug}_force_duration"
        else:
            self._attr_translation_key  = "eo_device_force_duration"
            self._attr_unique_id        = f"{entry.entry_id}_{device.device_type}_{slug}_force_duration"
        self._attr_suggested_object_id = f"{slug}_force_duration"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def current_option(self) -> str:
        duration = self._device.pool_force_duration_h if self._is_pool else self._device.force_duration_h
        for label, hours in _DURATIONS.items():
            if hours == duration:
                return label
        return _DEFAULT_OPTION

    async def async_select_option(self, option: str) -> None:
        if option in _DURATIONS:
            if self._is_pool:
                self._device.pool_force_duration_h = _DURATIONS[option]
            else:
                self._device.force_duration_h = _DURATIONS[option]
        self.async_write_ha_state()


class _BatteryForceSelect(CoordinatorEntity, SelectEntity):
    """Base for the battery force settings (persisted with the battery state)."""

    _attr_has_entity_name = True
    _suffix: str

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self._suffix}"

    @property
    def suggested_object_id(self) -> str:
        return self._suffix

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def _async_saved(self) -> None:
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        # A force in progress follows the new setting at once
        await self.coordinator.battery_drive.async_sync()


class BatteryForceDirectionSelect(_BatteryForceSelect):
    """Charge or discharge for the battery force."""

    _attr_translation_key = "eo_battery_force_direction"
    _attr_options = list(BATTERY_FORCE_DIRECTIONS)
    _suffix = "battery_force_direction"

    @property
    def current_option(self) -> str:
        return self.coordinator.device_manager.battery_device.force_direction

    async def async_select_option(self, option: str) -> None:
        if option in BATTERY_FORCE_DIRECTIONS:
            self.coordinator.device_manager.battery_device.force_direction = option
        await self._async_saved()


class BatteryForceDurationSelect(_BatteryForceSelect):
    """Duration of the battery force."""

    _attr_translation_key = "eo_battery_force_duration"
    _attr_options = list(_DURATIONS.keys())
    _suffix = "battery_force_duration"

    @property
    def current_option(self) -> str:
        duration = self.coordinator.device_manager.battery_device.force_duration_h
        for label, hours in _DURATIONS.items():
            if hours == duration:
                return label
        return _DEFAULT_OPTION

    async def async_select_option(self, option: str) -> None:
        if option in _DURATIONS:
            self.coordinator.device_manager.battery_device.force_duration_h = _DURATIONS[option]
        # Applies to the next force (as for the devices)
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
