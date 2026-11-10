"""ManagedDevice — time window, HVAC, fit zones, deadlines, state readers."""
from __future__ import annotations

from datetime import datetime, time

import pytest

from custom_components.helios.const import (
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_POWER_W,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END, CONF_DEVICE_DEADLINE,
    CONF_EV_DEPARTURE_TIME, CONF_EV_BATTERY_CAPACITY_WH, CONF_EV_SOC_ENTITY,
    CONF_HVAC_TEMP_ENTITY, CONF_HVAC_SETPOINT_ENTITY, CONF_HVAC_MODE, CONF_HVAC_HYSTERESIS_K,
    CONF_POOL_WATER_TEMP_ENTITY,
    DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC, DEVICE_TYPE_HVAC, DEVICE_TYPE_POOL,
    HVAC_MODE_COOL, HVAC_MODE_HEAT,
)
from custom_components.helios.managed_device import ManagedDevice


def _device(device_type: str = DEVICE_TYPE_GENERIC, **cfg) -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME: "Appareil", CONF_DEVICE_TYPE: device_type, CONF_DEVICE_POWER_W: 1000, **cfg,
    })


def _reader(states: dict):
    return lambda entity_id: states.get(entity_id)


class TestAllowedWindow:
    def test_unparseable_window_is_always_allowed(self):
        device = _device(**{CONF_DEVICE_ALLOWED_START: "bad"})
        assert device.is_in_allowed_window(time(3, 0)) is True

    @pytest.mark.parametrize("now, expected", [
        (time(23, 0), True), (time(5, 0), True), (time(12, 0), False),
    ])
    def test_overnight_window(self, now, expected):
        device = _device(**{CONF_DEVICE_ALLOWED_START: "22:00", CONF_DEVICE_ALLOWED_END: "06:00"})
        assert device.is_in_allowed_window(now) is expected


class TestHvac:
    def _hvac(self, mode: str) -> ManagedDevice:
        return _device(DEVICE_TYPE_HVAC, **{
            CONF_HVAC_TEMP_ENTITY: "sensor.room", CONF_HVAC_SETPOINT_ENTITY: "input_number.setpoint",
            CONF_HVAC_MODE: mode, CONF_HVAC_HYSTERESIS_K: 0.5,
        })

    @pytest.mark.parametrize("mode, room, satisfied", [
        (HVAC_MODE_HEAT, "19.6", True),    # within 0.5 °C below the setpoint
        (HVAC_MODE_HEAT, "18.0", False),
        (HVAC_MODE_COOL, "20.4", True),
        (HVAC_MODE_COOL, "22.0", False),
    ])
    def test_satisfied_within_hysteresis(self, mode, room, satisfied):
        reader = _reader({"sensor.room": room, "input_number.setpoint": "20"})
        assert self._hvac(mode).is_satisfied(reader) is satisfied

    @pytest.mark.parametrize("room, urgency", [("20", 0.0), ("18.5", 0.5), ("15", 1.0)])
    def test_urgency_grows_with_deviation(self, room, urgency):
        reader = _reader({"sensor.room": room, "input_number.setpoint": "20"})
        assert self._hvac(HVAC_MODE_HEAT).urgency_modifier(reader) == pytest.approx(urgency)


class TestFitScore:
    def test_zero_power_device_has_no_fit(self):
        assert ManagedDevice.compute_fit_score(0, 2000, 0) == 0.0

    def test_small_grid_import_within_allowance(self):
        # 1500 W device, 1000 W surplus, no battery, 1000 W allowance → 500 W import
        assert ManagedDevice.compute_fit_score(1500, 1000, 0, grid_allowance_w=1000) == pytest.approx(0.2)

    def test_generic_score_has_no_urgency(self):
        device = _device(DEVICE_TYPE_GENERIC)
        score = device.effective_score(_reader({}), surplus_w=1000, bat_available_w=0)
        # 0.5 × priority/10 + 0.5 × fit(=1.0)
        assert score == pytest.approx(0.5 * device.priority / 10 + 0.5)


_NOON = datetime(2026, 7, 1, 12, 0)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOON


@pytest.fixture
def noon(monkeypatch):
    """Freeze managed_device's clock at 12:00 (deadline tests depend on the time of day)."""
    monkeypatch.setattr("custom_components.helios.managed_device.datetime", _FixedDatetime)
    return _NOON


class TestDeadlines:
    def test_ev_departure_uses_battery_capacity(self, noon):
        device = _device(DEVICE_TYPE_EV, **{
            CONF_EV_SOC_ENTITY: "sensor.ev_soc",
            CONF_EV_DEPARTURE_TIME: "14:00", CONF_EV_BATTERY_CAPACITY_WH: 40000,
        })
        reader = _reader({"sensor.ev_soc": "80"})
        far = _device(DEVICE_TYPE_EV, **{CONF_EV_SOC_ENTITY: "sensor.ev_soc"})
        # 40 kWh at 1 kW cannot be charged in 2 h → departure is pressing
        assert device.urgency_modifier(reader) > far.urgency_modifier(reader)

    def test_deadline_already_passed_means_tomorrow(self, noon):
        device = _device(DEVICE_TYPE_EV)
        # 11:55 has passed → tomorrow, ~24 h left: departure barely matters
        assert device._deadline_urgency("11:55", energy_wh=None, power_w=1000) == pytest.approx(0.3)

    def test_without_energy_nor_cycle_assumes_one_hour(self, noon):
        device = _device(DEVICE_TYPE_EV)
        device.appliance_cycle_duration_minutes = 0
        assert device._deadline_urgency("12:50", energy_wh=None, power_w=1000) == pytest.approx(1.0)

    def test_appliance_legacy_deadline_string(self, noon):
        device = _device(DEVICE_TYPE_APPLIANCE, **{CONF_DEVICE_DEADLINE: "12:30"})
        device.appliance_state = "preparing"
        assert device.urgency_modifier(_reader({})) == pytest.approx(1.0)

    def test_auto_deadline_after_last_slot_is_midnight(self):
        device = _device(DEVICE_TYPE_APPLIANCE)    # default slots 12:00, 18:00
        assert device._compute_auto_deadline(datetime(2026, 7, 1, 21, 0)) == datetime(2026, 7, 2, 0, 0)


class TestPoolHelpers:
    def test_non_numeric_water_temperature(self):
        device = _device(DEVICE_TYPE_POOL, **{CONF_POOL_WATER_TEMP_ENTITY: "sensor.water"})
        assert device._pool_temp(_reader({"sensor.water": "err"}), "sensor.water") is None

    def test_deadline_after_allowed_end_is_tomorrow(self):
        device = _device(DEVICE_TYPE_POOL, **{CONF_DEVICE_ALLOWED_END: "20:00"})
        now = datetime(2026, 7, 1, 21, 0)
        assert device._pool_deadline_dt(now) == datetime(2026, 7, 2, 20, 0)

    def test_deadline_without_allowed_end_is_midnight(self):
        device = _device(DEVICE_TYPE_POOL, **{CONF_DEVICE_ALLOWED_END: "never"})
        now = datetime(2026, 7, 1, 21, 0)
        assert device._pool_deadline_dt(now) == datetime(2026, 7, 2, 0, 0)


class TestStateReaders:
    def test_state_float_non_numeric_falls_back(self):
        assert ManagedDevice._state_float(_reader({"sensor.x": "abc"}), "sensor.x", fallback=7.0) == 7.0

    @pytest.mark.parametrize("raw", [None, "unavailable", "unknown"])
    def test_state_bool_unknown_falls_back(self, raw):
        assert ManagedDevice._state_bool(_reader({"binary_sensor.x": raw}), "binary_sensor.x", fallback=False) is False


class TestHvacClimateSetpoint:
    """Regression #11: a climate setpoint entity is read from its temperature attribute."""

    def _hvac(self, mode: str = HVAC_MODE_HEAT) -> ManagedDevice:
        return _device(DEVICE_TYPE_HVAC, **{
            CONF_HVAC_TEMP_ENTITY: "sensor.room", CONF_HVAC_SETPOINT_ENTITY: "climate.salon",
            CONF_HVAC_MODE: mode, CONF_HVAC_HYSTERESIS_K: 0.5,
        })

    async def test_heating_below_climate_setpoint_is_not_satisfied(self, hass):
        hass.states.async_set("sensor.room", "18")
        hass.states.async_set("climate.salon", "heat", {"temperature": 21})
        reader = ManagedDevice._make_ha_reader(hass)
        device = self._hvac()
        assert reader("climate.salon") == "21"
        assert device.is_satisfied(reader) is False
        assert device.urgency_modifier(reader) == pytest.approx(1.0)
        hass.states.async_set("sensor.room", "20.8")
        assert device.is_satisfied(reader) is True

    async def test_climate_without_setpoint_reads_none(self, hass):
        hass.states.async_set("climate.salon", "off", {})
        assert ManagedDevice._make_ha_reader(hass)("climate.salon") is None
