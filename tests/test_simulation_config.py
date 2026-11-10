"""Simulation — SimDevice → ManagedDevice config, state dict, engine options."""
from __future__ import annotations

import pytest

from custom_components.helios.const import (
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_WH_TEMP_MIN_ENTITY, CONF_EV_SOC_ENTITY, CONF_EV_PLUGGED_ENTITY,
    CONF_POOL_FILTRATION_ENTITY, CONF_APPLIANCE_START_SCRIPT,
    CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_EV, DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER,
    APPLIANCE_STATE_PREPARING,
)
from custom_components.helios.simulation import engine
from custom_components.helios.simulation.devices import SimDevice
from custom_components.helios.simulation.engine import SimConfig


class TestToManagedConfig:
    def test_allowed_window_rounding(self):
        # 6.9999 h rounds to 60 min → next hour; ≥ 24 h is clamped
        cfg = SimDevice(name="d", power_w=500, allowed_start=6.9999, allowed_end=24.0).to_managed_config()
        assert cfg[CONF_DEVICE_ALLOWED_START] == "07:00"
        assert cfg[CONF_DEVICE_ALLOWED_END] == "23:59"

    def test_optional_entities_forwarded(self):
        wh = SimDevice(name="wh", power_w=2000, device_type=DEVICE_TYPE_WATER_HEATER,
                       wh_temp=50.0, wh_temp_min_entity="input_number.wh_min")
        assert wh.to_managed_config()[CONF_WH_TEMP_MIN_ENTITY] == "input_number.wh_min"

        ev = SimDevice(name="ev", power_w=3700, device_type=DEVICE_TYPE_EV, ev_soc=40.0,
                       ev_soc_entity="sensor.ev_soc", ev_plugged_entity="binary_sensor.ev_plugged")
        ev_cfg = ev.to_managed_config()
        assert ev_cfg[CONF_EV_SOC_ENTITY] == "sensor.ev_soc"
        assert ev_cfg[CONF_EV_PLUGGED_ENTITY] == "binary_sensor.ev_plugged"

        pool = SimDevice(name="pool", power_w=800, device_type=DEVICE_TYPE_POOL,
                         pool_filtration_entity="sensor.pool_quota")
        assert pool.to_managed_config()[CONF_POOL_FILTRATION_ENTITY] == "sensor.pool_quota"

    def test_appliance_gets_noop_start_script(self):
        cfg = SimDevice(name="dw", power_w=2000, device_type=DEVICE_TYPE_APPLIANCE,
                        appliance_cycle_duration_minutes=90).to_managed_config()
        assert cfg[CONF_APPLIANCE_START_SCRIPT] == "script.sim_noop"
        assert cfg[CONF_APPLIANCE_CYCLE_DURATION_MINUTES] == 90

    def test_state_dict_includes_wh_min_entity(self):
        sd = SimDevice(name="wh", power_w=2000, device_type=DEVICE_TYPE_WATER_HEATER,
                       wh_temp=50.0, wh_temp_min=45.0, wh_temp_min_entity="input_number.wh_min")
        assert sd.make_state_dict()["input_number.wh_min"] == "45.0"


class TestEngineOptions:
    def test_appliance_ready_at_start_is_preparing(self):
        sd = SimDevice(name="dw", power_w=2000, device_type=DEVICE_TYPE_APPLIANCE,
                       appliance_ready_at_start=True)
        [md] = engine._managed_from_sim([sd])
        assert md.appliance_state == APPLIANCE_STATE_PREPARING

    def test_base_load_noise_scales_consumption(self, monkeypatch):
        base = SimConfig(bat_enabled=False, forecast_noise=0.0)
        flat = engine.run(base, [])
        monkeypatch.setattr(engine.random, "gauss", lambda mu, sigma: 1.5)
        noisy = engine.run(SimConfig(bat_enabled=False, forecast_noise=0.0, base_load_noise=0.2), [])
        assert noisy.e_load_kwh == pytest.approx(flat.e_load_kwh * 1.5, rel=0.01)

    async def test_run_from_running_event_loop(self):
        """Called from a coroutine, run() offloads to a worker thread instead of failing."""
        result = engine.run(SimConfig(bat_enabled=False), [])
        assert result.e_pv_kwh > 0
