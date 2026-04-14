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

# Fixed weights — score global is a user indicator only, not configurable
_W_SURPLUS = 0.5
_W_TEMPO   = 0.3
_W_SOLAR   = 0.2


class ScoringEngine:
    """Fixed-weight scoring with fuzzy-style normalization per dimension.

    Score = 0.5·f_surplus(surplus_w) + 0.3·f_tempo(color) + 0.2·f_solar(…)

    Each f_* returns a value in [0..1]:
      - 1.0 = strongly favors turning devices ON / using energy now
      - 0.0 = strongly favors keeping devices OFF / conserving
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.soc_min         = float(config.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
        self.soc_max         = float(config.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX))
        self.peak_pv_kw      = float(config.get(CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W)) / 1000.0
        self.battery_enabled = bool(config.get(CONF_BATTERY_ENABLED, False))
        self.charge_max_w    = float(config.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 0.0))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute(self, data: dict[str, Any]) -> float:
        """Return global score in [0..1]."""
        f_surplus, f_tempo, f_solar = self.compute_components(data)
        score = _W_SURPLUS * f_surplus + _W_TEMPO * f_tempo + _W_SOLAR * f_solar
        return round(min(max(score, 0.0), 1.0), 3)

    def compute_components(self, data: dict[str, Any]) -> tuple[float, float, float]:
        """Return (f_surplus, f_tempo, f_solar) — each in [0..1]."""
        f_surplus = self._score_surplus(data.get("surplus_w", 0.0))
        f_tempo   = self._score_tempo(data.get("tempo_color"))
        f_solar   = self._score_solar(data)
        return f_surplus, f_tempo, f_solar

    # ------------------------------------------------------------------
    # Per-dimension scoring functions (fuzzy membership)
    # ------------------------------------------------------------------
    def _score_surplus(self, surplus_w: float) -> float:
        """Map PV surplus to [0..1].

        Rampe unique de 0 W à (charge_max_w + 500 W) → 0.0 à 1.0.
        Sans batterie : charge_max_w = 0 → rampe 0 → 500 W.
        """
        if surplus_w <= 0:
            return 0.0
        return min(1.0, surplus_w / max(1.0, self.charge_max_w + 500.0))

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
