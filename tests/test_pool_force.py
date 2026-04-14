"""Tests for pool force mode logic."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice, _POOL_MUST_RUN_WINDOW_H
from custom_components.helios.const import (
    DEVICE_TYPE_POOL,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_POOL_FILTRATION_ENTITY,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
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
    mgr.battery_device = None
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


# ---------------------------------------------------------------------------
# Pool quota dispatch — score_too_low vs must_run interaction
#
# Rule:
#   - CAN cut  : pool ON, not satisfied, but minutes_left > deficit
#                (there is time to restart and complete the quota later)
#   - CANNOT cut: pool ON, not satisfied, deficit >= minutes_left
#                must_run_now() fires → gate skipped → pool stays on
#   - MUST start: pool OFF, not satisfied, deficit >= minutes_left
#                must_run_now() fires → pool turned on regardless of score
#   - STAYS off : pool OFF, not satisfied, deficit < minutes_left
#                (score too low but quota can still be met later)
# ---------------------------------------------------------------------------

def _pool_device_with_end(allowed_end: str, required_min: float, done_min: float) -> ManagedDevice:
    """Pool device with a configured allowed_end and pre-set quota counters."""
    cfg = {
        **_pool_config(),
        CONF_DEVICE_ALLOWED_START: "07:00",
        CONF_DEVICE_ALLOWED_END:   allowed_end,
    }
    d = ManagedDevice(cfg)
    d.pool_daily_run_minutes    = done_min
    d.pool_required_minutes_today = required_min
    d.pool_last_date            = date.today()
    return d


def _make_mgr_with_hass(device: ManagedDevice):
    """Return (manager, hass_mock) wired together."""
    hass = MagicMock()
    hass.services = AsyncMock()
    hass.states.get.return_value = None
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = [device]
    mgr._store = store
    mgr._scan_interval = 5
    mgr._dispatch_threshold = 0.6
    mgr.decision_log = deque(maxlen=500)
    mgr.battery_device = None
    return mgr, hass


def _low_score():
    return {
        "global_score":     0.1,
        "surplus_w":        0.0,
        "bat_available_w":  0.0,
        "dispatch_threshold": 0.6,
    }


async def _dispatch_at(mgr, hass, now_str: str, score_input: dict):
    """Run async_dispatch with time frozen at *now_str* (HH:MM) in both modules."""
    now = datetime.combine(date.today(), datetime.strptime(now_str, "%H:%M").time())
    with patch("custom_components.helios.device_manager.datetime") as mock_dm, \
         patch("custom_components.helios.managed_device.datetime") as mock_md:
        mock_dm.now.return_value = now
        mock_dm.combine = datetime.combine
        mock_md.now.return_value = now
        mock_md.combine = datetime.combine
        await mgr.async_dispatch(hass, score_input)


class TestPoolQuotaDispatch:
    """Pool quota enforcement via the must_run / score_too_low interaction.

    async_dispatch increments pool_daily_run_minutes by scan_interval (5 min) for
    devices that are ON, BEFORE evaluating must_run. Test values account for this:

    - Pool ON  : effective done = done_min + 5
    - Pool OFF : effective done = done_min (no update)

    Rule:
      CAN cut   – pool ON,  (done+5 + deficit_after < minutes_left)
      CANNOT cut – pool ON,  deficit_after >= minutes_left  → must_run protects
      MUST start – pool OFF, deficit >= minutes_left         → must_run turns on
      STAYS off  – pool OFF, deficit < minutes_left          → waits for better score
    """

    @pytest.mark.asyncio
    async def test_can_cut_when_enough_time_remains(self):
        """Pool ON, small deficit vs large window → cut allowed.

        16:00, allowed_end=22:00 → 360 min left.
        done=255 ON → after +5: done=260, deficit=40. 40 < 360 → must_run=False.
        """
        device = _pool_device_with_end("22:00", required_min=300, done_min=255)
        device.is_on = True
        device.turned_on_at = time.time() - 60 * 60

        mgr, hass = _make_mgr_with_hass(device)
        await _dispatch_at(mgr, hass, "16:00", _low_score())

        assert device.is_on is False
        assert device.last_decision_reason in ("fit_negligible", "overcommit")

    @pytest.mark.asyncio
    async def test_cannot_cut_when_deficit_exceeds_time_left(self):
        """Pool ON, deficit >> minutes_left → must_run fires, pool stays on despite low score.

        21:30, allowed_end=22:00 → 30 min left.
        done=200 ON → after +5: done=205, deficit=95. 95 > 30 → must_run=True.
        """
        device = _pool_device_with_end("22:00", required_min=300, done_min=200)
        device.is_on = True
        device.turned_on_at = time.time() - 60 * 60

        mgr, hass = _make_mgr_with_hass(device)
        await _dispatch_at(mgr, hass, "21:30", _low_score())

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_cannot_cut_when_deficit_just_exceeds_time_left(self):
        """Pool ON, deficit slightly > minutes_left → must_run fires.

        21:05, allowed_end=22:00 → 55 min left.
        done=235 ON → after +5: done=240, deficit=60. 60 > 55 → must_run=True.
        """
        device = _pool_device_with_end("22:00", required_min=300, done_min=235)
        device.is_on = True
        device.turned_on_at = time.time() - 60 * 60

        mgr, hass = _make_mgr_with_hass(device)
        await _dispatch_at(mgr, hass, "21:05", _low_score())

        assert device.is_on is True

    @pytest.mark.asyncio
    async def test_must_start_when_deficit_equals_time_left(self):
        """Pool OFF, deficit == minutes_left → must_run turns it on even with low score.

        21:10, allowed_end=22:00 → 50 min left.
        done=250 OFF → no update, deficit=50. 50 >= 50 → must_run=True.
        """
        device = _pool_device_with_end("22:00", required_min=300, done_min=250)
        device.is_on = False

        mgr, hass = _make_mgr_with_hass(device)
        await _dispatch_at(mgr, hass, "21:10", _low_score())

        assert device.is_on is True
        assert device.last_decision_reason == "urgency"

    @pytest.mark.asyncio
    async def test_must_start_when_deficit_exceeds_time_left(self):
        """Pool OFF, deficit > minutes_left → must_run turns it on even with low score.

        21:40, allowed_end=22:00 → 20 min left.
        done=200 OFF → no update, deficit=100. 100 > 20 → must_run=True.
        """
        device = _pool_device_with_end("22:00", required_min=300, done_min=200)
        device.is_on = False

        mgr, hass = _make_mgr_with_hass(device)
        await _dispatch_at(mgr, hass, "21:40", _low_score())

        assert device.is_on is True
        assert device.last_decision_reason == "urgency"

    @pytest.mark.asyncio
    async def test_stays_off_when_deficit_less_than_time_left(self):
        """Pool OFF, score too low, deficit < minutes_left → stays off (can start later).

        16:00, allowed_end=22:00 → 360 min left.
        done=255 OFF → no update, deficit=45. 45 < 360 → must_run=False.
        """
        device = _pool_device_with_end("22:00", required_min=300, done_min=255)
        device.is_on = False

        mgr, hass = _make_mgr_with_hass(device)
        await _dispatch_at(mgr, hass, "16:00", _low_score())

        assert device.is_on is False


# ---------------------------------------------------------------------------
# must_run_now — deadline = allowed_end, not midnight
# ---------------------------------------------------------------------------

def _pool_with_end(allowed_end: str, required_h: float = 5.0) -> tuple[ManagedDevice, callable]:
    """Return a pool device closing at *allowed_end* and a simple StateReader."""
    cfg = {
        **_pool_config(),
        CONF_DEVICE_ALLOWED_START: "07:00",
        CONF_DEVICE_ALLOWED_END:   allowed_end,
    }
    device = ManagedDevice(cfg)
    device.pool_daily_run_minutes = 0.0
    device.pool_required_minutes_today = required_h * 60.0
    device.pool_last_date = date.today()
    reader = lambda _: str(required_h)
    return device, reader


class TestPoolMustRunDeadline:
    """must_run_now fires relative to allowed_end, not midnight."""

    def test_within_window_far_from_deadline_no_must_run(self):
        """Pool runs until 22:00; at 10:00 with 300 min required there is plenty of time."""
        device, reader = _pool_with_end("22:00", required_h=5.0)
        # 10:00 → 720 min until 22:00 → well outside _POOL_MUST_RUN_WINDOW_H * 60
        now = datetime.combine(date.today(), datetime.strptime("10:00", "%H:%M").time())
        assert device.must_run_now(reader, now=now) is False

    def test_within_window_just_inside_must_run_window(self):
        """Pool runs until 22:00; at 14:01 only _POOL_MUST_RUN_WINDOW_H-1 h remain → still outside window."""
        device, reader = _pool_with_end("22:00", required_h=5.0)
        # 22:00 - 8h = 14:00; at 14:01 there are 7h59 left → must_run not yet active
        now = datetime.combine(date.today(), datetime.strptime("14:01", "%H:%M").time())
        assert device.must_run_now(reader, now=now) is False

    def test_just_entered_must_run_window_deficit_too_small(self):
        """8h window activated (14:00 for 22:00 end); deficit small vs time left → no must_run."""
        device, reader = _pool_with_end("22:00", required_h=5.0)
        device.pool_daily_run_minutes = 270.0  # 30 min deficit
        now = datetime.combine(date.today(), datetime.strptime("14:00", "%H:%M").time())
        # minutes_left=480, deficit=30 → 30 < 480 → must_run=False
        assert device.must_run_now(reader, now=now) is False

    def test_deficit_equals_minutes_left_triggers_must_run(self):
        """When deficit == minutes_left exactly, must_run fires."""
        device, reader = _pool_with_end("22:00", required_h=5.0)
        device.pool_daily_run_minutes = 240.0  # 60 min deficit
        # 21:00 → 60 min until 22:00 → deficit(60) >= minutes_left(60) → True
        now = datetime.combine(date.today(), datetime.strptime("21:00", "%H:%M").time())
        assert device.must_run_now(reader, now=now) is True

    def test_deficit_exceeds_minutes_left_triggers_must_run(self):
        """When deficit > minutes_left, must_run fires."""
        device, reader = _pool_with_end("22:00", required_h=5.0)
        device.pool_daily_run_minutes = 200.0  # 100 min deficit
        # 21:30 → 30 min until 22:00 → deficit(100) > minutes_left(30) → True
        now = datetime.combine(date.today(), datetime.strptime("21:30", "%H:%M").time())
        assert device.must_run_now(reader, now=now) is True

    def test_satisfied_pool_never_must_run(self):
        """Quota fully met → must_run = False regardless of time."""
        device, reader = _pool_with_end("22:00", required_h=5.0)
        device.pool_daily_run_minutes = 300.0  # exactly met
        now = datetime.combine(date.today(), datetime.strptime("21:55", "%H:%M").time())
        assert device.must_run_now(reader, now=now) is False

    def test_midnight_fallback_when_no_allowed_end(self):
        """Without allowed_end, deadline falls back to tomorrow's midnight."""
        device = _make_device()  # default config has no allowed_end
        device.pool_daily_run_minutes = 0.0
        device.pool_required_minutes_today = 60.0
        device.pool_last_date = date.today()
        reader = lambda _: "1"
        # At 23:30, only 30 min to midnight; deficit=60 → 60 >= 30 → True
        now = datetime.combine(date.today(), datetime.strptime("23:30", "%H:%M").time())
        assert device.must_run_now(reader, now=now) is True
