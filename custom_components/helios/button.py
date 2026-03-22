"""Button entities for Helios Energy Optimizer."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EnergyOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ForceOptimizationButton(coordinator, entry)])


class ForceOptimizationButton(ButtonEntity):
    """Button that triggers the daily optimization immediately."""

    _attr_translation_key = "force_optimization"
    _attr_has_entity_name = True

    def __init__(self, coordinator: EnergyOptimizerCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_force_optimization"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_press(self) -> None:
        """Run the daily optimization now."""
        _LOGGER.info("Helios: manual optimization triggered via button")
        from .daily_optimizer import async_run_daily_optimization
        await async_run_daily_optimization(self.hass, self._coordinator)
        await self._coordinator.async_request_refresh()
