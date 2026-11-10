"""Binary sensor entities — one per managed device, reflects Helios control state."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE
from .coordinator import EnergyOptimizerCoordinator
from .managed_device import ManagedDevice
from .pool_filtration import FROST_OFF


def _epoch_to_iso(ts: float | None) -> str | None:
    """Convert epoch seconds to ISO 8601 string (UTC), or None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        DeviceControlSensor(coordinator, entry, device)
        for device in coordinator.device_manager.devices
    ]
    for device in coordinator.device_manager.devices:
        if device.device_type == DEVICE_TYPE_POOL:
            entities += [
                PoolWinteringSensor(coordinator, entry, device),
                PoolFrostSensor(coordinator, entry, device),
            ]
    async_add_entities(entities)


class DeviceControlSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor: True when Helios is actively controlling the device ON."""

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        slug = slugify(device.name)
        self._attr_unique_id = f"{entry.entry_id}_device_{slug}"
        self._attr_has_entity_name = True
        self._attr_translation_key = "eo_device"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return bool(self._device.is_on)

    @property
    def extra_state_attributes(self) -> dict:
        d = self._device
        attrs: dict = {
            "device_type":   d.device_type,
            "turned_on_at":  _epoch_to_iso(d.turned_on_at),
            "turned_off_at": _epoch_to_iso(d.turned_off_at),
        }
        if d.device_type == DEVICE_TYPE_POOL:
            attrs["pool_daily_run_minutes"] = round(d.pool_daily_run_minutes, 1)
        if d.device_type == DEVICE_TYPE_APPLIANCE:
            attrs["appliance_state"] = d.appliance_state
        return attrs


class _PoolBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base for pool state binary sensors."""

    _attr_has_entity_name = True
    _suffix = ""

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        slug = slugify(device.name)
        self._attr_unique_id = f"{entry.entry_id}_pool_{slug}_{self._suffix}"
        self._attr_suggested_object_id = f"{slug}_{self._suffix}"
        self._attr_translation_placeholders = {"name": device.name}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Helios",
            manufacturer="Community",
            model="Helios",
            entry_type=DeviceEntryType.SERVICE,
        )


class PoolWinteringSensor(_PoolBinarySensor):
    """ON while the pool is in (automatic) wintering."""

    _suffix = "wintering"
    _attr_translation_key = "eo_pool_wintering"

    @property
    def is_on(self) -> bool:
        return self._device.pool_wintering

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "wintering_enabled":    self._device.pool_wintering_enabled,
            "wintering_temp":       self._device.pool_wintering_temp,
            "wintering_duration_h": self._device.pool_wintering_duration_h,
        }


class PoolFrostSensor(_PoolBinarySensor):
    """ON while frost protection is active (sessions or continuous run)."""

    _suffix = "frost_protection"
    _attr_translation_key = "eo_pool_frost_protection"

    @property
    def is_on(self) -> bool:
        return self._device.pool_frost_level != FROST_OFF

    @property
    def extra_state_attributes(self) -> dict:
        return {"frost_level": self._device.pool_frost_level}
