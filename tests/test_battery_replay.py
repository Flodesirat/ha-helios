"""Replay of a recorded day (16/10/2026) through the battery Drive controller.

The recording (from the marstek-smartmeter project) gives the house's net
load — consumption minus solar, battery excluded — at each OWON meter report.
Each report goes through BatteryDriveController.async_handle_reading, as in
Home Assistant. A simulated Venus A follows what its integration sends:

- at once when the written value moves by 30 W or more from the last one
  sent, otherwise on the next report 60 s after the last send (the
  integration's own rule, which Helios does not model);
- 3 s after it was sent.

The reference (540 Wh import, 119 Wh export) is marstek-smartmeter's
tools/replay.py with the settings of tools/ha_drive.py — target 30 W, settle
3 s, 1450 W, 30 W step — which knows exactly what was sent (the _Sent model).
Helios does not: it counts a written value as applied.
"""
from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.battery_drive import BatteryDriveController
from custom_components.helios.managed_device import BatteryDevice
from custom_components.helios.const import (
    CONF_BATTERIES, CONF_BATTERY_ENABLED, CONF_BATTERY_NAME,
    CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_GRID_TARGET_BLUE_W, CONF_GRID_POWER_ENTITY,
    BATTERY_STATE_REGULATING,
)

_RECORDING = Path(__file__).parent / "data" / "2026-10-16_house.csv"
_MAX_W = 1450.0
_MIN_STEP_W = 30.0     # integration: sent at once from this move
_REFRESH_S = 60.0      # integration: otherwise sent on its refresh
_FOLLOW_S = 3.0        # battery follows a sent setpoint after ~3 s

REFERENCE_IMPORT_WH = 540.0
REFERENCE_EXPORT_WH = 119.0


def _load_house() -> list[tuple[float, float]]:
    with open(_RECORDING, encoding="utf-8") as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return [(float(r["seconds"]), float(r["house_w"])) for r in rows]


def _controller() -> tuple[BatteryDriveController, list[float]]:
    """A controller driving one battery, its number.set_value calls recorded."""
    written: list[float] = []

    async def _async_call(domain, service, data=None, blocking=False):  # noqa: ARG001
        if (domain, service) == ("number", "set_value"):
            written.append(float(data["value"]))

    hass = MagicMock()
    hass.services.async_call = AsyncMock(side_effect=_async_call)
    coordinator = MagicMock()
    coordinator.config = {
        CONF_BATTERY_ENABLED: True,
        CONF_GRID_POWER_ENTITY: "sensor.grid",
        CONF_BATTERY_GRID_TARGET_BLUE_W: 30,
        CONF_BATTERIES: [{
            CONF_BATTERY_NAME: "Venus A",
            CONF_BATTERY_DRIVE_POWER_ENTITY: "number.venus_drive",
            CONF_BATTERY_MAX_CHARGE_POWER_W: _MAX_W,
            CONF_BATTERY_MAX_DISCHARGE_POWER_W: _MAX_W,
        }],
    }
    coordinator.tempo_color = "blue"
    coordinator.tempo_next_color = "blue"
    coordinator.is_off_peak_now.return_value = False  # 10:09–17:26, daytime
    coordinator.device_manager.battery_device = BatteryDevice({})  # no force
    ctrl = BatteryDriveController(hass, coordinator)
    ctrl.active = True  # as after the Drive script
    return ctrl, written


async def _replay() -> dict[str, float]:
    house = _load_house()
    ctrl, written = _controller()
    battery: list[tuple[float, float]] = [(float("-inf"), 0.0)]  # (takes effect at, W)
    sent, sent_at = 0.0, float("-inf")

    def battery_at(t: float) -> float:
        power = 0.0
        for at, p in battery:
            if at <= t:
                power = p
        return power

    for t, load in house:
        await ctrl.async_handle_reading(load - battery_at(t), t)
        value = written[-1] if written else 0.0
        if abs(value - sent) >= _MIN_STEP_W or t - sent_at >= _REFRESH_S:
            sent_at = t  # a refresh of the same value restarts the 60 s too
            if value != sent:
                sent = value
                battery.append((t + _FOLLOW_S, value))

    # Grid energy between reports: house load minus battery output
    result = {"import_wh": 0.0, "export_wh": 0.0, "writes": float(len(written))}
    breaks = sorted({t for t, _ in house} | {t for t, _ in battery if t > house[0][0]})
    for a, b in zip(breaks, breaks[1:]):
        if b - a > 300:
            continue  # recording gap
        load = next(l for t, l in reversed(house) if t <= a)
        grid = load - battery_at(a)
        key = "import_wh" if grid >= 0 else "export_wh"
        result[key] += abs(grid) * (b - a) / 3600
    result["state"] = ctrl.state
    return result


@pytest.mark.asyncio
async def test_replay_16_10_close_to_reference():
    """Without the _Sent model, import stays within 5 % of the reference."""
    r = await _replay()
    print(
        f"\nreplay 16/10: import {r['import_wh']:.0f} Wh (ref {REFERENCE_IMPORT_WH:.0f}), "
        f"export {r['export_wh']:.0f} Wh (ref {REFERENCE_EXPORT_WH:.0f}), {r['writes']:.0f} writes"
    )
    assert r["state"] == BATTERY_STATE_REGULATING
    assert r["import_wh"] <= REFERENCE_IMPORT_WH * 1.05
    assert r["export_wh"] <= REFERENCE_EXPORT_WH * 1.10
