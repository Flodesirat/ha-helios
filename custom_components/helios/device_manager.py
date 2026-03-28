"""DeviceManager — orchestrates all managed devices: scoring, dispatch, state machines."""
from __future__ import annotations

import logging
import time as time_mod
from collections import deque
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .managed_device import ManagedDevice

from .const import (
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
    CONF_SCAN_INTERVAL_MINUTES, CONF_DISPATCH_THRESHOLD,
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    TEMPO_RED,
    APPLIANCE_STATE_IDLE, APPLIANCE_STATE_PREPARING, APPLIANCE_STATE_RUNNING,
    APPLIANCE_STATE_DONE,
    DEFAULT_SCAN_INTERVAL, DEFAULT_DISPATCH_THRESHOLD,
    STORAGE_KEY, STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# How long (seconds) power must stay below threshold to confirm cycle ended
_APPLIANCE_LOW_POWER_CONFIRM_S = 180


class DeviceManager:
    """Orchestrates all managed devices: scoring, dispatch, state machines."""

    def __init__(
        self,
        hass: HomeAssistant,
        devices_config: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> None:
        self.devices: list[ManagedDevice] = [ManagedDevice(c, config) for c in devices_config]
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._scan_interval: float = float(config.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL))
        self._dispatch_threshold: float = float(config.get(CONF_DISPATCH_THRESHOLD, DEFAULT_DISPATCH_THRESHOLD))
        # Decision log — rolling buffer, max 100 entries
        self.decision_log: deque[dict] = deque(maxlen=100)
        # Unsubscribe callbacks for appliance ready-entity listeners
        self._unsub_ready_listeners: list = []

    # ------------------------------------------------------------------
    # Startup — restore persisted device state
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Restore persisted device state from HA storage and reconcile switch states."""
        data: dict = await self._store.async_load() or {}
        today = date.today()
        now_ts = time_mod.time()

        for device in self.devices:
            stored = data.get(device.name, {})

            # Restore manual_mode (user-set, sticky across restarts)
            if stored.get("manual_mode", False):
                device.manual_mode = True
                _LOGGER.debug("Device '%s': restored manual_mode=True", device.name)

            # Pool-specific restoration
            if device.device_type == DEVICE_TYPE_POOL:
                stored_date_str: str | None = stored.get("date")
                if stored_date_str:
                    try:
                        stored_date = date.fromisoformat(stored_date_str)
                        if stored_date == today:
                            device.pool_daily_run_minutes = float(stored.get("minutes", 0.0))
                            device.pool_last_date = today
                            required = stored.get("required_minutes")
                            if required is not None:
                                device.pool_required_minutes_today = float(required)
                            _LOGGER.debug(
                                "Pool '%s': restored %.1f min done, %.1f min required for today",
                                device.name, device.pool_daily_run_minutes,
                                device.pool_required_minutes_today or 0.0,
                            )
                    except ValueError:
                        pass

                # Restore force/inhibit only if still active
                force_until = stored.get("pool_force_until")
                if force_until and float(force_until) > now_ts:
                    device.pool_force_until = float(force_until)
                    _LOGGER.debug(
                        "Pool '%s': restored force mode (%.0f s remaining)",
                        device.name, device.pool_force_until - now_ts,
                    )
                inhibit_until = stored.get("pool_inhibit_until")
                if inhibit_until and float(inhibit_until) > now_ts:
                    device.pool_inhibit_until = float(inhibit_until)
                    _LOGGER.debug(
                        "Pool '%s': restored inhibit mode (%.0f s remaining)",
                        device.name, device.pool_inhibit_until - now_ts,
                    )

            # Reconcile is_on from the actual HA switch state.
            # If the switch is physically ON, Helios resumes control without
            # interrupting it — turned_on_at is set to now so that min_on_minutes
            # is honoured before any turn-off decision.
            if device.switch_entity:
                state = self._hass.states.get(device.switch_entity)
                if state and state.state == "on":
                    device.is_on = True
                    device.turned_on_at = now_ts
                    _LOGGER.debug(
                        "Device '%s': resumed control (switch '%s' is ON)",
                        device.name, device.switch_entity,
                    )

        # Register immediate listeners on appliance ready entities.
        # This allows the prepare script to be triggered as soon as the user
        # sets the ready entity to ON, without waiting for the next 5-min cycle.
        for device in self.devices:
            if device.device_type == DEVICE_TYPE_APPLIANCE and device.appliance_ready_entity:
                self._register_appliance_ready_listener(device)

    def _register_appliance_ready_listener(self, device: ManagedDevice) -> None:
        """Watch the ready entity and immediately trigger prepare on rising edge."""

        def _make_cb(dev: ManagedDevice, ready_entity: str):
            async def _on_ready_change(event) -> None:  # noqa: ANN001
                new_state = event.data.get("new_state")
                if not new_state or new_state.state != "on":
                    return
                if dev.appliance_state != APPLIANCE_STATE_IDLE:
                    return  # Already in PREPARING / RUNNING — ignore
                _LOGGER.info(
                    "Appliance '%s': ready entity turned ON — triggering prepare immediately",
                    dev.name,
                )
                await self._async_appliance_to_preparing(self._hass, dev, ready_entity)
            return _on_ready_change

        unsub = async_track_state_change_event(
            self._hass,
            [device.appliance_ready_entity],
            _make_cb(device, device.appliance_ready_entity),
        )
        self._unsub_ready_listeners.append(unsub)

    def async_unload(self) -> None:
        """Cancel all appliance ready-entity listeners."""
        for unsub in self._unsub_ready_listeners:
            unsub()
        self._unsub_ready_listeners.clear()

    @staticmethod
    async def _async_appliance_to_preparing(
        hass: HomeAssistant, device: ManagedDevice, ready_entity: str
    ) -> None:
        """Transition an appliance from IDLE to PREPARING.

        Runs the prepare script (if any) and immediately resets the ready entity
        so the user's helper switch reflects that preparation is in progress.
        """
        device.appliance_state = APPLIANCE_STATE_PREPARING
        _LOGGER.info(
            "Appliance '%s': preparing — waiting for optimal start window", device.name
        )
        if device.appliance_prepare_script:
            await hass.services.async_call(
                "script", "turn_on",
                {"entity_id": device.appliance_prepare_script},
                blocking=False,
            )
        # Reset the ready entity straight away so the toggle shows as OFF
        await hass.services.async_call(
            "input_boolean", "turn_off",
            {"entity_id": ready_entity},
            blocking=False,
        )

    async def _async_save_device_data(self) -> None:
        """Persist device runtime state (manual_mode, pool counters, force/inhibit)."""
        data: dict = {}
        for device in self.devices:
            entry: dict = {"manual_mode": device.manual_mode}
            if device.device_type == DEVICE_TYPE_POOL:
                entry.update({
                    "date":             (device.pool_last_date or date.today()).isoformat(),
                    "minutes":          device.pool_daily_run_minutes,
                    "required_minutes": device.pool_required_minutes_today,
                    "pool_force_until":   device.pool_force_until,
                    "pool_inhibit_until": device.pool_inhibit_until,
                })
            data[device.name] = entry
        await self._store.async_save(data)

    async def async_persist_device_state(self) -> None:
        """Public entry point for switch entities to trigger an immediate persist."""
        await self._async_save_device_data()

    # ------------------------------------------------------------------
    # Main dispatch loop — called each coordinator cycle
    # ------------------------------------------------------------------
    async def async_dispatch(
        self,
        hass: HomeAssistant,
        score_input: dict[str, Any],
    ) -> None:
        reader = ManagedDevice._make_ha_reader(hass)
        global_score:       float       = score_input.get("global_score",       0.0)
        surplus_w:          float       = score_input.get("surplus_w",          0.0)
        bat_available_w:    float       = score_input.get("bat_available_w",    0.0)
        dispatch_threshold: float       = score_input.get("dispatch_threshold", self._dispatch_threshold)
        battery_soc:        float | None = score_input.get("battery_soc")
        configured_allowance_w: float   = float(score_input.get("grid_allowance_w", 250.0))
        pv_power_w:         float       = score_input.get("pv_power_w",         0.0)
        house_power_w:      float       = score_input.get("house_power_w",      0.0)
        tempo_color:        str | None  = score_input.get("tempo_color")
        soc_reserve_rouge:  float       = float(score_input.get("soc_reserve_rouge", DEFAULT_BATTERY_SOC_RESERVE_ROUGE))

        # Red-day strict mode: when SOC is below the battery reserve, do not
        # activate NEW devices unless they fit within the PV surplus alone.
        # Already-ON devices are not affected — we don't cut them off mid-cycle.
        _red_strict = (
            tempo_color == TEMPO_RED
            and battery_soc is not None
            and battery_soc < soc_reserve_rouge
        )

        # Base context injected into every decision log entry
        _base_ctx: dict = {
            "battery_soc": battery_soc,
            "pv_w":        round(pv_power_w),
            "house_w":     round(house_power_w),
        }

        # Mode "Pleine" (SOC ≥ 96 %) : autoriser un léger tirage réseau pour
        # décharger la batterie avant qu'elle atteigne 100 % et perde en efficacité.
        grid_allowance_w: float = configured_allowance_w if (battery_soc is not None and battery_soc >= 96.0) else 0.0
        if grid_allowance_w:
            _LOGGER.info(
                "Dispatch: SOC=%.0f%% (Pleine) — tolérance réseau +%.0fW activée",
                battery_soc, grid_allowance_w,
            )
        today  = date.today()
        now    = datetime.now().time()
        now_ts = time_mod.time()

        # ---- Update pool run counters (always, including during force mode) ----
        pool_changed = False
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL or device.manual_mode:
                continue
            before_minutes = device.pool_daily_run_minutes
            before_required = device.pool_required_minutes_today
            device.update_pool_run_time(self._scan_interval, today)
            device.try_capture_pool_required(reader, now.hour)
            if (device.pool_daily_run_minutes != before_minutes
                    or device.pool_required_minutes_today != before_required):
                pool_changed = True
        if pool_changed:
            await self._async_save_device_data()

        # ---- Pool force ON: maintain / expire ----
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL or device.pool_force_until is None or device.manual_mode:
                continue
            if now_ts < device.pool_force_until:
                if not device.is_on:
                    await self._async_set_switch(hass, device, True, reason="force_mode", context=_base_ctx)
            else:
                device.pool_force_until = None
                _LOGGER.info("Pool '%s': force mode expired", device.name)

        # ---- Pool inhibit: ensure off / expire ----
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_POOL or device.pool_inhibit_until is None or device.manual_mode:
                continue
            if now_ts < device.pool_inhibit_until:
                if device.is_on:
                    await self._async_set_switch(hass, device, False, reason="inhibit_mode", context=_base_ctx)
            else:
                device.pool_inhibit_until = None
                _LOGGER.info("Pool '%s': inhibit mode expired", device.name)

        def _helios_manages(device: ManagedDevice) -> bool:
            """False if Helios must not touch this device (manual mode, or pool locked)."""
            if device.manual_mode:
                return False
            if device.device_type == DEVICE_TYPE_POOL:
                if device.pool_force_until is not None and now_ts < device.pool_force_until:
                    return False
                if device.pool_inhibit_until is not None and now_ts < device.pool_inhibit_until:
                    return False
            return True

        # ---- Collect must-run overrides (skip devices Helios doesn't manage) ----
        must_run = {d for d in self.devices if d.must_run_now(reader) and _helios_manages(d)}

        # ---- Réserve zone (SOC ≤ 20 %): suppress non-safety overrides ----
        # In this zone the battery is critically low.  The water heater legionella
        # protection is a genuine safety override (health risk); pool filtration is
        # not — its urgency is already reflected in urgency_modifier().
        if battery_soc is not None and battery_soc <= 20.0 and must_run:
            suppressed = {d for d in must_run if d.device_type != DEVICE_TYPE_WATER_HEATER}
            if suppressed:
                _LOGGER.warning(
                    "Dispatch: SOC=%.0f%% (Réserve) — must_run supprimé pour: %s",
                    battery_soc,
                    ", ".join(d.name for d in suppressed),
                )
            must_run -= suppressed

        # ---- Gate: skip normal dispatch if global score too low ----
        if global_score < dispatch_threshold and not must_run:
            for device in self.devices:
                if device.device_type == DEVICE_TYPE_APPLIANCE:
                    # State machine always runs so IDLE→READY→RUNNING transitions
                    # are not blocked by a low global score.
                    await self._async_handle_appliance(
                        hass, device, global_score, surplus_w, bat_available_w
                    )
                    continue
                if not _helios_manages(device):
                    continue  # manual / force / inhibit — hands off
                if device.is_on and device.interruptible:
                    satisfied = device.is_satisfied(reader)
                    if satisfied or self._min_on_elapsed(device):
                        reason = "satisfied" if satisfied else "score_too_low"
                        await self._async_set_switch(hass, device, False, reason=reason, context=_base_ctx)
            return

        # ---- Priority preemption for PREPARING appliances ----
        # If a high-priority appliance is ready to start (score+fit or urgency)
        # but can't fit because lower-priority interruptible devices are running,
        # turn off those devices to free budget within this cycle.
        preparing_apps = [
            d for d in self.devices
            if d.device_type == DEVICE_TYPE_APPLIANCE
            and d.appliance_state == APPLIANCE_STATE_PREPARING
            and _helios_manages(d)
        ]
        for app in sorted(preparing_apps, key=lambda d: d.priority, reverse=True):
            urgency = app.urgency_modifier(reader)
            fit = ManagedDevice.compute_fit_score(app.power_w, surplus_w, bat_available_w)
            # Conditions to start are already met — no preemption needed
            if (global_score >= 0.4 and fit >= 0.3) or urgency >= 0.8:
                continue
            # Score not high enough regardless of budget — skip
            if global_score < 0.4 and urgency < 0.8:
                continue
            # Find lower-priority ON interruptible non-appliance devices
            candidates = sorted(
                [
                    d for d in self.devices
                    if d.device_type != DEVICE_TYPE_APPLIANCE
                    and d.is_on
                    and d.interruptible
                    and d.priority < app.priority
                    and _helios_manages(d)
                    and self._min_on_elapsed(d)
                ],
                key=lambda d: d.priority,  # Preempt lowest priority first
            )
            freed_w = 0.0
            to_preempt: list[ManagedDevice] = []
            for c in candidates:
                freed_w += c.actual_power_w(reader)
                to_preempt.append(c)
                if ManagedDevice.compute_fit_score(
                    app.power_w, surplus_w + freed_w, bat_available_w
                ) >= 0.3:
                    break
            else:
                continue  # Can't free enough budget even with all candidates
            for c in to_preempt:
                _LOGGER.info(
                    "Dispatch: preempting '%s' (priority=%d) to start appliance '%s' (priority=%d)",
                    c.name, c.priority, app.name, app.priority,
                )
                await self._async_set_switch(
                    hass, c, False,
                    reason="preempted",
                    context={**_base_ctx, "preempted_by": app.name},
                )
            surplus_w += freed_w  # Make freed budget visible to appliance state machine

        # ---- Score all eligible devices ----
        scored: list[tuple[float, ManagedDevice]] = []

        for device in self.devices:
            # Devices not under Helios control are skipped entirely
            if not _helios_manages(device):
                continue

            # Appliance state machine is handled separately
            if device.device_type == DEVICE_TYPE_APPLIANCE:
                await self._async_handle_appliance(
                    hass, device, global_score, surplus_w, bat_available_w
                )
                continue

            # Must-run override → bypass allowed window and force on immediately.
            # Safety overrides (legionella, off-peak HC heating) must not be blocked
            # by a misconfigured or too-narrow allowed window.
            if device in must_run:
                if not device.is_on:
                    await self._async_set_switch(hass, device, True, reason="must_run", context=_base_ctx)
                continue

            # Outside allowed window → turn off
            if not device.is_in_allowed_window(now):
                if device.is_on and device.interruptible and self._min_on_elapsed(device):
                    await self._async_set_switch(hass, device, False, reason="outside_window", context=_base_ctx)
                continue

            # Already satisfied → turn off immediately (reaching target is always a valid stop)
            if device.is_satisfied(reader):
                if device.is_on and device.interruptible:
                    await self._async_set_switch(hass, device, False, reason="satisfied", context=_base_ctx)
                continue

            score = device.effective_score(reader, surplus_w, bat_available_w)
            scored.append((score, device))

        # ---- Greedy allocation (highest score first) ----
        scored.sort(key=lambda x: x[0], reverse=True)

        # Add back the power of currently-ON Helios devices: house_w already
        # includes their consumption, so surplus_w is already reduced by their
        # load. Without this correction, each cycle they would compete against
        # their own consumption and get turned off spuriously.
        helios_on_w = sum(d.actual_power_w(reader) for d in self.devices if d.is_on and _helios_manages(d))
        remaining = surplus_w + bat_available_w + grid_allowance_w + helios_on_w

        for score, device in scored:
            # For fit calculation, add back this device's actual draw if already ON
            # so it doesn't penalise itself when re-evaluated each cycle.
            # Use actual_power_w: a water heater whose thermostat has cut (0 W actual)
            # must not inflate fit_surplus with its nominal power.
            fit_surplus = surplus_w + (device.actual_power_w(reader) if device.is_on else 0)
            fit = ManagedDevice.compute_fit_score(device.power_w, fit_surplus, bat_available_w)

            # Skip if fit is negligible (would import too much from grid)
            if fit < 0.1:
                if device.is_on and device.interruptible and self._min_on_elapsed(device):
                    await self._async_set_switch(hass, device, False, reason="fit_negligible", context=_base_ctx)
                continue

            if device.power_w <= remaining:
                # Red-day strict guard: on red days below battery reserve, only
                # activate NEW devices that fit within the PV surplus alone.
                # This prevents the physical battery from being drained to power
                # devices on expensive red days when the reserve is not met.
                if not device.is_on and _red_strict and device.power_w > surplus_w:
                    _LOGGER.debug(
                        "Dispatch: '%s' blocked — red day strict mode "
                        "(SOC=%.0f%% < reserve=%.0f%%, power=%dW > surplus=%dW)",
                        device.name, battery_soc, soc_reserve_rouge,
                        device.power_w, surplus_w,
                    )
                    continue
                remaining -= device.power_w
                if not device.is_on:
                    await self._async_set_switch(
                        hass, device, True,
                        reason="dispatch",
                        context={
                            **_base_ctx,
                            "global_score":    round(global_score, 3),
                            "surplus_w":       round(surplus_w),
                            "bat_available_w": round(bat_available_w),
                            "fit":             round(fit, 3),
                        },
                    )
            else:
                if device.is_on and device.interruptible and self._min_on_elapsed(device):
                    await self._async_set_switch(
                        hass, device, False,
                        reason="no_budget",
                        context={
                            **_base_ctx,
                            "power_w":     device.power_w,
                            "remaining_w": round(remaining),
                        },
                    )

    # ------------------------------------------------------------------
    # Appliance state machine
    # ------------------------------------------------------------------
    async def _async_handle_appliance(
        self,
        hass: HomeAssistant,
        device: ManagedDevice,
        global_score: float,
        surplus_w: float,
        bat_available_w: float,
    ) -> None:
        reader = ManagedDevice._make_ha_reader(hass)
        now_ts = time_mod.time()

        if device.appliance_state == APPLIANCE_STATE_IDLE:
            # Normally the state-change listener (registered in async_setup) handles
            # the IDLE→PREPARING transition immediately when the ready entity turns ON.
            # This fallback fires on the first coordinator tick after HA startup if the
            # entity was already ON before Helios had a chance to register the listener.
            ready = ManagedDevice._state_bool(reader, device.appliance_ready_entity, fallback=False)
            if ready:
                await DeviceManager._async_appliance_to_preparing(
                    hass, device, device.appliance_ready_entity
                )
            return

        if device.appliance_state == APPLIANCE_STATE_PREPARING:
            fit     = ManagedDevice.compute_fit_score(device.power_w, surplus_w, bat_available_w)
            urgency = device.urgency_modifier(reader)

            should_start = (
                (global_score >= 0.4 and fit >= 0.3)
                or urgency >= 0.8   # deadline imminent → start regardless of surplus
            )
            if not should_start:
                return

            # Transition: PREPARING → RUNNING
            _LOGGER.info("Appliance '%s': starting (score=%.2f fit=%.2f urgency=%.2f)",
                         device.name, global_score, fit, urgency)

            if not device.appliance_start_script:
                _LOGGER.warning(
                    "Appliance '%s': no start_script configured — "
                    "cycle will be tracked but nothing will actually start",
                    device.name,
                )

            if device.appliance_start_script:
                await hass.services.async_call(
                    "script", "turn_on",
                    {"entity_id": device.appliance_start_script},
                    blocking=False,
                )

            device.appliance_state     = APPLIANCE_STATE_RUNNING
            device.appliance_cycle_start = now_ts
            device.is_on               = True
            return

        if device.appliance_state == APPLIANCE_STATE_RUNNING:
            done = False

            if device.appliance_power_entity:
                # Primary: detect power drop
                power = ManagedDevice._state_float(reader, device.appliance_power_entity)
                if power < device.appliance_power_threshold_w:
                    if device.appliance_low_power_since is None:
                        device.appliance_low_power_since = now_ts
                    elif now_ts - device.appliance_low_power_since >= _APPLIANCE_LOW_POWER_CONFIRM_S:
                        done = True
                else:
                    device.appliance_low_power_since = None
            elif device.appliance_cycle_start is not None:
                # Fallback: elapsed time
                elapsed_m = (now_ts - device.appliance_cycle_start) / 60
                done = elapsed_m >= device.appliance_cycle_duration_minutes

            if done:
                device.appliance_state          = APPLIANCE_STATE_DONE
                device.is_on                    = False
                device.appliance_cycle_start    = None
                device.appliance_low_power_since = None
                _LOGGER.info("Appliance '%s': cycle complete", device.name)
                if device.appliance_ready_entity:
                    await hass.services.async_call(
                        "input_boolean", "turn_off",
                        {"entity_id": device.appliance_ready_entity},
                        blocking=False,
                    )
            return

        if device.appliance_state == APPLIANCE_STATE_DONE:
            device.appliance_state = APPLIANCE_STATE_IDLE

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _min_on_elapsed(self, device: ManagedDevice) -> bool:
        """True if the device has been on long enough to allow turning it off."""
        if device.turned_on_at is None:
            return True
        elapsed_m = (time_mod.time() - device.turned_on_at) / 60
        return elapsed_m >= device.min_on_minutes

    async def _async_set_switch(
        self,
        hass: HomeAssistant,
        device: ManagedDevice,
        on: bool,
        reason: str = "",
        context: dict | None = None,
    ) -> None:
        entry: dict = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "device": device.name,
            "action": "on" if on else "off",
            "reason": reason or "unknown",
        }
        if context:
            entry.update(context)
        self.decision_log.append(entry)
        if device.device_type == DEVICE_TYPE_EV:
            script = device.ev_charge_start_script if on else device.ev_charge_stop_script
            if script:
                await hass.services.async_call(
                    "script", "turn_on",
                    {"entity_id": script},
                    blocking=False,
                )
            elif device.switch_entity:
                await hass.services.async_call(
                    "homeassistant",
                    "turn_on" if on else "turn_off",
                    {"entity_id": device.switch_entity},
                    blocking=False,
                )
        elif device.switch_entity:
            await hass.services.async_call(
                "homeassistant",
                "turn_on" if on else "turn_off",
                {"entity_id": device.switch_entity},
                blocking=False,
            )
        device.is_on = on
        if on:
            device.turned_on_at  = time_mod.time()
        else:
            device.turned_off_at = time_mod.time()
        _LOGGER.debug("Device '%s' → %s", device.name, "ON" if on else "OFF")
