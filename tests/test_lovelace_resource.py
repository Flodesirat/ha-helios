"""Tests — auto-registration of the Helios card in Lovelace resources."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios import _CARD_URL_BASE, _versioned_card_url, _async_do_register_lovelace_resource

from .test_init import _setup


# ---------------------------------------------------------------------------
# Fake lovelace resources collection
# ---------------------------------------------------------------------------

class _FakeResources:
    """Minimal stand-in for HA's ResourceStorageCollection."""

    def __init__(self, pre_existing: list[dict] | None = None):
        self._items: list[dict] = list(pre_existing or [])
        self.created: list[dict] = []
        self.updated: list[dict] = []

    async def async_load(self) -> None:
        pass

    def async_items(self) -> list[dict]:
        return list(self._items)

    async def async_create_item(self, data: dict) -> None:
        self.created.append(data)
        self._items.append(data)

    async def async_update_item(self, item_id: str, data: dict) -> None:
        self.updated.append({"id": item_id, **data})
        for item in self._items:
            if item.get("id") == item_id:
                item.update(data)


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
    assert fake_resources.created[0]["url"] == _versioned_card_url()
    assert fake_resources.created[0]["res_type"] == "module"


async def test_resource_not_duplicated():
    """Card must not be registered again if already present with current version."""
    existing = [{"id": "abc", "url": _versioned_card_url(), "res_type": "module"}]
    fake_resources = _FakeResources(pre_existing=existing)
    hass = MagicMock()
    hass.data = {"lovelace": _make_lovelace_data(fake_resources)}

    await _async_do_register_lovelace_resource(hass)

    assert fake_resources.created == [], "Resource was duplicated even though it was already registered"
    assert fake_resources.updated == [], "Resource was updated even though it was already up-to-date"


async def test_resource_updated_on_version_change():
    """Outdated URL (old version) must be updated in place, not duplicated."""
    existing = [{"id": "abc", "url": f"{_CARD_URL_BASE}?v=0.0.1", "res_type": "module"}]
    fake_resources = _FakeResources(pre_existing=existing)
    hass = MagicMock()
    hass.data = {"lovelace": _make_lovelace_data(fake_resources)}

    await _async_do_register_lovelace_resource(hass)

    assert fake_resources.created == [], "Resource was created instead of updated"
    assert len(fake_resources.updated) == 1
    assert fake_resources.updated[0]["url"] == _versioned_card_url()


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
