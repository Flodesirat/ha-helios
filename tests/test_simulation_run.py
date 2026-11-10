"""Tests for the simulation CLI (simulation/run.py, entry point of sim.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import custom_components.helios.simulation.run as sim_run  # noqa: E402

CONFIG_DIR = Path(sim_run.__file__).parent / "config"


def _main(monkeypatch, capsys, *args: str) -> str:
    monkeypatch.setattr(sys, "argv", ["sim.py", *args])
    sim_run.main()
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_bar_is_clamped(self):
        assert sim_run._bar(0.5, 10) == "█" * 5 + "░" * 5
        assert sim_run._bar(-1.0, 4) == "░" * 4
        assert sim_run._bar(2.0, 4) == "█" * 4

    def test_fmt_w_switches_to_kw(self):
        assert sim_run._fmt_w(500) == "500 W"
        assert sim_run._fmt_w(1500) == "1.5 kW"
        assert sim_run._fmt_w(-2000) == "-2.0 kW"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestMain:
    def test_default_run_prints_summary_and_devices(self, monkeypatch, capsys):
        out = _main(monkeypatch, capsys)
        assert "HELIOS — Simulation journée" in out
        assert "summer" in out
        assert "PV produit" in out
        assert "Autoconsommation" in out
        assert "Appareil" in out
        # Hourly table only in verbose mode
        assert "Légende du tableau horaire" not in out

    def test_verbose_with_decisions_prints_hourly_table_and_log(self, monkeypatch, capsys):
        out = _main(monkeypatch, capsys, "-v", "--decisions")
        assert "Légende du tableau horaire" in out
        table = out.split("Décisions appareils")[0]
        hourly = [l for l in table.splitlines() if l[2:7].endswith(":00") and l[2:4].isdigit()]
        assert [l[2:7] for l in hourly] == [f"{h:02d}:00" for h in range(24)]
        assert "Décisions appareils (5 min)" in out

    def test_no_battery_shows_no_soc(self, monkeypatch, capsys):
        out = _main(monkeypatch, capsys, "--no-battery", "-v", "--tempo", "red")
        assert "Tempo : red" in out
        # Without battery, the battery and SOC columns show a dash
        hourly = [l for l in out.splitlines() if l.strip().startswith("03:00")]
        assert hourly and "—" in hourly[0]

    def test_explicit_json_files_and_weights(self, monkeypatch, capsys):
        out = _main(
            monkeypatch, capsys,
            "--season", "winter", "--cloud", "cloudy",
            "--devices", str(CONFIG_DIR / "devices.json"),
            "--base-load", str(CONFIG_DIR / "base_load.json"),
            "--tariff", str(CONFIG_DIR / "tariff.json"),
            "--appliance-schedule", str(CONFIG_DIR / "appliance_schedule.json"),
            "--weight-surplus", "0.5", "--weight-tempo", "0.2",
            "--weight-soc", "0.2", "--weight-solar", "0.1",
        )
        assert "winter" in out
        assert "cloudy" in out
        assert "PV produit" in out

    def test_weights_are_passed_to_sim_config(self, monkeypatch, capsys):
        captured = {}
        real_run = sim_run.run

        def _spy(cfg, devices):
            captured["scoring"] = cfg.scoring
            return real_run(cfg, devices)

        monkeypatch.setattr(sim_run, "run", _spy)
        _main(monkeypatch, capsys, "--weight-surplus", "0.7", "--weight-solar", "0.0")
        assert captured["scoring"] == {
            "weight_pv_surplus": 0.7,
            "weight_tempo": 0.3,
            "weight_battery_soc": 0.2,
            "weight_solar": 0.0,
        }

    def test_without_default_schedule_devices_stay_default(self, monkeypatch, capsys):
        captured = {}
        real_run = sim_run.run

        def _spy(cfg, devices):
            captured["devices"] = devices
            return real_run(cfg, devices)

        monkeypatch.setattr(sim_run, "_DEFAULT_SCHED", CONFIG_DIR / "missing.json")
        monkeypatch.setattr(sim_run, "run", _spy)
        _main(monkeypatch, capsys)
        assert captured["devices"] is None

    def test_compare_runs_every_season_and_cloud(self, monkeypatch, capsys):
        calls = []
        real_run = sim_run.run

        def _spy(cfg, devices):
            calls.append((cfg.season, cfg.cloud))
            return real_run(cfg, devices)

        monkeypatch.setattr(sim_run, "run", _spy)
        out = _main(monkeypatch, capsys, "--compare")
        assert "Comparaison saisons × météo" in out
        assert len(calls) == 12
        assert set(calls) == {
            (s, c)
            for s in ("winter", "spring", "summer", "autumn")
            for c in ("clear", "partly_cloudy", "cloudy")
        }

    def test_invalid_choice_exits(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _main(monkeypatch, capsys, "--season", "monsoon")
