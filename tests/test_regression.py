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
from collections import deque
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_POOL_FILTRATION_ENTITY,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
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


def _make_manager(devices, init_threshold=0.3, scan_interval=5):
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()

    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = scan_interval
    mgr._dispatch_threshold = init_threshold
    mgr.decision_log = deque(maxlen=500)
    mgr._coordinator = None
    mgr._unsub_ready_listeners = []
    return mgr


def _score(global_score=0.8, surplus_w=400.0, bat_w=0.0, dispatch_threshold=None, real_surplus_w=None):
    d = {
        "global_score":    global_score,
        "surplus_w":       surplus_w,
        "bat_available_w": bat_w,
    }
    if dispatch_threshold is not None:
        d["dispatch_threshold"] = dispatch_threshold
    if real_surplus_w is not None:
        d["real_surplus_w"] = real_surplus_w
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
        coordinator.device_manager.async_persist_device_state = AsyncMock()
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

        mgr = _make_manager([device], init_threshold=0.3)
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


# ---------------------------------------------------------------------------
# Bug 3 — min_on_minutes must be respected on "satisfied" and "score_too_low" shutoff
# ---------------------------------------------------------------------------
# Root cause: when a water heater was forced on by must_run (e.g. legionella floor or
# sensor glitch), it could be turned off in the very next dispatch cycle if is_satisfied
# returned True or the score gate fired — without checking min_on_minutes.
#
# Fix: both the gate path (score < threshold) and the normal dispatch path
# now always check _min_on_elapsed before issuing any shutoff.
# ---------------------------------------------------------------------------

_WH_TEMP   = "sensor.wh_temp"
_WH_SWITCH = "switch.wh"


def _wh_device(min_on_minutes: int = 30) -> ManagedDevice:
    """Water heater with a 30-minute minimum on time."""
    return ManagedDevice(
        {
            CONF_DEVICE_NAME:          "Chauffe-eau",
            CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY: _WH_SWITCH,
            CONF_DEVICE_POWER_W:       2000,
            CONF_WH_TEMP_ENTITY:       _WH_TEMP,
            CONF_WH_TEMP_TARGET:       61.0,
            CONF_WH_TEMP_MIN:          10.0,
            CONF_DEVICE_MIN_ON_MINUTES: min_on_minutes,
        },
        {},  # no off-peak slots → outside HC at all times
    )


def _wh_hass(temp: float) -> MagicMock:
    hass = MagicMock()
    hass.services = AsyncMock()

    def _state(entity_id):
        s = MagicMock()
        s.state = str(temp) if entity_id == _WH_TEMP else "unavailable"
        return s

    hass.states.get.side_effect = _state
    return hass


class TestMinOnMinutes_SatisfiedShutoff:
    """min_on_minutes must gate ALL shutoffs — satisfied, score_too_low, and fit_negligible."""

    @pytest.mark.asyncio
    async def test_gate_path_does_not_shut_off_before_min_on(self):
        """Gate path (score < threshold): device on for < min_on_minutes → stays ON.

        Scenario: device was just turned on via must_run (sensor glitch).
        Next cycle: score is low, sensor now reads temp >= target (satisfied).
        Without the fix the gate would turn it off immediately; with the fix
        it must wait until min_on_minutes has elapsed.
        """
        device = _wh_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 60  # only 1 minute ago

        mgr = _make_manager([device])
        # temp=65 > target=61 → is_satisfied=True; score 0.1 < threshold 0.4 → gate fires
        hass = _wh_hass(temp=65.0)

        await mgr.async_dispatch(hass, _score(global_score=0.1, surplus_w=0.0, dispatch_threshold=0.4))

        assert device.is_on is True, (
            "Device must not be turned off before min_on_minutes has elapsed"
        )

    @pytest.mark.asyncio
    async def test_gate_path_shuts_off_after_min_on_elapsed(self):
        """Gate path: device on for > min_on_minutes → turned OFF when satisfied."""
        device = _wh_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 31 * 60  # 31 minutes ago

        mgr = _make_manager([device])
        hass = _wh_hass(temp=65.0)  # satisfied

        await mgr.async_dispatch(hass, _score(global_score=0.1, surplus_w=0.0, dispatch_threshold=0.4))

        assert device.is_on is False, (
            "Device must be turned off once min_on_minutes has elapsed and it is satisfied"
        )

    @pytest.mark.asyncio
    async def test_normal_dispatch_path_does_not_shut_off_before_min_on(self):
        """Normal dispatch path (score >= threshold): satisfied device stays ON if min_on not met."""
        device = _wh_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 60  # only 1 minute ago

        mgr = _make_manager([device])
        # score 0.9 > threshold 0.4 → normal dispatch path; temp=65 → satisfied
        hass = _wh_hass(temp=65.0)

        await mgr.async_dispatch(hass, _score(global_score=0.9, surplus_w=0.0, dispatch_threshold=0.4))

        assert device.is_on is True, (
            "Normal dispatch path must also respect min_on_minutes on satisfied shutoff"
        )

    @pytest.mark.asyncio
    async def test_normal_dispatch_path_shuts_off_after_min_on_elapsed(self):
        """Normal dispatch path: device satisfied + min_on elapsed → turned OFF."""
        device = _wh_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 31 * 60  # 31 minutes ago

        mgr = _make_manager([device])
        hass = _wh_hass(temp=65.0)

        await mgr.async_dispatch(hass, _score(global_score=0.9, surplus_w=0.0, dispatch_threshold=0.4))

        assert device.is_on is False, (
            "Device must be turned off once min_on_minutes elapsed and score path reaches satisfied"
        )


# ---------------------------------------------------------------------------
# Bug 5 — Water heater overshoots off-peak minimum during HC
# ---------------------------------------------------------------------------
# Root cause: when a WH reaches is_satisfied() during off-peak (temp >= off_peak_min),
# the min_on_elapsed guard prevented the shutoff if the device had been on for less
# than min_on_minutes. The physical heating element then kept running to wh_temp_target
# (controlled by the WH's own internal thermostat).
#
# Fix: water heaters bypass min_on_elapsed in both dispatch paths when satisfied
# during off-peak hours. The internal thermostat handles cycle protection; Helios
# must cut the switch immediately at the HC threshold.
# ---------------------------------------------------------------------------


def _wh_hc_device(min_on_minutes: int = 30) -> ManagedDevice:
    """Water heater with off-peak slot 22:00–06:00 and an HC minimum of 50°C."""
    return ManagedDevice(
        {
            CONF_DEVICE_NAME:           "Chauffe-eau HC",
            CONF_DEVICE_TYPE:           DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY:  "switch.wh_hc",
            CONF_DEVICE_POWER_W:        2000,
            CONF_WH_TEMP_ENTITY:        "sensor.wh_hc_temp",
            CONF_WH_TEMP_TARGET:        65.0,
            CONF_WH_TEMP_MIN:           50.0,  # off-peak minimum / HC target
            CONF_DEVICE_MIN_ON_MINUTES: min_on_minutes,
        },
        {
            CONF_OFF_PEAK_1_START: "22:00",
            CONF_OFF_PEAK_1_END:   "06:00",
        },
    )


def _wh_hc_hass(temp: float) -> MagicMock:
    hass = MagicMock()
    hass.services = AsyncMock()

    def _state(entity_id):
        s = MagicMock()
        s.state = str(temp) if entity_id == "sensor.wh_hc_temp" else "unavailable"
        return s

    hass.states.get.side_effect = _state
    return hass


class TestWaterHeaterHCOvershoot:
    """WH must stop at off_peak_min during HC even if min_on has not elapsed."""

    @pytest.mark.asyncio
    async def test_wh_cuts_at_off_peak_min_before_min_on_elapsed(self):
        """WH in off-peak: temp >= off_peak_min → turned off immediately (bypass min_on)."""
        import datetime as dt_mod
        device = _wh_hc_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 10 * 60  # only 10 min ago

        mgr = _make_manager([device])
        # temp=51 > wh_temp_min=50 → is_satisfied during HC; score high enough for normal path
        hass = _wh_hc_hass(temp=51.0)

        with patch("custom_components.helios.device_manager.datetime") as mock_dt:
            # Simulate 23:00 — inside the 22:00–06:00 off-peak slot
            _now_t = dt_mod.time(23, 0)
            mock_dt.now.return_value.time.return_value = _now_t
            mock_dt.combine.return_value = dt_mod.datetime(2024, 1, 1, 23, 0, 0)
            await mgr.async_dispatch(hass, _score(global_score=0.9, surplus_w=0.0, dispatch_threshold=0.4))

        assert device.is_on is False, (
            "WH must be turned off at off-peak minimum regardless of min_on_minutes"
        )

    @pytest.mark.asyncio
    async def test_wh_stays_on_below_off_peak_min(self):
        """WH in off-peak: temp < off_peak_min → stays ON (not satisfied yet)."""
        import datetime as dt_mod
        device = _wh_hc_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 10 * 60

        mgr = _make_manager([device])
        hass = _wh_hc_hass(temp=45.0)  # below off_peak_min=50 → not satisfied

        with patch("custom_components.helios.device_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_mod.time(23, 0)
            mock_dt.combine.return_value = dt_mod.datetime(2024, 1, 1, 23, 0, 0)
            await mgr.async_dispatch(hass, _score(global_score=0.9, surplus_w=5000.0, dispatch_threshold=0.4))

        assert device.is_on is True, (
            "WH below off-peak minimum must keep running"
        )

    @pytest.mark.asyncio
    async def test_wh_peak_hours_still_respects_min_on(self):
        """WH during peak hours: min_on_elapsed guard still applies (sensor glitch protection)."""
        import datetime as dt_mod
        device = _wh_hc_device(min_on_minutes=30)
        device.is_on = True
        device.turned_on_at = time.time() - 60  # 1 min ago

        mgr = _make_manager([device])
        hass = _wh_hc_hass(temp=66.0)  # above wh_temp_target=65 → satisfied during peak

        with patch("custom_components.helios.device_manager.datetime") as mock_dt:
            # Simulate 14:00 — peak hours (outside 22:00–06:00 slot)
            mock_dt.now.return_value.time.return_value = dt_mod.time(14, 0)
            mock_dt.combine.return_value = dt_mod.datetime(2024, 1, 1, 14, 0, 0)
            await mgr.async_dispatch(hass, _score(global_score=0.9, surplus_w=5000.0, dispatch_threshold=0.4))

        assert device.is_on is True, (
            "During peak hours, min_on_minutes must still be respected (sensor glitch protection)"
        )


# ---------------------------------------------------------------------------
# Bug 4 — helios_on_w double-counted in dispatch budget
# ---------------------------------------------------------------------------
# Root cause: surplus_w received by async_dispatch is already the virtual surplus
# (real_surplus + helios_on_w), computed in coordinator._build_score_input().
# The dispatch loop was also adding helios_on_w a second time to `remaining`,
# giving devices a phantom budget equal to the power already consumed by
# currently-ON Helios devices — potentially causing grid imports.
#
# Fix: remove the second + helios_on_w in device_manager.async_dispatch.
# ---------------------------------------------------------------------------

_WH2_TEMP    = "sensor.wh2_temp"
_WH2_SWITCH  = "switch.wh2"


def _wh2_device(power_w: int, temp_entity: str, switch_entity: str,
                priority: int = 5, min_on_minutes: int = 0) -> ManagedDevice:
    """Water heater factory — temp between floor and target → not satisfied, not must_run."""
    return ManagedDevice(
        {
            CONF_DEVICE_NAME:           f"WH_{power_w}W",
            CONF_DEVICE_TYPE:           DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY:  switch_entity,
            CONF_DEVICE_POWER_W:        power_w,
            CONF_WH_TEMP_ENTITY:        temp_entity,
            CONF_WH_TEMP_TARGET:        61.0,
            CONF_WH_TEMP_MIN:           10.0,
            CONF_DEVICE_MIN_ON_MINUTES: min_on_minutes,
        },
        {},  # no off-peak slots
    )


def _two_wh_hass(temp1: float, temp2: float) -> MagicMock:
    hass = MagicMock()
    hass.services = AsyncMock()

    def _state(entity_id):
        s = MagicMock()
        mapping = {_WH_TEMP: temp1, _WH2_TEMP: temp2}
        s.state = str(mapping.get(entity_id, "unavailable"))
        return s

    hass.states.get.side_effect = _state
    return hass


class TestHeliosOnW_NotDoubleCountedInBudget:
    """surplus_w is already the virtual surplus — helios_on_w must not be added again.

    Scenario: virtual_surplus = 600 W, device1 (400 W, already ON),
    device2 (300 W, OFF).  Total = 700 W > 600 W — only one can run.

    With the bug: remaining = 600 + 400 = 1000 W → both start → 100 W grid import.
    With the fix: remaining = 600 W         → only one runs within budget.
    """

    @pytest.mark.asyncio
    async def test_total_on_power_does_not_exceed_virtual_surplus(self):
        """After dispatch, the sum of ON device powers must not exceed virtual_surplus."""
        VIRTUAL_SURPLUS = 600

        # device1: 400 W, already ON, temp=58 (slightly unsatisfied, low urgency)
        device1 = _wh2_device(400, _WH_TEMP,  _WH_SWITCH,  priority=4)
        device1.is_on       = True
        device1.turned_on_at = time.time() - 3600  # min_on elapsed

        # device2: 300 W, OFF, temp=20 (very unsatisfied → high urgency → higher score)
        device2 = _wh2_device(300, _WH2_TEMP, _WH2_SWITCH, priority=7)
        device2.is_on = False

        mgr  = _make_manager([device1, device2])
        hass = _two_wh_hass(temp1=58.0, temp2=20.0)

        # real_surplus_w = virtual_surplus - device1.power_w (already consuming 400 W)
        await mgr.async_dispatch(
            hass,
            _score(global_score=0.8, surplus_w=VIRTUAL_SURPLUS, bat_w=0.0,
                   real_surplus_w=VIRTUAL_SURPLUS - 400),
        )

        on_power = (400 if device1.is_on else 0) + (300 if device2.is_on else 0)
        assert on_power <= VIRTUAL_SURPLUS, (
            f"Total ON power ({on_power} W) exceeds virtual_surplus ({VIRTUAL_SURPLUS} W) "
            "— helios_on_w was likely double-counted in the dispatch budget."
        )

    @pytest.mark.asyncio
    async def test_single_device_on_fits_exactly_within_virtual_surplus(self):
        """A single ON device whose power equals virtual_surplus must keep running."""
        VIRTUAL_SURPLUS = 400

        device = _wh2_device(400, _WH_TEMP, _WH_SWITCH)
        device.is_on        = True
        device.turned_on_at = time.time() - 3600

        mgr  = _make_manager([device])
        hass = _two_wh_hass(temp1=40.0, temp2=40.0)

        await mgr.async_dispatch(
            hass,
            _score(global_score=0.8, surplus_w=VIRTUAL_SURPLUS, bat_w=0.0),
        )

        assert device.is_on is True, (
            "Device whose power equals virtual_surplus must stay ON — "
            "virtual surplus already accounts for its consumption."
        )
