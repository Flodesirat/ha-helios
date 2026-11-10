# Historique — config flow

## Formulaire Stratégie : pas des prix refusé par HA (#4, 2026-11-10)

### Contexte

Les six prix Tempo du formulaire Stratégie utilisaient
`NumberSelectorConfig(step=0.0001)`. Home Assistant refuse un pas inférieur à
0,001 (`vol.Range(min=1e-3)` dans `helpers/selector.py`) : `_strategy_schema()`
levait `MultipleInvalid` et le formulaire ne s'affichait ni dans le config
flow ni dans l'options flow.

### Décisions

- **`step="any"`** plutôt que `0.001` : les tarifs Tempo sont publiés au
  dix-millième d'euro (ex. 0,1296 €/kWh). Avec un pas de 0,001, le
  frontend refuserait ces valeurs ; `"any"` les garde telles quelles.
- Bornes inchangées (0,01 à 5 €/kWh), pas de migration : les valeurs
  enregistrées restent valides.

### Tests

- `test_config_flow_battery.py::test_config_flow_two_batteries` parcourt
  maintenant le config flow jusqu'à `CREATE_ENTRY` et vérifie qu'un prix à
  quatre décimales est conservé.
- `test_options_strategy_form_renders` : la section Stratégie de l'options
  flow s'affiche et enregistre un prix à quatre décimales.

## Options flow Sources : entités facultatives vides (#10, 2026-11-11)

### Contexte

La section Sources de l'options flow pré-remplissait les entités facultatives
non configurées avec `default=""`, refusé par l'`EntitySelector` : impossible
d'enregistrer sans tout renseigner. Trouvé en écrivant les tests de #8.

### Décisions

- Pré-remplissage par `suggested_value` (plus de `default`).
- Un champ facultatif laissé vide est enregistré à `None` dans les options :
  la source est retirée, même si elle venait de `entry.data` (les options
  priment à la lecture). Les consommateurs testent déjà une valeur vide.

### Tests

- `test_config_flow_devices.py` : `test_options_sources_saves_with_unset_optional_entities`,
  `test_options_sources_empty_field_removes_source`, `test_options_sources_prefilled_and_merged`.
