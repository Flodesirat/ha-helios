"""Pool filtration rules — pure logic, no HA dependency.

- Filtration table: daily filtration hours as a function of water temperature.
- Wintering: automatic switch on the midnight water temperature, with hysteresis.
- Frost protection: level derived from the pump-room temperature, with hysteresis.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Wintering exits when the reference temperature reaches threshold + this value.
WINTERING_HYSTERESIS_K = 2.0
# Frost protection exits when the room temperature reaches threshold + this value.
FROST_HYSTERESIS_K = 1.0

FROST_OFF        = "off"
FROST_SESSIONS   = "sessions"
FROST_CONTINUOUS = "continuous"


def parse_filtration_table(raw: str | None) -> list[tuple[float, float]]:
    """Parse "10:2, 14:4, 20:10" into [(10.0, 2.0), (14.0, 4.0), (20.0, 10.0)].

    Points are sorted by temperature. Raises ValueError on a malformed entry,
    fewer than two points, a duplicate temperature or hours outside [0, 24].
    """
    points: list[tuple[float, float]] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        temp_s, sep, hours_s = chunk.partition(":")
        if not sep:
            raise ValueError(f"missing ':' in '{chunk}'")
        temp  = float(temp_s.strip().replace("°", ""))
        hours = float(hours_s.strip().rstrip("h"))
        if not 0.0 <= hours <= 24.0:
            raise ValueError(f"hours out of range in '{chunk}'")
        points.append((temp, hours))
    if len(points) < 2:
        raise ValueError("at least two points are required")
    points.sort()
    temps = [t for t, _ in points]
    if len(set(temps)) != len(temps):
        raise ValueError("duplicate temperature")
    return points


def is_valid_filtration_table(raw: str | None) -> bool:
    try:
        parse_filtration_table(raw)
    except ValueError:
        return False
    return True


def filtration_hours(table: list[tuple[float, float]], water_temp: float) -> float:
    """Linear interpolation of the table, clamped to its first and last points."""
    if water_temp <= table[0][0]:
        return table[0][1]
    if water_temp >= table[-1][0]:
        return table[-1][1]
    for (t0, h0), (t1, h1) in zip(table, table[1:]):
        if t0 <= water_temp <= t1:
            return h0 + (h1 - h0) * (water_temp - t0) / (t1 - t0)
    return table[-1][1]  # unreachable — table is sorted


def next_wintering(currently: bool, water_temp: float, threshold: float) -> bool:
    """Wintering state for the day, from the reference water temperature."""
    if currently:
        return water_temp < threshold + WINTERING_HYSTERESIS_K
    return water_temp < threshold


def next_frost_level(
    current: str,
    room_temp: float,
    sessions_below: float,
    continuous_below: float,
) -> str:
    """Frost protection level from the pump-room temperature.

    Enter at the threshold, exit at threshold + FROST_HYSTERESIS_K.
    """
    if room_temp < continuous_below:
        return FROST_CONTINUOUS
    if current == FROST_CONTINUOUS and room_temp < continuous_below + FROST_HYSTERESIS_K:
        return FROST_CONTINUOUS
    if room_temp < sessions_below:
        return FROST_SESSIONS
    if current != FROST_OFF and room_temp < sessions_below + FROST_HYSTERESIS_K:
        return FROST_SESSIONS
    return FROST_OFF


def frost_wants_on(level: str, minute_of_hour: int, session_minutes: int) -> bool:
    """True when frost protection requires the pump to run at this minute."""
    if level == FROST_CONTINUOUS:
        return True
    if level == FROST_SESSIONS:
        return minute_of_hour < session_minutes
    return False
