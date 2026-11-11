# Historique — batterie

## Régulation en mode Drive, multi-batteries, pré-charge avant jour rouge (#2, 2026-11-10)

### Contexte

Jusqu'à 1.0, Helios basculait entre deux scripts utilisateur (`forced_charge` /
`autoconsommation`) et laissait le BMS gérer la puissance. Le script
`tools/ha_drive.py` du projet marstek-smartmeter régulait déjà une Venus A en
mode Drive via ha-marstek-local-api. Ce lot intègre cette régulation dans
Helios, pour une ou plusieurs batteries, sans dépendre de la marque.

### Décisions

- **Boucle séparée** du cycle du coordinator (minute) : déclenchée à chaque
  relevé du capteur réseau. Écoute de `state_changed` **et** `state_reported`,
  car un relevé de même valeur ne change que `last_reported`.
- **`PassiveRegulator` repris tel quel** (`battery_regulator.py`) :
  correction immédiate et complète, base = consigne appliquée au moment du
  relevé. **Settle fixé à 3 s.**
- **Pas de modèle `_Sent`** : le seuil de 30 W et le rafraîchissement de 60 s
  restent dans l'intégration de la batterie ; Helios compte la valeur écrite
  comme appliquée. Validé par le rejeu du 16/10 (voir Tests).
- **Changement de mode par scripts** (Drive / neutre, par batterie) : Helios
  n'écrit que l'entité `number` de consigne → compatible d'autres batteries.
  Script Drive rappelé toutes les heures, suivi d'une réécriture de la consigne.
- **Consigne perdue** : relecture des `number` toutes les 60 s, deux lectures
  discordantes de suite → reprise depuis 0 W et nouvelle attente de 15 s.
- **Cibles réseau par couleur Tempo du jour** (30 W par défaut).
- **Pas de décharge en heures creuses** (première version).
- **SOC** : décharge interdite à `battery_soc_min` ; charge jusqu'à 100 %. Le
  SOC de réserve jour rouge reste réservé au dispatch des appareils.
- **Pré-charge** : `tempo_next_color == red` et heures creuses → charge à
  pleine puissance jusqu'à 100 %, plafonnée pour que l'import ne dépasse pas
  `grid_subscription_w`. On reste en Drive.
- **Plusieurs batteries** : consigne / N, chaque part bornée ; la part qu'une
  batterie ne peut pas prendre est redistribuée aux autres. *Écart avec le
  ticket* : une division simple par N ne converge pas (une batterie vide fige
  la base à la part de l'autre) — d'où la redistribution, identique à / N
  quand toutes les batteries suivent.
- **`switch.helios_battery_driving`** remplace `battery_manual` : persisté
  (`__battery__.driving`), un ancien `manual_mode = True` devient OFF. OFF ou
  Helios coupé → script neutre, `bat_available_w = 0`, batterie hors dispatch.
- **Migration** config entry v1 → v2 : la batterie à plat devient une liste
  d'une batterie ; `battery_charge_script` / `battery_autoconsum_script`
  supprimés. `coordinator.config` expose les totaux (capacité, puissances) dans
  les clés à plat pour le dispatch, le scoring et l'optimiseur ; le SOC global
  est pondéré par les capacités.

### Paramètres et entités ajoutés

- Communs : `battery_grid_target_blue_w`, `battery_grid_target_white_w`,
  `battery_grid_target_red_w`.
- Par batterie (`batteries`) : `battery_name`, `battery_soc_entity`,
  `battery_power_entity`, `battery_drive_power_entity`, `battery_drive_script`,
  `battery_neutral_script`, `battery_capacity_kwh`,
  `battery_max_charge_power_w`, `battery_max_discharge_power_w`.
- `switch.helios_battery_driving` ; `sensor.helios_battery` : état = état de la
  régulation, attributs `setpoint_w`, `target_w`, `last_grid_w`, `batteries`.
- Carte : modale batterie avec bouton de pilotage et section Régulation.

### Tests ajoutés

- `test_battery_regulator.py` : formule, settle, bornes, répartition et
  convergence avec une batterie vide, pré-charge plafonnée.
- `test_battery_config.py` : liste, totaux, SOC pondéré, migration v1 → v2.
- `test_battery_drive.py` : activation / désactivation, relevé de même valeur,
  attente de 15 s, cibles Tempo, deux batteries, pas de décharge en HC,
  pré-charge, consigne perdue (une lecture ne suffit pas, deux oui), rappel
  horaire.
- `test_battery_setup.py` : de bout en bout dans HA (migration au chargement,
  cycle Drive, Helios coupé, retrait de l'ancien switch).
- `test_battery_driving_persistence.py`, `test_config_flow_battery.py`.
- `test_battery_replay.py` : rejeu du 16/10 à travers le contrôleur, batterie
  simulée appliquant la règle de l'intégration (30 W / 60 s). Résultat :
  **539 Wh d'import, 120 Wh d'export**, contre 540 / 119 pour la référence qui
  connaît exactement ce qui est envoyé. Seuil : import ≤ référence + 5 %,
  export ≤ référence + 10 %. Un régulateur dégradé (gain 0,5) donne
  638 / 222 Wh et fait échouer le test.

### Remarques

- Le formulaire Stratégie utilise `step=0.0001` pour les prix, refusé par HA
  2025.1 (minimum 0,001) : défaut antérieur, non traité ici.
- `async_unload` n'annule pas la relance de démarrage du coordinator
  (`_startup_check_unsub`) : défaut antérieur, non traité ici.

## Mode forçage : charge / décharge, puissance, durée (#6, 2026-11-10)

### Contexte

Les appareils ont un mode forçage (switch + durée) ; la batterie pilotée en
Drive n'en avait pas. On veut forcer une charge ou une décharge à une
puissance et pour une durée choisies, depuis la carte.

### Décisions

- **Entités** : `switch.helios_battery_force`,
  `select.helios_battery_force_direction`, `number.helios_battery_force_power`
  (nouvelle plateforme `number`), `select.helios_battery_force_duration`
  (mêmes durées que les appareils). Réglages et échéance sauvegardés dans
  `__battery__` du store et restaurés au redémarrage.
- **Consigne fixe**, répartie comme la régulation ; puissance par défaut =
  puissance max du sens. Un changement de sens ou de puissance s'applique tout
  de suite.
- **Limites : SOC seulement** (charge jusqu'à 100 %, décharge jusqu'à
  `battery_soc_min`). Ni plafond de puissance souscrite, ni interdiction de
  décharger en heures creuses.
- **Limite de SOC atteinte** → fin du forçage, reprise de la régulation depuis
  la puissance du forçage (sans à-coup). Idem à l'expiration (détectée au
  relevé suivant ou à la vérification de 60 s) et à l'arrêt manuel, sans
  inhibition.
- **Pilotage OFF** : le forçage utilise Drive pour sa durée (script Drive puis
  neutre), `battery_driving` reste OFF.
- Priorité sur la pré-charge ; Helios coupé → forçage annulé ; batterie hors
  dispatch pendant le forçage (`bat_available_w = 0`, pas de demande).
- Consigne perdue pendant un forçage → la consigne forcée est réécrite tout de
  suite.
- **Carte** : section Forçage dans la modale batterie — sens, puissance par
  boutons (±50 W, 25 / 50 / 75 / 100 % du max ; pas de champ de saisie, la
  modale est reconstruite à chaque mise à jour), durées, bouton
  « Forcer maintenant » / « Arrêter le forçage ».

### Tests ajoutés

- `test_battery_drive.py::TestForce` : écriture immédiate, puissance par
  défaut, répartition, pas de régulation pendant le forçage, décharge forcée en
  HC, pas de plafond d'abonnement, priorité sur la pré-charge, fin à la limite
  de SOC (reprise sans à-coup), arrêt manuel, expiration, pilotage OFF (Drive
  puis neutre), Helios coupé, changement de puissance, consigne perdue.
- `test_battery_setup.py::test_force_through_entities` : de bout en bout par
  les entités.
- `test_battery_driving_persistence.py` : sauvegarde / restauration du
  forçage, forçage expiré non restauré.
- `test_bat_available.py::test_forced_returns_zero`.

## Économies et charge depuis le réseau (#12, 2026-11-11)

### Contexte

Une charge de la batterie depuis le réseau (forçage, pré-charge de nuit) ne
faisait pas bouger les compteurs d'économies, alors que la décharge était
ensuite comptée comme économie. L'électricité achetée ressortait comme
économisée. Cause : `max(0, maison − import)` dans
`coordinator._async_sample_sensors`. `house_power` n'inclut pas la charge
batterie, donc `maison − import < 0` pendant une charge réseau, et le plancher
annulait l'incrément.

### Décisions

- **Économie = coût sans installation − coût réel**, soit
  `(maison − import) × prix du moment`, sans plancher à 0. C'est la définition
  qu'utilisait déjà la simulation (`cost_no_pv_eur − cost_eur`).
- Charge réseau → les compteurs baissent au tarif du moment (HC) ; décharge →
  ils remontent au tarif du moment (HP). Il reste le gain d'arbitrage moins les
  pertes de rendement. La charge depuis le surplus PV reste neutre.
- L'export n'est toujours pas valorisé.
- L'économie d'une journée peut être négative. Les capteurs sont en
  `state_class = TOTAL`, qui accepte les baisses : pas de changement d'entité.

### Tests ajoutés

- `test_coordinator_misc.py` : charge réseau (compteurs en baisse), décharge
  (hausse), export (seule la maison compte), cycle charge HC / décharge HP
  (solde = gain d'arbitrage).

## Réserve du matin (#14, 2026-11-11)

### Contexte

La régulation Drive interdisait toute décharge en heures creuses (règle
« première version » du lot Drive). Une batterie pleine le soir le restait
jusqu'au matin : la nuit était couverte par le réseau et, au lever du jour, la
batterie ne pouvait pas absorber la production solaire.

### Décisions

- Nouveau paramètre global `battery_morning_reserve_soc` (« Réserve du
  matin », %), étape Battery et options flow. Il arbitre entre deux
  objectifs : garder de l'énergie pour les premières heures pleines (un kWh
  restitué en HP vaut plus qu'en HC) et vider la batterie pour le solaire.
- En heures creuses, la régulation décharge jusqu'à
  `max(battery_soc_min, réserve)` ; au-dessous, consigne ≤ 0 (la charge sur
  export reste possible). En heures pleines, seul `battery_soc_min` compte.
- **Défaut 100 %** : comportement inchangé pour les installations existantes.
- Batterie sans entité SOC : pas de décharge en heures creuses, la réserve ne
  pouvant pas être vérifiée (comme avant).
- Pré-charge avant jour rouge prioritaire (pas de décharge) ; forçage
  inchangé (limites SOC globales seulement).
- Le dispatch des appareils (`bat_available_w`) n'est pas modifié.
- Exposé dans les diagnostics (`battery.morning_reserve_soc`).
- Pistes non retenues pour ce lot : réserve selon la couleur Tempo du
  lendemain, réserve calculée depuis la prévision PV et la consommation du
  matin.

### Tests ajoutés

- `test_battery_drive.py::TestMorningReserve` : décharge au-dessus de la
  réserve, arrêt à la réserve (charge sur export conservée), réserve consommée
  en HP, réserve < SOC min, batterie sans entité SOC, pré-charge prioritaire.
- `test_battery_replay.py` : rejeu du 16/10 en heures creuses — SOC au-dessus
  de la réserve (import / export identiques au rejeu de jour), SOC à la
  réserve (aucune consigne de décharge).
- `test_diagnostics.py` : clé `morning_reserve_soc`.
