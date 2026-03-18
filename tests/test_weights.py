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
          soc=50         → _score_soc     = 1.0
        Before: 0.4×0.2 + 0.3×0.0 + 0.2×1.0 + 0.1×0.5 = 0.33
        After:  0.1×0.2 + 0.1×0.0 + 0.7×1.0 + 0.1×0.5 = 0.77
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
# ScoringEngine — forecast scoring
# ---------------------------------------------------------------------------

class TestForecastScoring:
    """_score_forecast non-monotone curve."""

    def _engine(self) -> ScoringEngine:
        return ScoringEngine({
            "weight_pv_surplus": 0.0,
            "weight_tempo":      0.0,
            "weight_battery_soc": 0.0,
            "weight_forecast":   1.0,
        })

    def test_no_forecast_returns_neutral(self):
        eng = self._engine()
        assert eng.compute({}) == 0.5
        assert eng.compute({"forecast_kwh": None}) == 0.5

    def test_zero_forecast_returns_neutral(self):
        eng = self._engine()
        assert eng.compute({"forecast_kwh": 0.0}) == 0.5

    def test_low_forecast_high_urgency(self):
        """1 kWh remaining → urgency, score > 0.5."""
        eng = self._engine()
        score = eng.compute({"forecast_kwh": 1.0})
        assert score > 0.5, f"Expected urgency score > 0.5, got {score}"

    def test_high_forecast_low_score(self):
        """15 kWh remaining → defer, score < 0.5."""
        eng = self._engine()
        score = eng.compute({"forecast_kwh": 15.0})
        assert score < 0.5, f"Expected deferred score < 0.5, got {score}"

    def test_curve_is_non_monotone(self):
        """Score at 1 kWh should be higher than at 7 kWh (non-monotone curve)."""
        eng = self._engine()
        score_1kwh = eng.compute({"forecast_kwh": 1.0})
        score_7kwh = eng.compute({"forecast_kwh": 7.0})
        assert score_1kwh > score_7kwh


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
        from simulation.optimizer import OptResult

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

        # Fake hass — async_add_executor_job returns fake results directly
        # (bypasses the grid search; optimize is imported inside the closure)
        hass = MagicMock()
        hass.states.get.return_value = None
        hass.async_add_executor_job = AsyncMock(return_value=[fake_result])

        await async_run_daily_optimization(hass, coordinator)

        coordinator.scoring_engine.update_weights.assert_called_once_with({
            "weight_pv_surplus":  0.5,
            "weight_tempo":       0.1,
            "weight_battery_soc": 0.3,
            "weight_forecast":    0.1,
        })
        assert coordinator.dispatch_threshold == 0.20
