"""Scoring engine — computes a normalized [0..1] optimization score."""
from __future__ import annotations

from typing import Any

from .const import (
    CONF_WEIGHT_PV_SURPLUS, CONF_WEIGHT_TEMPO,
    CONF_WEIGHT_BATTERY_SOC, CONF_WEIGHT_FORECAST,
    CONF_BATTERY_CAPACITY_KWH,
    DEFAULT_WEIGHT_PV_SURPLUS, DEFAULT_WEIGHT_TEMPO,
    DEFAULT_WEIGHT_BATTERY_SOC, DEFAULT_WEIGHT_FORECAST,
    TEMPO_BLUE, TEMPO_WHITE, TEMPO_RED,
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
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
        0 W → 0.0, 500 W → ~0.5, 2000 W+ → 1.0 (trapezoid membership).
        TODO: make thresholds configurable.
        """
        # TODO: implement fuzzy trapezoid
        return 0.0

    def _score_tempo(self, color: str | None) -> float:
        """Map Tempo color to [0..1].
        Blue (cheap) → 1.0, White → 0.5, Red (expensive) → 0.0.
        None (no Tempo) → neutral 0.5.
        """
        # TODO: implement
        mapping = {TEMPO_BLUE: 1.0, TEMPO_WHITE: 0.5, TEMPO_RED: 0.0}
        return mapping.get(color, 0.5)

    def _score_soc(self, soc: float | None) -> float:
        """Map battery SOC to [0..1].
        High SOC → less urgent to charge → lower score for charging devices.
        Low SOC → more urgent to conserve battery → lower score for devices.
        Sweet spot around 40-60% → highest score.
        TODO: implement.
        """
        # TODO: implement
        return 0.5

    def _score_forecast(self, data: dict[str, Any]) -> float:
        """Score based on solar forecast for next hours.
        Good forecast → defer non-urgent loads to later.
        TODO: integrate forecast sensor.
        """
        # TODO: implement
        return 0.5
