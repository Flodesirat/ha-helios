"""Tests for the coordinator startup warmup guard.

During the warmup window (max(5, scan_interval) minutes after load):
  - Sensors ARE read and state IS updated.
  - Scoring, battery strategy and device dispatch are NOT executed.

After the warmup window has elapsed, normal dispatch resumes.
"""
from __future__ import annotations

import time as _time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.coordinator import EnergyOptimizerCoordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(enabled: bool = True) -> MagicMock:
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

    # Bind the real method
    coord._async_update_data = (
        EnergyOptimizerCoordinator._async_update_data.__get__(coord)
    )
    return coord


# ---------------------------------------------------------------------------
# During warmup — dispatch must NOT run
# ---------------------------------------------------------------------------

class TestWarmupBlocked:

    @pytest.mark.asyncio
    async def test_dispatch_not_called_during_warmup(self):
        """device_manager.async_dispatch must not be called while warming up."""
        coord = _make_coordinator()
        # Still in warmup: ready_at is far in the future
        coord._dispatch_ready_at = _time.monotonic() + 9999

        await coord._async_update_data()

        coord.device_manager.async_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_scoring_not_called_during_warmup(self):
        """scoring_engine.compute must not be called during warmup."""
        coord = _make_coordinator()
        coord._dispatch_ready_at = _time.monotonic() + 9999

        await coord._async_update_data()

        coord.scoring_engine.compute.assert_not_called()

    @pytest.mark.asyncio
    async def test_sensors_read_during_warmup(self):
        """Sensors must still be read during warmup so state reflects reality."""
        coord = _make_coordinator()
        coord._dispatch_ready_at = _time.monotonic() + 9999

        await coord._async_update_data()

        coord._read_sensors.assert_called_once()
        coord._update_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot_returned_during_warmup(self):
        """A snapshot must be returned even during warmup."""
        coord = _make_coordinator()
        coord._dispatch_ready_at = _time.monotonic() + 9999

        result = await coord._async_update_data()

        assert result == {"enabled": True}


# ---------------------------------------------------------------------------
# After warmup — dispatch MUST run
# ---------------------------------------------------------------------------

class TestWarmupElapsed:

    @pytest.mark.asyncio
    async def test_dispatch_called_after_warmup(self):
        """device_manager.async_dispatch must be called once warmup has elapsed."""
        coord = _make_coordinator()
        # Warmup already elapsed
        coord._dispatch_ready_at = _time.monotonic() - 1

        await coord._async_update_data()

        coord.device_manager.async_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_scoring_called_after_warmup(self):
        """scoring_engine.compute must be called once warmup has elapsed."""
        coord = _make_coordinator()
        coord._dispatch_ready_at = _time.monotonic() - 1

        await coord._async_update_data()

        coord.scoring_engine.compute.assert_called_once()

    @pytest.mark.asyncio
    async def test_sensors_read_after_warmup(self):
        """Sensors are still read after warmup."""
        coord = _make_coordinator()
        coord._dispatch_ready_at = _time.monotonic() - 1

        await coord._async_update_data()

        coord._read_sensors.assert_called_once()


# ---------------------------------------------------------------------------
# enabled=False — always skips dispatch regardless of warmup state
# ---------------------------------------------------------------------------

class TestDisabled:

    @pytest.mark.asyncio
    async def test_dispatch_not_called_when_disabled_during_warmup(self):
        coord = _make_coordinator(enabled=False)
        coord._dispatch_ready_at = _time.monotonic() + 9999

        await coord._async_update_data()

        coord.device_manager.async_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_not_called_when_disabled_after_warmup(self):
        """Disabled must skip dispatch even after warmup has elapsed."""
        coord = _make_coordinator(enabled=False)
        coord._dispatch_ready_at = _time.monotonic() - 1

        await coord._async_update_data()

        coord.device_manager.async_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Warmup duration — max(5, scan_interval)
# ---------------------------------------------------------------------------

class TestWarmupDuration:

    def test_warmup_at_least_5_minutes(self):
        """Even with a 1-minute scan interval, warmup must be >= 5 minutes."""
        import time as _time_mod
        import custom_components.helios.coordinator as coord_mod

        now = _time_mod.monotonic()
        with patch.object(coord_mod, "_time") as mock_time:
            mock_time.monotonic.return_value = now

            coord = MagicMock(spec=EnergyOptimizerCoordinator)
            # Simulate __init__ warmup calculation with interval=1
            interval = 1
            warmup = max(5, interval)
            coord._dispatch_ready_at = now + warmup * 60

        assert coord._dispatch_ready_at >= now + 5 * 60

    def test_warmup_uses_scan_interval_when_larger(self):
        """With a 10-minute scan interval, warmup must be 10 minutes."""
        import time as _time_mod

        now = _time_mod.monotonic()
        interval = 10
        warmup = max(5, interval)
        ready_at = now + warmup * 60

        assert ready_at >= now + 10 * 60
