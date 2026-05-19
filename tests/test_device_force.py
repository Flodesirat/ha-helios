"""Tests for generic device force mode (water_heater, ev_charger, generic)."""
from __future__ import annotations

import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dev_config(name="Chauffe-eau", dtype=DEVICE_TYPE_WATER_HEATER, switch="switch.wh"):
    return {
        CONF_DEVICE_NAME:          name,
        CONF_DEVICE_TYPE:          dtype,
        CONF_DEVICE_SWITCH_ENTITY: switch,
        CONF_DEVICE_POWER_W:       2000,
    }


def _make_device(dtype=DEVICE_TYPE_WATER_HEATER, name=None) -> ManagedDevice:
    return ManagedDevice(_dev_config(
        name=name or f"Device_{dtype}",
        dtype=dtype,
    ))


def _make_manager(devices, scan_interval=5) -> DeviceManager:
    hass = MagicMock()
    hass.states.get.return_value = None
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()

    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = scan_interval
    mgr._dispatch_threshold = 0.3
    mgr.decision_log = deque(maxlen=500)
    mgr.battery_device = None
    return mgr


def _score(global_score=0.8, surplus_w=3000.0, bat_available_w=0.0):
    return {
        "global_score":    global_score,
        "surplus_w":       surplus_w,
        "bat_available_w": bat_available_w,
        "dispatch_threshold": 0.3,
    }


def _hass():
    h = MagicMock()
    h.services = AsyncMock()
    h.states.get.return_value = None
    return h


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestForceFieldsInitialState:
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_force_until_is_none(self, dtype):
        d = _make_device(dtype)
        assert d.force_until is None

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_inhibit_until_is_none(self, dtype):
        d = _make_device(dtype)
        assert d.inhibit_until is None

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_force_duration_default(self, dtype):
        d = _make_device(dtype)
        assert d.force_duration_h == 2.0

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_force_duration_can_be_changed(self, dtype):
        d = _make_device(dtype)
        d.force_duration_h = 0.25  # 15 min
        assert d.force_duration_h == 0.25


# ---------------------------------------------------------------------------
# Force ON — device stays on despite low score
# ---------------------------------------------------------------------------

class TestForceModeKeepsDeviceOn:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_turns_device_on_with_zero_score(self, dtype):
        """Device OFF + force active → turned ON even if global_score=0."""
        device = _make_device(dtype)
        device.is_on = False
        device.force_until = time.time() + 3600

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score(global_score=0.0, surplus_w=0.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_survives_low_score_gate(self, dtype):
        """Device ON + force active → stays ON when score < threshold."""
        device = _make_device(dtype)
        device.is_on = True
        device.force_until = time.time() + 3600

        mgr = _make_manager([device])
        # global_score=0.1 < threshold=0.3 → gate would normally turn off
        await mgr.async_dispatch(_hass(), _score(global_score=0.1, surplus_w=0.0))

        assert device.is_on is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_survives_no_surplus(self, dtype):
        """Device ON + force active + score OK but surplus=0 → optimizer would cut, force prevents it."""
        device = _make_device(dtype)
        device.is_on = True
        device.force_until = time.time() + 3600

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score(global_score=0.9, surplus_w=0.0))

        assert device.is_on is True


# ---------------------------------------------------------------------------
# Force expiry
# ---------------------------------------------------------------------------

class TestForceModeExpiry:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_until_cleared_when_expired(self, dtype):
        """force_until is set to None after expiry."""
        device = _make_device(dtype)
        device.is_on = True
        device.force_until = time.time() - 1  # already expired

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score(global_score=0.0, surplus_w=0.0))

        assert device.force_until is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_optimizer_resumes_after_force_expires(self, dtype):
        """After force expires with score=0/surplus=0, optimizer takes control (device goes off).

        Note: water_heater is excluded — when wh_temp_entity is None, urgency=1.0
        (safety: keep running if sensor unavailable). That behaviour is tested elsewhere.
        """
        device = _make_device(dtype)
        device.is_on = True
        device.turned_on_at = time.time() - 3600  # satisfies min_on_minutes
        device.force_until = time.time() - 1  # expired

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score(global_score=0.0, surplus_w=0.0))

        # force expired → optimizer now manages → low score → device off
        assert device.is_on is False


# ---------------------------------------------------------------------------
# Inhibit mode — device stays off despite high score
# ---------------------------------------------------------------------------

class TestInhibitModeKeepsDeviceOff:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_inhibit_prevents_turn_on_with_high_score(self, dtype):
        """Device OFF + inhibit active → stays OFF despite great conditions."""
        device = _make_device(dtype)
        device.is_on = False
        device.inhibit_until = time.time() + 3600

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score(global_score=0.9, surplus_w=5000.0))

        assert device.is_on is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_inhibit_turns_device_off_if_was_on(self, dtype):
        """Device ON + inhibit starts → turned off."""
        device = _make_device(dtype)
        device.is_on = True
        device.inhibit_until = time.time() + 3600

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score())

        assert device.is_on is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_inhibit_until_cleared_when_expired(self, dtype):
        """inhibit_until is set to None after expiry."""
        device = _make_device(dtype)
        device.is_on = False
        device.inhibit_until = time.time() - 1  # expired

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score())

        assert device.inhibit_until is None


# ---------------------------------------------------------------------------
# Force + inhibit interaction (simulates DeviceForceSwitch behaviour)
# ---------------------------------------------------------------------------

class TestForceInhibitSwitchBehaviour:
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_turn_on_sets_force_until(self, dtype):
        """Simulates DeviceForceSwitch.async_turn_on: sets force_until, clears inhibit."""
        d = _make_device(dtype)
        d.force_duration_h = 2.0

        before = time.time()
        d.inhibit_until = before + 999  # was inhibiting
        d.force_until = before + d.force_duration_h * 3600
        d.inhibit_until = None

        assert d.force_until is not None
        assert d.force_until >= before + 2 * 3600 - 1
        assert d.inhibit_until is None

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_turn_off_sets_inhibit_and_clears_force(self, dtype):
        """Simulates DeviceForceSwitch.async_turn_off: clears force, sets inhibit."""
        d = _make_device(dtype)
        d.force_duration_h = 4.0
        d.force_until = time.time() + 9999  # was in force mode

        before = time.time()
        d.force_until = None
        d.inhibit_until = time.time() + d.force_duration_h * 3600

        assert d.force_until is None
        assert d.inhibit_until is not None
        assert d.inhibit_until >= before + 4 * 3600 - 1

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_short_durations_15min(self, dtype):
        """15-min duration (0.25 h) sets correct force window."""
        d = _make_device(dtype)
        d.force_duration_h = 0.25
        before = time.time()
        d.force_until = before + d.force_duration_h * 3600

        assert d.force_until >= before + 15 * 60 - 1
        assert d.force_until <= before + 15 * 60 + 1

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_short_durations_30min(self, dtype):
        """30-min duration (0.5 h) sets correct force window."""
        d = _make_device(dtype)
        d.force_duration_h = 0.50
        before = time.time()
        d.force_until = before + d.force_duration_h * 3600

        assert d.force_until >= before + 30 * 60 - 1
        assert d.force_until <= before + 30 * 60 + 1


# ---------------------------------------------------------------------------
# Manual mode overrides force / inhibit
# ---------------------------------------------------------------------------

class TestManualModeOverridesForce:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_manual_mode_disables_force(self, dtype):
        """force_until is ignored when manual_mode is True."""
        device = _make_device(dtype)
        device.is_on = False
        device.manual_mode = True
        device.force_until = time.time() + 3600

        mgr = _make_manager([device])
        await mgr.async_dispatch(_hass(), _score())

        assert device.is_on is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_manual_mode_disables_inhibit(self, dtype):
        """inhibit_until is ignored when manual_mode is True — optimizer not blocked."""
        device = _make_device(dtype)
        device.is_on = True
        device.manual_mode = True
        device.inhibit_until = time.time() + 3600

        mgr = _make_manager([device])
        # Even with inhibit, manual mode means Helios hands off completely
        await mgr.async_dispatch(_hass(), _score(global_score=0.9, surplus_w=5000.0))

        # In manual mode Helios does nothing — device stays as-is
        assert device.is_on is True


# ---------------------------------------------------------------------------
# Device independence — force on one doesn't affect another
# ---------------------------------------------------------------------------

class TestDeviceIndependence:
    @pytest.mark.asyncio
    async def test_force_on_wh_does_not_affect_ev(self):
        """Force on water_heater must not block the EV charger from being managed."""
        wh = _make_device(DEVICE_TYPE_WATER_HEATER, name="Chauffe-eau")
        ev = _make_device(DEVICE_TYPE_EV, name="VE")

        wh.is_on = False
        wh.force_until = time.time() + 3600

        ev.is_on = True
        ev.turned_on_at = time.time() - 3600
        ev.inhibit_until = None

        mgr = _make_manager([wh, ev])
        # Low score + no surplus → optimizer tries to turn off ev
        await mgr.async_dispatch(_hass(), _score(global_score=0.0, surplus_w=0.0))

        assert wh.is_on is True   # force protects wh
        assert ev.is_on is False  # ev managed normally

    @pytest.mark.asyncio
    async def test_two_devices_same_type_force_independent(self):
        """Force on one generic device must not affect another generic device."""
        d1 = _make_device(DEVICE_TYPE_GENERIC, name="Prise 1")
        d2 = _make_device(DEVICE_TYPE_GENERIC, name="Prise 2")

        d1.is_on = False
        d1.force_until = time.time() + 3600

        d2.is_on = True
        d2.turned_on_at = time.time() - 3600
        d2.inhibit_until = None

        mgr = _make_manager([d1, d2])
        await mgr.async_dispatch(_hass(), _score(global_score=0.0, surplus_w=0.0))

        assert d1.is_on is True   # forced ON
        assert d2.is_on is False  # cut by optimizer


# ---------------------------------------------------------------------------
# Remaining-time calculation (replicates sensor logic)
# ---------------------------------------------------------------------------

class TestForceRemainingMinutes:
    def _remaining(self, device: ManagedDevice) -> float:
        fu = device.force_until
        return 0.0 if fu is None else round(max(0.0, (fu - time.time()) / 60), 1)

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_zero_when_inactive(self, dtype):
        d = _make_device(dtype)
        assert self._remaining(d) == 0.0

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_zero_when_expired(self, dtype):
        d = _make_device(dtype)
        d.force_until = time.time() - 60
        assert self._remaining(d) == 0.0

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_correct_minutes_for_2h(self, dtype):
        d = _make_device(dtype)
        d.force_until = time.time() + 7200
        assert 119.0 <= self._remaining(d) <= 120.0

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_correct_minutes_for_15min(self, dtype):
        d = _make_device(dtype)
        d.force_until = time.time() + 15 * 60
        assert 14.0 <= self._remaining(d) <= 15.0

    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    def test_does_not_go_negative(self, dtype):
        d = _make_device(dtype)
        d.force_until = time.time() - 1
        assert self._remaining(d) == 0.0


# ---------------------------------------------------------------------------
# Persistence (save/restore)
# ---------------------------------------------------------------------------

class TestForcePersistence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_until_saved(self, dtype):
        """force_until is included in the persisted data."""
        device = _make_device(dtype)
        device.force_until   = time.time() + 3600
        device.inhibit_until = None

        store = AsyncMock()
        store.async_save = AsyncMock()

        mgr = DeviceManager.__new__(DeviceManager)
        mgr.devices = [device]
        mgr._store = store
        mgr.battery_device = None

        await mgr._async_save_device_data()

        saved = store.async_save.call_args[0][0]
        entry = saved[device.name]
        assert entry["force_until"] == device.force_until

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_inhibit_until_saved(self, dtype):
        """inhibit_until is included in the persisted data."""
        device = _make_device(dtype)
        device.force_until   = None
        device.inhibit_until = time.time() + 1800

        store = AsyncMock()
        store.async_save = AsyncMock()

        mgr = DeviceManager.__new__(DeviceManager)
        mgr.devices = [device]
        mgr._store = store
        mgr.battery_device = None

        await mgr._async_save_device_data()

        saved = store.async_save.call_args[0][0]
        entry = saved[device.name]
        assert entry["inhibit_until"] == device.inhibit_until

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_restored_if_still_active(self, dtype):
        """force_until is restored from store when still in the future."""
        future_ts = time.time() + 3600
        device = _make_device(dtype)

        store = AsyncMock()
        stored_data = {device.name: {"force_until": future_ts, "inhibit_until": None}}
        store.async_load = AsyncMock(return_value=stored_data)
        store.async_save = AsyncMock()

        hass = MagicMock()
        hass.states.get.return_value = None

        mgr = DeviceManager.__new__(DeviceManager)
        mgr.devices = [device]
        mgr._store = store
        mgr._hass = hass
        mgr._scan_interval = 5
        mgr.decision_log = deque(maxlen=500)
        mgr.remaining_w = 0.0
        mgr.battery_device = None
        mgr._unsub_ready_listeners = []
        mgr._coordinator = None

        await mgr.async_setup()

        assert device.force_until == future_ts

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dtype", [DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_GENERIC])
    async def test_force_not_restored_if_expired(self, dtype):
        """force_until is NOT restored when it is already in the past."""
        past_ts = time.time() - 60
        device = _make_device(dtype)

        store = AsyncMock()
        stored_data = {device.name: {"force_until": past_ts, "inhibit_until": None}}
        store.async_load = AsyncMock(return_value=stored_data)
        store.async_save = AsyncMock()

        hass = MagicMock()
        hass.states.get.return_value = None

        mgr = DeviceManager.__new__(DeviceManager)
        mgr.devices = [device]
        mgr._store = store
        mgr._hass = hass
        mgr._scan_interval = 5
        mgr.decision_log = deque(maxlen=500)
        mgr.remaining_w = 0.0
        mgr.battery_device = None
        mgr._unsub_ready_listeners = []
        mgr._coordinator = None

        await mgr.async_setup()

        assert device.force_until is None
