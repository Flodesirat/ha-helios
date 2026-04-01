"""Switch entities exposed by Energy Optimizer."""
from __future__ import annotations

import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, MODE_AUTO, MODE_OFF, DEVICE_TYPE_POOL, DEVICE_TYPE_EV
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice


class _BaseDeviceSwitch(CoordinatorEntity, SwitchEntity):
    """Base class for per-device switch entities."""

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

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "last_effective_score": self._device.last_effective_score,
            "last_decision_reason": self._device.last_decision_reason,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )


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
    entities += [
        EVPluggedSwitch(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type == DEVICE_TYPE_EV and not device.ev_plugged_entity
    ]
    async_add_entities(entities)


class EnergyOptimizerModeSwitch(CoordinatorEntity, SwitchEntity):
    """Master on/off switch — toggles between AUTO and OFF mode."""

    _attr_has_entity_name = True
    _attr_translation_key = "eo_auto_mode"

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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )


class PoolForceSwitch(_BaseDeviceSwitch):
    """Force pool filtration ON for the selected duration, or turn it OFF immediately."""

    _attr_translation_key = "eo_pool_force"

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator, entry, device)
        slug = slugify(device.name)
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_pool_{slug}_force"

    @property
    def is_on(self) -> bool:
        fu = self._device.pool_force_until
        return fu is not None and time.time() < fu

    async def async_turn_on(self, **kwargs) -> None:
        duration_s = self._device.pool_force_duration_h * 3600
        self._device.pool_force_until = time.time() + duration_s
        if self._device.switch_entity:
            await self.hass.services.async_call(
                "homeassistant", "turn_on",
                {"entity_id": self._device.switch_entity},
                blocking=False,
            )
        self._device.is_on = True
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.pool_force_until = None
        self._device.pool_inhibit_until = time.time() + self._device.pool_force_duration_h * 3600
        if self._device.switch_entity:
            await self.hass.services.async_call(
                "homeassistant", "turn_off",
                {"entity_id": self._device.switch_entity},
                blocking=False,
            )
        self._device.is_on = False
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class DeviceManualSwitch(_BaseDeviceSwitch):
    """Per-device manual mode switch — ON means Helios hands off the device entirely."""

    _attr_translation_key = "eo_device_manual"

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator, entry, device)
        slug = slugify(device.name)
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_device_{slug}_manual"

    @property
    def is_on(self) -> bool:
        return self._device.manual_mode

    async def async_turn_on(self, **kwargs) -> None:
        self._device.manual_mode = True
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.manual_mode = False
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()


class EVPluggedSwitch(_BaseDeviceSwitch):
    """Manual 'EV plugged in' indicator — used when no external plugged entity is configured."""

    _attr_translation_key = "eo_ev_plugged"

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator, entry, device)
        slug = slugify(device.name)
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_ev_{slug}_plugged"

    @property
    def is_on(self) -> bool:
        return self._device.ev_plugged_manual

    async def async_turn_on(self, **kwargs) -> None:
        self._device.ev_plugged_manual = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.ev_plugged_manual = False
        self.async_write_ha_state()
