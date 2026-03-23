"""Binary sensor entities — one per managed device, reflects Helios control state."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE
from .coordinator import EnergyOptimizerCoordinator
from .device_manager import ManagedDevice

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_to_iso(ts: float | None) -> str | None:
    """Convert epoch seconds to ISO 8601 string (UTC), or None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        DeviceControlSensor(coordinator, entry, device)
        for device in coordinator.device_manager.devices
    ])


class DeviceControlSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor: True when Helios is actively controlling the device ON."""

    def __init__(
        self,
        coordinator: EnergyOptimizerCoordinator,
        entry: ConfigEntry,
        device: ManagedDevice,
    ) -> None:
        super().__init__(coordinator)
        self._entry   = entry
        self._device  = device
        slug          = slugify(device.name)
        self._attr_unique_id = f"{entry.entry_id}_device_{slug}"
        self._attr_has_entity_name = True
        self._attr_translation_key = "eo_device"
        self._attr_translation_placeholders = {"name": device.name}

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
    def is_on(self) -> bool:
        return self._device.is_on

    @property
    def extra_state_attributes(self) -> dict:
        d = self._device
        attrs = {
            "device_type":   d.device_type,
            "turned_on_at":  _epoch_to_iso(d.turned_on_at),
            "turned_off_at": _epoch_to_iso(d.turned_off_at),
        }
        if d.device_type == DEVICE_TYPE_POOL:
            attrs["pool_daily_run_minutes"] = round(d.pool_daily_run_minutes, 1)
        if d.device_type == DEVICE_TYPE_APPLIANCE:
            attrs["appliance_state"] = d.appliance_state
        return attrs
