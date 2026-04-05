"""Solar, load and Tempo profiles for day simulation."""
from __future__ import annotations

import json
import math
import random
from typing import Callable, Literal

Season = Literal["winter", "spring", "summer", "autumn"]
CloudCover = Literal["clear", "partly_cloudy", "cloudy"]


# ---------------------------------------------------------------------------
# Solar PV profile — season × cloud cover
# ---------------------------------------------------------------------------

# Seasonal curves calibrated for France (~47°N)
# sunrise/sunset in decimal hours, sigma = half-width of the Gaussian bell
_SEASON_PARAMS: dict[str, dict] = {
    "winter": {"sunrise": 8.0, "sunset": 17.0, "peak_hour": 12.5, "sigma": 2.2, "scale": 0.50},
    "spring": {"sunrise": 6.5, "sunset": 20.0, "peak_hour": 13.0, "sigma": 3.3, "scale": 0.82},
    "summer": {"sunrise": 5.5, "sunset": 21.5, "peak_hour": 13.5, "sigma": 3.8, "scale": 1.08},
    "autumn": {"sunrise": 7.5, "sunset": 18.5, "peak_hour": 12.5, "sigma": 2.8, "scale": 0.72},
}

# Cloud cover modifiers: triangular(min, max, mode) multiplied to the raw PV
_CLOUD_MODIFIERS: dict[str, tuple[float, float, float] | None] = {
    "clear":        None,                    # no noise
    "partly_cloudy": (0.50, 1.00, 0.85),
    "cloudy":        (0.10, 0.45, 0.25),
}


def solar_elevation(hour: float, season: Season = "summer") -> float:
    """Return a synthetic solar elevation (degrees) consistent with the PV profile.

    Uses the same seasonal Gaussian parameters as pv_power_w so that the
    scoring engine's f_solar is coherent with the simulated PV production.
    Elevation is 0° at sunrise/sunset and peaks at the Gaussian peak hour.
    The peak elevation is calibrated for France (~47°N):
      winter ≈ 22°, spring/autumn ≈ 40°, summer ≈ 63°.
    """
    _PEAK_ELEVATION: dict[str, float] = {
        "winter": 22.0,
        "spring": 40.0,
        "summer": 63.0,
        "autumn": 38.0,
    }
    p = _SEASON_PARAMS[season]
    if hour <= p["sunrise"] or hour >= p["sunset"]:
        return 0.0
    peak_el = _PEAK_ELEVATION.get(season, 40.0)
    # Gaussian shape matching the PV profile
    elev = peak_el * math.exp(-0.5 * ((hour - p["peak_hour"]) / p["sigma"]) ** 2)
    return max(0.0, elev)


def pv_power_w(
    hour: float,
    season: Season = "summer",
    cloud: CloudCover = "clear",
    peak_w: float = 4000.0,
) -> float:
    """Return instantaneous PV power (W) at *hour* for the given season and cloud cover."""
    p = _SEASON_PARAMS[season]
    if hour < p["sunrise"] or hour > p["sunset"]:
        return 0.0

    raw = peak_w * p["scale"] * math.exp(-0.5 * ((hour - p["peak_hour"]) / p["sigma"]) ** 2)

    modifier = _CLOUD_MODIFIERS[cloud]
    if modifier is not None:
        raw *= random.triangular(*modifier)

    return max(0.0, raw)


# ---------------------------------------------------------------------------
# Base house load profile (without controllable devices)
# ---------------------------------------------------------------------------

def base_load_w(hour: float) -> float:
    """Typical French household base load (W) — no controllable devices."""
    if 0.0 <= hour < 2.0:
        return 400.0
    if 2.0 <= hour < 5.5:
        return 250.0
    if 5.5 <= hour < 7.0:
        return 250.0 + (hour - 5.5) * 500.0       # morning ramp-up
    if 7.0 <= hour < 9.0:
        return 1000.0                               # breakfast peak
    if 9.0 <= hour < 17.0:
        return 450.0                                # daytime trough
    if 17.0 <= hour < 18.5:
        return 450.0 + (hour - 17.0) * 430.0       # evening ramp-up
    if 18.5 <= hour < 22.0:
        return 1100.0                               # evening peak
    if 22.0 <= hour < 24.0:
        return 1100.0 - (hour - 22.0) * 350.0      # wind-down
    return 400.0


# ---------------------------------------------------------------------------
# Load base load from JSON
# ---------------------------------------------------------------------------

def load_base_load_from_json(path: str) -> Callable[[float], float]:
    """Return a base_load_w(hour) function built from a JSON segment file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    segments = [
        (float(s["from"]), float(s["to"]), float(s["w_start"]), float(s["w_end"]))
        for s in data["segments"]
    ]

    def _load(hour: float) -> float:
        for h_from, h_to, w_start, w_end in segments:
            if h_from <= hour < h_to:
                if w_start == w_end:
                    return w_start
                t = (hour - h_from) / (h_to - h_from)
                return w_start + t * (w_end - w_start)
        return 0.0

    return _load


# ---------------------------------------------------------------------------
# Tempo color
# ---------------------------------------------------------------------------

def tempo_color(hour: float, day_color: str = "blue") -> str:
    """Return effective Tempo color. RED is expensive only 6h–22h."""
    if day_color == "red" and 6.0 <= hour < 22.0:
        return "red"
    if day_color == "white":
        return "white"
    return "blue"
