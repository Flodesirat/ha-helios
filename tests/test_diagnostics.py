"""Tests for the diagnostics module.

Covers:
- Full diagnostics payload structure and key presence
- Correct values from coordinator / device_manager / scoring_engine
- Robustness when optional data is absent (no optimizer run yet, empty
  decision log, no battery, no devices)
"""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from unittest.mock import MagicMock as _MagicMock

from custom_components.helios.diagnostics import async_get_config_entry_diagnostics
from custom_components.helios.consumption_learner import ConsumptionLearner, SLOTS
from custom_components.helios.const import (
    DOMAIN,
    DEVICE_TYPE_EV, DEVICE_TYPE_POOL, DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY,
    CONF_DEVICE_POWER_ENTITY,
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET,
)
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.scoring_engine import ScoringEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scoring_engine(
    w_surplus=0.4, w_tempo=0.3, w_soc=0.2, w_forecast=0.1
):
    eng = ScoringEngine({})
    eng.w_surplus  = w_surplus
    eng.w_tempo    = w_tempo
    eng.w_soc      = w_soc
    eng.w_forecast = w_forecast
    return eng


def _make_device_manager(devices=None, log_entries=None):
    dm = MagicMock()
    dm.devices     = devices or []
    dm.decision_log = deque(log_entries or [], maxlen=500)
    return dm


def _make_learner(profile: list[float] | None = None, sample_count: int = 0):
    """Build a minimal ConsumptionLearner without real HA storage."""
    learner = ConsumptionLearner.__new__(ConsumptionLearner)
    learner._alpha = 0.05
    learner._profile = profile
    learner._sample_count = sample_count
    store = _MagicMock()
    learner._store = store
    return learner


def _make_coordinator(
    *,
    devices=None,
    log_entries=None,
    battery_soc=None,
    optimizer_top20=None,
    optimizer_chosen=None,
    optimizer_chosen_schedule=None,
    optimizer_context=None,
    optimizer_last_run=None,
    ema_profile: list[float] | None = None,
    ema_sample_count: int = 0,
):
    coordinator = MagicMock()

    # Scoring engine
    coordinator.scoring_engine = _make_scoring_engine()

    # Device manager
    coordinator.device_manager = _make_device_manager(
        devices=devices,
        log_entries=log_entries,
    )

    # Coordinator state
    coordinator.mode              = "auto"
    coordinator.global_score      = 0.72
    coordinator.dispatch_threshold = 0.30
    coordinator.surplus_w         = 1200.0
    coordinator.pv_power_w        = 2500.0
    coordinator.grid_power_w      = -300.0
    coordinator.house_power_w     = 1000.0
    coordinator.bat_available_w   = 800.0
    coordinator.battery_soc       = battery_soc
    coordinator.battery_power_w   = None
    coordinator.battery_action    = "idle"
    coordinator.tempo_color       = "blue"
    coordinator.tempo_next_color  = None
    coordinator.forecast_kwh      = 8.5
    coordinator.grid_allowance_w  = 250.0
    coordinator._build_score_input = lambda: {
        "surplus_w":   1200.0,
        "tempo_color": "blue",
        "battery_soc": battery_soc,
        "forecast_kwh": 8.5,
    }
    coordinator._cfg = {
        # Sources
        "pv_power_entity":        "sensor.pv",
        "grid_power_entity":      "sensor.grid",
        "house_power_entity":     "sensor.house",
        "tempo_color_entity":     None,
        "tempo_next_color_entity": None,
        "forecast_entity":        None,
        "peak_pv_w":              5000,
        "off_peak_1_start":       "22:00",
        "off_peak_1_end":         "06:00",
        "off_peak_2_start":       None,
        "off_peak_2_end":         None,
        # Battery
        "battery_enabled":               True,
        "battery_soc_entity":            "sensor.bat_soc",
        "battery_power_entity":          "sensor.bat_power",
        "battery_charge_script":         None,
        "battery_autoconsum_script":     None,
        "battery_capacity_kwh":          10.0,
        "battery_soc_min":               10,
        "battery_soc_max":               95,
        "battery_soc_reserve_rouge":     80,
        "battery_max_charge_power_w":    3000,
        "battery_max_discharge_power_w": 3000,
        # Strategy
        "mode":                  "auto",
        "scan_interval_minutes": 5,
        "dispatch_threshold":    0.3,
        "grid_allowance_w":      250,
        "optimizer_alpha":       0.5,
        "ema_alpha":             0.05,
        "base_load_noise":       0.20,
        "optimizer_n_runs":      5,
        "risk_lambda":           0.5,
        "weight_pv_surplus":     0.4,
        "weight_tempo":          0.3,
        "weight_battery_soc":    0.2,
        "weight_forecast":       0.1,
    }

    # Optimizer diagnostics fields
    coordinator.optimizer_last_run         = optimizer_last_run
    coordinator.optimizer_context          = optimizer_context or {}
    coordinator.optimizer_chosen           = optimizer_chosen or {}
    coordinator.optimizer_top20            = optimizer_top20 or []
    coordinator.optimizer_chosen_schedule  = optimizer_chosen_schedule or []

    # EMA learner
    coordinator.consumption_learner = _make_learner(
        profile=ema_profile if ema_profile is not None else [300.0] * SLOTS,
        sample_count=ema_sample_count,
    )

    return coordinator


def _make_hass(coordinator):
    entry = MagicMock()
    entry.entry_id = "test_entry"

    hass = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}
    return hass, entry


# ---------------------------------------------------------------------------
# Top-level structure
# ---------------------------------------------------------------------------

class TestDiagnosticsStructure:
    """The returned dict must always contain the three expected top-level keys."""

    @pytest.mark.asyncio
    async def test_top_level_keys_present(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert set(result.keys()) == {"current_state", "configuration", "optimizer", "base_load_profile", "decision_log"}

    @pytest.mark.asyncio
    async def test_current_state_keys(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)
        cs = result["current_state"]

        for key in (
            "mode", "global_score", "dispatch_threshold",
            "surplus_w", "pv_power_w", "grid_power_w", "house_power_w",
            "bat_available_w", "battery_soc", "battery_action",
            "tempo_color", "forecast_kwh", "grid_allowance_w",
            "scoring_weights", "devices",
        ):
            assert key in cs, f"missing key: {key}"

    @pytest.mark.asyncio
    async def test_optimizer_keys(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)
        opt = result["optimizer"]

        for key in ("last_run", "context", "chosen", "top20", "chosen_schedule"):
            assert key in opt, f"missing optimizer key: {key}"

    @pytest.mark.asyncio
    async def test_scoring_weights_keys(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)
        weights = result["current_state"]["scoring_weights"]

        assert set(weights.keys()) == {"surplus", "tempo", "soc", "forecast"}

    @pytest.mark.asyncio
    async def test_score_breakdown_present(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)
        cs = result["current_state"]

        assert "score_breakdown" in cs
        bd = cs["score_breakdown"]
        assert set(bd.keys()) == {"f_surplus", "f_tempo", "f_soc", "f_forecast"}
        for v in bd.values():
            assert 0.0 <= v <= 1.0, f"score component out of [0, 1]: {v}"


# ---------------------------------------------------------------------------
# Current state values
# ---------------------------------------------------------------------------

class TestCurrentStateValues:

    @pytest.mark.asyncio
    async def test_scalar_values_forwarded(self):
        coordinator = _make_coordinator(battery_soc=72.5)
        hass, entry = _make_hass(coordinator)

        cs = (await async_get_config_entry_diagnostics(hass, entry))["current_state"]

        assert cs["mode"]               == "auto"
        assert cs["global_score"]       == 0.72
        assert cs["surplus_w"]          == 1200.0
        assert cs["pv_power_w"]         == 2500.0
        assert cs["house_power_w"]      == 1000.0
        assert cs["battery_soc"]        == 72.5
        assert cs["tempo_color"]        == "blue"
        assert cs["grid_allowance_w"]   == 250.0

    @pytest.mark.asyncio
    async def test_scoring_weights_values(self):
        coordinator = _make_coordinator()
        coordinator.scoring_engine = _make_scoring_engine(
            w_surplus=0.5, w_tempo=0.2, w_soc=0.2, w_forecast=0.1
        )
        hass, entry = _make_hass(coordinator)

        weights = (await async_get_config_entry_diagnostics(hass, entry))[
            "current_state"
        ]["scoring_weights"]

        assert weights["surplus"]  == 0.5
        assert weights["tempo"]    == 0.2
        assert weights["soc"]      == 0.2
        assert weights["forecast"] == 0.1

    @pytest.mark.asyncio
    async def test_battery_soc_none(self):
        """battery_soc must be serialisable even when None (no battery configured)."""
        coordinator = _make_coordinator(battery_soc=None)
        hass, entry = _make_hass(coordinator)

        cs = (await async_get_config_entry_diagnostics(hass, entry))["current_state"]

        assert cs["battery_soc"] is None


# ---------------------------------------------------------------------------
# Configuration section
# ---------------------------------------------------------------------------

class TestConfigurationSection:

    @pytest.mark.asyncio
    async def test_configuration_top_level_sections(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        cfg = (await async_get_config_entry_diagnostics(hass, entry))["configuration"]

        assert set(cfg.keys()) == {"sources", "battery", "strategy"}

    @pytest.mark.asyncio
    async def test_sources_section(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        sources = (await async_get_config_entry_diagnostics(hass, entry))["configuration"]["sources"]

        assert sources["pv_power_entity"]   == "sensor.pv"
        assert sources["grid_power_entity"] == "sensor.grid"
        assert sources["peak_pv_w"]         == 5000
        assert sources["off_peak_1_start"]  == "22:00"
        assert sources["off_peak_1_end"]    == "06:00"
        assert sources["off_peak_2_start"]  is None

    @pytest.mark.asyncio
    async def test_battery_section(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        bat = (await async_get_config_entry_diagnostics(hass, entry))["configuration"]["battery"]

        assert bat["enabled"]           is True
        assert bat["capacity_kwh"]      == 10.0
        assert bat["soc_min"]           == 10
        assert bat["soc_max"]           == 95
        assert bat["soc_reserve_rouge"] == 80
        assert bat["max_charge_power_w"]    == 3000
        assert bat["max_discharge_power_w"] == 3000

    @pytest.mark.asyncio
    async def test_strategy_section(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        strat = (await async_get_config_entry_diagnostics(hass, entry))["configuration"]["strategy"]

        assert strat["mode"]                  == "auto"
        assert strat["scan_interval_minutes"] == 5
        assert strat["dispatch_threshold"]    == 0.3
        assert strat["grid_allowance_w"]      == 250
        assert strat["optimizer_alpha"]       == 0.5
        assert strat["ema_alpha"]             == 0.05
        assert strat["weight_pv_surplus"]     == 0.4
        assert strat["weight_tempo"]          == 0.3
        assert strat["weight_battery_soc"]    == 0.2
        assert strat["weight_forecast"]       == 0.1


# ---------------------------------------------------------------------------
# Devices list
# ---------------------------------------------------------------------------

class TestDevicesList:

    def _pool_device(self, name="Piscine"):
        return ManagedDevice({
            CONF_DEVICE_NAME:          name,
            CONF_DEVICE_TYPE:          DEVICE_TYPE_POOL,
            CONF_DEVICE_SWITCH_ENTITY: "switch.pompe",
            CONF_DEVICE_POWER_W:       300,
            CONF_DEVICE_PRIORITY:      5,
        })

    @pytest.mark.asyncio
    async def test_no_devices(self):
        coordinator = _make_coordinator(devices=[])
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["current_state"]["devices"] == []

    @pytest.mark.asyncio
    async def test_device_fields(self):
        device = self._pool_device("Piscine")
        device.is_on       = True
        device.manual_mode = False

        coordinator = _make_coordinator(devices=[device])
        hass, entry = _make_hass(coordinator)

        devices = (await async_get_config_entry_diagnostics(hass, entry))[
            "current_state"
        ]["devices"]

        assert len(devices) == 1
        d = devices[0]
        assert d["name"]        == "Piscine"
        assert d["type"]        == DEVICE_TYPE_POOL
        assert d["is_on"]       is True
        assert d["manual_mode"] is False
        assert d["priority"]    == 5
        assert d["power_w"]     == 300.0

    @pytest.mark.asyncio
    async def test_device_with_power_entity_uses_measured_power(self):
        """A device with power_entity configured must read actual W from hass
        via StateReader — not receive hass directly (regression for TypeError)."""
        device = ManagedDevice({
            CONF_DEVICE_NAME:          "Chauffe-eau",
            CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY: "switch.cwe",
            CONF_DEVICE_POWER_W:       2000,
            CONF_DEVICE_PRIORITY:      8,
            CONF_DEVICE_POWER_ENTITY:  "sensor.cwe_power",
        })
        device.is_on = True

        coordinator = _make_coordinator(devices=[device])
        hass, entry = _make_hass(coordinator)

        # Simulate the power sensor returning 1800 W
        power_state = MagicMock()
        power_state.state = "1800"
        hass.states.get = MagicMock(return_value=power_state)

        # Must not raise TypeError even though device.power_entity is set
        devices = (await async_get_config_entry_diagnostics(hass, entry))[
            "current_state"
        ]["devices"]

        assert len(devices) == 1
        assert devices[0]["actual_power_w"] == pytest.approx(1800.0)

    @pytest.mark.asyncio
    async def test_ev_device_reads_entities_via_reader(self):
        """EV device: soc and plugged must be read via StateReader, not hass directly."""
        device = ManagedDevice({
            CONF_DEVICE_NAME:          "Voiture",
            CONF_DEVICE_TYPE:          DEVICE_TYPE_EV,
            CONF_DEVICE_SWITCH_ENTITY: "switch.ev",
            CONF_DEVICE_POWER_W:       7400,
            CONF_DEVICE_PRIORITY:      6,
            CONF_EV_SOC_ENTITY:        "sensor.ev_soc",
            CONF_EV_PLUGGED_ENTITY:    "binary_sensor.ev_plugged",
            CONF_EV_SOC_TARGET:        80,
        })
        device.is_on = False

        coordinator = _make_coordinator(devices=[device])
        hass, entry = _make_hass(coordinator)

        def _states_get(entity_id):
            s = MagicMock()
            if entity_id == "sensor.ev_soc":
                s.state = "55"
            elif entity_id == "binary_sensor.ev_plugged":
                s.state = "on"
            else:
                s.state = "unavailable"
            return s

        hass.states.get = MagicMock(side_effect=_states_get)

        # Must not raise TypeError
        devices = (await async_get_config_entry_diagnostics(hass, entry))[
            "current_state"
        ]["devices"]

        ev = devices[0]["ev"]
        assert ev["soc"] == pytest.approx(55.0)
        assert ev["soc_target"] == 80
        assert ev["plugged"] is True

    @pytest.mark.asyncio
    async def test_wh_device_reads_temp_via_reader(self):
        """Water heater: temp must be read via StateReader, not hass directly."""
        device = ManagedDevice({
            CONF_DEVICE_NAME:          "Chauffe-eau",
            CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY: "switch.cwe",
            CONF_DEVICE_POWER_W:       2000,
            CONF_DEVICE_PRIORITY:      8,
            CONF_WH_TEMP_ENTITY:       "sensor.cwe_temp",
            CONF_WH_TEMP_TARGET:       60,
        })
        device.is_on = True

        coordinator = _make_coordinator(devices=[device])
        hass, entry = _make_hass(coordinator)

        def _states_get(entity_id):
            s = MagicMock()
            s.state = "52" if entity_id == "sensor.cwe_temp" else "unavailable"
            return s

        hass.states.get = MagicMock(side_effect=_states_get)

        # Must not raise TypeError
        devices = (await async_get_config_entry_diagnostics(hass, entry))[
            "current_state"
        ]["devices"]

        wh = devices[0]["water_heater"]
        assert wh["temp"] == pytest.approx(52.0)
        assert wh["temp_target"] == 60

    @pytest.mark.asyncio
    async def test_multiple_devices(self):
        d1 = self._pool_device("Piscine")
        d2 = ManagedDevice({
            CONF_DEVICE_NAME:          "Chauffe-eau",
            CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY: "switch.cwe",
            CONF_DEVICE_POWER_W:       2000,
            CONF_DEVICE_PRIORITY:      8,
        })

        coordinator = _make_coordinator(devices=[d1, d2])
        hass, entry = _make_hass(coordinator)

        devices = (await async_get_config_entry_diagnostics(hass, entry))[
            "current_state"
        ]["devices"]

        assert len(devices) == 2
        assert {d["name"] for d in devices} == {"Piscine", "Chauffe-eau"}


# ---------------------------------------------------------------------------
# Optimizer section
# ---------------------------------------------------------------------------

class TestOptimizerSection:

    @pytest.mark.asyncio
    async def test_optimizer_empty_before_first_run(self):
        """Before the first daily optimization, all optimizer fields must be
        present but empty / None — no KeyError or AttributeError."""
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        opt = (await async_get_config_entry_diagnostics(hass, entry))["optimizer"]

        assert opt["last_run"]         is None
        assert opt["context"]          == {}
        assert opt["chosen"]           == {}
        assert opt["top20"]            == []
        assert opt["chosen_schedule"]  == []

    @pytest.mark.asyncio
    async def test_optimizer_top20_forwarded(self):
        top20 = [
            {"rank": i + 1, "objective": round(0.9 - i * 0.01, 2)}
            for i in range(20)
        ]
        coordinator = _make_coordinator(
            optimizer_top20=top20,
            optimizer_last_run="2026-03-22T05:00:00+00:00",
        )
        hass, entry = _make_hass(coordinator)

        opt = (await async_get_config_entry_diagnostics(hass, entry))["optimizer"]

        assert len(opt["top20"]) == 20
        assert opt["top20"][0]["rank"] == 1
        assert opt["top20"][0]["objective"] == 0.9

    @pytest.mark.asyncio
    async def test_optimizer_chosen_schedule_forwarded(self):
        schedule = [
            {"hour": f"{h:02d}:00", "pv_w": h * 100, "active_devices": []}
            for h in range(24)
        ]
        coordinator = _make_coordinator(optimizer_chosen_schedule=schedule)
        hass, entry = _make_hass(coordinator)

        opt = (await async_get_config_entry_diagnostics(hass, entry))["optimizer"]

        assert len(opt["chosen_schedule"]) == 24
        assert opt["chosen_schedule"][12]["hour"] == "12:00"

    @pytest.mark.asyncio
    async def test_optimizer_context_forwarded(self):
        ctx = {
            "season": "spring", "cloud": "clear", "tempo": "blue",
            "bat_soc_start": 85.0, "forecast_kwh": 12.0, "peak_pv_w": 5000,
        }
        coordinator = _make_coordinator(optimizer_context=ctx)
        hass, entry = _make_hass(coordinator)

        opt = (await async_get_config_entry_diagnostics(hass, entry))["optimizer"]

        assert opt["context"]["season"]  == "spring"
        assert opt["context"]["tempo"]   == "blue"
        assert opt["context"]["peak_pv_w"] == 5000


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------

class TestBaseLoadProfile:

    @pytest.mark.asyncio
    async def test_base_load_profile_keys(self):
        coordinator = _make_coordinator()
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)
        blp = result["base_load_profile"]

        assert "sample_count" in blp
        assert "hourly_w" in blp
        assert "profile_288" in blp

    @pytest.mark.asyncio
    async def test_hourly_w_has_24_entries(self):
        coordinator = _make_coordinator(ema_profile=[400.0] * SLOTS, ema_sample_count=10)
        hass, entry = _make_hass(coordinator)

        blp = (await async_get_config_entry_diagnostics(hass, entry))["base_load_profile"]

        assert len(blp["hourly_w"]) == 24
        assert blp["hourly_w"][0]["hour"] == "00:00"
        assert blp["hourly_w"][23]["hour"] == "23:00"

    @pytest.mark.asyncio
    async def test_hourly_w_values_are_correct(self):
        """All slots at 600 W → every hourly average must be 600 W."""
        coordinator = _make_coordinator(ema_profile=[600.0] * SLOTS)
        hass, entry = _make_hass(coordinator)

        blp = (await async_get_config_entry_diagnostics(hass, entry))["base_load_profile"]

        assert all(entry["w"] == 600.0 for entry in blp["hourly_w"])

    @pytest.mark.asyncio
    async def test_profile_288_length(self):
        coordinator = _make_coordinator(ema_profile=[300.0] * SLOTS)
        hass, entry = _make_hass(coordinator)

        blp = (await async_get_config_entry_diagnostics(hass, entry))["base_load_profile"]

        assert len(blp["profile_288"]) == SLOTS

    @pytest.mark.asyncio
    async def test_sample_count_forwarded(self):
        coordinator = _make_coordinator(ema_sample_count=42)
        hass, entry = _make_hass(coordinator)

        blp = (await async_get_config_entry_diagnostics(hass, entry))["base_load_profile"]

        assert blp["sample_count"] == 42

    @pytest.mark.asyncio
    async def test_profile_none_returns_empty(self):
        """If learner has no profile yet, diagnostics must not crash."""
        coordinator = _make_coordinator()
        coordinator.consumption_learner = _make_learner(profile=None, sample_count=0)
        hass, entry = _make_hass(coordinator)

        blp = (await async_get_config_entry_diagnostics(hass, entry))["base_load_profile"]

        assert blp["sample_count"] == 0
        assert blp["hourly_w"] == []
        assert blp["profile_288"] == []


class TestDecisionLog:

    @pytest.mark.asyncio
    async def test_empty_log(self):
        coordinator = _make_coordinator(log_entries=[])
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["decision_log"] == []

    @pytest.mark.asyncio
    async def test_log_entries_forwarded(self):
        entries = [
            {
                "ts": "2026-03-22T08:30:00",
                "device": "Piscine",
                "action": "on",
                "reason": "dispatch",
                "battery_soc": 72.5,
                "pv_w": 2400,
                "house_w": 850,
                "global_score": 0.81,
                "surplus_w": 1550,
                "bat_available_w": 600,
                "fit": 0.92,
            },
            {
                "ts": "2026-03-22T09:00:00",
                "device": "Piscine",
                "action": "off",
                "reason": "satisfied",
                "battery_soc": 74.0,
                "pv_w": 2600,
                "house_w": 900,
            },
        ]
        coordinator = _make_coordinator(log_entries=entries)
        hass, entry = _make_hass(coordinator)

        log = (await async_get_config_entry_diagnostics(hass, entry))["decision_log"]

        assert len(log) == 2
        assert log[0]["device"] == "Piscine"
        assert log[0]["action"] == "on"
        assert log[0]["battery_soc"] == 72.5
        assert log[0]["pv_w"] == 2400
        assert log[0]["house_w"] == 850
        assert log[1]["reason"] == "satisfied"

    @pytest.mark.asyncio
    async def test_log_is_a_list_not_deque(self):
        """decision_log must be serialisable (list, not deque)."""
        entries = [{"ts": "2026-03-22T10:00:00", "device": "X", "action": "on", "reason": "dispatch"}]
        coordinator = _make_coordinator(log_entries=entries)
        hass, entry = _make_hass(coordinator)

        log = (await async_get_config_entry_diagnostics(hass, entry))["decision_log"]

        assert isinstance(log, list)

    @pytest.mark.asyncio
    async def test_log_with_missing_optional_fields(self):
        """Log entries with only the mandatory fields must not cause errors."""
        entries = [
            {"ts": "2026-03-22T10:00:00", "device": "Piscine", "action": "on", "reason": "must_run"},
        ]
        coordinator = _make_coordinator(log_entries=entries)
        hass, entry = _make_hass(coordinator)

        log = (await async_get_config_entry_diagnostics(hass, entry))["decision_log"]

        assert log[0]["reason"] == "must_run"
        # Optional fields absent — no KeyError
        assert "global_score" not in log[0]
        assert "fit" not in log[0]


# ---------------------------------------------------------------------------
# Robustness — missing / None entities
# ---------------------------------------------------------------------------

class TestRobustness:

    @pytest.mark.asyncio
    async def test_no_battery(self):
        """Integration without battery: battery_soc is None, no crash."""
        coordinator = _make_coordinator(battery_soc=None)
        coordinator.bat_available_w = 0.0
        coordinator.battery_action  = "idle"
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["current_state"]["battery_soc"] is None
        assert result["current_state"]["bat_available_w"] == 0.0

    @pytest.mark.asyncio
    async def test_no_forecast(self):
        """Integration without forecast entity: forecast_kwh is None, no crash."""
        coordinator = _make_coordinator()
        coordinator.forecast_kwh = None
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["current_state"]["forecast_kwh"] is None

    @pytest.mark.asyncio
    async def test_no_tempo(self):
        """Integration without Tempo entity: tempo_color is None, no crash."""
        coordinator = _make_coordinator()
        coordinator.tempo_color = None
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["current_state"]["tempo_color"] is None

    @pytest.mark.asyncio
    async def test_result_is_json_serialisable(self):
        """The entire payload must be JSON-serialisable (no deque, no MagicMock)."""
        import json

        schedule = [{"hour": f"{h:02d}:00", "pv_w": 0, "active_devices": []} for h in range(24)]
        top20    = [{"rank": 1, "objective": 0.85}]
        entries  = [{"ts": "2026-03-22T08:00:00", "device": "X", "action": "on", "reason": "dispatch"}]

        coordinator = _make_coordinator(
            battery_soc=55.0,
            optimizer_top20=top20,
            optimizer_chosen_schedule=schedule,
            optimizer_last_run="2026-03-22T05:00:00+00:00",
            log_entries=entries,
        )
        hass, entry = _make_hass(coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        # Must not raise
        serialised = json.dumps(result)
        assert len(serialised) > 0
