# Historique — Carte Lovelace (`helios-card`)

## Score optionnel (#9, 2026-11-10)

**Contexte.** La barre « Score » de la carte était toujours affichée, même en
mode compact ; seule sa décomposition (`sections.score_detailed`) pouvait être
masquée.

**Décisions.**
- Nouvelle option `sections.score` (défaut `true`), rangée avec les autres
  sections plutôt qu'en option de premier niveau.
- `score: false` masque la barre **et** la décomposition : la décomposition
  n'a pas de sens sans le score qu'elle détaille.
- Contrairement aux autres sections, `score` s'applique aussi en mode
  compact, où la barre reste affichée par défaut : aucun changement visible
  pour les configurations existantes.

**Tests.** Pas de tests automatisés pour le JavaScript de la carte. Deux
scénarios ajoutés à `www/test_card.html` : « Sections : sans score » et
« Compact sans score ».
