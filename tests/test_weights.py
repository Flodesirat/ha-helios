"""Tests for scoring weights logic — ScoringEngine, update_weights, daily optimizer."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.scoring_engine import ScoringEngine
from custom_components.helios.daily_optimizer import (
    season_from_date,
    cloud_from_forecast,
)
from custom_components.helios.const import (
    DEFAULT_WEIGHT_PV_SURPLUS,
    DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC,
    DEFAULT_WEIGHT_FORECAST,
    DEFAULT_OPTIMIZER_ALPHA,
    DEFAULT_DISPATCH_THRESHOLD,
)


# ---------------------------------------------------------------------------
# ScoringEngine — weight application
# ---------------------------------------------------------------------------

class TestScoringEngineWeights:
    """ScoringEngine initialises and applies weights correctly."""

    def _engine(self, **overrides) -> ScoringEngine:
        cfg = {
            "weight_pv_surplus":  DEFAULT_WEIGHT_PV_SURPLUS,
            "weight_tempo":       DEFAULT_WEIGHT_TEMPO,
            "weight_battery_soc": DEFAULT_WEIGHT_BATTERY_SOC,
            "weight_forecast":    DEFAULT_WEIGHT_FORECAST,
        }
        cfg.update(overrides)
        return ScoringEngine(cfg)

    def test_default_weights_loaded(self):
        eng = self._engine()
        assert eng.w_surplus  == DEFAULT_WEIGHT_PV_SURPLUS
        assert eng.w_tempo    == DEFAULT_WEIGHT_TEMPO
        assert eng.w_soc      == DEFAULT_WEIGHT_BATTERY_SOC
        assert eng.w_forecast == DEFAULT_WEIGHT_FORECAST

    def test_update_weights_replaces_all(self):
        eng = self._engine()
        eng.update_weights({
            "weight_pv_surplus":  0.5,
            "weight_tempo":       0.2,
            "weight_battery_soc": 0.2,
            "weight_forecast":    0.1,
        })
        assert eng.w_surplus  == 0.5
        assert eng.w_tempo    == 0.2
        assert eng.w_soc      == 0.2
        assert eng.w_forecast == 0.1

    def test_update_weights_partial(self):
        """Partial update must only change the supplied keys."""
        eng = self._engine()
        eng.update_weights({"weight_pv_surplus": 0.6})
        assert eng.w_surplus  == 0.6
        assert eng.w_tempo    == DEFAULT_WEIGHT_TEMPO      # unchanged
        assert eng.w_soc      == DEFAULT_WEIGHT_BATTERY_SOC
        assert eng.w_forecast == DEFAULT_WEIGHT_FORECAST

    def test_score_range_always_01(self):
        """Computed score must always stay in [0, 1]."""
        eng = self._engine()
        for surplus in (-500, 0, 250, 1000, 5000):
            for tempo in ("blue", "white", "red", None):
                for soc in (0, 20, 50, 80, 100, None):
                    score = eng.compute({
                        "surplus_w":   surplus,
                        "tempo_color": tempo,
                        "battery_soc": soc,
                    })
                    assert 0.0 <= score <= 1.0, (
                        f"score={score} out of [0,1] for surplus={surplus}, "
                        f"tempo={tempo}, soc={soc}"
                    )

    def test_high_surplus_blue_raises_score(self):
        """Large PV surplus on a blue day must produce a high score."""
        eng = self._engine()
        score = eng.compute({"surplus_w": 2000, "tempo_color": "blue", "battery_soc": 50})
        assert score >= 0.7, f"Expected score ≥ 0.7, got {score}"

    def test_no_surplus_red_lowers_score(self):
        """No surplus on a red Tempo day must produce a low score."""
        eng = self._engine()
        score = eng.compute({"surplus_w": 0, "tempo_color": "red", "battery_soc": 10})
        assert score <= 0.3, f"Expected score ≤ 0.3, got {score}"

    def test_surplus_weight_increases_sensitivity(self):
        """Increasing w_surplus must increase the impact of surplus on the score."""
        low_w  = self._engine(weight_pv_surplus=0.1, weight_tempo=0.4,
                              weight_battery_soc=0.4, weight_forecast=0.1)
        high_w = self._engine(weight_pv_surplus=0.8, weight_tempo=0.1,
                              weight_battery_soc=0.1, weight_forecast=0.0)
        data_surplus = {"surplus_w": 2000, "tempo_color": "white", "battery_soc": 50}
        data_no_surplus = {"surplus_w": 0,    "tempo_color": "white", "battery_soc": 50}

        delta_low  = low_w.compute(data_surplus)  - low_w.compute(data_no_surplus)
        delta_high = high_w.compute(data_surplus) - high_w.compute(data_no_surplus)
        assert delta_high > delta_low, (
            "Higher w_surplus should produce larger score delta when surplus changes"
        )

    def test_updated_weights_affect_score(self):
        """Score must change after update_weights is called.

        Uses data where dimensions score differently so weight shifts matter:
          surplus_w=100  → _score_surplus ≈ 0.2  (small surplus)
          tempo="red"    → _score_tempo   = 0.0
          soc=50         → _score_soc     > 0.5  (above pivot with default soc_min/max)
        Raising w_soc to 0.7 must increase the final score.
        """
        eng = self._engine()
        data = {"surplus_w": 100, "tempo_color": "red", "battery_soc": 50}
        score_before = eng.compute(data)

        eng.update_weights({
            "weight_pv_surplus":  0.1,
            "weight_tempo":       0.1,
            "weight_battery_soc": 0.7,
            "weight_forecast":    0.1,
        })
        score_after = eng.compute(data)
        assert score_before != score_after, "Score should change after update_weights"
        assert score_after > score_before, (
            "Higher soc weight with soc=1.0 should raise the score"
        )


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
        """At the pivot (midpoint), score must be exactly 0.6."""
        eng = self._engine(soc_min=10, soc_max=90)
        pivot = (10 + 90) / 2.0  # 50.0
        assert eng._score_soc(pivot) == pytest.approx(0.6)

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
    """_score_forecast density-based curve (monotone decreasing).

    density = forecast_kwh / (peak_pv_kw × hours_remaining)
    high density → defer (low score), low density → urgency (high score).
    """

    def _engine(self, peak_pv_w=3000.0) -> ScoringEngine:
        return ScoringEngine({
            "weight_pv_surplus":  0.0,
            "weight_tempo":       0.0,
            "weight_battery_soc": 0.0,
            "weight_forecast":    1.0,
            "peak_pv_w":          peak_pv_w,
        })

    def test_no_forecast_returns_neutral(self):
        eng = self._engine()
        assert eng.compute({}) == pytest.approx(0.5)
        assert eng.compute({"forecast_kwh": None}) == pytest.approx(0.5)

    def test_zero_forecast_returns_neutral(self):
        eng = self._engine()
        assert eng.compute({"forecast_kwh": 0.0}) == pytest.approx(0.5)

    def test_low_density_high_urgency(self):
        """Near sunset with little left → density low → score near 0.9."""
        eng = self._engine(peak_pv_w=3000)
        # hour=18.5, hours_remaining=1.5h, forecast=0.2 kWh → density=0.2/(3×1.5)=0.044
        score = eng.compute({"forecast_kwh": 0.2, "hour": 18.5})
        assert score >= 0.85, f"Expected urgency score ≥ 0.85, got {score}"

    def test_high_density_strong_defer(self):
        """Morning, sunny forecast → density ≥ 1 → score = 0.1."""
        eng = self._engine(peak_pv_w=3000)
        # hour=9, hours_remaining=11h, forecast=36 kWh → density=36/(3×11)=1.09
        score = eng.compute({"forecast_kwh": 36.0, "hour": 9})
        assert score == pytest.approx(0.10)

    def test_monotone_decreasing_with_forecast(self):
        """More forecast remaining at same hour → score must not increase."""
        eng = self._engine(peak_pv_w=3000)
        forecasts = [0.5, 2.0, 5.0, 10.0, 20.0]
        scores = [eng.compute({"forecast_kwh": f, "hour": 12}) for f in forecasts]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Score increased from forecast={forecasts[i]} ({scores[i]:.3f}) "
                f"to forecast={forecasts[i+1]} ({scores[i+1]:.3f})"
            )

    def test_end_of_day_low_remaining_triggers_urgency(self):
        """Near sunset with little left → urgency; sunny morning → defer."""
        eng = self._engine(peak_pv_w=3000)
        # 17h / 1 kWh → density=1/(3×3)=0.11 → urgency
        score_end_of_day = eng.compute({"forecast_kwh": 1.0, "hour": 17})
        # 10h / 20 kWh → density=20/(3×10)=0.67 → defer
        score_sunny_morning = eng.compute({"forecast_kwh": 20.0, "hour": 10})
        assert score_end_of_day > score_sunny_morning, (
            "End-of-day with little remaining must be more urgent than a sunny morning"
        )

    def test_scales_with_installation_size(self):
        """A large installation should defer more for the same absolute kWh."""
        small = self._engine(peak_pv_w=2000)
        large = self._engine(peak_pv_w=6000)
        # 6 kWh at noon: small installation has density=6/(2×8)=0.375, large=6/(6×8)=0.125
        score_small = small.compute({"forecast_kwh": 6.0, "hour": 12})
        score_large = large.compute({"forecast_kwh": 6.0, "hour": 12})
        assert score_small < score_large, (
            "Large installation should see 6 kWh as less urgent than small installation"
        )

    def test_score_range_always_01(self):
        eng = self._engine()
        for hour in (6, 9, 12, 15, 18, 20):
            for kwh in (0.0, 0.5, 2.0, 5.0, 15.0, 30.0):
                s = eng.compute({"forecast_kwh": kwh, "hour": hour})
                assert 0.0 <= s <= 1.0, f"hour={hour}, kwh={kwh} → score={s}"

    def test_after_sunset_residual_returns_neutral(self):
        """Regression: a tiny forecast residual at night must not produce urgency score.

        At 22:00 the forecast entity may still carry a residual (e.g. 0.03 kWh)
        instead of 0. With the old code this produced density=0.022 → score=0.90.
        After the fix, hour ≥ 20 returns 0.5 regardless of the residual.
        """
        eng = self._engine(peak_pv_w=2700)
        for hour in (19.0, 20.0, 21.0, 22.0, 23.0):
            score = eng.compute({"forecast_kwh": 0.03, "hour": hour})
            assert score == pytest.approx(0.5), (
                f"Expected neutral 0.5 at hour={hour} with residual forecast, got {score}"
            )

    def test_just_before_cutoff_still_active(self):
        """hour=18.9 is still before the 19h cutoff — forecast score is computed normally."""
        eng = self._engine(peak_pv_w=2700)
        # density = 0.5 / (2.7 × 1.1) = 0.17 → between 0.1 and 0.5 → urgency > 0.5
        score = eng.compute({"forecast_kwh": 0.5, "hour": 18.9})
        assert score > 0.5, f"Expected urgency score before 19h cutoff, got {score}"


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


# ---------------------------------------------------------------------------
# daily_optimizer — dispatch_threshold applied by optimizer
# ---------------------------------------------------------------------------

class TestDispatchThresholdApplication:
    """Verify the coordinator's dispatch_threshold is updated after optimization."""

    @pytest.mark.asyncio
    async def test_threshold_updated_on_coordinator(self):
        """async_run_daily_optimization must write dispatch_threshold to coordinator."""
        from custom_components.helios.daily_optimizer import async_run_daily_optimization
        from custom_components.helios.const import (
            CONF_PEAK_PV_W, CONF_BATTERY_ENABLED, CONF_DEVICES,
            CONF_OPTIMIZER_ALPHA,
        )
        from custom_components.helios.simulation.optimizer import OptResult

        fake_result = OptResult(
            w_surplus=0.5, w_tempo=0.1, w_soc=0.3, w_forecast=0.1,
            threshold=0.20,
            autoconsumption=0.9, savings_rate=0.8, cost_eur=1.0, objective=0.85,
        )

        # Minimal fake coordinator
        coordinator = MagicMock()
        coordinator.entry.data = {
            CONF_PEAK_PV_W:        3000.0,
            CONF_BATTERY_ENABLED:  False,
            CONF_DEVICES:          [],
            CONF_OPTIMIZER_ALPHA:  0.5,
        }
        coordinator.dispatch_threshold = DEFAULT_DISPATCH_THRESHOLD
        coordinator.async_save_optimizer_state = AsyncMock()

        # Fake hass — async_add_executor_job returns fake results directly
        # (bypasses the grid search; optimize is imported inside the closure)
        hass = MagicMock()
        hass.states.get.return_value = None
        hass.async_add_executor_job = AsyncMock(return_value=([fake_result], []))

        await async_run_daily_optimization(hass, coordinator)

        coordinator.scoring_engine.update_weights.assert_called_once_with({
            "weight_pv_surplus":  0.5,
            "weight_tempo":       0.1,
            "weight_battery_soc": 0.3,
            "weight_forecast":    0.1,
        })
        assert coordinator.dispatch_threshold == 0.20
