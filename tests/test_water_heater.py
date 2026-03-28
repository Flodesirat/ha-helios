"""Tests for water heater off-peak (HC) and on-peak logic."""
from __future__ import annotations

from datetime import time
from unittest.mock import MagicMock

import pytest

from custom_components.helios.managed_device import ManagedDevice, StateReader, _parse_off_peak_slots
from custom_components.helios.const import (
    DEVICE_TYPE_WATER_HEATER,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY, CONF_DEVICE_POWER_W,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_WH_TEMP_ENTITY, CONF_WH_TEMP_TARGET, CONF_WH_TEMP_MIN, CONF_WH_TEMP_MIN_ENTITY,
    CONF_OFF_PEAK_1_START, CONF_OFF_PEAK_1_END,
    CONF_OFF_PEAK_2_START, CONF_OFF_PEAK_2_END,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMP_ENTITY   = "sensor.wh_temp"
MIN_ENTITY    = "sensor.wh_min_temp"
TARGET        = 55.0
LEGIONELLA    = 45.0   # static floor
OFF_PEAK_MIN  = 50.0   # dynamic off-peak minimum (from entity)


def _wh_config(
    off_peak_1_start="22:00",
    off_peak_1_end="06:00",
    off_peak_2_start=None,
    off_peak_2_end=None,
) -> tuple[dict, dict]:
    """Return (device_config, global_config)."""
    device_cfg = {
        CONF_DEVICE_NAME:          "Chauffe-eau",
        CONF_DEVICE_TYPE:          DEVICE_TYPE_WATER_HEATER,
        CONF_DEVICE_SWITCH_ENTITY: "switch.chauffe_eau",
        CONF_DEVICE_POWER_W:       2000,
        CONF_WH_TEMP_ENTITY:       TEMP_ENTITY,
        CONF_WH_TEMP_TARGET:       TARGET,
        CONF_WH_TEMP_MIN:          LEGIONELLA,
        CONF_WH_TEMP_MIN_ENTITY:   MIN_ENTITY,
    }
    global_cfg: dict = {
        CONF_OFF_PEAK_1_START: off_peak_1_start,
        CONF_OFF_PEAK_1_END:   off_peak_1_end,
    }
    if off_peak_2_start:
        global_cfg[CONF_OFF_PEAK_2_START] = off_peak_2_start
        global_cfg[CONF_OFF_PEAK_2_END]   = off_peak_2_end
    return device_cfg, global_cfg


def _make_device(
    off_peak_1_start="22:00",
    off_peak_1_end="06:00",
    off_peak_2_start=None,
    off_peak_2_end=None,
) -> ManagedDevice:
    device_cfg, global_cfg = _wh_config(off_peak_1_start, off_peak_1_end,
                                         off_peak_2_start, off_peak_2_end)
    return ManagedDevice(device_cfg, global_cfg)


def _hass(temp: float, off_peak_min: float = OFF_PEAK_MIN) -> MagicMock:
    """Mock hass that returns *temp* for the temp entity and *off_peak_min* for the min entity."""
    hass = MagicMock()

    def _state(entity_id):
        s = MagicMock()
        if entity_id == TEMP_ENTITY:
            s.state = str(temp)
        elif entity_id == MIN_ENTITY:
            s.state = str(off_peak_min)
        else:
            s.state = "unavailable"
        return s

    hass.states.get.side_effect = _state
    return hass


def _reader(temp: float, off_peak_min: float = OFF_PEAK_MIN) -> StateReader:
    """StateReader equivalent of _hass — no HA dependency needed."""
    states = {TEMP_ENTITY: str(temp), MIN_ENTITY: str(off_peak_min)}
    return lambda eid: states.get(eid, "unavailable")


# ---------------------------------------------------------------------------
# Off-peak detection helpers
# ---------------------------------------------------------------------------

class TestOffPeakDetection:

    def test_midnight_crossing_inside(self):
        """23:30 is inside the 22:00–06:00 slot."""
        device = _make_device("22:00", "06:00")
        assert device._is_off_peak(time(23, 30)) is True

    def test_midnight_crossing_early_morning(self):
        """03:00 is inside the 22:00–06:00 slot (after midnight)."""
        device = _make_device("22:00", "06:00")
        assert device._is_off_peak(time(3, 0)) is True

    def test_midnight_crossing_outside(self):
        """14:00 is outside the 22:00–06:00 slot."""
        device = _make_device("22:00", "06:00")
        assert device._is_off_peak(time(14, 0)) is False

    def test_same_day_slot(self):
        """12:30 is inside a 12:00–14:00 slot (no midnight crossing)."""
        device = _make_device("12:00", "14:00")
        assert device._is_off_peak(time(12, 30)) is True

    def test_boundary_at_start(self):
        """Exactly 22:00 is the first minute of the off-peak slot."""
        device = _make_device("22:00", "06:00")
        assert device._is_off_peak(time(22, 0)) is True

    def test_boundary_at_end(self):
        """Exactly 06:00 is NOT included (half-open interval [start, end))."""
        device = _make_device("22:00", "06:00")
        assert device._is_off_peak(time(6, 0)) is False

    def test_two_slots(self):
        """Device with two off-peak slots is in off-peak for both."""
        device = _make_device("22:00", "06:00", "12:00", "14:00")
        assert device._is_off_peak(time(13, 0)) is True
        assert device._is_off_peak(time(23, 0)) is True
        assert device._is_off_peak(time(10, 0)) is False

    def test_no_slots_never_off_peak(self):
        """No off-peak configuration → never in off-peak."""
        device = ManagedDevice(
            {
                CONF_DEVICE_NAME: "CE", CONF_DEVICE_TYPE: DEVICE_TYPE_WATER_HEATER,
                CONF_WH_TEMP_ENTITY: TEMP_ENTITY,
                CONF_WH_TEMP_TARGET: TARGET, CONF_WH_TEMP_MIN: LEGIONELLA,
            },
            {},  # empty global config
        )
        assert device._is_off_peak(time(2, 0)) is False


# ---------------------------------------------------------------------------
# must_run_now — off-peak forcing
# ---------------------------------------------------------------------------

class TestMustRunNow:

    def test_off_peak_below_hysteresis_threshold_forces_on(self):
        """During HC, temp below (off-peak min − hysteresis) → must_run = True.

        With off_peak_min=50 and default hysteresis=3°C, trigger threshold = 47°C.
        """
        device = _make_device()
        reader = _reader(temp=44.0, off_peak_min=50.0)  # 44 < 50 - 3 = 47

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            assert device.must_run_now(reader) is True

    def test_off_peak_within_hysteresis_band_no_force(self):
        """During HC, temp in hysteresis band [47–50°C] → must_run = False.

        In this band the device is not forced on by must_run, but normal scoring
        can still turn it on if surplus is available.
        """
        device = _make_device()
        reader = _reader(temp=48.0, off_peak_min=50.0)  # 48 is between 47 and 50

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            assert device.must_run_now(reader) is False

    def test_off_peak_at_min_no_longer_forces(self):
        """During HC, temp exactly at off-peak min → must_run = False."""
        device = _make_device()
        reader = _reader(temp=50.0, off_peak_min=50.0)  # temp == min

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            assert device.must_run_now(reader) is False

    def test_off_peak_above_min_no_longer_forces(self):
        """During HC, temp above off-peak min → must_run = False."""
        device = _make_device()
        reader = _reader(temp=52.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(1, 0)),
            )
            assert device.must_run_now(reader) is False

    def test_on_peak_below_target_no_force(self):
        """Outside HC, temp below target → must_run = False (normal scoring applies)."""
        device = _make_device()
        reader = _reader(temp=48.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(14, 0)),  # on-peak hour
            )
            assert device.must_run_now(reader) is False

    def test_legionella_safety_always_forces_on(self):
        """Below legionella floor → must_run = True at any time of day."""
        device = _make_device()
        reader = _reader(temp=44.0)  # below LEGIONELLA = 45

        # On-peak hour
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(14, 0)),
            )
            assert device.must_run_now(reader) is True

        # Off-peak hour — also forces
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            assert device.must_run_now(reader) is True


    def test_off_peak_too_close_to_end_no_force(self):
        """HC ends at 06:00, min_on_minutes=60 → no trigger after 05:00 (only 50 min left).

        temp=46 is above the legionella floor (45°C) but below the HC trigger
        threshold (50 - 3 = 47°C), so only the HC guard can block it.
        """
        device_cfg, global_cfg = _wh_config()
        device_cfg[CONF_DEVICE_MIN_ON_MINUTES] = 60
        device = ManagedDevice(device_cfg, global_cfg)
        reader = _reader(temp=46.0, off_peak_min=50.0)  # 46 < 47 → would trigger without guard

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(5, 10)),  # 50 min before 06:00 end < 60 min
            )
            assert device.must_run_now(reader) is False

    def test_off_peak_exactly_at_cutoff_forces(self):
        """HC ends at 06:00, min_on_minutes=60 → trigger allowed at exactly 05:00 (60 min left)."""
        device_cfg, global_cfg = _wh_config()
        device_cfg[CONF_DEVICE_MIN_ON_MINUTES] = 60
        device = ManagedDevice(device_cfg, global_cfg)
        reader = _reader(temp=46.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(5, 0)),  # exactly 60 min before 06:00
            )
            assert device.must_run_now(reader) is True

    def test_off_peak_early_morning_forces(self):
        """HC at 02:00, 4h before end → must_run = True."""
        device_cfg, global_cfg = _wh_config()
        device_cfg[CONF_DEVICE_MIN_ON_MINUTES] = 60
        device = ManagedDevice(device_cfg, global_cfg)
        reader = _reader(temp=46.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(2, 0)),  # 4 h before 06:00 end
            )
            assert device.must_run_now(reader) is True


# ---------------------------------------------------------------------------
# is_satisfied — cut at off-peak min during HC, cut at target on-peak
# ---------------------------------------------------------------------------

class TestIsSatisfied:

    def test_off_peak_satisfied_at_off_peak_min(self):
        """During HC, temp == off-peak min → satisfied (heater cuts)."""
        device = _make_device()
        reader = _reader(temp=50.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            assert device.is_satisfied(reader) is True

    def test_off_peak_satisfied_above_off_peak_min(self):
        """During HC, temp > off-peak min → satisfied."""
        device = _make_device()
        reader = _reader(temp=53.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(3, 0)),
            )
            assert device.is_satisfied(reader) is True

    def test_off_peak_not_satisfied_below_off_peak_min(self):
        """During HC, temp < off-peak min → NOT satisfied."""
        device = _make_device()
        reader = _reader(temp=47.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            assert device.is_satisfied(reader) is False

    def test_on_peak_satisfied_at_target(self):
        """Outside HC, temp >= target → satisfied."""
        device = _make_device()
        reader = _reader(temp=55.0)  # == TARGET

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(14, 0)),
            )
            assert device.is_satisfied(reader) is True

    def test_on_peak_not_satisfied_below_target(self):
        """Outside HC, temp below target → NOT satisfied."""
        device = _make_device()
        reader = _reader(temp=52.0)  # below TARGET = 55

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(14, 0)),
            )
            assert device.is_satisfied(reader) is False

    def test_off_peak_not_satisfied_if_above_min_but_below_target(self):
        """During HC at 52°C with min=50 and target=55: satisfied (min reached, HC done)."""
        device = _make_device()
        reader = _reader(temp=52.0, off_peak_min=50.0)  # 52 > 50, but < 55

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            # During HC the satisfaction threshold is off_peak_min, not target
            assert device.is_satisfied(reader) is True


# ---------------------------------------------------------------------------
# urgency_modifier
# ---------------------------------------------------------------------------

class TestUrgencyModifier:

    def test_on_peak_far_from_target_is_urgent(self):
        """Outside HC, temp well below target → urgency close to 1."""
        device = _make_device()
        # temp = LEGIONELLA = 45, target = 55, range = 10 → urgency = (55-45)/10 = 1.0
        reader = _reader(temp=45.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(14, 0)),
            )
            urgency = device.urgency_modifier(reader)
            assert urgency == pytest.approx(1.0)

    def test_on_peak_close_to_target_low_urgency(self):
        """Outside HC, temp just below target → urgency close to 0."""
        device = _make_device()
        # temp = 54, target = 55, range = 10 → urgency = 1/10 = 0.1
        reader = _reader(temp=54.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(14, 0)),
            )
            urgency = device.urgency_modifier(reader)
            assert urgency == pytest.approx(0.1)

    def test_off_peak_far_from_min_is_urgent(self):
        """During HC, temp well below off-peak min → high urgency."""
        device = _make_device()
        # temp=45, off_peak_min=50, target=55, range = 55-50=5 → deficit=50-45=5 → 1.0
        reader = _reader(temp=45.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            urgency = device.urgency_modifier(reader)
            assert urgency == pytest.approx(1.0)

    def test_off_peak_at_min_zero_urgency(self):
        """During HC, temp at off-peak min → urgency = 0 (no deficit)."""
        device = _make_device()
        reader = _reader(temp=50.0, off_peak_min=50.0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "custom_components.helios.managed_device.datetime",
                _fixed_datetime(time(23, 0)),
            )
            urgency = device.urgency_modifier(reader)
            assert urgency == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Helpers for patching datetime.now().time()
# ---------------------------------------------------------------------------

class _fixed_datetime:
    """Minimal datetime replacement that returns a fixed time() from now()."""

    def __init__(self, fixed_time: time):
        self._fixed_time = fixed_time

    def now(self):
        mock = MagicMock()
        mock.time.return_value = self._fixed_time
        return mock

    def combine(self, *args, **kwargs):
        from datetime import datetime
        return datetime.combine(*args, **kwargs)

    def __getattr__(self, name):
        from datetime import datetime
        return getattr(datetime, name)
