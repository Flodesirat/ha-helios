# Energy Optimizer — Context for Claude Code

## What this project is

A Home Assistant custom integration (HACS-compatible) that maximizes solar
self-consumption by intelligently controlling home devices and a battery.
It is designed to be **fully installation-agnostic**: all input/output entities
are configured by the user through a UI config flow, so the integration works
with any inverter, meter, or smart plug brand.

Target users: French households with rooftop PV, a stationary battery, and an
EDF Tempo contract. The integration is internationalised (fr + en) and must
remain shareable on HACS without code changes between installations.

---

## Repository layout

```
custom_components/energy_optimizer/
├── __init__.py          # Entry point: async_setup_entry / async_unload_entry
├── manifest.json        # HACS metadata
├── const.py             # All constants and config-entry key names
├── config_flow.py       # 4-step config flow + options flow
├── coordinator.py       # DataUpdateCoordinator — main loop
├── scoring_engine.py    # Weighted scoring with fuzzy normalisation
├── battery_strategy.py  # Battery charge/discharge/reserve decision
├── device_manager.py    # Device registry + greedy dispatch
├── sensor.py            # HA sensor entities (surplus, score, battery action)
├── switch.py            # HA switch entity (auto mode on/off)
├── strings.json         # UI labels (source of truth)
└── translations/
    ├── en.json
    └── fr.json
```

---

## Architecture decisions (do not revisit without good reason)

### Config flow — 4 steps
1. **Sources** — PV power entity, grid power entity, house consumption entity,
   Tempo color entity (all optional except PV)
2. **Battery** — optional; if `battery_enabled=False`, all battery logic is
   bypassed silently throughout the codebase
3. **Devices** — loop: add as many devices as needed, each typed
4. **Strategy** — scoring weights (must sum to 1.0), scan interval, mode

### Options flow
Sections: `sources` | `battery` | `strategy`. Reloads the integration on save
via `_async_update_listener` in `__init__.py`.

### Device model
Each device has a **type** with type-specific fields on top of shared fields:

| Type | Extra fields |
|------|-------------|
| `ev_charger` | `ev_soc_entity`, `ev_plugged_entity`, `ev_soc_target` |
| `water_heater` | `wh_temp_entity`, `wh_temp_target` |
| `hvac` | `hvac_temp_entity`, `hvac_setpoint_entity` |
| `appliance` | `appliance_program_entity` |

Shared fields for all types: `device_name`, `device_type`, `device_switch_entity`,
`device_power_w`, `device_priority` (1–10), `device_min_on_minutes`,
`device_allowed_start`, `device_allowed_end`.

### Scoring engine
`ScoringEngine.compute(data) → float [0..1]`

```
score = w_surplus  × f_surplus(surplus_w)
      + w_tempo    × f_tempo(tempo_color)
      + w_soc      × f_soc(battery_soc)
      + w_forecast × f_forecast(data)
```

Each `f_*` returns [0..1] where **1.0 = use energy now**, **0.0 = conserve**.
Use **fuzzy trapezoid membership functions**, not hard thresholds.

Default weights: surplus=0.4, tempo=0.3, soc=0.2, forecast=0.1.

### Battery strategy
`BatteryStrategy.decide(data) → "charge" | "discharge" | "reserve" | "idle"`

Priority order:
1. Red Tempo + SOC < `soc_reserve` → `"charge"` (fill up during HC before HP)
2. Red Tempo → `"reserve"` (protect SOC, do not discharge during HP)
3. PV surplus + SOC < `soc_max` → `"charge"`
4. No surplus + SOC > `soc_min` → `"discharge"`
5. Otherwise → `"idle"`

### Device dispatch (DeviceManager)
`async_dispatch(hass, score_input)` — greedy algorithm:
1. Filter eligible devices (in allowed window, not satisfied, not blocked by
   `min_on_minutes`)
2. Compute effective score per device:
   `effective = global_score × (priority / 10) × device_score_modifier(hass)`
3. Sort descending
4. Assign `surplus_w` greedily: if device fits in remaining surplus → turn ON
5. Devices not selected and currently ON → turn OFF (respect `min_on_minutes`)

### Tempo integration
Tempo color is read from a plain sensor entity (string: "blue"/"white"/"red").
- **Blue** → cheap, `f_tempo = 1.0`
- **White** → normal, `f_tempo = 0.5`
- **Red** → expensive 6h–22h, `f_tempo = 0.0` ; battery enters `"reserve"` mode

The integration does **not** hardcode EDF API calls. The user maps their
existing Tempo sensor entity in the config flow.

---

## Coding conventions

- All async HA interactions via `hass.states.get()`, `hass.services.async_call()`.
  Never use synchronous state access inside coroutines.
- Entity unique IDs: `f"{entry.entry_id}_{suffix}"` — always scoped to entry.
- `_LOGGER = logging.getLogger(__name__)` in every module.
- All config keys are constants from `const.py` — never use raw strings.
- `coordinator.py` is the only module allowed to write to `coordinator.*`
  instance variables. Sensor/switch entities read them via `self.coordinator.*`.
- Do not add external `requirements` to `manifest.json` unless strictly
  necessary — keep the integration dependency-free.

---

## Current TODO list (priority order)

### 1. `scoring_engine.py` — implement fuzzy membership functions

```python
def _score_surplus(self, surplus_w: float) -> float:
    # Trapezoid: 0W→0.0, ramp up 0–500W, plateau 500–3000W→1.0
    # Use self.capacity_kwh to scale thresholds if useful

def _score_soc(self, soc: float | None) -> float:
    # Bell-like: very low SOC→0.0 (conserve), very high→0.5 (no urgency),
    # mid SOC (40–60%)→1.0 (ideal to use devices)
    # Return 0.5 if soc is None

def _score_forecast(self, data: dict) -> float:
    # If forecast entity is available: good forecast→defer loads→lower score
    # No forecast entity → return neutral 0.5
```

### 2. `battery_strategy.py` — implement decision tree + async_apply

```python
def decide(self, data: dict) -> str:
    # Implement priority order described above

async def async_apply(self, hass: HomeAssistant, action: str) -> None:
    # "charge"    → set charge_entity to max charge power
    # "discharge" → set discharge_entity to max discharge power
    # "reserve"   → set discharge_entity to 0 (block discharge)
    # "idle"      → no action
    # Use hass.services.async_call("number", "set_value", {...})
```

### 3. `device_manager.py` — implement dispatch + satisfaction checks

```python
async def async_dispatch(self, hass, score_input):
    # Greedy algorithm described above

def is_satisfied(self, hass) -> bool:
    # EV: ev_soc_entity >= ev_soc_target OR not plugged in
    # Water heater: wh_temp_entity >= wh_temp_target
    # HVAC: always False (thermostat handles satisfaction internally)
    # Appliance: always False

def is_in_allowed_window(self, now: time) -> bool:
    # Parse self.allowed_start / self.allowed_end as "HH:MM"
    # Return True if now is within [start, end]
```

### 4. `config_flow.py` — pre-fill options flow steps with current values

In `EnergyOptimizerOptionsFlow.async_step_sources/battery/strategy`,
pre-populate `data_schema` defaults from `self._entry.options` or
`self._entry.data` (options override data).

### 5. `coordinator.py` — wire forecast entity (future)

Add `CONF_FORECAST_ENTITY` to sources step and pass its value into
`_build_score_input()` once the scoring engine uses it.

---

## HA integration patterns reference

```python
# Read a sensor state safely
state = hass.states.get("sensor.my_entity")
value = float(state.state) if state and state.state not in ("unavailable", "unknown") else 0.0

# Call a service
await hass.services.async_call(
    "number", "set_value",
    {"entity_id": "number.battery_charge", "value": 2000},
    blocking=False,
)

# Turn a switch on/off
await hass.services.async_call(
    "homeassistant", "turn_on",
    {"entity_id": "switch.my_device"},
    blocking=False,
)
```

---

## Testing hints

- Use `pytest-homeassistant-custom-component` for unit tests.
- Mock `hass.states.get()` to inject sensor values without a real HA instance.
- `ScoringEngine` and `BatteryStrategy` are pure-ish classes — unit-test them
  directly with dict inputs, no HA mocking needed.
- Config flow can be tested with `hass.config_entries.flow.async_init()`.
