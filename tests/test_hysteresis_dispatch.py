"""Tests for the dispatch hysteresis bonus.

When an already-ON device falls out of budget (surplus drops), it receives a
decaying bonus so it isn't cut off immediately.  The bonus is linear:

    bonus(t) = hysteresis_w × (1 − t / hysteresis_duration_s)

Key behaviours tested:
- Already-ON device stays on when surplus drops (bonus covers the gap)
- OFF device is NOT started by the bonus (it only helps ON devices)
- Bonus expires after hysteresis_duration → device turns off
- Device stays on when surplus fully recovers (hysteresis_since reset to None)
- hysteresis_w=0 disables the bonus entirely
- hysteresis_duration_minutes=0 disables the bonus entirely
- Two devices competing: the already-ON one is kept, the OFF one is not started
- Partial bonus: bonus decays but is still enough to cover a mid-range deficit
"""
from __future__ import annotations

import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET,
    CONF_WH_TEMP_MIN, CONF_DEVICE_MIN_ON_MINUTES,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMP_A  = "sensor.wh_a_temp"
_TEMP_B  = "sensor.wh_b_temp"
_SW_A    = "switch.wh_a"
_SW_B    = "switch.wh_b"


def _wh(power_w: int, temp_entity: str, switch_entity: str,
        priority: int = 5) -> ManagedDevice:
    """Water heater with temp between floor (10°C) and target (61°C) → unsatisfied, not must_run."""
    from custom_components.helios.const import CONF_DEVICE_PRIORITY
    return ManagedDevice(
        {
            CONF_DEVICE_NAME:           f"WH_{power_w}W",
            CONF_DEVICE_TYPE:           DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY:  switch_entity,
            CONF_DEVICE_POWER_W:        power_w,
            CONF_WH_TEMP_ENTITY:        temp_entity,
            CONF_WH_TEMP_TARGET:        61.0,
            CONF_WH_TEMP_MIN:           10.0,
            CONF_DEVICE_MIN_ON_MINUTES: 0,
            CONF_DEVICE_PRIORITY:       priority,
        },
        {},  # no off-peak slots
    )


def _make_manager(devices) -> DeviceManager:
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()

    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = 5
    mgr._dispatch_threshold = 0.3
    mgr.decision_log = deque(maxlen=500)
    mgr._coordinator = None
    mgr._unsub_ready_listeners = []
    mgr.battery_device = None
    return mgr


def _hass(temps: dict[str, float]) -> MagicMock:
    hass = MagicMock()
    hass.services = AsyncMock()

    def _state(entity_id):
        s = MagicMock()
        s.state = str(temps.get(entity_id, "unavailable"))
        return s

    hass.states.get.side_effect = _state
    return hass


def _score(
    surplus_w: float = 500.0,
    hysteresis_w: float = 300.0,
    hysteresis_duration_minutes: float = 10.0,
    global_score: float = 0.8,
) -> dict:
    return {
        "global_score":                global_score,
        "surplus_w":                   surplus_w,
        "bat_available_w":             0.0,
        "hysteresis_w":                hysteresis_w,
        "hysteresis_duration_minutes": hysteresis_duration_minutes,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHysteresisKeepsOnDeviceAlive:
    """Already-ON device survives a surplus drop thanks to the bonus."""

    @pytest.mark.asyncio
    async def test_on_device_stays_on_when_surplus_drops_to_zero(self):
        """Surplus drops to 0 W but the already-ON device gets a 300 W bonus → stays on."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600  # min_on elapsed

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=0.0, hysteresis_w=300.0))

        assert device.is_on, "Already-ON device should stay on thanks to the hysteresis bonus"

    @pytest.mark.asyncio
    async def test_on_device_stays_on_when_surplus_is_partially_insufficient(self):
        """Surplus (100 W) covers less than device power (300 W); bonus (300 W) makes it viable."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=100.0, hysteresis_w=300.0))

        assert device.is_on


class TestHysteresisDoesNotStartOffDevice:
    """The bonus never starts a device that was OFF."""

    @pytest.mark.asyncio
    async def test_off_device_not_started_with_zero_surplus(self):
        """Surplus = 0 W, device is OFF → bonus does not apply → device stays off."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on = False

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=0.0, hysteresis_w=300.0))

        assert not device.is_on, "Hysteresis bonus must never start an OFF device"


class TestHysteresisBonusExpiry:
    """When the bonus has fully decayed, the device is turned off."""

    @pytest.mark.asyncio
    async def test_device_turns_off_after_bonus_expires(self):
        """Simulate a device that entered the bonus zone 11 minutes ago (> 10 min duration)."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600
        t0 = time.time()
        device.hysteresis_since = t0 - 11 * 60  # bonus started 11 min ago

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        with patch("custom_components.helios.device_manager.time_mod.time", return_value=t0):
            await mgr.async_dispatch(
                hass,
                _score(surplus_w=0.0, hysteresis_w=300.0, hysteresis_duration_minutes=10.0),
            )

        assert not device.is_on, "Device should be off once the bonus has fully expired"

    @pytest.mark.asyncio
    async def test_device_stays_on_mid_bonus_period(self):
        """5 minutes into a 10-minute bonus, device fits within residual bonus → stays on.

        bonus(5min) = 300 × (1 − 5/10) = 150 W.
        Device power = 100 W, surplus = 0 → effective budget = 150 W → fits with margin.
        """
        device = _wh(100, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600
        t0 = time.time()
        device.hysteresis_since = t0 - 5 * 60  # 5 min elapsed out of 10

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        with patch("custom_components.helios.device_manager.time_mod.time", return_value=t0):
            await mgr.async_dispatch(
                hass,
                _score(surplus_w=0.0, hysteresis_w=300.0, hysteresis_duration_minutes=10.0),
            )

        assert device.is_on, "Device should still be on when the bonus has not yet fully expired"


class TestHysteresisDisabled:
    """Disabling hysteresis (w=0 or duration=0) cuts the device immediately."""

    @pytest.mark.asyncio
    async def test_hysteresis_w_zero_disables_bonus(self):
        """hysteresis_w=0 → no bonus → already-ON device turns off when surplus = 0."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=0.0, hysteresis_w=0.0))

        assert not device.is_on, "With hysteresis_w=0, device must turn off immediately"

    @pytest.mark.asyncio
    async def test_hysteresis_duration_zero_disables_bonus(self):
        """hysteresis_duration_minutes=0 → duration=0 s → elapsed always >= duration → no bonus."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        await mgr.async_dispatch(
            hass,
            _score(surplus_w=0.0, hysteresis_w=300.0, hysteresis_duration_minutes=0.0),
        )

        assert not device.is_on, "With duration=0, bonus expires instantly → device must turn off"


class TestHysteresisSinceReset:
    """hysteresis_since is cleared when the device returns to a real-budget surplus."""

    @pytest.mark.asyncio
    async def test_hysteresis_since_reset_when_back_in_budget(self):
        """Surplus recovers to 500 W (> device 300 W) → device stays on AND timer reset."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on            = True
        device.turned_on_at     = time.time() - 3600
        device.hysteresis_since = time.time() - 120  # was in bonus zone

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=500.0, hysteresis_w=300.0))

        assert device.is_on
        assert device.hysteresis_since is None, (
            "hysteresis_since should be reset to None when device is back within real budget"
        )

    @pytest.mark.asyncio
    async def test_hysteresis_since_reset_on_turn_off(self):
        """When the device is turned off (bonus expired), hysteresis_since is cleared."""
        device = _wh(300, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600
        t0 = time.time()
        device.hysteresis_since = t0 - 20 * 60  # well past 10 min duration

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        with patch("custom_components.helios.device_manager.time_mod.time", return_value=t0):
            await mgr.async_dispatch(
                hass,
                _score(surplus_w=0.0, hysteresis_w=300.0, hysteresis_duration_minutes=10.0),
            )

        assert not device.is_on
        assert device.hysteresis_since is None, (
            "hysteresis_since must be cleared when device is turned off"
        )


class TestHysteresisTwoDevices:
    """Bonus keeps the ON device running; surplus is not enough to also start the OFF one."""

    @pytest.mark.asyncio
    async def test_on_device_kept_off_device_not_started(self):
        """Surplus = 0 W, device_A is ON (gets bonus), device_B is OFF (no bonus).

        Both need 300 W.  With 300 W bonus, A stays on.  B cannot start (no surplus remains).
        """
        device_a = _wh(300, _TEMP_A, _SW_A, priority=5)
        device_a.is_on        = True
        device_a.turned_on_at = time.time() - 3600

        device_b = _wh(300, _TEMP_B, _SW_B, priority=5)
        device_b.is_on = False

        mgr  = _make_manager([device_a, device_b])
        hass = _hass({_TEMP_A: 40.0, _TEMP_B: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=0.0, hysteresis_w=300.0))

        assert device_a.is_on,  "Already-ON device should stay on via hysteresis bonus"
        assert not device_b.is_on, "OFF device must not start when there is no real surplus"

    @pytest.mark.asyncio
    async def test_equal_urgency_on_device_wins_over_off_device(self):
        """With equal urgency, device_A (ON, bonus gives fit > 0) wins over device_B (OFF, fit=0).

        Both devices have the same temperature so urgency is identical.
        device_A gets the bonus → fit = 0.6 → selected.
        device_B has fit = 0 → not started.
        The bonus is not a global budget increase: device_B cannot start on device_A's bonus.
        """
        device_a = _wh(300, _TEMP_A, _SW_A)
        device_a.is_on        = True
        device_a.turned_on_at = time.time() - 3600

        device_b = _wh(300, _TEMP_B, _SW_B)
        device_b.is_on = False

        mgr  = _make_manager([device_a, device_b])
        # Same temperature → same urgency → only fit difference determines winner
        hass = _hass({_TEMP_A: 40.0, _TEMP_B: 40.0})

        await mgr.async_dispatch(hass, _score(surplus_w=0.0, hysteresis_w=500.0))

        assert device_a.is_on,      "ON device with bonus (fit>0) should be selected and stay on"
        assert not device_b.is_on,  "OFF device cannot start on the ON device's bonus budget"


class TestHysteresisBonusDecay:
    """Bonus decays linearly — partial decay must still allow the device to stay on."""

    @pytest.mark.asyncio
    async def test_partial_decay_still_covers_device(self):
        """5 min into a 10-min bonus: bonus = 300 × 0.5 = 150 W.

        Device needs 100 W, surplus = 0 → effective budget = 150 W → device stays on.
        """
        device = _wh(100, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600
        t0 = time.time()
        device.hysteresis_since = t0 - 5 * 60  # 5 min elapsed

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        with patch("custom_components.helios.device_manager.time_mod.time", return_value=t0):
            await mgr.async_dispatch(
                hass,
                _score(surplus_w=0.0, hysteresis_w=300.0, hysteresis_duration_minutes=10.0),
            )

        assert device.is_on, "Half-decayed bonus (150 W) should cover a 100 W device"

    @pytest.mark.asyncio
    async def test_partial_decay_insufficient_for_large_device(self):
        """5 min into a 10-min bonus: bonus = 300 × 0.5 = 150 W.

        Device needs 200 W, surplus = 0 → 150 W bonus not enough → device turns off.
        """
        device = _wh(200, _TEMP_A, _SW_A)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600
        t0 = time.time()
        device.hysteresis_since = t0 - 5 * 60

        mgr  = _make_manager([device])
        hass = _hass({_TEMP_A: 40.0})

        with patch("custom_components.helios.device_manager.time_mod.time", return_value=t0):
            await mgr.async_dispatch(
                hass,
                _score(surplus_w=0.0, hysteresis_w=300.0, hysteresis_duration_minutes=10.0),
            )

        assert not device.is_on, "Half-decayed bonus (150 W) must not cover a 200 W device"
