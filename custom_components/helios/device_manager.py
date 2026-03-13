"""Device manager — maintains device registry and dispatches on/off commands."""
from __future__ import annotations

import logging
from datetime import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET,
    CONF_HVAC_TEMP_ENTITY, CONF_HVAC_SETPOINT_ENTITY,
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC, DEVICE_TYPE_APPLIANCE,
)

_LOGGER = logging.getLogger(__name__)


class ManagedDevice:
    """Represents one configurable device."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.name: str = config[CONF_DEVICE_NAME]
        self.device_type: str = config[CONF_DEVICE_TYPE]
        self.switch_entity: str = config[CONF_DEVICE_SWITCH_ENTITY]
        self.power_w: float = config[CONF_DEVICE_POWER_W]
        self.priority: int = config.get(CONF_DEVICE_PRIORITY, 5)
        self.min_on_minutes: int = config.get(CONF_DEVICE_MIN_ON_MINUTES, 30)
        self.allowed_start: str = config.get(CONF_DEVICE_ALLOWED_START, "00:00")
        self.allowed_end: str   = config.get(CONF_DEVICE_ALLOWED_END,   "23:59")

        # Type-specific
        self.ev_soc_entity:       str | None = config.get(CONF_EV_SOC_ENTITY)
        self.ev_plugged_entity:   str | None = config.get(CONF_EV_PLUGGED_ENTITY)
        self.ev_soc_target:       float      = config.get(CONF_EV_SOC_TARGET, 80)
        self.wh_temp_entity:      str | None = config.get(CONF_WH_TEMP_ENTITY)
        self.wh_temp_target:      float      = config.get(CONF_WH_TEMP_TARGET, 55)
        self.hvac_temp_entity:    str | None = config.get(CONF_HVAC_TEMP_ENTITY)
        self.hvac_setpoint_entity:str | None = config.get(CONF_HVAC_SETPOINT_ENTITY)

        # Runtime state
        self.is_on: bool = False
        self.on_since_minutes: float = 0.0
        self.blocked_until: float | None = None  # epoch timestamp

    # ------------------------------------------------------------------
    # Eligibility checks
    # ------------------------------------------------------------------
    def is_in_allowed_window(self, now: time) -> bool:
        """Return True if current time is within the device's allowed schedule."""
        # TODO: implement
        return True

    def is_satisfied(self, hass: HomeAssistant) -> bool:
        """Return True if the device has already reached its target (SOC, temp…).
        Satisfied devices should not be turned on even with high score.
        """
        # TODO: per-type satisfaction check
        return False

    def device_score_modifier(self, hass: HomeAssistant) -> float:
        """Return a [0..1] modifier based on device-specific urgency.
        EV: low SOC → high modifier. Water heater: low temp → high modifier.
        """
        # TODO: implement per type
        return 1.0


class DeviceManager:
    """Manages the full list of devices and dispatches actions."""

    def __init__(self, hass: HomeAssistant, devices_config: list[dict]) -> None:
        self.devices: list[ManagedDevice] = [
            ManagedDevice(cfg) for cfg in devices_config
        ]

    async def async_dispatch(self, hass: HomeAssistant, score_input: dict[str, Any]) -> None:
        """Rank eligible devices and turn them on/off based on available surplus.

        Algorithm (to implement):
        1. Compute each device's effective score = global_score × priority_weight × device_modifier
        2. Sort descending
        3. Greedily assign surplus_w to devices (highest score first)
        4. Devices that fit within surplus → turn ON
        5. Devices that exceed available power → turn OFF
        6. Respect min_on_minutes (don't turn off a device that just started)
        """
        # TODO: implement dispatch loop
        pass

    async def _async_set_device(self, hass: HomeAssistant, device: ManagedDevice, on: bool) -> None:
        """Call switch.turn_on or switch.turn_off for a device."""
        service = "turn_on" if on else "turn_off"
        await hass.services.async_call(
            "homeassistant",
            service,
            {"entity_id": device.switch_entity},
            blocking=False,
        )
        device.is_on = on
        _LOGGER.debug("%s → %s", device.name, service)
