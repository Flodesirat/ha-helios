"""Switch entities exposed by Energy Optimizer."""
from __future__ import annotations

import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    CONF_BATTERY_ENABLED,
    DEVICE_TYPE_POOL, DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_GENERIC,
)
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice


class _BaseDeviceSwitch(CoordinatorEntity, SwitchEntity):
    """Base class for per-device switch entities."""

    _attr_has_entity_name = True
    _attr_suggested_object_id: str | None = None

    @property
    def suggested_object_id(self) -> str | None:
        return self._attr_suggested_object_id

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
            "device_name": self._device.name,
            "device_type": self._device.device_type,
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
    if coordinator.config.get(CONF_BATTERY_ENABLED) and coordinator.device_manager.battery_device is not None:
        entities.append(BatteryDrivingSwitch(coordinator, entry))
        entities.append(BatteryForceSwitch(coordinator, entry))
    # switch.helios_battery_manual (before 1.1) is replaced by battery_driving
    registry = er.async_get(hass)
    if old_id := registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_battery_manual"):
        registry.async_remove(old_id)
    entities += [
        PoolForceSwitch(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type == DEVICE_TYPE_POOL
    ]
    entities += [
        DeviceForceSwitch(coordinator, entry, device)
        for device in coordinator.device_manager.devices
        if device.device_type in {DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC}
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

    suggested_object_id = "auto_mode"

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.enabled = True
        self.async_write_ha_state()
        await self.coordinator.battery_drive.async_sync()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.enabled = False
        self.async_write_ha_state()
        await self.coordinator.battery_drive.async_sync()

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
        self._attr_suggested_object_id = f"{slug}_force"

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


class DeviceForceSwitch(_BaseDeviceSwitch):
    """Force a device (water_heater, ev_charger, appliance) ON for the selected duration."""

    _attr_translation_key = "eo_device_force"

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator, entry, device)
        slug = slugify(device.name)
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_unique_id = f"{entry.entry_id}_{device.device_type}_{slug}_force"
        self._attr_suggested_object_id = f"{slug}_force"

    @property
    def is_on(self) -> bool:
        fu = self._device.force_until
        return fu is not None and time.time() < fu

    async def async_turn_on(self, **kwargs) -> None:
        duration_s = self._device.force_duration_h * 3600
        self._device.force_until = time.time() + duration_s
        self._device.inhibit_until = None
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
        self._device.force_until = None
        self._device.inhibit_until = time.time() + self._device.force_duration_h * 3600
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


class BatteryForceSwitch(CoordinatorEntity, SwitchEntity):
    """Force a charge or a discharge of the batteries (direction, power and
    duration set by select.helios_battery_force_direction,
    number.helios_battery_force_power and select.helios_battery_force_duration).

    Works with driving off too: Drive is then used for the force only.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery_force"
    suggested_object_id = "battery_force"

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_battery_force"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # A force ends between coordinator cycles (expiry, SOC limit)
        self.async_on_remove(self.coordinator.battery_drive.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self.coordinator.battery_forced

    @property
    def extra_state_attributes(self) -> dict:
        bat = self.coordinator.device_manager.battery_device
        return {"force_remaining_min": round(bat.force_remaining_s() / 60, 1) if bat else 0.0}

    async def _async_set(self, until: float | None) -> None:
        bat = self.coordinator.device_manager.battery_device
        if bat is None:
            return
        bat.force_until = until
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        await self.coordinator.battery_drive.async_sync()

    async def async_turn_on(self, **kwargs) -> None:
        bat = self.coordinator.device_manager.battery_device
        if bat is not None:
            await self._async_set(time.time() + bat.force_duration_h * 3600)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(None)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )


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
        self._attr_suggested_object_id = f"{slug}_manual"

    @property
    def is_on(self) -> bool:
        return self._device.manual_mode

    async def async_turn_on(self, **kwargs) -> None:
        self._device.manual_mode = True
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.manual_mode = False
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


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
        self._attr_suggested_object_id = f"{slug}_plugged"

    @property
    def is_on(self) -> bool:
        return self._device.ev_plugged_manual

    async def async_turn_on(self, **kwargs) -> None:
        self._device.ev_plugged_manual = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._device.ev_plugged_manual = False
        self.async_write_ha_state()


class BatteryDrivingSwitch(CoordinatorEntity, SwitchEntity):
    """Helios drives the batteries (Drive mode regulation).

    ON  → the Drive scripts are called and the setpoints follow the grid.
    OFF → the neutral scripts are called; the battery is left out of the
          dispatch (no charge demand, nothing available for the devices).
    The state is persisted and restored on restart.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery_driving"
    suggested_object_id = "battery_driving"

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_battery_driving"

    @property
    def is_on(self) -> bool:
        return self.coordinator.battery_driving

    async def _async_set(self, driving: bool) -> None:
        bat = self.coordinator.device_manager.battery_device
        if bat is None:
            return
        bat.driving = driving
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        await self.coordinator.battery_drive.async_sync()

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(False)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )
