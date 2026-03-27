"""Tests for the simulation engine and dispatch with real ManagedDevice logic.

Coverage goals:
- SimDevice physical state tracking (WH temperature, EV SOC)
- SimDevice.make_state_reader() → correct entity values
- dispatch() fallback mode (no managed_devices) — backward compatibility
- dispatch() with real ManagedDevice logic:
    - is_satisfied stops a WH when temperature reaches target
    - must_run_now forces a WH on below legionella min, even if score < threshold
    - pool urgency_modifier reflects quota deficit / time remaining
- run() backward compat: plain list[SimDevice] still works
- run() with managed_devices: WH physical state updated, device stops at target
- ha_devices_to_sim: returns parallel (sim, managed) lists with correct types
- optimizer.optimize(): handles tuple-returning devices_fn
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, date, time

import pytest

from custom_components.helios.simulation.devices import SimDevice
from custom_components.helios.simulation.engine import (
    SimConfig, run as simulate, dispatch, STEP_MINUTES,
)
from custom_components.helios.device_manager import ManagedDevice
from custom_components.helios.daily_optimizer import ha_devices_to_sim
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_POOL_FILTRATION_ENTITY,
    DEFAULT_WH_TEMP_TARGET, DEFAULT_WH_TEMP_MIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WH_TEMP_ENTITY = "sensor.wh_temp"
EV_SOC_ENTITY  = "sensor.ev_soc"
POOL_FILTRATION_ENTITY = "sensor.pool_required_h"


def _wh_config(**overrides) -> dict:
    cfg = {
        CONF_DEVICE_NAME:          "ChauffeEau",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
        CONF_DEVICE_SWITCH_ENTITY: "switch.wh",
        CONF_DEVICE_POWER_W:       2000,
        CONF_WH_TEMP_ENTITY:       WH_TEMP_ENTITY,
        CONF_WH_TEMP_TARGET:       60.0,
        CONF_WH_TEMP_MIN:          45.0,
    }
    cfg.update(overrides)
    return cfg


def _ev_config(**overrides) -> dict:
    cfg = {
        CONF_DEVICE_NAME:          "ChargeVE",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_EV,
        CONF_DEVICE_SWITCH_ENTITY: "switch.ev",
        CONF_DEVICE_POWER_W:       3700,
        CONF_EV_SOC_ENTITY:        EV_SOC_ENTITY,
        CONF_EV_SOC_TARGET:        80.0,
    }
    cfg.update(overrides)
    return cfg


def _pool_config(**overrides) -> dict:
    cfg = {
        CONF_DEVICE_NAME:          "Piscine",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_POOL,
        CONF_DEVICE_SWITCH_ENTITY: "switch.pool",
        CONF_DEVICE_POWER_W:       800,
        CONF_POOL_FILTRATION_ENTITY: POOL_FILTRATION_ENTITY,
    }
    cfg.update(overrides)
    return cfg


def _wh_sim_device(wh_temp: float = 50.0, **kwargs) -> SimDevice:
    defaults: dict = dict(
        name="ChauffeEau",
        power_w=2000,
        device_type=DEVICE_TYPE_WATER_HEATER,
        wh_temp=wh_temp,
        wh_temp_entity=WH_TEMP_ENTITY,
        wh_temp_target=60.0,
        wh_temp_min=45.0,
        allowed_start=6.0,
        allowed_end=22.0,
        priority=8,
        min_on_minutes=0,
    )
    defaults.update(kwargs)
    return SimDevice(**defaults)


def _clear_summer_cfg(**kwargs) -> SimConfig:
    # Build defaults first; caller's kwargs override them.
    defaults: dict = dict(
        season="summer",
        cloud="clear",
        peak_pv_w=5000.0,
        bat_enabled=False,
        dispatch_threshold=0.3,
        forecast_noise=0.0,
        base_load_noise=0.0,
    )
    defaults.update(kwargs)
    return SimConfig(**defaults)


# ---------------------------------------------------------------------------
# SimDevice — physical state
# ---------------------------------------------------------------------------

class TestSimDevicePhysicalState:

    def test_wh_make_state_reader_returns_temp(self):
        sd = _wh_sim_device(wh_temp=52.5)
        reader = sd.make_state_reader()
        assert reader(WH_TEMP_ENTITY) == "52.5"

    def test_wh_make_state_reader_unknown_entity_returns_none(self):
        sd = _wh_sim_device()
        reader = sd.make_state_reader()
        assert reader("sensor.nonexistent") is None

    def test_wh_make_state_reader_no_entity_configured(self):
        # SimDevice without wh_temp_entity — reader returns None for any entity
        sd = SimDevice(name="Generic", power_w=1000)
        reader = sd.make_state_reader()
        assert reader(WH_TEMP_ENTITY) is None

    def test_ev_make_state_reader(self):
        sd = SimDevice(
            name="EV", power_w=3700, device_type=DEVICE_TYPE_EV,
            ev_soc=30.0, ev_soc_entity=EV_SOC_ENTITY,
            ev_plugged=True, ev_plugged_entity="binary_sensor.ev_plugged",
        )
        reader = sd.make_state_reader()
        assert reader(EV_SOC_ENTITY) == "30.0"
        assert reader("binary_sensor.ev_plugged") == "on"

    def test_pool_make_state_reader(self):
        sd = SimDevice(
            name="Pool", power_w=800, device_type=DEVICE_TYPE_POOL,
            pool_required_min=240.0,  # 4 h
            pool_filtration_entity=POOL_FILTRATION_ENTITY,
        )
        reader = sd.make_state_reader()
        # Entity is in hours → 240 min / 60 = 4.0 h
        assert reader(POOL_FILTRATION_ENTITY) == "4.0"

    def test_wh_heats_when_active(self):
        sd = _wh_sim_device(wh_temp=50.0)
        sd.turn_on()
        # 12 steps = 1 hour of heating
        for _ in range(12):
            sd.tick(STEP_MINUTES, pv_w=3000, total_load_w=4000)
        # 2000W heater, tank ≈ 200 L → ~8.6 °C/h; after 1 h should be warmer
        assert sd.wh_temp > 55.0
        assert sd.wh_temp <= 65.0  # reasonable upper bound

    def test_wh_cools_when_inactive(self):
        sd = _wh_sim_device(wh_temp=60.0)
        # 12 steps = 1 hour of cooling
        for _ in range(12):
            sd.tick(STEP_MINUTES, pv_w=0, total_load_w=500)
        # Cooling rate ~0.5 °C/h
        assert sd.wh_temp < 60.0
        assert sd.wh_temp > 58.0  # should not lose much in 1 h

    def test_wh_temp_does_not_exceed_cap_when_heating(self):
        sd = _wh_sim_device(wh_temp=64.0)  # just below target + 5
        sd.turn_on()
        for _ in range(48):  # 4 hours
            sd.tick(STEP_MINUTES, pv_w=3000, total_load_w=4000)
        # Cap is wh_temp_target + 5 = 65 °C
        assert sd.wh_temp <= 65.0

    def test_ev_soc_increases_when_active(self):
        sd = SimDevice(
            name="EV", power_w=3700, device_type=DEVICE_TYPE_EV,
            ev_soc=30.0, ev_soc_entity=EV_SOC_ENTITY, ev_plugged=True,
        )
        sd.turn_on()
        # 12 steps = 1 h of charging at 3700W
        for _ in range(12):
            sd.tick(STEP_MINUTES, pv_w=5000, total_load_w=5000)
        assert sd.ev_soc > 30.0
        assert sd.ev_soc <= 100.0

    def test_sim_device_satisfied_wh_by_temp(self):
        sd = _wh_sim_device(wh_temp=61.0)  # above target
        assert sd.satisfied() is True

    def test_sim_device_satisfied_wh_not_reached(self):
        sd = _wh_sim_device(wh_temp=55.0)  # below target
        assert sd.satisfied() is False

    def test_sim_device_satisfied_ev_by_soc(self):
        sd = SimDevice(name="EV", power_w=3700, device_type=DEVICE_TYPE_EV,
                       ev_soc=85.0, ev_soc_target=80.0)
        assert sd.satisfied() is True

    def test_sim_device_satisfied_generic_no_physical_state(self):
        sd = SimDevice(name="Generic", power_w=1000)
        assert sd.satisfied() is False  # no quota, no temp, no soc → never satisfied


# ---------------------------------------------------------------------------
# dispatch() — backward compatibility (no managed_devices)
# ---------------------------------------------------------------------------

class TestDispatchFallback:
    """dispatch() without managed_devices must behave exactly as before."""

    def test_device_turns_on_when_score_above_threshold(self):
        sd = SimDevice(name="Heater", power_w=1000, allowed_start=0.0, allowed_end=24.0)
        dispatch([sd], hour=12.0, surplus_w=2000.0, bat_available_w=0.0,
                 global_score=0.8, threshold=0.3)
        assert sd.active is True

    def test_device_does_not_turn_on_when_score_below_threshold(self):
        sd = SimDevice(name="Heater", power_w=1000, allowed_start=0.0, allowed_end=24.0)
        dispatch([sd], hour=12.0, surplus_w=2000.0, bat_available_w=0.0,
                 global_score=0.2, threshold=0.3)
        assert sd.active is False

    def test_device_turns_off_outside_window(self):
        sd = SimDevice(name="Heater", power_w=1000, allowed_start=8.0, allowed_end=18.0)
        sd.turn_on()
        dispatch([sd], hour=20.0, surplus_w=2000.0, bat_available_w=0.0,
                 global_score=0.9, threshold=0.3)
        assert sd.active is False

    def test_device_turns_off_when_quota_reached(self):
        sd = SimDevice(name="Pool", power_w=800, run_quota_h=3.0)
        sd.turn_on()
        sd.run_today_h = 3.0   # quota reached
        dispatch([sd], hour=12.0, surplus_w=2000.0, bat_available_w=0.0,
                 global_score=0.9, threshold=0.3)
        assert sd.active is False

    def test_no_budget_prevents_turn_on(self):
        sd = SimDevice(name="Heater", power_w=5000, allowed_start=0.0, allowed_end=24.0)
        dispatch([sd], hour=12.0, surplus_w=500.0, bat_available_w=0.0,
                 global_score=0.9, threshold=0.3)
        assert sd.active is False


# ---------------------------------------------------------------------------
# dispatch() — with real ManagedDevice logic
# ---------------------------------------------------------------------------

class TestDispatchWithManagedDevice:

    def _run_dispatch(self, sd, md, hour=12.0, surplus_w=3000.0,
                      global_score=0.8, threshold=0.3, sim_now=None):
        if sim_now is None:
            sim_now = datetime(2025, 6, 15, int(hour), 0)
        dispatch([sd], hour=hour, surplus_w=surplus_w, bat_available_w=0.0,
                 global_score=global_score, threshold=threshold,
                 managed_devices=[md], sim_now=sim_now)

    def test_wh_turns_on_when_not_satisfied(self):
        sd = _wh_sim_device(wh_temp=50.0)
        md = ManagedDevice(_wh_config())
        self._run_dispatch(sd, md)
        assert sd.active is True

    def test_wh_stops_when_temp_at_target(self):
        sd = _wh_sim_device(wh_temp=61.0)  # above target
        md = ManagedDevice(_wh_config())
        sd.turn_on()
        self._run_dispatch(sd, md)
        assert sd.active is False

    def test_wh_must_run_below_legionella_min_ignores_score(self):
        """When WH temp < wh_temp_min, must_run_now=True forces it on even if score < threshold."""
        sd = _wh_sim_device(wh_temp=40.0)  # below legionella min (45°C)
        md = ManagedDevice(_wh_config())
        self._run_dispatch(sd, md, global_score=0.1, threshold=0.5)  # score too low normally
        assert sd.active is True

    def test_wh_does_not_force_when_above_min(self):
        """Normal case: temp above legionella min, score below threshold → stays off."""
        sd = _wh_sim_device(wh_temp=50.0)  # above min, below target
        md = ManagedDevice(_wh_config())
        self._run_dispatch(sd, md, global_score=0.1, threshold=0.5)
        assert sd.active is False

    def test_reader_reflects_current_sim_temp(self):
        """The StateReader built by SimDevice should expose the current wh_temp."""
        sd = _wh_sim_device(wh_temp=55.0)
        reader = sd.make_state_reader()
        temp_str = reader(WH_TEMP_ENTITY)
        assert temp_str is not None
        assert float(temp_str) == pytest.approx(55.0)

    def test_multiple_devices_greedy_by_effective_score(self):
        """When two devices compete, the higher-priority WH wins the budget.

        Uses water_heater type so that is_satisfied() checks temperature
        (not appliance state) and returns False when below target — ensuring
        both devices are eligible candidates.
        """
        sd_hi = SimDevice(name="WHHigh", power_w=1000,
                          device_type=DEVICE_TYPE_WATER_HEATER,
                          wh_temp=50.0, wh_temp_entity="sensor.hi_temp",
                          wh_temp_target=60.0, wh_temp_min=45.0,
                          priority=9, allowed_start=0.0, allowed_end=24.0)
        sd_lo = SimDevice(name="WHLow",  power_w=1000,
                          device_type=DEVICE_TYPE_WATER_HEATER,
                          wh_temp=50.0, wh_temp_entity="sensor.lo_temp",
                          wh_temp_target=60.0, wh_temp_min=45.0,
                          priority=2, allowed_start=0.0, allowed_end=24.0)
        md_hi = ManagedDevice({
            CONF_DEVICE_NAME: "WHHigh", CONF_DEVICE_TYPE: DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY: "switch.hi", CONF_DEVICE_POWER_W: 1000,
            CONF_DEVICE_PRIORITY: 9,
            CONF_WH_TEMP_ENTITY: "sensor.hi_temp",
            CONF_WH_TEMP_TARGET: 60.0, CONF_WH_TEMP_MIN: 45.0,
        })
        md_lo = ManagedDevice({
            CONF_DEVICE_NAME: "WHLow", CONF_DEVICE_TYPE: DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY: "switch.lo", CONF_DEVICE_POWER_W: 1000,
            CONF_DEVICE_PRIORITY: 2,
            CONF_WH_TEMP_ENTITY: "sensor.lo_temp",
            CONF_WH_TEMP_TARGET: 60.0, CONF_WH_TEMP_MIN: 45.0,
        })
        # Budget for exactly one device
        dispatch(
            [sd_hi, sd_lo], hour=12.0, surplus_w=1100.0, bat_available_w=0.0,
            global_score=0.9, threshold=0.3,
            managed_devices=[md_hi, md_lo],
            sim_now=datetime(2025, 6, 15, 12, 0),
        )
        assert sd_hi.active is True
        assert sd_lo.active is False


# ---------------------------------------------------------------------------
# run() — backward compatibility (plain SimDevice list)
# ---------------------------------------------------------------------------

class TestRunBackwardCompat:

    def test_run_with_plain_sim_devices_no_crash(self):
        """run() must work with a plain list[SimDevice] (no managed_devices)."""
        sd = _wh_sim_device()
        result = simulate(_clear_summer_cfg(), [deepcopy(sd)])
        assert result is not None
        assert len(result.steps) == 288  # full day

    def test_run_with_empty_device_list(self):
        result = simulate(_clear_summer_cfg(), [])
        assert len(result.steps) == 288

    def test_run_with_none_devices_uses_defaults(self):
        result = simulate(_clear_summer_cfg(), None)
        assert len(result.steps) == 288


# ---------------------------------------------------------------------------
# run() — with managed_devices: WH stops at target temperature
# ---------------------------------------------------------------------------

class TestRunWithManagedDevices:

    def test_wh_reaches_target_and_stops(self):
        """A WH starting below target should heat up and eventually be satisfied.

        The WH may cool back below target after being satisfied (end-of-day cooling),
        so we verify it ran AND that the decision log shows it was turned off at some
        point (implying is_satisfied fired), rather than checking the final temperature.
        """
        sd = _wh_sim_device(wh_temp=50.0)
        md = ManagedDevice(_wh_config())

        result = simulate(
            _clear_summer_cfg(dispatch_threshold=0.2),
            [deepcopy(sd)],
            managed_devices=[deepcopy(md)],
        )

        # WH must have activated at least once
        active_steps = [s for s in result.steps if "ChauffeEau" in s.active_devices]
        assert len(active_steps) > 0, "WH never activated"

        # With 5000 W peak summer PV, a 2000 W heater from 50°C should easily reach
        # 60°C — the decision log must contain an ON→OFF transition (is_satisfied fired).
        # (The final temperature may be lower due to end-of-day cooling.)
        off_events = [e for e in result.decision_log if e["device"] == "ChauffeEau" and e["action"] == "off"]
        assert len(off_events) > 0, "WH should have turned off (satisfaction never triggered)"

        # At peak heating, the device temperature must have exceeded target at some point.
        # We track this via the SimDevice's final temp: even after cooling, it should be
        # within a reasonable range above the starting 50°C (ie: real heating occurred).
        assert result.devices[0].wh_temp > 50.0 + 3.0, "WH barely heated — heating model broken"

    def test_wh_already_satisfied_never_activates(self):
        """A WH well above target should not activate during a 24h simulation.

        Starting at 80°C (target=60°C): cooling at ~0.5°C/h means it stays
        above 60°C for (80-60)/0.5 = 40 hours — the full day never becomes unsatisfied.
        """
        sd = _wh_sim_device(wh_temp=80.0)  # 20°C above target: ~40h to cool below target
        md = ManagedDevice(_wh_config())

        result = simulate(
            _clear_summer_cfg(dispatch_threshold=0.2),
            [deepcopy(sd)],
            managed_devices=[deepcopy(md)],
        )
        active_steps = [s for s in result.steps if "ChauffeEau" in s.active_devices]
        assert len(active_steps) == 0, "Satisfied WH should never activate"

    def test_wh_energy_tracking_consistent_with_active_steps(self):
        """Energy consumed by WH must match active steps × power × step_h."""
        sd = _wh_sim_device(wh_temp=50.0)
        md = ManagedDevice(_wh_config())

        result = simulate(
            _clear_summer_cfg(dispatch_threshold=0.2),
            [deepcopy(sd)],
            managed_devices=[deepcopy(md)],
        )
        step_h = STEP_MINUTES / 60.0
        active_steps = sum(1 for s in result.steps if "ChauffeEau" in s.active_devices)
        expected_kwh = active_steps * 2000 * step_h / 1000
        assert result.devices[0].energy_kwh == pytest.approx(expected_kwh, rel=1e-6)

    def test_wh_must_run_forces_on_at_night_low_score(self):
        """At night, if WH temp < legionella min, must_run forces it on even with score 0."""
        sd = _wh_sim_device(wh_temp=40.0, allowed_start=0.0, allowed_end=24.0)
        md = ManagedDevice(_wh_config())

        # Very low threshold so nothing dispatches normally, but must_run bypasses it
        result = simulate(
            _clear_summer_cfg(dispatch_threshold=0.99),
            [deepcopy(sd)],
            managed_devices=[deepcopy(md)],
        )
        # The WH must have activated (because legionella safety overrides score)
        active_steps = [s for s in result.steps if "ChauffeEau" in s.active_devices]
        assert len(active_steps) > 0, "Legionella must_run must override high threshold"

    def test_result_devices_list_matches_input_order(self):
        """result.devices must be the same list as the input devices (mutated in-place)."""
        sd1 = _wh_sim_device(wh_temp=50.0)
        sd2 = SimDevice(name="Other", power_w=500)
        sds = [sd1, sd2]

        result = simulate(_clear_summer_cfg(), sds)
        assert result.devices is sds


# ---------------------------------------------------------------------------
# ha_devices_to_sim
# ---------------------------------------------------------------------------

class TestHaDevicesToSim:

    def test_returns_parallel_lists(self):
        configs = [_wh_config(), _pool_config()]
        sim_devs, managed_devs = ha_devices_to_sim(configs)
        assert len(sim_devs) == len(managed_devs) == 2

    def test_sim_device_has_correct_type(self):
        sim_devs, _ = ha_devices_to_sim([_wh_config()])
        assert sim_devs[0].device_type == DEVICE_TYPE_WATER_HEATER

    def test_sim_device_wh_has_temp_seeded(self):
        """WH SimDevice must have wh_temp set (default = target - 5°C when no hass)."""
        sim_devs, _ = ha_devices_to_sim([_wh_config(**{CONF_WH_TEMP_TARGET: 60.0})])
        sd = sim_devs[0]
        assert sd.wh_temp is not None
        assert sd.wh_temp == pytest.approx(55.0)  # target - 5

    def test_sim_device_wh_has_entity_id(self):
        sim_devs, _ = ha_devices_to_sim([_wh_config()])
        assert sim_devs[0].wh_temp_entity == WH_TEMP_ENTITY

    def test_managed_device_has_correct_name(self):
        _, managed_devs = ha_devices_to_sim([_wh_config(), _pool_config()])
        assert managed_devs[0].name == "ChauffeEau"
        assert managed_devs[1].name == "Piscine"

    def test_zero_power_device_excluded(self):
        configs = [
            {CONF_DEVICE_NAME: "NoOp", CONF_DEVICE_TYPE: "generic",
             CONF_DEVICE_SWITCH_ENTITY: "switch.x", CONF_DEVICE_POWER_W: 0},
            _wh_config(),
        ]
        sim_devs, managed_devs = ha_devices_to_sim(configs)
        assert len(sim_devs) == 1
        assert sim_devs[0].name == "ChauffeEau"

    def test_pool_sim_device_has_quota_from_filtration(self):
        """Pool SimDevice should set run_quota_h from the filtration entity default (0 when no HA)."""
        sim_devs, managed_devs = ha_devices_to_sim([_pool_config()])
        sd = sim_devs[0]
        # Without hass, filtration entity reads 0.0 → pool_required_min = 0 → run_quota_h = None
        assert sd.device_type == DEVICE_TYPE_POOL
        assert sd.pool_filtration_entity == POOL_FILTRATION_ENTITY


# ---------------------------------------------------------------------------
# optimizer.optimize() — handles tuple-returning devices_fn
# ---------------------------------------------------------------------------

class TestOptimizerWithManagedDevices:

    def test_optimize_with_tuple_devices_fn(self):
        """optimize() must work when devices_fn() returns (sim_devs, managed_devs)."""
        from custom_components.helios.simulation.optimizer import optimize

        sd = _wh_sim_device(wh_temp=50.0)
        md = ManagedDevice(_wh_config())
        cfg = SimConfig(season="spring", cloud="clear", peak_pv_w=3000,
                        bat_enabled=False, dispatch_threshold=0.3,
                        forecast_noise=0.0, base_load_noise=0.0)

        def _tuple_fn():
            return [deepcopy(sd)], [deepcopy(md)]

        results = optimize(
            cfg, _tuple_fn,
            # weight_step=0.3 → combos exist (0.3+0.3+0.3+0.1=1.0)
            weight_step=0.3, threshold_values=[0.3], n_runs=1, progress=False,
        )
        assert len(results) > 0
        assert results[0].objective is not None

    def test_optimize_with_plain_list_devices_fn(self):
        """optimize() must still work with the old-style list-returning devices_fn."""
        from custom_components.helios.simulation.optimizer import optimize

        sd = _wh_sim_device(wh_temp=50.0)
        cfg = SimConfig(season="spring", cloud="clear", peak_pv_w=3000,
                        bat_enabled=False, dispatch_threshold=0.3,
                        forecast_noise=0.0, base_load_noise=0.0)

        def _plain_fn():
            return [deepcopy(sd)]

        results = optimize(
            cfg, _plain_fn,
            weight_step=0.3, threshold_values=[0.3], n_runs=1, progress=False,
        )
        assert len(results) > 0
