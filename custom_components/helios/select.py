"""Select entities — force duration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC
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
    async_add_entities([
        ForceDurationSelect(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type in _FORCEABLE_TYPES
    ])


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
