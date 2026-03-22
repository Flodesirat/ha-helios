"""Integration load tests — verify Helios can be set up and torn down in HA."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios.const import DOMAIN, PLATFORMS, CONF_FORECAST_ENTITY


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    """Add entry to hass and set up the integration.

    - Seeds dummy sensor states so the coordinator doesn't log spurious warnings.
    - Patches Store to avoid filesystem access during tests.
    """
    entry.add_to_hass(hass)

    hass.states.async_set("sensor.pv_power",    "1500")
    hass.states.async_set("sensor.grid_power",  "100")
    hass.states.async_set("sensor.house_power", "800")
    hass.states.async_set("sensor.tempo_color", "blue")

    with (
        patch.object(Store, "async_load", return_value=None),
        patch.object(Store, "async_save", return_value=None),
    ):
        return await hass.config_entries.async_setup(entry.entry_id)


# ---------------------------------------------------------------------------
# Manifest sanity check (pure Python, no HA instance needed)
# ---------------------------------------------------------------------------

def test_manifest_is_valid():
    """manifest.json must exist and declare the expected domain."""
    manifest_path = (
        Path(__file__).parent.parent
        / "custom_components" / "helios" / "manifest.json"
    )
    assert manifest_path.exists(), "manifest.json not found"

    manifest = json.loads(manifest_path.read_text())

    assert manifest.get("domain") == DOMAIN, "Wrong domain in manifest"
    assert "version" in manifest,            "Missing 'version' in manifest"
    assert "name" in manifest,               "Missing 'name' in manifest"
    assert manifest.get("config_flow") is True, "'config_flow' must be true"


# ---------------------------------------------------------------------------
# Setup tests
# ---------------------------------------------------------------------------

async def test_setup_entry_succeeds(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """async_setup_entry must return True and register the coordinator."""
    result = await _setup(hass, config_entry)

    assert result is True, "async_setup_entry returned False"
    assert DOMAIN in hass.data, f"'{DOMAIN}' not found in hass.data"
    assert config_entry.entry_id in hass.data[DOMAIN], (
        "Coordinator not stored under entry_id"
    )


async def test_platforms_loaded(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """All declared platforms must register at least one entity after setup."""
    await _setup(hass, config_entry)

    entity_ids = hass.states.async_entity_ids()
    sensors  = [e for e in entity_ids if e.startswith("sensor.")]
    switches = [e for e in entity_ids if e.startswith("switch.")]

    assert sensors,  "No sensor entities registered after setup"
    assert switches, "No switch entities registered after setup"


async def test_coordinator_initial_values(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """Coordinator must expose valid numeric attributes after first refresh."""
    await _setup(hass, config_entry)

    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    assert isinstance(coordinator.pv_power_w,   float)
    assert isinstance(coordinator.surplus_w,    float)
    assert isinstance(coordinator.global_score, float)
    assert 0.0 <= coordinator.global_score <= 1.0, (
        f"global_score out of [0,1]: {coordinator.global_score}"
    )


# ---------------------------------------------------------------------------
# Unload tests
# ---------------------------------------------------------------------------

async def test_unload_entry_succeeds(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """async_unload_entry must return True and remove the coordinator."""
    await _setup(hass, config_entry)

    result = await hass.config_entries.async_unload(config_entry.entry_id)

    assert result is True, "async_unload_entry returned False"
    assert config_entry.entry_id not in hass.data.get(DOMAIN, {}), (
        "Coordinator still present in hass.data after unload"
    )


# ---------------------------------------------------------------------------
# Forecast entity — options-flow override
# ---------------------------------------------------------------------------

async def test_forecast_kwh_read_from_options(
    hass: HomeAssistant,
    minimal_entry_data: dict,
):
    """forecast_kwh must be populated when CONF_FORECAST_ENTITY lives in entry.options.

    Regression test: the coordinator was reading only entry.data, so a forecast
    entity configured (or reconfigured) via the options flow was silently ignored,
    causing forecast_kwh = None in diagnostics.
    """
    hass.states.async_set("sensor.forecast_pv", "8.5")

    # Simulate: initial setup without forecast, then options-flow adds it
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=minimal_entry_data,          # no forecast entity in data
        options={CONF_FORECAST_ENTITY: "sensor.forecast_pv"},  # set via options flow
        title="Helios Forecast Test",
        entry_id="test_forecast_options",
    )
    await _setup(hass, entry)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.forecast_kwh == pytest.approx(8.5), (
        f"forecast_kwh should be 8.5 (from options), got {coordinator.forecast_kwh}"
    )


async def test_forecast_kwh_read_from_data(
    hass: HomeAssistant,
    minimal_entry_data: dict,
):
    """forecast_kwh must be populated when CONF_FORECAST_ENTITY lives in entry.data."""
    hass.states.async_set("sensor.forecast_pv", "5.2")

    data = {**minimal_entry_data, CONF_FORECAST_ENTITY: "sensor.forecast_pv"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        title="Helios Forecast Data Test",
        entry_id="test_forecast_data",
    )
    await _setup(hass, entry)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.forecast_kwh == pytest.approx(5.2), (
        f"forecast_kwh should be 5.2 (from data), got {coordinator.forecast_kwh}"
    )


async def test_forecast_kwh_none_when_not_configured(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """forecast_kwh must be None when no forecast entity is configured at all."""
    await _setup(hass, config_entry)

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    assert coordinator.forecast_kwh is None, (
        f"forecast_kwh should be None when not configured, got {coordinator.forecast_kwh}"
    )


async def test_double_unload_is_safe(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
):
    """A second unload must not raise an exception."""
    await _setup(hass, config_entry)
    await hass.config_entries.async_unload(config_entry.entry_id)

    try:
        await hass.config_entries.async_unload(config_entry.entry_id)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Second unload raised: {exc}")
