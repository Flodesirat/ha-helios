"""Simulation profiles — PV curve, base load, JSON base load, Tempo color."""
from __future__ import annotations

import json
import random

import pytest

from custom_components.helios.simulation.profiles import (
    base_load_w, load_base_load_from_json, pv_power_w, tempo_color,
)


class TestPvPower:
    def test_zero_at_night(self):
        assert pv_power_w(2.0, "summer", "clear", 4000) == 0.0

    def test_cloud_modifier_reduces_clear_sky(self):
        random.seed(1)
        clear = pv_power_w(13.0, "summer", "clear", 4000)
        cloudy = pv_power_w(13.0, "summer", "cloudy", 4000)
        assert 0.0 < cloudy < clear


class TestBaseLoad:
    @pytest.mark.parametrize("hour, expected", [
        (1.0, 400.0), (3.0, 250.0), (6.0, 500.0), (8.0, 1000.0),
        (12.0, 450.0), (18.0, 880.0), (20.0, 1100.0), (23.0, 750.0),
    ])
    def test_profile(self, hour, expected):
        assert base_load_w(hour) == pytest.approx(expected)

    def test_out_of_day_hour_falls_back(self):
        assert base_load_w(24.5) == 400.0


class TestBaseLoadFromJson:
    def test_flat_and_ramp_segments(self, tmp_path):
        path = tmp_path / "base_load.json"
        path.write_text(json.dumps({"segments": [
            {"from": 0, "to": 6, "w_start": 300, "w_end": 300},
            {"from": 6, "to": 8, "w_start": 300, "w_end": 900},
        ]}))
        load = load_base_load_from_json(str(path))
        assert load(3.0) == 300
        assert load(7.0) == pytest.approx(600.0)
        # Hours outside every segment draw nothing
        assert load(10.0) == 0.0


class TestTempoColor:
    @pytest.mark.parametrize("hour, day, expected", [
        (12.0, "red", "red"),
        (23.0, "red", "blue"),     # red only 6h–22h
        (3.0, "white", "white"),
        (12.0, "blue", "blue"),
    ])
    def test_color(self, hour, day, expected):
        assert tempo_color(hour, day) == expected
