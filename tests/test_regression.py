"""Regression tests — bugs discovered in production and fixed.

Bug 1 (sensors not updating after force activation):
    PoolForceSwitch must call coordinator.async_request_refresh() after
    turn_on / turn_off so that CoordinatorEntity sensors (forçage restant,
    filtration journée) reflect the new state immediately.

Bug 2 (pump cut after 5 minutes):
    DeviceManager was using self._dispatch_threshold (frozen at init) instead
    of the dispatch_threshold passed in score_input. When the daily optimizer
    updates coordinator.dispatch_threshold, the DeviceManager must honour it.
"""
from __future__ import annotations

import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager, ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_POOL,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_POOL_FILTRATION_ENTITY,
    DEFAULT_DISPATCH_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers (shared with test_pool_force but duplicated to keep tests independent)
# ---------------------------------------------------------------------------

def _pool_cfg(name="Piscine", switch="switch.pompe"):
    return {
        CONF_DEVICE_NAME:            name,
        CONF_DEVICE_TYPE:            DEVICE_TYPE_POOL,
        CONF_DEVICE_SWITCH_ENTITY:   switch,
        CONF_DEVICE_POWER_W:         300,
        CONF_POOL_FILTRATION_ENTITY: "sensor.filtration_h",
    }


def _make_device(cfg=None) -> ManagedDevice:
    return ManagedDevice(cfg or _pool_cfg())


def _make_manager(devices, init_threshold=DEFAULT_DISPATCH_THRESHOLD, scan_interval=5):
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()

    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = scan_interval
    mgr._dispatch_threshold = init_threshold
    return mgr


def _score(global_score=0.8, surplus_w=400.0, bat_w=0.0, dispatch_threshold=None):
    d = {
        "global_score":    global_score,
        "surplus_w":       surplus_w,
        "bat_available_w": bat_w,
    }
    if dispatch_threshold is not None:
        d["dispatch_threshold"] = dispatch_threshold
    return d


def _make_hass():
    hass = MagicMock()
    hass.services = AsyncMock()
    hass.states.get.return_value = None
    return hass


# ---------------------------------------------------------------------------
# Bug 1 — coordinator.async_request_refresh() called after turn_on / turn_off
# ---------------------------------------------------------------------------

class TestForceSwitch_RefreshOnToggle:
    """PoolForceSwitch must trigger an immediate coordinator refresh."""

    def _make_switch(self, device):
        from custom_components.helios.switch import PoolForceSwitch
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        sw = PoolForceSwitch.__new__(PoolForceSwitch)
        sw.coordinator = coordinator
        sw._entry   = entry
        sw._device  = device
        sw.hass     = _make_hass()
        sw.async_write_ha_state = MagicMock()
        return sw, coordinator

    @pytest.mark.asyncio
    async def test_turn_on_calls_refresh(self):
        """async_request_refresh must be awaited after turn_on."""
        device = _make_device()
        sw, coordinator = self._make_switch(device)

        await sw.async_turn_on()

        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_turn_off_calls_refresh(self):
        """async_request_refresh must be awaited after turn_off."""
        device = _make_device()
        sw, coordinator = self._make_switch(device)

        await sw.async_turn_off()

        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_turn_on_sets_force_until_before_refresh(self):
        """pool_force_until must be set before the refresh is triggered."""
        device = _make_device()
        device.pool_force_duration_h = 2.0
        sw, coordinator = self._make_switch(device)

        captured_force_until = {}

        async def _capture():
            captured_force_until["value"] = device.pool_force_until

        coordinator.async_request_refresh.side_effect = _capture

        before = time.time()
        await sw.async_turn_on()

        assert captured_force_until["value"] is not None
        assert captured_force_until["value"] >= before + 2 * 3600 - 1

    @pytest.mark.asyncio
    async def test_turn_off_clears_force_until_before_refresh(self):
        """pool_force_until must be None and pool_inhibit_until set before the refresh."""
        device = _make_device()
        device.pool_force_until = time.time() + 7200
        device.pool_force_duration_h = 2.0
        sw, coordinator = self._make_switch(device)

        captured = {}

        async def _capture():
            captured["force_until"]   = device.pool_force_until
            captured["inhibit_until"] = device.pool_inhibit_until

        coordinator.async_request_refresh.side_effect = _capture

        await sw.async_turn_off()

        assert captured["force_until"] is None
        assert captured["inhibit_until"] is not None

    @pytest.mark.asyncio
    async def test_force_remaining_sensor_reflects_value_after_refresh(self):
        """After turn_on + refresh, PoolForceRemainingSensor native_value > 0."""
        device = _make_device()
        device.pool_force_duration_h = 4.0
        sw, _ = self._make_switch(device)

        await sw.async_turn_on()

        # Simulate what the sensor computes after the coordinator refresh
        fu = device.pool_force_until
        remaining = 0.0 if fu is None else round(max(0.0, (fu - time.time()) / 60), 1)

        assert remaining > 0.0, "Sensor must show remaining time after force is activated"
        assert 239.0 <= remaining <= 240.0


# ---------------------------------------------------------------------------
# Bug 2 — dispatch_threshold from score_input overrides frozen self._dispatch_threshold
# ---------------------------------------------------------------------------

class TestDispatchThreshold_FromScoreInput:
    """DeviceManager must use dispatch_threshold from score_input, not self._dispatch_threshold."""

    @pytest.mark.asyncio
    async def test_score_input_threshold_used_over_init_threshold(self):
        """When score_input contains dispatch_threshold, it overrides the init value."""
        device = _make_device()
        device.is_on = True
        device.pool_last_date = date.today()
        device.pool_daily_run_minutes = 300.0  # quota already met → must_run_now=False

        # Manager initialised with a LOW threshold (0.1)
        # score_input provides a HIGH threshold (0.9) → gate should fire and turn device off
        mgr = _make_manager([device], init_threshold=0.1)
        hass = _make_hass()
        mock_state = MagicMock()
        mock_state.state = "4"
        hass.states.get.return_value = mock_state

        await mgr.async_dispatch(
            hass,
            _score(global_score=0.5, surplus_w=400.0, dispatch_threshold=0.9),
        )

        assert device.is_on is False, (
            "Gate should fire using score_input threshold (0.9 > 0.5), not init threshold (0.1)"
        )

    @pytest.mark.asyncio
    async def test_score_input_threshold_prevents_gate_from_firing(self):
        """When score_input threshold is LOW, the gate must not fire even if init threshold was HIGH."""
        device = _make_device()
        device.is_on = True
        device.pool_last_date = date.today()
        device.pool_daily_run_minutes = 0.0

        # Manager initialised with a HIGH threshold (0.9)
        # score_input provides a LOW threshold (0.1) → gate should NOT fire
        mgr = _make_manager([device], init_threshold=0.9)
        hass = _make_hass()
        mock_state = MagicMock()
        mock_state.state = "4"
        hass.states.get.return_value = mock_state

        await mgr.async_dispatch(
            hass,
            _score(global_score=0.5, surplus_w=400.0, dispatch_threshold=0.1),
        )

        assert device.is_on is True, (
            "Gate must not fire: score_input threshold (0.1) < global_score (0.5)"
        )

    @pytest.mark.asyncio
    async def test_fallback_to_init_threshold_when_not_in_score_input(self):
        """When score_input has no dispatch_threshold key, self._dispatch_threshold is used."""
        device = _make_device()
        device.is_on = True
        device.pool_last_date = date.today()
        device.pool_daily_run_minutes = 300.0  # quota already met → must_run_now=False

        # init threshold 0.9, score 0.5 → gate fires → device off
        mgr = _make_manager([device], init_threshold=0.9)
        hass = _make_hass()
        mock_state = MagicMock()
        mock_state.state = "4"
        hass.states.get.return_value = mock_state

        # No dispatch_threshold key in score_input → fallback to init (0.9)
        await mgr.async_dispatch(
            hass,
            _score(global_score=0.5, surplus_w=400.0),  # no dispatch_threshold
        )

        assert device.is_on is False, (
            "Without dispatch_threshold in score_input, init threshold (0.9) must be used"
        )

    @pytest.mark.asyncio
    async def test_force_mode_still_protected_with_updated_threshold(self):
        """Force mode protects device even when score_input carries a high threshold."""
        device = _make_device()
        device.is_on = True
        device.pool_force_until = time.time() + 3600

        mgr = _make_manager([device], init_threshold=0.1)
        hass = _make_hass()

        # High threshold via score_input — gate would fire for non-forced devices
        await mgr.async_dispatch(
            hass,
            _score(global_score=0.2, surplus_w=0.0, dispatch_threshold=0.9),
        )

        assert device.is_on is True, (
            "Force mode must protect the device regardless of dispatch_threshold origin"
        )

    @pytest.mark.asyncio
    async def test_optimizer_updated_threshold_is_honoured(self):
        """Simulates the daily optimizer raising coordinator.dispatch_threshold
        and verifies DeviceManager respects it via score_input."""
        device = _make_device()
        device.is_on = True
        device.pool_last_date = date.today()
        device.pool_daily_run_minutes = 300.0  # quota already met → must_run_now=False

        # Optimizer raised the threshold to 0.7 (stored on coordinator)
        optimizer_threshold = 0.7

        mgr = _make_manager([device], init_threshold=DEFAULT_DISPATCH_THRESHOLD)
        hass = _make_hass()
        mock_state = MagicMock()
        mock_state.state = "4"
        hass.states.get.return_value = mock_state

        # Coordinator passes its (updated) threshold to score_input
        await mgr.async_dispatch(
            hass,
            _score(global_score=0.5, surplus_w=400.0, dispatch_threshold=optimizer_threshold),
        )

        assert device.is_on is False, (
            "After optimizer raises threshold to 0.7, score 0.5 must trigger the gate"
        )


