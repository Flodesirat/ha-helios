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
`decide() → "forced_charge" | "autoconsommation"` — appelle un script utilisateur au changement d'état.
`forced_charge` : `tempo_next_color == red` et heures creuses. Sinon `autoconsommation`.
(Évolution prévue : régulation en mode Drive, multi-batteries — voir ticket dédié.)

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


## Façon de travailler

0. **Quand on te demande de faire une tâche**, avant tout développement :
   1. Vérifier qu'il n'existe pas de tâche similaire (`gh issue list`, y
      compris les tickets fermés si pertinent :
      `gh issue list --state all --search "<mots-clés>"`).
   2. **Pas de tâche similaire** : créer le ticket GitHub avec le label de
      son domaine (`gh label list`) : `batterie`, `appareils`, `dispatch`,
      `config-flow`, `carte`, `simulation`, `infra`. C'est le même découpage
      que les fichiers de `docs/historique/`. Ajouter `enhancement` ou `bug`.
   3. **Tâche similaire trouvée** : en informer l'utilisateur, charger le
      contexte (`gh issue view <n>`, lot correspondant dans
      `docs/historique/`, repéré via l'index `docs/historique-lots.md`) et,
      si nécessaire, mettre à jour le ticket (description, périmètre,
      critères).
1. **Lire le ticket GitHub** de la tâche (`gh issue view <n>`) et, si le sujet
   touche un lot déjà livré, le lot correspondant dans `docs/historique/`
   (lire la section, pas le fichier entier) — décisions déjà prises. Les
   lots 1 à 10 de la refonte du dispatch (avril 2026) sont dans `plan.md`.
   `README.md` est la spécification fonctionnelle.
2. Une tâche = un ticket = un lot cohérent.
3. Suivre les conventions déjà en place (section « Conventions » ci-dessus)
   plutôt que d'en introduire de nouvelles.
4. **Toute amélioration ou changement doit être testé** :
   - tests unitaires dans `tests/` (pytest, `asyncio_mode = auto`) ;
   - pour la régulation et le dispatch, un test qui rejoue une journée
     enregistrée (ex. `tests/data/`, simulation dans `simulation/`) ;
   - commande : `python -m pytest tests/ -q` — tout doit être vert, la CI
     exige une couverture ≥ 45 %.
5. **Documenter** : consigner le lot (décisions, tests ajoutés) à la fin du
   fichier de domaine de `docs/historique/` (`batterie.md`, `appareils.md`…)
   et l'ajouter à l'index `docs/historique-lots.md`. Mettre à jour
   `README.md` si le fonctionnel change (paramètres, entités, comportement).
6. **Tickets GitHub** : en terminant une tâche, fermer son ticket
   (`gh issue close <n>`).
7. Commits en français, à l'impératif, un commit par lot, avec le trailer
   `Co-Authored-By` attendu. Mentionner le ticket (`Ferme #<n>`).

### Versioning

- `manifest.json` porte la version. À chaque push sur `main` avec tests
  verts, la CI (`auto-beta.yml`) publie une bêta : `X.Y.ZbN` → `X.Y.Zb(N+1)`,
  et après une version stable `X.Y.Z` → `X.Y.(Z+1)b1`.
- **Nouvelle fonctionnalité** (nouveaux paramètres, nouvelles entités,
  migration de config) : incrémenter le numéro intermédiaire. Dans le
  premier commit du lot, passer `manifest.json` à `X.(Y+1).0b0` pour que la
  CI publie `X.(Y+1).0b1`.
- **Correctif** : laisser la CI incrémenter le patch.
- La version stable est publiée à la main par `release.yml`
  (workflow_dispatch).


## TODO restant

- `config_flow.py` — pré-remplir l'options flow devices avec les valeurs courantes
- Les TODO plus anciens de `ISSUES.md` sont à migrer en tickets GitHub.
