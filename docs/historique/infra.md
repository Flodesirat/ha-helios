# Historique — infra

## Relance de démarrage annulée au déchargement (#5, 2026-11-10)

### Contexte

Tant que les entités sources (PV, réseau, maison) ne sont pas disponibles,
`_async_update_data` programme `_async_startup_refresh` toutes les 60 s
(`_startup_check_unsub`). `EnergyOptimizerCoordinator.async_unload`
n'annulait pas ce timer : après un déchargement ou un rechargement d'Helios
avec des sources indisponibles, la relance se déclenchait encore sur le
coordinator déchargé et pouvait lancer un cycle complet (dispatch compris)
en parallèle du nouveau coordinator.

### Décisions

- `async_unload` annule `_startup_check_unsub`, comme les autres timers du
  coordinator. Pas d'autre changement de comportement.

### Tests

- `test_init.py::test_unload_cancels_startup_refresh` : setup sans capteurs
  sources (la relance est programmée), puis unload. Le timer doit être annulé ;
  sans le correctif, le test échoue aussi sur « Lingering timer » de la
  fixture HA.

## Couverture de tests à 100 % (#8, 2026-11-11)

### Contexte

La CI exigeait 45 % de couverture ; la suite était à 82 %. Objectif : 100 %,
verrouillé par `--cov-fail-under=100`.

### Décisions

- **Seuil CI à 100 %** (`.github/workflows/tests.yml`, `CLAUDE.md`).
- **`.coveragerc`** : exclut seulement `if __name__ == "__main__":` et
  `if TYPE_CHECKING:`. Aucun `# pragma: no cover` dans le code.
- **Code mort supprimé plutôt qu'exclu** (vérifié par recherche d'appels) :
  - `simulation/devices.py` : `SimDevice.in_window / satisfied / min_on_respected / turn_off`,
    tout le proxy de `SimBatteryDevice` (le moteur n'utilise que `update`, `tick`,
    `as_battery_device`) ;
  - `simulation/engine.py` : `_SimNoOpStore.async_load`, `_SimDatetimeProxy.__call__ / fromisoformat` ;
  - `managed_device.py` : `_minutes_to_off_peak_end`, alias `BatteryDevice.manual_mode` ;
  - `coordinator.py` : `_float_or_none` ; les deux `_float` imbriqués utilisent `state_float` ;
  - `__init__.py` : repli si `manifest.json` illisible, import `slugify` inutilisé ;
  - `switch.py` : gardes `battery_device is None` (entités créées seulement avec une batterie) ;
  - `battery_drive.py` : `_async_apply_force` reçoit l'appareil forcé (les appelants le vérifient déjà) ;
  - `pool_filtration.filtration_hours` : plus de `return` inaccessible ;
  - `simulation/run.py` : sélection des lignes horaires robuste à l'arrondi flottant.
- `daily_optimizer.ha_devices_to_sim` : les heures `HH:MM:SS` (format du
  `TimeSelector`) sont lues via `_parse_time` au lieu de retomber sur la valeur
  par défaut.
- Deux bugs trouvés en écrivant les tests, traités dans leur propre ticket :
  #10 (options flow Sources) et #11 (consigne HVAC `climate`).

### Tests

Nouveaux fichiers : `test_simulation_run.py`, `test_simulation_config.py`,
`test_profiles.py`, `test_config_flow_devices.py`, `test_init_services.py`,
`test_select.py`, `test_button.py`, `test_switch.py`, `test_binary_sensor.py`,
`test_sensor_attributes.py`, `test_managed_device_misc.py`,
`test_coordinator_misc.py`, `test_device_manager_misc.py`.
Compléments dans `test_battery_drive.py` (erreurs de service, SOC inconnu,
forçage avec pilotage coupé), `test_battery_config.py`, `test_daily_optimizer.py`,
`test_diagnostics.py`. Les tests d'échéance figent l'heure (`datetime` patché)
pour ne pas dépendre de l'heure d'exécution.
