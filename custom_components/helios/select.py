"""Select entities — pool force duration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, DEVICE_TYPE_POOL
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice

_DURATIONS: dict[str, float] = {
    "1h":  1.0,
    "2h":  2.0,
    "4h":  4.0,
    "12h": 12.0,
    "24h": 24.0,
}
_DEFAULT_OPTION = "2h"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PoolForceDurationSelect(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type == DEVICE_TYPE_POOL
    ])


class PoolForceDurationSelect(CoordinatorEntity, SelectEntity):
    """Select the duration for pool force mode."""

    _attr_options = list(_DURATIONS.keys())

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
        self._attr_has_entity_name = True
        self._attr_translation_key = "eo_pool_force_duration"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_pool_{slug}_force_duration"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Helios",
            "manufacturer": "Community",
            "model": "Helios",
            "entry_type": "service",
        }

    @property
    def current_option(self) -> str:
        # Find the label matching the stored duration
        for label, hours in _DURATIONS.items():
            if hours == self._device.pool_force_duration_h:
                return label
        return _DEFAULT_OPTION

    async def async_select_option(self, option: str) -> None:
        if option in _DURATIONS:
            self._device.pool_force_duration_h = _DURATIONS[option]
        self.async_write_ha_state()
