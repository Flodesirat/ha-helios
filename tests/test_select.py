"""Select entities — force duration of devices and battery."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.const import (
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_POWER_W,
    DEVICE_TYPE_GENERIC, DEVICE_TYPE_POOL,
)
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.select import BatteryForceDurationSelect, ForceDurationSelect


def _device(device_type: str) -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME: "Prise", CONF_DEVICE_TYPE: device_type, CONF_DEVICE_POWER_W: 500,
    })


def _select(device: ManagedDevice) -> ForceDurationSelect:
    select = ForceDurationSelect(MagicMock(), MagicMock(entry_id="e1"), device)
    select.async_write_ha_state = MagicMock()
    return select


@pytest.mark.parametrize("device_type, attr", [
    (DEVICE_TYPE_GENERIC, "force_duration_h"),
    (DEVICE_TYPE_POOL,    "pool_force_duration_h"),
])
async def test_device_force_duration_select(device_type, attr):
    device = _device(device_type)
    select = _select(device)
    assert select.current_option == "2h"
    await select.async_select_option("30min")
    assert getattr(device, attr) == 0.5
    assert select.current_option == "30min"
    select.async_write_ha_state.assert_called_once()


async def test_device_force_duration_ignores_unknown_option():
    device = _device(DEVICE_TYPE_GENERIC)
    select = _select(device)
    await select.async_select_option("3 days")
    assert device.force_duration_h == 2.0


async def test_device_force_duration_unlisted_value_shows_default():
    device = _device(DEVICE_TYPE_GENERIC)
    device.force_duration_h = 3.0       # e.g. set by an older version
    assert _select(device).current_option == "2h"


async def test_battery_force_duration_unlisted_value_shows_default():
    coordinator = MagicMock()
    coordinator.device_manager.battery_device.force_duration_h = 5.0
    coordinator.device_manager.async_persist_device_state = AsyncMock()
    select = BatteryForceDurationSelect(coordinator, MagicMock(entry_id="e1"))
    select.async_write_ha_state = MagicMock()
    assert select.current_option == "2h"
    await select.async_select_option("4h")
    assert coordinator.device_manager.battery_device.force_duration_h == 4.0
    coordinator.device_manager.async_persist_device_state.assert_awaited_once()
