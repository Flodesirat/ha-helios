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

            # Two time-change subscriptions: 05:00 optimizer + 00:00 energy reset
            assert mock_track.call_count == 2
            calls_kwargs = [c[1] for c in mock_track.call_args_list]
            five_am = next(k for k in calls_kwargs if k.get("hour") == 5)
            assert five_am.get("minute") == 0
            assert five_am.get("second") == 0
            midnight = next(k for k in calls_kwargs if k.get("hour") == 0)
            assert midnight.get("minute") == 0
            assert midnight.get("second") == 0

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


# ---------------------------------------------------------------------------
# HA config → simulation devices
# ---------------------------------------------------------------------------

from custom_components.helios.const import (  # noqa: E402
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_POWER_W,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_EV_SOC_ENTITY,
    CONF_POOL_WATER_TEMP_ENTITY, CONF_POOL_FILTRATION_TABLE,
    CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    CONF_FORECAST_ENTITY, CONF_EMA_ENABLED,
    CONF_BATTERIES, CONF_BATTERY_SOC_ENTITY,
    DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_EV, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
)
from custom_components.helios.daily_optimizer import (  # noqa: E402
    ha_devices_to_sim, season_from_date,
)


def test_season_from_today():
    assert season_from_date() in {"winter", "spring", "summer", "autumn"}


class TestHaDevicesToSim:

    def test_allowed_window_from_time_selector(self):
        """The UI stores times as HH:MM:SS."""
        [sd], _ = ha_devices_to_sim([{
            CONF_DEVICE_NAME: "Prise", CONF_DEVICE_TYPE: "generic", CONF_DEVICE_POWER_W: 500,
            CONF_DEVICE_ALLOWED_START: "06:30:00", CONF_DEVICE_ALLOWED_END: "bad",
        }])
        assert sd.allowed_start == 6.5
        assert sd.allowed_end == pytest.approx(23 + 59 / 60)   # invalid → default 23:59

    @pytest.mark.parametrize("raw, expected", [("48", 48.0), ("unavailable", 55.0), ("hot", 55.0)])
    async def test_water_heater_temperature_seeded_from_ha(self, hass, raw, expected):
        hass.states.async_set("sensor.wh_temp", raw)
        [sd], _ = ha_devices_to_sim([{
            CONF_DEVICE_NAME: "Ballon", CONF_DEVICE_TYPE: DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_POWER_W: 2000, CONF_WH_TEMP_ENTITY: "sensor.wh_temp",
            CONF_WH_TEMP_TARGET: 60,
        }], hass=hass)
        # Default: 5 °C below the target
        assert sd.wh_temp == expected

    async def test_ev_soc_seeded_from_ha(self, hass):
        hass.states.async_set("sensor.ev_soc", "35")
        [sd], _ = ha_devices_to_sim([{
            CONF_DEVICE_NAME: "VE", CONF_DEVICE_TYPE: DEVICE_TYPE_EV,
            CONF_DEVICE_POWER_W: 3700, CONF_EV_SOC_ENTITY: "sensor.ev_soc",
        }], hass=hass)
        assert sd.ev_soc == 35.0
        assert sd.ev_plugged is True

    async def test_pool_quota_from_water_temperature(self, hass):
        hass.states.async_set("sensor.pool_water", "20")
        [sd], [md] = ha_devices_to_sim([{
            CONF_DEVICE_NAME: "Piscine", CONF_DEVICE_TYPE: DEVICE_TYPE_POOL,
            CONF_DEVICE_POWER_W: 800, CONF_POOL_WATER_TEMP_ENTITY: "sensor.pool_water",
            CONF_POOL_FILTRATION_TABLE: "10:2, 20:10, 30:20",
        }], hass=hass)
        assert sd.run_quota_h == pytest.approx(10.0)
        assert md.pool_required_minutes_today == pytest.approx(600.0)

    def test_pool_without_hass_has_no_quota(self):
        [sd], [md] = ha_devices_to_sim([{
            CONF_DEVICE_NAME: "Piscine", CONF_DEVICE_TYPE: DEVICE_TYPE_POOL, CONF_DEVICE_POWER_W: 800,
        }])
        assert sd.run_quota_h is None
        assert md.pool_required_minutes_today is None

    def test_appliance_cycle_and_schedule(self):
        [sd], _ = ha_devices_to_sim(
            [{
                CONF_DEVICE_NAME: "Lave-linge", CONF_DEVICE_TYPE: DEVICE_TYPE_APPLIANCE,
                CONF_DEVICE_POWER_W: 2000, CONF_APPLIANCE_CYCLE_DURATION_MINUTES: 90,
            }],
            appliance_schedule={"Lave-linge": 9.5},
        )
        assert sd.appliance_cycle_duration_minutes == 90
        assert sd.appliance_ready_at_hour == 9.5


# ---------------------------------------------------------------------------
# Forecast inputs: forecast entity, battery SOC, schedule and base load files
# ---------------------------------------------------------------------------

class TestDailyForecastSources:

    @pytest.mark.parametrize("forecast, expected_kwh", [(3.0, 3.0), ("n/a", None)])
    async def test_forecast_entity_sets_cloud_cover(self, forecast, expected_kwh):
        coordinator = _make_coordinator()
        coordinator.entry.data = {**coordinator.entry.data, CONF_FORECAST_ENTITY: "sensor.forecast"}
        hass = _make_hass(forecast_state=forecast)
        with _patch_sim() as mock_sim:
            await async_run_daily_forecast(hass, coordinator)
        assert coordinator.optimizer_context["forecast_kwh"] == expected_kwh
        sim_cfg = mock_sim.call_args.args[0]
        if expected_kwh is None:
            assert sim_cfg.cloud == "clear"
        else:
            # 3 kWh for a 3 kWp array is far below any clear-sky day
            assert sim_cfg.cloud == "cloudy"

    async def test_battery_soc_seeds_simulation(self):
        coordinator = _make_coordinator()
        coordinator.entry.data = {
            **coordinator.entry.data,
            CONF_BATTERY_ENABLED: True,
            CONF_BATTERIES: [{CONF_BATTERY_SOC_ENTITY: "sensor.bat_soc"}],
        }
        hass = _make_hass()
        hass.states.get.side_effect = lambda eid: MagicMock(state="72") if eid == "sensor.bat_soc" else None
        with _patch_sim() as mock_sim:
            await async_run_daily_forecast(hass, coordinator)
        assert coordinator.optimizer_context["bat_soc_start"] == 72.0
        assert mock_sim.call_args.args[0].bat_enabled is True

    async def test_missing_schedule_and_base_load_files_are_tolerated(self, caplog):
        coordinator = _make_coordinator()
        coordinator.entry.data = {**coordinator.entry.data, CONF_EMA_ENABLED: False}
        hass = _make_hass()
        hass.async_add_executor_job = AsyncMock(side_effect=OSError("no file"))
        with _patch_sim() as mock_sim:
            await async_run_daily_forecast(hass, coordinator)
        assert "could not load base_load.json" in caplog.text
        assert mock_sim.call_args.args[0].base_load_fn is None

    async def test_ema_disabled_uses_static_base_load(self):
        coordinator = _make_coordinator()
        coordinator.entry.data = {**coordinator.entry.data, CONF_EMA_ENABLED: False}
        static_fn = MagicMock()
        hass = _make_hass()
        hass.async_add_executor_job = AsyncMock(side_effect=[{}, static_fn])
        with _patch_sim() as mock_sim:
            await async_run_daily_forecast(hass, coordinator)
        assert mock_sim.call_args.args[0].base_load_fn is static_fn
