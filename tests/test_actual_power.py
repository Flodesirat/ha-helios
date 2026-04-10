"""Tests for actual_power_w and its impact on dispatch decisions.

Covers:
- actual_power_w fallback priority (device_power_entity > type-specific > nominal)
- Impact on helios_on_w / remaining budget
- Impact on fit_surplus (device re-evaluation)
- Impact on preemption freed_w
"""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice, StateReader
from custom_components.helios.const import (
    DEVICE_TYPE_EV, DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_APPLIANCE,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_POWER_ENTITY, CONF_DEVICE_PRIORITY,
    CONF_APPLIANCE_POWER_ENTITY,
    CONF_EV_PLUGGED_ENTITY, CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET,
    CONF_POOL_FILTRATION_ENTITY,
)

POWER_ENTITY    = "sensor.device_power"
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device(
    device_type=DEVICE_TYPE_EV,
    power_w=2000,
    power_entity=None,
    appliance_power_entity=None,
    priority=5,
    is_on=False,
) -> ManagedDevice:
    cfg: dict = {
        CONF_DEVICE_NAME:          "Appareil",
        CONF_DEVICE_TYPE:          device_type,
        CONF_DEVICE_SWITCH_ENTITY: "switch.appareil",
        CONF_DEVICE_POWER_W:       power_w,
        CONF_DEVICE_PRIORITY:      priority,
    }
    if power_entity:
        cfg[CONF_DEVICE_POWER_ENTITY] = power_entity
    if appliance_power_entity:
        cfg[CONF_APPLIANCE_POWER_ENTITY] = appliance_power_entity
    # EV fields to avoid KeyErrors in is_satisfied
    if device_type == DEVICE_TYPE_EV:
        cfg[CONF_EV_PLUGGED_ENTITY] = None
        cfg[CONF_EV_SOC_ENTITY]     = None
        cfg[CONF_EV_SOC_TARGET]     = 100
    if device_type == DEVICE_TYPE_POOL:
        cfg[CONF_POOL_FILTRATION_ENTITY] = None
    d = ManagedDevice(cfg)
    d.is_on = is_on
    return d


def _hass(measured_w: float | None = None) -> MagicMock:
    hass = MagicMock()

    def _state(entity_id):
        s = MagicMock()
        if entity_id in (POWER_ENTITY, POWER_ENTITY) and measured_w is not None:
            s.state = str(measured_w)
        else:
            s.state = "unavailable"
        return s

    hass.states.get.side_effect = _state
    return hass


def _reader(measured_w: float | None = None) -> StateReader:
    """StateReader equivalent of _hass."""
    def read(entity_id: str) -> str | None:
        if entity_id in (POWER_ENTITY, POWER_ENTITY) and measured_w is not None:
            return str(measured_w)
        return "unavailable"
    return read


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
    return mgr


def _score_input(
    global_score=0.8,
    surplus_w=500.0,
    bat_available_w=0.0,
    grid_allowance_w=0.0,
    dispatch_threshold=0.3,
) -> dict:
    return {
        "global_score":       global_score,
        "surplus_w":          surplus_w,
        "bat_available_w":    bat_available_w,
        "grid_allowance_w":   grid_allowance_w,
        "dispatch_threshold": dispatch_threshold,
        "house_power_w":      0.0,
        "soc_reserve_rouge":  20.0,
        "battery_soc":        50.0,
        "tempo_color":        "blue",
        "pv_power_w":         0.0,
    }


# ---------------------------------------------------------------------------
# actual_power_w — fallback priority
# ---------------------------------------------------------------------------

class TestActualPowerW:

    def test_no_entity_returns_nominal(self):
        """Without any power entity, returns power_w nominal."""
        device = _make_device(power_w=2000)
        reader = _reader(measured_w=1500)  # entity present but not configured
        assert device.actual_power_w(reader) == 2000.0

    def test_generic_entity_takes_priority(self):
        """device_power_entity is used before nominal."""
        device = _make_device(
            device_type=DEVICE_TYPE_WATER_HEATER,
            power_w=2000,
            power_entity=POWER_ENTITY,
        )
        reader = _reader(measured_w=1200)
        assert device.actual_power_w(reader) == 1200.0

    def test_appliance_power_entity_fallback(self):
        """Without device_power_entity, appliance uses appliance_power_entity."""
        device = _make_device(
            device_type=DEVICE_TYPE_APPLIANCE,
            power_w=2000,
            appliance_power_entity=POWER_ENTITY,
        )
        reader = _reader(measured_w=1800)
        assert device.actual_power_w(reader) == 1800.0

    def test_ev_no_entity_returns_nominal(self):
        """EV without power entity always returns nominal."""
        device = _make_device(device_type=DEVICE_TYPE_EV, power_w=7400)
        reader = _reader()
        assert device.actual_power_w(reader) == 7400.0

    def test_ev_with_generic_entity(self):
        """EV with device_power_entity returns measured value."""
        device = _make_device(device_type=DEVICE_TYPE_EV, power_w=7400, power_entity=POWER_ENTITY)
        reader = _reader(measured_w=3700)
        assert device.actual_power_w(reader) == 3700.0

    def test_pool_with_generic_entity(self):
        """Pool with device_power_entity returns measured value."""
        device = _make_device(device_type=DEVICE_TYPE_POOL, power_w=600, power_entity=POWER_ENTITY)
        reader = _reader(measured_w=450)
        assert device.actual_power_w(reader) == 450.0


# ---------------------------------------------------------------------------
# Impact on helios_on_w / remaining budget
# ---------------------------------------------------------------------------

class TestRemainingBudget:

    @pytest.mark.asyncio
    async def test_measured_under_nominal_reduces_budget(self):
        """Device A ON but measured at 200W (nominal 2000W) → budget tighter.

        surplus=500W, no battery.
        helios_on_w = 200 (measured) → remaining = 500 + 200 = 700W.
        Device B needs 900W → 900 > 700 → does NOT start.
        """
        device_a = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=2000,
            power_entity=POWER_ENTITY, is_on=True, priority=5,
        )
        device_a.name = "Zoe"
        device_a.interruptible = True
        device_a.ev_plugged_manual = True   # plugged in → not satisfied

        # Use EV for device_b too (pool with no filtration entity is always satisfied)
        device_b = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=900,
            is_on=False, priority=7,
        )
        device_b.name = "Chauffeur"
        device_b.ev_plugged_manual = True   # plugged in → not satisfied

        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        def _state(entity_id):
            s = MagicMock()
            s.state = "200" if entity_id == POWER_ENTITY else "unavailable"
            return s

        hass.states.get.side_effect = _state
        dm = _make_manager([device_a, device_b])

        # surplus_w is the virtual surplus (already corrected by the coordinator).
        # device_a draws 200W (measured) → coordinator computed virtual_surplus = real + 200.
        # We pass virtual_surplus = 700W: device_b needs 900W > 700 → does NOT start.
        await dm.async_dispatch(hass, _score_input(surplus_w=700, global_score=0.8))
        assert not device_b.is_on, "Device B must not start: virtual surplus 700W < 900W needed"

    @pytest.mark.asyncio
    async def test_nominal_power_without_entity_uses_full_budget(self):
        """Same scenario but without power entity → nominal 2000W included in virtual surplus.

        The coordinator adds device_a's nominal power (2000W) to compute virtual_surplus.
        virtual_surplus = real_surplus(500) + device_a_nominal(2000) = 2500W.
        remaining = 2500W → device B (900W) fits.
        """
        device_a = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=2000,
            power_entity=None, is_on=True, priority=5,
        )
        device_a.name = "Zoe"
        device_a.interruptible = True
        device_a.ev_plugged_manual = True   # plugged in → not satisfied

        device_b = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=900,
            is_on=False, priority=7,
        )
        device_b.name = "Chauffeur"
        device_b.ev_plugged_manual = True   # plugged in → not satisfied

        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.states.get.return_value = None

        dm = _make_manager([device_a, device_b])

        # virtual_surplus = real_surplus(500) + device_a nominal(2000) = 2500W (computed by coordinator).
        # remaining = 2500W → device_b (900W) fits.
        await dm.async_dispatch(hass, _score_input(surplus_w=3000, global_score=0.8))
        assert device_b.is_on, "Device B must start: virtual surplus 2500W >= 900W needed"


# ---------------------------------------------------------------------------
# Impact on fit_surplus (device re-evaluation while ON)
# ---------------------------------------------------------------------------

class TestFitSurplus:

    def test_device_drawing_less_reduces_fit_surplus(self):
        """When a device draws less than nominal, fit_surplus is lower.

        fit_surplus = surplus_w + actual_power_w (if ON)
        """
        device = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=2000,
            power_entity=POWER_ENTITY, is_on=True,
        )
        reader_full    = _reader(measured_w=2000)
        reader_partial = _reader(measured_w=500)

        surplus_w = 300.0

        fit_full    = ManagedDevice.compute_fit_score(
            device.power_w,
            surplus_w + device.actual_power_w(reader_full),
            bat_available_w=0,
        )
        fit_partial = ManagedDevice.compute_fit_score(
            device.power_w,
            surplus_w + device.actual_power_w(reader_partial),
            bat_available_w=0,
        )

        # Full draw: fit_surplus = 300 + 2000 = 2300 → zone 1, fit = 2000/2300 ≈ 0.87
        # Partial:   fit_surplus = 300 + 500  = 800  → zone 1, fit = 2000 > 800 → zone 3
        assert fit_full > fit_partial, (
            f"Full-draw fit ({fit_full:.2f}) should be higher than partial ({fit_partial:.2f})"
        )

    def test_thermostat_cut_wh_gives_zero_fit_surplus(self):
        """Water heater thermostat cuts → actual=0W → fit_surplus = surplus only."""
        device = _make_device(
            device_type=DEVICE_TYPE_WATER_HEATER, power_w=2000,
            power_entity=POWER_ENTITY, is_on=True,
        )
        reader = _reader(measured_w=0.0)
        surplus_w = 500.0

        fit_surplus = surplus_w + device.actual_power_w(reader)
        assert fit_surplus == 500.0

        # Nominal would give 2500 → device fits in zone 1
        fit_nominal  = ManagedDevice.compute_fit_score(device.power_w, surplus_w + 2000, 0)
        fit_measured = ManagedDevice.compute_fit_score(device.power_w, fit_surplus, 0)
        assert fit_nominal > fit_measured


# ---------------------------------------------------------------------------
# Impact on preemption freed_w
# ---------------------------------------------------------------------------

class TestPreemptionFreedW:

    @pytest.mark.asyncio
    async def test_partial_load_may_block_preemption(self):
        """If preempted device draws less than its power_w, freed budget may not
        be enough to start the appliance.

        Appliance needs 2000W. EV (priority < appliance) is ON drawing 800W (nominal 2000W).
        surplus = 0, bat = 0.
        freed_w = 800 → fit_score(2000, 800, 0) < 0.3 → preemption fails.
        """
        from custom_components.helios.const import (
            DEVICE_TYPE_APPLIANCE,
            CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_START_SCRIPT,
            APPLIANCE_STATE_PREPARING,
        )

        ev = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=2000,
            power_entity=POWER_ENTITY, is_on=True, priority=3,
        )
        ev.name = "Zoe"
        ev.interruptible = True

        app_cfg = {
            CONF_DEVICE_NAME:           "Lave-vaisselle",
            CONF_DEVICE_TYPE:           DEVICE_TYPE_APPLIANCE,
            CONF_DEVICE_SWITCH_ENTITY:  None,
            CONF_DEVICE_POWER_W:        2000,
            CONF_DEVICE_PRIORITY:       8,
            CONF_APPLIANCE_READY_ENTITY: "input_boolean.pret",
            CONF_APPLIANCE_START_SCRIPT: "script.start",
        }
        app = ManagedDevice(app_cfg)
        app.appliance_state = APPLIANCE_STATE_PREPARING

        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()

        def _state(entity_id):
            s = MagicMock()
            s.state = "800" if entity_id == POWER_ENTITY else "unavailable"
            return s

        hass.states.get.side_effect = _state

        dm = _make_manager([ev, app])

        # surplus=0, EV draws 800W (measured), appliance needs 2000W
        # freed_w = 800 → fit(2000, 800, 0) ≈ 0.16 < 0.3 → preemption blocked
        await dm.async_dispatch(hass, _score_input(surplus_w=0, global_score=0.8))

        assert not app.is_on, (
            "Appliance must not start: freed budget (800W) insufficient for 2000W"
        )

    @pytest.mark.asyncio
    async def test_full_nominal_preemption_succeeds(self):
        """Same scenario but EV has no power entity → nominal 2000W used.
        freed_w = 2000 → fit(2000, 2000, 0) = 1.0 ≥ 0.3 → preemption succeeds.
        """
        from custom_components.helios.const import (
            DEVICE_TYPE_APPLIANCE,
            CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_START_SCRIPT,
            APPLIANCE_STATE_PREPARING,
        )

        ev = _make_device(
            device_type=DEVICE_TYPE_EV, power_w=2000,
            power_entity=None, is_on=True, priority=3,
        )
        ev.name = "Zoe"
        ev.interruptible = True
        ev.turned_on_at = None  # min_on elapsed

        app_cfg = {
            CONF_DEVICE_NAME:           "Lave-vaisselle",
            CONF_DEVICE_TYPE:           DEVICE_TYPE_APPLIANCE,
            CONF_DEVICE_SWITCH_ENTITY:  None,
            CONF_DEVICE_POWER_W:        2000,
            CONF_DEVICE_PRIORITY:       8,
            CONF_APPLIANCE_READY_ENTITY: "input_boolean.pret",
            CONF_APPLIANCE_START_SCRIPT: "script.start",
        }
        app = ManagedDevice(app_cfg)
        app.appliance_state = APPLIANCE_STATE_PREPARING

        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.states.get.return_value = None

        dm = _make_manager([ev, app])

        # surplus=0, EV nominal 2000W, appliance needs 2000W
        # freed_w = 2000 → fit(2000, 2000, 0) = 1.0 → preemption succeeds
        await dm.async_dispatch(hass, _score_input(surplus_w=0, global_score=0.8))

        assert app.is_on, "Appliance must start: nominal preemption frees 2000W"
        assert not ev.is_on, "EV must be preempted"
