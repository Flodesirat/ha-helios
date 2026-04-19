"""Tests for the coordinator startup guard.

Dispatch is skipped until all mandatory source entities have a valid state.
Sensors ARE always read and state IS always updated.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.coordinator import EnergyOptimizerCoordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(enabled: bool = True, sources_ready: bool = True) -> MagicMock:
    """Build a minimal coordinator mock with _async_update_data bound to it."""
    coord = MagicMock(spec=EnergyOptimizerCoordinator)

    # HA runtime
    coord.hass = MagicMock()

    # Core state
    coord.enabled = enabled
    coord._cfg = {"battery_enabled": False}
    coord.dispatch_threshold = 0.3
    coord.grid_allowance_w   = 250.0
    coord.bat_available_w    = 0.0
    coord.global_score       = 0.0
    coord.pv_power_w         = 0.0
    coord.grid_power_w       = 0.0
    coord.house_power_w      = 0.0
    coord.surplus_w          = 0.0
    coord.battery_soc        = None
    coord.battery_power_w    = None
    coord.tempo_color        = None
    coord.tempo_next_color   = None
    coord.forecast_kwh       = None
    coord.battery_action     = "autoconsommation"

    # Scoring & dispatch mocks
    coord.scoring_engine = MagicMock()
    coord.scoring_engine.compute.return_value = 0.8
    coord.scoring_engine.compute_components.return_value = (0.5, 0.3, 0.2)
    coord.device_manager = MagicMock()
    coord.device_manager.async_dispatch = AsyncMock()
    coord.battery_strategy = MagicMock()
    coord.battery_strategy.decide.return_value = "idle"
    coord.battery_strategy.async_apply = AsyncMock()

    # Sensor / state helpers
    _raw = {
        "pv_power_w": 1000.0, "grid_power_w": 0.0, "house_power_w": 200.0,
        "battery_soc": None, "battery_power_w": None,
        "tempo_color": "blue", "tempo_next_color": None, "forecast_kwh": None,
    }
    coord._read_sensors = AsyncMock(return_value=_raw)
    coord._update_state = MagicMock()
    coord._build_score_input = MagicMock(return_value={"surplus_w": 800.0})
    coord._snapshot = MagicMock(return_value={"enabled": enabled})
    coord._sources_ready = MagicMock(return_value=sources_ready)
    coord._startup_check_unsub = None

    # Bind the real method
    coord._async_update_data = (
        EnergyOptimizerCoordinator._async_update_data.__get__(coord)
    )
    return coord


# ---------------------------------------------------------------------------
# Sources not ready — dispatch must NOT run
# ---------------------------------------------------------------------------

class TestSourcesNotReady:

    @pytest.mark.asyncio
    async def test_dispatch_not_called_when_sources_unavailable(self):
        """device_manager.async_dispatch must not be called while sources are unavailable."""
        coord = _make_coordinator(sources_ready=False)
        await coord._async_update_data()
        coord.device_manager.async_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_scoring_not_called_when_sources_unavailable(self):
        """scoring_engine.compute must not be called while sources are unavailable."""
        coord = _make_coordinator(sources_ready=False)
        await coord._async_update_data()
        coord.scoring_engine.compute.assert_not_called()

    @pytest.mark.asyncio
    async def test_sensors_read_when_sources_unavailable(self):
        """Sensors must still be read so state reflects reality as soon as possible."""
        coord = _make_coordinator(sources_ready=False)
        await coord._async_update_data()
        coord._read_sensors.assert_called_once()
        coord._update_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot_returned_when_sources_unavailable(self):
        """A snapshot must be returned even when sources are unavailable."""
        coord = _make_coordinator(sources_ready=False)
        result = await coord._async_update_data()
        assert result == {"enabled": True}


# ---------------------------------------------------------------------------
# Sources ready — dispatch MUST run
# ---------------------------------------------------------------------------

class TestSourcesReady:

    @pytest.mark.asyncio
    async def test_dispatch_called_when_sources_ready(self):
        """device_manager.async_dispatch must be called when all sources are available."""
        coord = _make_coordinator(sources_ready=True)
        await coord._async_update_data()
        coord.device_manager.async_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_scoring_called_when_sources_ready(self):
        """scoring_engine.compute must be called when all sources are available."""
        coord = _make_coordinator(sources_ready=True)
        await coord._async_update_data()
        coord.scoring_engine.compute.assert_called_once()

    @pytest.mark.asyncio
    async def test_sensors_read_when_sources_ready(self):
        """Sensors are still read after sources become available."""
        coord = _make_coordinator(sources_ready=True)
        await coord._async_update_data()
        coord._read_sensors.assert_called_once()


# ---------------------------------------------------------------------------
# enabled=False — always skips dispatch regardless of source state
# ---------------------------------------------------------------------------

class TestDisabled:

    @pytest.mark.asyncio
    async def test_dispatch_not_called_when_disabled_sources_unavailable(self):
        coord = _make_coordinator(enabled=False, sources_ready=False)
        await coord._async_update_data()
        coord.device_manager.async_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_not_called_when_disabled_sources_ready(self):
        """Disabled must skip dispatch even when sources are available."""
        coord = _make_coordinator(enabled=False, sources_ready=True)
        await coord._async_update_data()
        coord.device_manager.async_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# _sources_ready unit tests
# ---------------------------------------------------------------------------

class TestSourcesReadyUnit:

    def _make_hass_state(self, state: str) -> MagicMock:
        s = MagicMock()
        s.state = state
        return s

    def _make_real_coordinator(self, entity_states: dict[str, str | None]) -> EnergyOptimizerCoordinator:
        """Build a real (non-mocked) coordinator to test _sources_ready."""
        from custom_components.helios.const import (
            CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
        )
        coord = MagicMock(spec=EnergyOptimizerCoordinator)
        coord._cfg = {
            CONF_PV_POWER_ENTITY:   "sensor.pv",
            CONF_GRID_POWER_ENTITY: "sensor.grid",
            CONF_HOUSE_POWER_ENTITY: "sensor.house",
        }

        def _get(entity_id: str) -> MagicMock | None:
            state = entity_states.get(entity_id)
            if state is None:
                return None
            return self._make_hass_state(state)

        coord.hass = MagicMock()
        coord.hass.states.get.side_effect = _get
        coord._sources_ready = EnergyOptimizerCoordinator._sources_ready.__get__(coord)
        return coord

    def test_all_valid_returns_true(self):
        coord = self._make_real_coordinator({
            "sensor.pv": "500", "sensor.grid": "0", "sensor.house": "200",
        })
        assert coord._sources_ready() is True

    def test_one_unavailable_returns_false(self):
        coord = self._make_real_coordinator({
            "sensor.pv": "unavailable", "sensor.grid": "0", "sensor.house": "200",
        })
        assert coord._sources_ready() is False

    def test_one_unknown_returns_false(self):
        coord = self._make_real_coordinator({
            "sensor.pv": "500", "sensor.grid": "unknown", "sensor.house": "200",
        })
        assert coord._sources_ready() is False

    def test_one_missing_returns_false(self):
        coord = self._make_real_coordinator({
            "sensor.pv": "500", "sensor.grid": "0",
            # sensor.house absent → hass.states.get returns None
        })
        assert coord._sources_ready() is False
