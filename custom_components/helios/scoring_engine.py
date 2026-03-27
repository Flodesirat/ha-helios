"""Scoring engine — computes a normalized [0..1] optimization score."""
from __future__ import annotations

from typing import Any

from .const import (
    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_FORECAST,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_FORECAST,
    DEFAULT_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MAX,
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
        self.capacity_kwh = config.get(CONF_BATTERY_CAPACITY_KWH, 5.0)
        self.soc_min    = float(config.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
        self.soc_max    = float(config.get(CONF_BATTERY_SOC_MAX, DEFAULT_BATTERY_SOC_MAX))

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
        # TODO: implement
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
        """Score based on remaining solar production forecast for today.

        The entity reports kWh still to be produced for the rest of the day.
        It can be revised upward when the sky clears (as seen in real data).

        Curve (non-monotone):
          None / unavailable  → 0.5  neutral
          0 kWh (sun set)     → 0.5  neutral — surplus scoring takes over
          0–2 kWh             → 0.5→0.8  urgency: last chance to use PV today
          2–5 kWh             → 0.8→0.4  sun fading, act now but not panic
          5–10 kWh            → 0.4→0.2  plenty of sun to come, be patient
          ≥ 10 kWh            → 0.2  strong defer: wait for production peak
        """
        forecast_kwh = data.get("forecast_kwh")
        if forecast_kwh is None:
            return 0.5
        if forecast_kwh <= 0.0:
            return 0.5
        if forecast_kwh <= 2.0:
            # Last kWhs of the day → urgency ramp up
            return 0.5 + 0.3 * (forecast_kwh / 2.0)
        if forecast_kwh <= 5.0:
            # Afternoon decline: high urgency → fading
            return 0.8 - 0.4 * (forecast_kwh - 2.0) / 3.0
        if forecast_kwh <= 10.0:
            # Morning/midday: decent sun ahead → defer
            return 0.4 - 0.2 * (forecast_kwh - 5.0) / 5.0
        # Very high forecast: strongly defer, wait for production peak
        return 0.2
