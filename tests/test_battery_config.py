"""Tests for battery_config — battery list, totals, aggregated SOC, v1 → v2 migration."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios import async_migrate_entry
from custom_components.helios.battery_config import (
    battery_totals,
    get_batteries,
    migrate_v1_to_v2,
    read_battery_power,
    read_battery_soc,
    with_battery_totals,
)
from custom_components.helios.const import (
    DOMAIN,
    CONF_BATTERIES, CONF_BATTERY_ENABLED, CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_CHARGE_SCRIPT, CONF_BATTERY_AUTOCONSUM_SCRIPT,
    CONF_BATTERY_SOC_MIN, CONF_PV_POWER_ENTITY,
)

_V1 = {
    CONF_PV_POWER_ENTITY:               "sensor.pv",
    CONF_BATTERY_ENABLED:               True,
    CONF_BATTERY_SOC_ENTITY:            "sensor.bat_soc",
    CONF_BATTERY_POWER_ENTITY:          "sensor.bat_power",
    CONF_BATTERY_CHARGE_SCRIPT:         "script.charge",
    CONF_BATTERY_AUTOCONSUM_SCRIPT:     "script.autoconso",
    CONF_BATTERY_CAPACITY_KWH:          5.0,
    CONF_BATTERY_MAX_CHARGE_POWER_W:    1450,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W: 1450,
    CONF_BATTERY_SOC_MIN:               20,
}


class TestGetBatteries:

    def test_list(self):
        cfg = {CONF_BATTERIES: [{CONF_BATTERY_NAME: "A"}, {CONF_BATTERY_NAME: "B"}]}
        assert [b[CONF_BATTERY_NAME] for b in get_batteries(cfg)] == ["A", "B"]

    def test_v1_flat_config_is_one_battery(self):
        bats = get_batteries(_V1)
        assert len(bats) == 1
        assert bats[0][CONF_BATTERY_SOC_ENTITY] == "sensor.bat_soc"
        assert CONF_BATTERY_SOC_MIN not in bats[0]  # global key, not per battery

    def test_no_battery(self):
        assert get_batteries({CONF_PV_POWER_ENTITY: "sensor.pv"}) == []


class TestTotals:

    def test_sums(self):
        cfg = {CONF_BATTERIES: [
            {CONF_BATTERY_CAPACITY_KWH: 5, CONF_BATTERY_MAX_CHARGE_POWER_W: 1450, CONF_BATTERY_MAX_DISCHARGE_POWER_W: 1450},
            {CONF_BATTERY_CAPACITY_KWH: 2.5, CONF_BATTERY_MAX_CHARGE_POWER_W: 800, CONF_BATTERY_MAX_DISCHARGE_POWER_W: 600},
        ]}
        assert battery_totals(cfg) == {
            CONF_BATTERY_CAPACITY_KWH: 7.5,
            CONF_BATTERY_MAX_CHARGE_POWER_W: 2250,
            CONF_BATTERY_MAX_DISCHARGE_POWER_W: 2050,
        }

    def test_with_totals_flat_keys(self):
        cfg = with_battery_totals({CONF_BATTERIES: [
            {CONF_BATTERY_MAX_DISCHARGE_POWER_W: 1000}, {CONF_BATTERY_MAX_DISCHARGE_POWER_W: 500},
        ]})
        assert cfg[CONF_BATTERY_MAX_DISCHARGE_POWER_W] == 1500

    def test_with_totals_leaves_v1_untouched(self):
        assert with_battery_totals(_V1) is _V1


class TestReadSoc:

    async def test_weighted_by_capacity(self, hass):
        hass.states.async_set("sensor.a_soc", "80")
        hass.states.async_set("sensor.b_soc", "20")
        cfg = {CONF_BATTERIES: [
            {CONF_BATTERY_SOC_ENTITY: "sensor.a_soc", CONF_BATTERY_CAPACITY_KWH: 6},
            {CONF_BATTERY_SOC_ENTITY: "sensor.b_soc", CONF_BATTERY_CAPACITY_KWH: 2},
        ]}
        assert read_battery_soc(hass, cfg) == pytest.approx(65.0)

    async def test_unavailable_battery_ignored(self, hass):
        hass.states.async_set("sensor.a_soc", "80")
        hass.states.async_set("sensor.b_soc", "unavailable")
        hass.states.async_set("sensor.a_pow", "300")
        hass.states.async_set("sensor.b_pow", "0")
        cfg = {CONF_BATTERIES: [
            {CONF_BATTERY_SOC_ENTITY: "sensor.a_soc", CONF_BATTERY_POWER_ENTITY: "sensor.a_pow"},
            {CONF_BATTERY_SOC_ENTITY: "sensor.b_soc", CONF_BATTERY_POWER_ENTITY: "sensor.b_pow"},
        ]}
        assert read_battery_soc(hass, cfg) == pytest.approx(80.0)
        assert read_battery_power(hass, cfg) == pytest.approx(300.0)

    async def test_none_when_no_soc(self, hass):
        hass.states.async_set("sensor.a_soc", "unknown")
        assert read_battery_soc(hass, {CONF_BATTERIES: [{CONF_BATTERY_SOC_ENTITY: "sensor.a_soc"}]}) is None


class TestMigration:

    def test_flat_battery_becomes_list(self):
        data, options = migrate_v1_to_v2(dict(_V1), {})
        assert CONF_BATTERY_SOC_ENTITY not in data
        assert CONF_BATTERY_CHARGE_SCRIPT not in data
        assert CONF_BATTERY_AUTOCONSUM_SCRIPT not in data
        assert data[CONF_BATTERY_SOC_MIN] == 20          # global key kept
        assert data[CONF_BATTERY_ENABLED] is True
        [bat] = data[CONF_BATTERIES]
        assert bat[CONF_BATTERY_SOC_ENTITY] == "sensor.bat_soc"
        assert bat[CONF_BATTERY_MAX_CHARGE_POWER_W] == 1450
        assert CONF_BATTERY_CHARGE_SCRIPT not in bat     # scripts replaced by Drive / neutral

    def test_options_win_and_are_cleaned(self):
        options = {CONF_BATTERY_SOC_ENTITY: "sensor.new_soc", CONF_BATTERY_SOC_MIN: 30}
        data, new_options = migrate_v1_to_v2(dict(_V1), options)
        assert data[CONF_BATTERIES][0][CONF_BATTERY_SOC_ENTITY] == "sensor.new_soc"
        assert new_options == {CONF_BATTERY_SOC_MIN: 30}

    def test_no_battery(self):
        data, _ = migrate_v1_to_v2({CONF_PV_POWER_ENTITY: "sensor.pv", CONF_BATTERY_ENABLED: False}, {})
        assert data[CONF_BATTERIES] == []

    async def test_async_migrate_entry(self, hass):
        entry = MockConfigEntry(domain=DOMAIN, data=dict(_V1), options={}, version=1)
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry) is True
        assert entry.version == 2
        assert entry.data[CONF_BATTERIES][0][CONF_BATTERY_SOC_ENTITY] == "sensor.bat_soc"
        assert CONF_BATTERY_SOC_ENTITY not in entry.data

    async def test_future_version_refused(self, hass):
        entry = MockConfigEntry(domain=DOMAIN, data={}, version=3)
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry) is False
