"""DeviceManager — state restore at startup, ready listener, dispatch gates, appliance cycle."""
from __future__ import annotations

import time
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.helios.const import (
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY, CONF_DEVICE_POWER_W,
    CONF_DEVICE_INTERRUPTIBLE,
    CONF_EV_CHARGE_START_SCRIPT, CONF_EV_CHARGE_STOP_SCRIPT,
    CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_POWER_ENTITY, CONF_APPLIANCE_START_SCRIPT,
    CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    CONF_POOL_WATER_TEMP_ENTITY, CONF_POOL_LOCAL_TEMP_ENTITY,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
    DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC, DEVICE_TYPE_POOL,
    DEVICE_TYPE_WATER_HEATER,
    APPLIANCE_STATE_DONE, APPLIANCE_STATE_IDLE, APPLIANCE_STATE_PREPARING, APPLIANCE_STATE_RUNNING,
    POOL_ERROR_NO_LOCAL_TEMP,
)
from custom_components.helios.device_manager import DeviceManager

from .test_dispatch import _make_hass, _make_manager, _mock_datetime, _score_input


# ---------------------------------------------------------------------------
# Restore at startup (async_setup)
# ---------------------------------------------------------------------------

def _cfg(name: str, device_type: str, **extra) -> dict:
    return {
        CONF_DEVICE_NAME: name, CONF_DEVICE_TYPE: device_type, CONF_DEVICE_POWER_W: 1000, **extra,
    }


async def _restored(hass: HomeAssistant, configs: list[dict], stored: dict) -> DeviceManager:
    mgr = DeviceManager(hass, configs, {})
    with patch.object(mgr._store, "async_load", new=AsyncMock(return_value=stored)):
        await mgr.async_setup()
    return mgr


class TestRestore:

    async def test_manual_mode_and_generic_inhibit(self, hass):
        until = time.time() + 600
        mgr = await _restored(hass, [_cfg("Prise", DEVICE_TYPE_GENERIC)], {
            "Prise": {"manual_mode": True, "inhibit_until": until},
        })
        [device] = mgr.devices
        assert device.manual_mode is True
        assert device.inhibit_until == until

    async def test_pool_force_and_inhibit_restored_bad_date_ignored(self, hass):
        until = time.time() + 600
        mgr = await _restored(hass, [_cfg("Piscine", DEVICE_TYPE_POOL)], {
            "Piscine": {
                "date": "not-a-date", "minutes": 90,
                "pool_force_until": until, "pool_inhibit_until": until,
            },
        })
        [pool] = mgr.devices
        assert pool.pool_daily_run_minutes == 0.0
        assert pool.pool_force_until == until
        assert pool.pool_inhibit_until == until

    @pytest.mark.parametrize("switch_state, stop_calls", [("off", 1), ("on", 0)])
    async def test_ev_charge_ended_during_downtime_runs_stop_script(self, hass, switch_state, stop_calls):
        calls = async_mock_service(hass, "script", "turn_on")
        hass.states.async_set("switch.charger", switch_state)
        await _restored(hass, [_cfg(
            "VE", DEVICE_TYPE_EV,
            **{CONF_DEVICE_SWITCH_ENTITY: "switch.charger", CONF_EV_CHARGE_STOP_SCRIPT: "script.ev_stop"},
        )], {"VE": {"is_on": True}})
        await hass.async_block_till_done()
        assert len(calls) == stop_calls

    @pytest.mark.parametrize("deadline, expected", [
        ("2026-07-01T18:00:00", datetime(2026, 7, 1, 18, 0)),
        ("garbage", None),
    ])
    async def test_appliance_preparing_restored(self, hass, deadline, expected):
        mgr = await _restored(hass, [_cfg("LV", DEVICE_TYPE_APPLIANCE)], {
            "LV": {"appliance_state": APPLIANCE_STATE_PREPARING, "appliance_deadline_dt": deadline},
        })
        [device] = mgr.devices
        assert device.appliance_state == APPLIANCE_STATE_PREPARING
        assert device.appliance_deadline_dt == expected

    async def test_appliance_cycle_finished_during_downtime(self, hass):
        calls = async_mock_service(hass, "input_boolean", "turn_off")
        mgr = await _restored(hass, [_cfg(
            "LV", DEVICE_TYPE_APPLIANCE,
            **{CONF_APPLIANCE_READY_ENTITY: "input_boolean.lv_ready", CONF_APPLIANCE_CYCLE_DURATION_MINUTES: 60},
        )], {"LV": {"appliance_state": APPLIANCE_STATE_RUNNING, "appliance_cycle_start": time.time() - 7200}})
        await hass.async_block_till_done()
        assert mgr.devices[0].appliance_state == APPLIANCE_STATE_DONE
        assert [c.data["entity_id"] for c in calls] == ["input_boolean.lv_ready"]
        mgr.async_unload()

    async def test_appliance_cycle_still_running_is_resumed(self, hass):
        start = time.time() - 600
        mgr = await _restored(hass, [_cfg(
            "LV", DEVICE_TYPE_APPLIANCE, **{CONF_APPLIANCE_CYCLE_DURATION_MINUTES: 60},
        )], {"LV": {"appliance_state": APPLIANCE_STATE_RUNNING, "appliance_cycle_start": start}})
        [device] = mgr.devices
        assert device.appliance_state == APPLIANCE_STATE_RUNNING
        assert device.is_on is True
        assert device.turned_on_at == start

    async def test_appliance_running_without_cycle_start_is_done(self, hass):
        mgr = await _restored(hass, [_cfg("LV", DEVICE_TYPE_APPLIANCE)], {
            "LV": {"appliance_state": APPLIANCE_STATE_RUNNING},
        })
        assert mgr.devices[0].appliance_state == APPLIANCE_STATE_DONE


# ---------------------------------------------------------------------------
# Appliance ready listener
# ---------------------------------------------------------------------------

class TestReadyListener:

    async def _manager(self, hass):
        async_mock_service(hass, "input_boolean", "turn_off")
        hass.states.async_set("input_boolean.lv_ready", "off")
        mgr = await _restored(hass, [_cfg(
            "LV", DEVICE_TYPE_APPLIANCE, **{CONF_APPLIANCE_READY_ENTITY: "input_boolean.lv_ready"},
        )], {})
        mgr._coordinator = MagicMock(async_request_refresh=AsyncMock())
        mgr._store.async_save = AsyncMock()
        return mgr

    async def test_ready_on_triggers_prepare_at_once(self, hass):
        mgr = await self._manager(hass)
        hass.states.async_set("input_boolean.lv_ready", "on")
        await hass.async_block_till_done()
        [device] = mgr.devices
        assert device.appliance_state == APPLIANCE_STATE_PREPARING
        mgr._store.async_save.assert_awaited()
        mgr._coordinator.async_request_refresh.assert_awaited_once()
        mgr.async_unload()

    async def test_ready_ignored_when_off_or_already_started(self, hass):
        mgr = await self._manager(hass)
        [device] = mgr.devices
        device.appliance_state = APPLIANCE_STATE_RUNNING
        hass.states.async_set("input_boolean.lv_ready", "on")
        await hass.async_block_till_done()
        assert device.appliance_state == APPLIANCE_STATE_RUNNING
        hass.states.async_set("input_boolean.lv_ready", "off")
        await hass.async_block_till_done()
        mgr._coordinator.async_request_refresh.assert_not_awaited()
        mgr.async_unload()
        assert mgr._unsub_ready_listeners == []


# ---------------------------------------------------------------------------
# Dispatch gates and phase 4
# ---------------------------------------------------------------------------

def _wh(name="Ballon", interruptible=True):
    from custom_components.helios.managed_device import ManagedDevice
    return ManagedDevice({
        CONF_DEVICE_NAME: name, CONF_DEVICE_TYPE: DEVICE_TYPE_WATER_HEATER,
        CONF_DEVICE_SWITCH_ENTITY: "switch.wh", CONF_DEVICE_POWER_W: 2000,
        CONF_WH_TEMP_ENTITY: "sensor.wh_temp", CONF_WH_TEMP_TARGET: 61, CONF_WH_TEMP_MIN: 10,
        CONF_DEVICE_INTERRUPTIBLE: interruptible,
    }, {})


async def _dispatch(mgr, hass, **score):
    with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
        await mgr.async_dispatch(hass, _score_input(**score))


class TestDispatchGates:

    async def test_red_strict_blocks_device_needing_battery(self):
        """Red day, SOC below the reserve: a new device must fit in the PV surplus alone."""
        device = _wh()
        mgr = _make_manager([device])
        hass = _make_hass({"sensor.wh_temp": "55"})
        await _dispatch(mgr, hass, surplus_w=1000, bat_available_w=2000,
                        battery_soc=50, tempo_color="red", soc_reserve_rouge=80)
        assert device.is_on is False

    async def test_soc_gate_blocks_new_device(self):
        device = _wh()
        mgr = _make_manager([device])
        hass = _make_hass({"sensor.wh_temp": "55"})
        await _dispatch(mgr, hass, surplus_w=3000, battery_soc=10, soc_min=20)
        assert device.is_on is False

    async def test_non_interruptible_device_left_on(self):
        device = _wh(interruptible=False)
        device.is_on = True
        device.turned_on_at = time.time() - 3600
        mgr = _make_manager([device])
        hass = _make_hass({"sensor.wh_temp": "62"})    # satisfied, no surplus
        await _dispatch(mgr, hass, surplus_w=0)
        assert device.is_on is True

    async def test_running_appliance_not_switched_off_by_phase_4(self):
        from custom_components.helios.managed_device import ManagedDevice
        device = ManagedDevice(_cfg("LV", DEVICE_TYPE_APPLIANCE, **{CONF_APPLIANCE_START_SCRIPT: "script.lv"}))
        device.appliance_state = APPLIANCE_STATE_RUNNING
        device.appliance_cycle_start = time.time()
        device.is_on = True
        mgr = _make_manager([device])
        await _dispatch(mgr, _make_hass({}), surplus_w=0)
        assert device.is_on is True
        assert device.appliance_state == APPLIANCE_STATE_RUNNING

    async def test_idle_appliance_left_on_is_not_switched_off(self):
        """An appliance seen ON outside a cycle (e.g. legacy switch ON at restart) is left alone."""
        from custom_components.helios.managed_device import ManagedDevice
        device = ManagedDevice(_cfg("LV", DEVICE_TYPE_APPLIANCE, **{CONF_DEVICE_SWITCH_ENTITY: "switch.lv"}))
        device.is_on = True
        mgr = _make_manager([device])
        hass = _make_hass({})
        await _dispatch(mgr, hass, surplus_w=0)
        assert device.is_on is True
        hass.services.async_call.assert_not_awaited()

    async def test_resolved_pool_error_is_logged(self, caplog):
        from custom_components.helios.managed_device import ManagedDevice
        pool = ManagedDevice(_cfg("Piscine", DEVICE_TYPE_POOL, **{
            CONF_DEVICE_SWITCH_ENTITY: "switch.pump",
            CONF_POOL_WATER_TEMP_ENTITY: "sensor.water", CONF_POOL_LOCAL_TEMP_ENTITY: "sensor.room",
        }))
        pool.pool_required_minutes_today = 120.0
        pool.pool_last_date = date.today()
        pool.pool_errors_last = [POOL_ERROR_NO_LOCAL_TEMP]
        mgr = _make_manager([pool])
        await _dispatch(mgr, _make_hass({"sensor.water": "22", "sensor.room": "12"}), surplus_w=0)
        assert pool.pool_errors_last == []
        assert "Pool 'Piscine': resolved" in caplog.text


# ---------------------------------------------------------------------------
# Appliance cycle end by power drop, start without script, EV scripts
# ---------------------------------------------------------------------------

class TestAppliancePowerDrop:

    def _running(self):
        from custom_components.helios.managed_device import ManagedDevice
        device = ManagedDevice(_cfg("LL", DEVICE_TYPE_APPLIANCE, **{
            CONF_APPLIANCE_POWER_ENTITY: "sensor.ll_power",
        }))
        device.appliance_state = APPLIANCE_STATE_RUNNING
        device.is_on = True
        return device

    async def test_low_power_must_last_before_cycle_ends(self):
        device = self._running()
        mgr = _make_manager([device])
        low, high = _make_hass({"sensor.ll_power": "2"}), _make_hass({"sensor.ll_power": "1500"})

        await mgr._async_handle_appliance(low, device)
        assert device.appliance_low_power_since is not None
        assert device.appliance_state == APPLIANCE_STATE_RUNNING

        await mgr._async_handle_appliance(high, device)      # power back → timer reset
        assert device.appliance_low_power_since is None

        await mgr._async_handle_appliance(low, device)
        device.appliance_low_power_since -= 200              # > 180 s of low power
        await mgr._async_handle_appliance(low, device)
        assert device.appliance_state == APPLIANCE_STATE_DONE
        assert device.is_on is False

    async def test_done_goes_back_to_idle(self):
        device = self._running()
        device.appliance_state = APPLIANCE_STATE_DONE
        mgr = _make_manager([device])
        await mgr._async_handle_appliance(_make_hass({}), device)
        assert device.appliance_state == APPLIANCE_STATE_IDLE


async def test_appliance_start_without_script_warns(caplog):
    from custom_components.helios.managed_device import ManagedDevice
    device = ManagedDevice(_cfg("LV", DEVICE_TYPE_APPLIANCE))
    device.appliance_state = APPLIANCE_STATE_PREPARING
    mgr = _make_manager([device])
    await mgr._async_start_appliance(_make_hass({}), device, 0.8, 1.0, 0.5)
    assert "no start_script configured" in caplog.text
    assert device.appliance_state == APPLIANCE_STATE_RUNNING


@pytest.mark.parametrize("on, script", [(True, "script.ev_start"), (False, "script.ev_stop")])
async def test_ev_switch_uses_charge_scripts(on, script):
    from custom_components.helios.managed_device import ManagedDevice
    device = ManagedDevice(_cfg("VE", DEVICE_TYPE_EV, **{
        CONF_DEVICE_SWITCH_ENTITY: "switch.charger",
        CONF_EV_CHARGE_START_SCRIPT: "script.ev_start", CONF_EV_CHARGE_STOP_SCRIPT: "script.ev_stop",
    }))
    mgr = _make_manager([device])
    hass = _make_hass({})
    await mgr._async_set_switch(hass, device, on, reason="test")
    hass.services.async_call.assert_awaited_once_with(
        "script", "turn_on", {"entity_id": script}, blocking=False
    )
