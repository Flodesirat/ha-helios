# Index des lots

Chaque lot livré est consigné à la fin du fichier de son domaine dans
`docs/historique/` (décisions, tests ajoutés). Ce fichier en est l'index.

| Lot | Date | Domaine | Ticket | Résumé | Où |
|-----|------|---------|--------|--------|----|
| 1–10 | 2026-04-14 → 15 | dispatch | — | Refonte du dispatch : `BatteryDevice`, fit, dispatch unifié, prévision journalière, carte | [plan.md](../plan.md) |
| Drive | 2026-11-10 | batterie | #2 | Régulation en mode Drive, multi-batteries, pré-charge avant jour rouge, `switch.helios_battery_driving` | [historique/batterie.md](historique/batterie.md) |
| Forçage | 2026-11-10 | batterie | #6 | Forçage de la batterie : charge / décharge, puissance, durée, depuis la carte | [historique/batterie.md](historique/batterie.md) |
| Pas des prix | 2026-11-10 | config-flow | #4 | Formulaire Stratégie : pas des prix Tempo `0.0001` → `"any"` (refusé par HA) | [historique/config-flow.md](historique/config-flow.md) |
| Relance de démarrage | 2026-11-10 | infra | #5 | Le timer de relance (sources indisponibles) est annulé au déchargement du coordinator | [historique/infra.md](historique/infra.md) |
| Veille de rouge | 2026-11-10 | appareils | #3 | Chauffe-eau : en HC, chauffe jusqu'à la cible de journée quand demain est rouge | [historique/appareils.md](historique/appareils.md) |
| Piscine | 2026-11-10 | appareils | #1 | Loi de filtration selon la température de l'eau, hivernage automatique, hors-gel, pas de filtration en HP rouge, mode et températures dans la carte | [historique/appareils.md](historique/appareils.md) |
| Score optionnel | 2026-11-10 | carte | #9 | Option `sections.score` : masque la barre de score et sa décomposition, aussi en mode compact | [historique/carte.md](historique/carte.md) |

## Consigner un nouveau lot

1. Ajouter une section `## <titre du lot> (#<ticket>, <date>)` à la fin de
   `docs/historique/<domaine>.md` (créer le fichier s'il n'existe pas) :
   contexte, décisions prises et pourquoi, paramètres ou entités ajoutés,
   tests ajoutés.
2. Ajouter une ligne au tableau ci-dessus.
