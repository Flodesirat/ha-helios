"""Coordinator — tariff, midnight reset, persistence, savings, startup retry, errors."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helios.const import (
    DOMAIN, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_PRICE_RED_HC, CONF_PRICE_RED_HP, CONF_PRICE_WHITE_HP,
    CONF_SAMPLE_INTERVAL_SECONDS, DEFAULT_SAMPLE_INTERVAL_SECONDS,
)

from .test_init import _setup


async def _coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    assert await _setup(hass, entry)
    return hass.data[DOMAIN][entry.entry_id]


def _local(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, 0, 5, tzinfo=dt_util.get_default_time_zone())


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("color, off_peak, key", [
    ("red", True, CONF_PRICE_RED_HC),
    ("red", False, CONF_PRICE_RED_HP),
    ("white", False, CONF_PRICE_WHITE_HP),
])
async def test_price_follows_tempo_color(hass, minimal_entry_data, color, off_peak, key):
    entry = MockConfigEntry(domain=DOMAIN, data={**minimal_entry_data, key: 0.1234})
    coord = await _coordinator(hass, entry)
    coord.tempo_color = color
    with patch.object(coord, "_is_off_peak_now", return_value=off_peak):
        assert coord._current_price_eur_kwh() == 0.1234


# ---------------------------------------------------------------------------
# Midnight reset
# ---------------------------------------------------------------------------

def _fill(coord) -> None:
    coord._energy_pv_kwh = 10.0
    coord._energy_import_kwh = 2.0
    coord._savings_eur = 1.5
    coord._savings_month_eur = 20.0
    coord._savings_total_eur = 300.0


async def test_midnight_reset_keeps_month_and_total(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    _fill(coord)
    with (
        patch("custom_components.helios.coordinator.dt_util.now", return_value=_local(2026, 11, 15)),
        patch.object(coord._energy_store, "async_save", new=AsyncMock()) as save,
    ):
        await coord._async_reset_daily_energy(None)
    assert (coord._energy_pv_kwh, coord._energy_import_kwh, coord._savings_eur) == (0.0, 0.0, 0.0)
    assert coord._savings_month_eur == 20.0
    assert coord._savings_total_eur == 300.0
    save.assert_awaited_once()


async def test_midnight_reset_on_first_of_month_resets_month(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    _fill(coord)
    with (
        patch("custom_components.helios.coordinator.dt_util.now", return_value=_local(2026, 11, 1)),
        patch.object(coord._energy_store, "async_save", new=AsyncMock()),
    ):
        await coord._async_reset_daily_energy(None)
    assert coord._savings_month_eur == 0.0
    assert coord._savings_month_last_reset.day == 1
    assert coord._savings_total_eur == 300.0


async def test_daily_optimizer_failure_is_logged(hass, config_entry, caplog):
    coord = await _coordinator(hass, config_entry)
    with patch(
        "custom_components.helios.coordinator.async_run_daily_forecast",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await coord._async_daily_optimize(None)
    assert "Helios daily optimizer failed: boom" in caplog.text


# ---------------------------------------------------------------------------
# Restore from storage
# ---------------------------------------------------------------------------

async def test_optimizer_state_restored(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    stored = {
        "optimizer_last_run": "2026-11-10T05:00:00",
        "optimizer_context": {"season": "autumn"},
        "optimizer_chosen": None,
    }
    with (
        patch.object(coord._optimizer_store, "async_load", new=AsyncMock(return_value=stored)),
        patch.object(coord._energy_store, "async_load", new=AsyncMock(return_value=None)),
    ):
        await coord.async_setup()
    assert coord.optimizer_last_run == "2026-11-10T05:00:00"
    assert coord.optimizer_context == {"season": "autumn"}
    assert coord.optimizer_chosen == {}
    assert coord.optimizer_top20 == []


async def _restore(coord, stored: dict, now: datetime) -> None:
    with (
        patch.object(coord._energy_store, "async_load", new=AsyncMock(return_value=stored)),
        patch("custom_components.helios.coordinator.dt_util.now", return_value=now),
    ):
        await coord._async_restore_energy()


_STORED = {
    "date": "2026-11-15", "month": "2026-11",
    "pv_kwh": 8.0, "import_kwh": 1.0, "export_kwh": 3.0, "consumption_kwh": 6.0,
    "savings_eur": 0.9, "savings_month_eur": 12.0, "savings_total_eur": 250.0,
}


async def test_energy_restored_same_day(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    await _restore(coord, _STORED, _local(2026, 11, 15, 10))
    assert coord._energy_pv_kwh == 8.0
    assert coord._energy_consumption_kwh == 6.0
    assert coord._savings_eur == 0.9
    assert coord._savings_month_eur == 12.0
    assert coord._savings_total_eur == 250.0


async def test_energy_from_another_day_keeps_month_and_total_only(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    coord._energy_pv_kwh = 0.0
    await _restore(coord, _STORED, _local(2026, 11, 16, 10))
    assert coord._energy_pv_kwh == 0.0
    assert coord._savings_month_eur == 12.0
    assert coord._savings_total_eur == 250.0


async def test_energy_from_another_month_keeps_total_only(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    coord._savings_month_eur = 0.0
    await _restore(coord, _STORED, _local(2026, 12, 2, 10))
    assert coord._savings_month_eur == 0.0
    assert coord._savings_total_eur == 250.0


# ---------------------------------------------------------------------------
# Savings accumulation (issue #12)
# ---------------------------------------------------------------------------

async def _sample(hass, coord, *, grid: float, house: float, price: float) -> float:
    """Run one sampling tick, return the energy of 1 W over the tick (kWh)."""
    hass.states.async_set("sensor.grid_power", str(grid))
    hass.states.async_set("sensor.house_power", str(house))
    with patch.object(coord, "_current_price_eur_kwh", return_value=price):
        await coord._async_sample_sensors(None)
    sample_s = int(coord._cfg.get(CONF_SAMPLE_INTERVAL_SECONDS, DEFAULT_SAMPLE_INTERVAL_SECONDS))
    return sample_s / 3_600_000.0


def _zero_savings(coord) -> None:
    coord._savings_eur = coord._savings_month_eur = coord._savings_total_eur = 0.0


def _savings(coord) -> tuple[float, float, float]:
    return coord._savings_eur, coord._savings_month_eur, coord._savings_total_eur


async def test_grid_charge_decrements_savings(hass, config_entry):
    """Battery charged from the grid: import 3000 W > house 500 W → counters drop."""
    coord = await _coordinator(hass, config_entry)
    _zero_savings(coord)
    dt = await _sample(hass, coord, grid=3000.0, house=500.0, price=0.13)
    expected = -2500.0 * dt * 0.13
    assert _savings(coord) == pytest.approx((expected,) * 3)


async def test_discharge_increments_savings(hass, config_entry):
    """Battery feeds the house: no import → whole house consumption is saved."""
    coord = await _coordinator(hass, config_entry)
    _zero_savings(coord)
    dt = await _sample(hass, coord, grid=0.0, house=800.0, price=0.70)
    assert _savings(coord) == pytest.approx((800.0 * dt * 0.70,) * 3)


async def test_export_counts_house_only(hass, config_entry):
    """Exporting: the export itself is not valued, the house is."""
    coord = await _coordinator(hass, config_entry)
    _zero_savings(coord)
    dt = await _sample(hass, coord, grid=-1500.0, house=400.0, price=0.16)
    assert coord._savings_eur == pytest.approx(400.0 * dt * 0.16)


async def test_hc_charge_hp_discharge_cycle_keeps_arbitrage(hass, config_entry):
    """Charge 2000 W in HC, give back 1500 W in HP (losses): net = arbitrage gain."""
    coord = await _coordinator(hass, config_entry)
    _zero_savings(coord)
    # HC: house 300 W, battery charges 2000 W from the grid → import 2300 W
    dt = await _sample(hass, coord, grid=2300.0, house=300.0, price=0.13)
    assert coord._savings_eur < 0
    # HP: house 1500 W fully covered by the battery
    await _sample(hass, coord, grid=0.0, house=1500.0, price=0.70)
    assert coord._savings_eur == pytest.approx((1500.0 * 0.70 - 2000.0 * 0.13) * dt)


# ---------------------------------------------------------------------------
# Manual savings correction (service helios.set_savings)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
    # daily corrected → delta propagated to month and total
    ({"daily": 3.0}, (3.0, 22.0, 302.0)),
    # month given explicitly → total follows the month delta
    ({"monthly": 25.0}, (1.0, 25.0, 305.0)),
    # every counter given → no propagation
    ({"daily": 2.0, "monthly": 30.0, "total": 400.0}, (2.0, 30.0, 400.0)),
])
async def test_set_savings_propagates_delta(hass, config_entry, kwargs, expected):
    coord = await _coordinator(hass, config_entry)
    coord._savings_eur, coord._savings_month_eur, coord._savings_total_eur = 1.0, 20.0, 300.0
    with patch.object(coord._energy_store, "async_save", new=AsyncMock()) as save:
        await coord.async_set_savings(**kwargs)
    assert (coord._savings_eur, coord._savings_month_eur, coord._savings_total_eur) == expected
    save.assert_awaited_once()


# ---------------------------------------------------------------------------
# Startup: wait for the source entities
# ---------------------------------------------------------------------------

async def test_startup_retry_until_sources_ready(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    hass.states.async_set("sensor.grid_power", "unavailable")
    await coord.async_refresh()
    assert coord._startup_check_unsub is not None

    # Still unavailable after 60 s → retried again
    first = coord._startup_check_unsub
    first()  # the timer fires: simulated by a direct call below
    await coord._async_startup_refresh()
    assert coord._startup_check_unsub is not None and coord._startup_check_unsub is not first

    # Sources back → refresh runs, no further retry
    coord._startup_check_unsub()  # the second timer fires
    hass.states.async_set("sensor.grid_power", "100")
    with patch.object(coord, "async_refresh", new=AsyncMock()) as refresh:
        await coord._async_startup_refresh()
    refresh.assert_awaited_once()
    assert coord._startup_check_unsub is None


async def test_pending_retry_cancelled_when_sources_come_back(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    hass.states.async_set("sensor.house_power", "unknown")
    await coord.async_refresh()
    assert coord._startup_check_unsub is not None
    hass.states.async_set("sensor.house_power", "800")
    await coord.async_refresh()
    assert coord._startup_check_unsub is None


async def test_unconfigured_source_does_not_block(hass, minimal_entry_data):
    data = {**minimal_entry_data}
    del data[CONF_GRID_POWER_ENTITY]
    del data[CONF_HOUSE_POWER_ENTITY]
    coord = await _coordinator(hass, MockConfigEntry(domain=DOMAIN, data=data))
    assert coord._sources_ready() is True


# ---------------------------------------------------------------------------
# Errors and bad readings
# ---------------------------------------------------------------------------

async def test_update_error_raises_update_failed(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    with (
        patch.object(coord, "_read_sensors", new=AsyncMock(side_effect=RuntimeError("bad"))),
        pytest.raises(UpdateFailed, match="Helios update failed: bad"),
    ):
        await coord._async_update_data()


async def test_non_numeric_power_reads_as_zero(hass, config_entry):
    coord = await _coordinator(hass, config_entry)
    coord._buf_pv.clear()
    hass.states.async_set("sensor.pv_power", "n/a")
    await coord._async_sample_sensors(None)
    assert list(coord._buf_pv) == [0.0]
