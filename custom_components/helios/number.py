"""Number entities — battery force power."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    BATTERY_FORCE_DISCHARGE,
)
from .coordinator import EnergyOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)

_FALLBACK_MAX_W = 15000.0  # when no battery power limit is configured


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.config.get(CONF_BATTERY_ENABLED) and coordinator.device_manager.battery_device is not None:
        async_add_entities([BatteryForcePowerNumber(coordinator, entry)])


class BatteryForcePowerNumber(CoordinatorEntity, NumberEntity):
    """Total power of the battery force (W), shared between the batteries.

    Until set, it shows the full power of the selected direction.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "eo_battery_force_power"
    suggested_object_id = "battery_force_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0.0
    _attr_native_step = 50.0
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_battery_force_power"

    def _max_w(self, discharge: bool) -> float:
        cfg = self.coordinator.config  # totals over all batteries
        key = CONF_BATTERY_MAX_DISCHARGE_POWER_W if discharge else CONF_BATTERY_MAX_CHARGE_POWER_W
        return float(cfg.get(key) or 0.0)

    @property
    def native_max_value(self) -> float:
        return max(self._max_w(True), self._max_w(False)) or _FALLBACK_MAX_W

    @property
    def native_value(self) -> float:
        bat = self.coordinator.device_manager.battery_device
        if bat.force_power_w is not None:
            return float(bat.force_power_w)
        return self._max_w(bat.force_direction == BATTERY_FORCE_DISCHARGE)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.device_manager.battery_device.force_power_w = float(value)
        await self.coordinator.device_manager.async_persist_device_state()
        self.async_write_ha_state()
        # A force in progress follows the new power at once
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
