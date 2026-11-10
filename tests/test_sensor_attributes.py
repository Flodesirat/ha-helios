"""Sensor entities — SOC label, base load profile, forecast, device state attributes."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.helios.const import (
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_POWER_W,
    CONF_WH_TEMP_ENTITY, CONF_EV_SOC_ENTITY, CONF_EV_PLUGGED_ENTITY,
    DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC, DEVICE_TYPE_WATER_HEATER,
    APPLIANCE_STATE_IDLE, APPLIANCE_STATE_PREPARING, APPLIANCE_STATE_RUNNING,
)
from custom_components.helios.daily_optimizer import ForecastResult
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.sensor import (
    DeviceStateSensor, EnergyOptimizerBaseLoadSensor, ForecastSensor, _soc_level_label,
)

_ENTRY = MagicMock(entry_id="e1")


@pytest.mark.parametrize("soc, label", [
    (None, None), (10, "Réserve"), (40, "Basse"), (70, "Optimale"),
    (85, "Haute"), (93, "Très haute"), (99, "Pleine"),
])
def test_soc_level_label(soc, label):
    assert _soc_level_label(soc) == label


class TestBaseLoadSensor:
    def test_no_profile_yet(self):
        coord = MagicMock()
        coord.consumption_learner.profile = None
        sensor = EnergyOptimizerBaseLoadSensor(coord, _ENTRY)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {"sample_count": 0, "hourly_w": []}

    def test_hourly_means(self):
        coord = MagicMock()
        # 12 five-minute slots per hour, value = hour × 100 W
        coord.consumption_learner.profile = [float((i // 12) * 100) for i in range(288)]
        coord.consumption_learner.sample_count = 42
        sensor = EnergyOptimizerBaseLoadSensor(coord, _ENTRY)
        attrs = sensor.extra_state_attributes
        assert attrs["sample_count"] == 42
        assert attrs["hourly_w"][0] == {"hour": "00:00", "w": 0.0}
        assert attrs["hourly_w"][23] == {"hour": "23:00", "w": 2300.0}
        assert sensor.native_value in {float(h * 100) for h in range(24)}


class TestForecastSensor:
    def test_no_forecast(self):
        coord = MagicMock(forecast_data=None)
        sensor = ForecastSensor(coord, _ENTRY)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_forecast_attributes(self):
        fd = ForecastResult(
            forecast_pv_kwh=20.0, forecast_consumption_kwh=15.0,
            forecast_import_kwh=3.0, forecast_export_kwh=8.0,
            forecast_self_consumption_pct=60.0, forecast_self_sufficiency_pct=80.0,
            forecast_cost=0.5, forecast_savings=2.5, last_forecast="2026-11-10T05:00:00",
        )
        sensor = ForecastSensor(MagicMock(forecast_data=fd), _ENTRY)
        assert sensor.native_value == 60.0
        attrs = sensor.extra_state_attributes
        assert attrs["forecast_import_kwh"] == 3.0
        assert attrs["last_forecast"] == "2026-11-10T05:00:00"
        assert len(attrs) == 9


def _device(device_type: str, **cfg) -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME: "Appareil", CONF_DEVICE_TYPE: device_type, CONF_DEVICE_POWER_W: 1000, **cfg,
    })


def _sensor(hass: HomeAssistant, device: ManagedDevice) -> DeviceStateSensor:
    coord = MagicMock()
    coord.hass = hass
    return DeviceStateSensor(coord, _ENTRY, device)


class TestDeviceStateSensor:

    @pytest.mark.parametrize("state, expected", [
        (APPLIANCE_STATE_RUNNING, "running"),
        (APPLIANCE_STATE_PREPARING, "waiting"),
        (APPLIANCE_STATE_IDLE, "off"),
    ])
    async def test_appliance_state(self, hass, state, expected):
        device = _device(DEVICE_TYPE_APPLIANCE)
        device.appliance_state = state
        assert _sensor(hass, device).native_value == expected

    @pytest.mark.parametrize("raw, expected", [("55.5", 55.5), ("unavailable", None), ("abc", None)])
    async def test_water_heater_temperature(self, hass, raw, expected):
        hass.states.async_set("sensor.wh_temp", raw)
        device = _device(DEVICE_TYPE_WATER_HEATER, **{CONF_WH_TEMP_ENTITY: "sensor.wh_temp"})
        attrs = _sensor(hass, device).extra_state_attributes
        assert attrs.get("temperature") == expected
        assert attrs["force_remaining_min"] == 0.0

    @pytest.mark.parametrize("raw, expected", [("62", 62.0), ("unknown", None), ("abc", None)])
    async def test_ev_soc_and_plugged(self, hass, raw, expected):
        hass.states.async_set("sensor.ev_soc", raw)
        hass.states.async_set("binary_sensor.ev_plugged", "on")
        device = _device(DEVICE_TYPE_EV, **{
            CONF_EV_SOC_ENTITY: "sensor.ev_soc", CONF_EV_PLUGGED_ENTITY: "binary_sensor.ev_plugged",
        })
        device.force_until = time.time() + 30 * 60
        attrs = _sensor(hass, device).extra_state_attributes
        assert attrs.get("soc") == expected
        assert attrs["plugged"] is True
        assert attrs["force_remaining_min"] == pytest.approx(30.0, abs=0.2)

    async def test_ev_plugged_entity_missing(self, hass):
        device = _device(DEVICE_TYPE_EV, **{CONF_EV_PLUGGED_ENTITY: "binary_sensor.missing"})
        assert "plugged" not in _sensor(hass, device).extra_state_attributes

    async def test_generic_force_attributes(self, hass):
        device = _device(DEVICE_TYPE_GENERIC)
        device.force_until = time.time() + 15 * 60
        attrs = _sensor(hass, device).extra_state_attributes
        assert attrs["force_remaining_min"] == pytest.approx(15.0, abs=0.2)
        # No entities registered in this test → no linked entity ids
        assert attrs["force_switch_entity"] is None
        assert attrs["force_duration_entity"] is None
