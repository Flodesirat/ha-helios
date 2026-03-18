# Helios

[![Tests](https://github.com/Flodesirat/ha-helios/actions/workflows/tests.yml/badge.svg)](https://github.com/Flodesirat/ha-helios/actions/workflows/tests.yml)

Intégration Home Assistant pour maximiser l'autoconsommation solaire et réduire la facture EDF Tempo en pilotant intelligemment les appareils et la batterie.

---

## Intégration Home Assistant

### Installation

Copier le dossier `custom_components/helios/` dans le répertoire `custom_components/` de Home Assistant, puis redémarrer. L'intégration est ensuite ajoutée via **Paramètres → Appareils et services → Ajouter une intégration → Helios**.

### Configuration — étape Sources

| Paramètre | Obligatoire | Description |
|-----------|-------------|-------------|
| Entité puissance PV | Oui | Sensor de production instantanée (W) |
| Entité puissance réseau | Non | Sensor import/export réseau (W, positif = import) |
| Entité puissance maison | Non | Sensor consommation totale du foyer (W) |
| Entité couleur Tempo | Non | Sensor retournant `blue`, `white` ou `red` |
| Entité couleur Tempo (lendemain) | Non | Couleur du prochain jour HP — utilisée par l'optimiseur à 5h (voir ci-dessous) |
| Entité prévision PV | Non | Sensor retournant les kWh restants à produire aujourd'hui |
| Puissance crête PV | Non | Puissance installée en Wc — utilisée par l'optimiseur (défaut : 3000 W) |

> **Pourquoi deux entités Tempo ?** L'optimiseur tourne à 5h, encore en heures creuses (22h–6h). La couleur qui s'applique à la journée qui commence (HP 6h–22h) est parfois stockée dans une entité "lendemain" par les intégrations Tempo. Si une seule entité est disponible, laisser l'autre vide.

### Configuration — étape Batterie

| Paramètre | Description |
|-----------|-------------|
| Batterie activée | Active tout le module batterie |
| Entité SOC | Sensor état de charge (%) |
| Capacité (kWh) | Capacité utile de la batterie |
| SOC min / max | Limites de décharge / charge (%) |
| SOC réserve rouge | SOC à maintenir les jours Tempo rouge (%) |
| Puissance max charge / décharge | Limites de puissance de l'onduleur (W) |
| Script charge forcée | Script HA à appeler pour forcer la charge |
| Script autoconsommation | Script HA pour repasser en mode autoconso |

### Configuration — étape Stratégie

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| Poids surplus PV | 0.40 | Importance du surplus PV dans le score |
| Poids Tempo | 0.30 | Importance de la couleur Tempo dans le score |
| Poids SOC batterie | 0.20 | Importance du SOC dans le score |
| Poids prévision | 0.10 | Importance de la prévision dans le score |
| Intervalle de scan | 5 min | Fréquence de la boucle de pilotage |
| Seuil de dispatch | 0.30 | Score global minimum pour activer les appareils |
| Mode | `auto` | `auto` \| `manual` \| `off` |
| Alpha optimiseur | 0.50 | Objectif de l'optimiseur quotidien : `0.0` = économies pures, `1.0` = autoconsommation pure |

> Les poids doivent sommer à 1.0 — le formulaire le valide.

---

### Optimiseur quotidien

Chaque matin à **5h00**, Helios lance automatiquement une recherche par grille pour trouver les poids de scoring et le seuil de dispatch optimaux pour la journée.

#### Fonctionnement

1. **Saison** — déterminée automatiquement à partir de la date du jour (décembre–février → hiver, etc.)
2. **Couverture nuageuse** — inférée depuis l'entité de prévision PV :
   - Le ratio `prévision / production théorique ciel clair` détermine `clear` / `partly_cloudy` / `cloudy`
   - Si l'entité n'est pas configurée, profil ciel clair par défaut
3. **Couleur Tempo** — lue depuis l'entité "couleur lendemain" (prioritaire) puis "couleur du jour"
4. **SOC initial** — lu depuis l'entité SOC batterie au moment du lancement
5. **Appareils** — les appareils configurés dans Helios sont convertis en modèles de simulation
6. **Recherche par grille** — ~168 combinaisons de poids × seuil évaluées en arrière-plan (non-bloquant)
7. **Application** — les meilleurs poids et seuil remplacent les valeurs courantes pour la journée

#### Paramètre alpha

Le paramètre **alpha** (`CONF_OPTIMIZER_ALPHA`) contrôle l'objectif de la recherche :

```
objectif = α × taux_autoconsommation + (1 - α) × taux_économie
```

| Alpha | Comportement |
|-------|-------------|
| `1.0` | Maximise l'autoconsommation (zéro export) |
| `0.5` | Équilibre autoconsommation et économies (défaut) |
| `0.0` | Minimise la facture (peut accepter un peu d'export si rentable) |

---

### Entités exposées

| Entité | Type | Description |
|--------|------|-------------|
| `sensor.helios_pv_power` | Sensor | Production PV instantanée (W) |
| `sensor.helios_grid_power` | Sensor | Puissance réseau (W) |
| `sensor.helios_house_power` | Sensor | Consommation maison (W) |
| `sensor.helios_score` | Sensor | Score global [0–1] |
| `sensor.helios_battery_action` | Sensor | Action batterie : `charge` \| `discharge` \| `reserve` \| `idle` |
| `sensor.helios_optimizer_weights` | Sensor | Poids du scoring actifs pour la journée (voir ci-dessous) |
| `switch.helios_auto_mode` | Switch | Active / désactive le pilotage automatique |

#### Détail — `sensor.helios_optimizer_weights`

| Champ | Description |
|-------|-------------|
| État | Seuil de dispatch actif `[0..1]` |
| Attribut `w_surplus` | Poids surplus PV |
| Attribut `w_tempo` | Poids couleur Tempo |
| Attribut `w_soc` | Poids SOC batterie |
| Attribut `w_forecast` | Poids prévision de production |
| Attribut `last_optimized` | Timestamp ISO de la dernière optimisation (5h du matin) |

Avant la première optimisation, l'entité reflète les poids configurés dans l'étape Strategy. Dès que l'optimiseur tourne à 5h, les valeurs sont mises à jour.

Exemple de carte Lovelace pour suivre les poids du jour :

```yaml
type: entities
title: Optimiseur Helios
entities:
  - entity: sensor.helios_optimizer_weights
    name: Seuil de dispatch
  - type: attribute
    entity: sensor.helios_optimizer_weights
    attribute: w_surplus
    name: Poids surplus PV
  - type: attribute
    entity: sensor.helios_optimizer_weights
    attribute: w_tempo
    name: Poids Tempo
  - type: attribute
    entity: sensor.helios_optimizer_weights
    attribute: w_soc
    name: Poids SOC batterie
  - type: attribute
    entity: sensor.helios_optimizer_weights
    attribute: w_forecast
    name: Poids prévision
  - type: attribute
    entity: sensor.helios_optimizer_weights
    attribute: last_optimized
    name: Dernière optimisation
```

### Carte Lovelace

Une carte SVG animée visualisant les flux d'énergie est disponible. Ajouter manuellement dans **Paramètres → Tableau de bord → Ressources** :

```
URL : /helios/helios-card.js
Type : Module JavaScript
```

Configuration minimale :

```yaml
type: custom:helios-card
entities:
  pv_power: sensor.helios_pv_power
  grid_power: sensor.helios_grid_power
  house_power: sensor.helios_house_power
  battery_soc: sensor.battery_soc      # optionnel
  score: sensor.helios_score
  battery_action: sensor.helios_battery_action
  auto_mode: switch.helios_auto_mode
```

---

## Simulation

Le répertoire `simulation/` permet de tester et d'optimiser les paramètres de l'intégration hors de Home Assistant, sur une journée complète simulée.

### Prérequis

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
```

### Utilisation rapide

```bash
# Journée d'été ensoleillée (paramètres par défaut)
python simulation/run.py

# Journée d'hiver nuageuse, jour Tempo rouge, mode verbeux
python simulation/run.py --season winter --cloud cloudy --tempo red -v

# Comparer toutes les saisons × conditions météo
python simulation/run.py --compare

# Optimiser les paramètres du scoring
python simulation/run.py --optimize
```

### Fichiers de configuration

Tous les paramètres métier sont dans `simulation/config/` :

| Fichier | Contenu |
|---------|---------|
| `devices.json` | Liste des appareils pilotés (puissance, fenêtre horaire, quotas) |
| `base_load.json` | Consommation de fond de la maison (segments horaires) |
| `tariff.json` | Tarifs EDF Tempo HC/HP par couleur (€/kWh) |

```bash
python simulation/run.py \
  --devices simulation/config/devices.json \
  --base-load simulation/config/base_load.json \
  --tariff simulation/config/tariff.json
```

#### Format `devices.json`

```json
[
  {
    "name": "Chauffe-eau",
    "power_w": 2000,
    "allowed_start": 8.0,
    "allowed_end": 18.0,
    "priority": 8,
    "min_on_minutes": 30,
    "run_quota_h": 2.0,
    "must_run_daily": true
  }
]
```

| Champ | Description |
|-------|-------------|
| `power_w` | Puissance de l'appareil (W) |
| `allowed_start` / `allowed_end` | Fenêtre horaire autorisée (heures décimales) |
| `priority` | Priorité de dispatch 1–10 (10 = plus prioritaire) |
| `min_on_minutes` | Durée minimale avant extinction |
| `run_quota_h` | Quota journalier (l'appareil s'arrête une fois la durée atteinte) |
| `must_run_daily` | Force au moins un cycle par jour |

#### Format `base_load.json`

Segments avec interpolation linéaire entre `w_start` et `w_end` :

```json
{
  "segments": [
    {"from": 0.0,  "to": 5.5,  "w_start": 300, "w_end": 300},
    {"from": 5.5,  "to": 7.0,  "w_start": 300, "w_end": 1000},
    {"from": 7.0,  "to": 9.0,  "w_start": 1000, "w_end": 1000}
  ]
}
```

#### Format `tariff.json`

```json
{
  "hc_start": 22.0,
  "hc_end": 6.0,
  "blue":  { "hc": 0.1325, "hp": 0.1612 },
  "white": { "hc": 0.1499, "hp": 0.1871 },
  "red":   { "hc": 0.1575, "hp": 0.7060 }
}
```

### Options complètes

#### Solaire

| Option | Défaut | Description |
|--------|--------|-------------|
| `--season` | `summer` | Saison : `winter` `spring` `summer` `autumn` |
| `--cloud` | `clear` | Météo : `clear` `partly_cloudy` `cloudy` |
| `--peak-pv W` | `4000` | Puissance crête PV (W) |

Courbes calibrées pour la France (~47°N) :

| Saison | Lever | Coucher | Intensité relative |
|--------|-------|---------|--------------------|
| winter | 8h00 | 17h00 | 50 % |
| spring | 6h30 | 20h00 | 82 % |
| summer | 5h30 | 21h30 | 108 % |
| autumn | 7h30 | 18h30 | 72 % |

#### Batterie

| Option | Défaut | Description |
|--------|--------|-------------|
| `--bat-soc PCT` | `50` | SOC initial (%) |
| `--bat-capacity KWH` | `10` | Capacité (kWh) |
| `--bat-charge-max W` | `2000` | Puissance max de charge (W) |
| `--bat-discharge-max W` | `2000` | Puissance max de décharge (W) |
| `--bat-efficiency` | `0.75` | Rendement aller-retour (0–1) |
| `--bat-soc-min PCT` | `20` | Plancher de décharge (%) |
| `--bat-soc-max PCT` | `95` | Plafond de charge (%) |
| `--bat-discharge-start H` | `6` | Heure à partir de laquelle la décharge est autorisée |
| `--no-battery` | — | Désactiver la batterie |

La batterie fonctionne en **mode autoconsommation** à partir de `--bat-discharge-start` :
- Surplus PV → charge batterie
- Déficit → décharge batterie pour éviter l'import réseau
- Avant `bat_discharge_start` : SOC maintenu (pas de décharge nocturne)

#### Tarif et coûts

| Option | Défaut | Description |
|--------|--------|-------------|
| `--tempo` | `blue` | Couleur Tempo du jour : `blue` `white` `red` |
| `--tariff JSON` | intégré | Fichier tarif personnalisé |

Les résultats incluent :
- **Coût avec PV** : uniquement l'énergie importée du réseau × tarif
- **Coût sans PV** : référence si toute la consommation venait du réseau
- **Économie réalisée** : différence des deux

#### Prévision de production

| Option | Défaut | Description |
|--------|--------|-------------|
| `--forecast-noise` | `0.15` | Écart-type de l'erreur de prévision (0 = parfaite) |

La simulation calcule à chaque pas la **production restante attendue** (courbe ciel dégagé),
puis applique un facteur d'erreur journalier aléatoire `N(1.0, σ)` pour simuler
l'imprécision d'une vraie entité de prévision.

Cela influence le scoring : beaucoup de production restante → patience (score bas),
peu de production restante → urgence (score haut).

#### Dispatch et scoring

| Option | Défaut | Description |
|--------|--------|-------------|
| `--threshold 0-1` | `0.30` | Seuil de score pour activer les appareils |
| `--weight-surplus 0-1` | `0.40` | Poids du surplus PV dans le score |
| `--weight-tempo 0-1` | `0.30` | Poids de la couleur Tempo dans le score |
| `--weight-soc 0-1` | `0.20` | Poids du SOC batterie dans le score |
| `--weight-forecast 0-1` | `0.10` | Poids de la prévision dans le score |

Pour rejouer une simulation avec les poids trouvés par `--optimize` :

```bash
# 1. Optimiser et noter la configuration optimale
python simulation/run.py --optimize --opt-top 1

# 2. Rejouer avec ces poids
python simulation/run.py \
  --weight-surplus 0.5 --weight-tempo 0.2 \
  --weight-soc 0.2 --weight-forecast 0.1 \
  --threshold 0.30 -v
```

### Mode comparaison

Compare toutes les combinaisons saison × météo en une seule commande :

```bash
python simulation/run.py --compare
```

```
  Saison    Météo               PV   Import   Export   Autocons.   Autosuff.
  winter    clear            10.6kWh  16.0kWh   0.6kWh  █████████░ 94.8%  ████░░░░░░ 38.5%
  winter    partly_cloudy     8.1kWh  17.3kWh   0.5kWh  █████████░ 93.8%  ███░░░░░░░ 30.6%
  ...
```

### Mode optimisation

Recherche par grille les poids du scoring et le seuil de dispatch qui maximisent
l'objectif combiné :

```
objectif = α × autoconsommation + (1-α) × taux_économie
```

```bash
# Équilibré autoconsommation / économies (α=0.5)
python simulation/run.py --optimize

# Priorité économies sur jour rouge
python simulation/run.py --optimize --opt-alpha 0.0 --tempo red

# Hiver nuageux, moyenner 10 runs pour lisser le bruit
python simulation/run.py --optimize --season winter --cloud partly_cloudy \
  --opt-runs 10 --opt-top 5
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--opt-alpha 0-1` | `0.5` | `1.0` = autoconsommation pure, `0.0` = économies pures |
| `--opt-runs N` | `1` | Runs moyennés par configuration |
| `--opt-top N` | `10` | Nombre de résultats affichés |

La configuration optimale est affichée directement copiable vers `SimConfig` ou
`custom_components/helios/scoring_engine.py`.

### Sortie verbose (`-v`)

```bash
python simulation/run.py -v
```

```
      H       PV   Maison    Réseau   Batterie    SOC  Score  Appareils
  08:00   1.5 kW   1.4 kW     -12 W     +104 W    52%  0.92  Chauffe-eau
  09:00   2.1 kW   1.5 kW     -67 W     +605 W    50%  0.92  Chauffe-eau, Pompe piscine
  ...
```

- **Réseau** : `+` = import, `-` = export
- **Batterie** : `+` = charge depuis PV, `-` = décharge vers maison
