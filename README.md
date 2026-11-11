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
| Entité couleur Tempo (lendemain) | Non | Couleur du prochain jour HP — utilisée par la prévision journalière à 5h (voir ci-dessous) |
| Entité prévision PV | Non | Sensor retournant les kWh restants à produire aujourd'hui |
| Puissance crête PV | Non | Puissance installée en Wc — utilisée par la prévision journalière (défaut : 3000 W) |

Dans les options, la section **Sources** reprend les entités configurées ; vider un champ facultatif retire la source (Helios ne la lit plus).

> **Pourquoi deux entités Tempo ?** La prévision journalière tourne à 5h, encore en heures creuses (22h–6h). La couleur qui s'applique à la journée qui commence (HP 6h–22h) est parfois stockée dans une entité "lendemain" par les intégrations Tempo. Si une seule entité est disponible, laisser l'autre vide.

### Configuration — étape Batterie

L'étape se fait en deux temps : les réglages communs, puis un formulaire par batterie (case « Ajouter une autre batterie » pour en déclarer plusieurs). Dans les options, la section **Batterie** ouvre les réglages communs puis la liste des batteries (ajouter, modifier, supprimer).

**Réglages communs**

| Paramètre | Description |
|-----------|-------------|
| Batterie activée | Active tout le module batterie |
| SOC min | Plancher de charge journalier (%). En dessous de ce seuil, la batterie est prioritaire sur tous les appareils (urgence = 1.0) et `bat_available_w` = 0 (la batterie ne cède rien aux autres). La régulation ne décharge pas non plus en dessous. |
| SOC min jour rouge | Plancher de charge les jours Tempo rouge (%), pour le pilotage des appareils. Remplace `SOC min` en jour rouge — même logique, plancher plus élevé pour protéger la réserve. **Ne limite pas la régulation.** |
| SOC max | Plafond de charge (%) pour le pilotage des appareils. La demande de charge de la batterie descend linéairement de `charge_max_w` à 0 entre `SOC min` et `SOC max`. Au-delà, la batterie ne demande plus de budget. La régulation, elle, charge jusqu'à 100 %. |
| Priorité batterie (1–10) | Importance de la batterie dans l'allocation greedy, sur la même échelle que les appareils. Défaut : 7. Une priorité haute garantit que la batterie est chargée avant les appareils moins importants ; une priorité basse lui permet de passer après eux quand le surplus est limité. Son urgence (dérivée du déficit SOC) peut amplifier ou atténuer l'effet de la priorité. |
| Cible réseau jour bleu / blanc / rouge | Puissance réseau visée par la régulation selon la couleur Tempo du jour (W, + = import). Défaut : 30 W — un léger import plutôt que de risquer d'exporter. |

**Par batterie**

| Paramètre | Description |
|-----------|-------------|
| Nom | Affiché dans la carte et les options |
| Entité SOC | Sensor état de charge (%) |
| Entité puissance | Sensor puissance batterie (W, négatif = charge, positif = décharge) |
| Entité consigne Drive | Entité `number` de la consigne de puissance (W, + = décharge, − = charge). Pour une Marstek Venus avec ha-marstek-local-api : `number.<batterie>_drive_grid_power`. Sans cette entité, la batterie n'est pas régulée. |
| Script de passage en Drive | Script appelé à l'activation du pilotage, au démarrage de Home Assistant et toutes les heures. Il met la batterie dans le mode où elle suit la consigne (ex. `select.<batterie>_operating_mode` → `Drive`). Helios attend la fin du script (jusqu'à 45 s). |
| Script de mode neutre | Script appelé quand le pilotage est désactivé ou qu'Helios est coupé : il rend la batterie à un mode autonome (ex. `Programmed`). |
| Capacité (kWh) | Capacité utile. Avec plusieurs batteries, le SOC global est la moyenne pondérée par les capacités. |
| Puissance max charge / décharge | Bornes de la consigne (W). Avec plusieurs batteries, le dispatch et l'optimiseur utilisent leur somme. |

Les scripts rendent Helios indépendant de la marque : toute batterie qui expose une consigne de puissance réglable peut être pilotée.

#### Régulation en mode Drive

Quand le pilotage est actif (`switch.helios_battery_driving` sur ON et Helios activé), Helios régule le réseau **à chaque relevé du capteur réseau**, indépendamment du cycle de dispatch des appareils. Un relevé est un nouveau `last_reported` : un relevé de même valeur est traité aussi.

```
consigne = clamp( puissance appliquée au moment du relevé + réseau − cible )
```

- **Correction immédiate et complète**, sans gain, bande morte ni filtre (testé sur une journée rejouée : ces variantes coûtaient environ 20 % d'import en plus).
- **Base = consigne réellement appliquée au moment de la mesure** : une consigne écrite compte comme appliquée 3 s après l'écriture. Sans cela, avec un compteur lent (10 à 15 s), la même erreur serait corrigée deux fois.
- **Bornes** : puissances max de chaque batterie ; pas de décharge si SOC ≤ `SOC min` ; pas de charge à 100 %.
- **Pas de décharge en heures creuses** : la consigne est bornée à ≤ 0 (la charge sur export reste possible).
- **Plusieurs batteries** : la consigne est répartie à parts égales (consigne / N). La part qu'une batterie ne peut pas prendre (vide, pleine, à sa puissance max) va aux autres. Une batterie dont le SOC est indisponible est laissée de côté.
- La consigne n'est écrite (`number.set_value`) que si sa valeur arrondie change. Le seuil d'envoi et le rafraîchissement vers la batterie restent gérés par l'intégration de la batterie.

**Pré-charge avant un jour rouge** — en heures creuses, quand la couleur du lendemain est rouge (`tempo_next_color`), la régulation est suspendue (on reste en Drive) : les batteries chargent à pleine puissance jusqu'à 100 %, plafonnées pour que l'import total ne dépasse pas la puissance souscrite (`grid_subscription_w`), recalculé à chaque relevé. Le chauffe-eau, qui chauffe la même nuit jusqu'à sa cible (voir `water_heater`), est compris dans cet import et passe en premier.

**Robustesse**

| Situation | Comportement |
|-----------|--------------|
| Passage en Drive | Régulation à partir de 0 W ; relevés ignorés 15 s après la première consigne non nulle (le changement de mode de la batterie est lent). |
| Toutes les heures | Rappel du script Drive, puis réécriture immédiate de la consigne courante. |
| Consigne perdue (ex. redémarrage de l'intégration de la batterie) | L'entité `number` est relue toutes les 60 s. Si elle diffère de la valeur écrite **deux lectures de suite**, Helios repart de 0 W, réécrit la consigne et applique à nouveau l'attente de 15 s. |
| Capteur réseau indisponible | Consigne inchangée. |
| Échec d'un appel de service | Journalisé ; le relevé suivant réessaie. |
| Redémarrage de HA / rechargement d'Helios | L'état de `switch.helios_battery_driving` est sauvegardé ; s'il était sur ON, le script Drive est rappelé. |

**Pilotage désactivé** (switch sur OFF, ou Helios coupé) : le script neutre est appelé, Helios n'écrit plus de consigne, et la batterie sort du dispatch des appareils (`bat_available_w` = 0, aucune demande de charge).

#### Forçage de la batterie

Comme les autres appareils, la batterie peut être forcée, depuis la modale batterie de la carte (clic sur la batterie) ou par ses entités :

| Réglage | Entité | Description |
|---------|--------|-------------|
| Sens | `select.helios_battery_force_direction` | `charge` ou `discharge` |
| Puissance | `number.helios_battery_force_power` | Puissance totale (W), répartie entre les batteries comme la régulation. Par défaut : la puissance max du sens choisi. |
| Durée | `select.helios_battery_force_duration` | 15 min à 24 h |
| Lancer / arrêter | `switch.helios_battery_force` | Attribut `force_remaining_min` |

- Pendant le forçage, la régulation est suspendue et la consigne est fixe (`+puissance` en décharge, `−puissance` en charge). Changer le sens ou la puissance s'applique tout de suite.
- **Seules les limites de SOC s'appliquent** : charge jusqu'à 100 %, décharge jusqu'à `SOC min`. La puissance souscrite et l'interdiction de décharger en heures creuses ne s'appliquent pas.
- Le forçage s'arrête à la fin de la durée, à l'arrêt du switch, ou dès que les batteries atteignent leur limite de SOC. La régulation reprend alors depuis la puissance du forçage.
- Il passe avant la pré-charge du jour rouge.
- Avec le pilotage sur OFF, le forçage utilise Drive le temps du forçage : script Drive au début, script neutre à la fin ; `switch.helios_battery_driving` reste sur OFF.
- Couper Helios annule le forçage. Pendant le forçage, la batterie sort du dispatch des appareils.
- Sens, puissance, durée et échéance sont sauvegardés : un forçage en cours reprend après un redémarrage.

#### Détection d'une batterie indisponible

Helios surveille en permanence les entités SOC configurées. Dès que l'état d'une batterie cesse d'être numérique (`unavailable`, `unknown`, ou toute valeur non parseable comme un nombre), elle est considérée comme **indisponible** :

| Grandeur | Comportement normal | Toutes les batteries indisponibles |
|----------|--------------------|-----------------------|
| `bat_available_w` | Calculé selon le SOC | **0 W** — aucune décharge allouée aux appareils |
| Demande de charge (`demand_w`) | Proportionnelle au déficit SOC | **0 W** — la batterie n'est plus candidate au dispatch |
| Buffer de puissance batterie | Alimenté à chaque cycle | **Non alimenté** — les échantillons indisponibles ne polluent pas la moyenne |
| Régulation | Consigne répartie sur les batteries | Une batterie indisponible est laissée de côté ; les autres continuent |

La carte Lovelace reflète également cet état : l'icône 🔋 passe en grisé et le SOC affiche **N/A** à la place du pourcentage.

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

**`generic`** — appareil générique interruptible. Helios l'active quand le fit est suffisant et qu'il reste du budget disponible, et l'éteint dès que le surplus disparaît. Pas de critère de satisfaction interne : il fonctionne tant que les conditions sont favorables. Exemples : chargeur de batterie externe, ventilation VMC boostée, déshumidificateur, pompe de relevage, arrosage automatique.

**`water_heater`** — ballon d'eau chaude. Satisfait quand la température atteint la cible (°C). L'urgence monte en proportion du déficit de température — si la température descend sous le plancher configuré, l'urgence atteint 1.0 et le démarrage est forcé indépendamment du surplus.

| Paramètre spécifique | Description |
|----------------------|-------------|
| Entité température | Sensor °C du ballon |
| Température cible | Seuil de satisfaction en heures pleines (°C) — le chauffe-eau s'arrête une fois atteint. Sert aussi de cible en heures creuses la veille d'un jour rouge |
| Température min | Plancher bas — si la température descend sous ce seuil, l'urgence atteint 1.0 et le démarrage est forcé indépendamment du surplus |
| Entité température min HC | Sensor ou `input_number` fixant la température à atteindre pendant les heures creuses. Si absent, la température min est utilisée |
| Hystérésis HC | Bande morte en heures creuses (°C) : Helios force le démarrage seulement si `temp < temp_min_hc − hystérésis`. Évite les cycles courts près du seuil (défaut : 3 °C) |

**Veille d'un jour rouge** : quand demain est un jour rouge Tempo (`tempo_next_color = red`), le chauffe-eau chauffe en heures creuses jusqu'à la **température cible** (celle de la journée) au lieu du minimum HC. Si le minimum HC est plus haut, c'est lui qui est gardé. Chauffer en HC (~0,16 €/kWh) évite de chauffer en heures pleines rouges (~0,71 €/kWh). En heures pleines, rien ne change (chauffe sur surplus, plancher légionelle). Aucun paramètre à régler.

La même nuit, la pré-charge de la batterie mesure l'import réseau (chauffe-eau compris) et limite sa charge pour rester sous la puissance souscrite : le chauffe-eau passe en premier.

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
| Entité consigne | `number`, `input_number` (état = température cible) ou `climate` (consigne lue dans l'attribut `temperature`) |
| Mode | `heat` ou `cool` |
| Hystérésis | Bande morte en °C pour éviter les cycles courts |

**`pool`** — pompe de piscine. Satisfait quand le quota journalier de filtration est atteint. L'urgence monte linéairement : `minutes manquantes / minutes restantes avant la fin de plage` — elle atteint 1.0 en fin de fenêtre si le quota n'est pas atteint, forçant le démarrage.

| Paramètre spécifique | Défaut | Description |
|----------------------|--------|-------------|
| Température de l'eau | — | Sensor °C de l'eau. Lu à minuit pour fixer le quota du jour (figé jusqu'au minuit suivant) |
| Température du local | — | Sensor °C du local technique, pour le hors-gel |
| Loi de filtration | `10:2, 14:4, 20:10, 24:12, 28:16, 32:24` | Points `température:heures`. Interpolation linéaire entre les points, valeurs des extrémités au-delà (ex. 17 °C → 7 h) |
| Hivernage automatique | désactivé | Active la bascule automatique en hivernage |
| Seuil d'hivernage | 12 °C | Eau à minuit sous ce seuil → hivernage ; sortie à seuil + 2 °C |
| Durée en hivernage | 2 h | Quota fixe en hivernage (au lieu de la loi) |
| Hors-gel : seuil des sessions | 2 °C | Local sous ce seuil → la pompe tourne au début de chaque heure ; sortie à seuil + 1 °C |
| Hors-gel : seuil de marche continue | −2 °C | Local sous ce seuil → marche continue ; sortie à seuil + 1 °C |
| Hors-gel : durée d'une session | 15 min | Minutes de marche au début de chaque heure |

**Journée** : le compteur de filtration et le quota repartent à minuit (premier cycle de la journée). Le hors-gel qui tourne après minuit compte donc dans la nouvelle journée.

**Hivernage** : pas de plage horaire. La filtration se fait sur le surplus solaire ; ce qui manque se fait en heures creuses avant minuit. Helios force la pompe en heures creuses dès que le temps d'heures creuses restant avant minuit ne dépasse plus le temps manquant (ex. HC 22h–6h, 2 h manquantes → 22h–24h). Jamais de marche forcée en heures pleines. Sans créneau d'heures creuses configuré, l'urgence se calcule jusqu'à minuit.

**Hors-gel** : prioritaire sur la plage horaire, les jours rouges et le quota. Le temps de marche compte dans la filtration du jour et peut dépasser le quota. Le mode manuel et un forçage ON/OFF en cours priment sur le hors-gel. Si la sonde du local devient indisponible, le niveau de hors-gel en cours est conservé. À la fin d'une session, la pompe revient au dispatch normal, sans attendre la durée minimale allumé.

**Jours rouges** : pendant les heures pleines d'un jour rouge Tempo (hors créneaux d'heures creuses de l'étape Stratégie), Helios n'allume pas la pompe et l'éteint si elle tourne (raison `red_peak`). Seuls le hors-gel et le forçage manuel restent possibles. Le déficit est accepté ; l'urgence peut le rattraper en heures creuses si la plage horaire le permet.

**Erreurs** : sans température d'eau (sonde absente, ou indisponible depuis minuit), il n'y a pas de quota du jour. Helios affiche « impossible de piloter la pompe » et ne touche plus à la pompe, sauf pour le hors-gel et le forçage manuel. Une sonde qui tombe en panne après la lecture de minuit ne gêne pas : le quota du jour est déjà fixé. Sans température du local (absente ou indisponible), Helios affiche « impossible de gérer le mode hors-gel » ; si la sonde tombe en panne pendant un hors-gel, le niveau en cours est conservé. Les erreurs sont écrites dans le journal HA (une fois, à leur apparition), exposées dans l'attribut `pool_errors` et affichées par la carte.

**Mise à jour depuis 1.1** : l'ancien paramètre « Entité heures de filtration » est supprimé. Une piscine configurée avant la 1.2 est en erreur « impossible de piloter la pompe » tant qu'elle n'est pas reconfigurée (Options → appareil) avec ses deux sondes.

**`appliance`** — électroménager à cycle unique (lave-vaisselle, lave-linge…). Non interruptible : une fois démarré, Helios ne l'éteint pas avant la fin du cycle. Le cycle est déclenché manuellement (bouton "Prêt" dans la carte) ou automatiquement via `appliance_schedule.json` en simulation. L'urgence est pilotée par la deadline automatique calculée au moment de la mise en attente (voir [Deadline automatique pour l'électroménager](#deadline-automatique-pour-lélectroménager)).

| Paramètre spécifique | Description |
|----------------------|-------------|
| Durée du cycle | Durée estimée du cycle en minutes — sert au calcul d'urgence |
| Deadline slots | Créneaux de fin souhaités, ex. `"12:00,18:00"` |
| Script de démarrage | Script HA appelé au lancement du cycle |

#### Mode manuel

Chaque appareil dispose d'un switch `helios_{slug}_manual` (pour la batterie, voir `switch.helios_battery_driving`). Quand il est activé, l'appareil est **entièrement ignoré du dispatch** — Helios ne lui alloue pas de budget, ne l'allume pas, ne l'éteint pas. L'utilisateur garde le contrôle total du switch physique associé.

#### Service — lancement immédiat d'un électroménager

Le service `helios.start_appliance` permet de forcer le démarrage immédiat d'un électroménager en état `PREPARING` (bouton "Prêt" pressé mais cycle pas encore lancé).

```yaml
service: helios.start_appliance
data:
  device_entity: sensor.helios_lave_vaisselle
```

| Champ | Description |
|-------|-------------|
| `device_entity` | `entity_id` du sensor Helios de l'électroménager (ex. `sensor.helios_lave_vaisselle`) |

Helios appelle le script de démarrage configuré et passe l'appareil en état `RUNNING`. Ce service est utilisé par le bouton **"Lancer maintenant"** dans la carte Lovelace.

#### Service — correction des économies

Le service `helios.set_savings` permet de corriger manuellement les compteurs d'économies, par exemple après une valeur de puissance aberrante qui a faussé le calcul.

```yaml
service: helios.set_savings
data:
  daily: 1.25
```

| Champ | Description |
|-------|-------------|
| `daily` | Nouvelle économie du jour (€) |
| `monthly` | Nouvelle économie du mois (€) |
| `total` | Nouvelle économie totale (€) |
| `config_entry_id` | Optionnel — limite la correction à une instance Helios |

Au moins un champ est requis. Un compteur corrigé reporte son écart sur les compteurs plus larges **non renseignés** (jour → mois → total) : dans l'exemple ci-dessus, si l'économie du jour valait 49,25 €, le mois et le total sont aussi diminués de 48 €. Les valeurs sont persistées immédiatement.

#### Priorité et ordre de dispatch

À chaque cycle, Helios calcule un **score effectif** pour chaque appareil non-manuel, batterie incluse.

**Avec notion d'urgence** (water_heater, ev_charger, hvac, pool, appliance, battery) :
```
score_effectif = 0.4 × priorité/10 + 0.3 × fit + 0.3 × urgence
```

**Sans notion d'urgence** (generic) :
```
score_effectif = 0.5 × priorité/10 + 0.5 × fit
```

- **`priorité/10`** — la priorité configurée (1–10), normalisée sur [0..1]
- **`fit`** — à quel point la puissance de l'appareil correspond au budget disponible (voir détail ci-dessous)
- **`urgence`** — à quel point l'appareil a besoin de tourner bientôt (voir détail ci-dessous)

> **Rôle du score effectif** — Le score ne décide pas directement si un appareil est allumé ou éteint. La décision repose sur d'autres critères : satisfaction de l'objectif, budget `remaining` disponible, `fit >= 0.1`. Le score effectif sert uniquement à **trier les appareils** dans l'allocation greedy : en cas de budget limité, l'appareil au score le plus élevé est servi en premier. Il est aussi exposé dans la carte Lovelace pour visualiser l'état de chaque appareil.

#### Grandeurs clés du dispatch

Ces valeurs sont calculées à chaque cycle et pilotent l'ensemble de la logique de dispatch.

| Grandeur | Formule / source | Rôle |
|----------|-----------------|------|
| **Surplus** (`surplus_w`) | `max(0, PV − maison)` soit `max(0, −réseau − décharge + charge)` | Bilan électrique instantané : positif quand le PV excède la consommation maison. La charge/décharge batterie y est implicitement incluse via `house_power_w` — la régulation de la batterie tourne dans sa propre boucle, à chaque relevé réseau. Sert de base au budget `remaining`. |
| **Surplus virtuel** (`virtual_surplus_w`) | `max(0, PV − maison + Σ appareils Helios actifs)` | Surplus corrigé en réajoutant la consommation des appareils que Helios a allumés. La batterie n'est **pas** dans ce Σ — sa charge/décharge est gérée par la régulation et déjà reflétée dans `house_power_w`. Sans cette correction, les appareils actifs gonflent `house_power_w` → surplus chute → fit s'effondre → extinction → rallumage → oscillation. Utilisé pour le calcul de `f_surplus` et de `fit`. |
| **Batterie disponible** (`bat_available_w`) | courbe pivot sur `[soc_min_jour … soc_max]`, plafonnée à `max_discharge_w` | Puissance de décharge supplémentaire estimée que la batterie peut fournir aux appareils. Vaut **0** si SOC < `soc_min` du jour (bleu/blanc) ou `soc_min_rouge` (jour rouge). Au-dessus, monte selon la courbe pivot : rampe plate jusqu'au pivot (0 → 0.3 × max), rampe forte ensuite (0.3 → 1.0 × max). Indépendant de la charge — `bat_available_w` représente la décharge possible, pas la charge en cours. Vaut également **0** si la batterie est indisponible (entité SOC non numérique). |
| **Remaining** (`remaining_w`) | `surplus_virtuel + bat_available_w` (initial) | Budget de dispatch résiduel. Initialisé en début de cycle, puis décrémenté à chaque appareil sélectionné dans l'ordre du score effectif — la `power_w` calculée du `BatteryDevice` est déduite comme n'importe quel appareil. Les appareils déjà actifs ne le réduisent pas — leur puissance est déjà absente de `virtual_surplus`. `grid_allowance_w` n'est **pas** dans `remaining` — il définit une tolérance d'import réseau utilisée uniquement dans la zone 3 du calcul de fit. |

> **Surplus réel vs surplus virtuel** : les deux valeurs divergent dès qu'au moins un appareil Helios est allumé. Le surplus virtuel (`virtual_surplus_w`) est utilisé pour `f_surplus` et le calcul de fit — il reflète ce que serait le surplus si ces appareils n'existaient pas. Le surplus réel (`surplus_w`) sert de base au budget `remaining` — il reflète la réalité mesurée. La batterie (gérée par la régulation) n'intervient pas dans cette correction.

#### Calcul du score de fit [0..1]

Le fit mesure **à quel point l'appareil exploite bien le budget disponible**. Il est calculé sur le `remaining_w` courant — recalculé après chaque allocation dans l'ordre du score effectif, de sorte que chaque appareil voit le budget réellement disponible après ceux mieux classés.

```
remaining_w = surplus_virtuel + bat_available_w − Σ appareils déjà alloués
```

`grid_allowance_w` n'est **pas** inclus dans `remaining` — il définit une tolérance d'import réseau au-delà du budget, utilisée uniquement pour le calcul de fit (zone 3).

**Cas particulier — BatteryDevice** : le fit de la batterie vaut toujours **1.0**. Sa demande est déjà calibrée par la courbe `power_w`, c'est l'urgence et la priorité qui la positionnent dans le tri.

**Zone 1 — surplus pur** (`puissance ≤ remaining − bat_available_w`) :
```
fit = puissance / (remaining − bat_available_w)
```
Monte avec la puissance de l'appareil relative au surplus disponible. Un appareil de 200 W face à 1000 W de surplus (fit 0.20) est moins bien classé qu'un appareil de 800 W (fit 0.80).

**Zone 2 — la batterie complète** (`remaining − bat_available_w < puissance ≤ remaining`) :
```
fit = 1.0 − 0.6 × (puissance_batterie_utilisée / bat_available_w)
```
Toujours entre 0.4 et 1.0. Un appareil en zone 2 peut dépasser un appareil en zone 1 qui n'absorbe qu'une petite fraction du surplus.

**Zone 3 — import réseau toléré** (`remaining < puissance ≤ remaining + grid_allowance_w`) :
```
fit = 0.4 × (1 − import_nécessaire / grid_allowance_w)
```
Entre 0 et 0.4. En **jour Tempo rouge**, `grid_allowance_w = 0` → zone 3 inexistante, tout ce qui dépasse `remaining` a un fit = 0.

**Hors budget** (`puissance > remaining + grid_allowance_w`) : `fit = 0`.

Exemples avec `surplus_virtuel = 1000 W`, `bat_available_w = 1000 W`, `grid_allowance_w = 400 W`, jour Tempo bleu (`remaining = 2000 W`) :

| Appareil | Puissance | Zone | Calcul | Fit |
|----------|-----------|------|--------|-----|
| Batterie | toute `power_w` | — | toujours 1.0 | **1.00** |
| Pompe piscine | 200 W | 1 — surplus pur | 200 / 1000 | **0.20** |
| Ballon d'eau chaude | 800 W | 1 — surplus pur | 800 / 1000 | **0.80** |
| Ballon d'eau chaude | 1200 W | 2 — bat. 200 W | 1 − 0.6 × (200/1000) | **0.88** |
| VE | 1800 W | 2 — bat. 800 W | 1 − 0.6 × (800/1000) | **0.52** |
| VE | 2200 W | 3 — import 200 W | 0.4 × (1 − 200/400) | **0.20** |
| VE | 2500 W | hors budget | — | **0.00** |

#### Calcul du score d'urgence [0..1]

L'urgence dépend du type d'appareil. Les appareils sans notion de deadline (`generic`) n'ont pas d'urgence — leurs poids sont redistribués sur priorité et fit.

| Type | Calcul |
|------|--------|
| **Batterie** | `1.0` si SOC < `soc_min` du jour — `0.0` si SOC ≥ `soc_max` — rampe linéaire entre les deux |
| **Ballon d'eau chaude** | `(température_cible − température_actuelle) / plage_temp` — monte quand la température baisse. En heures creuses, référence sur le minimum HC plutôt que la cible (sur la cible la veille d'un jour rouge) |
| **VE** | `0.6 × déficit_SOC + 0.4 × urgence_départ` — combine le déficit de charge et le temps restant avant l'heure de départ configurée |
| **HVAC** | `écart_consigne / 3°C` — urgence maximale à 3 °C d'écart |
| **Piscine** | `minutes_de_filtration_manquantes / minutes_restantes_avant_fin_de_plage` — monte en fin de fenêtre horaire si le quota n'est pas atteint |
| **Appareil programmable** | Basé sur la deadline automatique calculée au moment de la mise en attente : `0.3` baseline → rampe vers `0.8` si < 3h de marge → `0.8` si < 1h → `1.0` si plus le temps de finir le cycle |
| **`generic`** | Pas d'urgence — score effectif : `0.5 × priorité/10 + 0.5 × fit` |

##### Deadline automatique pour l'électroménager

Quand tu indiques qu'un appareil (lave-vaisselle, lave-linge…) est prêt à tourner, Helios calcule automatiquement une **deadline de fin de cycle** à partir des créneaux configurés via **Deadline slots** (liste d'heures séparées par des virgules) :

| Exemple | Comportement |
|---------|--------------|
| `12:00,18:00` | Défaut — le prochain créneau non dépassé devient la deadline |
| `18:00` | Un seul créneau — finir avant 18h quelle que soit l'heure de lancement |
| `10:00,14:00,20:00` | Trois créneaux — finir avant 10h, 14h ou 20h |

Avec le réglage par défaut `12:00,18:00`, le comportement est :

| Heure de mise en attente | Deadline calculée |
|--------------------------|-------------------|
| Avant 12h00 | 12h00 — cycle terminé avant le déjeuner |
| 12h00 – 17h59 | 18h00 — cycle terminé en fin d'après-midi |
| 18h00 ou après | Minuit |

Si tous les créneaux sont dépassés, la deadline tombe à minuit.

Cette deadline pilote la montée en urgence : l'appareil attend d'abord du surplus solaire, puis accélère à mesure que la deadline approche. **Quand urgence = 1.0** (plus assez de temps pour finir le cycle), le démarrage est forcé quelle que soit la situation — fit, budget, jour rouge. Les appareils à urgence = 1.0 passent en tête du tri et démarrent inconditionnellement.

> Ce mécanisme s'applique à tout appareil dont l'urgence peut atteindre 1.0 : électroménager (deadline dépassée), ballon d'eau chaude (temp < plancher), batterie (SOC < soc_min du jour).

Les appareils sont traités en **ordre décroissant de score effectif** : le premier obtient le budget disponible en priorité. Si le surplus restant est insuffisant pour les suivants, ils ne démarrent pas. **La carte Lovelace affiche les appareils dans ce même ordre**, ce qui permet de voir en un coup d'œil quel appareil est le plus susceptible d'être activé (ou coupé) au prochain cycle.

#### Algorithme de dispatch — allumage et extinction unifiés

Helios ne distingue pas "allumage" et "extinction" en deux passes séparées. À chaque cycle, il recalcule from scratch quels appareils doivent tourner, puis éteint ceux qui n'ont pas été sélectionnés.

**Étape 1 — Budget initial**
```
remaining = surplus_virtuel + bat_available_w
```

**Étape 2 — Phase obligatoire**

Les appareils garantis sont servis en premier, dans cet ordre :

| Condition | Raison |
|-----------|--------|
| Hors de la plage horaire autorisée | Ignoré — ni allumé ni compté dans `remaining` |
| Objectif atteint (quota, température cible, SOC VE…) | Ignoré — `satisfied` |
| Urgence = 1.0 (deadline dépassée, temp sous plancher, batterie sous `soc_min`) | Démarrage forcé — déduit du `remaining` |
| Déjà allumé et `min_on_minutes` non écoulé | Maintenu — déduit du `remaining` |

Les appareils non-interruptibles (ex. lave-linge en cycle) ne peuvent jamais être éteints par Helios — ils sont traités comme `min_on_minutes` non écoulé.

**Étape 3 — Phase greedy**

Avec le `remaining` après la phase obligatoire, les appareils restants concourent par score effectif décroissant. À chaque tour :
1. Le fit est recalculé avec le `remaining` courant
2. Le score effectif est recalculé
3. L'appareil au meilleur score est sélectionné, `remaining` est décrémenté de sa `power_w`
4. On recommence jusqu'à épuisement du budget ou des candidats

**Étape 3bis — Hystérèse de maintien**

Pendant la phase greedy, un appareil déjà allumé dont le fit tombe à 0 (surplus insuffisant) reçoit un bonus de budget décroissant plutôt que d'être éteint immédiatement :

```
bonus(t) = hysteresis_w × (1 − t / hysteresis_duration)
```

où `t` est le temps écoulé depuis l'entrée dans la zone de déficit. Le bonus permet à l'appareil de rester dans le budget pendant la durée configurée — utile lors d'une baisse momentanée du surplus (passage d'un nuage, pic de consommation domestique). Une fois le bonus épuisé, l'appareil passe en extinction normale à l'étape 4.

Le timer est remis à zéro dès que l'appareil repasse dans le budget réel, ou dès qu'il est éteint.

**Étape 4 — Extinction**

Tout appareil actuellement allumé qui n'a pas été sélectionné aux étapes 2, 3 ou 3bis est éteint.

> **Conséquence clé** : un lave-vaisselle à urgence élevée peut naturellement "déloger" une pompe de piscine à urgence basse si le budget est limité — non par une règle d'overcommit, mais parce que la piscine n'est simplement pas sélectionnée à l'étape 3.

---

### Configuration — étape Stratégie

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| Intervalle de scan | 5 min | Fréquence de la boucle de pilotage |
| Mode | `auto` | `auto` \| `manual` \| `off` |
| Bonus hystérèse (`hysteresis_w`) | 200 W | Budget supplémentaire accordé aux appareils déjà allumés quand le surplus baisse momentanément. Décroît linéairement jusqu'à zéro. Mettre à 0 pour désactiver. |
| Durée hystérèse (`hysteresis_duration_minutes`) | 10 min | Durée sur laquelle le bonus décroît jusqu'à zéro avant extinction de l'appareil. |

---

### Score global

Le score global est un nombre entre 0 et 1 calculé à chaque cycle. Il répond à la question : **est-ce un bon moment pour consommer de l'électricité ?** `1.0` = conditions idéales (surplus abondant, tarif bas, bon ensoleillement), `0.0` = défavorable.

C'est un **indicateur à destination de l'utilisateur** — il n'intervient pas dans le dispatch automatique des appareils. Il permet de décider si déclencher manuellement un appareil est pertinent, et est affiché de façon proéminente dans la carte Lovelace.

#### Formule

```
score = 0.5 × f_surplus + 0.3 × f_tempo + 0.2 × f_solar
```

Chaque `f_*` ∈ [0..1]. Les poids sont fixes.

#### Composante surplus (`f_surplus`)

Mesure l'excédent de production PV par rapport à la consommation de base.

Rampe linéaire de `0 W` à `charge_max_w + 500 W` → `0.0` à `1.0`.

Sans batterie, `charge_max_w = 0` → rampe de `0 W` à `500 W`. Avec batterie, le seuil de saturation est repoussé d'autant que la capacité de charge — un surplus de 500 W reste modeste si la batterie peut en absorber 2000 W.

#### Composante Tempo (`f_tempo`)

| Couleur | Score |
|---------|-------|
| Bleu (pas cher) | 1.0 |
| Blanc | 0.5 |
| Rouge (cher) | 0.0 |
| Non configuré | 0.5 (neutre) |

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

### Prévision journalière

Chaque matin à **5h00**, Helios lance une simulation de la journée à venir pour produire une prévision indicative.

#### Fonctionnement

1. **Saison** — déterminée automatiquement à partir de la date du jour
2. **Couverture nuageuse** — inférée depuis l'entité de prévision PV (ratio prévision / ciel clair) ; profil ciel clair par défaut si non configurée
3. **Couleur Tempo** — lue depuis l'entité "couleur lendemain" (prioritaire) puis "couleur du jour"
4. **SOC initial** — lu depuis les entités SOC des batteries au moment du lancement (moyenne pondérée par les capacités)
5. **Appareils** — les appareils configurés dans Helios (y compris le BatteryDevice) sont convertis en modèles de simulation
6. **Simulation** — la journée est simulée pas à pas avec le nouvel algorithme de dispatch

#### Résultats exposés

La prévision est disponible en attributs du sensor `helios_forecast` (voir aussi [Détail — `sensor.helios_forecast`](#détail----sensorhelios_forecast)) :

| Attribut | Description |
|----------|-------------|
| `forecast_pv_kwh` | Production PV estimée (kWh) |
| `forecast_consumption_kwh` | Consommation totale estimée (kWh) |
| `forecast_import_kwh` | Import réseau estimé (kWh) |
| `forecast_export_kwh` | Export réseau estimé (kWh) |
| `forecast_self_consumption_pct` | Taux d'autoconsommation estimé (% du PV consommé localement) |
| `forecast_self_sufficiency_pct` | Taux d'autosuffisance estimé (% de la consommation couverte par le PV) |
| `forecast_cost` | Coût journalier estimé (€) |
| `forecast_savings` | Économie estimée par rapport à une consommation 100 % réseau (€) |
| `last_forecast` | Timestamp ISO de la dernière simulation (5h du matin) |

---

### Entités exposées

#### Entités système

| Entité | Type | Description |
|--------|------|-------------|
| `sensor.helios_pv_power` | Sensor | Production PV instantanée (W) |
| `sensor.helios_grid_power` | Sensor | Puissance réseau (W, positif = import) |
| `sensor.helios_house_power` | Sensor | Consommation maison (W) |
| `sensor.helios_score` | Sensor | Score global [0–1] — indicateur : est-ce un bon moment pour consommer ? Attributs : `f_surplus`, `f_tempo`, `f_solar` (composantes détaillées) |
| `sensor.helios_forecast` | Sensor | Prévision journalière (mise à jour à 5h). État : taux d'autoconsommation estimé (%). Voir [Détail — `sensor.helios_forecast`](#détail----sensorhelios_forecast) |
| `switch.helios_auto_mode` | Switch | Active / désactive le pilotage automatique |

#### Entités batterie

| Entité | Type | Description |
|--------|------|-------------|
| `sensor.helios_battery` | Sensor | État de la régulation : `off` (non pilotée), `startup_wait` (passage en Drive), `regulating`, `precharge` (pré-charge jour rouge), `mode_lost` (consigne perdue, reprise depuis 0 W), `forced` (forçage). Mis à jour à chaque relevé réseau. Attributs : `setpoint_w` (consigne calculée, + = décharge), `target_w` (cible réseau active), `last_grid_w` (dernier relevé), `batteries` (consigne écrite par batterie), `driving`, `forced`, `force_direction`, `force_power_w`, `force_duration_h`, `force_remaining_min`, `soc` (%, pondéré par les capacités), `power_w` (W mesurés, négatif = charge), `available_w` (W de décharge disponibles pour le dispatch), `demand_w` (W demandés en charge), `battery_available`, `urgency` [0–1], `effective_score` [0–1] |
| `switch.helios_battery_driving` | Switch | Helios pilote les batteries (mode Drive). OFF → script neutre, plus de consigne, batterie exclue du dispatch. État sauvegardé. Remplace `switch.helios_battery_manual` (supprimé à la mise à jour ; un mode manuel actif devient pilotage OFF). |
| `switch.helios_battery_force` | Switch | Lance / arrête un forçage (voir [Forçage de la batterie](#forçage-de-la-batterie)). Attribut `force_remaining_min`. |
| `select.helios_battery_force_direction` | Select | Sens du forçage : `charge` / `discharge` |
| `number.helios_battery_force_power` | Number | Puissance du forçage (W, totale) |
| `select.helios_battery_force_duration` | Select | Durée du forçage (15 min à 24 h) |
 
#### Entités par appareil

Pour chaque appareil configuré (slug = nom normalisé) :

| Entité | Type | Description |
|--------|------|-------------|
| `sensor.helios_{slug}` | Sensor | État de l'appareil — `on` \| `off` \| `satisfied` \| `waiting`. Attributs : `effective_score`, `fit`, `urgency`, `power_w`, `reason` (dernière décision) |
| `switch.helios_{slug}_manual` | Switch | Passe l'appareil en mode manuel — Helios ne le pilote plus, l'état du switch physique reste sous contrôle de l'utilisateur |

Piscine, en plus :

| Entité | Type | Description |
|--------|------|-------------|
| `binary_sensor.helios_{slug}_wintering` | Binary sensor | ON en hivernage. Attributs : `wintering_enabled`, `wintering_temp`, `wintering_duration_h` |
| `binary_sensor.helios_{slug}_frost_protection` | Binary sensor | ON quand le hors-gel est actif. Attribut `frost_level` : `off`, `sessions`, `continuous` |

Le sensor `sensor.helios_{slug}` d'une piscine porte aussi `pool_mode` (`manual`, `frost`, `wintering`, `auto`, dans cet ordre de priorité ; `frost` quand la pompe tourne pour le hors-gel), `pool_errors` (`no_water_temperature`, `no_local_temperature`), `water_temperature`, `local_temperature`, `pool_wintering`, `frost_level`, `filtration_done_min`, `filtration_required_min`. La carte affiche le mode (❄️ en hors-gel), les deux températures et les erreurs.

#### Entités énergie et économies

Compteurs journaliers remis à zéro à minuit, et cumul total des économies :

| Entité | Type | Description |
|--------|------|-------------|
| `sensor.helios_energy_pv` | Sensor | Énergie PV produite dans la journée (kWh) |
| `sensor.helios_energy_import` | Sensor | Énergie importée du réseau dans la journée (kWh) |
| `sensor.helios_energy_export` | Sensor | Énergie exportée vers le réseau dans la journée (kWh) |
| `sensor.helios_energy_consumption` | Sensor | Consommation totale de la journée (kWh) |
| `sensor.helios_daily_savings` | Sensor | Économie réalisée dans la journée par rapport à une consommation 100 % réseau (€) — remis à zéro à minuit |
| `sensor.helios_total_savings` | Sensor | Économie cumulée depuis la création de l'intégration (€) |

L'économie est calculée à chaque relevé : `(consommation maison − import réseau) × prix du moment` (couleur Tempo × HC/HP), soit le coût sans installation moins le coût réel. L'export n'est pas valorisé. Elle n'est pas bornée à 0 : une charge de la batterie depuis le réseau (forçage, pré-charge de nuit) **fait baisser** les compteurs du coût de l'énergie achetée, que la décharge restitue ensuite au tarif du moment. Sur un cycle charge HC / décharge HP, il reste le gain d'arbitrage moins les pertes. L'économie d'une journée peut donc être négative.

#### Entité prévision journalière

| Entité | Type | Description |
|--------|------|-------------|
| `sensor.helios_forecast` | Sensor | État : taux d'autoconsommation estimé (%). Attributs détaillés ci-dessous. Mis à jour chaque matin à 5h. |

#### Détail — `sensor.helios_forecast`

| Attribut | Description |
|----------|-------------|
| `forecast_pv_kwh` | Production PV estimée pour la journée (kWh) |
| `forecast_consumption_kwh` | Consommation totale estimée (kWh) |
| `forecast_import_kwh` | Import réseau estimé (kWh) |
| `forecast_export_kwh` | Export réseau estimé (kWh) |
| `forecast_self_consumption_pct` | Taux d'autoconsommation estimé (% du PV consommé localement) |
| `forecast_self_sufficiency_pct` | Taux d'autosuffisance estimé (% de la consommation couverte par le PV) |
| `forecast_cost` | Coût journalier estimé (€) |
| `forecast_savings` | Économie estimée par rapport à une consommation 100 % réseau (€) |
| `last_forecast` | Timestamp ISO de la dernière simulation (5h du matin) |

### Carte Lovelace

Une carte SVG animée visualisant les flux d'énergie en temps réel. Ajouter dans **Paramètres → Tableau de bord → Ressources** :

```
URL : /helios/helios-card.js
Type : Module JavaScript
```

#### Test visuel en local

```bash
python3 -m http.server 8765 --directory custom_components/helios/www/
# → http://localhost:8765/test_card.html
```

---

#### Vue flux SVG

Le cœur de la carte est un diagramme SVG avec quatre nœuds (☀️ PV, 🏠 Maison, ⚡ Réseau, 🔋 Batterie) reliés par des lignes animées indiquant la direction et l'intensité des flux.

Chaque nœud affiche directement dans le SVG :
- La **puissance instantanée** sur la ligne (W ou kW)
- L'**énergie journalière** en label secondaire (kWh) — sur la ligne PV, sous les nœuds Réseau et Maison — toujours visible, y compris en mode compact

La **batterie** est toujours cliquable — elle ouvre une modale avec SOC, état, urgence et switch manuel.

---

#### Options de configuration

| Option | Type | Défaut | Description |
|--------|------|--------|-------------|
| `compact` | bool | `false` | Vue condensée : flux SVG et barre de score, toutes les autres sections masquées |
| `info_url` | string | — | URL du bouton ℹ️ (coin haut droit) |
| `node_urls` | object | — | URLs de navigation au clic sur les nœuds SVG (voir ci-dessous) |
| `sections` | object | — | Affichage par section en mode non-compact (voir ci-dessous) |
| `entry_id` | string | auto | Identifiant de l'entrée Helios (détecté automatiquement) |
| `entities` | object | auto | Entités explicites — remplace l'auto-découverte |

---

#### Mode compact

Flux SVG et barre de score (masquable avec `sections.score: false`), sans les autres sections. Idéal pour une vue secondaire ou une tuile résumée.

```yaml
type: custom:helios-card
compact: true
info_url: /lovelace/energie   # optionnel
```

---

#### Navigation au clic sur les nœuds

Les nœuds ☀️, ⚡ et 🏠 peuvent naviguer vers une page Lovelace ou une URL externe. Un nœud sans URL configurée n'est pas cliquable (pas de curseur).

```yaml
type: custom:helios-card
node_urls:
  pv:    /lovelace/solaire        # ☀️ nœud PV
  grid:  /lovelace/reseau         # ⚡ nœud réseau
  house: /lovelace/consommation   # 🏠 nœud maison
```

URLs relatives → navigation HA interne. URLs absolues (`https://…`) → nouvel onglet.

---

#### Sections configurables

En mode non-compact, les sections ci-dessous s'affichent sous le flux SVG quand les données sont disponibles. Chacune peut être masquée indépendamment en la passant à `false`. La barre de score (`score`) est la seule à rester affichée en mode compact.

| Section | Défaut | Contenu |
|---------|--------|---------|
| `money_saving` | `true` | Économies journalières et totales (€) |
| `score` | `true` | Barre du score global, aussi en mode compact. `false` masque aussi sa décomposition |
| `score_detailed` | `true` | Décomposition du score global (f_surplus, f_tempo, f_solar avec poids) |
| `surplus_computation` | `true` | Budget dispatch : surplus, surplus virtuel, bat. disponible, remaining |
| `forecast` | `true` | Prévision journalière (mise à jour à 5h) |
| `devices` | `true` | Liste des appareils triés par score effectif |

Exemple — flux + score barre + appareils uniquement :

```yaml
type: custom:helios-card
sections:
  money_saving: false
  score_detailed: false
  surplus_computation: false
  forecast: false
```

Exemple — vue compacte sans score (flux SVG seul) :

```yaml
type: custom:helios-card
compact: true
sections:
  score: false
```

> Les labels kWh dans le SVG (énergie journalière) ne font pas partie des sections — ils sont toujours affichés, y compris en mode compact.

---

#### Configuration complète (entités manuelles)

En mode automatique, la carte détecte toutes les entités Helios via le registre HA. Ce mode manuel permet de pointer explicitement des entités tierces ou de contourner l'auto-découverte.

```yaml
type: custom:helios-card
entities:
  pv_power:      sensor.helios_pv_power
  grid_power:    sensor.helios_grid_power    # positif = import, négatif = export
  house_power:   sensor.helios_house_power
  score:         sensor.helios_score
devices:
  - name: Piscine
    type: pool
    entity:              switch.helios_piscine_manual
    filtration_done:     sensor.helios_pool_filtration_done     # minutes
    filtration_required: sensor.helios_pool_filtration_required # minutes
    force_remaining:     sensor.helios_pool_force_remaining     # optionnel, minutes
  - name: Chauffe-eau
    type: water_heater
    entity:      switch.helios_chauffe_eau_manual
    temp_entity: sensor.temperature_chauffe_eau
    temp_target: 61                           # optionnel — cible affichée (°C)
  - name: Lave-vaisselle
    type: appliance
    entity:       switch.helios_lave_vaisselle_manual
    state_entity: sensor.helios_lave_vaisselle
  - name: Voiture
    type: ev_charger
    entity:         switch.helios_voiture_manual
    soc_entity:     sensor.ev_soc             # optionnel
    plugged_entity: binary_sensor.ev_branche  # optionnel — on = branché
```

---

## Simulation

Le moteur de simulation permet de tester le comportement du dispatch sur une journée complète hors de Home Assistant, et de produire les prévisions journalières.

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

# Simulation complète avec batterie et appareils personnalisés
python sim.py -v \
    --season spring \
    --peak-pv 2800 \
    --bat-soc 20 \
    --bat-capacity 4.2 \
    --bat-charge-max 1200 \
    --bat-discharge-max 1200 \
    --bat-efficiency 0.95 \
    --bat-discharge-start 6 \
    --devices custom_components/helios/simulation/config/devices.json \
    --base-load custom_components/helios/simulation/config/base_load.json
```

### Fichiers de configuration

Tous les paramètres métier sont dans `custom_components/helios/simulation/config/` :

| Fichier | Contenu |
|---------|---------|
| `devices.json` | Appareils fictifs pour la simulation sur PC — **indépendant de la config HA** (les appareils réels sont configurés via le config flow) |
| `appliance_schedule.json` | Planning de déclenchement des appareils programmables — **partagé** entre le simulateur et la prévision journalière HA |
| `base_load.json` | Consommation de fond — utilisé par la simulation **et** par l'intégration HA au démarrage (cold start EMA) ; remplacé progressivement par le profil appris |
| `tariff.json` | Tarifs EDF Tempo HC/HP — utilisé par la simulation **et** par la prévision journalière HA (calcul du coût et de l'économie estimés) |

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
| `device_type` | `"generic"` | `generic`, `water_heater`, `ev_charger`, `hvac`, `pool`, `appliance` |
| `power_w` | — | Puissance consommée (W) |
| `allowed_start` / `allowed_end` | `0.0` / `24.0` | Fenêtre horaire autorisée (heures décimales, ex. `8.0` = 8h00) |
| `priority` | `5` | Priorité de dispatch 1–10 (10 = plus prioritaire) |
| `min_on_minutes` | `0` | Durée minimale allumé avant que Helios puisse éteindre |
| `run_quota_h` | — | Quota journalier en heures — optionnel, applicable à tout type. L'appareil s'arrête une fois la durée atteinte. Pour `pool`, c'est le quota de filtration. |

**Type `pool`** — suit le quota de filtration journalier défini par `run_quota_h`.

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
- **La prévision journalière HA** — lu au démarrage de `ha_devices_to_sim()` depuis le même chemin bundlé

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

Si le fichier est absent ou illisible, la simulation et la prévision journalière continuent sans déclencher d'appareils programmables (log debug uniquement, pas d'erreur).

#### Format `base_load.json`

> **Note** — Ce fichier est utilisé par la simulation et par la prévision journalière comme profil de consommation de fond. L'apprentissage automatique du profil via EMA (exponentielle mobile) est prévu pour une version future — il permettra à Helios d'apprendre la consommation réelle de la maison sans ce fichier. Cette fonctionnalité n'est pas encore implémentée.

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
- **La prévision journalière HA** (tourne à 05:00) — pour calculer le coût et l'économie estimés

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

Les valeurs par défaut correspondent aux tarifs EDF Tempo TTC en vigueur au 03/03/2026. Les tarifs évoluent chaque année — vérifier les valeurs actuelles sur [le site EDF](https://www.edf.fr/tarif-tempo).

**Modes à venir (roadmap config flow)**

Le tarif sera à terme configurable directement dans le config flow, sans `tariff.json`. Trois modes prévus :

| Mode | Description | Format cible |
|------|-------------|--------------|
| `flat` | Tarif fixe, même prix 24h/24 | `{ "type": "flat", "price": 0.25 }` |
| `hphc` | HP/HC classique, sans Tempo | `{ "type": "hphc", "hc_start": 22.0, "hc_end": 6.0, "hc": 0.17, "hp": 0.25 }` |
| `tempo` | EDF Tempo (mode actuel via `tariff.json`) | intégré dans le config flow |

### Paramètres CLI

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
| `--bat-discharge-start H` | `6` | Heure à partir de laquelle la décharge est autorisée. En jour rouge, la batterie se charge en HC (avant cette heure) puis repasse en autoconsommation. |
| `--bat-soc-min-red PCT` | `80` | SOC min les jours Tempo rouge (%) |
| `--bat-priority N` | `7` | Priorité de la batterie dans le dispatch (1–10) |
| `--no-battery` | — | Désactiver la batterie |

La batterie fonctionne en **mode autoconsommation** à partir de `--bat-discharge-start` :
- Surplus PV → charge batterie
- Déficit → décharge batterie pour éviter l'import réseau
- Avant `bat_discharge_start` : SOC maintenu (pas de décharge nocturne) — en jour rouge, c'est la plage de charge forcée réseau

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

Cela alimente la prévision journalière qui en déduit la couverture nuageuse via `cloud_from_forecast` — un écart important entre prévision et ciel clair indique une journée nuageuse et ajuste la simulation de la journée en conséquence.

#### Dispatch

Le dispatch utilise l'algorithme unifié (phase obligatoire → phase greedy) avec les poids fixes `0.4 × priorité + 0.3 × fit + 0.3 × urgence`. Aucun paramètre configurable.

### Mode comparaison

Compare toutes les combinaisons saison × météo en une seule commande :

```bash
python sim.py --compare
```

```
  Saison    Météo               PV   Import   Export   Autocons.   Autosuff.    Coût   Économie
  winter    clear            10.6kWh  16.0kWh   0.6kWh  █████████░ 94.8%  ████░░░░░░ 38.5%   3.21€   4.87€
  winter    partly_cloudy     8.1kWh  17.3kWh   0.5kWh  █████████░ 93.8%  ███░░░░░░░ 30.6%   3.54€   4.12€
  ...
```


### Sortie verbose (`-v`)

```bash
python sim.py -v
```

```
      H       PV   Maison    Réseau   Batterie    SOC  Score  Remaining  Appareils
  08:00   1.5 kW   1.4 kW     -12 W     +104 W    52%  0.92     1320 W  Chauffe-eau
  09:00   2.1 kW   1.5 kW     -67 W     +605 W    50%  0.92      480 W  Chauffe-eau, Pompe piscine
  ...
```

- **Réseau** : `+` = import, `-` = export
- **Batterie** : `+` = charge depuis PV, `-` = décharge vers maison
- **Remaining** : budget de dispatch résiduel après allocation (`surplus_virtuel + bat_available_w − Σ alloués`)

### Log des décisions (`--decisions`)

Affiche chaque changement d'état d'appareil au pas de 5 minutes, avec le fit, l'urgence, le score effectif, le budget restant et le SOC batterie au moment de la décision.

```bash
python sim.py --decisions
python sim.py -v --decisions   # combinable avec la vue horaire
```

```
  ── Décisions appareils (5 min) ──────────────────────────────────────────────
  Heure  Appareil       Action  Eff.Score   Fit  Urgency  Remaining    SOC
  08:15  Chauffe-eau        on      0.721  0.87     0.65     1120 W    49%
  08:40  Piscine            on      0.634  0.52     0.40      500 W    49%
  12:30  Zoé                on      0.810  1.00     0.72     1980 W    52%
  18:20  Zoé               off      0.310  0.12     0.15      220 W    20%
```

Utile pour diagnostiquer un **chattering** (cycles ON/OFF rapides), vérifier qu'un appareil se déclenche dans la bonne fenêtre horaire, ou comprendre pourquoi un appareil n'a pas été sélectionné (fit trop bas, budget insuffisant).

---

## Releases

### Beta automatique

À chaque push sur `main` qui passe les tests, le workflow **Auto Beta Release** publie automatiquement une pré-release :

- Si la version courante est `1.2.3b4` → devient `1.2.3b5`
- Si la version courante est une stable `1.2.3` → devient `1.2.4b1`

La version dans `manifest.json` est mise à jour et commitée par le bot, un tag `v…` est poussé, et une GitHub Release (marquée *pre-release*) est créée avec les notes générées automatiquement.

### Release stable

1. Aller dans **Actions → Release → Run workflow**
2. Saisir le numéro de version cible (ex. `1.3.0` ou `1.3.0b2` pour forcer un beta nommé)
3. Le workflow :
   - Exécute les tests — s'arrête en cas d'échec
   - Met à jour `manifest.json`
   - Crée un commit + tag `v1.3.0`
   - Publie une GitHub Release avec les notes générées automatiquement
