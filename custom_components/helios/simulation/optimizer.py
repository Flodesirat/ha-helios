"""Grid-search optimizer — REMOVED.

The optimizer (grid search over scoring weights / dispatch threshold) has been
removed as part of the Lot 8 refactoring.  Scoring weights are now fixed
constants and there is no dispatch threshold in the algorithm.

This stub is kept so that any old import does not immediately crash with an
ImportError; the symbols raise NotImplementedError at call time.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OptResult:
    """Stub — optimizer has been removed."""
    w_surplus: float = 0.0
    w_tempo: float = 0.0
    w_soc: float = 0.0
    w_solar: float = 0.0
    threshold: float = 0.0
    autoconsumption: float = 0.0
    savings_rate: float = 0.0
    cost_eur: float = 0.0
    objective: float = 0.0
    obj_mean: float = 0.0
    obj_std: float = 0.0


def optimize(*args, **kwargs):
    raise NotImplementedError(
        "simulation/optimizer.py has been removed. "
        "Scoring weights are now fixed; use SimConfig.scoring to override them."
    )
