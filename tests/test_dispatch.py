"""Tests pour l'algorithme de dispatch unifié (Phase 1–4)."""
from __future__ import annotations

import datetime as dt_mod
import time
from collections import deque
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice, BatteryDevice
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
    CONF_BATTERY_SOC_MIN, CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_RESERVE_ROUGE, CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_PRIORITY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WH_TEMP_ENTITY = "sensor.wh_temp"
_WH_SWITCH      = "switch.wh"


def _wh_device(
    name: str = "ChauffeEau",
    power_w: float = 2000.0,
    priority: int = 5,
    temp_target: float = 61.0,
    temp_min: float = 10.0,
    min_on_minutes: int = 0,
    allowed_start: str = "00:00",
    allowed_end: str = "23:59",
    temp_entity: str = _WH_TEMP_ENTITY,
    switch: str = _WH_SWITCH,
) -> ManagedDevice:
    return ManagedDevice(
        {
            CONF_DEVICE_NAME:           name,
            CONF_DEVICE_TYPE:           DEVICE_TYPE_WATER_HEATER,
            CONF_DEVICE_SWITCH_ENTITY:  switch,
            CONF_DEVICE_POWER_W:        power_w,
            CONF_DEVICE_PRIORITY:       priority,
            CONF_WH_TEMP_ENTITY:        temp_entity,
            CONF_WH_TEMP_TARGET:        temp_target,
            CONF_WH_TEMP_MIN:           temp_min,
            CONF_DEVICE_MIN_ON_MINUTES: min_on_minutes,
            CONF_DEVICE_ALLOWED_START:  allowed_start,
            CONF_DEVICE_ALLOWED_END:    allowed_end,
        },
        {},  # no off-peak slots
    )


def _bat_device(
    soc_min: float = 20.0,
    soc_max: float = 95.0,
    soc_min_rouge: float = 80.0,
    charge_max_w: float = 3000.0,
    priority: int = 7,
) -> BatteryDevice:
    return BatteryDevice({
        CONF_BATTERY_SOC_MIN:            soc_min,
        CONF_BATTERY_SOC_MAX:            soc_max,
        CONF_BATTERY_SOC_RESERVE_ROUGE:  soc_min_rouge,
        CONF_BATTERY_MAX_CHARGE_POWER_W: charge_max_w,
        CONF_BATTERY_PRIORITY:           priority,
    })


def _make_manager(
    devices: list,
    battery_device: BatteryDevice | None = None,
    scan_interval: float = 5.0,
) -> DeviceManager:
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()

    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = scan_interval
    mgr.decision_log = deque(maxlen=500)
    mgr._coordinator = None
    mgr._unsub_ready_listeners = []
    mgr.battery_device = battery_device
    mgr.remaining_w = 0.0
    return mgr


def _score_input(
    global_score: float = 0.8,
    surplus_w: float = 1000.0,
    bat_available_w: float = 0.0,
    battery_soc: float | None = None,
    tempo_color: str = "blue",
    soc_max: float = 95.0,
    soc_min: float = 20.0,
    soc_reserve_rouge: float = 80.0,
    grid_allowance_w: float = 250.0,
) -> dict:
    return {
        "global_score":    global_score,
        "surplus_w":       surplus_w,
        "bat_available_w": bat_available_w,
        "battery_soc":     battery_soc,
        "tempo_color":     tempo_color,
        "soc_max":         soc_max,
        "soc_min":         soc_min,
        "soc_reserve_rouge": soc_reserve_rouge,
        "grid_allowance_w": grid_allowance_w,
    }


def _make_hass(states: dict[str, str] | None = None) -> MagicMock:
    """Construit un faux hass.  states : entity_id → raw string state."""
    hass = MagicMock()
    hass.services = AsyncMock()

    def _get(entity_id):
        raw = (states or {}).get(entity_id)
        if raw is None:
            return None
        s = MagicMock()
        s.state = raw
        return s

    hass.states.get.side_effect = _get
    return hass


def _mock_datetime(hour: int = 12, minute: int = 0):
    """Contexte qui patche device_manager.datetime sur l'heure indiquée."""
    mock = MagicMock()
    _now_t = dt_mod.time(hour, minute)
    _now_dt = dt_mod.datetime(2024, 6, 1, hour, minute)
    mock.now.return_value.time.return_value = _now_t
    mock.combine.return_value = _now_dt
    return mock


# ---------------------------------------------------------------------------
# Phase 2 — urgence
# ---------------------------------------------------------------------------

class TestUrgencyForcing:

    @pytest.mark.asyncio
    async def test_urgency_device_forced_regardless_of_budget(self):
        """Urgence = 1.0 → démarrage même si remaining < 0 (budget = 0)."""
        # WH avec temp < temp_min → urgency=1.0
        device = _wh_device(power_w=2000, temp_target=61.0, temp_min=50.0)
        device.is_on = False

        mgr = _make_manager([device])
        # temp=40 < temp_min=50 → urgency=1.0
        hass = _make_hass({_WH_TEMP_ENTITY: "40.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=0.0, bat_available_w=0.0),
            )

        assert device.is_on is True, "Un appareil en urgence doit démarrer même sans budget"

    @pytest.mark.asyncio
    async def test_urgency_device_forced_on_red_day(self):
        """Urgence = 1.0 → démarrage même en jour rouge et sans surplus suffisant.

        La garde red_strict (Phase 3) ne bloque que les nouvelles activations du greedy.
        L'urgence (Phase 2) est prioritaire et contourne cette garde.
        """
        device = _wh_device(power_w=2000, temp_target=61.0, temp_min=50.0)
        device.is_on = False

        mgr = _make_manager([device])
        # temp=40 < temp_min=50 → urgency=1.0
        # surplus=100 < power_w=2000 → red_strict bloquerait en Phase 3
        hass = _make_hass({_WH_TEMP_ENTITY: "40.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(
                    surplus_w=100.0,
                    battery_soc=30.0,
                    tempo_color="red",
                    soc_reserve_rouge=80.0,
                ),
            )

        assert device.is_on is True, "L'urgence doit contourner la garde red_strict"


# ---------------------------------------------------------------------------
# Phase 2 — min_on maintenu
# ---------------------------------------------------------------------------

class TestMinOnMaintained:

    @pytest.mark.asyncio
    async def test_min_on_device_maintained(self):
        """min_on_minutes non écoulé → appareil maintenu ON (obligatoire) malgré budget = 0."""
        device = _wh_device(
            power_w=2000, temp_target=61.0, temp_min=10.0, min_on_minutes=30
        )
        device.is_on = True
        device.turned_on_at = time.time() - 60  # allumé il y a 1 min (< 30 min)

        mgr = _make_manager([device])
        # temp=65 → satisfied ; mais min_on non écoulé → doit rester ON
        hass = _make_hass({_WH_TEMP_ENTITY: "65.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=0.0, bat_available_w=0.0),
            )

        assert device.is_on is True, "min_on non écoulé : l'appareil ne doit pas être éteint"


# ---------------------------------------------------------------------------
# Phase 2 — hors plage horaire ignoré
# ---------------------------------------------------------------------------

class TestOutOfWindowIgnored:

    @pytest.mark.asyncio
    async def test_out_of_window_ignored(self):
        """Appareil hors plage → ignoré, ni allumé ni compté dans le budget."""
        # Plage 10:00–10:30 ; on simule 12:00 → hors plage
        device = _wh_device(
            power_w=2000, temp_target=61.0, temp_min=10.0,
            allowed_start="10:00", allowed_end="10:30",
        )
        device.is_on = False

        mgr = _make_manager([device])
        # temp=40 < temp_min=50 → urgency normale, mais hors plage → ignoré
        hass = _make_hass({_WH_TEMP_ENTITY: "40.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12, 0)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0),  # budget très large
            )

        assert device.is_on is False, "Un appareil hors plage ne doit pas être allumé"


# ---------------------------------------------------------------------------
# Phase 2 — satisfait → éteint
# ---------------------------------------------------------------------------

class TestSatisfiedDeviceOff:

    @pytest.mark.asyncio
    async def test_satisfied_device_off(self):
        """Objectif atteint (temp ≥ cible) → éteint si ON et min_on écoulé."""
        device = _wh_device(
            power_w=2000, temp_target=61.0, temp_min=10.0, min_on_minutes=30
        )
        device.is_on = True
        device.turned_on_at = time.time() - 31 * 60  # 31 min (> min_on)

        mgr = _make_manager([device])
        # temp=65 ≥ temp_target=61 → satisfied
        hass = _make_hass({_WH_TEMP_ENTITY: "65.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0),
            )

        assert device.is_on is False, "Un appareil satisfait doit être éteint"


# ---------------------------------------------------------------------------
# Phase 3 — ordre greedy par score effectif
# ---------------------------------------------------------------------------

class TestGreedyOrderByScore:

    @pytest.mark.asyncio
    async def test_greedy_order_by_score(self):
        """L'appareil au score le plus élevé est sélectionné en premier.

        Device A (priority=9, temp=55) a un score effectif > Device B (priority=3, temp=59).
        Avec un budget pour un seul appareil, A doit être sélectionné.
        """
        # temp=55 → urgency = (61-55)/(61-10) ≈ 0.118
        dev_a = _wh_device(
            name="DeviceA", power_w=1000, priority=9,
            temp_target=61.0, temp_min=10.0,
            switch="switch.a", temp_entity="sensor.temp_a",
        )
        # temp=59 → urgency = (61-59)/(61-10) ≈ 0.039
        dev_b = _wh_device(
            name="DeviceB", power_w=1000, priority=3,
            temp_target=61.0, temp_min=10.0,
            switch="switch.b", temp_entity="sensor.temp_b",
        )
        dev_a.is_on = False
        dev_b.is_on = False

        mgr = _make_manager([dev_a, dev_b])
        hass = _make_hass({
            "sensor.temp_a": "55.0",
            "sensor.temp_b": "59.0",
        })

        # Budget pour un seul appareil de 1000 W ; surplus_pur=1000
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=1000.0, bat_available_w=0.0),
            )

        # A doit être sélectionné (score effectif plus élevé) ; B ne tient pas dans le budget restant
        assert dev_a.is_on is True, "Device A (priority plus élevée) doit être sélectionné en premier"
        assert dev_b.is_on is False, "Device B ne doit pas être sélectionné faute de budget"


# ---------------------------------------------------------------------------
# Phase 3 — recalcul dynamique du fit
# ---------------------------------------------------------------------------

class TestGreedyDynamicFitRecalc:

    @pytest.mark.asyncio
    async def test_greedy_dynamic_fit_recalc(self):
        """Le fit du second appareil est recalculé après allocation du premier.

        Setup : remaining=2000 (surplus=1500, bat=500)
          Device A : power=1000, priority=9 → fit zone1, score élevé → sélectionné en premier
          Device B : power=2000, priority=3 → fit=0.4 avant A ; fit=0.0 après (hors budget)

        Résultat : B.last_fit == 0.0 (recalculé sur remaining=1000, sans grid allowance).
        """
        dev_a = _wh_device(
            name="A", power_w=1000, priority=9,
            temp_target=61.0, temp_min=10.0,
            switch="switch.a", temp_entity="sensor.temp_a",
        )
        dev_b = _wh_device(
            name="B", power_w=2000, priority=3,
            temp_target=61.0, temp_min=10.0,
            switch="switch.b", temp_entity="sensor.temp_b",
        )
        dev_a.is_on = False
        dev_b.is_on = False

        mgr = _make_manager([dev_a, dev_b])
        # temp=55 pour les deux → ni satisfaits, ni urgence
        hass = _make_hass({
            "sensor.temp_a": "55.0",
            "sensor.temp_b": "55.0",
        })

        # surplus=1500, bat=500 → remaining=2000 ; grid_allowance=0 (battery_soc=None)
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=1500.0, bat_available_w=500.0, battery_soc=None, grid_allowance_w=0.0),
            )

        assert dev_a.is_on is True, "Device A (priority élevée) doit être sélectionné"
        assert dev_b.is_on is False, "Device B ne tient pas dans le budget restant"
        # Après allocation de A, remaining=1000 ; compute_fit(2000, 1000, 500, 0)=0.0
        assert dev_b.last_fit == pytest.approx(0.0), (
            "Le fit de B doit être recalculé à 0.0 après allocation de A"
        )


# ---------------------------------------------------------------------------
# Phase 4 — extinction des appareils non sélectionnés
# ---------------------------------------------------------------------------

class TestExtinctionRemovesUnselected:

    @pytest.mark.asyncio
    async def test_extinction_removes_unselected(self):
        """Appareil allumé mais non sélectionné (budget épuisé) → éteint en Phase 4."""
        device = _wh_device(power_w=2000, temp_target=61.0, temp_min=10.0)
        device.is_on = True
        device.turned_on_at = time.time() - 60 * 60  # largement > min_on

        mgr = _make_manager([device])
        # temp=40 → pas satisfait, urgency faible
        hass = _make_hass({_WH_TEMP_ENTITY: "40.0"})

        # Budget = 0 → fit=0 → greedy break → phase 4 éteint l'appareil ON
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=0.0, bat_available_w=0.0),
            )

        assert device.is_on is False, "L'appareil ON sans budget doit être éteint en Phase 4"


# ---------------------------------------------------------------------------
# Phase 1 — remaining exclut grid_allowance_w
# ---------------------------------------------------------------------------

class TestRemainingExcludesGridAllowance:

    @pytest.mark.asyncio
    async def test_remaining_excludes_grid_allowance(self):
        """remaining = surplus_virtuel + bat_available_w, sans grid_allowance_w.

        Même avec grid_allowance_w activé (battery_soc >= soc_max), la tolérance
        réseau n'est PAS ajoutée à remaining — elle n'agit que via le fit (Zone 3).
        """
        mgr = _make_manager([])  # pas d'appareils → remaining non modifié
        hass = _make_hass()

        surplus = 500.0
        bat_avail = 200.0

        # battery_soc=95 >= soc_max=95 → grid_allowance_w = configured (250)
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(
                    surplus_w=surplus,
                    bat_available_w=bat_avail,
                    battery_soc=95.0,
                    soc_max=95.0,
                    grid_allowance_w=250.0,
                ),
            )

        expected_remaining = surplus + bat_avail  # 700, sans les 250 de grid_allowance
        assert mgr.remaining_w == pytest.approx(expected_remaining), (
            f"remaining_w devrait être {expected_remaining} (pas {mgr.remaining_w})"
        )


# ---------------------------------------------------------------------------
# Phase 3 — BatteryDevice dans le greedy
# ---------------------------------------------------------------------------

class TestBatteryDeviceInGreedy:

    @pytest.mark.asyncio
    async def test_battery_device_in_greedy(self):
        """BatteryDevice entre en compétition Phase 3 avec fit=1.0.

        Avec urgency=0.6 (< 1.0 → pas obligatoire en Phase 2), la batterie
        concurrence le WH dans le greedy. Son score effectif plus élevé (fit=1.0)
        garantit qu'elle est sélectionnée.
        """
        # WH : priority=5, temp=55 → urgency≈0.12, power=1000
        wh = _wh_device(power_w=1000, priority=5, temp_target=61.0, temp_min=10.0)
        wh.is_on = False

        # BatteryDevice : priority=7, SOC=50 (entre soc_min=20 et soc_max=95)
        # urgency = (95-50)/(95-20) = 45/75 = 0.6 < 1.0 → Phase 3
        bat = _bat_device(soc_min=20.0, soc_max=95.0, charge_max_w=2000.0, priority=7)
        bat.update(soc=50.0, tempo_red=False)

        mgr = _make_manager([wh], battery_device=bat)
        hass = _make_hass({_WH_TEMP_ENTITY: "55.0"})

        # Surplus large → budget pour les deux
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0, bat_available_w=0.0),
            )

        # La batterie doit être marquée is_on=True (sélectionnée par le greedy)
        assert bat.is_on is True, "BatteryDevice doit être sélectionnée par le greedy"
        assert wh.is_on is True, "Le WH doit aussi être sélectionné (budget suffisant)"


# ---------------------------------------------------------------------------
# Manuel — ignoré du dispatch
# ---------------------------------------------------------------------------

class TestManualDeviceIgnored:

    @pytest.mark.asyncio
    async def test_manual_device_ignored(self):
        """is_manual=True → ignoré du dispatch (ni allumé ni éteint par Helios)."""
        device = _wh_device(power_w=2000, temp_target=61.0, temp_min=50.0)
        device.is_on = False
        device.manual_mode = True  # exclut le device de _helios_manages()

        mgr = _make_manager([device])
        # temp=40 < temp_min=50 → urgency=1.0 si Helios gérait ce device
        hass = _make_hass({_WH_TEMP_ENTITY: "40.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0),
            )

        assert device.is_on is False, "Un appareil en mode manuel ne doit pas être touché par Helios"

    @pytest.mark.asyncio
    async def test_manual_device_on_not_turned_off(self):
        """Appareil manuel déjà ON → Helios ne l'éteint pas non plus (Phase 4)."""
        device = _wh_device(power_w=2000, temp_target=61.0, temp_min=10.0)
        device.is_on = True
        device.manual_mode = True
        device.turned_on_at = time.time() - 3600  # min_on largement écoulé

        mgr = _make_manager([device])
        # temp=65 → satisfied ; mais en mode manuel → Helios doit ignorer
        hass = _make_hass({_WH_TEMP_ENTITY: "65.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=0.0),
            )

        assert device.is_on is True, "Un appareil en mode manuel ON ne doit pas être éteint par Helios"
