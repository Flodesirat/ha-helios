# Index des lots

Chaque lot livré est consigné à la fin du fichier de son domaine dans
`docs/historique/` (décisions, tests ajoutés). Ce fichier en est l'index.

| Lot | Date | Domaine | Ticket | Résumé | Où |
|-----|------|---------|--------|--------|----|
| 1–10 | 2026-04-14 → 15 | dispatch | — | Refonte du dispatch : `BatteryDevice`, fit, dispatch unifié, prévision journalière, carte | [plan.md](../plan.md) |
| Drive | 2026-11-10 | batterie | #2 | Régulation en mode Drive, multi-batteries, pré-charge avant jour rouge, `switch.helios_battery_driving` | [historique/batterie.md](historique/batterie.md) |

## Consigner un nouveau lot

1. Ajouter une section `## <titre du lot> (#<ticket>, <date>)` à la fin de
   `docs/historique/<domaine>.md` (créer le fichier s'il n'existe pas) :
   contexte, décisions prises et pourquoi, paramètres ou entités ajoutés,
   tests ajoutés.
2. Ajouter une ligne au tableau ci-dessus.
