"""Constants for Energy Optimizer."""

DOMAIN = "helios"
PLATFORMS = ["sensor", "switch"]

# Config entry keys — sources
CONF_PV_POWER_ENTITY = "pv_power_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_HOUSE_POWER_ENTITY = "house_power_entity"
CONF_TEMPO_COLOR_ENTITY = "tempo_color_entity"

# Config entry keys — battery
CONF_BATTERY_ENABLED = "battery_enabled"
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"
CONF_BATTERY_CHARGE_ENTITY = "battery_charge_entity"
CONF_BATTERY_DISCHARGE_ENTITY = "battery_discharge_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_BATTERY_SOC_MIN = "battery_soc_min"
CONF_BATTERY_SOC_MAX = "battery_soc_max"
CONF_BATTERY_SOC_RESERVE_ROUGE = "battery_soc_reserve_rouge"

# Config entry keys — devices list
CONF_DEVICES = "devices"

# Per-device keys (shared)
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_TYPE = "device_type"
CONF_DEVICE_SWITCH_ENTITY = "device_switch_entity"
CONF_DEVICE_POWER_W = "device_power_w"
CONF_DEVICE_PRIORITY = "device_priority"
CONF_DEVICE_MIN_ON_MINUTES = "device_min_on_minutes"
CONF_DEVICE_ALLOWED_START = "device_allowed_start"
CONF_DEVICE_ALLOWED_END = "device_allowed_end"

# Per-device keys — EV charger
CONF_EV_SOC_ENTITY = "ev_soc_entity"
CONF_EV_SOC_TARGET = "ev_soc_target"
CONF_EV_PLUGGED_ENTITY = "ev_plugged_entity"

# Per-device keys — water heater
CONF_WH_TEMP_ENTITY = "wh_temp_entity"
CONF_WH_TEMP_TARGET = "wh_temp_target"

# Per-device keys — HVAC / heat pump
CONF_HVAC_TEMP_ENTITY = "hvac_temp_entity"
CONF_HVAC_SETPOINT_ENTITY = "hvac_setpoint_entity"

# Per-device keys — appliance (washer, dishwasher…)
CONF_APPLIANCE_PROGRAM_ENTITY = "appliance_program_entity"

# Config entry keys — scoring weights
CONF_WEIGHT_PV_SURPLUS = "weight_pv_surplus"
CONF_WEIGHT_TEMPO = "weight_tempo"
CONF_WEIGHT_BATTERY_SOC = "weight_battery_soc"
CONF_WEIGHT_FORECAST = "weight_forecast"

# Config entry keys — general
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_MODE = "mode"

# Device types
DEVICE_TYPE_EV = "ev_charger"
DEVICE_TYPE_WATER_HEATER = "water_heater"
DEVICE_TYPE_HVAC = "hvac"
DEVICE_TYPE_APPLIANCE = "appliance"
DEVICE_TYPES = [DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC, DEVICE_TYPE_APPLIANCE]

# Tempo colors
TEMPO_BLUE = "blue"
TEMPO_WHITE = "white"
TEMPO_RED = "red"
TEMPO_COLORS = [TEMPO_BLUE, TEMPO_WHITE, TEMPO_RED]

# Operating modes
MODE_AUTO = "auto"
MODE_MANUAL = "manual"
MODE_OFF = "off"
MODES = [MODE_AUTO, MODE_MANUAL, MODE_OFF]

# Defaults
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_BATTERY_SOC_MIN = 10
DEFAULT_BATTERY_SOC_MAX = 95
DEFAULT_BATTERY_SOC_RESERVE_ROUGE = 80
DEFAULT_WEIGHT_PV_SURPLUS = 0.4
DEFAULT_WEIGHT_TEMPO = 0.3
DEFAULT_WEIGHT_BATTERY_SOC = 0.2
DEFAULT_WEIGHT_FORECAST = 0.1
DEFAULT_DEVICE_PRIORITY = 5
DEFAULT_DEVICE_MIN_ON_MINUTES = 30
DEFAULT_ALLOWED_START = "00:00"
DEFAULT_ALLOWED_END = "23:59"
DEFAULT_EV_SOC_TARGET = 80
DEFAULT_WH_TEMP_TARGET = 55
