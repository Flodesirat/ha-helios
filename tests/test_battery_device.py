"""Tests pour BatteryDevice — urgence, power_w, fit, score_effectif."""
from __future__ import annotations

import pytest

from custom_components.helios.managed_device import BatteryDevice
from custom_components.helios.const import (
    CONF_BATTERY_PRIORITY,
    CONF_BATTERY_SOC_MIN,
    CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_BATTERY_MAX_CHARGE_POWER_W,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _bat(
    soc_min: float = 20.0,
    soc_max: float = 95.0,
    soc_min_rouge: float = 80.0,
    charge_max_w: float = 3000.0,
    priority: int = 7,
    manual_entity: str | None = None,
) -> BatteryDevice:
    cfg = {
        CONF_BATTERY_SOC_MIN:            soc_min,
        CONF_BATTERY_SOC_MAX:            soc_max,
        CONF_BATTERY_SOC_RESERVE_ROUGE:  soc_min_rouge,
        CONF_BATTERY_MAX_CHARGE_POWER_W: charge_max_w,
        CONF_BATTERY_PRIORITY:           priority,
    }
    return BatteryDevice(cfg, manual_entity=manual_entity)


# ---------------------------------------------------------------------------
# Urgence
# ---------------------------------------------------------------------------

class TestUrgency:

    def test_urgency_below_soc_min_jour(self):
        """SOC < soc_min_jour → urgency == 1.0."""
        bat = _bat(soc_min=20.0, soc_max=95.0)
        bat.update(soc=15.0, tempo_red=False)
        assert bat.urgency == pytest.approx(1.0)

    def test_urgency_above_soc_max(self):
        """SOC ≥ soc_max → urgency == 0.0."""
        bat = _bat(soc_min=20.0, soc_max=95.0)
        bat.update(soc=95.0, tempo_red=False)
        assert bat.urgency == pytest.approx(0.0)

    def test_urgency_at_soc_max_plus_one(self):
        """SOC > soc_max → urgency == 0.0."""
        bat = _bat(soc_max=95.0)
        bat.update(soc=100.0, tempo_red=False)
        assert bat.urgency == pytest.approx(0.0)

    def test_urgency_linear_between(self):
        """Rampe linéaire entre soc_min_jour (20) et soc_max (100) : midpoint → 0.5."""
        bat = _bat(soc_min=20.0, soc_max=100.0)
        bat.update(soc=60.0, tempo_red=False)
        # urgency = (100 - 60) / (100 - 20) = 40/80 = 0.5
        assert bat.urgency == pytest.approx(0.5)

    def test_urgency_at_soc_min_exactly(self):
        """SOC == soc_min_jour (limite basse) → urgency == 1.0."""
        bat = _bat(soc_min=20.0, soc_max=95.0)
        bat.update(soc=20.0, tempo_red=False)
        assert bat.urgency == pytest.approx(1.0)

    def test_urgency_soc_min_jour_blue(self):
        """Jour bleu → soc_min_jour utilise soc_min normal."""
        bat = _bat(soc_min=20.0, soc_min_rouge=80.0)
        bat.update(soc=10.0, tempo_red=False)
        assert bat.soc_min_jour == pytest.approx(20.0)
        assert bat.urgency == pytest.approx(1.0)

    def test_urgency_soc_min_jour_rouge(self):
        """Jour rouge → soc_min_jour utilise soc_min_rouge (plus élevé)."""
        bat = _bat(soc_min=20.0, soc_min_rouge=80.0, soc_max=95.0)
        bat.update(soc=50.0, tempo_red=True)
        # soc_min_jour = 80 ; SOC=50 < 80 → urgency = 1.0
        assert bat.soc_min_jour == pytest.approx(80.0)
        assert bat.urgency == pytest.approx(1.0)

    def test_urgency_soc_above_soc_min_rouge_on_red_day(self):
        """Jour rouge, SOC entre soc_min_rouge et soc_max → urgency < 1.0."""
        bat = _bat(soc_min=20.0, soc_min_rouge=80.0, soc_max=100.0)
        bat.update(soc=90.0, tempo_red=True)
        # soc_min_jour = 80 ; urgency = (100 - 90) / (100 - 80) = 10/20 = 0.5
        assert bat.urgency == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# power_w
# ---------------------------------------------------------------------------

class TestPowerW:

    def test_power_w_at_soc_min(self):
        """SOC = soc_min_jour → power_w == charge_max_w."""
        bat = _bat(soc_min=20.0, soc_max=95.0, charge_max_w=3000.0)
        bat.update(soc=20.0, tempo_red=False)
        assert bat.power_w == pytest.approx(3000.0)

    def test_power_w_at_soc_max(self):
        """SOC = soc_max → power_w == 0.0."""
        bat = _bat(soc_min=20.0, soc_max=95.0, charge_max_w=3000.0)
        bat.update(soc=95.0, tempo_red=False)
        assert bat.power_w == pytest.approx(0.0)

    def test_power_w_linear_between(self):
        """Rampe linéaire entre soc_min_jour (20) et soc_max (100)."""
        bat = _bat(soc_min=20.0, soc_max=100.0, charge_max_w=2000.0)
        bat.update(soc=60.0, tempo_red=False)
        # power_w = 2000 * (100 - 60) / (100 - 20) = 2000 * 40/80 = 1000
        assert bat.power_w == pytest.approx(1000.0)

    def test_power_w_below_soc_min(self):
        """SOC < soc_min_jour → power_w == charge_max_w (demande maximale)."""
        bat = _bat(soc_min=20.0, soc_max=95.0, charge_max_w=3000.0)
        bat.update(soc=5.0, tempo_red=False)
        assert bat.power_w == pytest.approx(3000.0)

    def test_power_w_decreases_with_soc(self):
        """power_w décroît à mesure que le SOC monte."""
        bat = _bat(soc_min=20.0, soc_max=100.0, charge_max_w=2000.0)
        socs = [20.0, 40.0, 60.0, 80.0, 100.0]
        powers = []
        for soc in socs:
            bat.update(soc=soc, tempo_red=False)
            powers.append(bat.power_w)
        for i in range(len(powers) - 1):
            assert powers[i] >= powers[i + 1], (
                f"power_w({socs[i]}) < power_w({socs[i+1]}): {powers[i]:.0f} < {powers[i+1]:.0f}"
            )


# ---------------------------------------------------------------------------
# fit et satisfied
# ---------------------------------------------------------------------------

class TestFitAndSatisfied:

    def test_fit_always_one(self):
        """fit == 1.0 quelle que soit la situation."""
        bat = _bat()
        for soc, red in [(10.0, False), (50.0, False), (90.0, True), (95.0, False)]:
            bat.update(soc=soc, tempo_red=red)
            assert bat.fit == pytest.approx(1.0), f"fit != 1.0 pour SOC={soc}, red={red}"

    def test_satisfied_above_soc_max(self):
        """SOC ≥ soc_max → satisfied == True."""
        bat = _bat(soc_max=95.0)
        bat.update(soc=95.0, tempo_red=False)
        assert bat.satisfied is True

    def test_satisfied_at_soc_above_max(self):
        """SOC > soc_max → satisfied == True."""
        bat = _bat(soc_max=95.0)
        bat.update(soc=100.0, tempo_red=False)
        assert bat.satisfied is True

    def test_not_satisfied_below_soc_max(self):
        """SOC < soc_max → satisfied == False."""
        bat = _bat(soc_max=95.0)
        bat.update(soc=80.0, tempo_red=False)
        assert bat.satisfied is False


# ---------------------------------------------------------------------------
# effective_score
# ---------------------------------------------------------------------------

class TestEffectiveScore:

    def test_effective_score_formula(self):
        """effective_score = 0.4×priority/10 + 0.3×1.0 + 0.3×urgency."""
        bat = _bat(soc_min=20.0, soc_max=100.0, priority=5)
        bat.update(soc=60.0, tempo_red=False)
        urgency = bat.urgency  # (100-60)/(100-20) = 0.5
        expected = 0.4 * (5 / 10.0) + 0.3 * 1.0 + 0.3 * urgency
        assert bat.effective_score == pytest.approx(expected)

    def test_effective_score_max_urgency(self):
        """Urgence maximale → effective_score inclut 0.3×1.0 de l'urgence."""
        bat = _bat(soc_min=20.0, soc_max=95.0, priority=7)
        bat.update(soc=10.0, tempo_red=False)  # soc < soc_min → urgency=1.0
        expected = 0.4 * 0.7 + 0.3 * 1.0 + 0.3 * 1.0
        assert bat.effective_score == pytest.approx(expected)

    def test_effective_score_zero_urgency(self):
        """Urgence nulle (SOC == soc_max) → effective_score = 0.4×pri/10 + 0.3."""
        bat = _bat(soc_max=95.0, priority=7)
        bat.update(soc=95.0, tempo_red=False)  # urgency=0.0
        expected = 0.4 * 0.7 + 0.3 * 1.0 + 0.3 * 0.0
        assert bat.effective_score == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Mode manuel
# ---------------------------------------------------------------------------

class TestManualMode:

    def test_not_manual_by_default(self):
        """is_manual=False par défaut (manual_mode=False, pas d'entité)."""
        bat = _bat()
        reader = lambda entity: None
        assert bat.is_manual(reader) is False

    def test_manual_mode_flag(self):
        """manual_mode=True → is_manual=True quel que soit le reader."""
        bat = _bat()
        bat.manual_mode = True
        reader = lambda entity: None
        assert bat.is_manual(reader) is True

    def test_manual_entity_on(self):
        """Switch manuel à 'on' → is_manual=True."""
        bat = _bat(manual_entity="switch.bat_manual")
        reader = lambda entity: "on"
        assert bat.is_manual(reader) is True

    def test_manual_entity_off(self):
        """Switch manuel à 'off' → is_manual=False."""
        bat = _bat(manual_entity="switch.bat_manual")
        reader = lambda entity: "off"
        assert bat.is_manual(reader) is False

    def test_manual_entity_unavailable(self):
        """Switch manuel indisponible → is_manual=False (fail-safe)."""
        bat = _bat(manual_entity="switch.bat_manual")
        reader = lambda entity: "unavailable"
        assert bat.is_manual(reader) is False
