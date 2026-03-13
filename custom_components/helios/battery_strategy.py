"""Battery strategy — decides charge/discharge/idle/reserve actions."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX, CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_CHARGE_ENTITY, CONF_BATTERY_DISCHARGE_ENTITY,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    TEMPO_RED,
)


class BatteryStrategy:
    """Decides what the battery should do each cycle.

    Actions:
      "charge"    — push energy into battery (from PV surplus or cheap grid)
      "discharge" — pull energy from battery to cover house load
      "reserve"   — hold SOC, do not discharge (e.g. red Tempo day)
      "idle"      — no active command
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.soc_min     = config.get(CONF_BATTERY_SOC_MIN,          DEFAULT_BATTERY_SOC_MIN)
        self.soc_max     = config.get(CONF_BATTERY_SOC_MAX,          DEFAULT_BATTERY_SOC_MAX)
        self.soc_reserve = config.get(CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE)
        self.charge_entity    = config.get(CONF_BATTERY_CHARGE_ENTITY)
        self.discharge_entity = config.get(CONF_BATTERY_DISCHARGE_ENTITY)

    def decide(self, data: dict[str, Any]) -> str:
        """Return action string based on current state.

        Priority order:
        1. Red Tempo + SOC < reserve → charge (use HC or PV)
        2. Red Tempo → reserve (protect SOC for HP period)
        3. Surplus PV + SOC < max → charge
        4. No surplus + SOC > min → discharge
        5. → idle
        """
        # TODO: implement full decision tree
        soc = data.get("battery_soc")
        surplus_w = data.get("surplus_w", 0.0)
        tempo = data.get("tempo_color")

        if soc is None:
            return "idle"

        # TODO: implement
        return "idle"

    async def async_apply(self, hass: HomeAssistant, action: str) -> None:
        """Send commands to battery charge/discharge entities.
        TODO: implement entity writes via hass.services.async_call.
        """
        pass
