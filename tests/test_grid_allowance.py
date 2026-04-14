"""Tests for grid_allowance_w — activation threshold based on configured soc_max.

Comportement attendu :
    - grid_allowance_w s'active quand battery_soc >= soc_max (configurable)
    - Le 96.0 codé en dur ne doit plus être utilisé
    - Si soc_max n'est pas fourni dans score_input, le fallback est 95.0
    - En dessous de soc_max, grid_allowance_w reste à 0 W
"""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wh_device(name="Chauffe-eau", power_w=2000, switch="switch.chauffe_eau") -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME:          name,
        CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
        CONF_DEVICE_SWITCH_ENTITY: switch,
        CONF_DEVICE_POWER_W:       power_w,
        CONF_DEVICE_PRIORITY:      5,
        CONF_WH_TEMP_ENTITY:       "sensor.wh_temp",
        CONF_WH_TEMP_TARGET:       55.0,
        CONF_WH_TEMP_MIN:          45.0,
    })


def _make_manager(devices) -> DeviceManager:
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._dispatch_threshold = 0.3
    mgr._scan_interval = 5
    mgr.decision_log = deque(maxlen=100)
    mgr.remaining_w = 0.0
    mgr._coordinator = None
    mgr._unsub_ready_listeners = []
    mgr.battery_device = None
    return mgr


def _make_hass(wh_temp: float = 50.0) -> MagicMock:
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()

    def _state(entity_id):
        s = MagicMock()
        if "wh_temp" in entity_id:
            s.state = str(wh_temp)
        else:
            s.state = "off"
        return s

    hass.states.get.side_effect = _state
    return hass


def _score_input(
    global_score: float = 0.8,
    surplus_w: float = 500.0,
    bat_available_w: float = 0.0,
    battery_soc: float | None = None,
    grid_allowance_w: float = 300.0,
    soc_max: float | None = None,
) -> dict:
    d = {
        "global_score":       global_score,
        "surplus_w":          surplus_w,
        "bat_available_w":    bat_available_w,
        "battery_soc":        battery_soc,
        "grid_allowance_w":   grid_allowance_w,
        "dispatch_threshold": 0.3,
        "house_power_w":      500.0,
        "pv_power_w":         1000.0,
    }
    if soc_max is not None:
        d["soc_max"] = soc_max
    return d


# ---------------------------------------------------------------------------
# grid_allowance_w activé au bon seuil
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grid_allowance_active_at_soc_max():
    """SOC == soc_max → grid_allowance_w doit être inclus dans le budget."""
    device = _make_wh_device()
    device.power_w = 2300  # > surplus seul, nécessite grid_allowance_w
    mgr = _make_manager([device])
    hass = _make_hass()  # wh_temp=50 : temp > min (pas must_run), temp < target (éligible)

    # surplus=2000, bat=0, grid_allowance=500 → budget total = 2500
    # device.power_w=2300, grid_import=300 < grid_allowance=500 → fit=0.4*(1-300/500)=0.16 → éligible
    await mgr.async_dispatch(hass, _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=95.0,
        grid_allowance_w=500.0,
        soc_max=95.0,
    ))

    assert device.is_on


@pytest.mark.asyncio
async def test_grid_allowance_inactive_below_soc_max():
    """SOC < soc_max → grid_allowance_w doit rester à 0, device ne démarre pas."""
    device = _make_wh_device()
    device.power_w = 2500
    mgr = _make_manager([device])
    hass = _make_hass()

    # surplus=2000, bat=0, grid_allowance désactivé (SOC 94 < soc_max 95)
    # budget = 2000 < 2500 → device ne peut pas démarrer
    await mgr.async_dispatch(hass, _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=94.0,
        grid_allowance_w=500.0,
        soc_max=95.0,
    ))

    assert not device.is_on


@pytest.mark.asyncio
async def test_grid_allowance_active_above_soc_max():
    """SOC > soc_max → grid_allowance_w activé."""
    device = _make_wh_device()
    device.power_w = 2300
    mgr = _make_manager([device])
    hass = _make_hass()

    await mgr.async_dispatch(hass, _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=98.0,
        grid_allowance_w=500.0,
        soc_max=95.0,
    ))

    assert device.is_on


@pytest.mark.asyncio
async def test_grid_allowance_inactive_when_soc_none():
    """SOC inconnu → grid_allowance_w désactivé (pas d'achat réseau par défaut)."""
    device = _make_wh_device()
    device.power_w = 2500
    mgr = _make_manager([device])
    hass = _make_hass()

    await mgr.async_dispatch(hass, _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=None,
        grid_allowance_w=500.0,
        soc_max=95.0,
    ))

    assert not device.is_on


# ---------------------------------------------------------------------------
# Valeur soc_max configurable — le 96.0 codé en dur ne doit plus être utilisé
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_custom_soc_max_90_activates_at_90():
    """soc_max=90 → grid_allowance_w activé à SOC=90, pas à SOC=89."""
    device = _make_wh_device()
    device.power_w = 2300  # grid_import=300 < grid_allowance=500 → fit > 0
    mgr_on  = _make_manager([device])
    hass_on = _make_hass()

    await mgr_on.async_dispatch(hass_on, _score_input(
        surplus_w=2000.0, battery_soc=90.0,
        grid_allowance_w=500.0, soc_max=90.0,
    ))
    assert device.is_on

    # Réinitialiser
    device.is_on = False

    mgr_off  = _make_manager([device])
    hass_off = _make_hass()

    await mgr_off.async_dispatch(hass_off, _score_input(
        surplus_w=2000.0, battery_soc=89.0,
        grid_allowance_w=500.0, soc_max=90.0,
    ))
    assert not device.is_on


@pytest.mark.asyncio
async def test_hardcoded_96_no_longer_used():
    """SOC=96 avec soc_max=97 → grid_allowance_w doit rester désactivé.

    Valide que le seuil 96.0 codé en dur n'est plus utilisé.
    """
    device = _make_wh_device()
    device.power_w = 2500
    mgr = _make_manager([device])
    hass = _make_hass()

    await mgr.async_dispatch(hass, _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=96.0,
        grid_allowance_w=500.0,
        soc_max=97.0,  # seuil configuré à 97, pas 96
    ))

    assert not device.is_on


# ---------------------------------------------------------------------------
# Fallback soc_max=95.0 quand absent du score_input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_soc_max_fallback_activates_at_95():
    """soc_max absent du score_input → fallback 95.0 → activé à SOC=95."""
    device = _make_wh_device()
    device.power_w = 2300  # grid_import=300 < grid_allowance=500 → fit > 0
    mgr = _make_manager([device])
    hass = _make_hass()

    score = _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=95.0,
        grid_allowance_w=500.0,
    )
    score.pop("soc_max", None)  # Pas de soc_max dans score_input

    await mgr.async_dispatch(hass, score)

    assert device.is_on


@pytest.mark.asyncio
async def test_soc_max_fallback_inactive_at_94():
    """soc_max absent → fallback 95.0 → désactivé à SOC=94."""
    device = _make_wh_device()
    device.power_w = 2500
    mgr = _make_manager([device])
    hass = _make_hass()

    score = _score_input(
        surplus_w=2000.0,
        bat_available_w=0.0,
        battery_soc=94.0,
        grid_allowance_w=500.0,
    )
    score.pop("soc_max", None)

    await mgr.async_dispatch(hass, score)

    assert not device.is_on
