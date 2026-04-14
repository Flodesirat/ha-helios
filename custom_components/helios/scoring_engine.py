"""Scoring engine — computes a normalized [0..1] optimization score."""
from __future__ import annotations

from typing import Any

from .const import (
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_ENABLED, CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_PEAK_PV_W,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    DEFAULT_PEAK_PV_W,
    TEMPO_BLUE, TEMPO_WHITE, TEMPO_RED,
    normalize_tempo_color,
)


class ScoringEngine:
    """Weighted scoring with fuzzy-style normalization per dimension.

    Score = w1·f_surplus(surplus_w) + w2·f_tempo(color) + w3·f_soc(soc) + w4·f_solar(…)

    Each f_* returns a value in [0..1]:
      - 1.0 = strongly favors turning devices ON / using energy now
      - 0.0 = strongly favors keeping devices OFF / conserving
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.w_surplus       = float(config.get("weight_pv_surplus",  0.4))
        self.w_tempo         = float(config.get("weight_tempo",        0.3))
        self.w_soc           = float(config.get("weight_battery_soc",  0.2))
        self.w_solar         = float(config.get("weight_solar",        0.1))
        self.soc_min         = float(config.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
        self.soc_max         = float(config.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX))
        self.peak_pv_kw      = float(config.get(CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W)) / 1000.0
        self.battery_enabled = bool(config.get(CONF_BATTERY_ENABLED, False))
        self.charge_max_w    = float(config.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 0.0))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def update_weights(self, scoring: dict[str, Any]) -> None:
        """Apply new scoring weights (from daily optimizer)."""
        self.w_surplus  = scoring.get("weight_pv_surplus",  self.w_surplus)
        self.w_tempo    = scoring.get("weight_tempo",        self.w_tempo)
        self.w_soc      = scoring.get("weight_battery_soc",  self.w_soc)
        self.w_solar    = scoring.get("weight_solar",        self.w_solar)

    def get_weights(self) -> dict[str, float]:
        """Return current scoring weights (for persistence)."""
        return {
            "weight_pv_surplus":  self.w_surplus,
            "weight_tempo":       self.w_tempo,
            "weight_battery_soc": self.w_soc,
            "weight_solar":       self.w_solar,
        }

    def compute(self, data: dict[str, Any]) -> float:
        """Return global score in [0..1]."""
        s_surplus = self._score_surplus(data.get("surplus_w", 0.0), data.get("battery_soc"))
        s_tempo   = self._score_tempo(data.get("tempo_color"))
        s_soc     = self._score_soc(data.get("battery_soc"))
        s_solar   = self._score_solar(data)

        score = (
            self.w_surplus * s_surplus
            + self.w_tempo   * s_tempo
            + self.w_soc     * s_soc
            + self.w_solar   * s_solar
        )
        return round(min(max(score, 0.0), 1.0), 3)

    # ------------------------------------------------------------------
    # Per-dimension scoring functions (fuzzy membership)
    # ------------------------------------------------------------------
    def _score_surplus(self, surplus_w: float, battery_soc: float | None) -> float:
        """Map PV surplus to [0..1].

        Cas A — pas de batterie OU SoC > soc_max :
            Rampe linéaire unique : 0 W → 0.0, 500 W → 1.0.

        Cas B — batterie active ET SoC ≤ soc_max :
            Double pente avec charge_max_w comme seuil de rupture.
            - [0, charge_max_w]          → rampe douce  0.0 → 0.3
            - [charge_max_w, +500 W]     → rampe rapide 0.3 → 1.0
            La batterie peut absorber jusqu'à charge_max_w sans urgence ;
            au-delà, l'énergie risque de partir sur le réseau.
        """
        if surplus_w <= 0:
            return 0.0

        battery_full = (
            not self.battery_enabled
            or battery_soc is None
            or battery_soc >= self.soc_max
        )

        if battery_full or self.charge_max_w <= 0:
            # Cas A : rampe unique
            return min(1.0, surplus_w / 500.0)

        # Cas B : double pente
        if surplus_w <= self.charge_max_w:
            return 0.3 * (surplus_w / self.charge_max_w)
        excess = surplus_w - self.charge_max_w
        return min(1.0, 0.3 + 0.7 * (excess / 500.0))

    def _score_tempo(self, color: str | None) -> float:
        """Map Tempo color to [0..1].
        Blue (cheap) → 1.0, White → 0.5, Red (expensive) → 0.0.
        None (no Tempo) → neutral 0.5.
        """
        mapping = {TEMPO_BLUE: 1.0, TEMPO_WHITE: 0.5, TEMPO_RED: 0.0}
        return mapping.get(normalize_tempo_color(color) or "", 0.5)

    def _score_soc(self, soc: float | None) -> float:
        """Map battery SOC to [0..1] using configured soc_min / soc_max.

        Réserve  (0 → soc_min)         → 0.0   dispatch bloqué
        Basse    (soc_min → pivot)      → 0.0 → 0.3   rampe plate (score bas)
        Confort  (pivot   → soc_max)    → 0.3 → 1.0   rampe forte (encourage consommation)
        Pleine   (≥ soc_max)            → 1.0

        pivot = (soc_min + soc_max) / 2 — le score reste bas tant que la batterie
        n'a pas atteint le pivot, puis monte fortement vers soc_max.
        None → neutre 0.5.
        """
        if soc is None:
            return 0.5
        if soc <= self.soc_min:
            return 0.0
        pivot = (self.soc_min + self.soc_max) / 2.0
        if soc <= pivot:
            return 0.3 * (soc - self.soc_min) / (pivot - self.soc_min)
        if soc <= self.soc_max:
            return 0.3 + 0.7 * (soc - pivot) / (self.soc_max - pivot)
        return 1.0

    def _score_solar(self, data: dict[str, Any]) -> float:
        """Solar potential based on sun elevation.

        f = max(0, sin(elevation_rad))

        At solar noon (maximum elevation) → ~1.0
        At sunrise/sunset (elevation = 0°) → 0.0
        At night (elevation < 0°) → 0.0

        In real HA: elevation comes from sun.sun entity attribute.
        In simulation: synthetic elevation derived from seasonal profile.
        Falls back to a fixed Gaussian (σ=3h, peak 13h) when elevation
        is not available.
        """
        import math
        elevation = data.get("solar_elevation")
        if elevation is not None:
            return round(max(0.0, math.sin(math.radians(float(elevation)))), 3)
        # Fallback: fixed Gaussian centred at 13h, σ=3h
        hour = float(data.get("hour", 13))
        return round(math.exp(-((hour - 13.0) ** 2) / 18.0), 3)
