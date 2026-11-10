"""Config flow and options flow — device steps, sources, strategy validation."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios.const import (
    DOMAIN,
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_PEAK_PV_W,
    CONF_HOUSE_POWER_ENTITY, CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY, CONF_FORECAST_ENTITY,
    CONF_BATTERIES, CONF_BATTERY_ENABLED,
    CONF_DEVICES, CONF_DEVICE_TYPE, CONF_DEVICE_NAME, CONF_DEVICE_POWER_W,
    CONF_DEVICE_SWITCH_ENTITY, CONF_DEVICE_PRIORITY,
    CONF_DEVICE_WEIGHT_PRIORITY, CONF_DEVICE_WEIGHT_FIT, CONF_DEVICE_WEIGHT_URGENCY,
    CONF_SCAN_INTERVAL_MINUTES, CONF_SAMPLE_INTERVAL_SECONDS,
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET,
    CONF_HVAC_TEMP_ENTITY, CONF_HVAC_MODE,
    CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    CONF_POOL_WATER_TEMP_ENTITY, CONF_POOL_LOCAL_TEMP_ENTITY,
    CONF_POOL_FILTRATION_TABLE, CONF_POOL_FILTRATION_ENTITY,
    CONF_POOL_FROST_TEMP, CONF_POOL_FROST_CONTINUOUS_TEMP,
    DEVICE_TYPE_GENERIC, DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPE_HVAC, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
    HVAC_MODE_COOL,
)

_POOL_INPUT = {
    CONF_POOL_WATER_TEMP_ENTITY: "sensor.pool_water",
    CONF_POOL_LOCAL_TEMP_ENTITY: "sensor.pool_local",
    CONF_POOL_FILTRATION_TABLE:  "10:2, 20:8, 28:16",
}

# Type-specific input for each device type (None = no type-specific step)
_TYPE_INPUT = {
    DEVICE_TYPE_GENERIC:      None,
    DEVICE_TYPE_EV:           {CONF_EV_SOC_ENTITY: "sensor.ev_soc", CONF_EV_SOC_TARGET: 90},
    DEVICE_TYPE_WATER_HEATER: {CONF_WH_TEMP_ENTITY: "sensor.wh_temp", CONF_WH_TEMP_TARGET: 60},
    DEVICE_TYPE_HVAC:         {CONF_HVAC_TEMP_ENTITY: "sensor.room", CONF_HVAC_MODE: HVAC_MODE_COOL},
    DEVICE_TYPE_POOL:         _POOL_INPUT,
    DEVICE_TYPE_APPLIANCE:    {CONF_APPLIANCE_READY_ENTITY: "input_boolean.dw_ready",
                               CONF_APPLIANCE_CYCLE_DURATION_MINUTES: 90},
}

_TYPE_STEP = {
    DEVICE_TYPE_EV:           "device_ev",
    DEVICE_TYPE_WATER_HEATER: "device_water_heater",
    DEVICE_TYPE_HVAC:         "device_hvac",
    DEVICE_TYPE_POOL:         "device_pool",
    DEVICE_TYPE_APPLIANCE:    "device_appliance",
}

_BAD_WEIGHTS = {
    CONF_DEVICE_WEIGHT_PRIORITY: 0.5,
    CONF_DEVICE_WEIGHT_FIT:      0.5,
    CONF_DEVICE_WEIGHT_URGENCY:  0.5,
}


def _no_setup():
    return patch("custom_components.helios.async_setup_entry", return_value=True)


def _schema_keys(flow) -> set[str]:
    return {str(k) for k in flow["data_schema"].schema}


def _defaults(flow) -> dict:
    return {
        str(k): k.default()
        for k in flow["data_schema"].schema
        if callable(getattr(k, "default", None))
    }


def _common(name: str, device_type: str) -> dict:
    common = {CONF_DEVICE_NAME: name, CONF_DEVICE_POWER_W: 1000}
    if device_type != DEVICE_TYPE_APPLIANCE:
        common[CONF_DEVICE_SWITCH_ENTITY] = f"switch.{name}"
    return common


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

async def _flow_at_devices(hass: HomeAssistant):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PV_POWER_ENTITY: "sensor.pv"}
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_BATTERY_ENABLED: False})
    assert flow["step_id"] == "devices"
    return flow


async def _cf(hass: HomeAssistant, flow, user_input):
    return await hass.config_entries.flow.async_configure(flow["flow_id"], user_input)


async def test_user_step_shows_form(hass: HomeAssistant):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert flow["type"] is FlowResultType.FORM
    assert flow["step_id"] == "user"


async def test_battery_unit_form_proposes_numbered_name(hass: HomeAssistant):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await _cf(hass, flow, {CONF_PV_POWER_ENTITY: "sensor.pv"})
    flow = await _cf(hass, flow, {CONF_BATTERY_ENABLED: True})
    assert flow["step_id"] == "battery_unit"
    assert "add_another" in _schema_keys(flow)
    assert _defaults(flow)["battery_name"].endswith(" 1")


async def test_config_flow_all_device_types(hass: HomeAssistant):
    """One device of each type goes through its own step, then the common step."""
    flow = await _flow_at_devices(hass)
    # No device yet → "add another device" ticked by default
    assert _defaults(flow)["add_device"] is True

    for device_type, type_input in _TYPE_INPUT.items():
        flow = await _cf(hass, flow, {"add_device": True})
        assert flow["step_id"] == "device_type"
        flow = await _cf(hass, flow, {CONF_DEVICE_TYPE: device_type})
        if type_input is not None:
            assert flow["step_id"] == _TYPE_STEP[device_type]
            flow = await _cf(hass, flow, type_input)
        assert flow["step_id"] == "device_common"
        # Appliances are started by script, they have no switch
        assert (CONF_DEVICE_SWITCH_ENTITY in _schema_keys(flow)) is (device_type != DEVICE_TYPE_APPLIANCE)
        flow = await _cf(hass, flow, _common(device_type, device_type))
        assert flow["step_id"] == "devices"
        # Devices already present → unticked by default
        assert _defaults(flow)["add_device"] is False

    flow = await _cf(hass, flow, {"add_device": False})
    assert flow["step_id"] == "strategy"
    with _no_setup():
        result = await _cf(hass, flow, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = result["data"][CONF_DEVICES]
    assert [d[CONF_DEVICE_TYPE] for d in devices] == list(_TYPE_INPUT)
    by_type = {d[CONF_DEVICE_TYPE]: d for d in devices}
    assert by_type[DEVICE_TYPE_EV][CONF_EV_SOC_TARGET] == 90
    assert by_type[DEVICE_TYPE_HVAC][CONF_HVAC_MODE] == HVAC_MODE_COOL
    assert by_type[DEVICE_TYPE_POOL][CONF_POOL_FILTRATION_TABLE] == "10:2, 20:8, 28:16"
    assert by_type[DEVICE_TYPE_WATER_HEATER][CONF_DEVICE_NAME] == DEVICE_TYPE_WATER_HEATER


async def test_config_flow_pool_validation_errors(hass: HomeAssistant):
    flow = await _flow_at_devices(hass)
    flow = await _cf(hass, flow, {"add_device": True})
    flow = await _cf(hass, flow, {CONF_DEVICE_TYPE: DEVICE_TYPE_POOL})
    flow = await _cf(hass, flow, {
        **_POOL_INPUT,
        CONF_POOL_FILTRATION_TABLE: "not a table",
        CONF_POOL_FROST_TEMP: 0,
        CONF_POOL_FROST_CONTINUOUS_TEMP: 2,
    })
    assert flow["step_id"] == "device_pool"
    assert flow["errors"] == {
        CONF_POOL_FILTRATION_TABLE: "invalid_filtration_table",
        CONF_POOL_FROST_CONTINUOUS_TEMP: "frost_continuous_above_sessions",
    }
    # The form keeps what was typed
    assert _defaults(flow)[CONF_POOL_FILTRATION_TABLE] == "not a table"


async def test_config_flow_device_weights_must_sum_to_one(hass: HomeAssistant):
    flow = await _flow_at_devices(hass)
    flow = await _cf(hass, flow, {"add_device": True})
    flow = await _cf(hass, flow, {CONF_DEVICE_TYPE: DEVICE_TYPE_GENERIC})
    flow = await _cf(hass, flow, {**_common("plug", DEVICE_TYPE_GENERIC), **_BAD_WEIGHTS})
    assert flow["step_id"] == "device_common"
    assert flow["errors"] == {"base": "device_weights_must_sum_to_one"}


async def test_config_flow_sample_interval_above_scan_interval(hass: HomeAssistant):
    """The sample interval cannot exceed the scan interval (the 10 s floor is the selector's)."""
    flow = await _flow_at_devices(hass)
    flow = await _cf(hass, flow, {"add_device": False})
    flow = await _cf(hass, flow, {CONF_SCAN_INTERVAL_MINUTES: 1, CONF_SAMPLE_INTERVAL_SECONDS: 90})
    assert flow["step_id"] == "strategy"
    assert flow["errors"] == {CONF_SAMPLE_INTERVAL_SECONDS: "sample_interval_out_of_range"}


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

_EXISTING_DEVICES = [
    {CONF_DEVICE_TYPE: DEVICE_TYPE_WATER_HEATER, CONF_DEVICE_NAME: "Ballon",
     CONF_DEVICE_SWITCH_ENTITY: "switch.ballon", CONF_DEVICE_POWER_W: 2000,
     CONF_WH_TEMP_ENTITY: "sensor.wh_temp", CONF_WH_TEMP_TARGET: 58},
    {CONF_DEVICE_TYPE: DEVICE_TYPE_POOL, CONF_DEVICE_NAME: "Piscine",
     CONF_DEVICE_SWITCH_ENTITY: "switch.pump", CONF_DEVICE_POWER_W: 800,
     CONF_POOL_FILTRATION_ENTITY: "sensor.old_quota", "pool_split_sessions": True,
     **_POOL_INPUT},
    {CONF_DEVICE_TYPE: DEVICE_TYPE_GENERIC, CONF_DEVICE_POWER_W: 300},
]


def _entry(hass: HomeAssistant, devices: list[dict] | None = None, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={
            CONF_PV_POWER_ENTITY: "sensor.pv",
            CONF_BATTERY_ENABLED: False, CONF_BATTERIES: [],
            CONF_DEVICES: [dict(d) for d in (devices or [])],
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


async def _of(hass: HomeAssistant, flow, user_input):
    return await hass.config_entries.options.async_configure(flow["flow_id"], user_input)


async def _open(hass: HomeAssistant, entry: MockConfigEntry, section: str):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["step_id"] == "init"
    return await _of(hass, flow, {"section": section})


def _suggested(flow) -> dict:
    return {
        str(k): k.description["suggested_value"]
        for k in flow["data_schema"].schema
        if (k.description or {}).get("suggested_value") is not None
    }


async def test_options_sources_prefilled_and_merged(hass: HomeAssistant):
    entry = _entry(hass, options={
        CONF_PEAK_PV_W: 6000,
        CONF_HOUSE_POWER_ENTITY: "sensor.house",
    })
    flow = await _open(hass, entry, "sources")
    assert flow["step_id"] == "sources"
    defaults = _defaults(flow)
    assert defaults[CONF_PV_POWER_ENTITY] == "sensor.pv"   # from data
    assert defaults[CONF_PEAK_PV_W] == 6000                # options win over data
    # Optional entities are suggested, never forced as defaults
    assert _suggested(flow) == {CONF_HOUSE_POWER_ENTITY: "sensor.house"}
    assert CONF_TEMPO_COLOR_ENTITY not in defaults
    with _no_setup():
        result = await _of(hass, flow, {
            CONF_PV_POWER_ENTITY: "sensor.pv2",
            CONF_GRID_POWER_ENTITY: "sensor.grid",
            CONF_HOUSE_POWER_ENTITY: "sensor.house",
        })
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PV_POWER_ENTITY] == "sensor.pv2"
    assert result["data"][CONF_GRID_POWER_ENTITY] == "sensor.grid"
    assert result["data"][CONF_HOUSE_POWER_ENTITY] == "sensor.house"
    assert result["data"][CONF_PEAK_PV_W] == 6000          # untouched options are kept


async def test_options_sources_saves_with_unset_optional_entities(hass: HomeAssistant):
    """Regression #10: unset optional entities used to default to "" and fail validation."""
    entry = _entry(hass)
    flow = await _open(hass, entry, "sources")
    with _no_setup():
        result = await _of(hass, flow, {CONF_PV_POWER_ENTITY: "sensor.pv"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TEMPO_NEXT_COLOR_ENTITY] is None


async def test_options_sources_empty_field_removes_source(hass: HomeAssistant):
    """Clearing a field removes the source, even when it came from the initial config."""
    entry = _entry(hass, options={CONF_FORECAST_ENTITY: "sensor.forecast"})
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TEMPO_COLOR_ENTITY: "sensor.tempo"}
    )
    flow = await _open(hass, entry, "sources")
    assert _suggested(flow) == {
        CONF_TEMPO_COLOR_ENTITY: "sensor.tempo",
        CONF_FORECAST_ENTITY: "sensor.forecast",
    }
    with _no_setup():
        result = await _of(hass, flow, {CONF_PV_POWER_ENTITY: "sensor.pv"})
    assert result["data"][CONF_TEMPO_COLOR_ENTITY] is None
    assert result["data"][CONF_FORECAST_ENTITY] is None
    # options take priority over data: the source is no longer read
    merged = {**entry.data, **entry.options}
    assert merged[CONF_TEMPO_COLOR_ENTITY] is None


async def test_options_battery_disabled_saves_directly(hass: HomeAssistant):
    entry = _entry(hass)
    flow = await _open(hass, entry, "battery")
    with _no_setup():
        result = await _of(hass, flow, {CONF_BATTERY_ENABLED: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_ENABLED] is False
    assert result["data"][CONF_BATTERIES] == []


async def test_options_strategy_sample_interval_error(hass: HomeAssistant):
    entry = _entry(hass)
    flow = await _open(hass, entry, "strategy")
    flow = await _of(hass, flow, {CONF_SCAN_INTERVAL_MINUTES: 1, CONF_SAMPLE_INTERVAL_SECONDS: 120})
    assert flow["step_id"] == "strategy"
    assert flow["errors"] == {CONF_SAMPLE_INTERVAL_SECONDS: "sample_interval_out_of_range"}


async def test_options_devices_list_labels(hass: HomeAssistant):
    entry = _entry(hass, _EXISTING_DEVICES)
    flow = await _open(hass, entry, "devices")
    assert flow["step_id"] == "devices_select"
    options = flow["data_schema"].schema["choice"].config["options"]
    assert [o["label"] for o in options] == ["Ballon", "Piscine", "Appareil 3", "Ajouter un appareil", "Terminer"]


async def test_options_devices_done_without_change(hass: HomeAssistant):
    entry = _entry(hass, _EXISTING_DEVICES)
    flow = await _open(hass, entry, "devices")
    with _no_setup():
        result = await _of(hass, flow, {"choice": "__done__"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICES] == _EXISTING_DEVICES


async def test_options_add_every_device_type(hass: HomeAssistant):
    entry = _entry(hass)
    flow = await _open(hass, entry, "devices")
    for device_type, type_input in _TYPE_INPUT.items():
        flow = await _of(hass, flow, {"choice": "__add__"})
        assert flow["step_id"] == "opt_device_type"
        flow = await _of(hass, flow, {CONF_DEVICE_TYPE: device_type})
        if type_input is not None:
            assert flow["step_id"] == f"opt_{_TYPE_STEP[device_type]}"
            flow = await _of(hass, flow, type_input)
        assert flow["step_id"] == "opt_device_common"
        assert (CONF_DEVICE_SWITCH_ENTITY in _schema_keys(flow)) is (device_type != DEVICE_TYPE_APPLIANCE)
        flow = await _of(hass, flow, _common(device_type, device_type))
        assert flow["step_id"] == "devices_select"
    with _no_setup():
        result = await _of(hass, flow, {"choice": "__done__"})
    devices = result["data"][CONF_DEVICES]
    assert [d[CONF_DEVICE_TYPE] for d in devices] == list(_TYPE_INPUT)
    assert {d[CONF_DEVICE_TYPE]: d for d in devices}[DEVICE_TYPE_EV][CONF_EV_SOC_ENTITY] == "sensor.ev_soc"


async def test_options_edit_device_prefilled(hass: HomeAssistant):
    entry = _entry(hass, _EXISTING_DEVICES)
    flow = await _open(hass, entry, "devices")
    flow = await _of(hass, flow, {"choice": "0"})
    assert flow["step_id"] == "opt_device_action"
    assert flow["description_placeholders"] == {"device_name": "Ballon"}
    flow = await _of(hass, flow, {"action": "edit"})
    assert flow["step_id"] == "opt_device_water_heater"
    assert _defaults(flow)[CONF_WH_TEMP_ENTITY] == "sensor.wh_temp"
    assert _defaults(flow)[CONF_WH_TEMP_TARGET] == 58
    flow = await _of(hass, flow, {CONF_WH_TEMP_ENTITY: "sensor.wh_temp", CONF_WH_TEMP_TARGET: 62})
    assert flow["step_id"] == "opt_device_common"
    defaults = _defaults(flow)
    assert defaults[CONF_DEVICE_NAME] == "Ballon"
    assert defaults[CONF_DEVICE_SWITCH_ENTITY] == "switch.ballon"
    assert defaults[CONF_DEVICE_POWER_W] == 2000
    flow = await _of(hass, flow, {CONF_DEVICE_NAME: "Ballon", CONF_DEVICE_POWER_W: 2400, CONF_DEVICE_PRIORITY: 8})
    with _no_setup():
        result = await _of(hass, flow, {"choice": "__done__"})
    edited = result["data"][CONF_DEVICES][0]
    assert edited[CONF_WH_TEMP_TARGET] == 62
    assert edited[CONF_DEVICE_POWER_W] == 2400
    assert edited[CONF_DEVICE_PRIORITY] == 8
    assert edited[CONF_DEVICE_SWITCH_ENTITY] == "switch.ballon"
    assert len(result["data"][CONF_DEVICES]) == 3


async def test_options_edit_pool_purges_legacy_keys(hass: HomeAssistant):
    entry = _entry(hass, _EXISTING_DEVICES)
    flow = await _open(hass, entry, "devices")
    flow = await _of(hass, flow, {"choice": "1"})
    flow = await _of(hass, flow, {"action": "edit"})
    assert flow["step_id"] == "opt_device_pool"
    assert _defaults(flow)[CONF_POOL_WATER_TEMP_ENTITY] == "sensor.pool_water"
    # Invalid input: errors, and the typed values stay in the form
    flow = await _of(hass, flow, {**_POOL_INPUT, CONF_POOL_FILTRATION_TABLE: "abc"})
    assert flow["step_id"] == "opt_device_pool"
    assert flow["errors"] == {CONF_POOL_FILTRATION_TABLE: "invalid_filtration_table"}
    assert _defaults(flow)[CONF_POOL_FILTRATION_TABLE] == "abc"
    flow = await _of(hass, flow, _POOL_INPUT)
    flow = await _of(hass, flow, {CONF_DEVICE_NAME: "Piscine", CONF_DEVICE_POWER_W: 800})
    with _no_setup():
        result = await _of(hass, flow, {"choice": "__done__"})
    pool = result["data"][CONF_DEVICES][1]
    assert CONF_POOL_FILTRATION_ENTITY not in pool
    assert "pool_split_sessions" not in pool
    assert pool[CONF_POOL_LOCAL_TEMP_ENTITY] == "sensor.pool_local"


async def test_options_delete_device(hass: HomeAssistant):
    entry = _entry(hass, _EXISTING_DEVICES)
    flow = await _open(hass, entry, "devices")
    flow = await _of(hass, flow, {"choice": "2"})
    # Unnamed device: the placeholder falls back to its position
    assert flow["description_placeholders"] == {"device_name": "Appareil 3"}
    flow = await _of(hass, flow, {"action": "delete"})
    assert flow["step_id"] == "devices_select"
    with _no_setup():
        result = await _of(hass, flow, {"choice": "__done__"})
    assert [d.get(CONF_DEVICE_NAME) for d in result["data"][CONF_DEVICES]] == ["Ballon", "Piscine"]


async def test_options_device_weights_must_sum_to_one(hass: HomeAssistant):
    entry = _entry(hass)
    flow = await _open(hass, entry, "devices")
    flow = await _of(hass, flow, {"choice": "__add__"})
    flow = await _of(hass, flow, {CONF_DEVICE_TYPE: DEVICE_TYPE_GENERIC})
    flow = await _of(hass, flow, {**_common("plug", DEVICE_TYPE_GENERIC), **_BAD_WEIGHTS})
    assert flow["step_id"] == "opt_device_common"
    assert flow["errors"] == {"base": "device_weights_must_sum_to_one"}


async def test_options_init_unknown_section_shows_menu_again(hass: HomeAssistant):
    entry = _entry(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    handler = hass.config_entries.options._progress[flow["flow_id"]]
    result = await handler.async_step_init({"section": "unknown"})
    assert result["step_id"] == "init"
