"""Sensor platform setup tests — regression guards for entity instantiation."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios.const import (
    DOMAIN,
    CONF_DEVICES,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W,
    CONF_DEVICE_PRIORITY,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_ALLOWED_START,
    CONF_DEVICE_ALLOWED_END,
    CONF_DEVICE_INTERRUPTIBLE,
    DEVICE_TYPE_APPLIANCE,
    DEVICE_TYPE_POOL,
    DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPE_EV,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_device(name: str, device_type: str = DEVICE_TYPE_APPLIANCE) -> dict:
    return {
        CONF_DEVICE_NAME:           name,
        CONF_DEVICE_TYPE:           device_type,
        CONF_DEVICE_SWITCH_ENTITY:  f"switch.{name.lower().replace(' ', '_')}",
        CONF_DEVICE_POWER_W:        1000,
        CONF_DEVICE_PRIORITY:       5,
        CONF_DEVICE_MIN_ON_MINUTES: 10,
        CONF_DEVICE_ALLOWED_START:  "06:00",
        CONF_DEVICE_ALLOWED_END:    "22:00",
        CONF_DEVICE_INTERRUPTIBLE:  True,
    }


async def _setup_with_devices(hass: HomeAssistant, minimal_entry_data: dict, devices: list) -> MockConfigEntry:
    data = {**minimal_entry_data, CONF_DEVICES: devices}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        title="Helios Sensor Test",
        entry_id="test_sensor_setup",
    )
    entry.add_to_hass(hass)

    hass.states.async_set("sensor.pv_power",    "1500")
    hass.states.async_set("sensor.grid_power",  "100")
    hass.states.async_set("sensor.house_power", "800")
    hass.states.async_set("sensor.tempo_color", "blue")

    with (
        patch.object(Store, "async_load", return_value=None),
        patch.object(Store, "async_save", return_value=None),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True, "async_setup_entry returned False"
    return entry


# ---------------------------------------------------------------------------
# Regression: DeviceStateSensor.__init__ raised AttributeError '_unique_suffix'
# because super().__init__() accessed self._unique_suffix before the subclass
# had a chance to set _attr_unique_id.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("device_type", [
    DEVICE_TYPE_APPLIANCE,
    DEVICE_TYPE_POOL,
    DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPE_EV,
])
async def test_device_state_sensor_instantiates(
    hass: HomeAssistant,
    minimal_entry_data: dict,
    device_type: str,
):
    """DeviceStateSensor must not raise AttributeError on instantiation.

    Regression: calling super().__init__() before _unique_suffix is defined
    caused 'DeviceStateSensor object has no attribute _unique_suffix'.
    """
    device = _minimal_device("Test Device", device_type)
    entry = await _setup_with_devices(hass, minimal_entry_data, [device])

    sensors = [e for e in hass.states.async_entity_ids() if e.startswith("sensor.")]
    device_sensors = [e for e in sensors if "test_device" in e]
    assert device_sensors, (
        f"No DeviceStateSensor registered for device_type={device_type!r}. "
        f"Known sensors: {sensors}"
    )


async def test_multiple_devices_all_registered(
    hass: HomeAssistant,
    minimal_entry_data: dict,
):
    """All configured devices must each produce a sensor entity."""
    devices = [
        _minimal_device("Chauffe Eau",  DEVICE_TYPE_WATER_HEATER),
        _minimal_device("Piscine",      DEVICE_TYPE_POOL),
        _minimal_device("Lave Vaisselle", DEVICE_TYPE_APPLIANCE),
    ]
    entry = await _setup_with_devices(hass, minimal_entry_data, devices)

    sensors = hass.states.async_entity_ids("sensor")
    for expected_fragment in ("chauffe_eau", "piscine", "lave_vaisselle"):
        assert any(expected_fragment in s for s in sensors), (
            f"Expected a sensor containing '{expected_fragment}', got: {sensors}"
        )


async def test_device_sensor_unique_id_scoped_to_entry(
    hass: HomeAssistant,
    minimal_entry_data: dict,
):
    """DeviceStateSensor unique_id must be scoped to the config entry_id."""
    device = _minimal_device("Mon Appareil")
    entry = await _setup_with_devices(hass, minimal_entry_data, [device])

    from homeassistant.helpers import entity_registry as er
    entity_registry = er.async_get(hass)
    device_entities = [
        e for e in entity_registry.entities.values()
        if e.config_entry_id == entry.entry_id and "mon_appareil" in (e.unique_id or "")
    ]
    assert device_entities, "DeviceStateSensor not found in entity registry"
    uid = device_entities[0].unique_id
    assert uid.startswith(entry.entry_id), (
        f"unique_id '{uid}' must start with entry_id '{entry.entry_id}'"
    )
