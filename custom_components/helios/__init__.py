"""Energy Optimizer — custom integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify

from .const import DOMAIN, PLATFORMS
from .coordinator import EnergyOptimizerCoordinator

SERVICE_START_APPLIANCE = "start_appliance"
_SERVICE_SCHEMA_START_APPLIANCE = vol.Schema({
    vol.Required("device_entity"): cv.entity_id,
})

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

_CARD_URL = "/helios/helios-card.js"
_CARD_PATH = Path(__file__).parent / "www" / "helios-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the Helios Lovelace card JS module."""
    try:
        hass.http.register_static_path(_CARD_URL, str(_CARD_PATH), cache_headers=False)
        _LOGGER.debug("Helios card served at %s", _CARD_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not register Helios card static path (expected in tests)")
    return True


async def _async_do_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Inner: add Helios card to Lovelace resources storage if not already present."""
    try:
        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None:
            _LOGGER.warning("Helios: lovelace not available in hass.data — card resource not auto-registered")
            return
        # hass.data["lovelace"] is a LovelaceData object (not a dict)
        res_coll = getattr(lovelace_data, "resources", None)
        if res_coll is None:
            _LOGGER.warning("Helios: lovelace resources collection missing — card resource not auto-registered")
            return
        await res_coll.async_load()
        for item in res_coll.async_items():
            if item.get("url") == _CARD_URL:
                _LOGGER.debug("Helios: Lovelace resource already registered (%s)", _CARD_URL)
                return
        await res_coll.async_create_item({"res_type": "module", "url": _CARD_URL})
        _LOGGER.info("Helios: Lovelace resource registered (%s)", _CARD_URL)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Helios: could not auto-register Lovelace resource: %s", err)


def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Schedule Lovelace resource registration, waiting for HA to be fully started."""

    @callback
    def _on_ha_started(_event=None) -> None:
        hass.async_create_task(_async_do_register_lovelace_resource(hass))

    if hass.is_running:
        hass.async_create_task(_async_do_register_lovelace_resource(hass))
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)


def _load_base_load_fallback():
    """Return a fallback base_load_fn loaded from the bundled base_load.json (blocking — run in executor)."""
    import pathlib
    from .simulation.profiles import load_base_load_from_json
    path = pathlib.Path(__file__).parent / "simulation" / "config" / "base_load.json"
    try:
        return load_base_load_from_json(str(path))
    except Exception:  # noqa: BLE001
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Optimizer from a config entry."""
    _async_register_lovelace_resource(hass)
    coordinator = EnergyOptimizerCoordinator(hass, entry)
    await coordinator.device_manager.async_setup()
    await coordinator.async_setup()
    fallback_fn = await hass.async_add_executor_job(_load_base_load_fallback)
    await coordinator.consumption_learner.async_load(
        fallback_fn=fallback_fn
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)
    return True


def _async_register_services(hass: HomeAssistant) -> None:
    """Register Helios services (idempotent — skipped if already registered)."""
    if hass.services.has_service(DOMAIN, SERVICE_START_APPLIANCE):
        return

    async def _handle_start_appliance(call: ServiceCall) -> None:
        entity_id: str = call.data["device_entity"]
        # Derive the slug from the entity_id: sensor.helios_{slug}
        device_slug = entity_id.removeprefix("sensor.helios_")
        if device_slug == entity_id:
            _LOGGER.warning("start_appliance: entity_id '%s' is not a Helios device sensor", entity_id)
            return
        # Find the coordinator that owns this entry
        for coordinator in hass.data.get(DOMAIN, {}).values():
            started = await coordinator.device_manager.async_force_start_appliance(hass, device_slug)
            if started:
                await coordinator.async_request_refresh()
                return

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_APPLIANCE,
        _handle_start_appliance,
        schema=_SERVICE_SCHEMA_START_APPLIANCE,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: EnergyOptimizerCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_unload()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — reload integration to apply changes."""
    await hass.config_entries.async_reload(entry.entry_id)
