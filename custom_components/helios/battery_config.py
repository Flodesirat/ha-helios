"""Battery list helpers — read, aggregate and migrate the battery configuration.

Since config entry v2 the batteries are a list under ``CONF_BATTERIES``, one
dict per battery. A v1 config (one battery, keys flat in the entry) is still
read as a list of one battery, so code and tests can use either shape.
"""
from __future__ import annotations

import logging
from typing import Any

from .const import (
    CONF_BATTERIES,
    CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_DRIVE_SCRIPT,
    CONF_BATTERY_NEUTRAL_SCRIPT,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_CHARGE_SCRIPT,
    CONF_BATTERY_AUTOCONSUM_SCRIPT,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_NAME,
)

_LOGGER = logging.getLogger(__name__)

# Keys that belong to one battery (flat in a v1 entry, inside the list in v2)
PER_BATTERY_KEYS = (
    CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_DRIVE_SCRIPT,
    CONF_BATTERY_NEUTRAL_SCRIPT,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W,
)
# Battery keys of a v1 entry that no longer exist
_LEGACY_KEYS = (CONF_BATTERY_CHARGE_SCRIPT, CONF_BATTERY_AUTOCONSUM_SCRIPT)


def get_batteries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the configured batteries (a v1 flat config gives a list of one)."""
    batteries = cfg.get(CONF_BATTERIES)
    if batteries is not None:
        return list(batteries)
    flat = {k: cfg[k] for k in PER_BATTERY_KEYS if cfg.get(k) not in (None, "")}
    if not flat:
        return []
    flat.setdefault(CONF_BATTERY_NAME, DEFAULT_BATTERY_NAME)
    return [flat]


def battery_totals(cfg: dict[str, Any]) -> dict[str, float]:
    """Capacity and power limits summed over all batteries."""
    batteries = get_batteries(cfg)
    return {
        CONF_BATTERY_CAPACITY_KWH: sum(
            float(b.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)) for b in batteries
        ),
        CONF_BATTERY_MAX_CHARGE_POWER_W: sum(
            float(b.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 0.0)) for b in batteries
        ),
        CONF_BATTERY_MAX_DISCHARGE_POWER_W: sum(
            float(b.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W, 0.0)) for b in batteries
        ),
    }


def with_battery_totals(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return *cfg* with the flat capacity/power keys set to the battery totals.

    The scoring engine, the dispatch and the daily optimizer reason on one
    battery: with a list, they see the sum of all batteries.
    """
    if CONF_BATTERIES not in cfg:
        return cfg
    return {**cfg, **battery_totals(cfg)}


def state_float(hass: Any, entity_id: str | None) -> float | None:
    """Numeric state of an entity, or None if missing, unavailable or not a number."""
    if not entity_id:
        return None
    s = hass.states.get(entity_id)
    if s is None or s.state in ("unavailable", "unknown"):
        return None
    try:
        return float(s.state)
    except ValueError:
        return None


def read_battery_soc(hass: Any, cfg: dict[str, Any]) -> float | None:
    """SOC of all batteries, weighted by capacity; None when none is known."""
    known = [
        (soc, float(b.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)))
        for b in get_batteries(cfg)
        if (soc := state_float(hass, b.get(CONF_BATTERY_SOC_ENTITY))) is not None
    ]
    if not known:
        return None
    capacity = sum(c for _, c in known)
    if capacity <= 0:
        return sum(s for s, _ in known) / len(known)
    return sum(s * c for s, c in known) / capacity


def read_battery_power(hass: Any, cfg: dict[str, Any]) -> float:
    """Power of the batteries whose SOC is known, summed (W, + = discharge).

    A battery without a known SOC is offline: its 0 W must not count.
    """
    return sum(
        state_float(hass, b.get(CONF_BATTERY_POWER_ENTITY)) or 0.0
        for b in get_batteries(cfg)
        if state_float(hass, b.get(CONF_BATTERY_SOC_ENTITY)) is not None
    )


def migrate_v1_to_v2(
    data: dict[str, Any], options: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move the flat battery of a v1 entry into the battery list.

    The options win over the data, as in the coordinator. The list goes into
    the data; the per-battery and script keys are removed from both.
    """
    batteries = get_batteries({**data, **options})
    drop = set(PER_BATTERY_KEYS) | set(_LEGACY_KEYS)
    new_data = {k: v for k, v in data.items() if k not in drop}
    new_options = {k: v for k, v in options.items() if k not in drop}
    new_data[CONF_BATTERIES] = batteries
    new_options.pop(CONF_BATTERIES, None)
    return new_data, new_options
