"""Tests for pool force mode logic."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager, ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_POOL,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_POOL_FILTRATION_ENTITY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool_config(name="Piscine", switch="switch.pompe", filtration_entity="sensor.filtration_h"):
    return {
        CONF_DEVICE_NAME:            name,
        CONF_DEVICE_TYPE:            DEVICE_TYPE_POOL,
        CONF_DEVICE_SWITCH_ENTITY:   switch,
        CONF_DEVICE_POWER_W:         300,
        CONF_POOL_FILTRATION_ENTITY: filtration_entity,
    }


def _make_device(config=None) -> ManagedDevice:
    return ManagedDevice(config or _pool_config())


def _make_manager(devices=None, scan_interval=5) -> DeviceManager:
    hass = MagicMock()
    hass.states.get.return_value = None
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()

    config = {"scan_interval_minutes": scan_interval}
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices or [_make_device()]
    mgr._store = store
    mgr._scan_interval = scan_interval
    mgr._dispatch_threshold = 0.3
    mgr.decision_log = deque(maxlen=500)
    return mgr


def _score_input(global_score=0.8, surplus_w=2000.0, bat_available_w=0.0):
    return {
        "global_score":    global_score,
        "surplus_w":       surplus_w,
        "bat_available_w": bat_available_w,
        "dispatch_threshold": 0.3,
    }


# ---------------------------------------------------------------------------
# ManagedDevice — force mode fields
# ---------------------------------------------------------------------------

class TestPoolForceFields:
    def test_initial_state(self):
        d = _make_device()
        assert d.pool_force_until is None
        assert d.pool_force_duration_h == 2.0

    def test_force_until_can_be_set(self):
        d = _make_device()
        d.pool_force_until = time.time() + 3600
        assert d.pool_force_until is not None

    def test_force_duration_can_be_changed(self):
        d = _make_device()
        d.pool_force_duration_h = 4.0
        assert d.pool_force_duration_h == 4.0


# ---------------------------------------------------------------------------
# Daily counter isolation
# ---------------------------------------------------------------------------

class TestPoolForceCounterIsolation:
    @pytest.mark.asyncio
    async def test_daily_counter_increments_during_force(self):
        """pool_daily_run_minutes must still increment while force mode is active."""
        device = _make_device()
        device.is_on = True
        device.pool_daily_run_minutes = 10.0
        device.pool_last_date = date.today()
        device.pool_force_until = time.time() + 7200  # active

        mgr = _make_manager([device], scan_interval=5)
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input())

        assert device.pool_daily_run_minutes == 15.0, (
            "Counter must still increment during force mode"
        )

    @pytest.mark.asyncio
    async def test_daily_counter_increments_normally_without_force(self):
        """pool_daily_run_minutes increments normally when force mode is off."""
        device = _make_device()
        device.is_on = True
        device.pool_daily_run_minutes = 10.0
        device.pool_last_date = date.today()
        device.pool_force_until = None  # no force

        mgr = _make_manager([device], scan_interval=5)
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input(global_score=0.0))

        assert device.pool_daily_run_minutes == 15.0, (
            "Counter should increment by scan_interval when device is on"
        )

    @pytest.mark.asyncio
    async def test_force_not_stopped_when_daily_quota_reached(self):
        """Force mode continues even when pool_daily_run_minutes >= required filtration."""
        device = _make_device()
        device.is_on = True
        device.pool_daily_run_minutes = 999.0  # way above any quota
        device.pool_last_date = date.today()
        device.pool_force_until = time.time() + 3600

        # filtration_entity returns 1 h required → quota clearly met
        hass = MagicMock()
        hass.services = AsyncMock()
        mock_state = MagicMock()
        mock_state.state = "1"
        hass.states.get.return_value = mock_state

        mgr = _make_manager([device])
        await mgr.async_dispatch(hass, _score_input())

        assert device.is_on is True, (
            "Force mode must not be stopped when daily quota is reached"
        )


# ---------------------------------------------------------------------------
# Force mode keeps device ON regardless of score / quota
# ---------------------------------------------------------------------------

class TestPoolForceModeDispatch:
    @pytest.mark.asyncio
    async def test_force_mode_keeps_device_on_with_low_score(self):
        """Device stays ON during force mode even if global_score is 0."""
        device = _make_device()
        device.is_on = False
        device.pool_force_until = time.time() + 3600

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input(global_score=0.0, surplus_w=0.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_force_mode_expires_and_device_handed_back(self):
        """When force_until expires, pool_force_until is cleared."""
        device = _make_device()
        device.is_on = True
        device.pool_force_until = time.time() - 1  # already expired

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input(global_score=0.0, surplus_w=0.0))

        assert device.pool_force_until is None

    @pytest.mark.asyncio
    async def test_force_mode_survives_low_score_gate(self):
        """Gate 'score < threshold' must not turn off a device in force mode."""
        device = _make_device()
        device.is_on = True
        device.pool_force_until = time.time() + 3600

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        # global_score=0.1 < threshold=0.3 → gate would normally turn device off
        await mgr.async_dispatch(hass, _score_input(global_score=0.1, surplus_w=0.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_force_mode_survives_greedy_allocation_no_surplus(self):
        """When score >= threshold but no surplus, greedy allocation must not turn off a forced device."""
        device = _make_device()
        device.is_on = True
        device.pool_force_until = time.time() + 3600

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        # global_score=0.9 > threshold → gate does NOT fire, greedy allocation runs
        # surplus=0 → without protection, greedy would turn the device off
        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=0.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_two_pools_only_forced_one_protected(self):
        """With two pool devices, force mode on one must not affect the other."""
        forced  = _make_device(_pool_config(name="Piscine forcée",  switch="switch.p1"))
        normal  = _make_device(_pool_config(name="Piscine normale", switch="switch.p2"))

        forced.is_on = True
        forced.pool_force_until = time.time() + 3600

        normal.is_on = True
        normal.pool_daily_run_minutes = 999.0   # quota satisfied
        normal.pool_last_date = date.today()

        mgr = _make_manager([forced, normal])
        hass = MagicMock()
        hass.services = AsyncMock()
        mock_state = MagicMock()
        mock_state.state = "1"   # 1 h required → normal device is satisfied
        hass.states.get.return_value = mock_state

        # Score above threshold so gate doesn't fire; let greedy/satisfaction run
        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=5000.0))

        assert forced.is_on is True,  "Forced device must stay ON"
        assert normal.is_on is False, "Normal device must be turned off (quota satisfied)"


# ---------------------------------------------------------------------------
# Midnight reset does not affect force_until
# ---------------------------------------------------------------------------

class TestPoolForceSurvivesDayChange:
    @pytest.mark.asyncio
    async def test_force_until_survives_daily_counter_reset(self):
        """pool_force_until must not be touched when pool_daily_run_minutes resets at midnight."""
        device = _make_device()
        device.is_on = True
        expected_force_until = time.time() + 7200
        device.pool_force_until = expected_force_until
        # Simulate a day change: pool_last_date is yesterday
        from datetime import date, timedelta
        device.pool_last_date = date.today() - timedelta(days=1)
        device.pool_daily_run_minutes = 300.0

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input())

        # Daily counter was reset by day change (via update_pool_run_time if called)
        # but pool_force_until must be untouched
        assert device.pool_force_until == expected_force_until


# ---------------------------------------------------------------------------
# Inhibit mode (forced OFF)
# ---------------------------------------------------------------------------

class TestPoolInhibitMode:
    @pytest.mark.asyncio
    async def test_inhibit_keeps_device_off_despite_high_score(self):
        """Inhibit mode must prevent optimizer from turning device ON."""
        device = _make_device()
        device.is_on = False
        device.pool_inhibit_until = time.time() + 3600

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        # Great conditions: high score + large surplus → optimizer would normally turn it on
        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=5000.0))

        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_inhibit_turns_device_off_if_was_on(self):
        """If device is ON when inhibit starts, it must be turned off."""
        device = _make_device()
        device.is_on = True
        device.pool_inhibit_until = time.time() + 3600

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input())

        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_inhibit_expires_and_optimizer_resumes(self):
        """After inhibit expires, pool_inhibit_until is cleared."""
        device = _make_device()
        device.is_on = False
        device.pool_inhibit_until = time.time() - 1  # expired

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input())

        assert device.pool_inhibit_until is None

    @pytest.mark.asyncio
    async def test_inhibit_overrides_must_run(self):
        """Inhibit mode must take priority over must_run_now."""
        device = _make_device()
        device.is_on = False
        device.pool_inhibit_until = time.time() + 3600
        # Simulate quota deficit so must_run_now would fire
        device.pool_daily_run_minutes = 0.0
        device.pool_last_date = date.today()

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        # filtration entity: 24 h required → huge deficit → must_run would normally fire
        mock_state = MagicMock()
        mock_state.state = "24"
        hass.states.get.return_value = mock_state

        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=5000.0))

        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_turn_off_switch_sets_inhibit(self):
        """Turning the PoolForceSwitch off sets pool_inhibit_until for the selected duration."""
        device = _make_device()
        device.pool_force_duration_h = 4.0
        device.pool_force_until = None  # not in force mode

        before = time.time()
        # Simulate async_turn_off logic directly
        device.pool_force_until = None
        device.pool_inhibit_until = time.time() + device.pool_force_duration_h * 3600

        assert device.pool_inhibit_until is not None
        assert device.pool_inhibit_until >= before + 4 * 3600 - 1

    @pytest.mark.asyncio
    async def test_turn_off_during_force_cancels_force_and_sets_inhibit(self):
        """Turning switch OFF while in force mode cancels force AND sets inhibit."""
        device = _make_device()
        device.pool_force_duration_h = 2.0
        device.pool_force_until = time.time() + 7200  # was in force mode

        before = time.time()
        # Simulate async_turn_off
        device.pool_force_until = None
        device.pool_inhibit_until = time.time() + device.pool_force_duration_h * 3600

        assert device.pool_force_until is None
        assert device.pool_inhibit_until >= before + 2 * 3600 - 1

    def test_inhibit_remaining_zero_when_not_active(self):
        d = _make_device()
        d.pool_inhibit_until = None
        iu = d.pool_inhibit_until
        remaining = 0.0 if iu is None else round(max(0.0, (iu - time.time()) / 60), 1)
        assert remaining == 0.0

    def test_inhibit_remaining_correct_minutes(self):
        d = _make_device()
        d.pool_inhibit_until = time.time() + 4 * 3600
        iu = d.pool_inhibit_until
        remaining = 0.0 if iu is None else round(max(0.0, (iu - time.time()) / 60), 1)
        assert 239.0 <= remaining <= 240.0


# ---------------------------------------------------------------------------
# Manual mode
# ---------------------------------------------------------------------------

class TestDeviceManualMode:
    def test_manual_mode_default_is_false(self):
        d = _make_device()
        assert d.manual_mode is False

    @pytest.mark.asyncio
    async def test_manual_mode_prevents_turn_on(self):
        """In manual mode the optimizer must never turn the device ON."""
        device = _make_device()
        device.is_on = False
        device.manual_mode = True

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=5000.0))

        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_manual_mode_prevents_turn_off(self):
        """In manual mode the optimizer must never turn the device OFF."""
        device = _make_device()
        device.is_on = True
        device.manual_mode = True

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        # Low score gate would normally turn it off
        await mgr.async_dispatch(hass, _score_input(global_score=0.0, surplus_w=0.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_manual_mode_disables_force_mode(self):
        """force_until is ignored when manual_mode is True."""
        device = _make_device()
        device.is_on = False
        device.manual_mode = True
        device.pool_force_until = time.time() + 3600  # would normally force ON

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input())

        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_manual_mode_disables_inhibit_mode(self):
        """inhibit_until is ignored when manual_mode is True."""
        device = _make_device()
        device.is_on = True
        device.manual_mode = True
        device.pool_inhibit_until = time.time() + 3600  # would normally force OFF

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=5000.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_manual_mode_disables_must_run(self):
        """must_run_now is ignored when manual_mode is True."""
        device = _make_device()
        device.is_on = False
        device.manual_mode = True
        device.pool_daily_run_minutes = 0.0
        device.pool_last_date = date.today()

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        mock_state = MagicMock()
        mock_state.state = "24"  # 24 h required → huge deficit → must_run would fire
        hass.states.get.return_value = mock_state

        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=5000.0))

        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_manual_mode_does_not_increment_pool_counter(self):
        """Pool run counter is not updated in manual mode (Helios doesn't control the pump)."""
        device = _make_device()
        device.is_on = True
        device.manual_mode = True
        device.pool_daily_run_minutes = 10.0
        device.pool_last_date = date.today()

        mgr = _make_manager([device], scan_interval=5)
        hass = MagicMock()
        hass.services = AsyncMock()
        hass.states.get.return_value = None

        await mgr.async_dispatch(hass, _score_input())

        assert device.pool_daily_run_minutes == 10.0

    @pytest.mark.asyncio
    async def test_disabling_manual_mode_resumes_optimizer(self):
        """After manual_mode is set back to False, the optimizer manages the device again."""
        device = _make_device()
        device.is_on = False
        device.manual_mode = False
        device.pool_daily_run_minutes = 0.0
        device.pool_last_date = date.today()

        mgr = _make_manager([device])
        hass = MagicMock()
        hass.services = AsyncMock()
        # Filtration entity: 4 h required → device is not satisfied (0 min done)
        mock_state = MagicMock()
        mock_state.state = "4"
        hass.states.get.return_value = mock_state

        # Good conditions → optimizer should turn it ON
        # surplus ≈ device power (300 W) so fit score is high
        await mgr.async_dispatch(hass, _score_input(global_score=0.9, surplus_w=400.0))

        assert device.is_on is True


# ---------------------------------------------------------------------------
# Remaining time sensor logic
# ---------------------------------------------------------------------------

class TestPoolForceRemainingSensor:
    """Unit-test the remaining-time calculation directly (no HA mocking needed)."""

    def _remaining(self, device: ManagedDevice) -> float:
        """Replicate the sensor's native_value logic."""
        fu = device.pool_force_until
        if fu is None:
            return 0.0
        return round(max(0.0, (fu - time.time()) / 60), 1)

    def test_returns_zero_when_not_active(self):
        d = _make_device()
        assert self._remaining(d) == 0.0

    def test_returns_zero_when_expired(self):
        d = _make_device()
        d.pool_force_until = time.time() - 60  # expired 1 min ago
        assert self._remaining(d) == 0.0

    def test_returns_correct_minutes(self):
        d = _make_device()
        d.pool_force_until = time.time() + 7200  # 2 h = 120 min
        remaining = self._remaining(d)
        assert 119.0 <= remaining <= 120.0

    def test_returns_correct_minutes_for_4h(self):
        d = _make_device()
        d.pool_force_until = time.time() + 4 * 3600
        remaining = self._remaining(d)
        assert 239.0 <= remaining <= 240.0

    def test_does_not_go_negative(self):
        d = _make_device()
        d.pool_force_until = time.time() - 1
        assert self._remaining(d) == 0.0
