"""Tests pour le calcul de fit en 3 zones — compute_fit (device_manager)."""
from __future__ import annotations

import pytest

from custom_components.helios.device_manager import compute_fit
from custom_components.helios.managed_device import BatteryDevice
from custom_components.helios.const import (
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_RESERVE_ROUGE, CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_PRIORITY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fit(power_w, remaining, bat_available_w=0.0, grid_allowance_w=0.0):
    return compute_fit(power_w, remaining, bat_available_w, grid_allowance_w)


def _bat(soc_min=20.0, soc_max=95.0, charge_max_w=3000.0, priority=7) -> BatteryDevice:
    return BatteryDevice({
        CONF_BATTERY_SOC_MIN:            soc_min,
        CONF_BATTERY_SOC_MAX:            soc_max,
        CONF_BATTERY_SOC_RESERVE_ROUGE:  80.0,
        CONF_BATTERY_MAX_CHARGE_POWER_W: charge_max_w,
        CONF_BATTERY_PRIORITY:           priority,
    })


# ---------------------------------------------------------------------------
# Zone 1 — power_w ≤ surplus_pur  (surplus_pur = remaining − bat_available_w)
# ---------------------------------------------------------------------------

class TestZone1:

    def test_zone1_fit(self):
        """power_w ≤ surplus_pur → fit = power_w / surplus_pur."""
        # remaining=1000, bat=0 → surplus_pur=1000 ; power=600
        result = _fit(power_w=600, remaining=1000)
        assert result == pytest.approx(0.6)

    def test_zone1_full_surplus(self):
        """power_w == surplus_pur → fit == 1.0."""
        result = _fit(power_w=1000, remaining=1000)
        assert result == pytest.approx(1.0)

    def test_zone1_with_bat_available(self):
        """surplus_pur = remaining − bat_available. power ≤ surplus_pur → zone 1."""
        # remaining=1500, bat=500 → surplus_pur=1000 ; power=500 → zone 1
        result = _fit(power_w=500, remaining=1500, bat_available_w=500)
        assert result == pytest.approx(0.5)

    def test_zero_power_returns_zero(self):
        """power_w ≤ 0 → fit = 0.0."""
        assert _fit(power_w=0, remaining=1000) == pytest.approx(0.0)
        assert _fit(power_w=-100, remaining=1000) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Zone 2 — surplus_pur < power_w ≤ remaining  (battery helps)
# ---------------------------------------------------------------------------

class TestZone2:

    def test_zone2_fit(self):
        """surplus_pur < power_w ≤ remaining → fit ∈ [0.4, 1.0]."""
        # remaining=2000, bat=500 → surplus_pur=1500
        # power=1800 → zone 2 ; bat_used=300/500=0.6 → fit=1-0.6*0.6=0.64
        result = _fit(power_w=1800, remaining=2000, bat_available_w=500)
        assert 0.4 <= result <= 1.0
        assert result == pytest.approx(1.0 - 0.6 * (300 / 500))

    def test_zone2_boundary_at_remaining(self):
        """power_w == remaining → fit == 1.0 − 0.6 == 0.4."""
        # remaining=2000, bat=500 → surplus_pur=1500
        # power=2000 → zone 2 boundary ; bat_used=500/500=1.0 → fit=0.4
        result = _fit(power_w=2000, remaining=2000, bat_available_w=500)
        assert result == pytest.approx(0.4)

    def test_zone2_just_above_surplus_pur(self):
        """One W above surplus_pur → fit starts just below 1.0."""
        # remaining=1000, bat=500 → surplus_pur=500
        # power=501 → zone 2 ; bat_used=1/500=0.002 → fit≈0.9988
        result = _fit(power_w=501, remaining=1000, bat_available_w=500)
        assert result == pytest.approx(1.0 - 0.6 * (1 / 500))
        assert result > 0.9

    def test_zone2_range_decreases_with_power(self):
        """Fit monotonically decreases as power_w increases through zone 2."""
        # remaining=2000, bat=1000 → surplus_pur=1000
        powers = [1100, 1400, 1700, 2000]
        fits = [_fit(p, 2000, bat_available_w=1000) for p in powers]
        for i in range(len(fits) - 1):
            assert fits[i] >= fits[i + 1], (
                f"fit({powers[i]}) < fit({powers[i+1]}): {fits[i]:.3f} < {fits[i+1]:.3f}"
            )


# ---------------------------------------------------------------------------
# Zone 3 — remaining < power_w ≤ remaining + grid_allowance  (grid import)
# ---------------------------------------------------------------------------

class TestZone3:

    def test_zone3_fit(self):
        """remaining < power_w ≤ remaining + grid_allowance → fit ∈ [0.0, 0.4]."""
        # remaining=1000, bat=0, grid=500 ; power=1200 → zone 3
        # import_w=200 ; fit=0.4*(1-200/500)=0.4*0.6=0.24
        result = _fit(power_w=1200, remaining=1000, bat_available_w=0, grid_allowance_w=500)
        assert 0.0 <= result <= 0.4
        assert result == pytest.approx(0.4 * (1 - 200 / 500))

    def test_zone3_boundary_just_above_remaining(self):
        """power_w = remaining + 1 → fit just below 0.4."""
        result = _fit(power_w=1001, remaining=1000, bat_available_w=0, grid_allowance_w=500)
        assert result == pytest.approx(0.4 * (1 - 1 / 500))
        assert result > 0.39

    def test_zone3_absent_on_zero_grid_allowance(self):
        """grid_allowance_w = 0 → tout ce qui dépasse remaining donne fit = 0."""
        result = _fit(power_w=1200, remaining=1000, bat_available_w=0, grid_allowance_w=0)
        assert result == pytest.approx(0.0)

    def test_zone3_boundary_at_full_allowance(self):
        """power_w == remaining + grid_allowance → fit == 0.0."""
        result = _fit(power_w=1500, remaining=1000, bat_available_w=0, grid_allowance_w=500)
        assert result == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Hors budget
# ---------------------------------------------------------------------------

class TestOutOfBudget:

    def test_out_of_budget_no_grid(self):
        """power_w > remaining, grid_allowance=0 → fit == 0.0."""
        assert _fit(power_w=2000, remaining=1000) == pytest.approx(0.0)

    def test_out_of_budget_with_grid(self):
        """power_w > remaining + grid_allowance → fit == 0.0."""
        assert _fit(power_w=2000, remaining=1000, grid_allowance_w=500) == pytest.approx(0.0)

    def test_no_remaining_no_grid(self):
        """remaining=0, bat=0, grid=0 → fit == 0.0 for any positive power."""
        assert _fit(power_w=100, remaining=0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# BatteryDevice — fit toujours 1.0
# ---------------------------------------------------------------------------

class TestBatteryDeviceFit:

    def test_fit_always_one(self):
        """BatteryDevice.fit == 1.0 quelle que soit la situation."""
        bat = _bat()
        bat.update(soc=50.0, tempo_red=False)
        assert bat.fit == pytest.approx(1.0)

    def test_fit_still_one_at_soc_max(self):
        """BatteryDevice.fit reste 1.0 même quand SOC ≈ soc_max."""
        bat = _bat(soc_max=95.0)
        bat.update(soc=94.9, tempo_red=False)
        assert bat.fit == pytest.approx(1.0)

    def test_fit_still_one_on_red_day(self):
        """BatteryDevice.fit reste 1.0 un jour rouge."""
        bat = _bat()
        bat.update(soc=30.0, tempo_red=True)
        assert bat.fit == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Recalcul dynamique après allocation
# ---------------------------------------------------------------------------

class TestDynamicRemainingAfterAllocation:

    def test_dynamic_remaining_after_allocation(self):
        """Après allocation d'un appareil, remaining décrémenté → fit du suivant recalculé.

        Setup :
          remaining = 2000  (surplus=1500 + bat=500)
          bat_available_w = 500
          Device A : power=1000 (zone 1, fit=0.67)
          Device B : power=2000 (zone 2 boundary, fit=0.4)

        Après allocation de A, remaining=1000 :
          Device B : power=2000 > remaining=1000 et pas de grid allowance → fit=0.0
        """
        # Avant allocation
        initial_remaining = 2000
        bat_avail = 500

        fit_b_before = _fit(2000, initial_remaining, bat_avail, grid_allowance_w=0)
        assert fit_b_before == pytest.approx(0.4)  # zone 2 boundary

        # Après allocation de A (power=1000)
        remaining_after_a = initial_remaining - 1000
        fit_b_after = _fit(2000, remaining_after_a, bat_avail, grid_allowance_w=0)
        assert fit_b_after == pytest.approx(0.0)  # hors budget (pas de grid allowance)

        assert fit_b_after < fit_b_before
