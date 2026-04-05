"""Grid-search optimizer — finds scoring weights and dispatch threshold
that maximize autoconsumption and minimize electricity cost.

Objective function (combined, range 0–1):
    obj = alpha × autoconsumption_rate + (1 - alpha) × savings_rate

where savings_rate = (cost_no_pv - cost) / cost_no_pv
"""
from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass, replace
from typing import Callable

from .engine import SimConfig, SimResult, run
from .devices import SimDevice


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class OptResult:
    w_surplus: float
    w_tempo: float
    w_soc: float
    w_solar: float
    threshold: float
    autoconsumption: float   # [0, 1]
    savings_rate: float      # [0, 1]  (cost_no_pv - cost) / cost_no_pv
    cost_eur: float
    objective: float         # risk-adjusted score: obj_mean − risk_lambda × obj_std
    obj_mean: float = 0.0    # mean of per-run objectives across Monte Carlo runs
    obj_std:  float = 0.0    # std-dev of per-run objectives (0 when n_runs == 1)


# ---------------------------------------------------------------------------
# Core optimizer
# ---------------------------------------------------------------------------

def optimize(
    cfg_base: SimConfig,
    devices_fn: "Callable[[], list[SimDevice] | tuple[list[SimDevice], list]]",
    *,
    objective_alpha: float = 0.5,
    w_solar: float = 0.1,
    threshold_values: list[float] | None = None,
    weight_step: float = 0.1,
    n_runs: int = 1,
    risk_lambda: float = 0.5,
    base_load_noise: float = 0.0,
    progress: bool = True,
) -> list[OptResult]:
    """Run grid search and return results sorted by risk-adjusted objective (best first).

    Args:
        cfg_base:          Base SimConfig (season, cloud, battery params…).
        devices_fn:        Callable returning a fresh device list for each run.
        objective_alpha:   Weight of autoconsomption in the objective (0=cost only, 1=AC only).
        w_solar:        Fixed forecast weight (removed from search space).
        threshold_values:  Dispatch score thresholds to test.
        weight_step:       Grid resolution for scoring weights (0.1 = 10 % steps).
        n_runs:            Monte Carlo runs per config; variance is estimated across runs.
        risk_lambda:       Penalty on objective std-dev: final_obj = mean − λ × std.
                           0 = pure mean, 0.5 = moderate risk aversion, 2 = very conservative.
        base_load_noise:   Std-dev of the day-level multiplicative noise injected on base load
                           for each Monte Carlo run (0 = deterministic).
        progress:          Print progress bar to stderr.
    """
    if threshold_values is None:
        threshold_values = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]

    # Build weight combinations (ws + wt + wb + w_solar = 1.0)
    n = round(1.0 / weight_step)
    vals = [round(i * weight_step, 2) for i in range(1, n)]
    combos = [
        (ws, wt, round(1.0 - w_solar - ws - wt, 2))
        for ws in vals
        for wt in vals
        if round(1.0 - w_solar - ws - wt, 2) >= weight_step
    ]
    total = len(combos) * len(threshold_values)

    results: list[OptResult] = []
    done = 0

    for ws, wt, wb in combos:
        for threshold in threshold_values:
            done += 1
            if progress:
                pct = done / total
                bar = "█" * round(pct * 30) + "░" * (30 - round(pct * 30))
                sys.stderr.write(f"\r  [{bar}] {done}/{total}  ")
                sys.stderr.flush()

            scoring = {
                "weight_pv_surplus":  ws,
                "weight_tempo":       wt,
                "weight_battery_soc": wb,
                "weight_solar":    w_solar,
            }
            cfg = replace(cfg_base, scoring=scoring, dispatch_threshold=threshold,
                          base_load_noise=base_load_noise)

            ac_sum = cost_sum = no_pv_sum = 0.0
            per_run_obj: list[float] = []
            for _ in range(n_runs):
                _devs = devices_fn()
                if isinstance(_devs, tuple):
                    _sim_devs, _managed_devs = _devs
                    r = run(cfg, _sim_devs, managed_devices=_managed_devs)
                else:
                    r = run(cfg, _devs)
                ac_sum    += r.autoconsumption_rate
                cost_sum  += r.cost_eur
                no_pv_sum += r.cost_no_pv_eur
                run_savings = (r.cost_no_pv_eur - r.cost_eur) / max(r.cost_no_pv_eur, 1e-9)
                per_run_obj.append(
                    objective_alpha * r.autoconsumption_rate
                    + (1.0 - objective_alpha) * run_savings
                )

            ac       = ac_sum / n_runs
            cost     = cost_sum / n_runs
            no_pv    = no_pv_sum / n_runs
            savings  = (no_pv - cost) / max(no_pv, 1e-9)
            obj_mean = statistics.mean(per_run_obj)
            obj_std  = statistics.pstdev(per_run_obj)   # 0.0 when n_runs == 1
            obj      = obj_mean - risk_lambda * obj_std

            results.append(OptResult(
                w_surplus=ws, w_tempo=wt, w_soc=wb, w_solar=w_solar,
                threshold=threshold,
                autoconsumption=ac,
                savings_rate=savings,
                cost_eur=cost,
                objective=obj,
                obj_mean=obj_mean,
                obj_std=obj_std,
            ))

    if progress:
        sys.stderr.write("\n")

    return sorted(results, key=lambda x: -x.objective)
