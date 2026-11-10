"""Battery Drive controller — regulates the grid on every meter reading.

Runs beside the coordinator's minute-scale cycle: each new reading of the grid
sensor (a new ``last_reported``, even with an unchanged value) recomputes the
setpoint and writes it to each battery's ``number`` entity. The battery's own
integration sends it to the battery; Helios only switches modes through the
user's scripts (Drive / neutral), so any battery with a Drive-like setpoint
entity can be driven.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)

from .battery_config import get_batteries, state_float
from .battery_regulator import BatteryLimits, PassiveRegulator, battery_limits, split_setpoint
from .const import (
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_NAME,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_DRIVE_POWER_ENTITY,
    CONF_BATTERY_DRIVE_SCRIPT,
    CONF_BATTERY_NEUTRAL_SCRIPT,
    CONF_BATTERY_MAX_CHARGE_POWER_W,
    CONF_BATTERY_MAX_DISCHARGE_POWER_W,
    CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN,
    CONF_BATTERY_GRID_TARGET_BLUE_W,
    CONF_BATTERY_GRID_TARGET_WHITE_W,
    CONF_BATTERY_GRID_TARGET_RED_W,
    DEFAULT_BATTERY_GRID_TARGET_W,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_SUBSCRIPTION_W, DEFAULT_GRID_SUBSCRIPTION_W,
    DEFAULT_BATTERY_NAME,
    BATTERY_DRIVE_SETTLE_S,
    BATTERY_DRIVE_STARTUP_HOLD_S,
    BATTERY_DRIVE_STATUS_S,
    BATTERY_DRIVE_SCRIPT_REFRESH_S,
    BATTERY_DRIVE_SCRIPT_TIMEOUT_S,
    BATTERY_DRIVE_MAX_SOC,
    BATTERY_DRIVE_LOST_READS,
    BATTERY_DRIVE_TOLERANCE_W,
    BATTERY_STATE_OFF,
    BATTERY_STATE_STARTUP_WAIT,
    BATTERY_STATE_REGULATING,
    BATTERY_STATE_PRECHARGE,
    BATTERY_STATE_MODE_LOST,
    BATTERY_STATE_FORCED,
    BATTERY_FORCE_DISCHARGE,
    TEMPO_WHITE,
    TEMPO_RED,
)

if TYPE_CHECKING:
    from .coordinator import EnergyOptimizerCoordinator
    from .managed_device import BatteryDevice

_LOGGER = logging.getLogger(__name__)


def precharge_setpoint(grid_w: float, base_w: float, max_charge_w: float, subscription_w: float) -> float:
    """Charge setpoint (≤ 0) at full power, keeping the import under the subscription.

    *grid_w* (+ = import) was measured while the batteries delivered *base_w*
    (+ = discharge): the house alone draws grid + base, and charging adds to it.
    """
    house_w = grid_w + base_w
    charge_w = min(max_charge_w, subscription_w - house_w)
    return -max(0.0, charge_w)


class _Battery:
    """Runtime state of one driven battery."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.name: str = cfg.get(CONF_BATTERY_NAME) or DEFAULT_BATTERY_NAME
        self.soc_entity: str | None = cfg.get(CONF_BATTERY_SOC_ENTITY)
        self.number_entity: str | None = cfg.get(CONF_BATTERY_DRIVE_POWER_ENTITY)
        self.drive_script: str | None = cfg.get(CONF_BATTERY_DRIVE_SCRIPT)
        self.neutral_script: str | None = cfg.get(CONF_BATTERY_NEUTRAL_SCRIPT)
        self.max_charge_w = float(cfg.get(CONF_BATTERY_MAX_CHARGE_POWER_W, 0.0))
        self.max_discharge_w = float(cfg.get(CONF_BATTERY_MAX_DISCHARGE_POWER_W, 0.0))
        self.written: int | None = None   # last value written to the number entity
        self.mismatch_reads: int = 0      # status reads in a row where the number differs


class BatteryDriveController:
    """Drive the batteries so that the grid stays near a target."""

    def __init__(self, hass: HomeAssistant, coordinator: EnergyOptimizerCoordinator) -> None:
        self.hass = hass
        self._coordinator = coordinator
        cfg = coordinator.config
        self._cfg = cfg
        self._grid_entity: str | None = cfg.get(CONF_GRID_POWER_ENTITY)
        self._soc_min = float(cfg.get(CONF_BATTERY_SOC_MIN, DEFAULT_BATTERY_SOC_MIN))
        self._subscription_w = float(cfg.get(CONF_GRID_SUBSCRIPTION_W, DEFAULT_GRID_SUBSCRIPTION_W))
        self.batteries: list[_Battery] = [
            _Battery(b) for b in get_batteries(cfg) if b.get(CONF_BATTERY_DRIVE_POWER_ENTITY)
        ]
        self.regulator = PassiveRegulator(settle=BATTERY_DRIVE_SETTLE_S, max_soc=BATTERY_DRIVE_MAX_SOC)

        self.state: str = BATTERY_STATE_OFF
        self.active: bool = False
        self.target_w: float = self._target_for(None)
        self._starting: bool = True      # until the first non-zero setpoint
        self._recovering: bool = False   # after a lost setpoint, until the hold ends
        self._forcing: bool = False      # a force is being applied (until it ends)
        self._hold_until: float = 0.0
        self._lock = asyncio.Lock()
        self._started = False
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True when there is something to drive."""
        return bool(self._cfg.get(CONF_BATTERY_ENABLED) and self.batteries and self._grid_entity)

    async def async_start(self) -> None:
        """Listen to the grid sensor and the timers, then drive if wanted."""
        if not self.configured:
            return
        self._started = True
        entities = [self._grid_entity]
        self._unsubs += [
            async_track_state_change_event(self.hass, entities, self._on_grid_event),
            async_track_state_report_event(self.hass, entities, self._on_grid_event),
            async_track_time_interval(
                self.hass, self._async_check_setpoints, timedelta(seconds=BATTERY_DRIVE_STATUS_S)
            ),
            async_track_time_interval(
                self.hass, self._async_refresh_drive, timedelta(seconds=BATTERY_DRIVE_SCRIPT_REFRESH_S)
            ),
        ]
        # In the background: the Drive scripts may take ~26 s each, which must
        # not hold up the setup of the integration.
        self.hass.async_create_task(self.async_sync(), "helios battery drive start")

    def async_stop(self) -> None:
        """Stop listening (unload). The batteries are left as they are: the
        driving switch is restored on the next start, which calls Drive again."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._started = False

    def add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Call *update_callback* when the state or the setpoints change."""
        self._listeners.append(update_callback)

        def remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _forced_device(self) -> BatteryDevice | None:
        """The battery device while a force runs, else None."""
        bat = self._coordinator.device_manager.battery_device
        return bat if bat is not None and bat.force_active() else None

    def _wanted(self) -> bool:
        """Drive while Helios is on and the batteries are driven — or forced
        (a force with driving off uses Drive for its duration only)."""
        return self.configured and self._coordinator.enabled and (
            self._coordinator.battery_driving or self._forced_device() is not None
        )

    async def async_sync(self) -> None:
        """Match the switches: enter or leave Drive, start or end a force."""
        if not self._started:
            return
        async with self._lock:
            await self._async_sync_locked(time.monotonic())

    async def _async_sync_locked(self, at: float) -> None:
        bat = self._coordinator.device_manager.battery_device
        if bat is not None and bat.force_until is not None and not self._coordinator.enabled:
            await self._async_cancel_force(bat, "Helios switched off")
        wanted = self._wanted()
        if wanted and not self.active:
            await self._async_activate()
        elif not wanted and self.active:
            self._forcing = False
            await self._async_deactivate()
            return
        if not self.active:
            return
        if self._forced_device() is not None:
            await self._async_apply_force(at)
        elif self._forcing:
            self._leave_force()

    async def _async_activate(self) -> None:
        _LOGGER.info("Battery Drive: activating %d battery(ies)", len(self.batteries))
        for bat in self.batteries:
            await self._async_run_script(bat.drive_script, bat.name)
        self._restart_from_zero()
        self.active = True
        self.state = BATTERY_STATE_STARTUP_WAIT
        self._notify()

    async def _async_deactivate(self) -> None:
        _LOGGER.info("Battery Drive: releasing the batteries")
        self.active = False
        for bat in self.batteries:
            await self._async_run_script(bat.neutral_script, bat.name)
            bat.written = None
            bat.mismatch_reads = 0
        self.regulator.resync(0.0)
        self.state = BATTERY_STATE_OFF
        self._notify()

    def _restart_from_zero(self) -> None:
        """Entering Drive (or losing the setpoint) leaves the battery at 0 W."""
        self.regulator.resync(0.0)
        for bat in self.batteries:
            bat.written = None
            bat.mismatch_reads = 0
        self._starting = True
        self._hold_until = 0.0

    # ------------------------------------------------------------------
    # Regulation
    # ------------------------------------------------------------------
    def _target_for(self, color: str | None) -> float:
        key = {
            TEMPO_RED: CONF_BATTERY_GRID_TARGET_RED_W,
            TEMPO_WHITE: CONF_BATTERY_GRID_TARGET_WHITE_W,
        }.get(color, CONF_BATTERY_GRID_TARGET_BLUE_W)
        return float(self._cfg.get(key, DEFAULT_BATTERY_GRID_TARGET_W))

    def _precharge_wanted(self) -> bool:
        return self._coordinator.tempo_next_color == TEMPO_RED and self._coordinator.is_off_peak_now()

    @callback
    def _on_grid_event(self, event: Event) -> None:
        if not self.active:
            return
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return  # keep the current setpoint
        try:
            grid_w = float(new_state.state)
        except ValueError:
            return
        if self._lock.locked():
            _LOGGER.debug("Battery Drive: reading skipped, previous one still being written")
            return
        self.hass.async_create_task(self.async_handle_reading(grid_w, time.monotonic()))

    async def async_handle_reading(self, grid_w: float, at: float) -> None:
        """Recompute and write the setpoints for a grid reading (+ = import) taken at *at*."""
        async with self._lock:
            if not self.active:
                return
            if at < self._hold_until:
                return  # the battery is still switching to Drive
            if self._forcing or self._forced_device() is not None:
                await self._async_sync_locked(at)  # applies the force, or ends it
                return
            precharge = self._precharge_wanted()
            off_peak = self._coordinator.is_off_peak_now()
            batteries, limits = self._available(allow_discharge=not (precharge or off_peak))
            if not batteries:
                return

            reg = self.regulator
            reg.max_discharge = sum(lim.max_discharge for lim in limits)
            reg.max_charge = sum(lim.max_charge for lim in limits)
            self.target_w = self._target_for(self._coordinator.tempo_color)
            reg.target = self.target_w
            if precharge:
                reg.last_grid = grid_w
                reg.last_base = reg.applied(at)
                reg.setpoint = precharge_setpoint(grid_w, reg.last_base, reg.max_charge, self._subscription_w)
            else:
                reg.update(grid_w, at)

            parts = split_setpoint(reg.setpoint, limits)
            changed = False
            for bat, part in zip(batteries, parts):
                changed |= await self._async_write(bat, round(part))
            if changed:
                reg.accepted(sum(b.written or 0 for b in batteries), at=at)
                if self._starting and any(b.written for b in batteries):
                    self._starting = False
                    self._hold_until = at + BATTERY_DRIVE_STARTUP_HOLD_S

            if self._starting or at < self._hold_until:
                self.state = BATTERY_STATE_MODE_LOST if self._recovering else BATTERY_STATE_STARTUP_WAIT
            else:
                self._recovering = False
                self.state = BATTERY_STATE_PRECHARGE if precharge else BATTERY_STATE_REGULATING
            self._notify()

    def _available(self, allow_discharge: bool) -> tuple[list[_Battery], list[BatteryLimits]]:
        """Batteries with a known SOC (or none configured) and what each may do."""
        batteries: list[_Battery] = []
        limits: list[BatteryLimits] = []
        for bat in self.batteries:
            soc = state_float(self.hass, bat.soc_entity)
            if bat.soc_entity and soc is None:
                bat.written = None  # unavailable: left out of the split
                continue
            batteries.append(bat)
            limits.append(battery_limits(
                bat.max_discharge_w, bat.max_charge_w, soc,
                min_soc=self._soc_min, max_soc=BATTERY_DRIVE_MAX_SOC,
                allow_discharge=allow_discharge,
            ))
        return batteries, limits

    # ------------------------------------------------------------------
    # Force (switch.helios_battery_force)
    # ------------------------------------------------------------------
    async def _async_apply_force(self, at: float) -> None:
        """Write the forced setpoint: fixed power, SOC limits only (the
        subscription and the off-peak rule do not apply)."""
        dev = self._forced_device()
        if dev is None:
            return
        self._forcing = True
        batteries, limits = self._available(allow_discharge=True)
        if not batteries:
            return  # no SOC known: keep the current setpoints
        discharge = dev.force_direction == BATTERY_FORCE_DISCHARGE
        full_w = sum(b.max_discharge_w if discharge else b.max_charge_w for b in batteries)
        power = full_w if dev.force_power_w is None else min(float(dev.force_power_w), full_w)
        parts = split_setpoint(power if discharge else -power, limits)
        if power > 0 and all(abs(p) < 1 for p in parts):
            # Every battery is at its SOC limit: the force is over
            await self._async_cancel_force(dev, "SOC limit reached")
            if not self._wanted():
                self._forcing = False
                await self._async_deactivate()
            else:
                self._leave_force()
            return
        changed = False
        for bat, part in zip(batteries, parts):
            changed |= await self._async_write(bat, round(part))
        if changed:
            self.regulator.accepted(sum(b.written or 0 for b in batteries), at=at)
        self.regulator.setpoint = float(sum(round(p) for p in parts))
        self._starting = False
        self._hold_until = 0.0
        self.state = BATTERY_STATE_FORCED
        self._notify()

    async def _async_cancel_force(self, dev: BatteryDevice, reason: str) -> None:
        _LOGGER.info("Battery Drive: force ended (%s)", reason)
        dev.force_until = None
        await self._coordinator.device_manager.async_persist_device_state()

    def _leave_force(self) -> None:
        """Back to regulation, from what the batteries deliver now (bumpless)."""
        self._forcing = False
        self.regulator.resync(float(sum(b.written or 0 for b in self.batteries)))
        self._starting = False
        self._hold_until = 0.0
        self.state = BATTERY_STATE_REGULATING
        self._notify()

    async def _async_write(self, bat: _Battery, value: int, force: bool = False) -> bool:
        """Write *value* to the battery's number entity if it changed; True if written."""
        if not force and bat.written == value:
            return False
        try:
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": bat.number_entity, "value": value},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 — retried on the next reading
            _LOGGER.warning("Battery Drive: setpoint %d W not written to %s: %s", value, bat.number_entity, err)
            return False
        bat.written = value
        return True

    # ------------------------------------------------------------------
    # Robustness
    # ------------------------------------------------------------------
    async def _async_check_setpoints(self, _now: Any = None) -> None:
        """Every 60 s: a number that differs from what was written, twice in a
        row, means the integration lost the setpoint (e.g. reset to 0 W by a
        restart). Two reads avoid acting on a read that crossed a write."""
        if not self.active or self._lock.locked():
            return
        async with self._lock:
            if self._forcing and self._forced_device() is None:
                await self._async_sync_locked(time.monotonic())  # the force expired
                if not self.active:
                    return
            lost = []
            for bat in self.batteries:
                if bat.written is None:
                    bat.mismatch_reads = 0
                    continue
                value = state_float(self.hass, bat.number_entity)
                if value is not None and abs(value - bat.written) > BATTERY_DRIVE_TOLERANCE_W:
                    bat.mismatch_reads += 1
                    if bat.mismatch_reads >= BATTERY_DRIVE_LOST_READS:
                        lost.append((bat, value))
                else:
                    bat.mismatch_reads = 0
            if not lost:
                return
            for bat, value in lost:
                _LOGGER.warning(
                    "Battery Drive: %s shows %.0f W instead of %d W: setpoint lost, restarting from 0 W",
                    bat.number_entity, value, bat.written,
                )
            self._restart_from_zero()
            if self._forced_device() is not None:
                await self._async_apply_force(time.monotonic())
                return
            self._recovering = True
            self.state = BATTERY_STATE_MODE_LOST
            self._notify()

    async def _async_refresh_drive(self, _now: Any = None) -> None:
        """Every hour: call the Drive scripts again, then rewrite the setpoints
        (switching to Drive may reset them to 0 W)."""
        if not self.active:
            return
        async with self._lock:
            for bat in self.batteries:
                await self._async_run_script(bat.drive_script, bat.name)
            for bat in self.batteries:
                if bat.written is not None:
                    await self._async_write(bat, bat.written, force=True)
            self.regulator.accepted(sum(b.written or 0 for b in self.batteries))

    async def _async_run_script(self, script: str | None, battery: str) -> None:
        """Run a user script and wait for it to finish (a mode change takes ~26 s)."""
        if not script:
            return
        domain, _, object_id = script.partition(".")
        try:
            async with asyncio.timeout(BATTERY_DRIVE_SCRIPT_TIMEOUT_S):
                await self.hass.services.async_call(domain or "script", object_id, {}, blocking=True)
        except TimeoutError:
            _LOGGER.warning("Battery Drive: %s (%s) still running after %.0f s", script, battery, BATTERY_DRIVE_SCRIPT_TIMEOUT_S)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Battery Drive: %s (%s) failed: %s", script, battery, err)

    # ------------------------------------------------------------------
    # Exposed data
    # ------------------------------------------------------------------
    @property
    def setpoint_w(self) -> float:
        """Total setpoint computed on the last reading (+ = discharge)."""
        return self.regulator.setpoint if self.active else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "regulation_state": self.state,
            "setpoint_w":       round(self.setpoint_w),
            "target_w":         round(self.target_w),
            "last_grid_w":      round(self.regulator.last_grid),
            "forcing":          self._forcing,
            "batteries": [
                {"name": b.name, "entity": b.number_entity, "written_w": b.written}
                for b in self.batteries
            ],
        }
