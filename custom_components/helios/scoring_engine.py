"""Scoring engine — computes a normalized [0..1] optimization score."""
from __future__ import annotations

from typing import Any

from .const import (
    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_FORECAST,
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    CONF_PEAK_PV_W,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_FORECAST,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
    DEFAULT_PEAK_PV_W,
    TEMPO_BLUE, TEMPO_WHITE, TEMPO_RED,
    normalize_tempo_color,
)


class ScoringEngine:
    """Weighted scoring with fuzzy-style normalization per dimension.

    Score = w1·f_surplus(surplus_w) + w2·f_tempo(color) + w3·f_soc(soc) + w4·f_forecast(…)

    Each f_* returns a value in [0..1]:
      - 1.0 = strongly favors turning devices ON / using energy now
      - 0.0 = strongly favors keeping devices OFF / conserving
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.w_surplus  = config.get(CONF_WEIGHT_PV_SURPLUS,  DEFAULT_WEIGHT_PV_SURPLUS)
        self.w_tempo    = config.get(CONF_WEIGHT_TEMPO,        DEFAULT_WEIGHT_TEMPO)
        self.w_soc      = config.get(CONF_WEIGHT_BATTERY_SOC,  DEFAULT_WEIGHT_BATTERY_SOC)
        self.w_forecast = config.get(CONF_WEIGHT_FORECAST,     DEFAULT_WEIGHT_FORECAST)
        self.soc_min    = float(config.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
        self.soc_max    = float(config.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX))
        self.peak_pv_kw = float(config.get(CONF_PEAK_PV_W, DEFAULT_PEAK_PV_W)) / 1000.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def update_weights(self, scoring: dict[str, Any]) -> None:
        """Apply new scoring weights (from daily optimizer)."""
        self.w_surplus  = scoring.get("weight_pv_surplus",  self.w_surplus)
        self.w_tempo    = scoring.get("weight_tempo",        self.w_tempo)
        self.w_soc      = scoring.get("weight_battery_soc",  self.w_soc)
        self.w_forecast = scoring.get("weight_forecast",     self.w_forecast)

    def get_weights(self) -> dict[str, float]:
        """Return current scoring weights (for persistence)."""
        return {
            "weight_pv_surplus":  self.w_surplus,
            "weight_tempo":       self.w_tempo,
            "weight_battery_soc": self.w_soc,
            "weight_forecast":    self.w_forecast,
        }

    def compute(self, data: dict[str, Any]) -> float:
        """Return global score in [0..1]."""
        s_surplus  = self._score_surplus(data.get("surplus_w", 0.0))
        s_tempo    = self._score_tempo(data.get("tempo_color"))
        s_soc      = self._score_soc(data.get("battery_soc"))
        s_forecast = self._score_forecast(data)

        score = (
            self.w_surplus  * s_surplus
            + self.w_tempo    * s_tempo
            + self.w_soc      * s_soc
            + self.w_forecast * s_forecast
        )
        return round(min(max(score, 0.0), 1.0), 3)

    # ------------------------------------------------------------------
    # Per-dimension scoring functions (fuzzy membership)
    # ------------------------------------------------------------------
    def _score_surplus(self, surplus_w: float) -> float:
        """Map PV surplus to [0..1].
        Trapezoid: ≤0 W → 0.0, ramp 0–500 W, plateau ≥500 W → 1.0.
        """
        if surplus_w <= 0:
            return 0.0
        if surplus_w >= 500:
            return 1.0
        return surplus_w / 500.0

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
        Basse    (soc_min → pivot)      → 0.0 → 0.6   rampe forte
        Confort  (pivot   → soc_max)    → 0.6 → 1.0   rampe plate
        Pleine   (≥ soc_max)            → 1.0

        pivot = (soc_min + soc_max) / 2  — garantit des pentes de largeur égale.
        None → neutre 0.5.
        """
        if soc is None:
            return 0.5
        if soc <= self.soc_min:
            return 0.0
        pivot = (self.soc_min + self.soc_max) / 2.0
        if soc <= pivot:
            return 0.6 * (soc - self.soc_min) / (pivot - self.soc_min)
        if soc <= self.soc_max:
            return 0.6 + 0.4 * (soc - pivot) / (self.soc_max - pivot)
        return 1.0

    def _score_forecast(self, data: dict[str, Any]) -> float:
        """Score based on production density: remaining forecast vs. expected potential.

        density = forecast_kwh / (peak_pv_kw × hours_remaining_of_sun)

        This single dimensionless ratio encodes both the installation size and
        the time of day — no hardcoded kWh thresholds needed.

          density ≥ 1.0  → 0.10  full clear-sky day ahead → defer strongly
          0.5 ≤ d < 1.0  → 0.10→0.50  decent production coming → defer
          0.1 ≤ d < 0.5  → 0.50→0.85  sun fading or cloudy → act progressively
          d < 0.1        → 0.90  last scraps / near sunset → urgency

          forecast None or 0 → 0.5  neutral (night: surplus scoring takes over)
          hour ≥ 19         → 0.5  production negligible — sensor residuals meaningless

        Sunset is approximated at 20 h; remaining window is clamped to ≥ 0.5 h
        to avoid division by zero near nightfall.
        """
        forecast_kwh = data.get("forecast_kwh")
        if forecast_kwh is None or forecast_kwh <= 0.0:
            return 0.5
        if self.peak_pv_kw <= 0.0:
            return 0.5  # PV peak not configured — can't compute density

        hour = float(data.get("hour", 12))
        if hour >= 19.0:
            return 0.5  # After 19h — production negligible, sensor residuals meaningless
        hours_remaining = max(0.5, 20.0 - hour)
        density = forecast_kwh / (self.peak_pv_kw * hours_remaining)

        if density >= 1.0:
            return 0.10
        if density >= 0.5:
            # 0.10 → 0.50 as density falls from 1.0 to 0.5
            return 0.10 + 0.40 * (1.0 - density) / 0.5
        if density >= 0.1:
            # 0.50 → 0.85 as density falls from 0.5 to 0.1
            return 0.50 + 0.35 * (0.5 - density) / 0.4
        return 0.90
