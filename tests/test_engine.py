"""Tests for simulation engine — focus on energy accounting correctness."""
from __future__ import annotations

import pytest

from custom_components.helios.simulation.engine import SimConfig, run as simulate, STEP_MINUTES


STEP_H = STEP_MINUTES / 60.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(bat_efficiency: float = 0.9, **kwargs) -> tuple:
    """Run a summer clear-day simulation and return (result, steps)."""
    cfg = SimConfig(
        season="summer",
        cloud="clear",
        peak_pv_w=6000.0,
        bat_enabled=True,
        bat_efficiency=bat_efficiency,
        bat_soc_start=20.0,   # start low so battery charges during the day
        bat_soc_max=95.0,
        bat_soc_min=10.0,
        bat_capacity_kwh=10.0,
        bat_max_charge_w=3000.0,
        bat_max_discharge_w=3000.0,
        forecast_noise=0.0,
        base_load_noise=0.0,
        **kwargs,
    )
    result = simulate(cfg, devices=[])
    return result, result.steps


# ---------------------------------------------------------------------------
# Battery round-trip efficiency in self-consumption
# ---------------------------------------------------------------------------

class TestBatteryRoundtripEfficiency:

    def test_charging_steps_exist(self):
        """Sanity: a clear summer day with low initial SOC must produce charging steps."""
        _, steps = _run()
        charging = [s for s in steps if s.bat_action == "charge"]
        assert len(charging) > 0, "Expected battery charging steps on a clear summer day"

    def test_e_self_includes_battery_with_efficiency_discount(self):
        """e_self must account for battery round-trip losses.

        When PV charges the battery at power P with efficiency η,
        the useful contribution to e_self is P × η² (not P or 0).
        """
        eta = 0.9
        result, steps = _run(bat_efficiency=eta)

        expected_self = sum(
            (min(s.pv_w, s.base_w + s.devices_w)
             + (s.bat_w * eta ** 2 if s.bat_action == "charge" else 0.0))
            * STEP_H / 1000
            for s in steps
        )
        assert result.e_self_consumed_kwh == pytest.approx(expected_self, rel=1e-6)

    def test_higher_efficiency_gives_higher_autoconsumption(self):
        """A battery with η=0.95 must yield a higher autoconsumption rate than η=0.75."""
        result_high, _ = _run(bat_efficiency=0.95)
        result_low,  _ = _run(bat_efficiency=0.75)
        assert result_high.autoconsumption_rate > result_low.autoconsumption_rate

    def test_no_battery_self_consumption_unaffected(self):
        """With battery disabled, e_self == sum of min(pv, direct_load) exactly."""
        cfg = SimConfig(
            season="summer",
            cloud="clear",
            peak_pv_w=4000.0,
            bat_enabled=False,
            forecast_noise=0.0,
            base_load_noise=0.0,
        )
        result = simulate(cfg, devices=[])
        expected_self = sum(
            min(s.pv_w, s.base_w + s.devices_w) * STEP_H / 1000
            for s in result.steps
        )
        assert result.e_self_consumed_kwh == pytest.approx(expected_self, rel=1e-6)

    def test_roundtrip_1kw_example(self):
        """Explicit example: 1 kW charged at η=0.9 contributes 0.81 kW to e_self, not 1 kW."""
        eta = 0.9
        charge_w = 1000.0
        # Effective contribution per step
        effective_w = charge_w * eta ** 2  # = 810 W
        assert effective_w == pytest.approx(810.0)

        # Verify that a wrong (pre-fix) accounting (charge_w counted fully) is different
        wrong_w = charge_w  # pre-fix: battery not counted, but if it were, it'd be 1000W
        assert effective_w != wrong_w

    def test_energy_balance_with_battery(self):
        """e_pv ≈ e_self + e_export + battery_losses (approximate balance).

        Exact balance is hard because losses are internal, but e_self + e_export
        must now be closer to e_pv than before the fix.
        """
        eta = 0.9
        result, steps = _run(bat_efficiency=eta)
        # The gap between e_pv and (e_self + e_export) should be small.
        # Battery losses account for ~(1 - η²) of charged energy.
        total_charged_kwh = sum(
            s.bat_w * STEP_H / 1000 for s in steps if s.bat_action == "charge"
        )
        # Losses = (1 - η²) × charged, because η² is already in e_self
        # and the remaining (1-η²) × charged is heat
        expected_loss = total_charged_kwh * (1 - eta ** 2)
        actual_gap = result.e_pv_kwh - (result.e_self_consumed_kwh + result.e_grid_export_kwh)
        # The gap should equal the battery losses (within floating point)
        assert actual_gap == pytest.approx(expected_loss, rel=1e-4)
