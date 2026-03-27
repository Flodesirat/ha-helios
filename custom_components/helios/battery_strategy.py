"""Battery strategy — two-state model: forced_charge or autoconsommation.

Design principles:
- Helios never directly controls charge/discharge power levels.
- It only switches between two modes by calling user-defined scripts.
- The battery's own BMS/inverter handles all power management in each mode.
- Calling a script only on state *change* avoids spamming the inverter.
- forced_charge triggers only during HC hours when tomorrow is a red day.
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BATTERY_CHARGE_SCRIPT,
    CONF_BATTERY_AUTOCONSUM_SCRIPT,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
    BATTERY_ACTION_FORCED_CHARGE,
    BATTERY_ACTION_AUTOCONSOMMATION,
    TEMPO_RED,
)

_LOGGER = logging.getLogger(__name__)

_OFF_PEAK_PAIRS = (
    (CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END),
    (CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END),
)


def _parse_time(value: str | None) -> time | None:
    """Parse 'HH:MM' or 'HH:MM:SS' string to a time object, or None."""
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _in_slot(now: time, start: time, end: time) -> bool:
    """True if *now* ∈ [start, end) — handles midnight crossing."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class BatteryStrategy:
    """Decide battery mode and apply via user scripts."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.charge_script: str | None = config.get(CONF_BATTERY_CHARGE_SCRIPT)
        self.autoconsum_script: str | None = config.get(CONF_BATTERY_AUTOCONSUM_SCRIPT)
        self._off_peak_slots: list[tuple[time, time]] = [
            (s, e)
            for sk, ek in _OFF_PEAK_PAIRS
            if (s := _parse_time(config.get(sk))) is not None
            and (e := _parse_time(config.get(ek))) is not None
        ]
        self._last_action: str | None = None

    def _is_off_peak(self, now: time) -> bool:
        return any(_in_slot(now, s, e) for s, e in self._off_peak_slots)

    def decide(self, data: dict[str, Any]) -> str:
        """Return 'forced_charge' or 'autoconsommation'."""
        if data.get("tempo_next_color") == TEMPO_RED and self._is_off_peak(datetime.now().time()):
            return BATTERY_ACTION_FORCED_CHARGE
        return BATTERY_ACTION_AUTOCONSOMMATION

    async def async_apply(self, hass: HomeAssistant, action: str) -> None:
        """Call the appropriate user script — only on state change."""
        if action == self._last_action:
            return

        script = self.charge_script if action == BATTERY_ACTION_FORCED_CHARGE else self.autoconsum_script

        if script:
            await hass.services.async_call(
                "script", "turn_on", {"entity_id": script}, blocking=False,
            )
            _LOGGER.info("Battery → %s (script: %s)", action, script)
        else:
            _LOGGER.debug("Battery action '%s' has no script configured — skipping", action)

        self._last_action = action
