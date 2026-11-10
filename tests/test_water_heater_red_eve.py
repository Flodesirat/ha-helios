"""Chauffe-eau : préchauffage maximal la veille d'un jour rouge (#3).

La veille d'un jour rouge, en heures creuses, la température à atteindre est
la cible de journée (wh_temp_target) au lieu du minimum HC.
"""
from __future__ import annotations

import datetime as dt_mod
from collections import deque
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.battery_drive import precharge_setpoint
from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY, CONF_DEVICE_POWER_W,
    CONF_DEVICE_MIN_ON_MINUTES, CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN, CONF_WH_TEMP_MIN_ENTITY,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
)

TEMP_ENTITY = "sensor.wh_temp"
MIN_ENTITY  = "sensor.wh_min_temp"
SWITCH      = "switch.chauffe_eau"
TARGET      = 60.0   # cible de journée
LEGIONELLA  = 45.0   # plancher statique
HC_MIN      = 50.0   # minimum HC (entité)


def _device(with_min_entity: bool = True) -> ManagedDevice:
    cfg = {
        CONF_DEVICE_NAME:           "Chauffe-eau",
        CONF_DEVICE_TYPE:           DEVICE_TYPE_WATER_HEATER,
        CONF_DEVICE_SWITCH_ENTITY:  SWITCH,
        CONF_DEVICE_POWER_W:        2000,
        CONF_DEVICE_MIN_ON_MINUTES: 0,
        CONF_DEVICE_ALLOWED_START:  "08:00",
        CONF_DEVICE_ALLOWED_END:    "20:00",
        CONF_WH_TEMP_ENTITY:        TEMP_ENTITY,
        CONF_WH_TEMP_TARGET:        TARGET,
        CONF_WH_TEMP_MIN:           LEGIONELLA,
    }
    if with_min_entity:
        cfg[CONF_WH_TEMP_MIN_ENTITY] = MIN_ENTITY
    return ManagedDevice(cfg, {CONF_OFF_PEAK_1_START: "22:00", CONF_OFF_PEAK_1_END: "06:00"})


def _reader(temp: float, hc_min: float = HC_MIN):
    states = {TEMP_ENTITY: str(temp), MIN_ENTITY: str(hc_min)}
    return lambda eid: states.get(eid)


def _at(hour: int, minute: int = 0) -> dt_mod.datetime:
    return dt_mod.datetime(2026, 11, 10, hour, minute)


# ---------------------------------------------------------------------------
# Logique du chauffe-eau
# ---------------------------------------------------------------------------

class TestRedEveTarget:

    def test_red_eve_off_peak_forces_up_to_day_target(self):
        """Veille de rouge en HC : forcé tant que temp < cible journée − hystérésis."""
        device = _device()
        device.tempo_next_red = True
        reader = _reader(temp=55.0)  # 55 < 60 − 3, mais ≥ minimum HC 50
        assert device.must_run_now(reader, now=_at(23)) is True
        assert device.is_satisfied(reader, now=_at(23)) is False

    def test_red_eve_off_peak_heats_until_day_target(self):
        """Une fois démarré, chauffe jusqu'à la cible de journée."""
        device = _device()
        device.tempo_next_red = True
        assert device.is_satisfied(_reader(temp=59.0), now=_at(3)) is False
        assert device.is_satisfied(_reader(temp=60.0), now=_at(3)) is True

    def test_not_red_eve_unchanged(self):
        """Lendemain non rouge : minimum HC habituel."""
        device = _device()
        device.tempo_next_red = False
        reader = _reader(temp=55.0)
        assert device.must_run_now(reader, now=_at(23)) is False
        assert device.is_satisfied(reader, now=_at(23)) is True

    def test_static_min_replaced_without_entity(self):
        """Sans entité de minimum HC, la cible de journée remplace le minimum statique."""
        device = _device(with_min_entity=False)
        device.tempo_next_red = True
        assert device.must_run_now(_reader(temp=50.0), now=_at(23)) is True

    def test_hc_min_above_day_target_kept(self):
        """Un minimum HC plus haut que la cible de journée n'est pas abaissé."""
        device = _device()
        device.tempo_next_red = True
        assert device.is_satisfied(_reader(temp=61.0, hc_min=62.0), now=_at(23)) is False

    @pytest.mark.parametrize("temp", [40.0, 52.0, 58.0])
    def test_on_peak_unchanged(self, temp):
        """Heures pleines : la veille de rouge ne change rien (surplus + légionelle)."""
        reader = _reader(temp=temp)
        ref = _device()
        eve = _device()
        eve.tempo_next_red = True
        assert eve.urgency_modifier(reader, now=_at(14)) == ref.urgency_modifier(reader, now=_at(14))
        assert eve.is_satisfied(reader, now=_at(14)) == ref.is_satisfied(reader, now=_at(14))

    def test_legionella_floor_kept_on_peak(self):
        device = _device()
        device.tempo_next_red = True
        assert device.must_run_now(_reader(temp=40.0), now=_at(14)) is True


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _manager(devices: list) -> DeviceManager:
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = 5.0
    mgr.decision_log = deque(maxlen=500)
    mgr._coordinator = None
    mgr._unsub_ready_listeners = []
    mgr.battery_device = None
    mgr.remaining_w = 0.0
    return mgr


def _hass(temp: float) -> MagicMock:
    hass = MagicMock()
    hass.services = AsyncMock()
    states = {TEMP_ENTITY: str(temp), MIN_ENTITY: str(HC_MIN)}

    def _get(entity_id):
        if entity_id not in states:
            return None
        s = MagicMock()
        s.state = states[entity_id]
        return s

    hass.states.get.side_effect = _get
    return hass


def _mock_datetime(hour: int):
    mock = MagicMock()
    mock.now.return_value.time.return_value = time(hour, 0)
    mock.combine.return_value = _at(hour)
    return mock


def _score_input(tempo_next_color: str | None) -> dict:
    return {
        "global_score": 0.0, "surplus_w": 0.0, "bat_available_w": 0.0,
        "battery_soc": None, "battery_available": False,
        "tempo_color": "white", "tempo_next_color": tempo_next_color,
    }


class TestDispatchRedEve:

    @pytest.mark.asyncio
    async def test_red_eve_night_forces_heater(self):
        device = _device()
        mgr = _manager([device])
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(23)):
            await mgr.async_dispatch(_hass(55.0), _score_input("red"))
        assert device.tempo_next_red is True
        assert device.is_on is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("next_color", ["blue", "white", None])
    async def test_other_nights_unchanged(self, next_color):
        device = _device()
        mgr = _manager([device])
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(23)):
            await mgr.async_dispatch(_hass(55.0), _score_input(next_color))
        assert device.tempo_next_red is False
        assert device.is_on is False

    @pytest.mark.asyncio
    async def test_red_eve_stops_at_day_target(self):
        device = _device()
        device.is_on = True
        mgr = _manager([device])
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(4)):
            await mgr.async_dispatch(_hass(60.0), _score_input("red"))
        assert device.is_on is False


# ---------------------------------------------------------------------------
# Même nuit que la pré-charge batterie : abonnement respecté
# ---------------------------------------------------------------------------

def test_precharge_leaves_room_for_heater():
    """Chauffe-eau 2 kW + maison 500 W, abonnement 6 kW : la batterie ne prend que le reste."""
    house_with_heater_w = 2500.0
    setpoint = precharge_setpoint(
        grid_w=house_with_heater_w, base_w=0.0, max_charge_w=5000.0, subscription_w=6000.0,
    )
    assert setpoint == -3500.0
    assert house_with_heater_w - setpoint <= 6000.0
