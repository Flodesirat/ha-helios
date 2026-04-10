"""Config flow and options flow for Helios Energy Optimizer."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    # Sources
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_POWER_ENTITY, CONF_TEMPO_COLOR_ENTITY, CONF_TEMPO_NEXT_COLOR_ENTITY,
    CONF_FORECAST_ENTITY,
    CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W,
    CONF_GRID_SUBSCRIPTION_W, DEFAULT_GRID_SUBSCRIPTION_W,
    # Battery
    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_CHARGE_SCRIPT, CONF_BATTERY_AUTOCONSUM_SCRIPT,
    CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    # Devices list
    CONF_DEVICES,
    # Common device fields
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_POWER_ENTITY, CONF_DEVICE_PRIORITY,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_DEVICE_MUST_RUN_DAILY, CONF_DEVICE_DEADLINE,
    CONF_DEVICE_WEIGHT_PRIORITY, CONF_DEVICE_WEIGHT_FIT, CONF_DEVICE_WEIGHT_URGENCY,
    # EV
    CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET, CONF_EV_PLUGGED_ENTITY,
    CONF_EV_DEPARTURE_TIME, CONF_EV_MIN_CHARGE_POWER_W, CONF_EV_BATTERY_CAPACITY_WH,
    CONF_EV_CHARGE_START_SCRIPT, CONF_EV_CHARGE_STOP_SCRIPT,
    # Water heater
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN, CONF_WH_TEMP_MIN_ENTITY,
    CONF_WH_POWER_ENTITY, CONF_WH_OFF_PEAK_HYSTERESIS_K,
    # HVAC
    CONF_HVAC_TEMP_ENTITY, CONF_HVAC_SETPOINT_ENTITY,
    CONF_HVAC_MODE, CONF_HVAC_HYSTERESIS_K, CONF_HVAC_MIN_OFF_MINUTES,
    HVAC_MODES,
    # Pool
    CONF_POOL_FILTRATION_ENTITY, CONF_POOL_SPLIT_SESSIONS,
    # Appliance
    CONF_APPLIANCE_READY_ENTITY, CONF_APPLIANCE_PREPARE_SCRIPT,
    CONF_APPLIANCE_START_SCRIPT, CONF_APPLIANCE_POWER_ENTITY,
    CONF_APPLIANCE_POWER_THRESHOLD_W, CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
    CONF_APPLIANCE_DEADLINE_SLOTS,
    # Scoring weights
    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_SOLAR,
    # Strategy
    CONF_SCAN_INTERVAL_MINUTES, CONF_MODE, CONF_DISPATCH_THRESHOLD,
    CONF_GRID_ALLOWANCE_W, CONF_OPTIMIZER_ALPHA,
    CONF_BASE_LOAD_NOISE, CONF_OPTIMIZER_N_RUNS, CONF_RISK_LAMBDA, CONF_EMA_ALPHA, CONF_EMA_ENABLED,
    CONF_SAMPLE_INTERVAL_SECONDS,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END, CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
    # Device / general types and defaults
    DEVICE_TYPES, DEVICE_TYPE_GENERIC, DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPE_HVAC, DEVICE_TYPE_APPLIANCE, DEVICE_TYPE_POOL,
    MODES, MODE_AUTO,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_SOLAR,
    DEFAULT_SCAN_INTERVAL, DEFAULT_DISPATCH_THRESHOLD, DEFAULT_GRID_ALLOWANCE_W, DEFAULT_OPTIMIZER_ALPHA,
    DEFAULT_BASE_LOAD_NOISE, DEFAULT_OPTIMIZER_N_RUNS, DEFAULT_RISK_LAMBDA, DEFAULT_EMA_ALPHA, DEFAULT_EMA_ENABLED,
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_DEVICE_PRIORITY, DEFAULT_DEVICE_MIN_ON_MINUTES,
    DEFAULT_ALLOWED_START, DEFAULT_ALLOWED_END,
    DEFAULT_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_URGENCY,
    DEFAULT_EV_SOC_TARGET, DEFAULT_EV_MIN_CHARGE_POWER_W,
    DEFAULT_WH_TEMP_TARGET, DEFAULT_WH_TEMP_MIN, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K,
    DEFAULT_HVAC_HYSTERESIS_K, DEFAULT_HVAC_MIN_OFF_MINUTES,
    DEFAULT_POOL_SPLIT_SESSIONS,
    DEFAULT_APPLIANCE_POWER_THRESHOLD_W, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES,
    DEFAULT_APPLIANCE_DEADLINE_SLOTS,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config Flow (initial setup — 4 steps)
# ---------------------------------------------------------------------------

class EnergyOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []
        self._current_device: dict[str, Any] = {}

    # ------------------------------------------------------------------ Step 1 — Sources
    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_battery()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
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
                vol.Optional(CONF_TEMPO_NEXT_COLOR_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_FORECAST_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PEAK_PV_W, default=DEFAULT_PEAK_PV_W): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=500, max=30000, step=100, unit_of_measurement="W")
                ),
                vol.Optional(CONF_GRID_SUBSCRIPTION_W, default=DEFAULT_GRID_SUBSCRIPTION_W): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1000, max=100000, step=500, unit_of_measurement="W")
                ),
            }),
        )

    # ------------------------------------------------------------------ Step 2 — Battery
    async def async_step_battery(self, user_input: dict | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()

        return self.async_show_form(
            step_id="battery",
            data_schema=_battery_schema(),
        )

    # ------------------------------------------------------------------ Step 3 — Devices list
    async def async_step_devices(self, user_input: dict | None = None):
        if user_input is not None:
            if user_input.get("add_device"):
                return await self.async_step_device_type()
            self._data[CONF_DEVICES] = self._devices
            return await self.async_step_strategy()

        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema({
                vol.Required("add_device", default=bool(not self._devices)): selector.BooleanSelector(),
            }),
        )

    async def async_step_device_type(self, user_input: dict | None = None):
        if user_input is not None:
            device_type = user_input[CONF_DEVICE_TYPE]
            self._current_device = {CONF_DEVICE_TYPE: device_type}
            return await self._route_device_type(device_type)

        return self.async_show_form(
            step_id="device_type",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_TYPE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DEVICE_TYPES, translation_key="device_type"
                    )
                ),
            }),
        )

    # ------------------------------------------------------------------ Type-specific steps

    async def async_step_device_ev(self, user_input: dict | None = None):
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        return self.async_show_form(
            step_id="device_ev",
            data_schema=vol.Schema({
                vol.Optional(CONF_EV_SOC_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_EV_PLUGGED_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
                ),
                vol.Optional(CONF_EV_SOC_TARGET, default=DEFAULT_EV_SOC_TARGET): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=20, max=100, step=5, unit_of_measurement="%")
                ),
                vol.Optional(CONF_EV_DEPARTURE_TIME): selector.TimeSelector(),
                vol.Optional(CONF_EV_MIN_CHARGE_POWER_W, default=DEFAULT_EV_MIN_CHARGE_POWER_W): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=22000, step=100, unit_of_measurement="W")
                ),
                vol.Optional(CONF_EV_BATTERY_CAPACITY_WH): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5000, max=150000, step=1000, unit_of_measurement="Wh")
                ),
                vol.Optional(CONF_EV_CHARGE_START_SCRIPT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
                vol.Optional(CONF_EV_CHARGE_STOP_SCRIPT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
            }),
        )

    async def async_step_device_water_heater(self, user_input: dict | None = None):
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        return self.async_show_form(
            step_id="device_water_heater",
            data_schema=vol.Schema({
                vol.Optional(CONF_WH_TEMP_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(CONF_WH_TEMP_TARGET, default=DEFAULT_WH_TEMP_TARGET): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=40, max=75, step=1, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_WH_TEMP_MIN, default=DEFAULT_WH_TEMP_MIN): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=65, step=1, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_WH_TEMP_MIN_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(CONF_WH_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_WH_OFF_PEAK_HYSTERESIS_K, default=DEFAULT_WH_OFF_PEAK_HYSTERESIS_K): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=10, step=0.5, unit_of_measurement="°C")
                ),
            }),
        )

    async def async_step_device_hvac(self, user_input: dict | None = None):
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        return self.async_show_form(
            step_id="device_hvac",
            data_schema=vol.Schema({
                vol.Optional(CONF_HVAC_TEMP_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_HVAC_SETPOINT_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["climate", "number", "input_number"])
                ),
                vol.Optional(CONF_HVAC_MODE, default=HVAC_MODES[0]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=HVAC_MODES, translation_key="hvac_mode"
                    )
                ),
                vol.Optional(CONF_HVAC_HYSTERESIS_K, default=DEFAULT_HVAC_HYSTERESIS_K): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.1, max=3.0, step=0.1, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_HVAC_MIN_OFF_MINUTES, default=DEFAULT_HVAC_MIN_OFF_MINUTES): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=30, step=1, unit_of_measurement="min")
                ),
            }),
        )

    async def async_step_device_pool(self, user_input: dict | None = None):
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        return self.async_show_form(
            step_id="device_pool",
            data_schema=vol.Schema({
                vol.Required(CONF_POOL_FILTRATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor", "input_number", "number"]
                    )
                ),
                vol.Optional(CONF_POOL_SPLIT_SESSIONS, default=DEFAULT_POOL_SPLIT_SESSIONS): selector.BooleanSelector(),
            }),
        )

    async def async_step_device_appliance(self, user_input: dict | None = None):
        if user_input is not None:
            self._current_device.update(user_input)
            return await self.async_step_device_common()

        return self.async_show_form(
            step_id="device_appliance",
            data_schema=vol.Schema({
                vol.Optional(CONF_APPLIANCE_READY_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor"])
                ),
                vol.Optional(CONF_APPLIANCE_PREPARE_SCRIPT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
                vol.Optional(CONF_APPLIANCE_START_SCRIPT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
                vol.Optional(CONF_APPLIANCE_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_APPLIANCE_POWER_THRESHOLD_W,
                    default=DEFAULT_APPLIANCE_POWER_THRESHOLD_W,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=200, step=1, unit_of_measurement="W")
                ),
                vol.Optional(
                    CONF_APPLIANCE_CYCLE_DURATION_MINUTES,
                    default=DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=480, step=5, unit_of_measurement="min")
                ),
                vol.Optional(
                    CONF_APPLIANCE_DEADLINE_SLOTS,
                    default=DEFAULT_APPLIANCE_DEADLINE_SLOTS,
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_DEVICE_DEADLINE): selector.TimeSelector(),
            }),
        )

    # ------------------------------------------------------------------ Step 3c — Common fields
    async def async_step_device_common(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            w_sum = (
                user_input.get(CONF_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_PRIORITY)
                + user_input.get(CONF_DEVICE_WEIGHT_FIT,      DEFAULT_DEVICE_WEIGHT_FIT)
                + user_input.get(CONF_DEVICE_WEIGHT_URGENCY,  DEFAULT_DEVICE_WEIGHT_URGENCY)
            )
            if abs(w_sum - 1.0) > 0.05:
                errors["base"] = "device_weights_must_sum_to_one"
            else:
                self._current_device.update(user_input)
                self._devices.append(self._current_device)
                self._current_device = {}
                return await self.async_step_devices()

        device_type = self._current_device.get(CONF_DEVICE_TYPE, "")
        is_appliance = (device_type == DEVICE_TYPE_APPLIANCE)

        switch_field: dict = {} if is_appliance else {
            vol.Optional(CONF_DEVICE_SWITCH_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
        }

        return self.async_show_form(
            step_id="device_common",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_NAME): selector.TextSelector(),
                **switch_field,
                vol.Required(CONF_DEVICE_POWER_W): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50, max=22000, step=50, unit_of_measurement="W")
                ),
                vol.Optional(CONF_DEVICE_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_DEVICE_PRIORITY, default=DEFAULT_DEVICE_PRIORITY): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=1)
                ),
                vol.Optional(CONF_DEVICE_MIN_ON_MINUTES, default=DEFAULT_DEVICE_MIN_ON_MINUTES): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=480, step=5, unit_of_measurement="min")
                ),
                vol.Optional(CONF_DEVICE_ALLOWED_START, default=DEFAULT_ALLOWED_START): selector.TimeSelector(),
                vol.Optional(CONF_DEVICE_ALLOWED_END,   default=DEFAULT_ALLOWED_END):   selector.TimeSelector(),
                vol.Optional(CONF_DEVICE_MUST_RUN_DAILY, default=False): selector.BooleanSelector(),
                # Dispatch weights (sum must equal 1.0)
                vol.Optional(CONF_DEVICE_WEIGHT_PRIORITY, default=DEFAULT_DEVICE_WEIGHT_PRIORITY): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
                ),
                vol.Optional(CONF_DEVICE_WEIGHT_FIT, default=DEFAULT_DEVICE_WEIGHT_FIT): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
                ),
                vol.Optional(CONF_DEVICE_WEIGHT_URGENCY, default=DEFAULT_DEVICE_WEIGHT_URGENCY): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
                ),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------ Step 4 — Strategy
    async def async_step_strategy(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            total = (
                user_input[CONF_WEIGHT_PV_SURPLUS]
                + user_input[CONF_WEIGHT_TEMPO]
                + user_input[CONF_WEIGHT_BATTERY_SOC]
                + user_input[CONF_WEIGHT_SOLAR]
            )
            if abs(total - 1.0) > 0.01:
                errors["base"] = "weights_must_sum_to_one"
            else:
                scan_s = int(user_input[CONF_SCAN_INTERVAL_MINUTES]) * 60
                sample_s = int(user_input[CONF_SAMPLE_INTERVAL_SECONDS])
                if not (10 <= sample_s <= scan_s):
                    errors[CONF_SAMPLE_INTERVAL_SECONDS] = "sample_interval_out_of_range"
            if not errors:
                self._data.update(user_input)
                return self.async_create_entry(title="Helios", data=self._data)

        return self.async_show_form(
            step_id="strategy",
            data_schema=_strategy_schema(),
            errors=errors,
        )

    # ------------------------------------------------------------------ Options flow entry
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyOptimizerOptionsFlow(config_entry)

    # ------------------------------------------------------------------ Helpers
    async def _route_device_type(self, device_type: str):
        if device_type == DEVICE_TYPE_GENERIC:
            return await self.async_step_device_common()
        if device_type == DEVICE_TYPE_EV:
            return await self.async_step_device_ev()
        if device_type == DEVICE_TYPE_WATER_HEATER:
            return await self.async_step_device_water_heater()
        if device_type == DEVICE_TYPE_HVAC:
            return await self.async_step_device_hvac()
        if device_type == DEVICE_TYPE_POOL:
            return await self.async_step_device_pool()
        return await self.async_step_device_appliance()


# ---------------------------------------------------------------------------
# Options Flow
# ---------------------------------------------------------------------------

_OPT_ADD  = "__add__"
_OPT_DONE = "__done__"


def _opt_default(device: dict, key: str) -> dict:
    """Return {'default': value} if the key exists in device, else {} (field left blank)."""
    if key in device and device[key] not in (None, ""):
        return {"default": device[key]}
    return {}


class EnergyOptimizerOptionsFlow(OptionsFlow):
    """Reconfigure without reinstalling — full CRUD on sources, battery, strategy and devices."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        # Load once — edits accumulate in memory until "Terminer"
        self._devices: list[dict[str, Any]] = list(
            config_entry.options.get(
                CONF_DEVICES, config_entry.data.get(CONF_DEVICES, [])
            )
        )
        self._editing_device_idx: int = -1   # -1 = new device
        self._current_device: dict[str, Any] = {}

    def _current(self, key: str, fallback: Any = None) -> Any:
        """Return current value from options (priority) or data."""
        return self._entry.options.get(key, self._entry.data.get(key, fallback))

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            section = user_input.get("section")
            if section == "sources":
                return await self.async_step_sources()
            if section == "battery":
                return await self.async_step_battery()
            if section == "strategy":
                return await self.async_step_strategy()
            if section == "devices":
                return await self.async_step_devices_select()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("section"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["sources", "battery", "strategy", "devices"]
                    )
                ),
            }),
        )

    async def async_step_sources(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(data={**self._entry.options, **user_input})

        return self.async_show_form(
            step_id="sources",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_PV_POWER_ENTITY,
                    default=self._current(CONF_PV_POWER_ENTITY),
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_GRID_POWER_ENTITY,
                    default=self._current(CONF_GRID_POWER_ENTITY, ""),
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_HOUSE_POWER_ENTITY,
                    default=self._current(CONF_HOUSE_POWER_ENTITY, ""),
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_TEMPO_COLOR_ENTITY,
                    default=self._current(CONF_TEMPO_COLOR_ENTITY, ""),
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_TEMPO_NEXT_COLOR_ENTITY,
                    default=self._current(CONF_TEMPO_NEXT_COLOR_ENTITY, ""),
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_FORECAST_ENTITY,
                    default=self._current(CONF_FORECAST_ENTITY, ""),
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_PEAK_PV_W,
                    default=self._current(CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=500, max=30000, step=100, unit_of_measurement="W")
                ),
                vol.Optional(
                    CONF_GRID_SUBSCRIPTION_W,
                    default=self._current(CONF_GRID_SUBSCRIPTION_W, DEFAULT_GRID_SUBSCRIPTION_W),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1000, max=100000, step=500, unit_of_measurement="W")
                ),
            }),
        )

    async def async_step_battery(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(data={**self._entry.options, **user_input})

        return self.async_show_form(
            step_id="battery",
            data_schema=_battery_schema(defaults={
                k: self._current(k)
                for k in (
                    CONF_BATTERY_ENABLED, CONF_BATTERY_SOC_ENTITY,
                    CONF_BATTERY_POWER_ENTITY,
                    CONF_BATTERY_CHARGE_SCRIPT, CONF_BATTERY_AUTOCONSUM_SCRIPT,
                    CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_SOC_MIN,
                    CONF_BATTERY_SOC_MAX, CONF_BATTERY_SOC_RESERVE_ROUGE,
                    CONF_BATTERY_MAX_CHARGE_POWER_W, CONF_BATTERY_MAX_DISCHARGE_POWER_W,
                )
                if self._current(k) is not None
            }),
        )

    async def async_step_strategy(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            total = (
                user_input[CONF_WEIGHT_PV_SURPLUS]
                + user_input[CONF_WEIGHT_TEMPO]
                + user_input[CONF_WEIGHT_BATTERY_SOC]
                + user_input[CONF_WEIGHT_SOLAR]
            )
            if abs(total - 1.0) > 0.01:
                errors["base"] = "weights_must_sum_to_one"
            else:
                scan_s = int(user_input[CONF_SCAN_INTERVAL_MINUTES]) * 60
                sample_s = int(user_input[CONF_SAMPLE_INTERVAL_SECONDS])
                if not (10 <= sample_s <= scan_s):
                    errors[CONF_SAMPLE_INTERVAL_SECONDS] = "sample_interval_out_of_range"
            if not errors:
                return self.async_create_entry(data={**self._entry.options, **user_input})

        return self.async_show_form(
            step_id="strategy",
            data_schema=_strategy_schema(defaults={
                k: self._current(k)
                for k in (
                    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
                    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_SOLAR,
                    CONF_SCAN_INTERVAL_MINUTES, CONF_MODE, CONF_DISPATCH_THRESHOLD,
                    CONF_GRID_ALLOWANCE_W, CONF_OPTIMIZER_ALPHA,
                    CONF_BASE_LOAD_NOISE, CONF_OPTIMIZER_N_RUNS, CONF_RISK_LAMBDA, CONF_EMA_ALPHA, CONF_EMA_ENABLED,
                    CONF_SAMPLE_INTERVAL_SECONDS,
                    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
                    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
                )
                if self._current(k) is not None
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------ Devices CRUD

    async def async_step_devices_select(self, user_input: dict | None = None):
        """CRUD menu: list existing devices, add a new one, or finish."""
        if user_input is not None:
            choice = user_input["choice"]
            if choice == _OPT_DONE:
                return self.async_create_entry(
                    data={**self._entry.options, CONF_DEVICES: self._devices}
                )
            if choice == _OPT_ADD:
                self._editing_device_idx = -1
                self._current_device = {}
                return await self.async_step_opt_device_type()
            else:
                self._editing_device_idx = int(choice)
                self._current_device = dict(self._devices[self._editing_device_idx])
                return await self.async_step_opt_device_action()

        options = [
            {"value": str(i), "label": d.get(CONF_DEVICE_NAME, f"Appareil {i + 1}")}
            for i, d in enumerate(self._devices)
        ] + [
            {"value": _OPT_ADD,  "label": "Ajouter un appareil"},
            {"value": _OPT_DONE, "label": "Terminer"},
        ]

        return self.async_show_form(
            step_id="devices_select",
            data_schema=vol.Schema({
                vol.Required("choice"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                ),
            }),
        )

    async def async_step_opt_device_action(self, user_input: dict | None = None):
        """Choose to edit or delete the selected device."""
        device_name = self._current_device.get(
            CONF_DEVICE_NAME, f"Appareil {self._editing_device_idx + 1}"
        )
        if user_input is not None:
            if user_input["action"] == "delete":
                self._devices.pop(self._editing_device_idx)
            else:
                return await self._route_opt_device_type(
                    self._current_device.get(CONF_DEVICE_TYPE, "")
                )
            return await self.async_step_devices_select()

        return self.async_show_form(
            step_id="opt_device_action",
            data_schema=vol.Schema({
                vol.Required("action", default="edit"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=["edit", "delete"])
                ),
            }),
            description_placeholders={"device_name": device_name},
        )

    async def async_step_opt_device_type(self, user_input: dict | None = None):
        """Select type for a new device (add flow in options)."""
        if user_input is not None:
            self._current_device[CONF_DEVICE_TYPE] = user_input[CONF_DEVICE_TYPE]
            return await self._route_opt_device_type(user_input[CONF_DEVICE_TYPE])

        return self.async_show_form(
            step_id="opt_device_type",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_TYPE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DEVICE_TYPES, translation_key="device_type"
                    )
                ),
            }),
        )

    async def _route_opt_device_type(self, device_type: str):
        if device_type == DEVICE_TYPE_GENERIC:
            return await self.async_step_opt_device_common()
        if device_type == DEVICE_TYPE_EV:
            return await self.async_step_opt_device_ev()
        if device_type == DEVICE_TYPE_WATER_HEATER:
            return await self.async_step_opt_device_water_heater()
        if device_type == DEVICE_TYPE_HVAC:
            return await self.async_step_opt_device_hvac()
        if device_type == DEVICE_TYPE_POOL:
            return await self.async_step_opt_device_pool()
        return await self.async_step_opt_device_appliance()

    async def async_step_opt_device_ev(self, user_input: dict | None = None):
        cd = self._current_device
        if user_input is not None:
            cd.update({k: v for k, v in user_input.items() if v not in (None, "")})
            return await self.async_step_opt_device_common()

        return self.async_show_form(
            step_id="opt_device_ev",
            data_schema=vol.Schema({
                vol.Optional(CONF_EV_SOC_ENTITY, **_opt_default(cd, CONF_EV_SOC_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_EV_PLUGGED_ENTITY, **_opt_default(cd, CONF_EV_PLUGGED_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
                ),
                vol.Optional(CONF_EV_SOC_TARGET, default=cd.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=20, max=100, step=5, unit_of_measurement="%")
                ),
                vol.Optional(CONF_EV_DEPARTURE_TIME, **_opt_default(cd, CONF_EV_DEPARTURE_TIME)): selector.TimeSelector(),
                vol.Optional(CONF_EV_MIN_CHARGE_POWER_W, default=cd.get(CONF_EV_MIN_CHARGE_POWER_W, DEFAULT_EV_MIN_CHARGE_POWER_W)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=22000, step=100, unit_of_measurement="W")
                ),
                vol.Optional(CONF_EV_BATTERY_CAPACITY_WH, **_opt_default(cd, CONF_EV_BATTERY_CAPACITY_WH)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5000, max=150000, step=1000, unit_of_measurement="Wh")
                ),
                vol.Optional(CONF_EV_CHARGE_START_SCRIPT, **_opt_default(cd, CONF_EV_CHARGE_START_SCRIPT)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
                vol.Optional(CONF_EV_CHARGE_STOP_SCRIPT, **_opt_default(cd, CONF_EV_CHARGE_STOP_SCRIPT)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
            }),
        )

    async def async_step_opt_device_water_heater(self, user_input: dict | None = None):
        cd = self._current_device
        if user_input is not None:
            cd.update({k: v for k, v in user_input.items() if v not in (None, "")})
            return await self.async_step_opt_device_common()

        return self.async_show_form(
            step_id="opt_device_water_heater",
            data_schema=vol.Schema({
                vol.Optional(CONF_WH_TEMP_ENTITY, **_opt_default(cd, CONF_WH_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(CONF_WH_TEMP_TARGET, default=cd.get(CONF_WH_TEMP_TARGET, DEFAULT_WH_TEMP_TARGET)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=40, max=75, step=1, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_WH_TEMP_MIN, default=cd.get(CONF_WH_TEMP_MIN, DEFAULT_WH_TEMP_MIN)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=65, step=1, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_WH_TEMP_MIN_ENTITY, **_opt_default(cd, CONF_WH_TEMP_MIN_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(CONF_WH_POWER_ENTITY, **_opt_default(cd, CONF_WH_POWER_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_WH_OFF_PEAK_HYSTERESIS_K, default=cd.get(CONF_WH_OFF_PEAK_HYSTERESIS_K, DEFAULT_WH_OFF_PEAK_HYSTERESIS_K)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=10, step=0.5, unit_of_measurement="°C")
                ),
            }),
        )

    async def async_step_opt_device_hvac(self, user_input: dict | None = None):
        cd = self._current_device
        if user_input is not None:
            cd.update({k: v for k, v in user_input.items() if v not in (None, "")})
            return await self.async_step_opt_device_common()

        return self.async_show_form(
            step_id="opt_device_hvac",
            data_schema=vol.Schema({
                vol.Optional(CONF_HVAC_TEMP_ENTITY, **_opt_default(cd, CONF_HVAC_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_HVAC_SETPOINT_ENTITY, **_opt_default(cd, CONF_HVAC_SETPOINT_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["climate", "number", "input_number"])
                ),
                vol.Optional(CONF_HVAC_MODE, default=cd.get(CONF_HVAC_MODE, HVAC_MODES[0])): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=HVAC_MODES, translation_key="hvac_mode")
                ),
                vol.Optional(CONF_HVAC_HYSTERESIS_K, default=cd.get(CONF_HVAC_HYSTERESIS_K, DEFAULT_HVAC_HYSTERESIS_K)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.1, max=3.0, step=0.1, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_HVAC_MIN_OFF_MINUTES, default=cd.get(CONF_HVAC_MIN_OFF_MINUTES, DEFAULT_HVAC_MIN_OFF_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=30, step=1, unit_of_measurement="min")
                ),
            }),
        )

    async def async_step_opt_device_pool(self, user_input: dict | None = None):
        cd = self._current_device
        if user_input is not None:
            cd.update({k: v for k, v in user_input.items() if v not in (None, "")})
            return await self.async_step_opt_device_common()

        return self.async_show_form(
            step_id="opt_device_pool",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_POOL_FILTRATION_ENTITY,
                    **_opt_default(cd, CONF_POOL_FILTRATION_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])
                ),
                vol.Optional(CONF_POOL_SPLIT_SESSIONS, default=cd.get(CONF_POOL_SPLIT_SESSIONS, DEFAULT_POOL_SPLIT_SESSIONS)): selector.BooleanSelector(),
            }),
        )

    async def async_step_opt_device_appliance(self, user_input: dict | None = None):
        cd = self._current_device
        if user_input is not None:
            cd.update({k: v for k, v in user_input.items() if v not in (None, "")})
            return await self.async_step_opt_device_common()

        return self.async_show_form(
            step_id="opt_device_appliance",
            data_schema=vol.Schema({
                vol.Optional(CONF_APPLIANCE_READY_ENTITY, **_opt_default(cd, CONF_APPLIANCE_READY_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor"])
                ),
                vol.Optional(CONF_APPLIANCE_PREPARE_SCRIPT, **_opt_default(cd, CONF_APPLIANCE_PREPARE_SCRIPT)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
                vol.Optional(CONF_APPLIANCE_START_SCRIPT, **_opt_default(cd, CONF_APPLIANCE_START_SCRIPT)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="script")
                ),
                vol.Optional(CONF_APPLIANCE_POWER_ENTITY, **_opt_default(cd, CONF_APPLIANCE_POWER_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_APPLIANCE_POWER_THRESHOLD_W, default=cd.get(CONF_APPLIANCE_POWER_THRESHOLD_W, DEFAULT_APPLIANCE_POWER_THRESHOLD_W)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=200, step=1, unit_of_measurement="W")
                ),
                vol.Optional(CONF_APPLIANCE_CYCLE_DURATION_MINUTES, default=cd.get(CONF_APPLIANCE_CYCLE_DURATION_MINUTES, DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=480, step=5, unit_of_measurement="min")
                ),
                vol.Optional(CONF_APPLIANCE_DEADLINE_SLOTS, default=cd.get(CONF_APPLIANCE_DEADLINE_SLOTS, DEFAULT_APPLIANCE_DEADLINE_SLOTS)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_DEVICE_DEADLINE, **_opt_default(cd, CONF_DEVICE_DEADLINE)): selector.TimeSelector(),
            }),
        )

    async def async_step_opt_device_common(self, user_input: dict | None = None):
        """Common fields (name, power, schedule…) — last step before saving the device."""
        errors: dict[str, str] = {}
        cd = self._current_device
        if user_input is not None:
            w_sum = (
                user_input.get(CONF_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_PRIORITY)
                + user_input.get(CONF_DEVICE_WEIGHT_FIT,      DEFAULT_DEVICE_WEIGHT_FIT)
                + user_input.get(CONF_DEVICE_WEIGHT_URGENCY,  DEFAULT_DEVICE_WEIGHT_URGENCY)
            )
            if abs(w_sum - 1.0) > 0.05:
                errors["base"] = "device_weights_must_sum_to_one"
            else:
                cd.update(user_input)
                if self._editing_device_idx == -1:
                    self._devices.append(dict(cd))
                else:
                    self._devices[self._editing_device_idx] = dict(cd)
                self._current_device = {}
                return await self.async_step_devices_select()

        is_appliance = (cd.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_APPLIANCE)
        opt_switch_field: dict = {} if is_appliance else {
            vol.Optional(CONF_DEVICE_SWITCH_ENTITY, **_opt_default(cd, CONF_DEVICE_SWITCH_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
        }

        return self.async_show_form(
            step_id="opt_device_common",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_NAME, default=cd.get(CONF_DEVICE_NAME, "")): selector.TextSelector(),
                **opt_switch_field,
                vol.Required(CONF_DEVICE_POWER_W, default=cd.get(CONF_DEVICE_POWER_W, 500)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50, max=22000, step=50, unit_of_measurement="W")
                ),
                vol.Optional(CONF_DEVICE_POWER_ENTITY, **_opt_default(cd, CONF_DEVICE_POWER_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_DEVICE_PRIORITY, default=cd.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=1)
                ),
                vol.Optional(CONF_DEVICE_MIN_ON_MINUTES, default=cd.get(CONF_DEVICE_MIN_ON_MINUTES, DEFAULT_DEVICE_MIN_ON_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=480, step=5, unit_of_measurement="min")
                ),
                vol.Optional(CONF_DEVICE_ALLOWED_START, default=cd.get(CONF_DEVICE_ALLOWED_START, DEFAULT_ALLOWED_START)): selector.TimeSelector(),
                vol.Optional(CONF_DEVICE_ALLOWED_END,   default=cd.get(CONF_DEVICE_ALLOWED_END,   DEFAULT_ALLOWED_END)):   selector.TimeSelector(),
                vol.Optional(CONF_DEVICE_MUST_RUN_DAILY, default=cd.get(CONF_DEVICE_MUST_RUN_DAILY, False)): selector.BooleanSelector(),
                vol.Optional(CONF_DEVICE_WEIGHT_PRIORITY, default=cd.get(CONF_DEVICE_WEIGHT_PRIORITY, DEFAULT_DEVICE_WEIGHT_PRIORITY)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
                ),
                vol.Optional(CONF_DEVICE_WEIGHT_FIT, default=cd.get(CONF_DEVICE_WEIGHT_FIT, DEFAULT_DEVICE_WEIGHT_FIT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
                ),
                vol.Optional(CONF_DEVICE_WEIGHT_URGENCY, default=cd.get(CONF_DEVICE_WEIGHT_URGENCY, DEFAULT_DEVICE_WEIGHT_URGENCY)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
                ),
            }),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Shared schema builders (reused by config + options flows)
# ---------------------------------------------------------------------------

def _battery_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_BATTERY_ENABLED, default=d.get(CONF_BATTERY_ENABLED, False)): selector.BooleanSelector(),
        vol.Optional(CONF_BATTERY_SOC_ENTITY, **_opt_default(d, CONF_BATTERY_SOC_ENTITY)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_BATTERY_POWER_ENTITY, **_opt_default(d, CONF_BATTERY_POWER_ENTITY)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_number"])
        ),
        vol.Optional(CONF_BATTERY_CHARGE_SCRIPT, **_opt_default(d, CONF_BATTERY_CHARGE_SCRIPT)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="script")
        ),
        vol.Optional(CONF_BATTERY_AUTOCONSUM_SCRIPT, **_opt_default(d, CONF_BATTERY_AUTOCONSUM_SCRIPT)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="script")
        ),
        vol.Optional(
            CONF_BATTERY_CAPACITY_KWH,
            default=d.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.5, max=100, step=0.5, unit_of_measurement="kWh")
        ),
        vol.Optional(
            CONF_BATTERY_MAX_CHARGE_POWER_W,
            default=d.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 0),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=15000, step=100, unit_of_measurement="W")
        ),
        vol.Optional(
            CONF_BATTERY_MAX_DISCHARGE_POWER_W,
            default=d.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W, 0),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=15000, step=100, unit_of_measurement="W")
        ),
        vol.Optional(
            CONF_BATTERY_SOC_MIN,
            default=d.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=50, step=1, unit_of_measurement="%")
        ),
        vol.Optional(
            CONF_BATTERY_SOC_MAX,
            default=d.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=50, max=100, step=1, unit_of_measurement="%")
        ),
        vol.Optional(
            CONF_BATTERY_SOC_RESERVE_ROUGE,
            default=d.get(CONF_BATTERY_SOC_RESERVE_ROUGE, DEFAULT_BATTERY_SOC_RESERVE_ROUGE),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
        ),
    })


def _strategy_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema({
        vol.Optional(
            CONF_WEIGHT_PV_SURPLUS,
            default=d.get(CONF_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_PV_SURPLUS),
        ): selector.NumberSelector(selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)),
        vol.Optional(
            CONF_WEIGHT_TEMPO,
            default=d.get(CONF_WEIGHT_TEMPO, DEFAULT_WEIGHT_TEMPO),
        ): selector.NumberSelector(selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)),
        vol.Optional(
            CONF_WEIGHT_BATTERY_SOC,
            default=d.get(CONF_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_BATTERY_SOC),
        ): selector.NumberSelector(selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)),
        vol.Optional(
            CONF_WEIGHT_SOLAR,
            default=d.get(CONF_WEIGHT_SOLAR, DEFAULT_WEIGHT_SOLAR),
        ): selector.NumberSelector(selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)),
        vol.Optional(
            CONF_SCAN_INTERVAL_MINUTES,
            default=d.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min")
        ),
        vol.Optional(
            CONF_SAMPLE_INTERVAL_SECONDS,
            default=d.get(CONF_SAMPLE_INTERVAL_SECONDS, DEFAULT_SAMPLE_INTERVAL_SECONDS),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=3600, step=10, unit_of_measurement="s")
        ),
        vol.Optional(
            CONF_DISPATCH_THRESHOLD,
            default=d.get(CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
        ),
        vol.Optional(
            CONF_GRID_ALLOWANCE_W,
            default=d.get(CONF_GRID_ALLOWANCE_W, DEFAULT_GRID_ALLOWANCE_W),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=2000, step=50, unit_of_measurement="W")
        ),
        vol.Optional(
            CONF_MODE,
            default=d.get(CONF_MODE, MODE_AUTO),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=MODES, translation_key="mode")
        ),
        vol.Optional(
            CONF_OPTIMIZER_ALPHA,
            default=d.get(CONF_OPTIMIZER_ALPHA, DEFAULT_OPTIMIZER_ALPHA),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
        ),
        vol.Optional(
            CONF_BASE_LOAD_NOISE,
            default=d.get(CONF_BASE_LOAD_NOISE, DEFAULT_BASE_LOAD_NOISE),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.05)
        ),
        vol.Optional(
            CONF_OPTIMIZER_N_RUNS,
            default=d.get(CONF_OPTIMIZER_N_RUNS, DEFAULT_OPTIMIZER_N_RUNS),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=30, step=1, mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Optional(
            CONF_RISK_LAMBDA,
            default=d.get(CONF_RISK_LAMBDA, DEFAULT_RISK_LAMBDA),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, max=2.0, step=0.05)
        ),
        vol.Optional(
            CONF_EMA_ALPHA,
            default=d.get(CONF_EMA_ALPHA, DEFAULT_EMA_ALPHA),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.01, max=0.5, step=0.01)
        ),
        vol.Optional(
            CONF_EMA_ENABLED,
            default=d.get(CONF_EMA_ENABLED, DEFAULT_EMA_ENABLED),
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_OFF_PEAK_1_START,
            default=d.get(CONF_OFF_PEAK_1_START, ""),
        ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TIME)),
        vol.Optional(
            CONF_OFF_PEAK_1_END,
            default=d.get(CONF_OFF_PEAK_1_END, ""),
        ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TIME)),
        vol.Optional(
            CONF_OFF_PEAK_2_START,
            default=d.get(CONF_OFF_PEAK_2_START, ""),
        ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TIME)),
        vol.Optional(
            CONF_OFF_PEAK_2_END,
            default=d.get(CONF_OFF_PEAK_2_END, ""),
        ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TIME)),
    })
