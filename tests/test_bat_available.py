"""Tests for _compute_bat_available_w — SOC floor depends on Tempo color.

Correct behaviour:
    - Red day  : use soc_reserve_rouge as the SOC floor → protect battery during HP
    - Blue/white day: use battery_soc_min as the floor → full capacity available
    - Capacity follows a pivot curve (same logic as f_soc):
        [floor → middle] → 0 → 0.3 × max_discharge  (flat ramp, score stays low)
        [middle → top]   → 0.3 → 1.0 × max_discharge (steep ramp)
        > top            → max_discharge
    - battery_power_w is NOT deducted here (handled upstream).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.helios.const import (
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_SOC_MIN,
    DEFAULT_BATTERY_SOC_MAX,
    TEMPO_BLUE,
    TEMPO_WHITE,
    TEMPO_RED,
)


def _pivot_capacity(soc: float, soc_floor: float, max_discharge_w: float,
                    soc_top: float = DEFAULT_BATTERY_SOC_MAX) -> float:
    """Mirror of _compute_bat_available_w pivot formula for test assertions."""
    soc_middle = (soc_floor + soc_top) / 2
    alpha = 0.3
    if soc > soc_top:
        return max_discharge_w
    if soc <= soc_middle:
        return max_discharge_w * ((soc - soc_floor) / (soc_middle - soc_floor)) * alpha
    return max_discharge_w * (alpha + (1 - alpha) * ((soc - soc_middle) / (soc_top - soc_middle)))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_coordinator(
    soc: float,
    tempo_color: str,
    soc_reserve_rouge: float = 85.0,
    soc_min: float = 10.0,
    capacity_kwh: float = 10.0,
    max_discharge_w: float = 3000.0,
    battery_power_w: float | None = None,
) -> MagicMock:
    from custom_components.helios.coordinator import EnergyOptimizerCoordinator

    cfg = {
        CONF_BATTERY_ENABLED:               True,
        CONF_BATTERY_CAPACITY_KWH:          capacity_kwh,
        CONF_BATTERY_SOC_RESERVE_ROUGE:     soc_reserve_rouge,
        CONF_BATTERY_SOC_MIN:               soc_min,
        CONF_BATTERY_MAX_DISCHARGE_POWER_W: max_discharge_w,
    }
    coord = MagicMock(spec=EnergyOptimizerCoordinator)
    coord._cfg = cfg
    coord.battery_soc = soc
    coord.tempo_color = tempo_color
    coord.battery_power_w = battery_power_w
    coord._compute_bat_available_w = (
        EnergyOptimizerCoordinator._compute_bat_available_w.__get__(coord)
    )
    return coord


# ---------------------------------------------------------------------------
# Blue / white day — floor = soc_min
# ---------------------------------------------------------------------------

def test_blue_day_above_soc_min_returns_positive():
    """Blue day, SOC well above soc_min → bat_available_w > 0."""
    coord = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                              soc_reserve_rouge=85.0, soc_min=10.0)
    assert coord._compute_bat_available_w() > 0.0


def test_blue_day_uses_soc_min_as_floor():
    """Blue day: pivot curve with soc_min as floor."""
    coord = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                              soc_reserve_rouge=85.0, soc_min=10.0,
                              capacity_kwh=10.0, max_discharge_w=3000.0)
    expected = _pivot_capacity(soc=85.0, soc_floor=10.0, max_discharge_w=3000.0)
    assert coord._compute_bat_available_w() == pytest.approx(expected)


def test_white_day_uses_soc_min_as_floor():
    """White day behaves the same as blue day (same floor logic)."""
    coord = _make_coordinator(soc=60.0, tempo_color=TEMPO_WHITE,
                              soc_reserve_rouge=85.0, soc_min=10.0,
                              capacity_kwh=10.0, max_discharge_w=3000.0)
    expected = _pivot_capacity(soc=60.0, soc_floor=10.0, max_discharge_w=3000.0)
    assert coord._compute_bat_available_w() == pytest.approx(expected)


def test_blue_day_at_soc_min_returns_zero():
    """Blue day, SOC == soc_min → nothing usable."""
    coord = _make_coordinator(soc=10.0, tempo_color=TEMPO_BLUE,
                              soc_reserve_rouge=85.0, soc_min=10.0)
    assert coord._compute_bat_available_w() == 0.0


def test_blue_day_below_soc_min_returns_zero():
    """Blue day, SOC < soc_min → still 0 (never discharge below minimum)."""
    coord = _make_coordinator(soc=5.0, tempo_color=TEMPO_BLUE,
                              soc_reserve_rouge=85.0, soc_min=10.0)
    assert coord._compute_bat_available_w() == 0.0


# ---------------------------------------------------------------------------
# Red day — floor = soc_reserve_rouge
# ---------------------------------------------------------------------------

def test_red_day_at_reserve_returns_zero():
    """Red day, SOC == soc_reserve_rouge → battery is protected, returns 0."""
    coord = _make_coordinator(soc=85.0, tempo_color=TEMPO_RED,
                              soc_reserve_rouge=85.0, soc_min=10.0)
    assert coord._compute_bat_available_w() == 0.0


def test_red_day_above_reserve_returns_positive():
    """Red day, SOC = 90% > reserve 85% → pivot curve with soc_reserve_rouge as floor."""
    coord = _make_coordinator(soc=90.0, tempo_color=TEMPO_RED,
                              soc_reserve_rouge=85.0, soc_min=10.0,
                              capacity_kwh=10.0, max_discharge_w=3000.0)
    expected = _pivot_capacity(soc=90.0, soc_floor=85.0, max_discharge_w=3000.0)
    assert coord._compute_bat_available_w() == pytest.approx(expected)


def test_red_day_below_reserve_returns_zero():
    """Red day, SOC < soc_reserve_rouge → fully protected."""
    coord = _make_coordinator(soc=70.0, tempo_color=TEMPO_RED,
                              soc_reserve_rouge=85.0, soc_min=10.0)
    assert coord._compute_bat_available_w() == 0.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_battery_disabled_returns_zero():
    from custom_components.helios.coordinator import EnergyOptimizerCoordinator
    coord = MagicMock(spec=EnergyOptimizerCoordinator)
    coord._cfg = {CONF_BATTERY_ENABLED: False}
    coord.battery_soc = 90.0
    coord.tempo_color = TEMPO_BLUE
    coord._compute_bat_available_w = (
        EnergyOptimizerCoordinator._compute_bat_available_w.__get__(coord)
    )
    assert coord._compute_bat_available_w() == 0.0


def test_soc_none_returns_zero():
    coord = _make_coordinator(soc=None, tempo_color=TEMPO_BLUE)
    assert coord._compute_bat_available_w() == 0.0


def test_max_discharge_caps_result():
    """Result is capped at max_discharge_w even when energy-based value is higher."""
    coord = _make_coordinator(soc=100.0, tempo_color=TEMPO_BLUE,
                              soc_min=10.0, capacity_kwh=20.0,
                              max_discharge_w=500.0)
    assert coord._compute_bat_available_w() == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# Current discharge deduction
# ---------------------------------------------------------------------------

def test_current_discharge_does_not_affect_available():
    """battery_power_w is not deducted in _compute_bat_available_w (handled upstream)."""
    coord_discharging = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                                          soc_min=10.0, capacity_kwh=10.0,
                                          max_discharge_w=3000.0,
                                          battery_power_w=500.0)
    coord_idle = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                                   soc_min=10.0, capacity_kwh=10.0,
                                   max_discharge_w=3000.0,
                                   battery_power_w=None)
    assert coord_discharging._compute_bat_available_w() == pytest.approx(
        coord_idle._compute_bat_available_w()
    )


def test_low_soc_above_floor_returns_low_capacity():
    """SOC just above floor stays in the flat ramp zone → low capacity returned."""
    coord = _make_coordinator(soc=50.0, tempo_color=TEMPO_BLUE,
                              soc_min=10.0, capacity_kwh=10.0,
                              max_discharge_w=3000.0)
    expected = _pivot_capacity(soc=50.0, soc_floor=10.0, max_discharge_w=3000.0)
    assert coord._compute_bat_available_w() == pytest.approx(expected)
    assert coord._compute_bat_available_w() < 3000.0 * 0.3  # still in flat zone


def test_charging_does_not_reduce_available():
    """A negative battery_power_w (charging) must not affect bat_available_w."""
    coord_no_bat = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                                     soc_min=10.0, capacity_kwh=10.0,
                                     max_discharge_w=3000.0,
                                     battery_power_w=None)
    coord_charging = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                                       soc_min=10.0, capacity_kwh=10.0,
                                       max_discharge_w=3000.0,
                                       battery_power_w=-800.0)  # negative = charging
    assert coord_charging._compute_bat_available_w() == pytest.approx(
        coord_no_bat._compute_bat_available_w()
    )


def test_no_battery_power_sensor_unchanged():
    """battery_power_w=None → pivot-based capacity, no deduction."""
    coord = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                              soc_min=10.0, capacity_kwh=10.0,
                              max_discharge_w=3000.0,
                              battery_power_w=None)
    expected = _pivot_capacity(soc=85.0, soc_floor=10.0, max_discharge_w=3000.0)
    assert coord._compute_bat_available_w() == pytest.approx(expected)
