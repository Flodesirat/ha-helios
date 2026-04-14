"""Tests for the daily forecast — scheduling, ForecastResult, end-to-end."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.daily_optimizer import (
    async_run_daily_forecast,
    ForecastResult,
)
from custom_components.helios.const import (
    CONF_PEAK_PV_W, CONF_BATTERY_ENABLED, CONF_DEVICES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_sim_result(
    *,
    e_pv_kwh: float = 15.0,
    e_load_kwh: float = 12.0,
    e_self_consumed_kwh: float = 10.0,
    e_grid_import_kwh: float = 2.0,
    e_grid_export_kwh: float = 5.0,
    bat_soc_end: float = 80.0,
    cost_eur: float = 1.5,
    cost_no_pv_eur: float = 4.0,
) -> MagicMock:
    r = MagicMock()
    r.e_pv_kwh = e_pv_kwh
    r.e_load_kwh = e_load_kwh
    r.e_self_consumed_kwh = e_self_consumed_kwh
    r.e_grid_import_kwh = e_grid_import_kwh
    r.e_grid_export_kwh = e_grid_export_kwh
    r.bat_soc_end = bat_soc_end
    r.cost_eur = cost_eur
    r.cost_no_pv_eur = cost_no_pv_eur
    r.autoconsumption_rate = e_self_consumed_kwh / max(e_pv_kwh, 1e-6)
    r.self_sufficiency_rate = e_self_consumed_kwh / max(e_load_kwh, 1e-6)
    r.savings_eur = cost_no_pv_eur - cost_eur
    return r


def _make_coordinator():
    from custom_components.helios.consumption_learner import ConsumptionLearner

    coordinator = MagicMock()
    coordinator.entry.data = {
        CONF_PEAK_PV_W:       3000.0,
        CONF_BATTERY_ENABLED: False,
        CONF_DEVICES:         [],
    }
    coordinator.entry.options = {}

    # Build a real (but storage-less) ConsumptionLearner so as_base_load_fn() works
    learner = ConsumptionLearner.__new__(ConsumptionLearner)
    learner._alpha = 0.05
    learner._profile = [300.0] * 288
    learner._sample_count = 1
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
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
    hass.async_add_executor_job = AsyncMock(return_value={})
    return hass


def _patch_sim(sim_result=None):
    """Context manager that patches simulation.engine.async_run."""
    result = sim_result or _fake_sim_result()
    return patch(
        "custom_components.helios.daily_optimizer._sim_async_run",
        new_callable=AsyncMock,
        return_value=result,
    )


# ---------------------------------------------------------------------------
# 5 am scheduling — coordinator registers the time listener
# ---------------------------------------------------------------------------

class TestDailyForecastScheduling:

    def test_coordinator_registers_5am_listener(self):
        """Coordinator must register async_track_time_change at hour=5 on init."""
        with patch(
            "custom_components.helios.coordinator.async_track_time_change"
        ) as mock_track:
            from custom_components.helios.coordinator import EnergyOptimizerCoordinator
            from custom_components.helios.const import (
                CONF_SCAN_INTERVAL_MINUTES, CONF_DEVICES,
            )

            entry = MagicMock()
            entry.data = {
                CONF_SCAN_INTERVAL_MINUTES: 5,
                CONF_DEVICES: [],
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
    async def test_5am_callback_calls_forecast(self):
        """The 5am callback must invoke async_run_daily_forecast."""
        with patch(
            "custom_components.helios.coordinator.async_track_time_change"
        ), patch(
            "custom_components.helios.coordinator.async_run_daily_forecast",
            new_callable=AsyncMock,
        ) as mock_forecast:
            from custom_components.helios.coordinator import EnergyOptimizerCoordinator
            from custom_components.helios.const import (
                CONF_SCAN_INTERVAL_MINUTES, CONF_DEVICES,
            )

            entry = MagicMock()
            entry.data = {
                CONF_SCAN_INTERVAL_MINUTES: 5,
                CONF_DEVICES: [],
            }
            entry.options = {}
            hass = MagicMock()
            hass.data = {}

            coordinator = EnergyOptimizerCoordinator(hass, entry)
            await coordinator._async_daily_optimize(now=MagicMock())

            mock_forecast.assert_called_once_with(hass, coordinator)


# ---------------------------------------------------------------------------
# ForecastResult produced correctly
# ---------------------------------------------------------------------------

class TestForecastResult:

    @pytest.mark.asyncio
    async def test_forecast_data_set_on_coordinator(self):
        """coordinator.forecast_data must be a ForecastResult after successful run."""
        coordinator = _make_coordinator()
        hass = _make_hass()

        with _patch_sim():
            await async_run_daily_forecast(hass, coordinator)

        assert coordinator.forecast_data is not None
        assert isinstance(coordinator.forecast_data, ForecastResult)

    @pytest.mark.asyncio
    async def test_forecast_result_fields(self):
        """ForecastResult fields must match the simulation output."""
        sim = _fake_sim_result(
            e_pv_kwh=20.0,
            e_load_kwh=15.0,
            e_self_consumed_kwh=12.0,
            e_grid_import_kwh=3.0,
            e_grid_export_kwh=8.0,
            cost_eur=2.0,
            cost_no_pv_eur=6.0,
        )
        coordinator = _make_coordinator()
        hass = _make_hass()

        with _patch_sim(sim):
            await async_run_daily_forecast(hass, coordinator)

        f = coordinator.forecast_data
        assert f.forecast_pv_kwh == pytest.approx(20.0)
        assert f.forecast_consumption_kwh == pytest.approx(15.0)
        assert f.forecast_import_kwh == pytest.approx(3.0)
        assert f.forecast_export_kwh == pytest.approx(8.0)
        assert f.forecast_self_consumption_pct == pytest.approx(60.0, abs=0.1)  # 12/20
        assert f.forecast_self_sufficiency_pct == pytest.approx(80.0, abs=0.1)  # 12/15
        assert f.forecast_cost == pytest.approx(2.0)
        assert f.forecast_savings == pytest.approx(4.0)
        assert "T" in f.last_forecast  # ISO format

    @pytest.mark.asyncio
    async def test_optimizer_last_run_set(self):
        """optimizer_last_run must be set to an ISO UTC timestamp."""
        coordinator = _make_coordinator()
        hass = _make_hass()

        with _patch_sim():
            await async_run_daily_forecast(hass, coordinator)

        assert coordinator.optimizer_last_run is not None
        assert "T" in coordinator.optimizer_last_run

    @pytest.mark.asyncio
    async def test_simulation_failure_leaves_forecast_unchanged(self):
        """If simulation raises, coordinator.forecast_data must not be modified."""
        coordinator = _make_coordinator()
        coordinator.forecast_data = None
        hass = _make_hass()

        with patch(
            "custom_components.helios.daily_optimizer._sim_async_run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("sim failed"),
        ):
            await async_run_daily_forecast(hass, coordinator)

        assert coordinator.forecast_data is None


# ---------------------------------------------------------------------------
# Input handling — forecast and tempo entities
# ---------------------------------------------------------------------------

class TestDailyForecastInputs:

    @pytest.mark.asyncio
    async def test_no_forecast_entity_uses_clear_sky_fallback(self):
        """Without forecast entity, the forecast still runs (clear-sky fallback)."""
        coordinator = _make_coordinator()
        hass = _make_hass()  # no forecast state configured

        with _patch_sim() as mock_sim:
            await async_run_daily_forecast(hass, coordinator)

        mock_sim.assert_called_once()

    @pytest.mark.asyncio
    async def test_tempo_next_color_used_before_6h(self):
        """Before 06:00, CONF_TEMPO_NEXT_COLOR_ENTITY must be preferred."""
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
        hass.async_add_executor_job = AsyncMock(return_value={})

        with _patch_sim(), patch(
            "custom_components.helios.daily_optimizer.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.hour = 5  # before 6h
            await async_run_daily_forecast(hass, coordinator)

        assert coordinator.optimizer_context["tempo"] == "blue"

    @pytest.mark.asyncio
    async def test_tempo_color_used_after_6h(self):
        """From 06:00 onwards, CONF_TEMPO_COLOR_ENTITY must take priority."""
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
        hass.async_add_executor_job = AsyncMock(return_value={})

        with _patch_sim(), patch(
            "custom_components.helios.daily_optimizer.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.hour = 10  # after 6h
            await async_run_daily_forecast(hass, coordinator)

        assert coordinator.optimizer_context["tempo"] == "red"
