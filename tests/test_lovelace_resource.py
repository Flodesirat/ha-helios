"""Tests — auto-registration of the Helios card in Lovelace resources."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios import _CARD_URL, _async_do_register_lovelace_resource

from .test_init import _setup


# ---------------------------------------------------------------------------
# Fake lovelace resources collection
# ---------------------------------------------------------------------------

class _FakeResources:
    """Minimal stand-in for HA's ResourceStorageCollection."""

    def __init__(self, pre_existing: list[dict] | None = None):
        self._items: list[dict] = list(pre_existing or [])
        self.created: list[dict] = []

    async def async_load(self) -> None:
        pass

    def async_items(self) -> list[dict]:
        return list(self._items)

    async def async_create_item(self, data: dict) -> None:
        self.created.append(data)
        self._items.append(data)


# ---------------------------------------------------------------------------
# Unit tests — _async_do_register_lovelace_resource in isolation
# ---------------------------------------------------------------------------

def _make_lovelace_data(resources=None):
    """Return a fake LovelaceData object (attribute-based, not a dict)."""
    obj = MagicMock()
    obj.resources = resources
    return obj


async def test_resource_registered_in_empty_collection():
    """Card must be added when the resources collection is empty."""
    fake_resources = _FakeResources()
    hass = MagicMock()
    hass.data = {"lovelace": _make_lovelace_data(fake_resources)}

    await _async_do_register_lovelace_resource(hass)

    assert len(fake_resources.created) == 1, (
        f"Expected 1 resource created, got {len(fake_resources.created)}"
    )
    assert fake_resources.created[0]["url"] == _CARD_URL
    assert fake_resources.created[0]["res_type"] == "module"


async def test_resource_not_duplicated():
    """Card must not be registered twice if already present."""
    existing = [{"url": _CARD_URL, "res_type": "module"}]
    fake_resources = _FakeResources(pre_existing=existing)
    hass = MagicMock()
    hass.data = {"lovelace": _make_lovelace_data(fake_resources)}

    await _async_do_register_lovelace_resource(hass)

    assert fake_resources.created == [], (
        "Resource was duplicated even though it was already registered"
    )


async def test_resource_graceful_without_lovelace_key():
    """Function must not raise when hass.data has no 'lovelace' key."""
    hass = MagicMock()
    hass.data = {}

    await _async_do_register_lovelace_resource(hass)  # must not raise


async def test_resource_graceful_without_resources_attribute():
    """Function must not raise when LovelaceData has no resources attribute."""
    hass = MagicMock()
    lovelace_obj = MagicMock(spec=[])  # no attributes at all
    hass.data = {"lovelace": lovelace_obj}

    await _async_do_register_lovelace_resource(hass)  # must not raise


# ---------------------------------------------------------------------------
# Integration test — verify _async_do_register_lovelace_resource is called
# ---------------------------------------------------------------------------

async def test_register_lovelace_called_on_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """_async_do_register_lovelace_resource must be scheduled during entry setup."""
    with patch(
        "custom_components.helios._async_do_register_lovelace_resource",
        new=AsyncMock(),
    ) as mock_fn:
        await _setup(hass, config_entry)
        await hass.async_block_till_done()

    mock_fn.assert_called_once_with(hass)
