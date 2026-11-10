# Historique — appareils

## Chauffe-eau : préchauffage maximal la veille d'un jour rouge (#3, 2026-11-10)

### Contexte

Un kWh en heures pleines rouges coûte environ 0,71 €, contre environ 0,16 € en
heures creuses. Chauffer le ballon au maximum pendant les heures creuses qui
précèdent un jour rouge évite de chauffer pendant les heures pleines rouges.

### Décisions

- **Pas de nouveau paramètre.** La veille d'un jour rouge, la température à
  atteindre en heures creuses est la cible de journée (`wh_temp_target`).
  *Écart avec le ticket* : le ticket prévoyait un paramètre dédié,
  `wh_temp_target_red_eve`. Il a été écarté à la relecture, la cible de
  journée suffit.
- **Même condition que la pré-charge batterie** :
  `tempo_next_color == red` et heures creuses. `DeviceManager` recopie
  l'information à chaque cycle dans `ManagedDevice.tempo_next_red`.
- La cible de journée **remplace le minimum HC** dans `_wh_off_peak_min` : la
  marche forcée (avec l'hystérésis HC), la satisfaction et l'urgence en HC
  passent par ce minimum. On prend `max(minimum HC, cible journée)` : un
  minimum HC plus haut est conservé.
- **Heures pleines inchangées** : chauffe sur surplus, plancher légionelle
  conservé.
- **Abonnement** : rien à ajouter. La pré-charge batterie
  (`precharge_setpoint`) se base sur l'import mesuré, chauffe-eau compris,
  et ne charge qu'avec la puissance restante sous `grid_subscription_w`.
- Diagnostics : `tempo_next_red` ajouté au bloc `water_heater`.
- Traductions : la description de `wh_temp_target` mentionne son usage en
  HC la veille d'un jour rouge.
- Simulation : non modifiée (elle ne fournit pas `tempo_next_color`).

### Tests ajoutés

- `test_water_heater_red_eve.py` :
  - marche forcée jusqu'à la cible de journée ;
  - arrêt une fois la cible atteinte ;
  - lendemain bleu, blanc ou inconnu : rien ne change ;
  - minimum statique sans entité ;
  - un minimum HC plus haut est conservé ;
  - heures pleines identiques, plancher légionelle conservé ;
  - dispatch de bout en bout (23 h et 4 h) ;
  - pré-charge sous l'abonnement avec le chauffe-eau en marche.

## Piscine : loi de filtration, hivernage, hors-gel, jours rouges (#1, 2026-11-10)

### Contexte

La durée de filtration venait d'une entité externe (heures). Le hors-gel était
assuré par un service externe (sessions de 15 min). Rien n'empêchait Helios de
filtrer pendant les heures pleines d'un jour rouge.

### Décisions

- **Entrées** : sonde d'eau et sonde du local technique, toutes deux
  obligatoires dans le formulaire. `pool_filtration_entity` est supprimé, sans
  repli : une piscine configurée avant la 1.2 est en erreur jusqu'à sa
  reconfiguration (pas de migration possible : on ne peut pas deviner la
  sonde). L'options flow supprime l'ancienne clé et `pool_split_sessions` à
  l'enregistrement.
- **Erreurs** (`pool_errors`, journal HA à l'apparition, carte) :
  - pas de quota du jour (sonde d'eau absente, ou indisponible depuis
    minuit) → « impossible de piloter la pompe ». Helios ne touche plus à la
    pompe (`_helios_manages` faux) ; hors-gel et forçage manuel restent actifs,
    et le hors-gel éteint lui-même la pompe en fin de session. Une sonde en
    panne après la lecture de minuit n'est pas une erreur : le quota est fixé ;
  - sonde du local absente ou indisponible → « impossible de gérer le mode
    hors-gel ».
- **Loi** : texte `temp:heures`, interpolation linéaire, bornée aux extrémités.
  Logique pure dans `pool_filtration.py`, sans HA. Formulaire refusé si la
  table est invalide ; une table invalide en config retombe sur la table par
  défaut, avec un avertissement.
- **Journée** : le compteur repart à minuit (premier cycle après le
  changement de date, inchangé) ; la sonde d'eau est lue au même moment
  (05:00 auparavant), quota figé jusqu'au minuit suivant. Le hors-gel qui
  tourne vers 0 h compte dans la nouvelle journée.
- **Hivernage automatique** (option) : décidé à la même lecture de 05:00.
  Entrée sous le seuil, sortie à seuil + 2 °C (hystérésis fixe), état
  persisté (`pool_wintering` dans le store). En hivernage, quota fixe
  (2 h par défaut), pas de plage horaire (`is_in_allowed_window` vrai),
  échéance à minuit. Placement : surplus solaire d'abord ; marche forcée
  seulement en heures creuses, quand le temps d'HC restant avant minuit
  (`_off_peak_minutes_until_midnight`) ne dépasse plus le déficit (HC 22h–6h,
  2 h manquantes → 22h–24h). Jamais forcée en heures pleines (urgence plafonnée
  à 0,99). Sans créneau HC configuré, urgence classique jusqu'à minuit.
- **Hors-gel** : sessions des N premières minutes de chaque heure sous
  `pool_frost_temp`, continu sous `pool_frost_continuous_temp`, sortie à
  seuil + 1 °C. Passe dédiée dans `async_dispatch`, avant le dispatch :
  prioritaire sur la plage horaire, le rouge et le quota. Le temps compte dans
  la filtration du jour et peut dépasser le quota (demande de l'utilisateur).
  Le mode manuel et le forçage ON/OFF priment. Sonde du local indisponible :
  le niveau courant est conservé.
- **Fin de session** : la pompe est rendue au dispatch avec `turned_on_at`
  remis à zéro. Le dispatch la garde (surplus, urgence) ou l'éteint, sans
  attendre `min_on_minutes` ni faire de coupure-rallumage.
- **Jours rouges** : en HP rouge (hors créneaux `off_peak_*`), la piscine
  n'est plus candidate. La Phase 4 l'éteint avec la raison `red_peak`. Déficit
  accepté.
- **Mode affiché** (`pool_mode`) : `manual` (mode manuel ou forçage ON/OFF en
  cours) > `frost` (pompe en marche pour le hors-gel) > `wintering` > `auto`.
  La carte affiche le mode (❄️ en hors-gel), la température de l'eau et celle
  du local.
- Entités : `binary_sensor.helios_<piscine>_wintering`,
  `binary_sensor.helios_<piscine>_frost_protection`.
- L'optimiseur journalier calcule le quota de la piscine avec la même règle
  (`_pool_required_minutes_live`). Simulation : inchangée (quota pré-chargé).
- Supprimés : `pool_split_sessions` (jamais utilisé), `_POOL_MUST_RUN_WINDOW_H`.
- Version : 1.2.0b0 (nouveaux paramètres et entités).

### Tests ajoutés

- `test_pool_filtration.py` :
  - table : parsing, tolérances, rejets, interpolation, bornes ;
  - hivernage et hors-gel : seuils et hystérésis, sessions, marche continue ;
  - quota à minuit : sonde d'eau, lecture à 0 h, sonde indisponible,
    quota figé, hivernage, sortie avec hystérésis, option désactivée,
    ancienne entité ignorée, table invalide ; remise à zéro à minuit avec le
    hors-gel compté dans la nouvelle journée ;
  - placement en hivernage : minutes d'HC avant minuit, marche forcée en HC
    au bon moment, attente du solaire le matin, jamais en HP, pas de plage
    horaire, sans créneau HC, dispatch (surplus hors plage, HP sans surplus,
    rattrapage en HC un jour rouge) ;
  - erreurs : sonde d'eau absente, panne après la lecture de minuit (pas
    d'erreur), sonde du local absente ou indisponible, pompe laissée telle
    quelle sans quota, hors-gel sans quota, journalisation unique ;
  - mode affiché et priorités ;
  - dispatch hors-gel : rouge + hors plage + quota dépassé, temps compté au-delà
    du quota, fin de session sans `min_on`, reprise par la filtration, pas
    d'effet en manuel ni en arrêt forcé, sonde indisponible, reprise d'une
    pompe déjà en filtration (mode hors-gel, sans appel au switch), forçage ON
    prioritaire, enchaînement des niveaux sur plusieurs cycles (sessions →
    continu → sessions → arrêt) ;
  - jours rouges : pas d'allumage malgré l'urgence, extinction en HP, HC
    normale, jour bleu inchangé ;
  - persistance de l'hivernage ; validation du formulaire.
- Tests piscine existants : passés de l'ancienne entité à la sonde d'eau
  (`test_pool_force.py`, `test_pool_persistence.py`, `test_regression.py`,
  `test_actual_power.py`).
