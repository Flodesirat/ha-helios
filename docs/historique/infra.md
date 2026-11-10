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
