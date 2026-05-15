"""Tests pour le comportement batterie indisponible (battery_available=False).

Couvre :
- BatteryDevice : urgency=0, power_w=0, satisfied=True quand available=False
- BatteryDevice : retour à la normale après update(..., available=True)
- _async_sample_sensors : buffer non alimenté quand l'entité SOC est unknown/unavailable
- _read_sensors : battery_available=False quand SOC non numérique
- _read_sensors : battery_power_w=None quand batterie indisponible
- Dispatch : BatteryDevice exclue du greedy quand unavailable
- Dispatch : battery_available=False propagé correctement via score_input
"""
from __future__ import annotations

import datetime as dt_mod
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.coordinator import EnergyOptimizerCoordinator
from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import BatteryDevice, ManagedDevice
from custom_components.helios.const import (
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_PRIORITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_SOC_MAX,
    CONF_BATTERY_SOC_MIN,
    CONF_BATTERY_SOC_RESERVE_ROUGE,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END,
    CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY, CONF_PV_POWER_ENTITY,
    CONF_SAMPLE_INTERVAL_SECONDS, CONF_SCAN_INTERVAL_MINUTES,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_MIN, CONF_WH_TEMP_TARGET,
    DEFAULT_SAMPLE_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_WATER_HEATER,
)


# ---------------------------------------------------------------------------
# Helpers partagés
# ---------------------------------------------------------------------------

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


def _make_coord(cfg: dict | None = None) -> MagicMock:
    coord = MagicMock(spec=EnergyOptimizerCoordinator)
    coord._cfg = cfg or {
        CONF_PV_POWER_ENTITY:         "sensor.pv",
        CONF_GRID_POWER_ENTITY:       "sensor.grid",
        CONF_HOUSE_POWER_ENTITY:      "sensor.house",
        CONF_BATTERY_ENABLED:         False,
        CONF_SCAN_INTERVAL_MINUTES:   DEFAULT_SCAN_INTERVAL,
        CONF_SAMPLE_INTERVAL_SECONDS: DEFAULT_SAMPLE_INTERVAL_SECONDS,
    }
    coord._buf_mean = EnergyOptimizerCoordinator._buf_mean
    coord._buf_pv      = deque(maxlen=10)
    coord._buf_house   = deque(maxlen=10)
    coord._buf_grid    = deque(maxlen=10)
    coord._buf_battery = deque(maxlen=10)
    coord._buf_devices = {}
    coord.device_manager = MagicMock()
    coord.device_manager.devices = []
    coord._energy_pv_kwh          = 0.0
    coord._energy_import_kwh      = 0.0
    coord._energy_export_kwh      = 0.0
    coord._energy_consumption_kwh = 0.0
    coord._savings_eur            = 0.0
    coord._savings_month_eur      = 0.0
    coord._savings_total_eur      = 0.0
    coord.tempo_color  = None
    return coord


def _hass_state(entity_id: str, value: str) -> MagicMock:
    """Retourne un mock d'état HA avec la valeur donnée."""
    s = MagicMock()
    s.state = value
    return s


def _make_hass(states: dict[str, str]) -> MagicMock:
    """hass mock : entity_id → raw string state, None si absent."""
    hass = MagicMock()
    hass.services = AsyncMock()

    def _get(entity_id):
        val = states.get(entity_id)
        if val is None:
            return None
        s = MagicMock()
        s.state = val
        return s

    hass.states.get.side_effect = _get
    return hass


def _hass_with_states(numeric: dict[str, float], unknown: list[str] | None = None) -> MagicMock:
    """hass mock : valeurs numériques + liste d'entités à mettre en 'unknown'."""
    hass = MagicMock()

    def _get(entity_id):
        s = MagicMock()
        if entity_id in (unknown or []):
            s.state = "unknown"
            return s
        if entity_id in numeric:
            s.state = str(numeric[entity_id])
            return s
        s.state = "unavailable"
        return s

    hass.states.get.side_effect = _get
    return hass


def _make_wh_device(
    power_w: float = 2000.0,
    priority: int = 5,
    temp_target: float = 61.0,
    temp_min: float = 10.0,
) -> ManagedDevice:
    return ManagedDevice({
        CONF_DEVICE_NAME:           "ChauffeEau",
        CONF_DEVICE_TYPE:           DEVICE_TYPE_WATER_HEATER,
        CONF_DEVICE_SWITCH_ENTITY:  "switch.wh",
        CONF_DEVICE_POWER_W:        power_w,
        CONF_DEVICE_PRIORITY:       priority,
        CONF_WH_TEMP_ENTITY:        "sensor.wh_temp",
        CONF_WH_TEMP_TARGET:        temp_target,
        CONF_WH_TEMP_MIN:           temp_min,
        CONF_DEVICE_MIN_ON_MINUTES: 0,
        CONF_DEVICE_ALLOWED_START:  "00:00",
        CONF_DEVICE_ALLOWED_END:    "23:59",
    })


def _make_manager(devices: list, battery_device: BatteryDevice | None = None) -> DeviceManager:
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = devices
    mgr._store = store
    mgr._scan_interval = 5.0
    mgr.decision_log = deque(maxlen=500)
    mgr._coordinator = None
    mgr._unsub_ready_listeners = []
    mgr.battery_device = battery_device
    mgr.remaining_w = 0.0
    return mgr


def _score_input(
    surplus_w: float = 1000.0,
    bat_available_w: float = 0.0,
    battery_soc: float | None = None,
    battery_available: bool = True,
    tempo_color: str = "blue",
    global_score: float = 0.8,
    grid_allowance_w: float = 0.0,
    soc_max: float = 95.0,
    soc_min: float = 20.0,
    soc_reserve_rouge: float = 80.0,
) -> dict:
    return {
        "global_score":      global_score,
        "surplus_w":         surplus_w,
        "bat_available_w":   bat_available_w,
        "battery_soc":       battery_soc,
        "battery_available": battery_available,
        "tempo_color":       tempo_color,
        "soc_max":           soc_max,
        "soc_min":           soc_min,
        "soc_reserve_rouge": soc_reserve_rouge,
        "grid_allowance_w":  grid_allowance_w,
    }


def _mock_datetime(hour: int = 12):
    mock = MagicMock()
    mock.now.return_value.time.return_value = dt_mod.time(hour, 0)
    mock.combine.return_value = dt_mod.datetime(2024, 6, 1, hour, 0)
    return mock


# ---------------------------------------------------------------------------
# BatteryDevice — comportement quand available=False
# ---------------------------------------------------------------------------

class TestBatteryDeviceUnavailable:

    def test_urgency_zero_when_unavailable(self):
        """available=False → urgency=0.0 quelle que soit le SOC."""
        bat = _bat_device(soc_min=20.0, soc_max=95.0)
        bat.update(soc=10.0, tempo_red=False, available=False)
        assert bat.urgency == pytest.approx(0.0)

    def test_power_w_zero_when_unavailable(self):
        """available=False → power_w=0.0 (aucune demande de charge)."""
        bat = _bat_device(charge_max_w=3000.0)
        bat.update(soc=5.0, tempo_red=False, available=False)
        assert bat.power_w == pytest.approx(0.0)

    def test_satisfied_true_when_unavailable(self):
        """available=False → satisfied=True → exclue du dispatch."""
        bat = _bat_device(soc_min=20.0, soc_max=95.0)
        bat.update(soc=10.0, tempo_red=False, available=False)
        assert bat.satisfied is True

    def test_urgency_red_day_still_zero_when_unavailable(self):
        """Jour rouge + SOC bas + available=False → urgency reste à 0.0."""
        bat = _bat_device(soc_min=20.0, soc_min_rouge=80.0, soc_max=95.0)
        bat.update(soc=50.0, tempo_red=True, available=False)
        assert bat.urgency == pytest.approx(0.0)
        assert bat.power_w == pytest.approx(0.0)

    def test_recovery_after_unavailable(self):
        """Après une indispo, update(..., available=True) restaure le comportement normal."""
        bat = _bat_device(soc_min=20.0, soc_max=95.0, charge_max_w=2000.0)

        # Batterie plantée
        bat.update(soc=50.0, tempo_red=False, available=False)
        assert bat.urgency == pytest.approx(0.0)
        assert bat.power_w == pytest.approx(0.0)
        assert bat.satisfied is True

        # Batterie revenue
        bat.update(soc=50.0, tempo_red=False, available=True)
        # urgency = (95-50)/(95-20) = 45/75 = 0.6
        assert bat.urgency == pytest.approx(45 / 75)
        assert bat.power_w > 0.0
        assert bat.satisfied is False

    def test_available_default_is_true(self):
        """Par défaut (available non fourni) le comportement est normal."""
        bat = _bat_device(soc_min=20.0, soc_max=95.0)
        bat.update(soc=50.0, tempo_red=False)  # available non passé
        assert bat.urgency > 0.0
        assert bat.power_w > 0.0
        assert bat.satisfied is False


# ---------------------------------------------------------------------------
# _async_sample_sensors — buffer batterie ignoré quand SOC indisponible
# ---------------------------------------------------------------------------

class TestSampleSensorsBatteryUnavailable:

    @pytest.mark.asyncio
    async def test_buffer_skipped_when_soc_unknown(self):
        """Quand l'entité SOC est 'unknown', le buffer batterie n'est pas alimenté."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0,
                     "sensor.house": 800.0, "sensor.bat_power": -500.0},
            unknown=["sensor.bat_soc"],
        )

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_battery) == [], "Buffer doit rester vide si SOC est 'unknown'"

    @pytest.mark.asyncio
    async def test_buffer_skipped_when_soc_unavailable(self):
        """Quand l'entité SOC est 'unavailable', le buffer batterie n'est pas alimenté."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        # sensor.bat_soc absent → "unavailable" via _hass_with_states
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0,
                     "sensor.house": 800.0, "sensor.bat_power": -500.0},
        )

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_battery) == [], "Buffer doit rester vide si SOC est 'unavailable'"

    @pytest.mark.asyncio
    async def test_buffer_fed_when_soc_numeric(self):
        """Quand le SOC est numérique, le buffer est alimenté normalement."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0,
                     "sensor.house": 800.0, "sensor.bat_soc": 60.0,
                     "sensor.bat_power": -500.0},
        )

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_battery) == [-500.0]

    @pytest.mark.asyncio
    async def test_buffer_not_polluted_across_cycles(self):
        """Un cycle normal puis un cycle en 'unknown' : le buffer garde la valeur valide."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        # Premier cycle : SOC disponible
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0,
                     "sensor.house": 800.0, "sensor.bat_soc": 60.0,
                     "sensor.bat_power": -500.0},
        )
        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)
        assert list(coord._buf_battery) == [-500.0]

        # Deuxième cycle : SOC inconnu (batterie plantée)
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0,
                     "sensor.house": 800.0, "sensor.bat_power": -500.0},
            unknown=["sensor.bat_soc"],
        )
        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        # Le buffer ne doit contenir que la valeur du premier cycle
        assert list(coord._buf_battery) == [-500.0], \
            "Le buffer ne doit pas être pollué par un cycle en 'unknown'"


# ---------------------------------------------------------------------------
# _read_sensors — battery_available et battery_power_w
# ---------------------------------------------------------------------------

class TestReadSensorsBatteryAvailable:

    @pytest.mark.asyncio
    async def test_battery_available_false_when_soc_unknown(self):
        """battery_available=False quand l'entité SOC retourne 'unknown'."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0, "sensor.house": 800.0},
            unknown=["sensor.bat_soc"],
        )

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["battery_available"] is False
        assert result["battery_soc"] is None

    @pytest.mark.asyncio
    async def test_battery_available_false_when_soc_unavailable(self):
        """battery_available=False quand l'entité SOC est absente (unavailable)."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0, "sensor.house": 800.0},
            # bat_soc absent → unavailable
        )

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["battery_available"] is False
        assert result["battery_soc"] is None

    @pytest.mark.asyncio
    async def test_battery_available_true_when_soc_numeric(self):
        """battery_available=True quand l'entité SOC retourne une valeur numérique."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0,
                     "sensor.house": 800.0, "sensor.bat_soc": 72.0,
                     "sensor.bat_power": -400.0},
        )

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["battery_available"] is True
        assert result["battery_soc"] == pytest.approx(72.0)

    @pytest.mark.asyncio
    async def test_battery_power_w_none_when_soc_unavailable(self):
        """battery_power_w=None quand le SOC est indisponible (même si le buffer contient des données)."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_SOC_ENTITY:   "sensor.bat_soc",
            CONF_BATTERY_POWER_ENTITY: "sensor.bat_power",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        # Buffer avec de l'historique (SOC était disponible avant)
        coord._buf_battery = deque([-500.0, -520.0], maxlen=10)
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0, "sensor.house": 800.0},
            unknown=["sensor.bat_soc"],
        )

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["battery_power_w"] is None

    @pytest.mark.asyncio
    async def test_battery_available_false_when_battery_disabled(self):
        """battery_available=False quand battery_enabled=False (pas de batterie configurée)."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:    "sensor.pv",
            CONF_GRID_POWER_ENTITY:  "sensor.grid",
            CONF_HOUSE_POWER_ENTITY: "sensor.house",
            CONF_BATTERY_ENABLED:    False,
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states(
            numeric={"sensor.pv": 2000.0, "sensor.grid": 0.0, "sensor.house": 800.0},
        )

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["battery_available"] is False
        assert result["battery_soc"] is None


# ---------------------------------------------------------------------------
# Dispatch — BatteryDevice exclue quand unavailable
# ---------------------------------------------------------------------------

class TestDispatchBatteryUnavailable:

    @pytest.mark.asyncio
    async def test_battery_excluded_from_greedy_when_unavailable(self):
        """BatteryDevice avec available=False → satisfied=True → exclue du greedy."""
        wh = _make_wh_device(power_w=1000, priority=5, temp_target=61.0, temp_min=10.0)
        wh.is_on = False

        bat = _bat_device(soc_min=20.0, soc_max=95.0, charge_max_w=2000.0, priority=7)
        # Batterie plantée : indisponible
        bat.update(soc=None, tempo_red=False, available=False)

        mgr = _make_manager([wh], battery_device=bat)
        hass = _make_hass({"sensor.wh_temp": "55.0"})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0, bat_available_w=0.0,
                             battery_soc=None, battery_available=False),
            )

        assert bat.is_on is False, "BatteryDevice indisponible ne doit pas être sélectionnée"
        assert wh.is_on is True,   "Le WH doit quand même être sélectionné"

    @pytest.mark.asyncio
    async def test_battery_available_after_recovery(self):
        """BatteryDevice retrouve son comportement normal après retour de disponibilité."""
        wh = _make_wh_device(power_w=1000, priority=3, temp_target=61.0, temp_min=10.0)
        wh.is_on = False

        bat = _bat_device(soc_min=20.0, soc_max=95.0, charge_max_w=2000.0, priority=7)

        mgr = _make_manager([wh], battery_device=bat)
        hass = _make_hass({"sensor.wh_temp": "55.0"})

        # Premier dispatch : batterie indisponible
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0, bat_available_w=0.0,
                             battery_soc=None, battery_available=False),
            )
        assert bat.is_on is False

        # Deuxième dispatch : batterie revenue (SOC=60, disponible)
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=5000.0, bat_available_w=0.0,
                             battery_soc=60.0, battery_available=True),
            )
        assert bat.is_on is True, "BatteryDevice doit être sélectionnée après retour de disponibilité"

    @pytest.mark.asyncio
    async def test_battery_available_propagated_to_update(self):
        """battery_available dans score_input est bien transmis à BatteryDevice.update()."""
        bat = _bat_device(soc_min=20.0, soc_max=95.0, charge_max_w=2000.0)
        mgr = _make_manager([], battery_device=bat)
        hass = _make_hass({})

        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(
                hass,
                _score_input(surplus_w=0.0, battery_soc=None, battery_available=False),
            )

        # Après le dispatch, la BatteryDevice doit avoir intégré available=False
        assert bat._available is False
        assert bat.satisfied is True

    @pytest.mark.asyncio
    async def test_dispatch_defaults_battery_available_to_false(self):
        """Sans clé battery_available dans score_input, le défaut est False (fail-safe)."""
        bat = _bat_device(soc_min=20.0, soc_max=95.0, charge_max_w=2000.0)
        mgr = _make_manager([], battery_device=bat)
        hass = _make_hass({})

        score_without_key = {
            "global_score": 0.8, "surplus_w": 5000.0, "bat_available_w": 0.0,
            "battery_soc": 60.0, "tempo_color": "blue",
            "soc_max": 95.0, "soc_min": 20.0, "soc_reserve_rouge": 80.0,
            "grid_allowance_w": 0.0,
            # battery_available intentionnellement absent
        }
        with patch("custom_components.helios.device_manager.datetime", _mock_datetime(12)):
            await mgr.async_dispatch(hass, score_without_key)

        assert bat._available is False, "Défaut manquant → doit être False (fail-safe)"


# ---------------------------------------------------------------------------
# _update_state — défaut False quand battery_available absent
# ---------------------------------------------------------------------------

class TestUpdateStateDefault:

    def test_update_state_defaults_battery_available_to_false(self):
        """Sans clé battery_available dans raw, _update_state fixe battery_available=False."""
        from custom_components.helios.coordinator import EnergyOptimizerCoordinator

        from custom_components.helios.const import CONF_EMA_ENABLED
        coord = MagicMock(spec=EnergyOptimizerCoordinator)
        coord._cfg = {CONF_EMA_ENABLED: False}
        coord.hass = MagicMock()
        coord.battery_soc = None
        coord.battery_available = True  # valeur initiale
        coord._compute_bat_available_w = MagicMock(return_value=0.0)

        raw = {
            "pv_power_w": 0.0, "grid_power_w": 0.0, "house_power_w": 0.0,
            "battery_soc": None, "battery_power_w": None,
            "tempo_color": None, "tempo_next_color": None,
            "forecast_kwh": None,
            # battery_available intentionnellement absent
        }
        EnergyOptimizerCoordinator._update_state(coord, raw)

        assert coord.battery_available is False, "Défaut manquant → doit être False (fail-safe)"
