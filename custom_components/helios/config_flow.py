"""Config flow and options flow for Energy Optimizer."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_TEMPO_COLOR_ENTITY,
    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY, CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX, CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_DEVICES,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET,
    CONF_HVAC_TEMP_ENTITY, CONF_HVAC_SETPOINT_ENTITY,
    CONF_APPLIANCE_PROGRAM_ENTITY,
    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_FORECAST,
    CONF_SCAN_INTERVAL_MINUTES, CONF_MODE,
    DEVICE_TYPES, DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPE_HVAC, DEVICE_TYPE_APPLIANCE,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_FORECAST,
    DEFAULT_SCAN_INTERVAL, DEFAULT_DEVICE_PRIORITY,
    DEFAULT_DEVICE_MIN_ON_MINUTES, DEFAULT_ALLOWED_START, DEFAULT_ALLOWED_END,
    DEFAULT_EV_SOC_TARGET, DEFAULT_WH_TEMP_TARGET,
    MODES, MODE_AUTO,
)

_LOGGER = logging.getLogger(__name__)


class EnergyOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial config flow (4 steps)."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []
        self._current_device: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 1 — Input sources
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict | None = None):
        """Step 1: energy source entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # TODO: validate entities exist in hass
            self._data.update(user_input)
            return await self.async_step_battery()

        schema = vol.Schema({
            vol.Required(CONF_PV_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_GRID_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HOUSE_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_TEMPO_COLOR_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Step 2 — Battery
    # ------------------------------------------------------------------
    async def async_step_battery(self, user_input: dict | None = None):
        """Step 2: battery configuration (optional)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()

        schema = vol.Schema({
            vol.Required(CONF_BATTERY_ENABLED, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_BATTERY_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_BATTERY_CHARGE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "input_number"])
            ),
            vol.Optional(CONF_BATTERY_DISCHARGE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "input_number"])
            ),
            vol.Optional(CONF_BATTERY_CAPACITY_KWH, default=5.0): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.5, max=100, step=0.5, unit_of_measurement="kWh")
            ),
            vol.Optional(CONF_BATTERY_SOC_MIN, default=DEFAULT_BATTERY_SOC_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=50, step=1, unit_of_measurement="%")
            ),
            vol.Optional(CONF_BATTERY_SOC_MAX, default=DEFAULT_BATTERY_SOC_MAX): selector.NumberSelector(
                selector.NumberSelectorConfig(min=50, max=100, step=1, unit_of_measurement="%")
            ),
            vol.Optional(CONF_BATTERY_SOC_RESERVE_ROUGE, default=DEFAULT_BATTERY_SOC_RESERVE_ROUGE): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            ),
        })
        return self.async_show_form(step_id="battery", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Step 3 — Devices (multi-step sub-flow)
    # ------------------------------------------------------------------
    async def async_step_devices(self, user_input: dict | None = None):
        """Step 3: device list — choose to add a device or continue."""
        if user_input is not None:
            if user_input.get("add_device"):
                return await self.async_step_device_type()
            self._data[CONF_DEVICES] = self._devices
            return await self.async_step_strategy()

        schema = vol.Schema({
            vol.Required("add_device", default=True): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="devices", data_schema=schema)

    async def async_step_device_type(self, user_input: dict | None = None):
        """Step 3a: select device type."""
        if user_input is not None:
            self._current_device = {CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE]}
            device_type = user_input[CONF_DEVICE_TYPE]
            if device_type == DEVICE_TYPE_EV:
                return await self.async_step_device_ev()
            if device_type == DEVICE_TYPE_WATER_HEATER:
                return await self.async_step_device_water_heater()
            if device_type == DEVICE_TYPE_HVAC:
                return await self.async_step_device_hvac()
            return await self.async_step_device_appliance()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(options=DEVICE_TYPES, translation_key="device_type")
            ),
        })
        return self.async_show_form(step_id="device_type", data_schema=schema)

    async def async_step_device_common(self, user_input: dict | None = None):
        """Shared fields for all device types (name, switch, power, priority, schedule)."""
        if user_input is not None:
            self._current_device.update(user_input)
            self._devices.append(self._current_device)
            self._current_device = {}
            return await self.async_step_devices()

        schema = vol.Schema({
            vol.Required(CONF_DEVICE_NAME): selector.TextSelector(),
            vol.Required(CONF_DEVICE_SWITCH_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "input_boolean"])
            ),
            vol.Required(CONF_DEVICE_POWER_W): selector.NumberSelector(
                selector.NumberSelectorConfig(min=50, max=20000, step=50, unit_of_measurement="W")
            ),
            vol.Optional(CONF_DEVICE_PRIORITY, default=DEFAULT_DEVICE_PRIORITY): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=10, step=1)
            ),
            vol.Optional(CONF_DEVICE_MIN_ON_MINUTES, default=DEFAULT_DEVICE_MIN_ON_MINUTES): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=480, step=5, unit_of_measurement="min")
            ),
            vol.Optional(CONF_DEVICE_ALLOWED_START, default=DEFAULT_ALLOWED_START): selector.TimeSelector(),
            vol.Optional(CONF_DEVICE_ALLOWED_END, default=DEFAULT_ALLOWED_END): selector.TimeSelector(),
        })
        return self.async_show_form(step_id="device_common", data_schema=schema)

    async def async_step_device_ev(self, user_input: dict | None = None):
        """EV-specific fields."""
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        schema = vol.Schema({
            vol.Optional(CONF_EV_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_EV_PLUGGED_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
            ),
            vol.Optional(CONF_EV_SOC_TARGET, default=DEFAULT_EV_SOC_TARGET): selector.NumberSelector(
                selector.NumberSelectorConfig(min=20, max=100, step=5, unit_of_measurement="%")
            ),
        })
        return self.async_show_form(step_id="device_ev", data_schema=schema)

    async def async_step_device_water_heater(self, user_input: dict | None = None):
        """Water heater specific fields."""
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        schema = vol.Schema({
            vol.Optional(CONF_WH_TEMP_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_WH_TEMP_TARGET, default=DEFAULT_WH_TEMP_TARGET): selector.NumberSelector(
                selector.NumberSelectorConfig(min=40, max=75, step=1, unit_of_measurement="°C")
            ),
        })
        return self.async_show_form(step_id="device_water_heater", data_schema=schema)

    async def async_step_device_hvac(self, user_input: dict | None = None):
        """HVAC / heat pump specific fields."""
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        schema = vol.Schema({
            vol.Optional(CONF_HVAC_TEMP_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HVAC_SETPOINT_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["climate", "number", "input_number"])
            ),
        })
        return self.async_show_form(step_id="device_hvac", data_schema=schema)

    async def async_step_device_appliance(self, user_input: dict | None = None):
        """Appliance (washer, dishwasher…) specific fields."""
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        schema = vol.Schema({
            vol.Optional(CONF_APPLIANCE_PROGRAM_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "input_select"])
            ),
        })
        return self.async_show_form(step_id="device_appliance", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 4 — Scoring strategy
    # ------------------------------------------------------------------
    async def async_step_strategy(self, user_input: dict | None = None):
        """Step 4: scoring weights and general settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            total = (
                user_input[CONF_WEIGHT_PV_SURPLUS]
                + user_input[CONF_WEIGHT_TEMPO]
                + user_input[CONF_WEIGHT_BATTERY_SOC]
                + user_input[CONF_WEIGHT_FORECAST]
            )
            if abs(total - 1.0) > 0.01:
                errors["base"] = "weights_must_sum_to_one"
            else:
                self._data.update(user_input)
                return self.async_create_entry(title="Energy Optimizer", data=self._data)

        schema = vol.Schema({
            vol.Optional(CONF_WEIGHT_PV_SURPLUS, default=DEFAULT_WEIGHT_PV_SURPLUS): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
            ),
            vol.Optional(CONF_WEIGHT_TEMPO, default=DEFAULT_WEIGHT_TEMPO): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
            ),
            vol.Optional(CONF_WEIGHT_BATTERY_SOC, default=DEFAULT_WEIGHT_BATTERY_SOC): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
            ),
            vol.Optional(CONF_WEIGHT_FORECAST, default=DEFAULT_WEIGHT_FORECAST): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
            ),
            vol.Optional(CONF_SCAN_INTERVAL_MINUTES, default=DEFAULT_SCAN_INTERVAL): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min")
            ),
            vol.Optional(CONF_MODE, default=MODE_AUTO): selector.SelectSelector(
                selector.SelectSelectorConfig(options=MODES, translation_key="mode")
            ),
        })
        return self.async_show_form(step_id="strategy", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Options flow entry point
    # ------------------------------------------------------------------
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyOptimizerOptionsFlow(config_entry)


class EnergyOptimizerOptionsFlow(OptionsFlow):
    """Options flow — reconfigure without reinstalling."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        """Show options menu: which section to edit."""
        if user_input is not None:
            section = user_input.get("section")
            if section == "sources":
                return await self.async_step_sources()
            if section == "battery":
                return await self.async_step_battery()
            if section == "strategy":
                return await self.async_step_strategy()

        schema = vol.Schema({
            vol.Required("section"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["sources", "battery", "strategy"])
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_sources(self, user_input: dict | None = None):
        """Edit source entities."""
        if user_input is not None:
            return self.async_create_entry(data={**self._entry.options, **user_input})
        # TODO: pre-fill with current values
        return self.async_show_form(step_id="sources", data_schema=vol.Schema({}))

    async def async_step_battery(self, user_input: dict | None = None):
        """Edit battery settings."""
        if user_input is not None:
            return self.async_create_entry(data={**self._entry.options, **user_input})
        return self.async_show_form(step_id="battery", data_schema=vol.Schema({}))

    async def async_step_strategy(self, user_input: dict | None = None):
        """Edit scoring weights."""
        if user_input is not None:
            return self.async_create_entry(data={**self._entry.options, **user_input})
        return self.async_show_form(step_id="strategy", data_schema=vol.Schema({}))
