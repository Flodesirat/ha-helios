"""Tests for pool required-minutes snapshot persistence across restarts."""
from __future__ import annotations

from collections import deque
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice, StateReader
from custom_components.helios.const import (
    DEVICE_TYPE_POOL,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_POOL_FILTRATION_ENTITY,
)

FILTRATION_ENTITY = "sensor.filtration_h"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool_config(name="Piscine"):
    return {
        CONF_DEVICE_NAME:            name,
        CONF_DEVICE_TYPE:            DEVICE_TYPE_POOL,
        CONF_DEVICE_SWITCH_ENTITY:   "switch.pompe",
        CONF_DEVICE_POWER_W:         300,
        CONF_POOL_FILTRATION_ENTITY: FILTRATION_ENTITY,
    }


def _make_manager(stored_data: dict) -> DeviceManager:
    """Build a DeviceManager with a store pre-loaded with *stored_data*."""
    hass = MagicMock()
    store = AsyncMock()
    store.async_load = AsyncMock(return_value=stored_data)
    store.async_save = AsyncMock()

    hass.states.get.return_value = None  # no HA switch state during tests

    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = [ManagedDevice(_pool_config())]
    mgr._hass = hass
    mgr._store = store
    mgr._scan_interval = 5
    mgr._dispatch_threshold = 0.3
    mgr.decision_log = deque(maxlen=500)
    mgr.battery_device = None
    return mgr


def _reader_with_filtration(minutes: float) -> StateReader:
    hours = str(minutes / 60.0)  # entity in hours
    return lambda eid: hours if eid == FILTRATION_ENTITY else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPoolRequiredPersistence:

    @pytest.mark.asyncio
    async def test_required_minutes_restored_on_same_day(self):
        """On restart same day, pool_required_minutes_today is restored from storage."""
        today = date.today().isoformat()
        mgr = _make_manager({"Piscine": {"date": today, "minutes": 30.0, "required_minutes": 180.0}})

        await mgr.async_setup()

        device = mgr.devices[0]
        assert device.pool_required_minutes_today == pytest.approx(180.0)
        assert device.pool_daily_run_minutes == pytest.approx(30.0)

    @pytest.mark.asyncio
    async def test_required_minutes_not_restored_next_day(self):
        """On restart the next day, the snapshot is discarded (stale date)."""
        mgr = _make_manager({"Piscine": {"date": "2000-01-01", "minutes": 30.0, "required_minutes": 180.0}})

        await mgr.async_setup()

        device = mgr.devices[0]
        assert device.pool_required_minutes_today is None
        assert device.pool_daily_run_minutes == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_required_minutes_not_restored_when_absent(self):
        """Old storage format without required_minutes key: snapshot stays None."""
        today = date.today().isoformat()
        mgr = _make_manager({"Piscine": {"date": today, "minutes": 45.0}})

        await mgr.async_setup()

        device = mgr.devices[0]
        assert device.pool_required_minutes_today is None
        assert device.pool_daily_run_minutes == pytest.approx(45.0)

    @pytest.mark.asyncio
    async def test_required_minutes_saved_when_snapshot_captured(self):
        """When try_capture_pool_required fires, the new snapshot is persisted."""
        today = date.today().isoformat()
        # No snapshot yet in storage
        mgr = _make_manager({"Piscine": {"date": today, "minutes": 0.0}})
        await mgr.async_setup()

        device = mgr.devices[0]
        assert device.pool_required_minutes_today is None

        # Simulate dispatch at 06:00 — entity says 3 h = 180 min
        reader = _reader_with_filtration(180.0)
        device.try_capture_pool_required(reader, current_hour=6)

        assert device.pool_required_minutes_today == pytest.approx(180.0)

        # The dispatch loop should now trigger a save
        saved = {}
        mgr._store.async_save = AsyncMock(side_effect=lambda d: saved.update(d))
        await mgr._async_save_device_data()

        assert saved["Piscine"]["required_minutes"] == pytest.approx(180.0)

    @pytest.mark.asyncio
    async def test_required_minutes_not_captured_before_5am(self):
        """Snapshot is not taken before 05:00 even if entity is available."""
        mgr = _make_manager({})
        await mgr.async_setup()

        device = mgr.devices[0]
        reader = _reader_with_filtration(180.0)
        device.try_capture_pool_required(reader, current_hour=4)

        assert device.pool_required_minutes_today is None
