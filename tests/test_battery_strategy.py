"""Tests for BatteryStrategy.decide()."""
from __future__ import annotations

from datetime import time
from unittest.mock import patch

from custom_components.helios.battery_strategy import BatteryStrategy
from custom_components.helios.const import (
    BATTERY_ACTION_FORCED_CHARGE,
    BATTERY_ACTION_AUTOCONSOMMATION,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strategy(off_peak_1=("22:00", "06:00"), off_peak_2=None):
    cfg = {
        CONF_OFF_PEAK_1_START: off_peak_1[0],
        CONF_OFF_PEAK_1_END:   off_peak_1[1],
    }
    if off_peak_2:
        cfg[CONF_OFF_PEAK_2_START] = off_peak_2[0]
        cfg[CONF_OFF_PEAK_2_END]   = off_peak_2[1]
    return BatteryStrategy(cfg)


def _decide_at(strategy, now: time, next_color: str | None) -> str:
    data = {"tempo_next_color": next_color}
    with patch("custom_components.helios.battery_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = now
        return strategy.decide(data)


# ---------------------------------------------------------------------------
# forced_charge
# ---------------------------------------------------------------------------

class TestForcedCharge:

    def test_hc_night_before_red(self):
        """HC + demain rouge → forced_charge."""
        s = _make_strategy()
        assert _decide_at(s, time(23, 0), next_color="red") == BATTERY_ACTION_FORCED_CHARGE

    def test_hc_early_morning_before_red(self):
        """03:00 en HC + demain rouge → forced_charge."""
        s = _make_strategy()
        assert _decide_at(s, time(3, 0), next_color="red") == BATTERY_ACTION_FORCED_CHARGE

    def test_hc_boundary_start(self):
        """Exactement 22:00 (début HC) + demain rouge → forced_charge."""
        s = _make_strategy()
        assert _decide_at(s, time(22, 0), next_color="red") == BATTERY_ACTION_FORCED_CHARGE


# ---------------------------------------------------------------------------
# autoconsommation
# ---------------------------------------------------------------------------

class TestAutoConsommation:

    def test_hp_before_red(self):
        """HP (14h) + demain rouge → autoconsommation (pas en HC)."""
        s = _make_strategy()
        assert _decide_at(s, time(14, 0), next_color="red") == BATTERY_ACTION_AUTOCONSOMMATION

    def test_hc_next_day_not_red(self):
        """HC + demain blanc → autoconsommation."""
        s = _make_strategy()
        assert _decide_at(s, time(23, 0), next_color="white") == BATTERY_ACTION_AUTOCONSOMMATION

    def test_hc_next_day_blue(self):
        """HC + demain bleu → autoconsommation."""
        s = _make_strategy()
        assert _decide_at(s, time(23, 0), next_color="blue") == BATTERY_ACTION_AUTOCONSOMMATION

    def test_hc_next_day_unknown(self):
        """HC + couleur demain inconnue (None) → autoconsommation."""
        s = _make_strategy()
        assert _decide_at(s, time(23, 0), next_color=None) == BATTERY_ACTION_AUTOCONSOMMATION

    def test_no_off_peak_slots(self):
        """Aucun créneau HC configuré → jamais de charge forcée."""
        s = BatteryStrategy({})
        assert _decide_at(s, time(23, 0), next_color="red") == BATTERY_ACTION_AUTOCONSOMMATION
