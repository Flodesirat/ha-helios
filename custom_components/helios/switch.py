"""Switch entities exposed by Energy Optimizer."""
from __future__ import annotations

import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, MODE_AUTO, MODE_OFF, DEVICE_TYPE_POOL
from .coordinator import EnergyOptimizerCoordinator
from .device_manager import ManagedDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [EnergyOptimizerModeSwitch(coordinator, entry)]
    entities += [
        PoolForceSwitch(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type == DEVICE_TYPE_POOL
    ]
    entities += [
        DeviceManualSwitch(coordinator, entry, device)
        for device in coordinator.device_manager.devices
    ]
    async_add_entities(entities)


class EnergyOptimizerModeSwitch(CoordinatorEntity, SwitchEntity):
    """Master on/off switch — toggles between AUTO and OFF mode."""

    _attr_name = "EO auto mode"

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_mode_auto"

    @property
    def is_on(self) -> bool:
        return self.coordinator.mode == MODE_AUTO

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.mode = MODE_AUTO
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.mode = MODE_OFF
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Energy Optimizer",
        }


class PoolForceSwitch(CoordinatorEntity, SwitchEntity):
    """Force pool filtration ON for the selected duration, or turn it OFF immediately."""

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
        self._attr_name      = f"EO {device.name} forçage"
        self._attr_unique_id = f"{entry.entry_id}_pool_{slug}_force"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Energy Optimizer",
        }

    @property
    def is_on(self) -> bool:
        fu = self._device.pool_force_until
        return fu is not None and time.time() < fu

    async def async_turn_on(self, **kwargs) -> None:
        duration_s = self._device.pool_force_duration_h * 3600
        self._device.pool_force_until = time.time() + duration_s
        # Apply immediately to the physical switch
        if self._device.switch_entity:
            await self.hass.services.async_call(
                "homeassistant", "turn_on",
                {"entity_id": self._device.switch_entity},
                blocking=False,
            )
        self._device.is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.pool_force_until = None
        # Block the optimizer from re-enabling the pump for the selected duration
        self._device.pool_inhibit_until = time.time() + self._device.pool_force_duration_h * 3600
        if self._device.switch_entity:
            await self.hass.services.async_call(
                "homeassistant", "turn_off",
                {"entity_id": self._device.switch_entity},
                blocking=False,
            )
        self._device.is_on = False
        self.async_write_ha_state()


class DeviceManualSwitch(CoordinatorEntity, SwitchEntity):
    """Per-device manual mode switch — ON means Helios hands off the device entirely."""

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
        self._attr_name      = f"EO {device.name} manuel"
        self._attr_unique_id = f"{entry.entry_id}_device_{slug}_manual"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Energy Optimizer",
        }

    @property
    def is_on(self) -> bool:
        return self._device.manual_mode

    async def async_turn_on(self, **kwargs) -> None:
        self._device.manual_mode = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.manual_mode = False
        self.async_write_ha_state()
