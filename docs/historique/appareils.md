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
