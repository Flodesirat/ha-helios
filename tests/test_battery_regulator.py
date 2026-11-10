"""Tests for battery_regulator — PassiveRegulator, limits, split, pre-charge."""
from __future__ import annotations

import pytest

from custom_components.helios.battery_drive import precharge_setpoint
from custom_components.helios.battery_regulator import (
    BatteryLimits,
    PassiveRegulator,
    battery_limits,
    split_setpoint,
)


def _reg(**kw) -> PassiveRegulator:
    kw.setdefault("max_discharge", 1450.0)
    kw.setdefault("max_charge", 1450.0)
    kw.setdefault("settle", 3.0)
    kw.setdefault("target", 30.0)
    return PassiveRegulator(**kw)


class TestPassiveRegulator:

    def test_full_correction_to_target(self):
        """consigne = appliquée + réseau − cible, sans gain."""
        reg = _reg()
        assert reg.update(500.0, at=0.0) == pytest.approx(470.0)

    def test_export_gives_charge(self):
        reg = _reg()
        assert reg.update(-400.0, at=0.0) == pytest.approx(-430.0)

    def test_base_is_setpoint_applied_at_measurement(self):
        """Un relevé antérieur au settle part de l'ancienne consigne, pas de la dernière écrite."""
        reg = _reg()
        reg.accepted(470.0, at=10.0)
        # Measured 1 s after the write: the battery was still at 0 W
        assert reg.update(500.0, at=11.0) == pytest.approx(470.0)
        # Measured 4 s after: the battery delivered 470 W
        assert reg.update(40.0, at=14.0) == pytest.approx(480.0)

    def test_clamped_to_power_limits(self):
        reg = _reg()
        assert reg.update(5000.0, at=0.0) == 1450.0
        assert reg.update(-5000.0, at=0.0) == -1450.0

    def test_no_discharge_at_min_soc(self):
        reg = _reg(min_soc=10.0)
        reg.soc = 10.0
        assert reg.update(500.0, at=0.0) == 0.0
        assert reg.update(-500.0, at=0.0) == pytest.approx(-530.0)

    def test_no_charge_at_max_soc(self):
        reg = _reg(max_soc=100.0)
        reg.soc = 100.0
        assert reg.update(-500.0, at=0.0) == 0.0

    def test_resync_restarts_from_given_power(self):
        reg = _reg()
        reg.accepted(800.0, at=0.0)
        reg.resync(0.0)
        assert reg.applied(at=100.0) == 0.0


class TestBatteryLimits:

    def test_free_battery(self):
        lim = battery_limits(1450, 1200, soc=50, min_soc=10, max_soc=100)
        assert (lim.max_discharge, lim.max_charge) == (1450, 1200)

    def test_empty_battery_cannot_discharge(self):
        lim = battery_limits(1450, 1200, soc=10, min_soc=10, max_soc=100)
        assert (lim.max_discharge, lim.max_charge) == (0, 1200)

    def test_full_battery_cannot_charge(self):
        lim = battery_limits(1450, 1200, soc=100, min_soc=10, max_soc=100)
        assert (lim.max_discharge, lim.max_charge) == (1450, 0)

    def test_discharge_forbidden(self):
        """Heures creuses / pré-charge : pas de décharge quel que soit le SOC."""
        lim = battery_limits(1450, 1200, soc=80, min_soc=10, max_soc=100, allow_discharge=False)
        assert lim.max_discharge == 0

    def test_unknown_soc_no_soc_limit(self):
        lim = battery_limits(1450, 1200, soc=None, min_soc=10, max_soc=100)
        assert (lim.max_discharge, lim.max_charge) == (1450, 1200)


class TestSplitSetpoint:

    def test_even_split(self):
        """Toutes les batteries libres : consigne / N."""
        limits = [BatteryLimits(1450, 1450), BatteryLimits(1450, 1450)]
        assert split_setpoint(1000.0, limits) == [500.0, 500.0]
        assert split_setpoint(-600.0, limits) == [-300.0, -300.0]

    def test_empty_battery_share_goes_to_the_other(self):
        """Sinon la régulation ne convergerait pas : la base resterait à la part de B."""
        limits = [BatteryLimits(0, 1450), BatteryLimits(1450, 1450)]
        assert split_setpoint(1000.0, limits) == [0.0, 1000.0]

    def test_power_limit_overflow_redistributed(self):
        limits = [BatteryLimits(300, 300), BatteryLimits(1450, 1450), BatteryLimits(1450, 1450)]
        parts = split_setpoint(1500.0, limits)
        assert parts[0] == 300.0
        assert parts[1] == pytest.approx(600.0)
        assert parts[2] == pytest.approx(600.0)
        assert sum(parts) == pytest.approx(1500.0)

    def test_all_saturated(self):
        limits = [BatteryLimits(500, 500), BatteryLimits(500, 500)]
        assert split_setpoint(3000.0, limits) == [500.0, 500.0]

    def test_no_battery(self):
        assert split_setpoint(500.0, []) == []

    def test_converges_with_an_empty_battery(self):
        """Boucle régulateur + répartition : la batterie libre finit par tout compenser."""
        reg = _reg(max_discharge=1450.0, target=0.0, settle=0.0)
        limits = [BatteryLimits(0, 1450), BatteryLimits(1450, 1450)]
        load = 1000.0
        applied = 0.0
        for t in range(5):
            total = reg.update(load - applied, at=float(t))
            applied = sum(split_setpoint(total, limits))
            reg.accepted(applied, at=float(t))
        assert applied == pytest.approx(1000.0)


class TestPrechargeSetpoint:

    def test_full_power(self):
        assert precharge_setpoint(grid_w=300, base_w=0, max_charge_w=1450, subscription_w=9000) == -1450

    def test_capped_by_subscription(self):
        """Maison à 300 W, abonnement 1500 W : on ne charge qu'à 1200 W."""
        assert precharge_setpoint(grid_w=300, base_w=0, max_charge_w=1450, subscription_w=1500) == -1200

    def test_house_measured_while_charging(self):
        """Réseau mesuré pendant une charge de 1000 W : la maison seule consomme 1300 − 1000."""
        assert precharge_setpoint(grid_w=1300, base_w=-1000, max_charge_w=1450, subscription_w=1500) == -1200

    def test_house_above_subscription(self):
        assert precharge_setpoint(grid_w=2000, base_w=0, max_charge_w=1450, subscription_w=1500) == 0
