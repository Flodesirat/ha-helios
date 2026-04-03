# ha-helios — Context for Claude Code

## Project

Home Assistant custom integration (HACS) maximisant l'autoconsommation solaire.
Domaine : `helios` — chemin : `custom_components/helios/`
Cible : foyers français, EDF Tempo, PV + batterie. Bilingue fr/en.

---

## Architecture

### Entités HA
- `_attr_has_entity_name = True` sur toutes les entités → HA préfixe automatiquement "helios_" au `suggested_object_id`. Ne jamais inclure "helios_" dans `suggested_object_id`.
- Unique IDs : `f"{entry.entry_id}_{suffix}"` — toujours scopés à l'entry.
- `coordinator.py` est le seul module qui écrit sur les variables d'instance du coordinator.

### Config flow — 4 étapes
1. **Sources** — PV, grid, house, Tempo color (entités HA)
2. **Battery** — optionnel ; si `battery_enabled=False`, toute la logique batterie est bypassée
3. **Devices** — boucle : autant d'appareils que nécessaire, chacun typé
4. **Strategy** — poids scoring, scan interval, mode

Options flow : sections `sources | battery | strategy`. Rechargement via `_async_update_listener`.

### Device model
Types : `ev_charger`, `water_heater`, `hvac`, `pool`, `appliance`
Champs communs : `device_name`, `device_type`, `switch_entity`, `power_w`, `priority` (1–10), `min_on_minutes`, `allowed_start/end`

### Scoring engine
```
score = w_surplus × f_surplus + w_tempo × f_tempo + w_soc × f_soc + w_forecast × f_forecast
```
Chaque `f_*` ∈ [0..1] (1.0 = consommer maintenant). Fonctions trapézoïdales fuzzy.
Poids par défaut : surplus=0.4, tempo=0.3, soc=0.2, forecast=0.1.

### Battery strategy
`decide() → "charge" | "discharge" | "reserve" | "idle"`
Priorité : Tempo rouge + SOC bas → charge ; Tempo rouge → reserve ; surplus → charge ; pas de surplus + SOC ok → discharge.

### Device dispatch
Algorithme greedy : filtre éligibles → score effectif = `global_score × (priority/10) × urgency_modifier` → tri décroissant → affecte surplus_w.

### Tempo
Couleur lue depuis une entité sensor standard (blue/white/red). Pas d'appel API EDF direct.

---

## Conventions

- Interactions HA async : `hass.states.get()`, `hass.services.async_call()` uniquement.
- Clés config : constantes de `const.py` — jamais de strings brutes.
- `_LOGGER = logging.getLogger(__name__)` dans chaque module.
- Pas de dépendances externes dans `manifest.json`.

---

## TODO restant

- `config_flow.py` — pré-remplir l'options flow devices avec les valeurs courantes
