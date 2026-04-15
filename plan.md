# Plan d'implémentation — Refonte dispatch Helios

## Avancement

| Lot | Statut | Détail |
|-----|--------|--------|
| 1   | ✅ Terminé (2026-04-14) | `const.py` nettoyé, `CONF_BATTERY_PRIORITY` / `DEFAULT_BATTERY_PRIORITY` ajoutés. 355 tests passent. |
| 2   | ✅ Terminé (2026-04-14) | `scoring_engine.py` refactorisé : poids fixes, `compute_components()`, `_score_surplus()` simplifiée. 357 tests passent, 3 skipped. |
| 3   | ✅ Terminé (2026-04-14) | `managed_device.py` / `device_manager.py` refactorisés. `BatteryDevice` créé. 350 tests passent. |
| 4   | ✅ Terminé (2026-04-14) | `coordinator.py` + `device_manager.py` : algorithme dispatch unifié 4 phases, `compute_fit()`, suppression `dispatch_threshold` et `async_save_optimizer_state()`. 350 tests passent, 3 skipped. |
| 5   | ✅ Terminé (2026-04-14) | `config_flow.py` : `battery_priority` ajouté dans `_battery_schema()`, champs `weight_*`/`dispatch_threshold`/`optimizer_alpha` supprimés des traductions. 350 tests passent. |
| 6   | ✅ Terminé (2026-04-14) | `daily_optimizer.py` : `ForecastResult`, `async_run_daily_forecast`, suppression grid search. 351 tests passent. |
| 7   | ✅ Terminé (2026-04-15) | `sensor.py` : `ForecastSensor`, attributs `f_surplus/f_tempo/f_solar`, `urgency/effective_score` batterie, `reason` device. `switch.py` : `BatteryManualSwitch`. 351 tests passent. |
| 8   | ✅ Terminé (2026-04-15) | `simulation/` : `SimBatteryDevice`, suppression `dispatch_threshold` et grid search, `--bat-priority`/`--bat-soc-min-red`, colonnes `Remaining`. 350 tests passent. |
| 9   | ⬜ À faire | Tests |
| 10  | ⬜ À faire | `www/helios-card.js` |

---

## Source de vérité

Le **README.md** est la spécification complète. Chaque agent doit le lire intégralement avant de commencer.
Sections clés par lot :

| Lot | Sections README à lire en priorité |
|-----|-------------------------------------|
| 1 | — (const pure) |
| 2 | "Score global", "Composante surplus (`f_surplus`)" |
| 3 | "Types d'appareils", "Calcul du score d'urgence", "Priorité et ordre de dispatch", "Entités batterie" |
| 4 | "Grandeurs clés du dispatch", "Calcul du score de fit", "Algorithme de dispatch — allumage et extinction unifiés" |
| 5 | "Configuration — étape Batterie", "Configuration — étape Stratégie" |
| 6 | "Prévision journalière", "Entité prévision journalière" |
| 7 | "Entités exposées" (toute la section) |
| 8 | "Simulation" (toute la section) |
| 9 | Tout |
| 10 | "Entités exposées", "Carte Lovelace" |

## Dépendances entre lots

```
Lot 1 (const.py)
    ├── Lot 2 (scoring_engine.py)
    ├── Lot 3 (managed_device.py / device_manager.py)
    └── Lot 5 (config_flow.py)
            Lot 2 ──┐
            Lot 3 ──┤
                    └── Lot 4 (coordinator.py)
                                ├── Lot 6 (daily_optimizer.py)
                                └── Lot 7 (sensor.py / switch.py)
Lot 8 (simulation/)  ← parallélisable avec Lot 3 et Lot 4
Lot 9 (tests)        ← dépend de tous les lots précédents
Lot 10 (helios-card) ← dépend de Lot 2, Lot 4, Lot 7 (entités et attributs finaux)
```

## Commande de test de référence

```bash
pytest tests/ -x -q
```

Tous les lots doivent laisser cette commande verte (307 tests à ce jour + nouveaux tests du lot concerné).

---

## Lot 1 — `const.py` : nettoyage et nouvelles constantes

### Fichiers à lire
- `custom_components/helios/const.py` — fichier à modifier

### État actuel
Les constantes suivantes existent et doivent être supprimées :
- Ligne 56 : `CONF_DEVICE_MUST_RUN_DAILY = "device_must_run_daily"`
- Lignes 126–129 : `CONF_WEIGHT_PV_SURPLUS`, `CONF_WEIGHT_TEMPO`, `CONF_WEIGHT_BATTERY_SOC`, `CONF_WEIGHT_SOLAR`
- Ligne 135 : `CONF_DISPATCH_THRESHOLD`
- Ligne 137 : `CONF_OPTIMIZER_ALPHA`
- Lignes 208–228 : `DEFAULT_DISPATCH_THRESHOLD`, `DEFAULT_OPTIMIZER_ALPHA`, `DEFAULT_OPTIMIZER_N_RUNS`, `DEFAULT_WEIGHT_PV_SURPLUS`, `DEFAULT_WEIGHT_TEMPO`, `DEFAULT_WEIGHT_BATTERY_SOC`, `DEFAULT_WEIGHT_SOLAR`

Note : `CONF_DEVICE_MUST_RUN` (sans `_DAILY`) n'existe pas dans ce fichier — ne pas chercher à le supprimer.

### Tâches
- [ ] Supprimer `CONF_DISPATCH_THRESHOLD`, `DEFAULT_DISPATCH_THRESHOLD`
- [ ] Supprimer `CONF_OPTIMIZER_ALPHA`, `DEFAULT_OPTIMIZER_ALPHA`, `DEFAULT_OPTIMIZER_N_RUNS`
- [ ] Supprimer `CONF_WEIGHT_PV_SURPLUS`, `DEFAULT_WEIGHT_PV_SURPLUS`
- [ ] Supprimer `CONF_WEIGHT_TEMPO`, `DEFAULT_WEIGHT_TEMPO`
- [ ] Supprimer `CONF_WEIGHT_BATTERY_SOC`, `DEFAULT_WEIGHT_BATTERY_SOC`
- [ ] Supprimer `CONF_WEIGHT_SOLAR`, `DEFAULT_WEIGHT_SOLAR`
- [ ] Supprimer `CONF_DEVICE_MUST_RUN_DAILY`
- [ ] Ajouter `CONF_BATTERY_PRIORITY = "battery_priority"`
- [ ] Ajouter `DEFAULT_BATTERY_PRIORITY = 7`

### Définition de terminé
`pytest tests/ -x -q` passe. Les constantes supprimées ne doivent plus être référencées nulle part (vérifier avec `grep -r "CONF_DISPATCH_THRESHOLD\|CONF_WEIGHT_\|CONF_OPTIMIZER_ALPHA\|CONF_DEVICE_MUST_RUN_DAILY" custom_components/`).

---

## Lot 2 — `scoring_engine.py` : score global indicateur (poids fixes)

### Fichiers à lire
- `custom_components/helios/scoring_engine.py` — fichier à modifier
- `tests/test_weights.py` — tests existants à mettre à jour

### État actuel
- `compute()` calcule `w_surplus×f_surplus + w_tempo×f_tempo + w_soc×f_soc + w_solar×f_solar`
- `w_surplus`, `w_tempo`, `w_soc`, `w_solar` sont des attributs d'instance configurables
- `update_weights()` et `get_weights()` existent
- `_score_soc()` utilise déjà la courbe pivot (flat ramp 0→0.3, steep ramp 0.3→1.0) — **déjà correct, ne pas modifier**
- `_score_surplus()` utilise une double pente avec `charge_max_w` comme seuil — **doit être simplifié**

### Objectif
Le score global est un indicateur utilisateur uniquement. Formule fixe :
```
score = 0.5 × f_surplus + 0.3 × f_tempo + 0.2 × f_solar
```

### Tâches
- [ ] Remplacer les attributs `w_surplus`, `w_tempo`, `w_soc`, `w_solar` par des constantes de module :
  ```python
  _W_SURPLUS = 0.5
  _W_TEMPO   = 0.3
  _W_SOLAR   = 0.2
  ```
- [ ] Supprimer `f_soc` de `compute()` — formule : `_W_SURPLUS×f_surplus + _W_TEMPO×f_tempo + _W_SOLAR×f_solar`
- [ ] Supprimer `update_weights()` et `get_weights()`
- [ ] Simplifier `_score_surplus()` : **rampe unique** de `0 W` à `(charge_max_w + 500) W` → `0.0` à `1.0`
  - Sans batterie : `charge_max_w = 0` → rampe `0 → 500 W`
  - Avec batterie : rampe `0 → (charge_max_w + 500) W`
  - Formule : `min(1.0, surplus_w / max(1.0, charge_max_w + 500.0))`
- [ ] Conserver `_score_soc()` telle quelle (méthode privée, non appelée par `compute()`, testée dans `TestSocScoring`)
- [ ] Ajouter une méthode `compute_components()` qui retourne le tuple `(f_surplus, f_tempo, f_solar)` — utilisé par le sensor pour exposer les attributs détaillés

### Interface exposée (consommée par Lot 4 et Lot 7)
```python
engine = ScoringEngine(config: dict)
score: float = engine.compute(data: dict)
# data keys: "surplus_w", "tempo_color", "solar_elevation" (ou "hour" en fallback)

f_surplus, f_tempo, f_solar = engine.compute_components(data: dict)
# Retourne le tuple des composantes [0..1] dans cet ordre
```

### Définition de terminé
`pytest tests/test_weights.py -x -q` passe. Mettre à jour `test_weights.py` en même temps (voir Lot 9 — section `test_weights.py`).

---

## Lot 3 — `managed_device.py` / `device_manager.py` : BatteryDevice et refonte urgence

### Fichiers à lire
- `custom_components/helios/managed_device.py` — classe `ManagedDevice` (ligne 105), méthodes `must_run_now()` (ligne 348), `urgency_modifier()` (ligne 399), `effective_score()` (ligne 502)
- `custom_components/helios/device_manager.py` — classe `DeviceManager` (ligne 33), logique `must_run` (lignes 323–347), dispatch greedy (lignes 455–604)
- `custom_components/helios/const.py` — après Lot 1

### État actuel
- `ManagedDevice` implémente `must_run_now()` et `urgency_modifier()` — pas de classe `BatteryDevice`
- `device_manager.py` ligne 323 : `must_run = {d for d in self.devices if d.must_run_now(reader)}`
- Ligne 347 : `if global_score < dispatch_threshold and not must_run:` — seuil de dispatch à supprimer
- `run_quota_h` existe dans `simulation/devices.py` mais **pas** dans `managed_device.py` — à vérifier et généraliser

### Tâches

#### Nouvelle classe `BatteryDevice` (dans `managed_device.py` ou fichier dédié)
- [ ] Créer `BatteryDevice` avec les règles suivantes :

  **`soc_min_jour`** (propriété calculée) :
  - Jour Tempo rouge → `soc_min_rouge` (config `CONF_BATTERY_SOC_RESERVE_ROUGE`)
  - Sinon → `soc_min` (config `CONF_BATTERY_SOC_MIN`)

  **`urgency`** (propriété) :
  - `1.0` si SOC < `soc_min_jour`
  - `0.0` si SOC ≥ `soc_max`
  - Rampe linéaire entre les deux : `(soc_max - soc) / (soc_max - soc_min_jour)`

  **`power_w`** (propriété, demande de charge) :
  - `charge_max_w` si SOC ≤ `soc_min_jour`
  - `0.0` si SOC ≥ `soc_max`
  - Linéaire entre les deux : `charge_max_w × (soc_max - soc) / (soc_max - soc_min_jour)`

  **`fit`** : toujours `1.0` (pas de calcul de zone)

  **`satisfied`** : `True` si SOC ≥ `soc_max`

  **`effective_score`** : `0.4 × priorité/10 + 0.3 × 1.0 + 0.3 × urgency`

  **`is_manual`** : lu depuis l'état du switch `helios_battery_manual`

#### Modifications sur `ManagedDevice`
- [ ] Supprimer `must_run_now()` — remplacé par `urgency == 1.0`
- [ ] Supprimer tout code lié à `CONF_DEVICE_MUST_RUN_DAILY`
- [ ] Mettre à jour `water_heater` : si `température < temp_min` → `urgency_modifier()` retourne `1.0`
- [ ] Mettre à jour la formule `effective_score()` :
  - Avec urgence (`water_heater`, `ev_charger`, `hvac`, `pool`, `appliance`) : `0.4 × priorité/10 + 0.3 × fit + 0.3 × urgence`
  - Sans urgence (`generic`) : `0.5 × priorité/10 + 0.5 × fit`
- [ ] Ajouter le champ `last_reason: str` (dernière décision prise, ex. `"urgency"`, `"greedy"`, `"satisfied"`, `"off_hours"`)

#### Modifications sur `DeviceManager`
- [ ] Supprimer la logique `must_run` (lignes 323–347)
- [ ] Supprimer le gate `global_score < dispatch_threshold` (ligne 347)
- [ ] Intégrer `BatteryDevice` dans la liste des candidats (si batterie activée et non manuelle)

### Interface exposée (consommée par Lot 4, Lot 6, Lot 8, Lot 9)
Tout device (ManagedDevice ou BatteryDevice) doit exposer :
```python
device.urgency: float          # [0.0–1.0]
device.power_w: float          # Puissance demandée (W)
device.fit: float              # [0.0–1.0] — calculé par le dispatcher sauf BatteryDevice
device.satisfied: bool         # Objectif atteint → ignorer
device.effective_score: float  # Score final utilisé pour le tri
device.is_manual: bool         # Exclu du dispatch si True
device.last_reason: str        # Dernière décision
device.min_on_remaining_s: float  # Secondes restantes de min_on_minutes (0 si écoulé)
device.is_on: bool             # État actuel
```

### Définition de terminé
`pytest tests/ -x -q` passe. Tester manuellement que `BatteryDevice.urgency` = 1.0 quand SOC < soc_min_jour via un test unitaire rapide.

---

## Lot 4 — `coordinator.py` : algorithme de dispatch unifié

### Fichiers à lire
- `custom_components/helios/coordinator.py` — fichier complet (long, ~600 lignes)
- `custom_components/helios/managed_device.py` — interface `ManagedDevice` après Lot 3
- `custom_components/helios/scoring_engine.py` — après Lot 2
- `custom_components/helios/const.py` — après Lot 1
- README.md sections : "Grandeurs clés du dispatch", "Calcul du score de fit", "Algorithme de dispatch"

### État actuel
- Ligne 84 : `self.dispatch_threshold = float(...)` — à supprimer
- Ligne 102 : `self.virtual_surplus_w` — existe déjà
- Ligne 103 : `self.bat_available_w` — existe déjà
- Lignes 221–227 : `update_weights()` et `dispatch_threshold` restaurés depuis le stockage — à supprimer
- Ligne 275 : `self.global_score = self.scoring_engine.compute(score_input)` — à mettre à jour
- Ligne 437 : `virtual_surplus_w = max(0.0, self.pv_power_w - self.house_power_w + helios_on_w)` — BatteryDevice doit être exclu de `helios_on_w`
- Ligne 380 : `_compute_bat_available_w()` — courbe pivot déjà implémentée, **ne pas modifier**

### Suppressions
- [ ] Supprimer `self.dispatch_threshold` et toutes ses utilisations
- [ ] Supprimer `async_save_optimizer_state()` et ses appels
- [ ] Supprimer la restauration de `scoring` et `dispatch_threshold` depuis le stockage (lignes 221–227)
- [ ] Supprimer l'écriture de `"scoring"` et `"dispatch_threshold"` dans le stockage (lignes 244–245)
- [ ] Supprimer la logique `must_run` dans `device_manager.py` (déjà décrit dans Lot 3)

### Algorithme de dispatch unifié
Remplace l'ancienne logique d'allumage/extinction séparée. À implémenter dans la méthode principale de dispatch :

**Étape 1 — Budget initial**
```python
remaining = virtual_surplus_w + bat_available_w
# grid_allowance_w est ABSENT de remaining — utilisé uniquement dans le calcul de fit zone 3
```

**Étape 2 — Phase obligatoire** (ordre : out-of-window → satisfied → urgency=1.0 → min_on)
```python
for device in all_candidates:
    if not device.in_window(current_hour):
        continue                          # ignoré, ni allumé ni compté
    if device.satisfied:
        turn_off(device)                  # éteindre si allumé
        continue
    if device.urgency >= 1.0:
        turn_on(device, reason="urgency")
        remaining -= device.power_w       # déduire même si remaining < 0
        obligatoire.add(device)
    elif device.is_on and device.min_on_remaining_s > 0:
        # maintenir allumé
        remaining -= device.power_w
        obligatoire.add(device)
```

**Étape 3 — Phase greedy**
```python
candidates = [d for d in all_candidates if d not in obligatoire
              and not d.satisfied and d.in_window(current_hour) and not d.is_manual]
selected = set()
while candidates:
    # Recalculer fit et score_effectif pour chaque candidat sur le remaining courant
    for d in candidates:
        d.fit = compute_fit(d.power_w, remaining, bat_available_w, grid_allowance_w)
        d.effective_score = d.compute_score()
    best = max(candidates, key=lambda d: d.effective_score)
    if best.fit <= 0.0:
        break                             # plus de budget
    turn_on(best, reason="greedy")
    remaining -= best.power_w
    selected.add(best)
    candidates.remove(best)
```

**Étape 4 — Extinction**
```python
for device in all_on_devices:
    if device not in obligatoire and device not in selected:
        turn_off(device, reason="budget")
```

### Calcul du fit (`compute_fit`)
```python
def compute_fit(power_w, remaining, bat_available_w, grid_allowance_w) -> float:
    surplus_pur = remaining - bat_available_w          # budget hors batterie
    if power_w <= 0:
        return 0.0
    if power_w <= surplus_pur:                         # Zone 1
        return power_w / max(1.0, surplus_pur)
    if power_w <= remaining:                           # Zone 2
        bat_used = power_w - surplus_pur
        return 1.0 - 0.6 * (bat_used / max(1.0, bat_available_w))
    if grid_allowance_w > 0 and power_w <= remaining + grid_allowance_w:  # Zone 3
        import_w = power_w - remaining
        return 0.4 * (1.0 - import_w / grid_allowance_w)
    return 0.0                                         # Hors budget
# BatteryDevice : fit = 1.0 toujours (ne passe pas par cette fonction)
```

### Score global
- [ ] Mettre à jour : `self.global_score, (f_surplus, f_tempo, f_solar) = self.scoring_engine.compute_components(score_input)` (après Lot 2)
- [ ] Stocker `f_surplus`, `f_tempo`, `f_solar` sur le coordinator pour les exposer dans le sensor

### Virtual surplus
- [ ] `helios_on_w` = Σ puissances des appareils Helios actifs, **BatteryDevice exclu** (sa charge est déjà dans `house_power_w` via le BMS)

### Définition de terminé
`pytest tests/ -x -q` passe. Vérifier via `--decisions` en simulation que l'extinction fonctionne (un appareil allumé au cycle N est éteint au cycle N+1 si le budget manque).

---

## Lot 5 — `config_flow.py` : nettoyage et priorité batterie

### Fichiers à lire
- `custom_components/helios/config_flow.py` — fichier complet
- `custom_components/helios/const.py` — après Lot 1

### État actuel
L'étape Stratégie contient des champs `weight_*`, `dispatch_threshold`, `optimizer_alpha` à supprimer.
L'étape Batterie ne contient pas encore `battery_priority`.
Il existe aussi un options flow (sections `sources | battery | strategy`).

### Tâches

#### Étape Stratégie (config flow + options flow)
- [ ] Retirer `weight_pv_surplus`, `weight_tempo`, `weight_battery_soc`, `weight_solar`
- [ ] Retirer `dispatch_threshold`
- [ ] Retirer `optimizer_alpha`
- [ ] Garder uniquement `scan_interval` et `mode`

#### Étape Batterie (config flow + options flow)
- [ ] Ajouter `battery_priority` : `vol.Optional(CONF_BATTERY_PRIORITY, default=DEFAULT_BATTERY_PRIORITY)` avec validateur `vol.All(int, vol.Range(min=1, max=10))`
- [ ] Ajouter la description dans `strings.json` et `translations/fr.json` / `translations/en.json`

### Définition de terminé
`pytest tests/test_init.py tests/test_sensor_setup.py -x -q` passe. Vérifier que le flow peut être complété sans erreur en simulation de config flow HA.

---

## Lot 6 — `daily_optimizer.py` : prévision journalière uniquement

### Fichiers à lire
- `custom_components/helios/daily_optimizer.py` — fichier complet
- `custom_components/helios/simulation/optimizer.py` — classe `OptResult` à supprimer/remplacer
- `custom_components/helios/simulation/engine.py` — moteur de simulation à utiliser
- `custom_components/helios/button.py` — appelle `async_run_daily_optimization` (ligne 46)
- `custom_components/helios/coordinator.py` — appelle `async_run_daily_optimization` (ligne 196)
- README.md section "Prévision journalière"

### État actuel
- `async_run_daily_optimization()` lance un grid search via `simulation/optimizer.py` puis appelle `coordinator.scoring_engine.update_weights()` et écrit `coordinator.dispatch_threshold`
- `OptResult` contient : `w_surplus`, `w_tempo`, `w_soc`, `w_solar`, `threshold`, `autoconsumption`, `savings_rate`, `cost_eur`, `objective`, `obj_mean`, `obj_std`
- La fonction est appelée depuis `coordinator.py` ligne 196 et `button.py` ligne 46

### Tâches
- [ ] Supprimer le grid search (supprime le recours à `simulation/optimizer.py`)
- [ ] Supprimer `OptResult` ou réduire à un dataclass `ForecastResult` sans champs de poids :
  ```python
  @dataclass
  class ForecastResult:
      forecast_pv_kwh: float
      forecast_consumption_kwh: float
      forecast_import_kwh: float
      forecast_export_kwh: float
      forecast_self_consumption_pct: float
      forecast_self_sufficiency_pct: float
      forecast_cost: float
      forecast_savings: float
      last_forecast: str          # ISO timestamp
  ```
- [ ] Renommer `async_run_daily_optimization` → `async_run_daily_forecast` — mettre à jour les imports dans `coordinator.py` et `button.py`
- [ ] Supprimer les appels à `coordinator.scoring_engine.update_weights()` et `coordinator.dispatch_threshold = ...`
- [ ] Utiliser `simulation/engine.py` pour lancer une simulation de la journée avec le nouvel algorithme de dispatch (Lot 4)
- [ ] Inclure `BatteryDevice` dans la simulation (Lot 3)
- [ ] Écrire le résultat sur `coordinator.forecast_data: ForecastResult` (nouvel attribut du coordinator)
- [ ] Supprimer la gestion de `must_run_daily`

### Définition de terminé
`pytest tests/test_daily_optimizer.py tests/test_weights.py -x -q` passe. Le forecast doit être produit sans exception avec les appareils par défaut.

---

## Lot 7 — `sensor.py` / `switch.py` : entités exposées ✅

### Tâches

#### `sensor.py`
- [x] `EnergyOptimizerScoreSensor` : attributs `f_surplus`, `f_tempo`, `f_solar` lus depuis `coordinator.f_surplus/f_tempo/f_solar` (suppression de `dispatch_threshold` obsolète)
- [x] `EnergyOptimizerBatterySensor` : ajout `urgency` et `effective_score` depuis `battery_device`
- [x] `DeviceStateSensor` : attribut `reason` ajouté (alias de `last_decision_reason`)
- [x] `ForecastSensor` créé (`suggested_object_id = "forecast"`) — état = `forecast_self_consumption_pct`, 9 attributs `ForecastResult`
- [x] `helios_optimizer_weights` vérifié : n'existe pas

#### `switch.py`
- [x] `BatteryManualSwitch` créé (`suggested_object_id = "battery_manual"`, `unique_id = "{entry_id}_battery_manual"`)
- [x] `BatteryDevice.manual_mode` persisté/restauré via le store HA (`__battery__` key)
- [x] Instancié uniquement si `battery_enabled=True`

### Résultat
`pytest tests/ -x -q` → **351 passed**

---

## Lot 8 — Simulation (`simulation/`) : dispatch unifié + CLI ✅ TERMINÉ

### Tâches

#### Modèles de simulation (`devices.py`)
- [x] Ajouter `SimBatteryDevice` avec les mêmes règles que `BatteryDevice` du Lot 3 :
  - `urgency`, `power_w`, `fit=1.0`, `satisfied`, `effective_score`
  - Paramètres : `soc_min`, `soc_min_rouge`, `soc_max`, `charge_max_w`, `priority`, `tempo_color`
  - Méthode `tick()` : la charge/décharge BMS reste autonome — `SimBatteryDevice` représente uniquement la demande de charge
- [x] Supprimer `must_run_daily` de `SimDevice`
- [x] Rendre `run_quota_h` générique : applicable à tout type (retirer le couplage exclusif avec `pool_required_min`)
- [x] `--bat-priority` câblé dans `SimConfig.bat_priority` (champ optionnel, défaut `7`)

#### Algorithme de dispatch (`engine.py`)
- [x] Supprimer `dispatch_threshold` de `SimConfig` et tous ses usages
- [x] Algorithme unifié 4 étapes délégué au vrai `DeviceManager.async_dispatch` (identique au Lot 4)
- [x] `compute_fit()` avec les 3 zones fourni par `device_manager.py` (même code que production)
- [x] Recalcul dynamique du `fit` après chaque allocation dans la phase greedy
- [x] `SimBatteryDevice` intégré dans le cycle de dispatch via `dm.battery_device`
- [x] Logique `must_run_now` supprimée (remplacée par urgency dans le vrai dispatcher)
- [x] Logique d'overcommit supprimée

#### Suppression du grid search
- [x] `simulation/optimizer.py` vidé — stub `OptResult` + `optimize()` lève `NotImplementedError`
- [x] `simulation/__init__.py` nettoyé (`OptResult`/`optimize` retirés)

#### CLI (`sim.py` / `run.py`)
- [x] Ajout `--bat-priority N` (défaut `7`)
- [x] Ajout `--bat-soc-min-red PCT` câblé dans `SimConfig.bat_soc_min_rouge`
- [x] Suppression `--optimize` et `--threshold`
- [x] Colonne `Remaining` dans la sortie verbose (`-v`)
- [x] Colonnes `--decisions` : `Eff.Score  Fit  Urgency  Remaining  SOC`

#### Correctif annexe
- [x] `BatteryDevice.turned_on_at = None` ajouté dans `managed_device.py` pour éviter `AttributeError` dans `_min_on_elapsed`

### Résultat
`pytest tests/test_simulation.py -x -q` : **29 passed**. `pytest tests/ -q` : **350 passed**. `python sim.py -v --decisions` s'exécute sans erreur.

---

## Lot 9 — Tests

### Fichiers à lire
- `tests/` — tous les fichiers existants
- Tous les fichiers modifiés par les lots 1–8

### `tests/test_weights.py` — mise à jour

#### Suppressions
- [ ] Supprimer toute la classe `TestDispatchThresholdApplication` (le quotidien ne met plus à jour `dispatch_threshold` ni les poids)
- [ ] Supprimer `test_update_weights_replaces_all` (méthode `update_weights()` supprimée)
- [ ] Supprimer `test_update_weights_partial`
- [ ] Supprimer `test_updated_weights_affect_score`
- [ ] Supprimer `test_surplus_weight_increases_sensitivity` (poids non configurables)
- [ ] Supprimer les imports `DEFAULT_WEIGHT_*`, `DEFAULT_OPTIMIZER_ALPHA`, `DEFAULT_DISPATCH_THRESHOLD`

#### Mises à jour
- [ ] `test_default_weights_loaded` → vérifier les constantes fixes : `_W_SURPLUS=0.5`, `_W_TEMPO=0.3`, `_W_SOLAR=0.2` (adapter selon l'API choisie en Lot 2)
- [ ] `test_score_range_always_01` → retirer `battery_soc` du `compute()` (f_soc absent du score global)
- [ ] `test_high_surplus_blue_raises_score` → retirer `battery_soc` du `compute()`
- [ ] `test_no_surplus_red_lowers_score` → retirer `battery_soc` du `compute()`

#### À conserver sans modification
- `TestSocScoring` (teste `_score_soc` directement — méthode toujours présente)
- `TestForecastScoring`
- `TestSeasonFromDate`
- `TestCloudFromForecast`

### `tests/test_bat_available.py` — aucune modification requise
Déjà à jour depuis la session précédente. Courbe pivot et tests associés sont corrects.

---

### `tests/test_battery_device.py` — nouveau fichier

```python
"""Tests pour BatteryDevice — urgence, power_w, fit, score_effectif."""
```

- [ ] `test_urgency_below_soc_min_jour` : SOC < `soc_min_jour` → `urgency == 1.0`
- [ ] `test_urgency_above_soc_max` : SOC ≥ `soc_max` → `urgency == 0.0`
- [ ] `test_urgency_linear_between` : rampe linéaire entre `soc_min_jour` et `soc_max`
- [ ] `test_power_w_at_soc_min` : SOC = `soc_min_jour` → `power_w == charge_max_w`
- [ ] `test_power_w_at_soc_max` : SOC = `soc_max` → `power_w == 0.0`
- [ ] `test_power_w_linear_between` : rampe linéaire entre les deux
- [ ] `test_fit_always_one` : `fit == 1.0` quelle que soit la situation
- [ ] `test_satisfied_above_soc_max` : SOC ≥ `soc_max` → `satisfied == True`
- [ ] `test_effective_score_formula` : `0.4×prio/10 + 0.3×1.0 + 0.3×urgency`
- [ ] `test_soc_min_jour_blue` : jour bleu → utilise `soc_min`
- [ ] `test_soc_min_jour_rouge` : jour rouge → utilise `soc_min_rouge`
- [ ] `test_manual_mode_excluded` : `is_manual=True` → ignoré du dispatch

---

### `tests/test_dispatch.py` — nouveau fichier

```python
"""Tests pour l'algorithme de dispatch unifié (Phase 1–4)."""
```

- [ ] `test_urgency_device_forced_regardless_of_budget` : urgence = 1.0 → démarrage même si `remaining < 0`
- [ ] `test_urgency_device_forced_on_red_day` : urgence = 1.0 → démarrage même en rouge, même sans surplus
- [ ] `test_min_on_device_maintained` : `min_on_minutes` non écoulé → maintenu même si score faible
- [ ] `test_out_of_window_ignored` : appareil hors plage → ignoré, ni allumé ni compté
- [ ] `test_satisfied_device_off` : objectif atteint → éteint si allumé
- [ ] `test_greedy_order_by_score` : l'appareil au score le plus élevé est sélectionné en premier
- [ ] `test_greedy_dynamic_fit_recalc` : le fit du second appareil est recalculé après allocation du premier
- [ ] `test_extinction_removes_unselected` : appareil allumé mais non sélectionné → éteint en phase 4
- [ ] `test_remaining_excludes_grid_allowance` : `remaining` = `surplus_virtuel + bat_available_w` sans `grid_allowance_w`
- [ ] `test_battery_device_in_greedy` : `BatteryDevice` entre en compétition phase 3 avec `fit=1.0`
- [ ] `test_manual_device_ignored` : `is_manual=True` → ignoré du dispatch entièrement

---

### `tests/test_fit.py` — nouveau fichier

```python
"""Tests pour le calcul de fit en 3 zones."""
```

- [ ] `test_zone1_fit` : `power_w ≤ surplus_pur` → `fit = power_w / surplus_pur`
- [ ] `test_zone1_full_surplus` : `power_w == surplus_pur` → `fit == 1.0`
- [ ] `test_zone2_fit` : `surplus_pur < power_w ≤ remaining` → `fit ∈ [0.4, 1.0]`
- [ ] `test_zone2_boundary_at_remaining` : `power_w == remaining` → `fit == 1.0 - 0.6 == 0.4`
- [ ] `test_zone3_fit` : `remaining < power_w ≤ remaining + grid_allowance` → `fit ∈ [0.0, 0.4]`
- [ ] `test_zone3_absent_on_red` : `grid_allowance_w = 0` → tout ce qui dépasse `remaining` donne `fit = 0`
- [ ] `test_out_of_budget` : `power_w > remaining + grid_allowance` → `fit == 0.0`
- [ ] `test_battery_device_fit_always_one` : `BatteryDevice` → `fit = 1.0` toujours
- [ ] `test_dynamic_remaining_after_allocation` : après allocation d'un appareil, `remaining` décrémenté → fit du suivant recalculé correctement

### Définition de terminé
`pytest tests/ -x -q` — tous les tests passent, zéro régression sur les 307 tests existants.

---

## Lot 10 — `www/helios-card.js` : mise à jour carte Lovelace

### Fichiers à lire
- `custom_components/helios/www/helios-card.js` — fichier complet à modifier
- README.md sections : "Entités exposées", "Carte Lovelace"

### Dépendances
Lot 10 dépend des lots 2, 4, 7 (nouvelles entités et attributs). Il peut être développé en parallèle des autres lots en ciblant la spécification du README.

### État actuel

| Ligne(s) | Problème |
|----------|---------|
| 778 | `score: "sensor.helios_global_score"` — ancien nom |
| 810, 815 | `disc?.["global_score"]` — ancien suffixe unique_id |
| 636–648 | Chip `h-sf-soc` (SOC factor) — supprimé du score |
| 935–938 | `factors` array : entrée `soc` avec `f_soc`/`w_soc` |
| 940–953 | Affichage des poids `×w_*` — attributs supprimés |
| 966–984 | Logique `dispatch_threshold` sur la barre de score — attribut supprimé |
| 1055 | Lit `last_decision_reason` → renommé `reason` (Lot 7) |
| 1103 | `_deviceIsOn` : `state === "running"` → `"on"` |
| 1183–1194 | `_reasonLabel` : raisons obsolètes (`must_run`, `score_too_low`, `overcommit`, `fit_negligible`) |
| 1280 | Modal lit `last_decision_reason` → `reason` |
| 1441–1449 | `getStubConfig` référence `sensor.helios_global_score` |

### Tâches

#### 1 — Renommage entité score
- [ ] `_discoverFromStates()` ligne 778 : `"sensor.helios_global_score"` → `"sensor.helios_score"`
- [ ] `_resolveAll()` lignes 810–815 : `disc?.["global_score"]` → `disc?.["score"]` et `disc["global_score"]` → `disc["score"]`
- [ ] `getStubConfig()` : `score: "sensor.helios_global_score"` → `"sensor.helios_score"`

#### 2 — Décomposition score : 3 facteurs (supprimer SOC)
- [ ] Supprimer le bloc HTML `h-sf-soc` + son séparateur `+` dans `_build()` (lignes 636–641)
- [ ] Dans `_doUpdate()`, retirer l'entrée `{ key: "soc", fAttr: "f_soc", wAttr: "w_soc" }` du tableau `factors`
- [ ] Supprimer l'affichage des poids (`w_*`) : retirer `this._txt(\`h-sf-${key}-w\`, ...)` et les balises `<span class="score-factor-w">` dans le HTML

#### 3 — Supprimer la barre dispatch_threshold
- [ ] Supprimer le `<div class="bar-threshold" id="h-score-threshold">` dans `_build()`
- [ ] Supprimer le bloc de logique `threshold` dans `_doUpdate()` (lignes 966–984) : colorer la barre selon le niveau de score (`> 0.6` vert, `> 0.3` orange, sinon rouge)
- [ ] Supprimer la CSS `.bar-threshold` si elle n'est plus utilisée

#### 4 — États et attributs appareils
- [ ] `_deviceIsOn()` : `state === "running"` → `state === "on"`
- [ ] `_renderDevice()` ligne 1055 : `"last_decision_reason"` → `"reason"`
- [ ] `_buildModalContent()` ligne 1280 : `"last_decision_reason"` → `"reason"`
- [ ] `_reasonLabel()` : mettre à jour la map des raisons :
  ```js
  const map = {
    urgency:        "Urgence",
    greedy:         "Surplus",
    satisfied:      "Satisfait",
    budget:         "Budget",
    off_hours:      "Hors plage",
    manual:         "Manuel",
    // rétrocompat (anciens logs)
    must_run:       "Forcé",
    dispatch:       "Surplus",
    no_budget:      "Budget",
    outside_window: "Hors plage",
  };
  ```

#### 5 — Batterie cliquable (modale)
- [ ] Rendre le nœud SVG `h-node-bat` cliquable : `cursor: pointer` + listener `click` → `this._openBatModal()`
- [ ] Créer `_openBatModal()` / `_closeBatModal()` / `_refreshBatModal()` sur le même patron que `_openModal()`
- [ ] Créer `_buildBatModalContent()` :
  - En-tête : icône 🔋, titre "Batterie", dot coloré selon action (`charge`/`discharge`/`idle`/`reserve`)
  - Section **Contrôle** : toggle `switch.helios_battery_manual` (même rendu que `hm-manual-btn`)
  - Section **État** :
    - SOC (%) avec barre de progression colorée (vert > 60 %, orange > 20 %, rouge sinon)
    - `power_w` (demande calculée, W)
    - `urgency` [0–1] avec chip coloré
    - `effective_score` [0–1]
  - Bouton fermer
- [ ] Résoudre `switch.helios_battery_manual` : chercher d'abord `disc["battery_manual"]` via l'entry_id, sinon `switch.helios_battery_manual` directement dans `hass.states`
- [ ] Câbler les actions `toggle-bat-manual` et `close-bat` dans le listener modal existant (ou un listener dédié `#h-bat-modal`)

#### 6 — Section prévision journalière
- [ ] Ajouter un `<div class="forecast" id="h-forecast">` dans `_build()`, après `h-budget-row`, masqué en mode compact
- [ ] CSS `.forecast` : titre de section + grille de chips sur le même style que `.budget-row`
- [ ] Dans `_doUpdate()`, résoudre l'entité forecast :
  - Auto-découverte : `disc["forecast"]` via l'entry_id, ou `sensor.helios_forecast` en fallback dans `_discoverFromStates()`
- [ ] Afficher les chips (section masquée si sensor absent/unavailable) :

  | Chip | Attribut | Format |
  |------|----------|--------|
  | ☀️ PV prévu | `forecast_pv_kwh` | `X.X kWh` |
  | 🏠 Conso | `forecast_consumption_kwh` | `X.X kWh` |
  | ⬇️ Import | `forecast_import_kwh` | `X.X kWh` |
  | ⬆️ Export | `forecast_export_kwh` | `X.X kWh` |
  | 🔄 Autoconso | `forecast_self_consumption_pct` | `XX %` |
  | 🛡️ Autosuffisance | `forecast_self_sufficiency_pct` | `XX %` |
  | 💶 Coût | `forecast_cost` | `X.XX €` |
  | 💰 Économie | `forecast_savings` | `X.XX €` |

- [ ] Afficher `last_forecast` comme sous-titre discret (ex. `Prévision du 14/04 05:00`)
- [ ] Section masquée en mode compact (cohérent avec `h-score-decomp` et `h-budget-row`)

### Définition de terminé
Vérification manuelle via `python3 -m http.server 8765 --directory custom_components/helios/www/` + `test_card.html` :
- Les 3 chips de score (Surplus, Tempo, Solaire) s'affichent sans chip SOC
- La barre de score n'a plus de marqueur de seuil, couleur dynamique
- Un clic sur le nœud batterie ouvre la modale avec toggle manuel et attributs `urgency`/`effective_score`
- La section prévision s'affiche en mode full et est absente en mode compact
- Aucune régression sur les flux SVG et les appareils existants
