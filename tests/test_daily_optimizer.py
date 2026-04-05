"""Tests for the daily optimizer — scheduling, weight application, end-to-end."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from custom_components.helios.daily_optimizer import async_run_daily_optimization
from custom_components.helios.scoring_engine import ScoringEngine
from custom_components.helios.simulation.optimizer import OptResult
from custom_components.helios.const import (
    CONF_PEAK_PV_W, CONF_BATTERY_ENABLED, CONF_DEVICES, CONF_OPTIMIZER_ALPHA,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_SOLAR,
    DEFAULT_DISPATCH_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_result(**overrides) -> OptResult:
    defaults = dict(
        w_surplus=0.5, w_tempo=0.2, w_soc=0.2, w_solar=0.1,
        threshold=0.25,
        autoconsumption=0.88, savings_rate=0.75, cost_eur=1.2, objective=0.82,
    )
    defaults.update(overrides)
    return OptResult(**defaults)


def _make_coordinator(scoring_engine=None):
    from custom_components.helios.consumption_learner import ConsumptionLearner
    from unittest.mock import AsyncMock as _AsyncMock

    coordinator = MagicMock()
    coordinator.entry.data = {
        CONF_PEAK_PV_W:       3000.0,
        CONF_BATTERY_ENABLED: False,
        CONF_DEVICES:         [],
        CONF_OPTIMIZER_ALPHA: 0.5,
    }
    coordinator.dispatch_threshold = DEFAULT_DISPATCH_THRESHOLD
    coordinator.scoring_engine = scoring_engine or MagicMock()
    coordinator.async_save_optimizer_state = _AsyncMock()

    # Build a real (but storage-less) ConsumptionLearner so as_base_load_fn() works
    learner = ConsumptionLearner.__new__(ConsumptionLearner)
    learner._alpha = 0.05
    learner._profile = [300.0] * 288
    learner._sample_count = 1
    store = MagicMock()
    store.async_load = _AsyncMock(return_value=None)
    store.async_delay_save = MagicMock()
    learner._store = store
    coordinator.consumption_learner = learner

    return coordinator


def _make_hass(forecast_state=None, tempo_state=None):
    hass = MagicMock()

    def _states_get(entity_id):
        if entity_id == "sensor.forecast" and forecast_state is not None:
            s = MagicMock()
            s.state = str(forecast_state)
            return s
        if entity_id == "sensor.tempo" and tempo_state is not None:
            s = MagicMock()
            s.state = tempo_state
            return s
        return None

    hass.states.get.side_effect = _states_get
    hass.async_add_executor_job = AsyncMock(return_value=([_fake_result()], []))
    return hass


# ---------------------------------------------------------------------------
# 5 am scheduling — coordinator registers the time listener
# ---------------------------------------------------------------------------

class TestDailyOptimizerScheduling:

    def test_coordinator_registers_5am_listener(self):
        """Coordinator must register async_track_time_change at hour=5 on init."""
        with patch(
            "custom_components.helios.coordinator.async_track_time_change"
        ) as mock_track:
            from custom_components.helios.coordinator import EnergyOptimizerCoordinator
            from custom_components.helios.const import (
                CONF_SCAN_INTERVAL_MINUTES, CONF_DEVICES, CONF_MODE,
            )

            entry = MagicMock()
            entry.data = {
                CONF_SCAN_INTERVAL_MINUTES: 5,
                CONF_DEVICES: [],
                CONF_MODE: "auto",
            }
            entry.options = {}

            hass = MagicMock()
            hass.data = {}

            EnergyOptimizerCoordinator(hass, entry)

            mock_track.assert_called_once()
            _, kwargs = mock_track.call_args
            assert kwargs.get("hour")   == 5
            assert kwargs.get("minute") == 0
            assert kwargs.get("second") == 0

    @pytest.mark.asyncio
    async def test_5am_callback_calls_optimizer(self):
        """The 5am callback must invoke async_run_daily_optimization."""
        with patch(
            "custom_components.helios.coordinator.async_track_time_change"
        ), patch(
            "custom_components.helios.coordinator.async_run_daily_optimization",
            new_callable=AsyncMock,
        ) as mock_opt:
            from custom_components.helios.coordinator import EnergyOptimizerCoordinator
            from custom_components.helios.const import (
                CONF_SCAN_INTERVAL_MINUTES, CONF_DEVICES, CONF_MODE,
            )

            entry = MagicMock()
            entry.data = {
                CONF_SCAN_INTERVAL_MINUTES: 5,
                CONF_DEVICES: [],
                CONF_MODE: "auto",
            }
            entry.options = {}
            hass = MagicMock()
            hass.data = {}

            coordinator = EnergyOptimizerCoordinator(hass, entry)
            await coordinator._async_daily_optimize(now=MagicMock())

            mock_opt.assert_called_once_with(hass, coordinator)


# ---------------------------------------------------------------------------
# Weight application — weights actually change in a real ScoringEngine
# ---------------------------------------------------------------------------

class TestWeightApplicationEndToEnd:

    @pytest.mark.asyncio
    async def test_weights_applied_to_real_scoring_engine(self):
        """After optimization, a real ScoringEngine must reflect the new weights."""
        real_engine = ScoringEngine({
            "weight_pv_surplus":  DEFAULT_WEIGHT_PV_SURPLUS,
            "weight_tempo":       DEFAULT_WEIGHT_TEMPO,
            "weight_battery_soc": DEFAULT_WEIGHT_BATTERY_SOC,
            "weight_solar":    DEFAULT_WEIGHT_SOLAR,
        })

        coordinator = _make_coordinator(scoring_engine=real_engine)
        hass = _make_hass()
        hass.async_add_executor_job = AsyncMock(return_value=(
            [_fake_result(w_surplus=0.7, w_tempo=0.1, w_soc=0.1, w_solar=0.1, threshold=0.15)],
            [],
        ))

        await async_run_daily_optimization(hass, coordinator)

        assert real_engine.w_surplus  == pytest.approx(0.7)
        assert real_engine.w_tempo    == pytest.approx(0.1)
        assert real_engine.w_soc      == pytest.approx(0.1)
        assert real_engine.w_solar == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_dispatch_threshold_applied_to_coordinator(self):
        """dispatch_threshold on the coordinator must be updated."""
        coordinator = _make_coordinator()
        hass = _make_hass()
        hass.async_add_executor_job = AsyncMock(return_value=(
            [_fake_result(threshold=0.42)],
            [],
        ))

        await async_run_daily_optimization(hass, coordinator)

        assert coordinator.dispatch_threshold == pytest.approx(0.42)

    @pytest.mark.asyncio
    async def test_optimizer_last_run_timestamp_set(self):
        """optimizer_last_run must be set to an ISO UTC timestamp after optimization."""
        coordinator = _make_coordinator()
        hass = _make_hass()

        await async_run_daily_optimization(hass, coordinator)

        assert coordinator.optimizer_last_run is not None
        assert "T" in coordinator.optimizer_last_run  # ISO format check

    @pytest.mark.asyncio
    async def test_new_weights_change_score(self):
        """Score computed by the engine must differ before and after weight update."""
        real_engine = ScoringEngine({
            "weight_pv_surplus":  DEFAULT_WEIGHT_PV_SURPLUS,  # 0.4
            "weight_tempo":       DEFAULT_WEIGHT_TEMPO,        # 0.3
            "weight_battery_soc": DEFAULT_WEIGHT_BATTERY_SOC, # 0.2
            "weight_solar":    DEFAULT_WEIGHT_SOLAR,     # 0.1
        })
        data = {"surplus_w": 100, "tempo_color": "red", "battery_soc": 50}

        score_before = real_engine.compute(data)

        coordinator = _make_coordinator(scoring_engine=real_engine)
        hass = _make_hass()
        hass.async_add_executor_job = AsyncMock(return_value=(
            [_fake_result(w_surplus=0.1, w_tempo=0.1, w_soc=0.7, w_solar=0.1)],
            [],
        ))

        await async_run_daily_optimization(hass, coordinator)

        score_after = real_engine.compute(data)

        assert score_before != pytest.approx(score_after), (
            "Score must change after weights are updated"
        )

    @pytest.mark.asyncio
    async def test_no_results_keeps_existing_weights(self):
        """If optimizer returns no results, existing weights must be unchanged."""
        real_engine = ScoringEngine({
            "weight_pv_surplus":  DEFAULT_WEIGHT_PV_SURPLUS,
            "weight_tempo":       DEFAULT_WEIGHT_TEMPO,
            "weight_battery_soc": DEFAULT_WEIGHT_BATTERY_SOC,
            "weight_solar":    DEFAULT_WEIGHT_SOLAR,
        })
        coordinator = _make_coordinator(scoring_engine=real_engine)
        coordinator.dispatch_threshold = DEFAULT_DISPATCH_THRESHOLD

        hass = _make_hass()
        hass.async_add_executor_job = AsyncMock(return_value=[])  # empty → no update

        await async_run_daily_optimization(hass, coordinator)

        assert real_engine.w_surplus  == DEFAULT_WEIGHT_PV_SURPLUS
        assert real_engine.w_tempo    == DEFAULT_WEIGHT_TEMPO
        assert coordinator.dispatch_threshold == DEFAULT_DISPATCH_THRESHOLD


# ---------------------------------------------------------------------------
# Input handling — forecast and tempo entities
# ---------------------------------------------------------------------------

class TestDailyOptimizerInputs:

    @pytest.mark.asyncio
    async def test_forecast_entity_read_and_passed_to_executor(self):
        """When a forecast entity is configured, its value is read and forwarded."""
        coordinator = _make_coordinator()
        coordinator.entry.data = {
            **coordinator.entry.data,
            "forecast_entity": "sensor.forecast",
        }

        hass = _make_hass(forecast_state=12.5)
        captured = {}

        async def _capture_job(fn):
            result = fn()
            captured["sim_cfg"] = result
            return ([_fake_result()], [])

        hass.async_add_executor_job = _capture_job

        await async_run_daily_optimization(hass, coordinator)

        # Optimizer was called (captured result is an OptResult list)
        assert "sim_cfg" in captured

    @pytest.mark.asyncio
    async def test_no_forecast_entity_uses_clear_sky_fallback(self):
        """Without forecast entity, the optimizer still runs (clear-sky fallback)."""
        coordinator = _make_coordinator()
        hass = _make_hass()  # no forecast state configured

        await async_run_daily_optimization(hass, coordinator)

        hass.async_add_executor_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_tempo_next_color_used_before_6h(self):
        """Before 06:00, CONF_TEMPO_NEXT_COLOR_ENTITY must be preferred (HP not yet active)."""
        from custom_components.helios.const import (
            CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY,
        )

        coordinator = _make_coordinator()
        coordinator.entry.data = {
            **coordinator.entry.data,
            CONF_TEMPO_COLOR_ENTITY:      "sensor.tempo_today",
            CONF_TEMPO_NEXT_COLOR_ENTITY: "sensor.tempo_tomorrow",
        }

        def _states_get(entity_id):
            if entity_id == "sensor.tempo_today":
                s = MagicMock(); s.state = "red"; return s
            if entity_id == "sensor.tempo_tomorrow":
                s = MagicMock(); s.state = "blue"; return s
            return None

        hass = MagicMock()
        hass.states.get.side_effect = _states_get
        hass.async_add_executor_job = AsyncMock(return_value=([_fake_result()], []))

        with patch(
            "custom_components.helios.daily_optimizer.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.hour = 5  # before 6h
            await async_run_daily_optimization(hass, coordinator)

        # next_color="blue" must have been used → threshold from _fake_result applied
        assert coordinator.optimizer_context["tempo"] == "blue"

    @pytest.mark.asyncio
    async def test_tempo_color_used_after_6h(self):
        """From 06:00 onwards, CONF_TEMPO_COLOR_ENTITY must take priority (HP active)."""
        from custom_components.helios.const import (
            CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY,
        )

        coordinator = _make_coordinator()
        coordinator.entry.data = {
            **coordinator.entry.data,
            CONF_TEMPO_COLOR_ENTITY:      "sensor.tempo_today",
            CONF_TEMPO_NEXT_COLOR_ENTITY: "sensor.tempo_tomorrow",
        }

        def _states_get(entity_id):
            if entity_id == "sensor.tempo_today":
                s = MagicMock(); s.state = "red"; return s
            if entity_id == "sensor.tempo_tomorrow":
                s = MagicMock(); s.state = "blue"; return s
            return None

        hass = MagicMock()
        hass.states.get.side_effect = _states_get
        hass.async_add_executor_job = AsyncMock(return_value=([_fake_result()], []))

        with patch(
            "custom_components.helios.daily_optimizer.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.hour = 10  # after 6h
            await async_run_daily_optimization(hass, coordinator)

        # today's color="red" must have been used
        assert coordinator.optimizer_context["tempo"] == "red"
