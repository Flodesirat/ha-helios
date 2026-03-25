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


def _parse_time(value: str | None) -> time | None:
    """Parse 'HH:MM' string to time, or None."""
    if not value:
        return None
    try:
        h, m = value.split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _in_slot(now: time, start: time, end: time) -> bool:
    """Return True if *now* is in [start, end) — handles midnight crossing."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class BatteryStrategy:
    """Decide battery mode and apply via user scripts."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.charge_script: str | None = config.get(CONF_BATTERY_CHARGE_SCRIPT)
        self.autoconsum_script: str | None = config.get(CONF_BATTERY_AUTOCONSUM_SCRIPT)
        self._off_peak_slots: list[tuple[time, time]] = []
        for start_key, end_key in (
            (CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END),
            (CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END),
        ):
            s = _parse_time(config.get(start_key))
            e = _parse_time(config.get(end_key))
            if s is not None and e is not None:
                self._off_peak_slots.append((s, e))
        self._last_action: str | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_off_peak(self, now: time) -> bool:
        return any(_in_slot(now, s, e) for s, e in self._off_peak_slots)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------
    def decide(self, data: dict[str, Any]) -> str:
        """Return 'forced_charge' or 'autoconsommation'.

        forced_charge: currently in HC AND tomorrow is RED.
          → Fill battery during cheap HC hours before the expensive red day.

        autoconsommation: all other cases.
        """
        next_color = data.get("tempo_next_color")
        now        = datetime.now().time()

        if next_color == TEMPO_RED and self._is_off_peak(now):
            return BATTERY_ACTION_FORCED_CHARGE

        return BATTERY_ACTION_AUTOCONSOMMATION

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    async def async_apply(self, hass: HomeAssistant, action: str) -> None:
        """Call the appropriate user script — only on state change."""
        if action == self._last_action:
            return  # nothing changed, avoid hammering the inverter

        script = (
            self.charge_script
            if action == BATTERY_ACTION_FORCED_CHARGE
            else self.autoconsum_script
        )

        if script:
            await hass.services.async_call(
                "script",
                "turn_on",
                {"entity_id": script},
                blocking=False,
            )
            _LOGGER.info("Battery → %s (script: %s)", action, script)
        else:
            _LOGGER.debug(
                "Battery action '%s' has no script configured — skipping", action
            )

        self._last_action = action
