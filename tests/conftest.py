"""Shared fixtures for Helios tests."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios.const import (
    DOMAIN,
    MODE_AUTO,
    CONF_PV_POWER_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_POWER_ENTITY,
    CONF_TEMPO_COLOR_ENTITY,
    CONF_BATTERY_ENABLED,
    CONF_DEVICES,
    CONF_MODE,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_DISPATCH_THRESHOLD,
    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_SOLAR,
    DEFAULT_DISPATCH_THRESHOLD,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_SOLAR,
)


# ---------------------------------------------------------------------------
# Allow custom integrations to be loaded in the test HA instance
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations during tests."""
    yield


# ---------------------------------------------------------------------------
# Minimal config entry — no battery, no devices
# ---------------------------------------------------------------------------
@pytest.fixture
def minimal_entry_data() -> dict:
    """Minimum viable config data: only the mandatory PV entity."""
    return {
        CONF_PV_POWER_ENTITY:       "sensor.pv_power",
        CONF_GRID_POWER_ENTITY:     "sensor.grid_power",
        CONF_HOUSE_POWER_ENTITY:    "sensor.house_power",
        CONF_TEMPO_COLOR_ENTITY:    "sensor.tempo_color",
        CONF_BATTERY_ENABLED:       False,
        CONF_DEVICES:               [],
        CONF_MODE:                  MODE_AUTO,
        CONF_SCAN_INTERVAL_MINUTES: 5,
        CONF_DISPATCH_THRESHOLD:    DEFAULT_DISPATCH_THRESHOLD,
        CONF_WEIGHT_PV_SURPLUS:     DEFAULT_WEIGHT_PV_SURPLUS,
        CONF_WEIGHT_TEMPO:          DEFAULT_WEIGHT_TEMPO,
        CONF_WEIGHT_BATTERY_SOC:    DEFAULT_WEIGHT_BATTERY_SOC,
        CONF_WEIGHT_SOLAR:       DEFAULT_WEIGHT_SOLAR,
    }


@pytest.fixture
def config_entry(minimal_entry_data) -> MockConfigEntry:
    """A MockConfigEntry ready to be added to hass."""
    return MockConfigEntry(
        domain=DOMAIN,
        data=minimal_entry_data,
        title="Helios Test",
        entry_id="test_helios_entry",
    )
