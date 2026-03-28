"""EMA learner for household base load profile.

Tracks the net base load (house consumption minus Helios-controlled devices)
using a per-slot Exponential Moving Average over 288 time slots (5-min resolution).

The learned profile is used as base_load_fn in the daily optimizer so that
simulations reflect the real household's consumption pattern rather than the
generic static base_load.json.

Update rule (applied at each coordinator tick):
    profile[slot] = α × net_base_w + (1 - α) × profile[slot]

α ≈ 0.05 → slow convergence (~1 week of ticks to reach 90 % of a new level),
which smooths out daily variability without over-fitting to a single outlier day.
"""
from __future__ import annotations

import logging
from typing import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

SLOTS = 288          # 24 h × 12 steps/h  (one slot = 5 min)
_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "helios_ema"
_SAVE_DELAY_S = 300  # debounce: write at most once per 5 min


class ConsumptionLearner:
    """Learns the household base load profile via per-slot EMA.

    Lifecycle:
        learner = ConsumptionLearner(hass, entry_id, alpha)
        await learner.async_load()          # call once during setup
        learner.update(slot, net_base_w)    # call at every coordinator tick
        learner.schedule_save()             # debounced — call after update
        fn = learner.as_base_load_fn()      # pass to SimConfig.base_load_fn
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        alpha: float = 0.05,
    ) -> None:
        self._hass = hass
        self._alpha = alpha
        self._profile: list[float] | None = None
        self._sample_count: int = 0
        self._store: Store = Store(hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}_{entry_id}")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def async_load(self, fallback_fn: Callable[[float], float] | None = None) -> None:
        """Load persisted profile, or initialise from fallback / flat default.

        Priority:
        1. Persisted data in HA storage (survives restarts).
        2. fallback_fn — typically loaded from base_load.json — used on first run.
        3. Flat 300 W profile as last resort.
        """
        data = await self._store.async_load()
        if data and "profile" in data and len(data["profile"]) == SLOTS:
            self._profile = [float(v) for v in data["profile"]]
            self._sample_count = int(data.get("sample_count", 0))
            _LOGGER.debug(
                "Helios EMA: profile loaded from storage (samples=%d)", self._sample_count
            )
            return

        if fallback_fn is not None:
            step_h = 5 / 60.0
            self._profile = [max(0.0, fallback_fn(i * step_h)) for i in range(SLOTS)]
            self._sample_count = 0
            _LOGGER.debug("Helios EMA: cold start — initialised from base_load.json fallback")
        else:
            self._profile = [300.0] * SLOTS
            self._sample_count = 0
            _LOGGER.debug("Helios EMA: cold start — initialised with flat 300 W default")

    # ------------------------------------------------------------------
    # Per-tick update
    # ------------------------------------------------------------------

    def update(self, slot: int, net_base_w: float) -> None:
        """Update the EMA for a given 5-min time slot.

        Args:
            slot:        Time slot [0, 287].  slot = (hour*60 + minute) // 5
            net_base_w:  Net base load in W = house_w − helios_devices_w.
                         Negative values are clamped to 0 (can happen when
                         measurement noise causes devices_w > house_w).
        """
        if self._profile is None:
            return
        slot = slot % SLOTS
        net_base_w = max(0.0, net_base_w)
        self._profile[slot] = (
            self._alpha * net_base_w + (1.0 - self._alpha) * self._profile[slot]
        )
        self._sample_count += 1

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def schedule_save(self) -> None:
        """Schedule a debounced write to HA storage (at most once per 5 min)."""
        if self._profile is None:
            return
        self._store.async_delay_save(self._serialize, _SAVE_DELAY_S)

    def _serialize(self) -> dict:
        return {
            "profile": self._profile,
            "sample_count": self._sample_count,
        }

    # ------------------------------------------------------------------
    # Simulation interface
    # ------------------------------------------------------------------

    def as_base_load_fn(self) -> Callable[[float], float]:
        """Return a snapshot callable(hour: float) -> W for use in SimConfig.

        Takes a snapshot of the current profile so that a concurrent EMA update
        during a long optimization run cannot mutate the values being used.
        """
        if self._profile is None:
            return lambda _: 300.0
        profile = list(self._profile)

        def _fn(hour: float) -> float:
            slot = int(hour * 12) % SLOTS
            return profile[slot]

        return _fn

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def sample_count(self) -> int:
        """Total number of EMA updates received since last cold start."""
        return self._sample_count

    @property
    def profile(self) -> list[float] | None:
        """Current 288-slot EMA profile in W, or None if not yet loaded."""
        return list(self._profile) if self._profile is not None else None
