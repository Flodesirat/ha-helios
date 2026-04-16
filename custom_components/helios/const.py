"""Constants for Helios Energy Optimizer."""

DOMAIN = "helios"
PLATFORMS = ["sensor", "switch", "binary_sensor", "select", "button"]

# ---------------------------------------------------------------------------
# Config entry keys — sources
# ---------------------------------------------------------------------------
CONF_PV_POWER_ENTITY    = "pv_power_entity"
CONF_GRID_POWER_ENTITY  = "grid_power_entity"
CONF_HOUSE_POWER_ENTITY = "house_power_entity"
CONF_TEMPO_COLOR_ENTITY      = "tempo_color_entity"
CONF_TEMPO_NEXT_COLOR_ENTITY = "tempo_next_color_entity"  # "couleur du lendemain" pour l'optimiseur 5h
CONF_FORECAST_ENTITY         = "forecast_entity"
CONF_PEAK_PV_W               = "peak_pv_w"               # puissance crête PV (W) — utilisée par l'optimiseur journalier
CONF_GRID_SUBSCRIPTION_W     = "grid_subscription_w"     # puissance souscrite au réseau (W)

# ---------------------------------------------------------------------------
# Config entry keys — battery
# ---------------------------------------------------------------------------
CONF_BATTERY_ENABLED              = "battery_enabled"
CONF_BATTERY_PRIORITY             = "battery_priority"
CONF_BATTERY_SOC_ENTITY           = "battery_soc_entity"
CONF_BATTERY_CHARGE_SCRIPT        = "battery_charge_script"
CONF_BATTERY_AUTOCONSUM_SCRIPT    = "battery_autoconsum_script"
CONF_BATTERY_CAPACITY_KWH         = "battery_capacity_kwh"
CONF_BATTERY_SOC_MIN              = "battery_soc_min"
CONF_BATTERY_SOC_MAX              = "battery_soc_max"
CONF_BATTERY_SOC_RESERVE_ROUGE    = "battery_soc_reserve_rouge"
CONF_BATTERY_MAX_CHARGE_POWER_W   = "battery_max_charge_power_w"
CONF_BATTERY_MAX_DISCHARGE_POWER_W = "battery_max_discharge_power_w"
CONF_BATTERY_POWER_ENTITY         = "battery_power_entity"

# Battery actions
BATTERY_ACTION_FORCED_CHARGE   = "forced_charge"
BATTERY_ACTION_AUTOCONSOMMATION = "autoconsommation"
BATTERY_ACTIONS = [BATTERY_ACTION_FORCED_CHARGE, BATTERY_ACTION_AUTOCONSOMMATION]

# ---------------------------------------------------------------------------
# Config entry keys — device list
# ---------------------------------------------------------------------------
CONF_DEVICES = "devices"

# ---------------------------------------------------------------------------
# Per-device keys — shared (all types)
# ---------------------------------------------------------------------------
CONF_DEVICE_NAME           = "device_name"
CONF_DEVICE_TYPE           = "device_type"
CONF_DEVICE_SWITCH_ENTITY  = "device_switch_entity"   # optional for start_only
CONF_DEVICE_POWER_W        = "device_power_w"          # peak/nominal power (W)
CONF_DEVICE_POWER_ENTITY   = "device_power_entity"     # measured power sensor (W) — optional
CONF_DEVICE_PRIORITY       = "device_priority"         # 1–10
CONF_DEVICE_MIN_ON_MINUTES = "device_min_on_minutes"
CONF_DEVICE_ALLOWED_START  = "device_allowed_start"
CONF_DEVICE_ALLOWED_END    = "device_allowed_end"
CONF_DEVICE_INTERRUPTIBLE  = "device_interruptible"    # derived from type, stored explicitly
CONF_DEVICE_DEADLINE       = "device_deadline"         # "HH:MM" finish-by time

# Per-device dispatch weights (must sum to 1.0 — validated in config flow)
CONF_DEVICE_WEIGHT_PRIORITY = "device_weight_priority"
CONF_DEVICE_WEIGHT_FIT      = "device_weight_fit"
CONF_DEVICE_WEIGHT_URGENCY  = "device_weight_urgency"

# ---------------------------------------------------------------------------
# Per-device keys — EV charger
# ---------------------------------------------------------------------------
CONF_EV_SOC_ENTITY          = "ev_soc_entity"
CONF_EV_SOC_TARGET          = "ev_soc_target"
CONF_EV_PLUGGED_ENTITY      = "ev_plugged_entity"
CONF_EV_DEPARTURE_TIME      = "ev_departure_time"         # "HH:MM"
CONF_EV_MIN_CHARGE_POWER_W  = "ev_min_charge_power_w"    # EVSE minimum (usually 6 A)
CONF_EV_BATTERY_CAPACITY_WH = "ev_battery_capacity_wh"   # optional, for time estimation
CONF_EV_CHARGE_START_SCRIPT = "ev_charge_start_script"
CONF_EV_CHARGE_STOP_SCRIPT  = "ev_charge_stop_script"

# ---------------------------------------------------------------------------
# Per-device keys — water heater
# ---------------------------------------------------------------------------
CONF_WH_TEMP_ENTITY           = "wh_temp_entity"
CONF_WH_TEMP_TARGET           = "wh_temp_target"
CONF_WH_TEMP_MIN              = "wh_temp_min"              # legionella floor → safety must_run override
CONF_WH_TEMP_MIN_ENTITY       = "wh_temp_min_entity"       # entity for off-peak minimum temperature
CONF_WH_OFF_PEAK_HYSTERESIS_K = "wh_off_peak_hysteresis_k" # °C below off-peak min before forcing ON

# ---------------------------------------------------------------------------
# Per-device keys — HVAC / heat pump
# ---------------------------------------------------------------------------
CONF_HVAC_TEMP_ENTITY      = "hvac_temp_entity"
CONF_HVAC_SETPOINT_ENTITY  = "hvac_setpoint_entity"
CONF_HVAC_MODE             = "hvac_mode"             # "heat" | "cool"
CONF_HVAC_HYSTERESIS_K     = "hvac_hysteresis_k"    # dead-band in °C
CONF_HVAC_MIN_OFF_MINUTES  = "hvac_min_off_minutes" # compressor protection

# HVAC modes
HVAC_MODE_HEAT = "heat"
HVAC_MODE_COOL = "cool"
HVAC_MODES = [HVAC_MODE_HEAT, HVAC_MODE_COOL]

# ---------------------------------------------------------------------------
# Per-device keys — pool
# ---------------------------------------------------------------------------
CONF_POOL_FILTRATION_ENTITY = "pool_filtration_entity"  # sensor → required hours today
CONF_POOL_SPLIT_SESSIONS    = "pool_split_sessions"     # allow multiple sessions per day

# ---------------------------------------------------------------------------
# Per-device keys — appliance (washer, dishwasher…)
# ---------------------------------------------------------------------------
CONF_APPLIANCE_READY_ENTITY      = "appliance_ready_entity"    # input_boolean
CONF_APPLIANCE_PREPARE_SCRIPT    = "appliance_prepare_script"  # optional pre-start script
CONF_APPLIANCE_START_SCRIPT      = "appliance_start_script"    # triggers the cycle
CONF_APPLIANCE_POWER_ENTITY      = "appliance_power_entity"    # optional — cycle detection
CONF_APPLIANCE_POWER_THRESHOLD_W = "appliance_power_threshold_w"
CONF_APPLIANCE_CYCLE_DURATION_MINUTES = "appliance_cycle_duration_minutes"
CONF_APPLIANCE_DEADLINE_SLOTS    = "appliance_deadline_slots"  # e.g. "12:00,18:00"

# Appliance internal states (not persisted in config)
APPLIANCE_STATE_IDLE      = "idle"
APPLIANCE_STATE_READY     = "ready"
APPLIANCE_STATE_PREPARING = "preparing"
APPLIANCE_STATE_RUNNING   = "running"
APPLIANCE_STATE_DONE      = "done"

# ---------------------------------------------------------------------------
# Config entry keys — general / strategy
# ---------------------------------------------------------------------------
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_GRID_ALLOWANCE_W      = "grid_allowance_w"    # W autorisés depuis le réseau en mode Pleine (SOC ≥ 96 %)
# Off-peak time slots (global, used by water heater HC logic) — HH:MM strings
CONF_OFF_PEAK_1_START = "off_peak_1_start"
CONF_OFF_PEAK_1_END   = "off_peak_1_end"
CONF_OFF_PEAK_2_START = "off_peak_2_start"
CONF_OFF_PEAK_2_END   = "off_peak_2_end"
# Electricity prices for savings computation (€/kWh) — Tempo color × HC/HP
CONF_PRICE_BLUE_HC  = "price_blue_hc"
CONF_PRICE_BLUE_HP  = "price_blue_hp"
CONF_PRICE_WHITE_HC = "price_white_hc"
CONF_PRICE_WHITE_HP = "price_white_hp"
CONF_PRICE_RED_HC   = "price_red_hc"
CONF_PRICE_RED_HP   = "price_red_hp"

CONF_BASE_LOAD_NOISE       = "base_load_noise"     # std-dev du bruit multiplicatif journalier sur la charge
CONF_RISK_LAMBDA           = "risk_lambda"         # coefficient de pénalité sur l'écart-type de l'objectif
CONF_EMA_ALPHA             = "ema_alpha"           # facteur d'apprentissage EMA de la charge de fond
CONF_EMA_ENABLED           = "ema_enabled"         # activer/désactiver l'apprentissage EMA
CONF_SAMPLE_INTERVAL_SECONDS = "sample_interval_seconds"  # intervalle d'échantillonnage des capteurs (s)

# ---------------------------------------------------------------------------
# Device types
# ---------------------------------------------------------------------------
DEVICE_TYPE_GENERIC      = "generic"
DEVICE_TYPE_EV           = "ev_charger"
DEVICE_TYPE_WATER_HEATER = "water_heater"
DEVICE_TYPE_HVAC         = "hvac"
DEVICE_TYPE_APPLIANCE    = "appliance"
DEVICE_TYPE_POOL         = "pool"
DEVICE_TYPES = [
    DEVICE_TYPE_GENERIC,
    DEVICE_TYPE_EV,
    DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPE_HVAC,
    DEVICE_TYPE_APPLIANCE,
    DEVICE_TYPE_POOL,
]

# ---------------------------------------------------------------------------
# Tempo colors
# ---------------------------------------------------------------------------
TEMPO_BLUE   = "blue"
TEMPO_WHITE  = "white"
TEMPO_RED    = "red"
TEMPO_COLORS = [TEMPO_BLUE, TEMPO_WHITE, TEMPO_RED]

# Normalize raw HA state values (FR/EN, any case) to canonical English
_TEMPO_NORMALIZE: dict[str, str] = {
    "blue":  TEMPO_BLUE,  "bleu":  TEMPO_BLUE,
    "white": TEMPO_WHITE, "blanc": TEMPO_WHITE,
    "red":   TEMPO_RED,   "rouge": TEMPO_RED,
}


def normalize_tempo_color(raw: str | None) -> str | None:
    """Return canonical tempo color ('blue'/'white'/'red') or None if unrecognised."""
    if raw is None:
        return None
    return _TEMPO_NORMALIZE.get(raw.lower())

# ---------------------------------------------------------------------------
# Operating modes
# ---------------------------------------------------------------------------
CONF_ENABLED         = "enabled"
DEFAULT_ENABLED      = True

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
STORAGE_KEY           = f"{DOMAIN}_pool_run"    # device state (pool counters, manual_mode…)
STORAGE_KEY_OPTIMIZER = f"{DOMAIN}_optimizer"   # daily optimizer results (weights, schedule…)
STORAGE_KEY_ENERGY    = f"{DOMAIN}_energy"      # daily energy accumulators (kWh, reset at midnight)
STORAGE_VERSION = 1

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_SCAN_INTERVAL              = 5      # minutes
DEFAULT_GRID_ALLOWANCE_W           = 250    # W
DEFAULT_BASE_LOAD_NOISE            = 0.20   # ±20 % de bruit journalier sur la charge domestique
DEFAULT_RISK_LAMBDA                = 0.5    # obj = mean − 0.5 × std  (risque modéré)
DEFAULT_EMA_ALPHA                  = 0.05   # convergence lente (~1 semaine) pour stabilité
DEFAULT_EMA_ENABLED                = True   # apprentissage activé par défaut
DEFAULT_SAMPLE_INTERVAL_SECONDS    = 30     # s — fréquence d'échantillonnage des capteurs
DEFAULT_PEAK_PV_W                  = 3000.0 # W — used when no real-time data available
DEFAULT_GRID_SUBSCRIPTION_W        = 9000   # W

DEFAULT_BATTERY_PRIORITY           = 7
DEFAULT_BATTERY_SOC_MIN            = 10     # %
DEFAULT_BATTERY_SOC_MAX            = 95     # %
DEFAULT_BATTERY_SOC_RESERVE_ROUGE  = 80     # %
DEFAULT_BATTERY_CAPACITY_KWH       = 5.0

DEFAULT_DEVICE_PRIORITY            = 5
DEFAULT_DEVICE_MIN_ON_MINUTES      = 30
DEFAULT_ALLOWED_START              = "00:00"
DEFAULT_ALLOWED_END                = "23:59"

DEFAULT_DEVICE_WEIGHT_PRIORITY     = 0.3
DEFAULT_DEVICE_WEIGHT_FIT          = 0.4
DEFAULT_DEVICE_WEIGHT_URGENCY      = 0.3

DEFAULT_EV_SOC_TARGET              = 80     # %
DEFAULT_EV_MIN_CHARGE_POWER_W      = 1380.0 # 6 A × 230 V

DEFAULT_WH_TEMP_TARGET             = 60.0   # °C
DEFAULT_WH_TEMP_MIN                = 55.0   # °C (legionella threshold)
DEFAULT_WH_OFF_PEAK_HYSTERESIS_K   = 3.0    # °C — must_run triggers only below (off_peak_min − 3°C)

DEFAULT_HVAC_HYSTERESIS_K          = 0.5    # °C
DEFAULT_HVAC_MIN_OFF_MINUTES       = 5

DEFAULT_POOL_SPLIT_SESSIONS        = True

DEFAULT_APPLIANCE_POWER_THRESHOLD_W     = 10.0   # W — "cycle ended" detection
DEFAULT_APPLIANCE_CYCLE_DURATION_MINUTES = 120   # min — fallback if no power sensor
DEFAULT_APPLIANCE_DEADLINE_SLOTS        = "12:00,18:00"

# EDF Tempo tariff defaults TTC (03/03/2026) — HC = 22h–6h, HP = 6h–22h
DEFAULT_PRICE_BLUE_HC  = 0.1325
DEFAULT_PRICE_BLUE_HP  = 0.1612
DEFAULT_PRICE_WHITE_HC = 0.1499
DEFAULT_PRICE_WHITE_HP = 0.1871
DEFAULT_PRICE_RED_HC   = 0.1575
DEFAULT_PRICE_RED_HP   = 0.7060
