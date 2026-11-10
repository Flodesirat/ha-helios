"""Tests for the pool filtration law, wintering, frost protection and red days (#1)."""
from __future__ import annotations

import time
from collections import deque
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.helios.config_flow import _validate_pool
from custom_components.helios.const import (
    DEVICE_TYPE_POOL,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY, CONF_DEVICE_POWER_W,
    CONF_DEVICE_ALLOWED_START, CONF_DEVICE_ALLOWED_END, CONF_DEVICE_MIN_ON_MINUTES,
    CONF_POOL_FILTRATION_ENTITY, CONF_POOL_WATER_TEMP_ENTITY, CONF_POOL_LOCAL_TEMP_ENTITY,
    CONF_POOL_FILTRATION_TABLE, CONF_POOL_WINTERING_ENABLED, CONF_POOL_WINTERING_TEMP,
    CONF_POOL_WINTERING_DURATION_H, CONF_POOL_FROST_TEMP, CONF_POOL_FROST_CONTINUOUS_TEMP,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    DEFAULT_POOL_FILTRATION_TABLE,
    POOL_MODE_AUTO, POOL_MODE_WINTERING, POOL_MODE_FROST, POOL_MODE_MANUAL,
    POOL_ERROR_NO_WATER_TEMP, POOL_ERROR_NO_LOCAL_TEMP,
    TEMPO_RED, TEMPO_BLUE,
)
from custom_components.helios.device_manager import DeviceManager
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.pool_filtration import (
    FROST_OFF, FROST_SESSIONS, FROST_CONTINUOUS,
    parse_filtration_table, is_valid_filtration_table, filtration_hours,
    next_wintering, next_frost_level, frost_wants_on,
)

WATER = "sensor.pool_water"
LOCAL = "sensor.pool_room"
GCFG = {CONF_OFF_PEAK_1_START: "22:00", CONF_OFF_PEAK_1_END: "06:00"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**over) -> dict:
    cfg = {
        CONF_DEVICE_NAME:            "Piscine",
        CONF_DEVICE_TYPE:            DEVICE_TYPE_POOL,
        CONF_DEVICE_SWITCH_ENTITY:   "switch.pompe",
        CONF_DEVICE_POWER_W:         500,
        CONF_DEVICE_MIN_ON_MINUTES:  30,
        CONF_POOL_WATER_TEMP_ENTITY: WATER,
        CONF_POOL_LOCAL_TEMP_ENTITY: LOCAL,
        CONF_POOL_FILTRATION_TABLE:  "10:2, 20:10, 30:24",
        CONF_POOL_WINTERING_ENABLED: True,
        CONF_POOL_WINTERING_TEMP:    12.0,
        CONF_POOL_WINTERING_DURATION_H: 2.0,
        CONF_POOL_FROST_TEMP:        2.0,
        CONF_POOL_FROST_CONTINUOUS_TEMP: -2.0,
    }
    cfg.update(over)
    return cfg


def _reader(states: dict):
    return lambda eid: states.get(eid)


def _device(**over) -> ManagedDevice:
    d = ManagedDevice(_cfg(**over), GCFG)
    d.pool_last_date = date.today()
    return d


def _manager(device: ManagedDevice, states: dict):
    hass = MagicMock()
    hass.services = AsyncMock()
    hass.states.get.side_effect = lambda eid: MagicMock(state=states[eid]) if eid in states else None
    store = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    store.async_save = AsyncMock()
    mgr = DeviceManager.__new__(DeviceManager)
    mgr.devices = [device]
    mgr._store = store
    mgr._scan_interval = 5
    mgr.decision_log = deque(maxlen=500)
    mgr.battery_device = None
    mgr._hass = hass
    return mgr, hass


def _score(surplus_w=0.0, tempo=TEMPO_BLUE) -> dict:
    return {"global_score": 0.1, "surplus_w": surplus_w, "bat_available_w": 0.0, "tempo_color": tempo}


async def _dispatch_at(mgr, hass, now_str: str, score_input: dict):
    now = datetime.combine(date.today(), datetime.strptime(now_str, "%H:%M").time())
    with patch("custom_components.helios.device_manager.datetime") as mock_dm, \
         patch("custom_components.helios.managed_device.datetime") as mock_md:
        mock_dm.now.return_value = now
        mock_dm.combine = datetime.combine
        mock_md.now.return_value = now
        mock_md.combine = datetime.combine
        await mgr.async_dispatch(hass, score_input)


# ---------------------------------------------------------------------------
# Filtration table
# ---------------------------------------------------------------------------

class TestFiltrationTable:
    def test_parse_sorted(self):
        assert parse_filtration_table("20:10, 10:2") == [(10.0, 2.0), (20.0, 10.0)]

    def test_parse_tolerates_units_and_semicolons(self):
        assert parse_filtration_table("10°:2h; 20:10") == [(10.0, 2.0), (20.0, 10.0)]

    def test_default_is_valid(self):
        assert is_valid_filtration_table(DEFAULT_POOL_FILTRATION_TABLE)

    @pytest.mark.parametrize("raw", ["", "10:2", "10-2, 20:4", "10:2, 10:4", "10:2, 20:25", "a:b, 1:2", None])
    def test_invalid(self, raw):
        assert not is_valid_filtration_table(raw)

    def test_interpolation(self):
        table = parse_filtration_table("10:2, 20:10, 30:24")
        assert filtration_hours(table, 15) == pytest.approx(6.0)
        assert filtration_hours(table, 25) == pytest.approx(17.0)
        assert filtration_hours(table, 20) == pytest.approx(10.0)

    def test_clamped_to_ends(self):
        table = parse_filtration_table("10:2, 20:10, 30:24")
        assert filtration_hours(table, 4) == 2.0
        assert filtration_hours(table, 35) == 24.0


# ---------------------------------------------------------------------------
# Wintering / frost rules
# ---------------------------------------------------------------------------

class TestWinteringRule:
    def test_enters_below_threshold(self):
        assert next_wintering(False, 11.9, 12.0) is True
        assert next_wintering(False, 12.0, 12.0) is False

    def test_hysteresis_on_exit(self):
        assert next_wintering(True, 13.5, 12.0) is True   # < 14 → stays
        assert next_wintering(True, 14.0, 12.0) is False


class TestFrostRule:
    def test_levels(self):
        assert next_frost_level(FROST_OFF, 5.0, 2.0, -2.0) == FROST_OFF
        assert next_frost_level(FROST_OFF, 1.5, 2.0, -2.0) == FROST_SESSIONS
        assert next_frost_level(FROST_OFF, -3.0, 2.0, -2.0) == FROST_CONTINUOUS

    def test_hysteresis(self):
        assert next_frost_level(FROST_SESSIONS, 2.5, 2.0, -2.0) == FROST_SESSIONS
        assert next_frost_level(FROST_SESSIONS, 3.0, 2.0, -2.0) == FROST_OFF
        assert next_frost_level(FROST_CONTINUOUS, -1.5, 2.0, -2.0) == FROST_CONTINUOUS
        assert next_frost_level(FROST_CONTINUOUS, -1.0, 2.0, -2.0) == FROST_SESSIONS
        assert next_frost_level(FROST_CONTINUOUS, 2.5, 2.0, -2.0) == FROST_SESSIONS

    def test_wants_on(self):
        assert frost_wants_on(FROST_SESSIONS, 0, 15) is True
        assert frost_wants_on(FROST_SESSIONS, 14, 15) is True
        assert frost_wants_on(FROST_SESSIONS, 15, 15) is False
        assert frost_wants_on(FROST_CONTINUOUS, 45, 15) is True
        assert frost_wants_on(FROST_OFF, 0, 15) is False


# ---------------------------------------------------------------------------
# ManagedDevice — daily quota at 05:00
# ---------------------------------------------------------------------------

class TestQuotaCapture:
    def test_quota_from_water_temperature(self):
        d = _device()
        d.try_capture_pool_required(_reader({WATER: "15"}), current_hour=5)
        assert d.pool_required_minutes_today == pytest.approx(360.0)
        assert d.pool_wintering is False

    def test_captured_at_midnight(self):
        d = _device()
        d.try_capture_pool_required(_reader({WATER: "15"}), current_hour=0)
        assert d.pool_required_minutes_today == pytest.approx(360.0)

    def test_old_filtration_entity_is_ignored(self):
        cfg = _cfg(**{CONF_POOL_FILTRATION_ENTITY: "sensor.filtration_h"})
        cfg.pop(CONF_POOL_WATER_TEMP_ENTITY)
        d = ManagedDevice(cfg, GCFG)
        d.try_capture_pool_required(_reader({"sensor.filtration_h": "3"}), current_hour=5)
        assert d.pool_required_minutes_today is None

    @pytest.mark.asyncio
    async def test_counter_and_quota_reset_at_midnight_frost_counted(self):
        d = _device()
        d.pool_last_date = date(2000, 1, 1)       # yesterday's data
        d.pool_daily_run_minutes = 300.0
        d.pool_required_minutes_today = 360.0
        d.is_on = True
        d.last_decision_reason = "frost"
        mgr, hass = _manager(d, {WATER: "20", LOCAL: "-5"})
        await _dispatch_at(mgr, hass, "00:02", _score())
        assert d.pool_daily_run_minutes == pytest.approx(5.0)   # new day, frost cycle counted
        assert d.pool_required_minutes_today == pytest.approx(600.0)  # water read at midnight
        assert d.is_on is True

    def test_unavailable_sensor_retries(self):
        d = _device()
        d.try_capture_pool_required(_reader({WATER: "unavailable"}), current_hour=5)
        assert d.pool_required_minutes_today is None
        d.try_capture_pool_required(_reader({WATER: "20"}), current_hour=6)
        assert d.pool_required_minutes_today == pytest.approx(600.0)

    def test_snapshot_is_frozen(self):
        d = _device()
        d.try_capture_pool_required(_reader({WATER: "15"}), current_hour=5)
        d.try_capture_pool_required(_reader({WATER: "28"}), current_hour=14)
        assert d.pool_required_minutes_today == pytest.approx(360.0)

    def test_wintering_uses_fixed_duration(self):
        d = _device()
        d.try_capture_pool_required(_reader({WATER: "8"}), current_hour=5)
        assert d.pool_wintering is True
        assert d.pool_required_minutes_today == pytest.approx(120.0)

    def test_wintering_exit_needs_hysteresis(self):
        d = _device()
        d.pool_wintering = True
        d.try_capture_pool_required(_reader({WATER: "13"}), current_hour=5)
        assert d.pool_wintering is True
        d.update_pool_run_time(5, date(2099, 1, 1))  # next day → new snapshot
        d.try_capture_pool_required(_reader({WATER: "14.5"}), current_hour=5)
        assert d.pool_wintering is False
        assert d.pool_required_minutes_today == pytest.approx(
            filtration_hours(d.pool_filtration_table, 14.5) * 60
        )

    def test_wintering_disabled(self):
        d = _device(**{CONF_POOL_WINTERING_ENABLED: False})
        d.try_capture_pool_required(_reader({WATER: "8"}), current_hour=5)
        assert d.pool_wintering is False
        assert d.pool_required_minutes_today == pytest.approx(120.0)  # table end value (2 h)

    def test_invalid_table_falls_back_to_default(self):
        d = _device(**{CONF_POOL_FILTRATION_TABLE: "garbage"})
        assert d.pool_filtration_table == parse_filtration_table(DEFAULT_POOL_FILTRATION_TABLE)


class TestPoolMode:
    def test_precedence(self):
        d = _device()
        assert d.pool_mode() == POOL_MODE_AUTO
        d.pool_wintering = True
        assert d.pool_mode() == POOL_MODE_WINTERING
        d.is_on = True
        d.last_decision_reason = "frost"
        assert d.pool_mode() == POOL_MODE_FROST
        d.pool_force_until = time.time() + 600
        assert d.pool_mode() == POOL_MODE_MANUAL
        d.pool_force_until = None
        d.manual_mode = True
        assert d.pool_mode() == POOL_MODE_MANUAL

    def test_frost_mode_only_while_running(self):
        d = _device()
        d.is_on = False
        d.last_decision_reason = "frost"
        assert d.pool_mode() == POOL_MODE_AUTO


# ---------------------------------------------------------------------------
# Dispatch — frost protection
# ---------------------------------------------------------------------------

class TestFrostDispatch:
    @pytest.mark.asyncio
    async def test_session_starts_despite_red_peak_window_and_quota(self):
        d = _device(**{CONF_DEVICE_ALLOWED_START: "10:00", CONF_DEVICE_ALLOWED_END: "18:00"})
        d.pool_required_minutes_today = 60.0
        d.pool_daily_run_minutes = 120.0   # quota already exceeded
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "1"})
        await _dispatch_at(mgr, hass, "07:05", _score(tempo=TEMPO_RED))
        assert d.is_on is True
        assert d.last_decision_reason == "frost"
        assert d.pool_mode() == POOL_MODE_FROST

    @pytest.mark.asyncio
    async def test_frost_time_counts_beyond_quota(self):
        d = _device()
        d.pool_required_minutes_today = 60.0
        d.pool_daily_run_minutes = 60.0
        d.is_on = True
        d.last_decision_reason = "frost"
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "-5"})
        await _dispatch_at(mgr, hass, "03:40", _score())
        assert d.pool_daily_run_minutes == pytest.approx(65.0)
        assert d.is_on is True   # continuous level: quota does not stop it

    @pytest.mark.asyncio
    async def test_session_end_hands_back_and_waives_min_on(self):
        d = _device()
        d.pool_required_minutes_today = 60.0
        d.pool_daily_run_minutes = 120.0
        d.pool_frost_level = FROST_SESSIONS
        d.is_on = True
        d.turned_on_at = time.time() - 15 * 60   # min_on (30) not elapsed
        d.last_decision_reason = "frost"
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "1"})
        await _dispatch_at(mgr, hass, "07:20", _score())
        assert d.is_on is False
        assert d.pool_mode() != POOL_MODE_FROST

    @pytest.mark.asyncio
    async def test_session_end_keeps_pump_when_filtration_needs_it(self):
        d = _device()
        d.pool_required_minutes_today = 600.0
        d.pool_daily_run_minutes = 0.0
        d.pool_frost_level = FROST_SESSIONS
        d.is_on = True
        d.last_decision_reason = "frost"
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "1"})
        await _dispatch_at(mgr, hass, "12:20", _score(surplus_w=3000.0))
        assert d.is_on is True
        assert d.pool_mode() == POOL_MODE_WINTERING or d.pool_mode() == POOL_MODE_AUTO

    @pytest.mark.asyncio
    async def test_no_frost_in_manual_mode(self):
        d = _device()
        d.manual_mode = True
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "-5"})
        await _dispatch_at(mgr, hass, "03:00", _score())
        assert d.is_on is False
        assert d.pool_frost_level == FROST_OFF  # not evaluated in manual mode

    @pytest.mark.asyncio
    async def test_no_frost_while_user_stop(self):
        d = _device()
        d.pool_inhibit_until = time.time() + 3600
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "-5"})
        await _dispatch_at(mgr, hass, "03:00", _score())
        assert d.is_on is False

    @pytest.mark.asyncio
    async def test_frost_takes_over_pump_already_filtering(self):
        """Pump running for filtration when frost starts: no switch call, mode becomes frost."""
        d = _device()
        d.pool_required_minutes_today = 600.0
        d.is_on = True
        d.turned_on_at = time.time() - 600
        d.last_decision_reason = "greedy"
        mgr, hass = _manager(d, {WATER: "20", LOCAL: "1"})
        await _dispatch_at(mgr, hass, "07:05", _score(surplus_w=3000.0))
        assert d.is_on is True
        assert d.last_decision_reason == "frost"
        assert d.pool_mode() == POOL_MODE_FROST
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_force_on_wins_over_frost(self):
        """Force ON during frost: the force keeps the pump, frost does not take it over."""
        d = _device()
        d.pool_required_minutes_today = 60.0
        d.pool_daily_run_minutes = 120.0
        d.pool_force_until = time.time() + 3600
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "-5"})
        await _dispatch_at(mgr, hass, "03:20", _score())
        assert d.is_on is True
        assert d.last_decision_reason == "force_mode"
        assert d.pool_mode() == POOL_MODE_MANUAL
        # Force still on during the session off-phase (minute ≥ 15): pump kept
        await _dispatch_at(mgr, hass, "03:40", _score())
        assert d.is_on is True
        assert d.last_decision_reason == "force_mode"

    @pytest.mark.asyncio
    async def test_levels_over_successive_cycles(self):
        """Sessions → continuous → sessions → off, as the room temperature moves."""
        d = _device()
        d.pool_required_minutes_today = 60.0
        d.pool_daily_run_minutes = 120.0   # quota reached: only frost runs the pump
        states = {WATER: "8", LOCAL: "1"}
        mgr, hass = _manager(d, states)

        await _dispatch_at(mgr, hass, "03:05", _score())
        assert (d.pool_frost_level, d.is_on) == (FROST_SESSIONS, True)

        states[LOCAL] = "-3"                       # below continuous threshold
        await _dispatch_at(mgr, hass, "03:20", _score())
        assert (d.pool_frost_level, d.is_on) == (FROST_CONTINUOUS, True)  # past minute 15, still on

        states[LOCAL] = "-1.5"                     # inside the continuous hysteresis
        await _dispatch_at(mgr, hass, "03:25", _score())
        assert (d.pool_frost_level, d.is_on) == (FROST_CONTINUOUS, True)

        states[LOCAL] = "-0.5"                     # exits continuous, still below sessions
        await _dispatch_at(mgr, hass, "03:30", _score())
        assert (d.pool_frost_level, d.is_on) == (FROST_SESSIONS, False)   # minute 30 → session over

        await _dispatch_at(mgr, hass, "04:00", _score())
        assert (d.pool_frost_level, d.is_on) == (FROST_SESSIONS, True)    # next hourly session

        states[LOCAL] = "3.5"                      # above sessions + hysteresis
        await _dispatch_at(mgr, hass, "04:10", _score())
        assert (d.pool_frost_level, d.is_on) == (FROST_OFF, False)
        assert d.pool_mode() == POOL_MODE_AUTO

    @pytest.mark.asyncio
    async def test_unavailable_room_sensor_keeps_level(self):
        d = _device()
        d.pool_frost_level = FROST_CONTINUOUS
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "unavailable"})
        await _dispatch_at(mgr, hass, "03:30", _score())
        assert d.pool_frost_level == FROST_CONTINUOUS
        assert d.is_on is True


# ---------------------------------------------------------------------------
# Wintering placement — solar surplus, otherwise off-peak hours before midnight
# ---------------------------------------------------------------------------

def _wintering_device(done_min=0.0, **over) -> ManagedDevice:
    d = _device(**over)
    d.pool_wintering = True
    d.pool_required_minutes_today = 120.0
    d.pool_daily_run_minutes = done_min
    return d


def _at(hhmm: str) -> datetime:
    return datetime.combine(date.today(), datetime.strptime(hhmm, "%H:%M").time())


class TestWinteringPlacement:
    def test_off_peak_minutes_until_midnight(self):
        d = _device()
        assert d._off_peak_minutes_until_midnight(_at("00:00").time()) == 480.0  # 0–6 + 22–24
        assert d._off_peak_minutes_until_midnight(_at("05:00").time()) == 180.0
        assert d._off_peak_minutes_until_midnight(_at("12:00").time()) == 120.0
        assert d._off_peak_minutes_until_midnight(_at("23:00").time()) == 60.0

    def test_forced_when_off_peak_left_equals_deficit(self):
        d = _wintering_device()
        assert d.urgency_modifier(_reader({}), now=_at("22:00")) == 1.0

    def test_not_forced_when_off_peak_left_exceeds_deficit(self):
        d = _wintering_device(done_min=60.0)
        assert d.urgency_modifier(_reader({}), now=_at("22:00")) < 1.0
        assert d.urgency_modifier(_reader({}), now=_at("23:00")) == 1.0

    def test_morning_off_peak_waits_for_solar(self):
        d = _wintering_device()
        assert d.urgency_modifier(_reader({}), now=_at("00:30")) < 1.0

    def test_never_forced_in_peak_hours(self):
        d = _wintering_device(**{CONF_POOL_WINTERING_DURATION_H: 6.0})
        d.pool_required_minutes_today = 360.0
        assert d.urgency_modifier(_reader({}), now=_at("21:30")) < 1.0

    def test_satisfied_no_urgency(self):
        d = _wintering_device(done_min=120.0)
        assert d.urgency_modifier(_reader({}), now=_at("23:00")) == 0.0

    def test_no_time_window_in_wintering(self):
        d = _wintering_device(**{CONF_DEVICE_ALLOWED_START: "10:00", CONF_DEVICE_ALLOWED_END: "18:00"})
        assert d.is_in_allowed_window(_at("23:00").time()) is True
        d.pool_wintering = False
        assert d.is_in_allowed_window(_at("23:00").time()) is False

    def test_without_off_peak_slots_deadline_is_midnight(self):
        d = ManagedDevice(_cfg(**{CONF_DEVICE_ALLOWED_END: "18:00"}), {})
        d.pool_wintering = True
        d.pool_required_minutes_today = 120.0
        assert d.urgency_modifier(_reader({}), now=_at("20:00")) == pytest.approx(120 / 240)

    @pytest.mark.asyncio
    async def test_dispatch_runs_on_solar_surplus_outside_window(self):
        d = _wintering_device(**{CONF_DEVICE_ALLOWED_START: "14:00", CONF_DEVICE_ALLOWED_END: "18:00"})
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "10"})
        await _dispatch_at(mgr, hass, "11:00", _score(surplus_w=3000.0))
        assert d.is_on is True

    @pytest.mark.asyncio
    async def test_dispatch_waits_in_peak_without_surplus(self):
        d = _wintering_device()
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "10"})
        await _dispatch_at(mgr, hass, "18:00", _score())
        assert d.is_on is False

    @pytest.mark.asyncio
    async def test_dispatch_catches_up_in_off_peak_even_on_red_day(self):
        d = _wintering_device()
        mgr, hass = _manager(d, {WATER: "8", LOCAL: "10"})
        await _dispatch_at(mgr, hass, "22:00", _score(tempo=TEMPO_RED))
        assert d.is_on is True
        assert d.last_decision_reason == "urgency"


# ---------------------------------------------------------------------------
# Dispatch — red Tempo days
# ---------------------------------------------------------------------------

class TestRedDay:
    @pytest.mark.asyncio
    async def test_no_start_in_red_peak_even_with_urgency(self):
        d = _device()
        d.pool_required_minutes_today = 600.0   # deficit > time left → urgency 1
        mgr, hass = _manager(d, {WATER: "20", LOCAL: "15"})
        await _dispatch_at(mgr, hass, "17:00", _score(surplus_w=3000.0, tempo=TEMPO_RED))
        assert d.is_on is False

    @pytest.mark.asyncio
    async def test_running_pump_stopped_in_red_peak(self):
        d = _device()
        d.pool_required_minutes_today = 600.0
        d.is_on = True
        d.turned_on_at = time.time() - 60
        mgr, hass = _manager(d, {WATER: "20", LOCAL: "15"})
        await _dispatch_at(mgr, hass, "06:05", _score(surplus_w=3000.0, tempo=TEMPO_RED))
        assert d.is_on is False
        assert d.last_decision_reason == "red_peak"

    @pytest.mark.asyncio
    async def test_red_off_peak_runs_normally(self):
        d = _device()
        d.pool_required_minutes_today = 600.0   # urgency 1 at 23:00
        mgr, hass = _manager(d, {WATER: "20", LOCAL: "15"})
        await _dispatch_at(mgr, hass, "23:00", _score(tempo=TEMPO_RED))
        assert d.is_on is True

    @pytest.mark.asyncio
    async def test_blue_day_unaffected(self):
        d = _device()
        d.pool_required_minutes_today = 600.0
        mgr, hass = _manager(d, {WATER: "20", LOCAL: "15"})
        await _dispatch_at(mgr, hass, "17:00", _score(surplus_w=3000.0))
        assert d.is_on is True


# ---------------------------------------------------------------------------
# Errors — missing water / room sensors
# ---------------------------------------------------------------------------

class TestPoolErrors:
    def test_no_error_when_both_sensors_ok(self):
        d = _device()
        d.pool_required_minutes_today = 300.0
        assert d.pool_errors(_reader({LOCAL: "10"})) == []

    def test_no_water_sensor_configured(self):
        cfg = _cfg()
        cfg.pop(CONF_POOL_WATER_TEMP_ENTITY)
        d = ManagedDevice(cfg, GCFG)
        d.try_capture_pool_required(_reader({LOCAL: "10"}), current_hour=0)
        assert d.pool_errors(_reader({LOCAL: "10"})) == [POOL_ERROR_NO_WATER_TEMP]

    def test_water_unavailable_mid_day_after_snapshot_is_fine(self):
        d = _device()
        d.try_capture_pool_required(_reader({WATER: "15"}), current_hour=0)
        assert d.pool_errors(_reader({WATER: "unavailable", LOCAL: "10"})) == []

    def test_room_sensor_missing_or_unavailable(self):
        d = _device()
        d.pool_required_minutes_today = 300.0
        assert d.pool_errors(_reader({LOCAL: "unavailable"})) == [POOL_ERROR_NO_LOCAL_TEMP]
        cfg = _cfg()
        cfg.pop(CONF_POOL_LOCAL_TEMP_ENTITY)
        d2 = ManagedDevice(cfg, GCFG)
        d2.pool_required_minutes_today = 300.0
        assert d2.pool_errors(_reader({})) == [POOL_ERROR_NO_LOCAL_TEMP]

    @pytest.mark.asyncio
    async def test_no_quota_helios_leaves_pump_alone(self):
        d = _device()
        d.is_on = True
        d.turned_on_at = time.time() - 3600
        mgr, hass = _manager(d, {WATER: "unavailable", LOCAL: "10"})
        await _dispatch_at(mgr, hass, "12:00", _score(surplus_w=0.0))
        assert d.is_on is True            # not turned off by budget
        hass.services.async_call.assert_not_called()
        assert POOL_ERROR_NO_WATER_TEMP in d.pool_errors_last

    @pytest.mark.asyncio
    async def test_no_quota_frost_still_works_and_stops_itself(self):
        d = _device()
        mgr, hass = _manager(d, {WATER: "unavailable", LOCAL: "1"})
        await _dispatch_at(mgr, hass, "03:05", _score())
        assert d.is_on is True
        assert d.last_decision_reason == "frost"
        await _dispatch_at(mgr, hass, "03:20", _score())
        assert d.is_on is False           # session over, nobody else drives the pump

    @pytest.mark.asyncio
    async def test_errors_logged_once(self, caplog):
        d = _device()
        mgr, hass = _manager(d, {WATER: "unavailable", LOCAL: "unavailable"})
        with caplog.at_level("ERROR"):
            await _dispatch_at(mgr, hass, "12:00", _score())
            await _dispatch_at(mgr, hass, "12:05", _score())
        msgs = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert sum("unable to drive the pump" in m for m in msgs) == 1
        assert sum("unable to manage frost protection" in m for m in msgs) == 1


# ---------------------------------------------------------------------------
# Persistence & config flow
# ---------------------------------------------------------------------------

class TestPersistence:
    @pytest.mark.asyncio
    async def test_wintering_saved_and_restored(self):
        d = _device()
        d.pool_wintering = True
        mgr, hass = _manager(d, {})
        await mgr._async_save_device_data()
        saved = mgr._store.async_save.call_args[0][0]
        assert saved["Piscine"]["pool_wintering"] is True

        d2 = _device()
        mgr2, _ = _manager(d2, {})
        mgr2._store.async_load = AsyncMock(return_value=saved)
        mgr2._unsub_ready_listeners = []
        await mgr2.async_setup()
        assert d2.pool_wintering is True


class TestConfigFlowValidation:
    def test_valid(self):
        assert _validate_pool(_cfg()) == {}

    def test_invalid_table(self):
        errors = _validate_pool(_cfg(**{CONF_POOL_FILTRATION_TABLE: "10:2"}))
        assert errors == {CONF_POOL_FILTRATION_TABLE: "invalid_filtration_table"}

    def test_continuous_above_sessions(self):
        errors = _validate_pool(_cfg(**{CONF_POOL_FROST_TEMP: 0.0, CONF_POOL_FROST_CONTINUOUS_TEMP: 1.0}))
        assert errors == {CONF_POOL_FROST_CONTINUOUS_TEMP: "frost_continuous_above_sessions"}
