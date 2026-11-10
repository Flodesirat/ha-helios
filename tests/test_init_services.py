"""Integration setup — card static path, Lovelace scheduling, services, reload."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.helios as helios
from custom_components.helios.const import DOMAIN

from .test_init import _setup


# ---------------------------------------------------------------------------
# Card static path / Lovelace resource
# ---------------------------------------------------------------------------

async def test_async_setup_survives_static_path_error(caplog):
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock(side_effect=RuntimeError("no http"))
    assert await helios.async_setup(hass, {}) is True
    assert "Could not register Helios card static path" in caplog.text


async def test_lovelace_registration_error_is_logged(caplog):
    resources = MagicMock()
    resources.async_load = AsyncMock(side_effect=RuntimeError("storage broken"))
    hass = MagicMock()
    hass.data = {"lovelace": MagicMock(resources=resources)}
    await helios._async_do_register_lovelace_resource(hass)
    assert "could not auto-register Lovelace resource" in caplog.text


async def test_lovelace_registration_waits_for_ha_started(hass: HomeAssistant):
    """Before HA is running, registration is deferred to EVENT_HOMEASSISTANT_STARTED."""
    with (
        patch.object(type(hass), "is_running", new=False),
        patch.object(helios, "_async_do_register_lovelace_resource", new=AsyncMock()) as do_register,
    ):
        helios._async_register_lovelace_resource(hass)
        await hass.async_block_till_done()
        do_register.assert_not_called()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
    do_register.assert_awaited_once_with(hass)


def test_base_load_fallback_returns_none_on_error():
    with patch(
        "custom_components.helios.simulation.profiles.load_base_load_from_json",
        side_effect=OSError("missing"),
    ):
        assert helios._load_base_load_fallback() is None


def test_base_load_fallback_loads_bundled_profile():
    fn = helios._load_base_load_fallback()
    assert callable(fn)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

async def _two_entries(hass: HomeAssistant, minimal_entry_data: dict):
    entries = [
        MockConfigEntry(domain=DOMAIN, data=minimal_entry_data, entry_id=eid)
        for eid in ("entry_a", "entry_b")
    ]
    for entry in entries:
        assert await _setup(hass, entry)
    return [hass.data[DOMAIN][e.entry_id] for e in entries]


async def test_set_savings_targets_one_entry(hass: HomeAssistant, minimal_entry_data: dict):
    coord_a, coord_b = await _two_entries(hass, minimal_entry_data)
    with (
        patch.object(coord_a, "async_set_savings", new=AsyncMock()) as set_a,
        patch.object(coord_b, "async_set_savings", new=AsyncMock()) as set_b,
    ):
        await hass.services.async_call(
            DOMAIN, "set_savings", {"daily": 1.5, "config_entry_id": "entry_b"}, blocking=True
        )
    set_a.assert_not_called()
    set_b.assert_awaited_once_with(daily=1.5, monthly=None, total=None)


async def test_set_savings_without_entry_id_updates_all(hass: HomeAssistant, minimal_entry_data: dict):
    coord_a, coord_b = await _two_entries(hass, minimal_entry_data)
    with (
        patch.object(coord_a, "async_set_savings", new=AsyncMock()) as set_a,
        patch.object(coord_b, "async_set_savings", new=AsyncMock()) as set_b,
    ):
        await hass.services.async_call(DOMAIN, "set_savings", {"total": 120}, blocking=True)
    set_a.assert_awaited_once_with(daily=None, monthly=None, total=120.0)
    set_b.assert_awaited_once_with(daily=None, monthly=None, total=120.0)


async def test_start_appliance_rejects_non_helios_entity(
    hass: HomeAssistant, config_entry: MockConfigEntry, caplog
):
    await _setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    with patch.object(
        coordinator.device_manager, "async_force_start_appliance", new=AsyncMock()
    ) as force:
        await hass.services.async_call(
            DOMAIN, "start_appliance", {"device_entity": "sensor.dishwasher"}, blocking=True
        )
    force.assert_not_called()
    assert "is not a Helios device sensor" in caplog.text


@pytest.mark.parametrize("started", [True, False])
async def test_start_appliance_refreshes_owner_only_when_started(
    hass: HomeAssistant, config_entry: MockConfigEntry, started: bool
):
    await _setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    with (
        patch.object(
            coordinator.device_manager, "async_force_start_appliance",
            new=AsyncMock(return_value=started),
        ) as force,
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh,
    ):
        await hass.services.async_call(
            DOMAIN, "start_appliance", {"device_entity": "sensor.helios_lave_vaisselle"}, blocking=True
        )
    force.assert_awaited_once_with(hass, "lave_vaisselle")
    assert refresh.await_count == (1 if started else 0)


# ---------------------------------------------------------------------------
# Reload on options update
# ---------------------------------------------------------------------------

async def test_options_update_reloads_entry(hass: HomeAssistant, config_entry: MockConfigEntry):
    await _setup(hass, config_entry)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        hass.config_entries.async_update_entry(config_entry, options={"peak_pv_w": 5000})
        await hass.async_block_till_done()
    reload.assert_awaited_once_with(config_entry.entry_id)

