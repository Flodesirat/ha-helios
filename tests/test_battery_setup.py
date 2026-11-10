"""End-to-end: Helios set up in HA with batteries driven in Drive mode."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.helios.const import (
    DOMAIN,
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_TEMPO_COLOR_ENTITY, CONF_DEVICES, CONF_SCAN_INTERVAL_MINUTES,
    CONF_BATTERIES, CONF_BATTERY_ENABLED, CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_POWER_ENTITY, CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_DRIVE_SCRIPT, CONF_BATTERY_NEUTRAL_SCRIPT, CONF_BATTERY_CHARGE_SCRIPT,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
)

_SOURCES = {
    CONF_PV_POWER_ENTITY:       "sensor.pv_power",
    CONF_GRID_POWER_ENTITY:     "sensor.grid_power",
    CONF_HOUSE_POWER_ENTITY:    "sensor.house_power",
    CONF_TEMPO_COLOR_ENTITY:    "sensor.tempo_color",
    CONF_DEVICES:               [],
    CONF_SCAN_INTERVAL_MINUTES: 5,
    CONF_BATTERY_ENABLED:       True,
}
_VENUS = {
    CONF_BATTERY_NAME:                  "Venus A",
    CONF_BATTERY_SOC_ENTITY:            "sensor.venus_soc",
    CONF_BATTERY_POWER_ENTITY:          "sensor.venus_power",
    CONF_BATTERY_DRIVE_POWER_ENTITY:    "number.venus_drive",
    CONF_BATTERY_DRIVE_SCRIPT:          "script.venus_drive",
    CONF_BATTERY_NEUTRAL_SCRIPT:        "script.venus_neutral",
    CONF_BATTERY_MAX_CHARGE_POWER_W:    1450,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W: 1450,
}


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.pv_power",    "1500")
    hass.states.async_set("sensor.grid_power",  "100")
    hass.states.async_set("sensor.house_power", "800")
    hass.states.async_set("sensor.tempo_color", "blue")
    hass.states.async_set("sensor.venus_soc",   "60")
    hass.states.async_set("sensor.venus_power", "0")
    with (
        patch.object(Store, "async_load", return_value=None),
        patch.object(Store, "async_save", return_value=None),
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return ok


async def test_v1_entry_migrated_on_setup(hass: HomeAssistant):
    """Une entry v1 (batterie à plat, scripts de charge) est migrée au chargement."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, entry_id="helios_v1",
        data={**_SOURCES,
              CONF_BATTERY_SOC_ENTITY: "sensor.venus_soc",
              CONF_BATTERY_CHARGE_SCRIPT: "script.old_charge",
              CONF_BATTERY_MAX_DISCHARGE_POWER_W: 1450},
    )
    assert await _setup(hass, entry)
    assert entry.version == 2
    assert entry.data[CONF_BATTERIES] == [{
        CONF_BATTERY_NAME: "Batterie",
        CONF_BATTERY_SOC_ENTITY: "sensor.venus_soc",
        CONF_BATTERY_MAX_DISCHARGE_POWER_W: 1450,
    }]
    assert CONF_BATTERY_CHARGE_SCRIPT not in entry.data
    # No Drive setpoint entity yet: nothing to drive, the switch still exists
    assert hass.states.get("sensor.helios_battery").state == "off"
    assert hass.states.get("switch.helios_battery_driving").state == "on"


async def test_drive_cycle(hass: HomeAssistant):
    """Démarrage en Drive, consigne écrite au relevé, arrêt via le switch."""
    set_value = async_mock_service(hass, "number", "set_value")
    drive = async_mock_service(hass, "script", "venus_drive")
    neutral = async_mock_service(hass, "script", "venus_neutral")
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, entry_id="helios_v2",
        data={**_SOURCES, CONF_BATTERIES: [_VENUS]},
    )
    assert await _setup(hass, entry)
    assert len(drive) == 1
    assert hass.states.get("sensor.helios_battery").state == "startup_wait"

    hass.states.async_set("sensor.grid_power", "530")
    await hass.async_block_till_done()
    assert [c.data["value"] for c in set_value] == [500]
    battery = hass.states.get("sensor.helios_battery")
    assert battery.attributes["setpoint_w"] == 500
    assert battery.attributes["batteries"][0]["written_w"] == 500

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.helios_battery_driving"}, blocking=True,
    )
    await hass.async_block_till_done()
    assert len(neutral) == 1
    assert hass.states.get("sensor.helios_battery").state == "off"
    assert hass.states.get("switch.helios_battery_driving").state == "off"

    hass.states.async_set("sensor.grid_power", "900")
    await hass.async_block_till_done()
    assert len(set_value) == 1  # not driven any more

    await hass.config_entries.async_unload(entry.entry_id)


async def test_helios_off_releases_battery(hass: HomeAssistant):
    async_mock_service(hass, "number", "set_value")
    async_mock_service(hass, "script", "venus_drive")
    neutral = async_mock_service(hass, "script", "venus_neutral")
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, entry_id="helios_off",
        data={**_SOURCES, CONF_BATTERIES: [_VENUS]},
    )
    assert await _setup(hass, entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.helios_auto_mode"}, blocking=True,
    )
    await hass.async_block_till_done()
    assert len(neutral) == 1
    assert hass.states.get("sensor.helios_battery").state == "off"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_old_manual_switch_removed(hass: HomeAssistant):
    """switch.helios_battery_manual (avant 1.1) est retiré du registre."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, entry_id="helios_reg",
        data={**_SOURCES, CONF_BATTERIES: [_VENUS]},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch", DOMAIN, "helios_reg_battery_manual",
        config_entry=entry, suggested_object_id="helios_battery_manual",
    )
    async_mock_service(hass, "script", "venus_drive")
    async_mock_service(hass, "number", "set_value")
    for entity_id, value in (
        ("sensor.pv_power", "1500"), ("sensor.grid_power", "100"),
        ("sensor.house_power", "800"), ("sensor.tempo_color", "blue"),
        ("sensor.venus_soc", "60"),
    ):
        hass.states.async_set(entity_id, value)
    with (
        patch.object(Store, "async_load", return_value=None),
        patch.object(Store, "async_save", return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert registry.async_get_entity_id("switch", DOMAIN, "helios_reg_battery_manual") is None
    assert hass.states.get("switch.helios_battery_driving") is not None
    await hass.config_entries.async_unload(entry.entry_id)
