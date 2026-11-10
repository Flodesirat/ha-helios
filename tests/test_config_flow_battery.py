"""Config flow and options flow — battery steps (global settings + battery list)."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios.const import (
    DOMAIN,
    CONF_PV_POWER_ENTITY,
    CONF_BATTERIES, CONF_BATTERY_ENABLED, CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_DRIVE_SCRIPT, CONF_BATTERY_NEUTRAL_SCRIPT,
    CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_GRID_TARGET_RED_W,
)

_A = {
    CONF_BATTERY_NAME:               "Venus A",
    CONF_BATTERY_SOC_ENTITY:         "sensor.a_soc",
    CONF_BATTERY_DRIVE_POWER_ENTITY: "number.a_drive",
    CONF_BATTERY_DRIVE_SCRIPT:       "script.a_drive",
    CONF_BATTERY_NEUTRAL_SCRIPT:     "script.a_neutral",
    CONF_BATTERY_MAX_CHARGE_POWER_W: 1450,
}
_B = {**_A, CONF_BATTERY_NAME: "Venus B", CONF_BATTERY_SOC_ENTITY: "sensor.b_soc",
      CONF_BATTERY_DRIVE_POWER_ENTITY: "number.b_drive"}


def _no_setup():
    return patch("custom_components.helios.async_setup_entry", return_value=True)


async def test_config_flow_two_batteries(hass: HomeAssistant):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PV_POWER_ENTITY: "sensor.pv"}
    )
    assert flow["step_id"] == "battery"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_BATTERY_ENABLED: True, CONF_BATTERY_GRID_TARGET_RED_W: 50}
    )
    assert flow["step_id"] == "battery_unit"
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {**_A, "add_another": True})
    assert flow["step_id"] == "battery_unit"
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {**_B, "add_another": False})
    assert flow["step_id"] == "devices"
    # The strategy form cannot be rendered by HA 2025.1 (its price selectors use
    # step=0.0001, below the 0.001 minimum): check the data gathered so far.
    data = hass.config_entries.flow._progress[flow["flow_id"]]._data
    assert data[CONF_BATTERY_GRID_TARGET_RED_W] == 50
    assert [b[CONF_BATTERY_NAME] for b in data[CONF_BATTERIES]] == ["Venus A", "Venus B"]
    assert "add_another" not in data[CONF_BATTERIES][0]
    hass.config_entries.flow.async_abort(flow["flow_id"])


async def test_config_flow_version_is_2():
    from custom_components.helios.config_flow import EnergyOptimizerConfigFlow
    assert EnergyOptimizerConfigFlow.VERSION == 2


async def test_config_flow_no_battery(hass: HomeAssistant):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PV_POWER_ENTITY: "sensor.pv"}
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_BATTERY_ENABLED: False})
    assert flow["step_id"] == "devices"


def _entry(hass: HomeAssistant, batteries: list[dict]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={CONF_PV_POWER_ENTITY: "sensor.pv", CONF_BATTERY_ENABLED: True, CONF_BATTERIES: batteries},
    )
    entry.add_to_hass(hass)
    return entry


async def _open_battery_list(hass: HomeAssistant, entry: MockConfigEntry, **global_input):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"section": "battery"})
    assert flow["step_id"] == "battery"
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {CONF_BATTERY_ENABLED: True, **global_input}
    )
    assert flow["step_id"] == "batteries_select"
    return flow


async def test_options_edit_battery_prefilled(hass: HomeAssistant):
    entry = _entry(hass, [_A])
    flow = await _open_battery_list(hass, entry)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"choice": "0"})
    assert flow["step_id"] == "opt_battery_action"
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"action": "edit"})
    assert flow["step_id"] == "opt_battery_unit"
    # The form is pre-filled with the current values
    defaults = {str(k): k.default() for k in flow["data_schema"].schema if callable(getattr(k, "default", None))}
    assert defaults[CONF_BATTERY_DRIVE_POWER_ENTITY] == "number.a_drive"
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**_A, CONF_BATTERY_MAX_CHARGE_POWER_W: 1000}
    )
    with _no_setup():
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"choice": "__done__"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERIES][0][CONF_BATTERY_MAX_CHARGE_POWER_W] == 1000


async def test_options_add_and_delete_battery(hass: HomeAssistant):
    entry = _entry(hass, [_A])
    flow = await _open_battery_list(hass, entry, **{CONF_BATTERY_GRID_TARGET_RED_W: 60})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"choice": "__add__"})
    assert flow["step_id"] == "opt_battery_unit"
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], _B)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"choice": "0"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"action": "delete"})
    assert flow["step_id"] == "batteries_select"
    with _no_setup():
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"choice": "__done__"})
    assert [b[CONF_BATTERY_NAME] for b in result["data"][CONF_BATTERIES]] == ["Venus B"]
    assert result["data"][CONF_BATTERY_GRID_TARGET_RED_W] == 60
