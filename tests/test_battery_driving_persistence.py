"""Tests for the persistence of switch.helios_battery_driving across restarts."""
from __future__ import annotations

import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import BatteryDevice


def _make_manager(stored_data: dict) -> DeviceManager:
    """A DeviceManager with a battery and a store pre-loaded with *stored_data*."""
    store = AsyncMock()
    store.async_load = AsyncMock(return_value=stored_data)
    store.async_save = AsyncMock()
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = []
    mgr._hass = MagicMock()
    mgr._store = store
    mgr._scan_interval = 5
    mgr._dispatch_threshold = 0.3
    mgr.decision_log = deque(maxlen=500)
    mgr.battery_device = BatteryDevice({})
    return mgr


class TestBatteryDrivingPersistence:

    @pytest.mark.asyncio
    async def test_driving_by_default(self):
        mgr = _make_manager({})
        await mgr.async_setup()
        assert mgr.battery_device.driving is True

    @pytest.mark.asyncio
    async def test_driving_off_restored(self):
        mgr = _make_manager({"__battery__": {"driving": False}})
        await mgr.async_setup()
        assert mgr.battery_device.driving is False

    @pytest.mark.asyncio
    async def test_legacy_manual_mode_means_not_driving(self):
        """Store d'avant 1.1 : manual_mode=True → pilotage désactivé."""
        mgr = _make_manager({"__battery__": {"manual_mode": True}})
        await mgr.async_setup()
        assert mgr.battery_device.driving is False

    @pytest.mark.asyncio
    async def test_legacy_auto_mode_means_driving(self):
        mgr = _make_manager({"__battery__": {"manual_mode": False}})
        await mgr.async_setup()
        assert mgr.battery_device.driving is True

    @pytest.mark.asyncio
    async def test_driving_saved(self):
        mgr = _make_manager({})
        await mgr.async_setup()
        mgr.battery_device.driving = False
        await mgr.async_persist_device_state()
        saved = mgr._store.async_save.call_args.args[0]
        assert saved["__battery__"]["driving"] is False

    @pytest.mark.asyncio
    async def test_force_saved_and_restored(self):
        """Forçage en cours : sens, puissance, durée et échéance survivent au redémarrage."""
        mgr = _make_manager({})
        await mgr.async_setup()
        bat = mgr.battery_device
        bat.force_until = time.time() + 600
        bat.force_direction = "discharge"
        bat.force_power_w = 800.0
        bat.force_duration_h = 1.0
        await mgr.async_persist_device_state()
        saved = mgr._store.async_save.call_args.args[0]

        restored = _make_manager(saved)
        await restored.async_setup()
        rb = restored.battery_device
        assert rb.force_active()
        assert (rb.force_direction, rb.force_power_w, rb.force_duration_h) == ("discharge", 800.0, 1.0)

    @pytest.mark.asyncio
    async def test_expired_force_not_restored(self):
        mgr = _make_manager({"__battery__": {"driving": True, "force_until": time.time() - 10}})
        await mgr.async_setup()
        assert mgr.battery_device.force_active() is False
