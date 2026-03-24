"""Tests for _compute_bat_available_w — SOC floor depends on Tempo color.

Correct behaviour:
    - Red day  : use soc_reserve_rouge as the SOC floor → protect battery during HP
    - Blue/white day: use battery_soc_min as the floor → full capacity available
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
    TEMPO_BLUE,
    TEMPO_WHITE,
    TEMPO_RED,
)


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
    """Blue day: usable = (soc - soc_min) / 100 × capacity × 500, capped at max_discharge."""
    coord = _make_coordinator(soc=85.0, tempo_color=TEMPO_BLUE,
                              soc_reserve_rouge=85.0, soc_min=10.0,
                              capacity_kwh=10.0, max_discharge_w=3000.0)
    expected = min((85.0 - 10.0) / 100.0 * 10.0 * 500, 3000.0)  # 3000 W
    assert coord._compute_bat_available_w() == pytest.approx(expected)


def test_white_day_uses_soc_min_as_floor():
    """White day behaves the same as blue day."""
    coord = _make_coordinator(soc=60.0, tempo_color=TEMPO_WHITE,
                              soc_reserve_rouge=85.0, soc_min=10.0,
                              capacity_kwh=10.0, max_discharge_w=3000.0)
    expected = min((60.0 - 10.0) / 100.0 * 10.0 * 500, 3000.0)  # 2500 W
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
    """Red day, SOC = 90% > reserve 85% → 5% usable."""
    coord = _make_coordinator(soc=90.0, tempo_color=TEMPO_RED,
                              soc_reserve_rouge=85.0, soc_min=10.0,
                              capacity_kwh=10.0, max_discharge_w=3000.0)
    expected = (90.0 - 85.0) / 100.0 * 10.0 * 500  # 250 W
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
