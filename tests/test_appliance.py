"""Tests for the appliance state machine."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_APPLIANCE,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY, CONF_DEVICE_POWER_W,
    CONF_DEVICE_PRIORITY,
    CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_PREPARE_SCRIPT, CONF_APPLIANCE_START_SCRIPT,
    CONF_APPLIANCE_POWER_ENTITY, CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    APPLIANCE_STATE_IDLE, APPLIANCE_STATE_PREPARING, APPLIANCE_STATE_RUNNING, APPLIANCE_STATE_DONE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

READY_ENTITY   = "input_boolean.lave_vaisselle_pret"
PREPARE_SCRIPT = "script.prepare_lave_vaisselle"
START_SCRIPT   = "script.start_lave_vaisselle"
POWER_ENTITY   = "sensor.lave_vaisselle_power"


def _make_device(
    ready_entity=READY_ENTITY,
    prepare_script=PREPARE_SCRIPT,
    start_script=START_SCRIPT,
    power_entity=None,
    cycle_duration_minutes=120,
) -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME:          "Lave-vaisselle",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_APPLIANCE,
        CONF_DEVICE_SWITCH_ENTITY: None,
        CONF_DEVICE_POWER_W:       2000,
        CONF_APPLIANCE_READY_ENTITY:          ready_entity,
        CONF_APPLIANCE_PREPARE_SCRIPT:        prepare_script,
        CONF_APPLIANCE_START_SCRIPT:          start_script,
        CONF_APPLIANCE_POWER_ENTITY:          power_entity,
        CONF_APPLIANCE_CYCLE_DURATION_MINUTES: cycle_duration_minutes,
    })


def _make_dm(device: ManagedDevice) -> DeviceManager:
    dm = DeviceManager.__new__(DeviceManager)
    dm.devices = [device]
    dm.decision_log = MagicMock()
    dm.decision_log.__iter__ = MagicMock(return_value=iter([]))
    return dm


def _make_hass(ready: bool = False, power_w: float = 2000.0) -> MagicMock:
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()

    def _state(entity_id):
        s = MagicMock()
        if entity_id == READY_ENTITY:
            s.state = "on" if ready else "off"
        elif entity_id == POWER_ENTITY:
            s.state = str(power_w)
        else:
            s.state = "unavailable"
        return s

    hass.states.get.side_effect = _state
    return hass


async def _handle(dm, device, hass, global_score=0.8, surplus_w=2500.0, bat_available_w=0.0):
    await dm._async_handle_appliance(hass, device, global_score, surplus_w, bat_available_w)


# ---------------------------------------------------------------------------
# IDLE state
# ---------------------------------------------------------------------------

class TestIdleState:

    @pytest.mark.asyncio
    async def test_stays_idle_when_ready_entity_false(self):
        """Ready entity off → stays IDLE, no script called."""
        device = _make_device()
        dm     = _make_dm(device)
        hass   = _make_hass(ready=False)

        await _handle(dm, device, hass)

        assert device.appliance_state == APPLIANCE_STATE_IDLE
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_transitions_to_preparing_when_ready(self):
        """Ready entity on → transitions to PREPARING, calls prepare script, resets ready entity."""
        device = _make_device()
        dm     = _make_dm(device)
        hass   = _make_hass(ready=True)

        await _handle(dm, device, hass)

        assert device.appliance_state == APPLIANCE_STATE_PREPARING
        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0] == (("script", "turn_on", {"entity_id": PREPARE_SCRIPT}), {"blocking": False})
        assert calls[1] == (("input_boolean", "turn_off", {"entity_id": READY_ENTITY}), {"blocking": False})

    @pytest.mark.asyncio
    async def test_no_prepare_script_still_transitions(self):
        """No prepare_script → transitions to PREPARING, ready entity is still reset."""
        device = _make_device(prepare_script=None)
        dm     = _make_dm(device)
        hass   = _make_hass(ready=True)

        await _handle(dm, device, hass)

        assert device.appliance_state == APPLIANCE_STATE_PREPARING
        hass.services.async_call.assert_called_once_with(
            "input_boolean", "turn_off",
            {"entity_id": READY_ENTITY},
            blocking=False,
        )


# ---------------------------------------------------------------------------
# PREPARING state
# ---------------------------------------------------------------------------

class TestPreparingState:

    @pytest.mark.asyncio
    async def test_stays_preparing_when_score_too_low(self):
        """Score too low → stays PREPARING, no start script."""
        device = _make_device()
        device.appliance_state = APPLIANCE_STATE_PREPARING
        dm   = _make_dm(device)
        hass = _make_hass()

        await _handle(dm, device, hass, global_score=0.2, surplus_w=0.0)

        assert device.appliance_state == APPLIANCE_STATE_PREPARING
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_transitions_to_running_when_conditions_met(self):
        """Good score + surplus → transitions to RUNNING and calls start_script only."""
        device = _make_device()
        device.appliance_state = APPLIANCE_STATE_PREPARING
        dm   = _make_dm(device)
        hass = _make_hass()

        await _handle(dm, device, hass, global_score=0.8, surplus_w=2500.0)

        assert device.appliance_state == APPLIANCE_STATE_RUNNING
        assert device.is_on is True
        # Only start_script is called — prepare_script was already called at IDLE→PREPARING
        hass.services.async_call.assert_called_once_with(
            "script", "turn_on",
            {"entity_id": START_SCRIPT},
            blocking=False,
        )

    @pytest.mark.asyncio
    async def test_prepare_script_not_called_again_at_start(self):
        """When starting from PREPARING, prepare_script must NOT be called again."""
        device = _make_device()
        device.appliance_state = APPLIANCE_STATE_PREPARING
        dm   = _make_dm(device)
        hass = _make_hass()

        await _handle(dm, device, hass, global_score=0.8, surplus_w=2500.0)

        calls = hass.services.async_call.call_args_list
        called_scripts = [c.args[2]["entity_id"] for c in calls]
        assert PREPARE_SCRIPT not in called_scripts
        assert START_SCRIPT in called_scripts


# ---------------------------------------------------------------------------
# Full cycle: IDLE → PREPARING → RUNNING → DONE → IDLE
# ---------------------------------------------------------------------------

class TestFullCycle:

    @pytest.mark.asyncio
    async def test_full_cycle_without_power_entity(self):
        """Complete cycle using elapsed-time detection."""
        import time as _time

        device = _make_device(cycle_duration_minutes=1)  # 1 min for test
        dm     = _make_dm(device)

        # Step 1: user activates ready entity → IDLE → PREPARING + prepare_script
        hass = _make_hass(ready=True)
        await _handle(dm, device, hass)
        assert device.appliance_state == APPLIANCE_STATE_PREPARING

        # Step 2: conditions met → PREPARING → RUNNING + start_script
        hass2 = _make_hass(ready=False)
        await _handle(dm, device, hass2, global_score=0.8, surplus_w=3000.0)
        assert device.appliance_state == APPLIANCE_STATE_RUNNING
        assert device.appliance_cycle_start is not None

        # Step 3: simulate elapsed time > cycle duration → DONE
        device.appliance_cycle_start -= 120  # 2 minutes ago
        await _handle(dm, device, hass2)
        assert device.appliance_state == APPLIANCE_STATE_DONE
        assert device.is_on is False

        # Step 4: DONE → IDLE
        await _handle(dm, device, hass2)
        assert device.appliance_state == APPLIANCE_STATE_IDLE

    @pytest.mark.asyncio
    async def test_script_call_order(self):
        """prepare_script is called at IDLE→PREPARING, start_script at PREPARING→RUNNING."""
        device = _make_device()
        dm     = _make_dm(device)
        all_calls = []

        async def _record_call(domain, service, data, **kwargs):
            all_calls.append(data["entity_id"])

        hass = _make_hass(ready=True)
        hass.services.async_call.side_effect = _record_call

        # IDLE → PREPARING: prepare_script + reset ready entity
        await _handle(dm, device, hass)
        assert all_calls == [PREPARE_SCRIPT, READY_ENTITY]

        # PREPARING → RUNNING
        hass2 = _make_hass(ready=False)
        hass2.services.async_call.side_effect = _record_call
        await _handle(dm, device, hass2, global_score=0.8, surplus_w=3000.0)
        assert all_calls == [PREPARE_SCRIPT, READY_ENTITY, START_SCRIPT]


# ---------------------------------------------------------------------------
# Multiple appliances — budget and priority
# ---------------------------------------------------------------------------

START_SCRIPT_A = "script.start_lave_vaisselle"
START_SCRIPT_B = "script.start_lave_linge"


def _make_two_appliances(priority_a: int = 5, priority_b: int = 5) -> tuple[ManagedDevice, ManagedDevice]:
    """Two appliances, each 2000 W, both in PREPARING state."""
    dev_a = ManagedDevice({
        CONF_DEVICE_NAME:          "Lave-vaisselle",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_APPLIANCE,
        CONF_DEVICE_SWITCH_ENTITY: None,
        CONF_DEVICE_POWER_W:       2000,
        CONF_DEVICE_PRIORITY:      priority_a,
        CONF_APPLIANCE_READY_ENTITY:          READY_ENTITY,
        CONF_APPLIANCE_PREPARE_SCRIPT:        None,
        CONF_APPLIANCE_START_SCRIPT:          START_SCRIPT_A,
        CONF_APPLIANCE_POWER_ENTITY:          None,
        CONF_APPLIANCE_CYCLE_DURATION_MINUTES: 120,
    })
    dev_b = ManagedDevice({
        CONF_DEVICE_NAME:          "Lave-linge",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_APPLIANCE,
        CONF_DEVICE_SWITCH_ENTITY: None,
        CONF_DEVICE_POWER_W:       2000,
        CONF_DEVICE_PRIORITY:      priority_b,
        CONF_APPLIANCE_READY_ENTITY:          "input_boolean.lave_linge_pret",
        CONF_APPLIANCE_PREPARE_SCRIPT:        None,
        CONF_APPLIANCE_START_SCRIPT:          START_SCRIPT_B,
        CONF_APPLIANCE_POWER_ENTITY:          None,
        CONF_APPLIANCE_CYCLE_DURATION_MINUTES: 90,
    })
    dev_a.appliance_state = APPLIANCE_STATE_PREPARING
    dev_b.appliance_state = APPLIANCE_STATE_PREPARING
    return dev_a, dev_b


def _make_dm_two(dev_a: ManagedDevice, dev_b: ManagedDevice) -> DeviceManager:
    dm = DeviceManager.__new__(DeviceManager)
    dm.devices = [dev_a, dev_b]
    dm.decision_log = MagicMock()
    dm.decision_log.__iter__ = MagicMock(return_value=iter([]))
    dm._scan_interval = 5.0
    dm._dispatch_threshold = 0.3
    store = MagicMock()
    store.async_save = AsyncMock()
    dm._store = store
    return dm


async def _dispatch(dm: DeviceManager, hass, surplus_w: float, global_score: float = 0.8):
    """Call async_dispatch with minimal score_input."""
    score_input = {
        "global_score": global_score,
        "surplus_w":    surplus_w,
        "tempo_color":  "blue",
        "battery_soc":  None,
        "bat_action":   "idle",
    }
    await dm.async_dispatch(hass, score_input)


class TestMultipleApplianceBudget:

    @pytest.mark.asyncio
    async def test_only_one_starts_when_budget_insufficient_for_both(self):
        """Surplus = 2500 W, both appliances need 2000 W — only one should start."""
        dev_a, dev_b = _make_two_appliances()
        dm   = _make_dm_two(dev_a, dev_b)
        hass = _make_hass(ready=False)

        await _dispatch(dm, hass, surplus_w=2500.0)

        running = [d for d in [dev_a, dev_b] if d.appliance_state == APPLIANCE_STATE_RUNNING]
        preparing = [d for d in [dev_a, dev_b] if d.appliance_state == APPLIANCE_STATE_PREPARING]
        assert len(running) == 1, "Exactly one appliance should have started"
        assert len(preparing) == 1, "The other appliance should still be PREPARING"

    @pytest.mark.asyncio
    async def test_higher_priority_starts_first(self):
        """When budget is insufficient for both, the higher-priority appliance starts."""
        # dev_a priority=3 (low), dev_b priority=8 (high) — order in dm.devices is [a, b]
        dev_a, dev_b = _make_two_appliances(priority_a=3, priority_b=8)
        dm   = _make_dm_two(dev_a, dev_b)
        hass = _make_hass(ready=False)

        await _dispatch(dm, hass, surplus_w=2500.0)

        assert dev_b.appliance_state == APPLIANCE_STATE_RUNNING, \
            "Higher-priority appliance (lave-linge, p=8) should start first"
        assert dev_a.appliance_state == APPLIANCE_STATE_PREPARING, \
            "Lower-priority appliance (lave-vaisselle, p=3) should remain PREPARING"

    @pytest.mark.asyncio
    async def test_lower_priority_first_in_list_but_higher_priority_wins(self):
        """dm.devices order should not matter — priority takes precedence."""
        # dev_a is first in the list but has lower priority
        dev_a, dev_b = _make_two_appliances(priority_a=2, priority_b=9)
        dm   = _make_dm_two(dev_a, dev_b)
        hass = _make_hass(ready=False)

        await _dispatch(dm, hass, surplus_w=2500.0)

        assert dev_b.appliance_state == APPLIANCE_STATE_RUNNING
        assert dev_a.appliance_state == APPLIANCE_STATE_PREPARING

    @pytest.mark.asyncio
    async def test_second_appliance_starts_next_cycle_when_budget_freed(self):
        """After the first appliance is RUNNING, the second can start on the next cycle
        if there is now enough surplus (e.g. first is drawing from battery, not surplus)."""
        dev_a, dev_b = _make_two_appliances(priority_a=8, priority_b=3)
        dm   = _make_dm_two(dev_a, dev_b)
        hass = _make_hass(ready=False)

        # Cycle 1: only dev_a (higher priority) starts
        await _dispatch(dm, hass, surplus_w=2500.0)
        assert dev_a.appliance_state == APPLIANCE_STATE_RUNNING
        assert dev_b.appliance_state == APPLIANCE_STATE_PREPARING

        # Cycle 2: surplus now 5000 W — enough for the second appliance too
        await _dispatch(dm, hass, surplus_w=5000.0)
        assert dev_b.appliance_state == APPLIANCE_STATE_RUNNING, \
            "Second appliance should start on next cycle when budget allows"

    @pytest.mark.asyncio
    async def test_both_start_when_budget_sufficient(self):
        """Surplus = 5000 W, both appliances need 2000 W — both should start."""
        dev_a, dev_b = _make_two_appliances()
        dm   = _make_dm_two(dev_a, dev_b)
        hass = _make_hass(ready=False)

        await _dispatch(dm, hass, surplus_w=5000.0)

        assert dev_a.appliance_state == APPLIANCE_STATE_RUNNING
        assert dev_b.appliance_state == APPLIANCE_STATE_RUNNING
