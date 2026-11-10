"""Switch entities — auto mode, device force, manual mode, EV plugged, battery driving."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.const import (
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_POWER_W, CONF_DEVICE_SWITCH_ENTITY,
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER,
)
from custom_components.helios.managed_device import BatteryDevice, ManagedDevice
from custom_components.helios.switch import (
    BatteryDrivingSwitch, DeviceForceSwitch, DeviceManualSwitch,
    EnergyOptimizerModeSwitch, EVPluggedSwitch,
)

_ENTRY = MagicMock(entry_id="e1")


def _coordinator() -> MagicMock:
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    coord.battery_drive.async_sync = AsyncMock()
    coord.device_manager.async_persist_device_state = AsyncMock()
    coord.device_manager.battery_device = BatteryDevice({})
    return coord


def _device(device_type: str = DEVICE_TYPE_WATER_HEATER, switch: str | None = "switch.ballon") -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME: "Ballon", CONF_DEVICE_TYPE: device_type,
        CONF_DEVICE_POWER_W: 2000, CONF_DEVICE_SWITCH_ENTITY: switch,
    })


def _entity(cls, coord, *args):
    entity = cls(coord, _ENTRY, *args)
    entity.hass = MagicMock()
    entity.hass.services.async_call = AsyncMock()
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_auto_mode_switch_syncs_battery_drive():
    coord = _coordinator()
    coord.enabled = False
    sw = _entity(EnergyOptimizerModeSwitch, coord)
    await sw.async_turn_on()
    assert coord.enabled is True and sw.is_on is True
    await sw.async_turn_off()
    assert coord.enabled is False
    assert coord.battery_drive.async_sync.await_count == 2


class TestDeviceForceSwitch:

    async def test_turn_on_forces_for_selected_duration(self):
        coord = _coordinator()
        device = _device()
        device.force_duration_h = 1.0
        device.inhibit_until = time.time() + 999
        sw = _entity(DeviceForceSwitch, coord, device)
        await sw.async_turn_on()
        assert sw.is_on is True
        assert device.force_until == pytest.approx(time.time() + 3600, abs=5)
        assert device.inhibit_until is None
        assert device.is_on is True
        sw.hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on", {"entity_id": "switch.ballon"}, blocking=False
        )
        coord.device_manager.async_persist_device_state.assert_awaited_once()
        coord.async_request_refresh.assert_awaited_once()

    async def test_turn_off_inhibits_for_selected_duration(self):
        coord = _coordinator()
        device = _device()
        device.force_until = time.time() + 999
        device.force_duration_h = 2.0
        sw = _entity(DeviceForceSwitch, coord, device)
        await sw.async_turn_off()
        assert sw.is_on is False
        assert device.force_until is None
        assert device.inhibit_until == pytest.approx(time.time() + 7200, abs=5)
        assert device.is_on is False
        sw.hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.ballon"}, blocking=False
        )

    async def test_device_without_switch_entity_calls_no_service(self):
        coord = _coordinator()
        device = _device(switch=None)
        sw = _entity(DeviceForceSwitch, coord, device)
        await sw.async_turn_on()
        await sw.async_turn_off()
        sw.hass.services.async_call.assert_not_awaited()
        assert coord.async_request_refresh.await_count == 2


async def test_manual_switch_hands_off_device():
    coord = _coordinator()
    device = _device()
    sw = _entity(DeviceManualSwitch, coord, device)
    await sw.async_turn_on()
    assert device.manual_mode is True and sw.is_on is True
    await sw.async_turn_off()
    assert device.manual_mode is False
    assert coord.device_manager.async_persist_device_state.await_count == 2
    assert coord.async_request_refresh.await_count == 2


async def test_ev_plugged_switch():
    device = _device(DEVICE_TYPE_EV)
    sw = _entity(EVPluggedSwitch, _coordinator(), device)
    await sw.async_turn_on()
    assert device.ev_plugged_manual is True and sw.is_on is True
    await sw.async_turn_off()
    assert device.ev_plugged_manual is False


async def test_battery_driving_switch_on_persists_and_syncs():
    coord = _coordinator()
    coord.device_manager.battery_device.driving = False
    sw = _entity(BatteryDrivingSwitch, coord)
    await sw.async_turn_on()
    assert coord.device_manager.battery_device.driving is True
    coord.device_manager.async_persist_device_state.assert_awaited_once()
    coord.battery_drive.async_sync.assert_awaited_once()
