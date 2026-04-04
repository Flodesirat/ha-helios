"""Tests for the power sampling buffer mechanism.

Covers:
- _rebuild_buffers: correct N, correct maxlen, device dict keys, reset on rebuild
- _buf_mean: empty fallback, single element, multiple elements, full buffer
- _async_sample_sensors: values pushed to buffers, battery conditional, devices, no state write
- _read_sensors: uses buffer mean when available, partial buffer, startup fallback
- _device_mean_power_w: buffer mean vs fallback to actual_power_w
"""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.helios.coordinator import EnergyOptimizerCoordinator
from custom_components.helios.managed_device import ManagedDevice
from custom_components.helios.const import (
    CONF_PV_POWER_ENTITY, CONF_GRID_POWER_ENTITY, CONF_HOUSE_POWER_ENTITY,
    CONF_BATTERY_ENABLED, CONF_BATTERY_POWER_ENTITY,
    CONF_SCAN_INTERVAL_MINUTES, CONF_SAMPLE_INTERVAL_SECONDS,
    CONF_DEVICE_NAME, CONF_DEVICE_TYPE, CONF_DEVICE_SWITCH_ENTITY,
    CONF_DEVICE_POWER_W, CONF_DEVICE_PRIORITY, CONF_DEVICE_POWER_ENTITY,
    DEFAULT_SCAN_INTERVAL, DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DEVICE_TYPE_EV,
    CONF_EV_PLUGGED_ENTITY, CONF_EV_SOC_ENTITY, CONF_EV_SOC_TARGET,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord(cfg: dict | None = None) -> MagicMock:
    """Build a minimal coordinator mock with real buffer methods and a real _buf_mean."""
    coord = MagicMock(spec=EnergyOptimizerCoordinator)
    coord._cfg = cfg or {
        CONF_PV_POWER_ENTITY:         "sensor.pv",
        CONF_GRID_POWER_ENTITY:       "sensor.grid",
        CONF_HOUSE_POWER_ENTITY:      "sensor.house",
        CONF_BATTERY_ENABLED:         False,
        CONF_SCAN_INTERVAL_MINUTES:   DEFAULT_SCAN_INTERVAL,
        CONF_SAMPLE_INTERVAL_SECONDS: DEFAULT_SAMPLE_INTERVAL_SECONDS,
    }
    # Wire up the real static helper so bound real methods can call it
    coord._buf_mean = EnergyOptimizerCoordinator._buf_mean
    coord._buf_pv      = deque(maxlen=10)
    coord._buf_house   = deque(maxlen=10)
    coord._buf_grid    = deque(maxlen=10)
    coord._buf_battery = deque(maxlen=10)
    coord._buf_devices = {}
    coord.device_manager = MagicMock()
    coord.device_manager.devices = []
    return coord


def _make_device(name: str = "Appareil", power_entity: str | None = None) -> ManagedDevice:
    cfg = {
        CONF_DEVICE_NAME:          name,
        CONF_DEVICE_TYPE:          DEVICE_TYPE_EV,
        CONF_DEVICE_SWITCH_ENTITY: "switch.test",
        CONF_DEVICE_POWER_W:       2000,
        CONF_DEVICE_PRIORITY:      5,
        CONF_EV_PLUGGED_ENTITY:    None,
        CONF_EV_SOC_ENTITY:        None,
        CONF_EV_SOC_TARGET:        100,
    }
    if power_entity:
        cfg[CONF_DEVICE_POWER_ENTITY] = power_entity
    return ManagedDevice(cfg)


def _hass_with_states(states: dict[str, float]) -> MagicMock:
    """Return a hass mock where the given entity IDs map to float values."""
    hass = MagicMock()

    def _get(entity_id):
        s = MagicMock()
        if entity_id in states:
            s.state = str(states[entity_id])
        else:
            s.state = "unavailable"
        return s

    hass.states.get.side_effect = _get
    return hass


# ---------------------------------------------------------------------------
# _rebuild_buffers
# ---------------------------------------------------------------------------

class TestRebuildBuffers:

    def test_n_equals_scan_over_sample(self):
        """N = scan_s // sample_s — e.g. 5 min / 30 s = 10 slots."""
        coord = _make_coord({
            **_make_coord()._cfg,
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.device_manager.devices = []
        EnergyOptimizerCoordinator._rebuild_buffers(coord)
        assert coord._buf_pv.maxlen == 10
        assert coord._buf_house.maxlen == 10
        assert coord._buf_grid.maxlen == 10
        assert coord._buf_battery.maxlen == 10

    def test_n_rounds_down(self):
        """300 s scan / 60 s sample = 5 slots (integer division)."""
        coord = _make_coord({
            **_make_coord()._cfg,
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 60,
        })
        coord.device_manager.devices = []
        EnergyOptimizerCoordinator._rebuild_buffers(coord)
        assert coord._buf_pv.maxlen == 5

    def test_n_minimum_is_1(self):
        """sample_interval > scan_interval → N = max(1, …) = 1, never 0."""
        coord = _make_coord({
            **_make_coord()._cfg,
            CONF_SCAN_INTERVAL_MINUTES:   1,    # 60 s
            CONF_SAMPLE_INTERVAL_SECONDS: 300,  # 5 min > scan → 60//300 = 0 → clamped to 1
        })
        coord.device_manager.devices = []
        EnergyOptimizerCoordinator._rebuild_buffers(coord)
        assert coord._buf_pv.maxlen == 1

    def test_device_buffers_created_for_each_device(self):
        """Each device gets a buffer keyed by device.name."""
        device_a = _make_device("Pompe")
        device_b = _make_device("Chauffe-eau")
        coord = _make_coord()
        coord.device_manager.devices = [device_a, device_b]
        EnergyOptimizerCoordinator._rebuild_buffers(coord)
        assert "Pompe" in coord._buf_devices
        assert "Chauffe-eau" in coord._buf_devices
        assert coord._buf_devices["Pompe"].maxlen == coord._buf_pv.maxlen

    def test_rebuild_clears_previous_data(self):
        """Rebuilding resets all buffers to empty (config-change scenario)."""
        coord = _make_coord()
        coord._buf_pv.append(999.0)
        coord._buf_house.append(999.0)
        coord._buf_devices = {"OldDevice": deque([500.0], maxlen=5)}
        coord.device_manager.devices = []
        EnergyOptimizerCoordinator._rebuild_buffers(coord)
        assert len(coord._buf_pv) == 0
        assert len(coord._buf_house) == 0
        assert "OldDevice" not in coord._buf_devices


# ---------------------------------------------------------------------------
# _buf_mean
# ---------------------------------------------------------------------------

class TestBufMean:

    def test_empty_buffer_returns_fallback(self):
        buf = deque(maxlen=5)
        assert EnergyOptimizerCoordinator._buf_mean(buf, fallback=42.0) == 42.0

    def test_empty_buffer_default_fallback_is_zero(self):
        buf = deque(maxlen=5)
        assert EnergyOptimizerCoordinator._buf_mean(buf) == 0.0

    def test_single_element(self):
        buf = deque([1500.0], maxlen=5)
        assert EnergyOptimizerCoordinator._buf_mean(buf) == 1500.0

    def test_multiple_elements_returns_mean(self):
        buf = deque([100.0, 200.0, 300.0], maxlen=5)
        assert EnergyOptimizerCoordinator._buf_mean(buf) == pytest.approx(200.0)

    def test_full_buffer_mean(self):
        buf = deque([0.0, 1000.0, 2000.0, 3000.0, 4000.0], maxlen=5)
        assert EnergyOptimizerCoordinator._buf_mean(buf) == pytest.approx(2000.0)

    def test_fallback_ignored_when_buffer_has_data(self):
        buf = deque([500.0], maxlen=5)
        assert EnergyOptimizerCoordinator._buf_mean(buf, fallback=9999.0) == 500.0


# ---------------------------------------------------------------------------
# _async_sample_sensors
# ---------------------------------------------------------------------------

class TestAsyncSampleSensors:

    @pytest.mark.asyncio
    async def test_pv_house_grid_pushed_to_buffers(self):
        """One call pushes exactly one value per power signal."""
        coord = _make_coord()
        coord.hass = _hass_with_states({
            "sensor.pv": 1500.0, "sensor.grid": 100.0, "sensor.house": 400.0,
        })
        coord.device_manager.devices = []

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_pv)    == [1500.0]
        assert list(coord._buf_grid)  == [100.0]
        assert list(coord._buf_house) == [400.0]
        assert list(coord._buf_battery) == []   # battery disabled

    @pytest.mark.asyncio
    async def test_battery_buffer_updated_when_enabled(self):
        """Battery buffer is filled only when CONF_BATTERY_ENABLED=True."""
        coord = _make_coord({
            CONF_PV_POWER_ENTITY:      "sensor.pv",
            CONF_GRID_POWER_ENTITY:    "sensor.grid",
            CONF_HOUSE_POWER_ENTITY:   "sensor.house",
            CONF_BATTERY_ENABLED:      True,
            CONF_BATTERY_POWER_ENTITY: "sensor.bat",
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,
        })
        coord.hass = _hass_with_states({
            "sensor.pv": 1000.0, "sensor.grid": 0.0,
            "sensor.house": 300.0, "sensor.bat": -500.0,
        })
        coord.device_manager.devices = []

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_battery) == [-500.0]

    @pytest.mark.asyncio
    async def test_battery_buffer_not_updated_when_disabled(self):
        """Battery buffer stays empty when battery is disabled."""
        coord = _make_coord()   # CONF_BATTERY_ENABLED=False by default
        coord.hass = _hass_with_states({
            "sensor.pv": 1000.0, "sensor.grid": 0.0, "sensor.house": 300.0,
        })
        coord.device_manager.devices = []

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_battery) == []

    @pytest.mark.asyncio
    async def test_multiple_samples_accumulate(self):
        """Three calls accumulate three values in each buffer."""
        coord = _make_coord()
        coord.hass = _hass_with_states({
            "sensor.pv": 1000.0, "sensor.grid": 0.0, "sensor.house": 300.0,
        })
        coord.device_manager.devices = []

        for _ in range(3):
            await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert len(coord._buf_pv)    == 3
        assert len(coord._buf_house) == 3
        assert len(coord._buf_grid)  == 3

    @pytest.mark.asyncio
    async def test_device_power_pushed_to_device_buffer(self):
        """Device actual_power_w is sampled and stored in its named buffer."""
        device = _make_device("Pompe", power_entity="sensor.pompe_power")
        coord = _make_coord()
        coord._buf_devices = {"Pompe": deque(maxlen=10)}
        coord.hass = _hass_with_states({
            "sensor.pv": 2000.0, "sensor.grid": 0.0, "sensor.house": 500.0,
            "sensor.pompe_power": 650.0,
        })
        coord.device_manager.devices = [device]

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_devices["Pompe"]) == [650.0]

    @pytest.mark.asyncio
    async def test_unavailable_entity_pushes_zero(self):
        """Unavailable HA sensor → 0.0 pushed to the buffer."""
        coord = _make_coord()
        coord.hass = _hass_with_states({
            # sensor.pv absent → unavailable → 0.0
            "sensor.grid": 0.0, "sensor.house": 300.0,
        })
        coord.device_manager.devices = []

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        assert list(coord._buf_pv) == [0.0]

    @pytest.mark.asyncio
    async def test_no_ha_state_written(self):
        """Sampling must never write HA state (no side effects on hass.states)."""
        coord = _make_coord()
        coord.hass = _hass_with_states({
            "sensor.pv": 1000.0, "sensor.grid": 0.0, "sensor.house": 300.0,
        })
        coord.device_manager.devices = []

        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        coord.hass.states.async_set.assert_not_called()


# ---------------------------------------------------------------------------
# _read_sensors — buffer mean vs fallback
# ---------------------------------------------------------------------------

class TestReadSensors:

    @pytest.mark.asyncio
    async def test_uses_buffer_mean_when_available(self):
        """When buffers have data, _read_sensors returns the mean of each buffer."""
        coord = _make_coord()
        coord._buf_pv    = deque([1000.0, 1200.0, 800.0], maxlen=10)
        coord._buf_grid  = deque([0.0, 50.0, 100.0], maxlen=10)
        coord._buf_house = deque([300.0, 400.0, 500.0], maxlen=10)
        coord.hass = _hass_with_states({})   # direct reads return unavailable

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["pv_power_w"]    == pytest.approx(1000.0)   # (1000+1200+800)/3
        assert result["grid_power_w"]  == pytest.approx(50.0)     # (0+50+100)/3
        assert result["house_power_w"] == pytest.approx(400.0)    # (300+400+500)/3

    @pytest.mark.asyncio
    async def test_partial_buffer_returns_mean_of_available(self):
        """A buffer with 2 elements (maxlen=10) returns mean of those 2."""
        coord = _make_coord()
        coord._buf_pv    = deque([800.0, 1200.0], maxlen=10)
        coord._buf_house = deque([350.0, 450.0], maxlen=10)
        coord._buf_grid  = deque([100.0], maxlen=10)
        coord.hass = _hass_with_states({})

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["pv_power_w"]    == pytest.approx(1000.0)   # (800+1200)/2
        assert result["house_power_w"] == pytest.approx(400.0)    # (350+450)/2
        assert result["grid_power_w"]  == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Startup scenario: empty buffers → direct HA sensor read fallback
# ---------------------------------------------------------------------------

class TestStartupFallback:

    @pytest.mark.asyncio
    async def test_empty_buffers_fall_back_to_direct_read(self):
        """At t=0, before any sample, all buffers are empty.
        _read_sensors must fall back to direct HA state reads so that sensor
        entities display correct values immediately after load."""
        coord = _make_coord()
        # Explicitly empty (simulates the state right after __init__)
        coord._buf_pv      = deque(maxlen=10)
        coord._buf_grid    = deque(maxlen=10)
        coord._buf_house   = deque(maxlen=10)
        coord._buf_battery = deque(maxlen=10)
        coord.hass = _hass_with_states({
            "sensor.pv":    2500.0,
            "sensor.grid":  200.0,
            "sensor.house": 600.0,
        })

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        assert result["pv_power_w"]    == 2500.0
        assert result["grid_power_w"]  == 200.0
        assert result["house_power_w"] == 600.0

    @pytest.mark.asyncio
    async def test_after_first_sample_buffer_takes_over(self):
        """After one sample, _read_sensors uses the buffered value, not the
        live sensor (even if the live sensor has since changed to a spike)."""
        coord = _make_coord()
        coord.hass = _hass_with_states({
            "sensor.pv": 1000.0, "sensor.grid": 0.0, "sensor.house": 300.0,
        })
        coord.device_manager.devices = []

        # Simulate first 30-s sampling tick
        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        # Sensor spikes to an unrealistic value
        coord.hass = _hass_with_states({
            "sensor.pv":    9999.0,
            "sensor.grid":  9999.0,
            "sensor.house": 9999.0,
        })

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        # The buffer holds 1000.0 — the spike must be ignored
        assert result["pv_power_w"]    == 1000.0
        assert result["grid_power_w"]  == 0.0
        assert result["house_power_w"] == 300.0

    @pytest.mark.asyncio
    async def test_mean_smooths_spike_over_window(self):
        """A single spike within a full window is damped by averaging.

        9 normal samples of 1000 W + 1 spike of 10000 W → mean ≈ 1900 W,
        not the raw spike value.
        """
        coord = _make_coord({
            **_make_coord()._cfg,
            CONF_SCAN_INTERVAL_MINUTES:   5,
            CONF_SAMPLE_INTERVAL_SECONDS: 30,   # N = 10
        })
        coord._buf_pv = deque(maxlen=10)
        coord.device_manager.devices = []
        coord.hass = _hass_with_states({
            "sensor.pv": 1000.0, "sensor.grid": 0.0, "sensor.house": 300.0,
        })

        for _ in range(9):
            await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        # Spike sample
        coord.hass = _hass_with_states({
            "sensor.pv": 10000.0, "sensor.grid": 0.0, "sensor.house": 300.0,
        })
        await EnergyOptimizerCoordinator._async_sample_sensors(coord, None)

        result = await EnergyOptimizerCoordinator._read_sensors(coord)

        # Mean = (9×1000 + 10000) / 10 = 1900 W — well below the spike
        assert result["pv_power_w"] == pytest.approx(1900.0)
        assert result["pv_power_w"] < 10000.0


# ---------------------------------------------------------------------------
# _device_mean_power_w
# ---------------------------------------------------------------------------

class TestDeviceMeanPowerW:

    def test_uses_buffer_mean_when_available(self):
        """Buffer with data → returns mean regardless of actual sensor."""
        device = _make_device("Zoe", power_entity="sensor.zoe")
        coord = _make_coord()
        coord._buf_devices = {"Zoe": deque([500.0, 700.0, 600.0], maxlen=10)}
        reader = MagicMock(return_value="unavailable")

        result = EnergyOptimizerCoordinator._device_mean_power_w(coord, device, reader)

        assert result == pytest.approx(600.0)

    def test_empty_buffer_falls_back_to_actual_power_w(self):
        """Empty buffer → falls back to device.actual_power_w() (nominal here)."""
        device = _make_device("Zoe")   # no power entity → nominal 2000 W
        coord = _make_coord()
        coord._buf_devices = {"Zoe": deque(maxlen=10)}  # empty

        def _reader(entity_id):
            return "unavailable"

        result = EnergyOptimizerCoordinator._device_mean_power_w(coord, device, _reader)

        assert result == 2000.0

    def test_device_not_in_dict_falls_back_to_actual_power_w(self):
        """Device absent from _buf_devices → falls back to actual_power_w."""
        device = _make_device("Inconnu")
        coord = _make_coord()
        coord._buf_devices = {}

        def _reader(entity_id):
            return "unavailable"

        result = EnergyOptimizerCoordinator._device_mean_power_w(coord, device, _reader)

        assert result == 2000.0

    def test_measured_value_in_buffer_overrides_nominal(self):
        """Buffer with measured values overrides the nominal power_w."""
        device = _make_device("Zoe", power_entity="sensor.zoe")
        coord = _make_coord()
        coord._buf_devices = {"Zoe": deque([3200.0, 3400.0], maxlen=10)}

        def _reader(entity_id):
            return "300"  # current sensor reads 300W (ignored — buffer takes over)

        result = EnergyOptimizerCoordinator._device_mean_power_w(coord, device, _reader)

        assert result == pytest.approx(3300.0)   # mean of buffer, not 300 from reader
