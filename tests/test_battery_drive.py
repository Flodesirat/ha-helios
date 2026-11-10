"""Tests for BatteryDriveController — Drive regulation through HA entities and scripts."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.helios.battery_drive import BatteryDriveController
from custom_components.helios.managed_device import BatteryDevice
from custom_components.helios.const import (
    CONF_BATTERIES, CONF_BATTERY_ENABLED, CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_DRIVE_SCRIPT, CONF_BATTERY_NEUTRAL_SCRIPT,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_SOC_MIN,
    CONF_BATTERY_GRID_TARGET_BLUE_W, CONF_BATTERY_GRID_TARGET_RED_W,
    CONF_GRID_POWER_ENTITY, CONF_GRID_SUBSCRIPTION_W,
    BATTERY_STATE_OFF, BATTERY_STATE_STARTUP_WAIT, BATTERY_STATE_REGULATING,
    BATTERY_STATE_PRECHARGE, BATTERY_STATE_MODE_LOST, BATTERY_STATE_FORCED,
    BATTERY_DRIVE_STARTUP_HOLD_S,
)


def _battery(name: str, max_w: float = 1450) -> dict:
    slug = name.lower()
    return {
        CONF_BATTERY_NAME:                  name,
        CONF_BATTERY_SOC_ENTITY:            f"sensor.{slug}_soc",
        CONF_BATTERY_DRIVE_POWER_ENTITY:    f"number.{slug}_drive",
        CONF_BATTERY_DRIVE_SCRIPT:          f"script.{slug}_drive",
        CONF_BATTERY_NEUTRAL_SCRIPT:        f"script.{slug}_neutral",
        CONF_BATTERY_MAX_CHARGE_POWER_W:    max_w,
        CONF_BATTERY_MAX_DISCHARGE_POWER_W: max_w,
    }


def _coordinator(batteries: list[dict], **cfg) -> MagicMock:
    coord = MagicMock()
    coord.config = {
        CONF_BATTERY_ENABLED:            True,
        CONF_GRID_POWER_ENTITY:          "sensor.grid",
        CONF_BATTERY_SOC_MIN:            10,
        CONF_BATTERY_GRID_TARGET_BLUE_W: 30,
        CONF_BATTERY_GRID_TARGET_RED_W:  50,
        CONF_BATTERIES:                  batteries,
        **cfg,
    }
    coord.enabled = True
    coord.battery_driving = True
    coord.tempo_color = "blue"
    coord.tempo_next_color = "blue"
    coord.is_off_peak_now.return_value = False
    coord.device_manager.battery_device = BatteryDevice({})
    coord.device_manager.async_persist_device_state = AsyncMock()
    return coord


@pytest.fixture
def services(hass):
    """Mocked number.set_value and the Drive / neutral scripts of batteries a and b."""
    return {
        "set_value": async_mock_service(hass, "number", "set_value"),
        **{
            f"{b}_{kind}": async_mock_service(hass, "script", f"{b}_{kind}")
            for b in ("a", "b") for kind in ("drive", "neutral")
        },
    }


def _written(services) -> list[tuple[str, float]]:
    return [(c.data["entity_id"], c.data["value"]) for c in services["set_value"]]


async def _started(hass, coord) -> BatteryDriveController:
    hass.states.async_set("sensor.a_soc", "50")
    hass.states.async_set("sensor.b_soc", "50")
    ctrl = BatteryDriveController(hass, coord)
    await ctrl.async_start()
    await hass.async_block_till_done()  # activation runs in the background
    return ctrl


# ---------------------------------------------------------------------------
# Activation / deactivation
# ---------------------------------------------------------------------------

class TestLifecycle:

    async def test_start_calls_drive_scripts(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A"), _battery("B")]))
        assert len(services["a_drive"]) == 1
        assert len(services["b_drive"]) == 1
        assert ctrl.active is True
        assert ctrl.state == BATTERY_STATE_STARTUP_WAIT
        ctrl.async_stop()

    async def test_not_driving_stays_off(self, hass, services):
        coord = _coordinator([_battery("A")])
        coord.battery_driving = False
        ctrl = await _started(hass, coord)
        assert ctrl.active is False
        assert ctrl.state == BATTERY_STATE_OFF
        assert services["a_drive"] == []
        ctrl.async_stop()

    async def test_driving_off_calls_neutral_script(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        coord.battery_driving = False
        await ctrl.async_sync()
        assert len(services["a_neutral"]) == 1
        assert ctrl.state == BATTERY_STATE_OFF
        ctrl.async_stop()

    async def test_helios_off_stops_regulation(self, hass, services):
        """Helios coupé → régulation coupée aussi (script neutre)."""
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        coord.enabled = False
        await ctrl.async_sync()
        assert len(services["a_neutral"]) == 1
        assert ctrl.active is False
        coord.enabled = True
        await ctrl.async_sync()
        assert len(services["a_drive"]) == 2
        ctrl.async_stop()

    async def test_battery_without_number_not_driven(self, hass, services):
        bat = _battery("A")
        del bat[CONF_BATTERY_DRIVE_POWER_ENTITY]
        ctrl = await _started(hass, _coordinator([bat]))
        assert ctrl.configured is False
        assert services["a_drive"] == []


# ---------------------------------------------------------------------------
# Regulation on grid readings
# ---------------------------------------------------------------------------

class TestRegulation:

    async def test_reading_writes_setpoint(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        assert _written(services) == [("number.a_drive", 470)]
        ctrl.async_stop()

    async def test_same_value_report_triggers_a_reading(self, hass, services):
        """Un relevé de même valeur (seul last_reported change) est traité."""
        hass.states.async_set("sensor.grid", "500")
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        hass.states.async_set("sensor.grid", "500")  # same state → state_reported
        await hass.async_block_till_done()
        assert _written(services) == [("number.a_drive", 470)]
        ctrl.async_stop()

    async def test_grid_unavailable_keeps_setpoint(self, hass, services):
        hass.states.async_set("sensor.grid", "500")
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        hass.states.async_set("sensor.grid", "unavailable")
        await hass.async_block_till_done()
        assert _written(services) == []
        ctrl.async_stop()

    async def test_unchanged_value_not_rewritten(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        # 20 s later the grid is back on target: same setpoint, no write
        await ctrl.async_handle_reading(30.0, at=100.0 + BATTERY_DRIVE_STARTUP_HOLD_S + 5)
        assert _written(services) == [("number.a_drive", 470)]
        assert ctrl.state == BATTERY_STATE_REGULATING
        ctrl.async_stop()

    async def test_startup_hold_after_first_setpoint(self, hass, services):
        """Les relevés sont ignorés 15 s après la première consigne non nulle."""
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        await ctrl.async_handle_reading(500.0, at=105.0)   # still switching: ignored
        assert len(_written(services)) == 1
        assert ctrl.state == BATTERY_STATE_STARTUP_WAIT
        await ctrl.async_handle_reading(200.0, at=120.0)
        assert _written(services)[-1] == ("number.a_drive", 640)
        assert ctrl.state == BATTERY_STATE_REGULATING
        ctrl.async_stop()

    async def test_target_follows_tempo_color(self, hass, services):
        coord = _coordinator([_battery("A")])
        coord.tempo_color = "red"
        ctrl = await _started(hass, coord)
        await ctrl.async_handle_reading(500.0, at=100.0)
        assert _written(services) == [("number.a_drive", 450)]
        assert ctrl.target_w == 50
        ctrl.async_stop()

    async def test_split_between_two_batteries(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A"), _battery("B")]))
        await ctrl.async_handle_reading(1030.0, at=100.0)
        assert _written(services) == [("number.a_drive", 500), ("number.b_drive", 500)]
        ctrl.async_stop()

    async def test_empty_battery_share_goes_to_other(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A"), _battery("B")]))
        hass.states.async_set("sensor.a_soc", "10")  # = soc_min: no discharge
        await ctrl.async_handle_reading(1030.0, at=100.0)
        assert _written(services) == [("number.a_drive", 0), ("number.b_drive", 1000)]
        ctrl.async_stop()

    async def test_unavailable_battery_left_out(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A"), _battery("B")]))
        hass.states.async_set("sensor.b_soc", "unavailable")
        await ctrl.async_handle_reading(1030.0, at=100.0)
        assert _written(services) == [("number.a_drive", 1000)]
        ctrl.async_stop()

    async def test_charges_up_to_100(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        hass.states.async_set("sensor.a_soc", "99")
        await ctrl.async_handle_reading(-500.0, at=100.0)
        assert _written(services) == [("number.a_drive", -530)]
        ctrl.async_stop()

    async def test_no_discharge_off_peak(self, hass, services):
        """Heures creuses : pas de décharge, la charge sur export reste possible."""
        coord = _coordinator([_battery("A")])
        coord.is_off_peak_now.return_value = True
        ctrl = await _started(hass, coord)
        await ctrl.async_handle_reading(500.0, at=100.0)
        assert _written(services) == [("number.a_drive", 0)]
        await ctrl.async_handle_reading(-400.0, at=101.0)
        assert _written(services)[-1] == ("number.a_drive", -430)
        ctrl.async_stop()


# ---------------------------------------------------------------------------
# Red-day pre-charge
# ---------------------------------------------------------------------------

class TestPrecharge:

    async def test_full_power_before_red_day(self, hass, services):
        coord = _coordinator([_battery("A")])
        coord.tempo_next_color = "red"
        coord.is_off_peak_now.return_value = True
        ctrl = await _started(hass, coord)
        await ctrl.async_handle_reading(300.0, at=100.0)
        assert _written(services) == [("number.a_drive", -1450)]
        await ctrl.async_handle_reading(1750.0, at=100.0 + BATTERY_DRIVE_STARTUP_HOLD_S + 5)
        assert ctrl.state == BATTERY_STATE_PRECHARGE
        ctrl.async_stop()

    async def test_capped_by_subscription(self, hass, services):
        coord = _coordinator([_battery("A")], **{CONF_GRID_SUBSCRIPTION_W: 1500})
        coord.tempo_next_color = "red"
        coord.is_off_peak_now.return_value = True
        ctrl = await _started(hass, coord)
        await ctrl.async_handle_reading(300.0, at=100.0)
        assert _written(services) == [("number.a_drive", -1200)]
        ctrl.async_stop()

    async def test_stops_when_full(self, hass, services):
        coord = _coordinator([_battery("A")])
        coord.tempo_next_color = "red"
        coord.is_off_peak_now.return_value = True
        ctrl = await _started(hass, coord)
        hass.states.async_set("sensor.a_soc", "100")
        await ctrl.async_handle_reading(300.0, at=100.0)
        assert _written(services) == [("number.a_drive", 0)]
        ctrl.async_stop()

    async def test_no_precharge_during_peak_hours(self, hass, services):
        coord = _coordinator([_battery("A")])
        coord.tempo_next_color = "red"
        ctrl = await _started(hass, coord)
        await ctrl.async_handle_reading(500.0, at=100.0)
        assert _written(services) == [("number.a_drive", 470)]
        ctrl.async_stop()


# ---------------------------------------------------------------------------
# Lost setpoint and hourly refresh
# ---------------------------------------------------------------------------

class TestRobustness:

    async def test_one_mismatch_is_not_a_loss(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        hass.states.async_set("number.a_drive", "0")
        await ctrl._async_check_setpoints()
        assert ctrl.state != BATTERY_STATE_MODE_LOST
        hass.states.async_set("number.a_drive", "470")  # the read crossed a write
        await ctrl._async_check_setpoints()
        await ctrl._async_check_setpoints()
        assert ctrl.state != BATTERY_STATE_MODE_LOST
        ctrl.async_stop()

    async def test_two_mismatches_restart_from_zero(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        hass.states.async_set("number.a_drive", "0")  # e.g. HA restart reset it
        await ctrl._async_check_setpoints()
        await ctrl._async_check_setpoints()
        assert ctrl.state == BATTERY_STATE_MODE_LOST
        # Next reading: base 0 W again, the setpoint is written again
        await ctrl.async_handle_reading(500.0, at=200.0)
        assert _written(services)[-1] == ("number.a_drive", 470)
        assert ctrl.state == BATTERY_STATE_MODE_LOST  # during the 15 s hold
        await ctrl.async_handle_reading(30.0, at=200.0 + BATTERY_DRIVE_STARTUP_HOLD_S + 5)
        assert ctrl.state == BATTERY_STATE_REGULATING
        ctrl.async_stop()

    async def test_hourly_refresh_rewrites_setpoint(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        await ctrl._async_refresh_drive()
        assert len(services["a_drive"]) == 2       # start + refresh
        assert _written(services) == [("number.a_drive", 470), ("number.a_drive", 470)]
        ctrl.async_stop()

    async def test_as_dict(self, hass, services):
        ctrl = await _started(hass, _coordinator([_battery("A")]))
        await ctrl.async_handle_reading(500.0, at=100.0)
        d = ctrl.as_dict()
        assert d["setpoint_w"] == 470
        assert d["target_w"] == 30
        assert d["batteries"] == [{"name": "A", "entity": "number.a_drive", "written_w": 470}]
        ctrl.async_stop()


# ---------------------------------------------------------------------------
# Force (switch.helios_battery_force)
# ---------------------------------------------------------------------------

def _force(coord, direction="charge", power=None, minutes=60):
    bat = coord.device_manager.battery_device
    bat.force_direction = direction
    bat.force_power_w = power
    bat.force_until = time.time() + minutes * 60
    return bat


class TestForce:

    async def test_forced_charge_written_at_once(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        _force(coord, "charge", power=800)
        await ctrl.async_sync()
        assert _written(services) == [("number.a_drive", -800)]
        assert ctrl.state == BATTERY_STATE_FORCED
        ctrl.async_stop()

    async def test_default_power_is_max_of_direction(self, hass, services):
        coord = _coordinator([_battery("A"), _battery("B", max_w=1000)])
        ctrl = await _started(hass, coord)
        _force(coord, "discharge")
        await ctrl.async_sync()
        assert _written(services) == [("number.a_drive", 1450), ("number.b_drive", 1000)]
        ctrl.async_stop()

    async def test_split_between_batteries(self, hass, services):
        coord = _coordinator([_battery("A"), _battery("B")])
        ctrl = await _started(hass, coord)
        _force(coord, "discharge", power=1000)
        await ctrl.async_sync()
        assert _written(services) == [("number.a_drive", 500), ("number.b_drive", 500)]
        ctrl.async_stop()

    async def test_reading_does_not_regulate_while_forced(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        _force(coord, "charge", power=800)
        await ctrl.async_sync()
        await ctrl.async_handle_reading(2000.0, at=100.0)
        assert _written(services) == [("number.a_drive", -800)]
        ctrl.async_stop()

    async def test_forced_discharge_allowed_off_peak(self, hass, services):
        """Le forçage passe outre l'interdiction de décharger en heures creuses."""
        coord = _coordinator([_battery("A")])
        coord.is_off_peak_now.return_value = True
        ctrl = await _started(hass, coord)
        _force(coord, "discharge", power=600)
        await ctrl.async_sync()
        assert _written(services) == [("number.a_drive", 600)]
        ctrl.async_stop()

    async def test_no_subscription_cap(self, hass, services):
        coord = _coordinator([_battery("A")], **{CONF_GRID_SUBSCRIPTION_W: 1000})
        ctrl = await _started(hass, coord)
        _force(coord, "charge", power=1450)
        await ctrl.async_handle_reading(900.0, at=100.0)
        assert _written(services) == [("number.a_drive", -1450)]
        ctrl.async_stop()

    async def test_has_priority_over_precharge(self, hass, services):
        coord = _coordinator([_battery("A")])
        coord.tempo_next_color = "red"
        coord.is_off_peak_now.return_value = True
        ctrl = await _started(hass, coord)
        _force(coord, "discharge", power=500)
        await ctrl.async_handle_reading(300.0, at=100.0)
        assert _written(services) == [("number.a_drive", 500)]
        ctrl.async_stop()

    async def test_soc_limit_ends_force(self, hass, services):
        """Batterie pleine pendant une charge forcée → fin du forçage, retour à la régulation."""
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        bat = _force(coord, "charge", power=800)
        await ctrl.async_sync()
        hass.states.async_set("sensor.a_soc", "100")
        await ctrl.async_handle_reading(0.0, at=100.0)
        assert bat.force_until is None
        coord.device_manager.async_persist_device_state.assert_awaited()
        assert ctrl.state == BATTERY_STATE_REGULATING
        # Regulation resumes from the forced power (bumpless)
        assert ctrl.regulator.applied(at=1e9) == -800
        ctrl.async_stop()

    async def test_switch_off_resumes_regulation(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        bat = _force(coord, "charge", power=800)
        await ctrl.async_sync()
        bat.force_until = None
        await ctrl.async_sync()
        assert ctrl.state == BATTERY_STATE_REGULATING
        await ctrl.async_handle_reading(-100.0, at=100.0)
        assert _written(services)[-1] == ("number.a_drive", -930)  # −800 + (−100) − 30
        ctrl.async_stop()

    async def test_expiry_detected_by_status_check(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        bat = _force(coord, "charge", power=800)
        await ctrl.async_sync()
        bat.force_until = time.time() - 1  # expired
        await ctrl._async_check_setpoints()
        assert ctrl.state == BATTERY_STATE_REGULATING
        ctrl.async_stop()

    async def test_force_with_driving_off_uses_drive_temporarily(self, hass, services):
        """Pilotage OFF : script Drive au début, script neutre à la fin, switch inchangé."""
        coord = _coordinator([_battery("A")])
        coord.battery_driving = False
        ctrl = await _started(hass, coord)
        assert ctrl.active is False
        bat = _force(coord, "discharge", power=700)
        await ctrl.async_sync()
        assert len(services["a_drive"]) == 1
        assert _written(services) == [("number.a_drive", 700)]
        bat.force_until = None
        await ctrl.async_sync()
        assert len(services["a_neutral"]) == 1
        assert ctrl.active is False
        assert ctrl.state == BATTERY_STATE_OFF
        assert coord.battery_driving is False
        ctrl.async_stop()

    async def test_helios_off_cancels_force(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        bat = _force(coord, "charge", power=800)
        await ctrl.async_sync()
        coord.enabled = False
        await ctrl.async_sync()
        assert bat.force_until is None
        assert len(services["a_neutral"]) == 1
        assert ctrl.state == BATTERY_STATE_OFF
        ctrl.async_stop()

    async def test_power_change_applied_at_once(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        bat = _force(coord, "charge", power=800)
        await ctrl.async_sync()
        bat.force_power_w = 400
        await ctrl.async_sync()
        assert _written(services)[-1] == ("number.a_drive", -400)
        ctrl.async_stop()

    async def test_lost_setpoint_rewrites_force(self, hass, services):
        coord = _coordinator([_battery("A")])
        ctrl = await _started(hass, coord)
        _force(coord, "charge", power=800)
        await ctrl.async_sync()
        hass.states.async_set("number.a_drive", "0")
        await ctrl._async_check_setpoints()
        await ctrl._async_check_setpoints()
        assert _written(services)[-1] == ("number.a_drive", -800)
        assert len(_written(services)) == 2
        assert ctrl.state == BATTERY_STATE_FORCED
        ctrl.async_stop()
