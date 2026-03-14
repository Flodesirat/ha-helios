"""Battery strategy — two-state model: forced_charge or autoconsommation.

Design principles:
- Helios never directly controls charge/discharge power levels.
- It only switches between two modes by calling user-defined scripts.
- The battery's own BMS/inverter handles all power management in each mode.
- Calling a script only on state *change* avoids spamming the inverter.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_CHARGE_SCRIPT,
    CONF_BATTERY_AUTOCONSUM_SCRIPT,
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    BATTERY_ACTION_FORCED_CHARGE,
    BATTERY_ACTION_AUTOCONSOMMATION,
    TEMPO_RED,
)

_LOGGER = logging.getLogger(__name__)


class BatteryStrategy:
    """Decide battery mode and apply via user scripts."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.soc_reserve: float = config.get(
            CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE
        )
        self.charge_script: str | None = config.get(CONF_BATTERY_CHARGE_SCRIPT)
        self.autoconsum_script: str | None = config.get(CONF_BATTERY_AUTOCONSUM_SCRIPT)
        self._last_action: str | None = None

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------
    def decide(self, data: dict[str, Any]) -> str:
        """Return 'forced_charge' or 'autoconsommation'.

        forced_charge: Tempo RED and SOC below reserve threshold.
          → Fill battery during cheap HC hours before HP starts.

        autoconsommation: all other cases.
          → The inverter's native mode handles surplus absorption
            and discharge autonomously — fast and failure-safe.
        """
        soc   = data.get("battery_soc")
        tempo = data.get("tempo_color")

        if (
            tempo == TEMPO_RED
            and soc is not None
            and soc < self.soc_reserve
        ):
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
