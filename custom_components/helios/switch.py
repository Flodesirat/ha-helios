"""Switch entities exposed by Energy Optimizer."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTO, MODE_MANUAL, MODE_OFF
from .coordinator import EnergyOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EnergyOptimizerModeSwitch(coordinator, entry)])


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
