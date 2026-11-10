"""Button entity — manual daily optimization."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.helios.button import ForceOptimizationButton


async def test_press_runs_optimization_then_refreshes():
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    button = ForceOptimizationButton(coordinator, MagicMock(entry_id="e1"))
    button.hass = MagicMock()
    with patch(
        "custom_components.helios.button.async_run_daily_forecast", new=AsyncMock()
    ) as run:
        await button.async_press()
    run.assert_awaited_once_with(button.hass, coordinator)
    coordinator.async_request_refresh.assert_awaited_once()
    assert button.unique_id == "e1_force_optimization"
