"""Energy Optimizer — custom integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import EnergyOptimizerCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

_CARD_URL = "/helios/helios-card.js"
_CARD_PATH = Path(__file__).parent / "www" / "helios-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the Helios Lovelace card as a static resource."""
    try:
        hass.http.register_static_path(_CARD_URL, str(_CARD_PATH), cache_headers=False)
        _LOGGER.debug("Helios card static path registered at %s", _CARD_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not register Helios card static path (expected in tests)")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Optimizer from a config entry."""
    coordinator = EnergyOptimizerCoordinator(hass, entry)
    await coordinator.device_manager.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await _async_register_lovelace_resource(hass)
    return True


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Add helios-card.js to Lovelace resources if not already present."""
    try:
        resources = hass.data["lovelace"]["resources"]
        await resources.async_load()

        for item in resources.async_items():
            if item.get("url") == _CARD_URL:
                _LOGGER.debug("Helios card already registered as Lovelace resource")
                return

        await resources.async_create_item({"res_type": "module", "url": _CARD_URL})
        _LOGGER.info("Helios card registered as Lovelace resource at %s", _CARD_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not auto-register Helios card as Lovelace resource "
            "(Lovelace not ready or running in tests)"
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — reload integration to apply changes."""
    await hass.config_entries.async_reload(entry.entry_id)
