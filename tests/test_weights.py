"""Tests for scoring weights logic — ScoringEngine, daily optimizer."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.scoring_engine import ScoringEngine, _W_SURPLUS, _W_TEMPO, _W_SOLAR
from custom_components.helios.daily_optimizer import (
    season_from_date,
    cloud_from_forecast,
)


# ---------------------------------------------------------------------------
# ScoringEngine — fixed weights and score computation
# ---------------------------------------------------------------------------

class TestScoringEngineWeights:
    """ScoringEngine uses fixed module-level weights."""

    def _engine(self) -> ScoringEngine:
        return ScoringEngine({})

    def test_fixed_weights(self):
        """Module constants must match the spec."""
        assert _W_SURPLUS == 0.5
        assert _W_TEMPO   == 0.3
        assert _W_SOLAR   == 0.2

    def test_score_range_always_01(self):
        """Computed score must always stay in [0, 1]."""
        eng = self._engine()
        for surplus in (-500, 0, 250, 1000, 5000):
            for tempo in ("blue", "white", "red", None):
                score = eng.compute({
                    "surplus_w":   surplus,
                    "tempo_color": tempo,
                })
                assert 0.0 <= score <= 1.0, (
                    f"score={score} out of [0,1] for surplus={surplus}, tempo={tempo}"
                )

    def test_high_surplus_blue_raises_score(self):
        """Large PV surplus on a blue day must produce a high score."""
        eng = self._engine()
        score = eng.compute({"surplus_w": 2000, "tempo_color": "blue"})
        assert score >= 0.7, f"Expected score ≥ 0.7, got {score}"

    def test_no_surplus_red_lowers_score(self):
        """No surplus on a red Tempo day must produce a low score."""
        eng = self._engine()
        score = eng.compute({"surplus_w": 0, "tempo_color": "red"})
        assert score <= 0.3, f"Expected score ≤ 0.3, got {score}"


# ---------------------------------------------------------------------------
# ScoringEngine — compute_components()
# ---------------------------------------------------------------------------

class TestComputeComponents:
    """compute_components() returns (f_surplus, f_tempo, f_solar)."""

    def _engine(self) -> ScoringEngine:
        return ScoringEngine({})

    def test_returns_three_values(self):
        eng = self._engine()
        result = eng.compute_components({"surplus_w": 250, "tempo_color": "blue"})
        assert len(result) == 3

    def test_components_in_range(self):
        eng = self._engine()
        for surplus in (0, 250, 1000):
            for tempo in ("blue", "white", "red", None):
                f_s, f_t, f_sol = eng.compute_components({"surplus_w": surplus, "tempo_color": tempo})
                assert 0.0 <= f_s <= 1.0
                assert 0.0 <= f_t <= 1.0
                assert 0.0 <= f_sol <= 1.0

    def test_consistent_with_compute(self):
        """compute() must equal the weighted sum of compute_components()."""
        eng = self._engine()
        data = {"surplus_w": 300, "tempo_color": "white", "hour": 13}
        f_s, f_t, f_sol = eng.compute_components(data)
        expected = round(_W_SURPLUS * f_s + _W_TEMPO * f_t + _W_SOLAR * f_sol, 3)
        assert eng.compute(data) == pytest.approx(expected)

    def test_blue_surplus_components(self):
        """On a blue day with surplus, f_tempo=1.0 and f_surplus > 0."""
        eng = self._engine()
        f_s, f_t, f_sol = eng.compute_components({"surplus_w": 500, "tempo_color": "blue"})
        assert f_t == pytest.approx(1.0)
        assert f_s > 0.0

    def test_red_no_surplus_components(self):
        """On a red day with no surplus, f_tempo=0.0 and f_surplus=0.0."""
        eng = self._engine()
        f_s, f_t, f_sol = eng.compute_components({"surplus_w": 0, "tempo_color": "red"})
        assert f_t == pytest.approx(0.0)
        assert f_s == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# ScoringEngine — _score_surplus() simplified ramp
# ---------------------------------------------------------------------------

class TestSurplusScoring:
    """_score_surplus — single ramp from 0 to (charge_max_w + 500)."""

    def test_zero_surplus_returns_zero(self):
        eng = ScoringEngine({})
        assert eng._score_surplus(0.0) == pytest.approx(0.0)

    def test_negative_surplus_returns_zero(self):
        eng = ScoringEngine({})
        assert eng._score_surplus(-100.0) == pytest.approx(0.0)

    def test_no_battery_ramp_500w(self):
        """Without battery, charge_max_w=0 → ramp 0→500 W."""
        eng = ScoringEngine({})
        assert eng._score_surplus(250.0) == pytest.approx(0.5)
        assert eng._score_surplus(500.0) == pytest.approx(1.0)
        assert eng._score_surplus(1000.0) == pytest.approx(1.0)

    def test_with_battery_ramp_extended(self):
        """With charge_max_w=2000, ramp goes 0→2500 W."""
        from custom_components.helios.const import CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_ENABLED
        eng = ScoringEngine({CONF_BATTERY_ENABLED: True, CONF_BATTERY_MAX_CHARGE_POWER_W: 2000.0})
        assert eng._score_surplus(1250.0) == pytest.approx(0.5)
        assert eng._score_surplus(2500.0) == pytest.approx(1.0)
        assert eng._score_surplus(5000.0) == pytest.approx(1.0)

    def test_monotone_increasing(self):
        """Score must never decrease as surplus rises."""
        eng = ScoringEngine({})
        surpluses = [0, 50, 100, 250, 500, 1000]
        scores = [eng._score_surplus(float(s)) for s in surpluses]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1]


# ---------------------------------------------------------------------------
# ScoringEngine — SOC scoring
# ---------------------------------------------------------------------------

class TestSocScoring:
    """_score_soc parametric curve: monotone increasing, anchored to soc_min/soc_max."""

    def _engine(self, soc_min=10, soc_max=95) -> ScoringEngine:
        return ScoringEngine({
            "battery_soc_min": soc_min,
            "battery_soc_max": soc_max,
        })

    def test_none_returns_neutral(self):
        assert self._engine()._score_soc(None) == pytest.approx(0.5)

    def test_below_soc_min_returns_zero(self):
        eng = self._engine(soc_min=20)
        assert eng._score_soc(0)  == pytest.approx(0.0)
        assert eng._score_soc(20) == pytest.approx(0.0)

    def test_at_soc_max_returns_one(self):
        eng = self._engine(soc_max=95)
        assert eng._score_soc(95)  == pytest.approx(1.0)
        assert eng._score_soc(100) == pytest.approx(1.0)

    def test_monotone_increasing(self):
        """Score must never decrease as SOC rises."""
        eng = self._engine(soc_min=10, soc_max=95)
        socs = list(range(0, 101, 5))
        scores = [eng._score_soc(float(s)) for s in socs]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], (
                f"Score decreased from soc={socs[i]} ({scores[i]:.3f}) "
                f"to soc={socs[i+1]} ({scores[i+1]:.3f})"
            )

    def test_pivot_at_midpoint(self):
        """At the pivot (midpoint), score must be exactly 0.3 (junction flat→steep ramp)."""
        eng = self._engine(soc_min=10, soc_max=90)
        pivot = (10 + 90) / 2.0  # 50.0
        assert eng._score_soc(pivot) == pytest.approx(0.3)

    def test_custom_soc_min_shifts_reserve_zone(self):
        """Raising soc_min must keep score=0 over the wider reserve zone."""
        eng = self._engine(soc_min=30, soc_max=95)
        assert eng._score_soc(29) == pytest.approx(0.0)
        assert eng._score_soc(30) == pytest.approx(0.0)
        assert eng._score_soc(31) > 0.0

    def test_score_range_always_01(self):
        for soc_min, soc_max in [(10, 95), (20, 90), (5, 100)]:
            eng = self._engine(soc_min=soc_min, soc_max=soc_max)
            for soc in range(0, 101):
                s = eng._score_soc(float(soc))
                assert 0.0 <= s <= 1.0, f"soc={soc} → score={s} out of [0,1]"


# ---------------------------------------------------------------------------
# ScoringEngine — forecast scoring
# ---------------------------------------------------------------------------

class TestForecastScoring:
    """_score_solar — Gaussian solar potential centred at 13h, σ=3h.

    f(h) = exp(-((h - 13)² / 18))
    Peak at noon, tapers off morning/evening, near-zero at night.
    No external forecast entity required.
    """

    def _engine(self) -> ScoringEngine:
        return ScoringEngine({})

    def test_peak_at_noon(self):
        """Solar noon (13h) → maximum score 1.0."""
        eng = self._engine()
        assert eng.compute({"hour": 13}) == pytest.approx(
            _W_SURPLUS * 0.0 + _W_TEMPO * 0.5 + _W_SOLAR * 1.0, abs=1e-3
        )

    def test_symmetric_around_noon(self):
        """Score at 10h and 16h should be equal (symmetric Gaussian)."""
        eng = self._engine()
        assert eng.compute({"hour": 10}) == pytest.approx(eng.compute({"hour": 16}), abs=1e-3)

    def test_morning_lower_than_noon(self):
        """8h should score lower than 13h."""
        eng = self._engine()
        assert eng.compute({"hour": 8}) < eng.compute({"hour": 13})

    def test_night_near_zero(self):
        """At 1h (night), solar potential is negligible."""
        eng = self._engine()
        # f_surplus=0, f_tempo=0.5, f_solar≈0 → score ≈ 0.3*0.5 = 0.15
        assert eng.compute({"hour": 1}) < 0.2

    def test_no_hour_defaults_to_noon(self):
        """Missing hour key defaults to 13h → f_solar ≈ 1.0."""
        eng = self._engine()
        f_s, f_t, f_sol = eng.compute_components({})
        assert f_sol == pytest.approx(1.0, abs=1e-3)

    def test_monotone_morning_to_noon(self):
        """f_solar increases from 6h to 13h."""
        eng = self._engine()
        hours = [6, 8, 10, 12, 13]
        scores = [eng.compute_components({"hour": h})[2] for h in hours]
        for i in range(len(scores) - 1):
            assert scores[i] < scores[i + 1], (
                f"f_solar did not increase from {hours[i]}h ({scores[i]:.3f}) "
                f"to {hours[i+1]}h ({scores[i+1]:.3f})"
            )

    def test_score_range_always_01(self):
        """Score is always in [0..1] for any hour."""
        eng = self._engine()
        for hour in range(0, 24):
            s = eng.compute({"hour": hour})
            assert 0.0 <= s <= 1.0, f"hour={hour} → score={s}"


# ---------------------------------------------------------------------------
# daily_optimizer — season_from_date
# ---------------------------------------------------------------------------

class TestSeasonFromDate:
    from datetime import date

    def test_winter(self):
        from datetime import date
        assert season_from_date(date(2026, 1, 15)) == "winter"
        assert season_from_date(date(2026, 12, 21)) == "winter"
        assert season_from_date(date(2026, 2, 28)) == "winter"

    def test_spring(self):
        from datetime import date
        assert season_from_date(date(2026, 3, 1)) == "spring"
        assert season_from_date(date(2026, 5, 31)) == "spring"

    def test_summer(self):
        from datetime import date
        assert season_from_date(date(2026, 6, 21)) == "summer"
        assert season_from_date(date(2026, 8, 15)) == "summer"

    def test_autumn(self):
        from datetime import date
        assert season_from_date(date(2026, 9, 1)) == "autumn"
        assert season_from_date(date(2026, 11, 30)) == "autumn"


# ---------------------------------------------------------------------------
# daily_optimizer — cloud_from_forecast
# ---------------------------------------------------------------------------

class TestCloudFromForecast:

    def test_clear_sky(self):
        assert cloud_from_forecast(23.0, 24.0) == "clear"      # 96%

    def test_partly_cloudy(self):
        assert cloud_from_forecast(13.0, 24.0) == "partly_cloudy"  # 54%

    def test_cloudy(self):
        assert cloud_from_forecast(6.0, 24.0) == "cloudy"      # 25%

    def test_boundary_clear(self):
        assert cloud_from_forecast(18.0, 24.0) == "clear"       # 75% exactly

    def test_boundary_partly_cloudy(self):
        assert cloud_from_forecast(10.8, 24.0) == "partly_cloudy"  # 45% exactly

    def test_zero_theoretical_returns_clear(self):
        """No theoretical production (e.g. night) must not raise."""
        assert cloud_from_forecast(0.0, 0.0) == "clear"

    def test_forecast_higher_than_theoretical(self):
        """Forecast can exceed theoretical (upward revision) → clear."""
        assert cloud_from_forecast(26.0, 24.0) == "clear"
