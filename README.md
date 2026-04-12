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
| SOC min | Plancher de décharge (%). En dessous de ce seuil, aucun nouvel appareil n'est activé — la batterie est prioritaire. Les appareils déjà allumés et ceux en `must_run` ne sont pas bloqués. |
| SOC max | Plafond de charge (%). Quand le SOC atteint ce seuil, Helios active une tolérance réseau : il autorise un léger tirage du réseau pour continuer à faire tourner les appareils et éviter que la batterie stagne à 100 % en perdant en efficacité. |
| SOC réserve rouge | SOC à préserver les jours Tempo rouge (%). En dessous de ce seuil, les appareils ne peuvent pas puiser sur la batterie — seul le surplus PV direct les alimente. Au-dessus, le fonctionnement redevient normal. |
| Puissance max charge / décharge | Limites de puissance de l'onduleur (W) |
| Script charge forcée | Script HA appelé **une seule fois** quand Helios décide de charger la batterie depuis le réseau (la nuit précédant un jour Tempo rouge, pendant les heures creuses). Le script doit mettre l'onduleur en mode "charge forcée réseau" — Helios ne contrôle pas la puissance ni la durée, c'est le BMS/onduleur qui gère. |
| Script autoconsommation | Script HA appelé **une seule fois** quand Helios revient en mode normal (fin des HC, jour non rouge, etc.). Le script doit remettre l'onduleur en mode "autoconsommation" — la batterie absorbe le surplus PV et décharge sur déficit maison selon sa propre logique. |

### Configuration — étape Appareils

Chaque appareil configuré est piloté par Helios via un interrupteur HA. Les paramètres communs :

| Paramètre | Description |
|-----------|-------------|
| Nom | Nom affiché dans la carte et les logs |
| Type | `generic`, `ev_charger`, `water_heater`, `hvac`, `pool`, `appliance` |
| Entité switch | Switch HA à piloter |
| Puissance (W) | Puissance consommée quand l'appareil est actif |
| Entité puissance | Sensor de puissance réelle (W) — optionnel, affine le calcul d'énergie consommée |
| Priorité (1–10) | Importance relative — 10 = plus prioritaire |
| Début / fin de plage | Fenêtre horaire autorisée pour l'activation |
| Durée minimale allumé | Évite les cycles courts (minutes) |

#### Types d'appareils

Chaque type a sa propre logique de satisfaction (quand s'arrêter) et d'urgence (à quel point il est pressé).

**`generic`** — appareil générique interruptible. Helios l'active quand le score global dépasse le seuil et l'éteint dès que le surplus disparaît. Pas de critère de satisfaction interne : il fonctionne tant que les conditions sont favorables. Exemples : chargeur de batterie externe, ventilation VMC boostée, déshumidificateur, pompe de relevage, arrosage automatique.

**`water_heater`** — ballon d'eau chaude. Satisfait quand la température atteint la cible (°C). Helios peut forcer le démarrage (`must_run`) si la température descend sous le plancher configuré, ou en heures creuses si elle est sous le seuil HC. L'urgence monte en proportion du déficit de température.

| Paramètre spécifique | Description |
|----------------------|-------------|
| Entité température | Sensor °C du ballon |
| Température cible | Seuil de satisfaction en heures pleines (°C) — le chauffe-eau s'arrête une fois atteint |
| Température min | Plancher bas — déclenche `must_run` si la température descend sous ce seuil, indépendamment du score et du surplus |
| Entité température min HC | Sensor ou `input_number` fixant la température à atteindre pendant les heures creuses. Si absent, la température cible principale est utilisée |
| Hystérésis HC | Bande morte en heures creuses (°C) : Helios force le démarrage seulement si `temp < temp_min_hc − hystérésis`. Évite les cycles courts près du seuil (défaut : 3 °C) |

**`ev_charger`** — chargeur de véhicule électrique. Satisfait quand le SOC atteint la cible (%). L'urgence combine le déficit de SOC et la proximité d'un éventuel départ. Si le véhicule n'est pas branché (`ev_plugged_entity = off`), l'appareil est ignoré.

| Paramètre spécifique | Description |
|----------------------|-------------|
| Entité SOC | Sensor % de la batterie du VE |
| SOC cible | Seuil de satisfaction (%) |
| Entité branché | Sensor/binary_sensor indiquant si le VE est branché |

**`hvac`** — climatisation / pompe à chaleur. Satisfait quand la température mesurée atteint la consigne (chauffage : temp ≥ consigne − hystérésis ; refroidissement : temp ≤ consigne + hystérésis). L'urgence est proportionnelle à l'écart température mesurée / consigne.

| Paramètre spécifique | Description |
|----------------------|-------------|
| Entité température | Sensor °C de la pièce |
| Entité consigne | Sensor ou input_number de la température cible |
| Mode | `heat` ou `cool` |
| Hystérésis | Bande morte en °C pour éviter les cycles courts |

**`pool`** — pompe de piscine. Satisfait quand le quota journalier de filtration est atteint. En fin de fenêtre horaire, Helios force le démarrage (`must_run`) si le quota n'est pas atteint. L'urgence monte linéairement : `minutes manquantes / minutes restantes avant la fin de plage`.

| Paramètre spécifique | Description |
|----------------------|-------------|
| Quota journalier | Durée de filtration cible (heures) |
| Entité filtration | Sensor indiquant le temps de filtration déjà effectué (heures) |

**`appliance`** — électroménager à cycle unique (lave-vaisselle, lave-linge…). Non interruptible : une fois démarré, Helios ne l'éteint pas avant la fin du cycle. Le cycle est déclenché manuellement (bouton "Prêt" dans la carte) ou automatiquement via `appliance_schedule.json` en simulation. L'urgence est pilotée par la deadline automatique calculée au moment de la mise en attente (voir [Deadline automatique pour l'électroménager](#deadline-automatique-pour-lélectroménager)).

| Paramètre spécifique | Description |
|----------------------|-------------|
| Durée du cycle | Durée estimée du cycle en minutes — sert au calcul d'urgence |
| Deadline slots | Créneaux de fin souhaités, ex. `"12:00,18:00"` |
| Script de démarrage | Script HA appelé au lancement du cycle |

#### Priorité, poids et ordre de dispatch

À chaque cycle, Helios calcule un **score effectif** pour chaque appareil :

```
score_effectif = (w_priority × priorité/10 + w_fit × fit + w_urgency × urgence) / total_poids
```

- **`priorité/10`** — la priorité configurée, normalisée sur [0..1]
- **`fit`** — à quel point la puissance de l'appareil correspond au surplus disponible (voir détail ci-dessous)
- **`urgence`** — à quel point l'appareil a besoin de tourner bientôt (voir détail ci-dessous)

> **Rôle du score effectif** — Le score ne décide pas directement si un appareil est allumé ou éteint. La décision repose sur d'autres critères : satisfaction de l'objectif, budget `remaining` disponible, `fit >= 0.1`. Le score effectif sert uniquement à **trier les appareils** dans l'allocation greedy : en cas de budget limité, l'appareil au score le plus élevé est servi en premier. Il est aussi exposé dans la carte Lovelace pour visualiser l'état de chaque appareil.

Les **poids** (`w_priority`, `w_fit`, `w_urgency`) sont configurables par appareil et doivent sommer à 1.0. Ils déterminent quelle composante prime pour cet appareil :

| Exemple | Réglage conseillé |
|---------|-------------------|
| Ballon d'eau chaude (légionellose) | Augmenter `w_urgency` |
| Piscine (filtration solaire uniquement) | Augmenter `w_fit` |
| VE (priorité absolue) | Augmenter `w_priority` |

#### Grandeurs clés du dispatch

Ces valeurs sont calculées à chaque cycle et pilotent l'ensemble de la logique de dispatch.

| Grandeur | Formule / source | Rôle |
|----------|-----------------|------|
| **Surplus** (`surplus_w`) | `max(0, PV − maison)` | Excédent PV brut après consommation de la maison. Sert de point de départ au budget `remaining`. |
| **Surplus virtuel** (`virtual_surplus_w`) | `max(0, PV − maison + Σ appareils Helios actifs)` | Surplus corrigé en réajoutant la consommation des appareils Helios déjà allumés. Utilisé dans le scoring (`f_surplus`) pour éviter le chattering : sans cette correction, les appareils actifs gonflent la consommation maison → le surplus chute → le score passe sous le seuil → extinction → rallumage → oscillation. |
| **Batterie disponible** (`bat_available_w`) | `min(énergie_utilisable × 500, max_discharge) − décharge_en_cours` | Puissance de décharge additionnelle que la batterie peut fournir aux appareils au-delà de ce qu'elle fournit déjà à la maison. Tient compte du SOC restant au-dessus du plancher (soc_min ou soc_réserve_rouge), de la puissance max de l'onduleur, et déduit la décharge déjà en cours pour éviter un double-comptage. Vaut 0 si le SOC est sous le plancher. |
| **Remaining** (`remaining_w`) | `surplus_réel + bat_available + grid_allowance` | Budget de dispatch résiduel après l'allocation greedy. Décrémenté à chaque nouvel appareil démarré. Les appareils déjà actifs ne le réduisent pas — leur puissance est déjà absente du surplus réel (`real_surplus_w`). |

> **Surplus réel vs surplus virtuel** : les deux valeurs divergent dès qu'au moins un appareil Helios est allumé. Le surplus virtuel (`virtual_surplus_w`) est passé au moteur de scoring (`f_surplus`) et au calcul de fit. Le surplus réel (`surplus_w` = `max(0, PV − maison)`) sert de base au budget `remaining`. Cette séparation garantit que le score reste stable pendant qu'un appareil tourne, tout en maintenant un budget de dispatch fidèle à la réalité mesurée.

#### Calcul du score de fit [0..1]

Le fit mesure **à quel point l'appareil exploite bien le budget disponible** (surplus PV + batterie). Il ne mesure pas simplement si le solaire suffit.

**Zone 1 — le surplus PV couvre entièrement l'appareil** : `fit = puissance_appareil / surplus`

Le score monte avec la puissance de l'appareil relative au surplus. L'idée : si tu as 2000 W de surplus, un appareil de 200 W l'absorbe très mal (0.10), alors qu'un appareil de 1800 W l'absorbe presque entièrement (0.90). Helios préfère envoyer le surplus vers un appareil qui en profite vraiment.

**Zone 2 — la batterie complète le surplus** : `fit = 1.0 − 0.6 × (puissance_batterie_utilisée / puissance_décharge_disponible)` → toujours entre 0.4 et 1.0 (rejoint exactement le plafond de la zone 3)

**Zone 3 — import réseau nécessaire** : dépend de `grid_allowance_w` (tolérance réseau configurée) et de la couleur Tempo :
- Jour **Tempo rouge** → fit = 0 (tout import interdit)
- Import nécessaire **> grid_allowance** → fit = 0 (dépasse le plafond autorisé)
- Import nécessaire **≤ grid_allowance** → `fit = 0.4 × (1 − import / grid_allowance)` — entre 0 et 0.4, selon la marge restante

Exemples avec surplus PV = 600 W, batterie disponible = 1000 W, grid_allowance = 400 W, jour Tempo bleu :

| Appareil | Puissance | Zone | Calcul | Fit |
|----------|-----------|------|--------|-----|
| Pompe piscine | 200 W | 1 — solaire pur | 200 / 600 | **0.33** |
| Pompe piscine | 500 W | 1 — solaire pur | 500 / 600 | **0.83** |
| Ballon d'eau chaude | 600 W | 1 — solaire pur | 600 / 600 | **1.00** |
| Ballon d'eau chaude | 800 W | 2 — batterie appoint | 1 − 0.6 × (200/1000) | **0.88** |
| VE | 1400 W | 2 — batterie appoint | 1 − 0.6 × (800/1000) | **0.52** |
| VE | 1800 W | 3 — import 200 W ≤ 400 W | 0.4 × (1 − 200/400) | **0.20** |
| VE | 2100 W | 3 — import 500 W > 400 W | — | **0.00** |

À noter : un appareil en zone 2 peut avoir un fit **plus élevé** qu'un appareil en zone 1, si ce dernier n'absorbe qu'une petite fraction du surplus. C'est voulu : Helios préfère un appareil de 800 W qui utilise 600 W de solaire + 200 W de batterie (fit 0.92) à un appareil de 200 W qui n'utilise que 200 W sur 600 W de solaire disponible (fit 0.33).

#### Calcul du score d'urgence [0..1]

L'urgence dépend du type d'appareil :

| Type | Calcul |
|------|--------|
| **Ballon d'eau chaude** | `(température_cible − température_actuelle) / plage_temp` — monte quand la température baisse. En heures creuses, référence sur le minimum HC plutôt que la cible |
| **VE** | `0.6 × déficit_SOC + 0.4 × urgence_départ` — combine le déficit de charge et le temps restant avant l'heure de départ configurée |
| **HVAC** | `écart_consigne / 3°C` — urgence maximale à 3 °C d'écart |
| **Piscine** | `minutes_de_filtration_manquantes / minutes_restantes_avant_minuit` — monte en fin de journée si le quota n'est pas atteint |
| **Appareil programmable** | Basé sur la deadline automatique calculée au moment de la mise en attente : `0.3` baseline → rampe vers `0.8` si < 3h de marge → `0.8` si < 1h → `1.0` si plus le temps de finir le cycle |

##### Deadline automatique pour l'électroménager

Quand tu indiques qu'un appareil (lave-vaisselle, lave-linge…) est prêt à tourner, Helios calcule automatiquement une **deadline de fin de cycle** selon l'heure de la mise en attente :

| Heure de mise en attente | Deadline calculée |
|--------------------------|-------------------|
| Avant 12h00 | 12h00 — cycle terminé avant le déjeuner |
| 12h00 – 17h59 | 18h00 — cycle terminé en fin d'après-midi |
| 18h00 ou après | Minuit |

Cette deadline pilote la montée en urgence : l'appareil attend d'abord du surplus solaire, puis accélère à mesure que la deadline approche, et force le démarrage si le cycle ne peut plus se terminer à temps.

Les créneaux sont configurables par appareil via le champ **Deadline slots** (config flow, étape Appareils, type `appliance`). La valeur est une liste d'heures séparées par des virgules :

| Exemple | Comportement |
|---------|--------------|
| `12:00,18:00` | Défaut — finir avant midi ou avant 18h |
| `18:00` | Un seul créneau — finir avant 18h quelle que soit l'heure de lancement |
| `10:00,14:00,20:00` | Trois créneaux — finir avant 10h, 14h ou 20h |

Si tous les créneaux sont dépassés, la deadline tombe à minuit.

Les appareils sont traités en **ordre décroissant de score effectif** : le premier obtient le budget disponible en priorité. Si le surplus restant est insuffisant pour les suivants, ils ne démarrent pas. **La carte Lovelace affiche les appareils dans ce même ordre**, ce qui permet de voir en un coup d'œil quel appareil est le plus susceptible d'être activé (ou coupé) au prochain cycle.

#### Conditions d'extinction

Un appareil allumé est coupé à chaque cycle si **l'une** des conditions suivantes est remplie — dans cet ordre de priorité :

| Condition | Raison affichée dans les logs |
|-----------|-------------------------------|
| Hors de la plage horaire autorisée | `outside_window` |
| Objectif atteint (quota, température cible, SOC VE…) | `satisfied` |
| Fit < 0.1 — import réseau trop important ou jour Tempo rouge avec import | `fit_negligible` |
| Overcommit — la consommation totale des appareils actifs dépasse `surplus_PV + batterie_disponible + grid_allowance` ; l'appareil le moins prioritaire est coupé en premier, un seul par cycle | `overcommit` |

**Condition interruptible** : un appareil ne peut être coupé que s'il est marqué `interruptible` **et** que sa durée minimale (`min_on_minutes`) est écoulée depuis la dernière activation. Les appareils non-interruptibles (ex. lave-linge en cycle) ne sont **jamais** coupés par Helios, quelle que soit la situation.

---

### Configuration — étape Stratégie

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| Poids surplus PV | 0.40 | Importance du surplus PV dans le score |
| Poids Tempo | 0.30 | Importance de la couleur Tempo dans le score |
| Poids SOC batterie | 0.20 | Importance du SOC dans le score |
| Poids solaire | 0.10 | Importance du potentiel solaire instantané dans le score |
| Intervalle de scan | 5 min | Fréquence de la boucle de pilotage |
| Seuil de dispatch | 0.30 | Score global minimum pour activer les appareils |
| Mode | `auto` | `auto` \| `manual` \| `off` |
| Alpha optimiseur | 0.50 | Objectif de l'optimiseur quotidien : `0.0` = économies pures, `1.0` = autoconsommation pure |

> **Poids et optimiseur** — Les poids configurés ici servent uniquement de valeurs initiales au premier démarrage, avant que l'optimiseur quotidien n'ait tourné. Dès 5h00, l'optimiseur les recalcule et les écrase (résultat persisté). En cas d'échec de l'optimiseur, ces valeurs font office de fallback. En pratique, les laisser aux valeurs par défaut suffit.

> Les poids doivent sommer à 1.0 — le formulaire le valide.

---

### Score global

Le score global est un nombre entre 0 et 1 calculé à chaque cycle. Il répond à la question : **faut-il consommer de l'énergie maintenant ?** `1.0` = oui fortement, `0.0` = non.

Il sert principalement de **seuil de dispatch** : si le score est inférieur au seuil configuré (défaut 0.30), aucun appareil n'est activé ce cycle.

#### Formule

```
score = w_surplus × f_surplus + w_tempo × f_tempo + w_soc × f_soc + w_solar × f_solar
```

Chaque `f_*` ∈ [0..1]. Les poids sont configurés dans l'étape Stratégie et optimisés quotidiennement.

#### Composante surplus (`f_surplus`)

Mesure l'excédent de production PV par rapport à la consommation de base.

**Sans batterie** (ou batterie pleine) : rampe linéaire, 0 W → `0.0`, 500 W → `1.0`.

**Avec batterie active** (SOC < soc_max) : double pente pour éviter d'activer des appareils quand la batterie peut encore absorber le surplus :
- `[0 W … charge_max_w]` → `0.0` à `0.3` (la batterie peut absorber — pas urgent)
- `[charge_max_w … +500 W]` → `0.3` à `1.0` (surplus dépasse la capacité de charge — activer les appareils)

#### Composante Tempo (`f_tempo`)

| Couleur | Score |
|---------|-------|
| Bleu (pas cher) | 1.0 |
| Blanc | 0.5 |
| Rouge (cher) | 0.0 |
| Non configuré | 0.5 (neutre) |

#### Composante SOC batterie (`f_soc`)

Encourage à consommer quand la batterie est suffisamment chargée. Le score reste bas tant que le SOC n'a pas dépassé le pivot, puis monte fortement vers `soc_max`.

Avec `soc_min=20%`, `soc_max=95%`, pivot = `57.5%` :

| SOC | Score |
|-----|-------|
| ≤ soc_min (20%) | 0.0 — réserve, rien n'est activé |
| soc_min → pivot (20%→57.5%) | 0.0 → 0.3 — rampe plate (score bas, batterie prioritaire) |
| pivot → soc_max (57.5%→95%) | 0.3 → 1.0 — rampe forte (encourage la consommation) |
| ≥ soc_max (95%) | 1.0 — batterie pleine, consomme librement |

#### Composante prévision (`f_solar`)

Mesure le **potentiel solaire instantané** à partir de l'élévation du soleil, fournie par l'entité `sun.sun` intégrée dans Home Assistant (pas de configuration requise).

```
f = max(0, sin(élévation_en_radians))
```

| Élévation | Score | Moment typique |
|-----------|-------|----------------|
| ~60–65° | ~0.87–0.91 | Midi solaire en été |
| ~22° | ~0.37 | Midi solaire en hiver |
| 0° | 0.00 | Lever / coucher du soleil |
| < 0° | 0.00 | Nuit |

Avantages par rapport à une Gaussienne fixe :
- Tient compte des saisons (midi hivernal ≈ 22°, estival ≈ 63° pour la France)
- Tient compte de l'heure d'été/hiver automatiquement via HA
- S'arrête exactement au lever et au coucher, sans coupure arbitraire

La météo du moment reste capturée par `f_surplus` (surplus_w faible = nuageux). En simulation, l'élévation est calculée synthétiquement à partir des profils saisonniers pour rester cohérente avec la production PV simulée.

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
| Attribut `w_solar` | Poids solaire de production |
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
    attribute: w_solar
    name: Poids solaire
  - type: attribute
    entity: sensor.helios_optimizer_weights
    attribute: last_optimized
    name: Dernière optimisation
```

### Carte Lovelace

Une carte SVG animée visualisant les flux d'énergie est disponible. Ajouter manuellement dans **Paramètres → Tableau de bord → Ressources** :

#### Test visuel en local

Pour prévisualiser la carte sans Home Assistant (3 scénarios : jour, nuit, compact) :

```bash
python3 -m http.server 8765 --directory custom_components/helios/www/
```

Puis ouvrir [http://localhost:8765/test_card.html](http://localhost:8765/test_card.html) dans un navigateur.

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

Le moteur de simulation permet de tester et d'optimiser les paramètres de l'intégration hors de Home Assistant, sur une journée complète simulée.

Le code de simulation est situé **exclusivement** dans `custom_components/helios/simulation/`.
Le script `sim.py` à la racine du dépôt sert de point d'entrée unique pour le développement.

### Prérequis

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
```

### Utilisation rapide

Toutes les commandes se lancent **depuis la racine du dépôt** avec `python sim.py` :

```bash
# Journée d'été ensoleillée (paramètres par défaut)
python sim.py

# Journée d'hiver nuageuse, jour Tempo rouge, mode verbeux
python sim.py --season winter --cloud cloudy --tempo red -v

# Comparer toutes les saisons × conditions météo
python sim.py --compare

# Optimiser les paramètres du scoring
python sim.py --optimize


# Optimiser complete avec résultat optimisé
python sim.py --cloud clear --season spring --peak-pv 2800 --tempo blue --bat-soc 20 --bat-capacity 4.2 --bat-charge-max 1200 --bat-discharge-max 1200 --bat-efficiency 0.95 --bat-discharge-start 6 -v --devices custom_components/helios/simulation/config/devices.json --base-load custom_components/helios/simulation/config/base_load.json --optimize --opt-alpha 0.5 --opt-top 20 --opt-runs 10 --base-load-noise 0.20
python sim.py -v \
    --season spring \
    --peak-pv 2800 \
    --bat-soc 20 \
    --bat-capacity 4.2 \
    --bat-charge-max 1200 \
    --bat-discharge-max 1200 \
    --bat-efficiency 0.95 \
    --devices custom_components/helios/simulation/config/devices.json \
    --base-load custom_components/helios/simulation/config/base_load.json \
    --weight-surplus 0.6 \
    --weight-tempo 0.1 \
    --weight-soc 0.2 \
    --weight-solar 0.1 \
    --threshold 0.6
```

### Fichiers de configuration

Tous les paramètres métier sont dans `custom_components/helios/simulation/config/` :

| Fichier | Contenu |
|---------|---------|
| `devices.json` | Appareils fictifs pour la simulation sur PC — **indépendant de la config HA** (les appareils réels sont configurés via le config flow) |
| `appliance_schedule.json` | Planning de déclenchement des appareils programmables — **partagé** entre le simulateur et l'optimiseur quotidien HA |
| `base_load.json` | Consommation de fond — utilisé par la simulation **et** par l'intégration HA au démarrage (cold start EMA) ; remplacé progressivement par le profil appris |
| `tariff.json` | Tarifs EDF Tempo HC/HP — utilisé par la simulation **et** par l'optimiseur quotidien HA (calcul du taux d'économies dans la fonction objectif) |

```bash
python sim.py \
  --devices custom_components/helios/simulation/config/devices.json \
  --base-load custom_components/helios/simulation/config/base_load.json \
  --tariff custom_components/helios/simulation/config/tariff.json
```

#### Format `devices.json`

**Champs communs** à tous les types :

```json
[
  {
    "name": "Pompe piscine",
    "device_type": "pool",
    "power_w": 620,
    "allowed_start": 8.0,
    "allowed_end": 20.0,
    "priority": 6,
    "min_on_minutes": 15,
    "run_quota_h": 3.0
  }
]
```

| Champ | Défaut | Description |
|-------|--------|-------------|
| `name` | — | Nom affiché dans les logs et sorties |
| `device_type` | `"generic"` | `generic`, `pool`, `appliance` (voir ci-dessous) |
| `power_w` | — | Puissance consommée (W) |
| `allowed_start` / `allowed_end` | `0.0` / `24.0` | Fenêtre horaire autorisée (heures décimales, ex. `8.0` = 8h00) |
| `priority` | `5` | Priorité de dispatch 1–10 (10 = plus prioritaire) |
| `min_on_minutes` | `0` | Durée minimale allumé avant que Helios puisse éteindre |
| `run_quota_h` | — | Quota journalier en heures (l'appareil s'arrête une fois la durée atteinte) |
| `must_run_daily` | `false` | Force au moins un cycle par jour |
| `w_priority` / `w_fit` / `w_urgency` | `0.30/0.40/0.30` | Poids des composantes du score effectif (doivent sommer à 1.0) |

**Type `pool`** — suit un quota de filtration journalier (`run_quota_h`).

**Type `appliance`** — lave-linge, lave-vaisselle : cycle unique non interruptible déclenché manuellement.

```json
{
  "name": "Lave-vaisselle",
  "device_type": "appliance",
  "power_w": 2000,
  "allowed_start": 8.0,
  "allowed_end": 18.0,
  "priority": 9,
  "min_on_minutes": 30,
  "appliance_cycle_duration_minutes": 30,
  "appliance_deadline_slots": "12:00,18:00"
}
```

| Champ | Défaut | Description |
|-------|--------|-------------|
| `appliance_cycle_duration_minutes` | `120` | Durée estimée du cycle (min) — sert au calcul d'urgence et à la détection de fin si pas de capteur de puissance |
| `appliance_deadline_slots` | `"12:00,18:00"` | Créneaux de fin souhaités (heures séparées par des virgules). Helios choisit le premier slot encore atteignable au moment de la mise en attente et monte l'urgence en conséquence |
| `appliance_ready_at_start` | `false` | Si `true`, l'appareil est mis en attente dès le début de la simulation (rarement utile — préférer `appliance_schedule.json`) |

> L'heure de déclenchement (`ready_at_hour`) se configure dans `appliance_schedule.json`, pas dans `devices.json`. Cette séparation permet de rejouer la simulation avec des horaires différents sans modifier la définition des appareils.

#### Format `appliance_schedule.json`

`appliance_schedule.json` définit **quand** chaque appareil programmable est mis en attente (bouton "Prêt" pressé). Il est **distinct de `devices.json`** qui décrit *ce que* fait l'appareil : puissance, priorité, deadline slots…

Ce fichier est utilisé par deux composants :
- **Le simulateur** — chargé automatiquement depuis `simulation/config/appliance_schedule.json` (ou via `--appliance-schedule chemin/vers/fichier.json`)
- **L'optimiseur quotidien HA** — lu au démarrage de `ha_devices_to_sim()` depuis le même chemin bundlé

```json
[
  {
    "_comment": "Lave-vaisselle prêt à 8h → deadline auto 12:00",
    "name": "Lave-vaisselle",
    "ready_at_hour": 8.0
  },
  {
    "_comment": "Lave-linge prêt à 13h → deadline auto 18:00",
    "name": "Lave-linge",
    "ready_at_hour": 13.0
  }
]
```

| Champ | Description |
|-------|-------------|
| `name` | Nom de l'appareil — doit correspondre exactement au `name` dans `devices.json` |
| `ready_at_hour` | Heure décimale à laquelle l'appareil passe en état PREPARING (ex. `8.0` = 8h00, `13.5` = 13h30) |

La deadline est calculée automatiquement en fonction de `ready_at_hour` et des `appliance_deadline_slots` définis dans `devices.json`. Par exemple, un appareil mis en attente à 8h00 avec les slots `"12:00,18:00"` recevra une deadline à 12:00.

Si le fichier est absent ou illisible, la simulation et l'optimiseur continuent sans déclencher d'appareils programmables (log debug uniquement, pas d'erreur).

#### Format `base_load.json`

> **Note** — Ce fichier est une configuration temporaire destinée à la simulation. En production, Helios apprend automatiquement le profil de consommation de la maison grâce à l'EMA intégrée (`sensor.helios_base_load_profile`), sans nécessiter ce fichier.

`base_load.json` décrit la consommation de fond de la maison **sans les appareils pilotés** sous forme de segments horaires. Chaque segment définit une plage avec interpolation linéaire entre `w_start` et `w_end`.

```json
{
  "segments": [
    {"from": 0.0,  "to": 2.0,  "w_start": 400,  "w_end": 400},
    {"from": 2.0,  "to": 5.5,  "w_start": 250,  "w_end": 250},
    {"from": 5.5,  "to": 7.0,  "w_start": 250,  "w_end": 500},
    {"from": 7.0,  "to": 11.5, "w_start": 500,  "w_end": 450},
    {"from": 11.5, "to": 12.5, "w_start": 2000, "w_end": 2000},
    {"from": 12.5, "to": 18.5, "w_start": 450,  "w_end": 450},
    {"from": 18.5, "to": 21.0, "w_start": 1100, "w_end": 1100},
    {"from": 21.0, "to": 24.0, "w_start": 400,  "w_end": 400}
  ]
}
```

| Champ | Description |
|-------|-------------|
| `from` / `to` | Plage horaire en heures décimales (0.0 = minuit, 12.5 = 12h30) |
| `w_start` | Puissance en W au début du segment |
| `w_end` | Puissance en W à la fin du segment — interpolation linéaire entre les deux |

Les segments doivent couvrir la journée complète de 0.0 à 24.0 sans chevauchement ni trou. La puissance représente uniquement la consommation de fond (éclairage, électroménager passif, veille…), **sans** inclure les appareils déclarés dans `devices.json`.

#### Format `tariff.json`

> **Note** — Ce fichier est temporaire. À terme, le tarif sera configurable directement dans le config flow de HA avec trois modes : tarif fixe, HP/HC et EDF Tempo.

`tariff.json` définit les prix d'achat du kWh selon la plage horaire et la couleur Tempo. Il est utilisé par deux composants :
- **Le simulateur** (`--tariff chemin/vers/fichier.json`, ou le fichier bundlé par défaut)
- **L'optimiseur quotidien HA** (tourne à 05:00) — pour calculer le taux d'économies dans sa fonction objectif : `α × autoconsommation + (1−α) × économies`

**Mode actuel : EDF Tempo** (seul mode supporté)

```json
{
  "hc_start": 22.0,
  "hc_end": 6.0,
  "blue":  { "hc": 0.1325, "hp": 0.1612 },
  "white": { "hc": 0.1499, "hp": 0.1871 },
  "red":   { "hc": 0.1575, "hp": 0.7060 }
}
```

| Champ | Description |
|-------|-------------|
| `hc_start` | Heure de début des Heures Creuses (format décimal, ex. `22.0` = 22h00) |
| `hc_end` | Heure de fin des HC (ex. `6.0` = 6h00) — la plage traverse minuit |
| `blue.hc` / `blue.hp` | Prix HC et HP les jours bleus (€/kWh TTC) |
| `white.hc` / `white.hp` | Prix HC et HP les jours blancs (€/kWh TTC) |
| `red.hc` / `red.hp` | Prix HC et HP les jours rouges (€/kWh TTC) |

Les valeurs par défaut correspondent aux tarifs EDF Tempo TTC en vigueur au 03/03/2026.

**Modes à implémenter (roadmap config flow)**

| Mode | Description | Format cible |
|------|-------------|--------------|
| `flat` | Tarif fixe, même prix 24h/24 | `{ "type": "flat", "price": 0.25 }` |
| `hphc` | HP/HC classique, sans Tempo | `{ "type": "hphc", "hc_start": 22.0, "hc_end": 6.0, "hc": 0.17, "hp": 0.25 }` |
| `tempo` | EDF Tempo (actuel) | format ci-dessus |

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
| `--weight-solar 0-1` | `0.10` | Poids du potentiel solaire dans le score |

Pour rejouer une simulation avec les poids trouvés par `--optimize` :

```bash
# 1. Optimiser et noter la configuration optimale
python sim.py --optimize --opt-top 1

# 2. Rejouer avec ces poids
python sim.py \
  --weight-surplus 0.5 --weight-tempo 0.2 \
  --weight-soc 0.2 --weight-solar 0.1 \
  --threshold 0.30 -v
```

### Mode comparaison

Compare toutes les combinaisons saison × météo en une seule commande :

```bash
python sim.py --compare
```

```
  Saison    Météo               PV   Import   Export   Autocons.   Autosuff.
  winter    clear            10.6kWh  16.0kWh   0.6kWh  █████████░ 94.8%  ████░░░░░░ 38.5%
  winter    partly_cloudy     8.1kWh  17.3kWh   0.5kWh  █████████░ 93.8%  ███░░░░░░░ 30.6%
  ...
```

### Mode optimisation

Recherche par grille les poids du scoring et le seuil de dispatch qui maximisent
l'objectif **ajusté au risque** :

```
objectif = E[α × autoconsommation + (1-α) × taux_économie] − λ × écart-type
```

Le terme `−λ × std` pénalise les configurations fragiles (bonnes en moyenne mais
très variables selon la consommation du jour). C'est l'équivalent d'un ratio de Sharpe.

```bash
# Équilibré autoconsommation / économies (α=0.5), déterministe
python sim.py --optimize

# Priorité économies sur jour rouge
python sim.py --optimize --opt-alpha 0.0 --tempo red

# Monte Carlo : 10 runs avec bruit de consommation ±20%, pénalité risque λ=0.5
python sim.py --optimize --season winter --cloud partly_cloudy \
  --opt-runs 10 --base-load-noise 0.20 --opt-risk-lambda 0.5 --opt-top 5

# Très conservateur (λ=2) : favorise la stabilité sur la performance moyenne
python sim.py --optimize --opt-runs 20 --base-load-noise 0.20 --opt-risk-lambda 2.0
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--opt-alpha 0-1` | `0.5` | `1.0` = autoconsommation pure, `0.0` = économies pures |
| `--opt-runs N` | `1` | Tirages Monte Carlo par configuration (estimation de variance) |
| `--opt-risk-lambda 0-2` | `0.5` | Pénalité sur l'écart-type (`0`=pure moyenne, `2`=très conservateur) |
| `--base-load-noise 0-1` | `0.0` | Bruit multiplicatif journalier sur la charge de fond (σ) |
| `--opt-top N` | `10` | Nombre de résultats affichés |

La colonne **Obj** affiche l'objectif ajusté au risque (`mean − λ×std`), **Moy** la
moyenne des runs, **Std** l'écart-type. Avec `--opt-runs 1` (déterministe), Std = 0 et
Obj = Moy.

La configuration optimale est affichée directement copiable vers `SimConfig` ou
`custom_components/helios/scoring_engine.py`.

### Sortie verbose (`-v`)

```bash
python sim.py -v
```

```
      H       PV   Maison    Réseau   Batterie    SOC  Score  Appareils
  08:00   1.5 kW   1.4 kW     -12 W     +104 W    52%  0.92  Chauffe-eau
  09:00   2.1 kW   1.5 kW     -67 W     +605 W    50%  0.92  Chauffe-eau, Pompe piscine
  ...
```

- **Réseau** : `+` = import, `-` = export
- **Batterie** : `+` = charge depuis PV, `-` = décharge vers maison

### Log des décisions (`--decisions`)

Affiche chaque changement d'état d'appareil au pas de 5 minutes, avec le score, la production PV, le surplus et le SOC batterie au moment de la décision.

```bash
python sim.py --decisions
python sim.py -v --decisions   # combinable avec la vue horaire
```

```
  ── Décisions appareils (5 min) ──────────────────────────────────────────────
  Heure  Appareil           Action  Score      PV  Surplus    SOC
  08:15  Chauffe-eau          on  0.681    873W     387W    49%
  08:40  Piscine              on  0.749   1039W     557W    49%
  12:30  Zoé                  on  0.765   2432W    1982W    52%
  18:20  Zoé                 off  0.590    666W     216W    20%
```

Utile pour diagnostiquer un **chattering** (cycles ON/OFF rapides) ou vérifier qu'un appareil se déclenche dans la bonne fenêtre horaire.
