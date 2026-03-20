"""
Helios day simulation — entry point.

Usage:
    python simulation/run.py [options]

Examples:
    python simulation/run.py
    python simulation/run.py --profile cloudy --peak-pv 6000 --tempo red -v
    python simulation/run.py --no-battery --threshold 0.5
    python simulation/run.py --compare   # run all profiles side by side
"""
from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .engine import SimConfig, SimResult, Tariff, run
from .devices import default_devices, load_devices_from_json
from .profiles import load_base_load_from_json
from .optimizer import OptResult, optimize


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _bar(ratio: float, width: int = 20) -> str:
    filled = round(ratio * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _fmt_w(w: float) -> str:
    if abs(w) >= 950:
        return f"{w / 1000:.1f} kW"
    return f"{w:.0f} W"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(result: SimResult, cfg: SimConfig, verbose: bool = False) -> None:
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║  HELIOS — Simulation journée                             ║")
    print(f"║  Saison : {cfg.season:<8}  Météo : {cfg.cloud:<14}  ║")
    print(f"║  Pic PV : {cfg.peak_pv_w:.0f} W          Tempo : {cfg.tempo:<5}            ║")
    print("╚══════════════════════════════════════════════════════════╝")

    if verbose:
        print()
        print(f"  {'H':>5}  {'PV':>7}  {'Maison':>7}  {'Réseau':>8}  "
              f"{'Batterie':>9}  {'SOC':>5}  {'Score':>5}  Appareils")
        print(f"  {'─'*5}  {'─'*7}  {'─'*7}  {'─'*8}  "
              f"{'─'*9}  {'─'*5}  {'─'*5}  {'─'*28}")

        prev_h = -1
        for s in result.steps:
            h = int(s.hour)
            mins = int((s.hour - h) * 60)
            if mins != 0:
                continue
            if h == prev_h:
                continue
            prev_h = h
            grid_str = f"{'+'if s.grid_w>0 else ''}{_fmt_w(s.grid_w)}"
            soc_str = f"{s.bat_soc:.0f}%" if s.bat_soc else "—"
            if s.bat_w > 0:
                bat_str = f"+{_fmt_w(s.bat_w)}"   # charge
            elif s.bat_w < 0:
                bat_str = f"{_fmt_w(s.bat_w)}"    # discharge
            else:
                bat_str = "—"
            devs = ", ".join(s.active_devices) if s.active_devices else "—"
            print(f"  {h:02d}:00  {_fmt_w(s.pv_w):>7}  {_fmt_w(s.total_load_w):>7}  "
                  f"{grid_str:>8}  {bat_str:>9}  {soc_str:>5}  {s.score:.2f}  {devs}")

    print()
    print(f"  {'PV produit':<30} {result.e_pv_kwh:>6.2f} kWh")
    print(f"  {'Consommation totale':<30} {result.e_load_kwh:>6.2f} kWh")
    print(f"  {'Autoconsommé (PV → maison)':<30} {result.e_self_consumed_kwh:>6.2f} kWh")
    print(f"  {'Export réseau':<30} {result.e_grid_export_kwh:>6.2f} kWh")
    print(f"  {'Import réseau':<30} {result.e_grid_import_kwh:>6.2f} kWh")
    print(f"  {'SOC batterie fin de journée':<30} {result.bat_soc_end:>5.1f} %")
    print(f"  {'Coût électricité (import)':<30} {result.cost_eur:>6.2f} €")
    print(f"  {'Coût sans PV (référence)':<30} {result.cost_no_pv_eur:>6.2f} €")
    print(f"  {'Économie réalisée':<30} {result.savings_eur:>6.2f} €")

    ac = result.autoconsumption_rate
    ss = result.self_sufficiency_rate

    print()
    print(f"  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  Autoconsommation  {_bar(ac)}  {ac*100:5.1f}%  │")
    print(f"  │  Autosuffisance    {_bar(ss)}  {ss*100:5.1f}%  │")
    print(f"  └─────────────────────────────────────────────────────┘")

    print()
    print(f"  {'Appareil':<22} {'Total':>8}  {'PV':>8}  {'Réseau':>8}  {'Durée':>6}")
    print(f"  {'─'*22}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*6}")
    for dev in result.devices:
        grid_e = dev.energy_kwh - dev.energy_from_pv_kwh
        print(f"  {dev.name:<22} {dev.energy_kwh:>6.2f} kWh"
              f"  {dev.energy_from_pv_kwh:>6.2f} kWh"
              f"  {grid_e:>6.2f} kWh"
              f"  {dev.run_today_h:>4.1f} h")
    print()


def print_optimize(results: list[OptResult], top: int, alpha: float) -> None:
    """Print optimization results table."""
    print()
    print("╔══════════════════════════════════════════════════════════════════════════════╗")
    print(f"║  HELIOS — Optimisation paramètres  (α={alpha:.1f} : autocons. / coût)          ║")
    print("╚══════════════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"  {'#':>3}  {'Surplus':>7}  {'Tempo':>5}  {'SOC':>5}  {'Seuil':>5}"
          f"  {'Autocons.':>10}  {'Économie':>9}  {'Coût':>6}  {'Objectif':>9}")
    print(f"  {'─'*3}  {'─'*7}  {'─'*5}  {'─'*5}  {'─'*5}"
          f"  {'─'*10}  {'─'*9}  {'─'*6}  {'─'*9}")
    for i, r in enumerate(results[:top], 1):
        print(f"  {i:>3}  {r.w_surplus:>6.0%}  {r.w_tempo:>4.0%}  {r.w_soc:>4.0%}"
              f"  {r.threshold:>4.0%}"
              f"  {_bar(r.autoconsumption, 8)} {r.autoconsumption*100:4.1f}%"
              f"  {r.savings_rate*100:>7.1f}%"
              f"  {r.cost_eur:>5.2f}€"
              f"  {r.objective:>8.4f}")
    print()
    best = results[0]
    print("  ── Configuration optimale ──────────────────────────────────────────────────")
    print(f"  Ajouter dans SimConfig / scoring :")
    print(f"    weight_pv_surplus  = {best.w_surplus}")
    print(f"    weight_tempo       = {best.w_tempo}")
    print(f"    weight_battery_soc = {best.w_soc}")
    print(f"    weight_forecast    = {best.w_forecast}")
    print(f"  dispatch_threshold   = {best.threshold}")
    print()


def print_comparison(seasons: list[str], clouds: list[str], cfg_base: SimConfig, devices: list | None) -> None:
    """Run all season × cloud combinations and print a comparison table."""
    results: list[tuple[str, str, SimResult]] = []
    for season in seasons:
        for cloud in clouds:
            cfg = SimConfig(
                season=season,
                cloud=cloud,
                peak_pv_w=cfg_base.peak_pv_w,
                tempo=cfg_base.tempo,
                bat_soc_start=cfg_base.bat_soc_start,
                bat_enabled=cfg_base.bat_enabled,
                bat_capacity_kwh=cfg_base.bat_capacity_kwh,
                dispatch_threshold=cfg_base.dispatch_threshold,
                base_load_fn=cfg_base.base_load_fn,
                scoring=cfg_base.scoring,
            )
            results.append((season, cloud, run(cfg, devices)))

    print()
    print("╔════════════════════════════════════════════════════════════════════════╗")
    print("║  HELIOS — Comparaison saisons × météo                                 ║")
    print("╚════════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"  {'Saison':<8}  {'Météo':<14} {'PV':>7}  {'Import':>7}  {'Export':>7}  "
          f"{'Autocons.':>10}  {'Autosuff.':>10}")
    print(f"  {'─'*8}  {'─'*14}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*10}  {'─'*10}")
    prev_season = None
    for season, cloud, r in results:
        if season != prev_season and prev_season is not None:
            print()
        prev_season = season
        print(
            f"  {season:<8}  {cloud:<14} {r.e_pv_kwh:>6.1f}kWh"
            f"  {r.e_grid_import_kwh:>6.1f}kWh"
            f"  {r.e_grid_export_kwh:>6.1f}kWh"
            f"  {_bar(r.autoconsumption_rate, 10)} {r.autoconsumption_rate*100:4.1f}%"
            f"  {_bar(r.self_sufficiency_rate, 10)} {r.self_sufficiency_rate*100:4.1f}%"
        )
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Helios day simulation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--season",
        choices=["winter", "spring", "summer", "autumn"],
        default="summer",
        help="Season (controls sunrise/sunset and solar intensity)",
    )
    parser.add_argument(
        "--cloud",
        choices=["clear", "partly_cloudy", "cloudy"],
        default="clear",
        help="Cloud cover",
    )
    parser.add_argument("--peak-pv", type=float, default=4000.0,
                        metavar="W", help="Peak PV power")
    parser.add_argument("--tempo", choices=["blue", "white", "red"], default="blue",
                        help="EDF Tempo color for the day")
    parser.add_argument("--bat-soc", type=float, default=50.0,
                        metavar="PCT", help="Initial battery SOC (%%)")
    parser.add_argument("--bat-capacity", type=float, default=10.0,
                        metavar="KWH", help="Battery capacity (kWh)")
    parser.add_argument("--bat-charge-max", type=float, default=2000.0,
                        metavar="W", help="Battery max charge power (W)")
    parser.add_argument("--bat-discharge-max", type=float, default=2000.0,
                        metavar="W", help="Battery max discharge power (W)")
    parser.add_argument("--bat-discharge-start", type=float, default=6.0,
                        metavar="H", help="Hour from which battery discharge is allowed (default: 6h)")
    parser.add_argument("--bat-efficiency", type=float, default=0.75,
                        metavar="0-1", help="Battery round-trip efficiency (default: 0.75)")
    parser.add_argument("--bat-soc-min", type=float, default=20.0,
                        metavar="PCT", help="Battery minimum SOC (%%) — floor for discharge")
    parser.add_argument("--bat-soc-max", type=float, default=95.0,
                        metavar="PCT", help="Battery maximum SOC (%%) — ceiling for charge")
    parser.add_argument("--no-battery", action="store_true",
                        help="Disable battery")
    parser.add_argument("--forecast-noise", type=float, default=0.15,
                        metavar="0-1", help="Forecast error std-dev (0=perfect, 0.15=±15%%)")
    parser.add_argument("--threshold", type=float, default=0.30,
                        metavar="0-1", help="Dispatch score threshold")
    parser.add_argument("--weight-surplus", type=float, default=None,
                        metavar="0-1", help="Override PV surplus weight")
    parser.add_argument("--weight-tempo", type=float, default=None,
                        metavar="0-1", help="Override Tempo color weight")
    parser.add_argument("--weight-soc", type=float, default=None,
                        metavar="0-1", help="Override battery SOC weight")
    parser.add_argument("--weight-forecast", type=float, default=None,
                        metavar="0-1", help="Override forecast weight")
    parser.add_argument("--compare", action="store_true",
                        help="Compare all solar profiles in a table")
    parser.add_argument("--optimize", action="store_true",
                        help="Grid-search optimal scoring weights and dispatch threshold")
    parser.add_argument("--opt-alpha", type=float, default=0.5, metavar="0-1",
                        help="Objective weight: 1=autoconsumption only, 0=cost savings only")
    parser.add_argument("--opt-runs", type=int, default=1, metavar="N",
                        help="Runs averaged per config (useful for stochastic cloud profiles)")
    parser.add_argument("--opt-top", type=int, default=10, metavar="N",
                        help="Number of top results to display")
    parser.add_argument("--devices", metavar="JSON",
                        help="Path to devices JSON (default: simulation/config/devices.json)")
    parser.add_argument("--base-load", metavar="JSON",
                        help="Path to base load JSON (default: built-in profile)")
    parser.add_argument("--tariff", metavar="JSON",
                        help="Path to tariff JSON (default: EDF Tempo 03/03/2026)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print hourly table")
    args = parser.parse_args()

    base_load_fn = load_base_load_from_json(args.base_load) if args.base_load else None
    devices = load_devices_from_json(args.devices) if args.devices else None
    tariff = Tariff.from_json(args.tariff) if args.tariff else Tariff()

    # Build scoring dict — only override keys that were explicitly passed
    scoring = {
        "weight_pv_surplus":  0.4,
        "weight_tempo":       0.3,
        "weight_battery_soc": 0.2,
        "weight_forecast":    0.1,
    }
    if args.weight_surplus  is not None: scoring["weight_pv_surplus"]  = args.weight_surplus
    if args.weight_tempo    is not None: scoring["weight_tempo"]        = args.weight_tempo
    if args.weight_soc      is not None: scoring["weight_battery_soc"]  = args.weight_soc
    if args.weight_forecast is not None: scoring["weight_forecast"]     = args.weight_forecast

    cfg = SimConfig(
        season=args.season,
        cloud=args.cloud,
        peak_pv_w=args.peak_pv,
        tempo=args.tempo,
        bat_soc_start=args.bat_soc,
        bat_enabled=not args.no_battery,
        bat_capacity_kwh=args.bat_capacity,
        bat_max_charge_w=args.bat_charge_max,
        bat_max_discharge_w=args.bat_discharge_max,
        bat_efficiency=args.bat_efficiency,
        bat_discharge_start=args.bat_discharge_start,
        bat_soc_min=args.bat_soc_min,
        bat_soc_max=args.bat_soc_max,
        dispatch_threshold=args.threshold,
        forecast_noise=args.forecast_noise,
        base_load_fn=base_load_fn,
        scoring=scoring,
        tariff=tariff,
    )

    if args.compare:
        print_comparison(
            ["winter", "spring", "summer", "autumn"],
            ["clear", "partly_cloudy", "cloudy"],
            cfg,
            devices,
        )
    elif args.optimize:
        devices_fn = (lambda p: lambda: load_devices_from_json(p))(args.devices) \
            if args.devices else default_devices
        results = optimize(
            cfg,
            devices_fn,
            objective_alpha=args.opt_alpha,
            n_runs=args.opt_runs,
        )
        print_optimize(results, top=args.opt_top, alpha=args.opt_alpha)
    else:
        result = run(cfg, devices)
        print_report(result, cfg, verbose=args.verbose)


if __name__ == "__main__":
    main()
