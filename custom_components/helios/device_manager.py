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

from .managed_device import ManagedDevice, BatteryDevice

from .const import (
    DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC, DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_BATTERY_ENABLED, CONF_BATTERY_MAX_CHARGE_POWER_W,
    DEFAULT_BATTERY_SOC_RESERVE_ROUGE,
    TEMPO_RED,
    APPLIANCE_STATE_IDLE, APPLIANCE_STATE_PREPARING, APPLIANCE_STATE_RUNNING,
    APPLIANCE_STATE_DONE,
    DEFAULT_SCAN_INTERVAL,
    STORAGE_KEY, STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# How long (seconds) power must stay below threshold to confirm cycle ended
_APPLIANCE_LOW_POWER_CONFIRM_S = 180


def compute_fit(
    power_w: float,
    remaining: float,
    bat_available_w: float,
    grid_allowance_w: float,
) -> float:
    """Compute fit score [0..1] for a device against the current remaining budget.

    Zone 1 (surplus pur) : device ≤ remaining − bat_available → [0..1]
    Zone 2 (batterie)    : device ≤ remaining               → [0.4..1]
    Zone 3 (réseau)      : device ≤ remaining + grid_allowance → [0..0.4]
    Hors budget          : 0.0
    """
    surplus_pur = remaining - bat_available_w
    if power_w <= 0:
        return 0.0
    if power_w <= surplus_pur:
        return power_w / max(1.0, surplus_pur)
    if power_w <= remaining:
        bat_used = power_w - surplus_pur
        return 1.0 - 0.6 * (bat_used / max(1.0, bat_available_w))
    if grid_allowance_w > 0 and power_w <= remaining + grid_allowance_w:
        import_w = power_w - remaining
        return 0.4 * (1.0 - import_w / grid_allowance_w)
    return 0.0


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
        self._coordinator = None  # Set by EnergyOptimizerCoordinator after construction
        self._scan_interval: float = float(config.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL))
        # Decision log — rolling buffer, max 100 entries
        self.decision_log: deque[dict] = deque(maxlen=100)
        # Remaining dispatch budget after last greedy allocation
        self.remaining_w: float = 0.0
        # BatteryDevice — instantiated if battery is enabled in config
        self.battery_device: BatteryDevice | None = (
            BatteryDevice(config) if config.get(CONF_BATTERY_ENABLED) else None
        )
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

            # EV-specific: restore is_on and handle stop script if charge ended during downtime.
            if device.device_type == DEVICE_TYPE_EV and stored.get("is_on", False):
                switch_state = self._hass.states.get(device.switch_entity) if device.switch_entity else None
                if switch_state and switch_state.state == "on":
                    # Charger still on — resume as if nothing happened (reconcile below handles it)
                    pass
                else:
                    # Charger turned off during HA downtime — fire stop script if configured
                    _LOGGER.info(
                        "Device '%s': was charging before restart but switch is now OFF — "
                        "executing stop script (charge ended during downtime)",
                        device.name,
                    )
                    if device.ev_charge_stop_script:
                        self._hass.async_create_task(
                            self._hass.services.async_call(
                                "script", "turn_on",
                                {"entity_id": device.ev_charge_stop_script},
                                blocking=False,
                            )
                        )

            # Appliance-specific: restore PREPARING/RUNNING state so appliances survive restarts.
            if device.device_type == DEVICE_TYPE_APPLIANCE:
                stored_app_state = stored.get("appliance_state")
                if stored_app_state == APPLIANCE_STATE_PREPARING:
                    device.appliance_state = APPLIANCE_STATE_PREPARING
                    deadline_str = stored.get("appliance_deadline_dt")
                    if deadline_str:
                        try:
                            device.appliance_deadline_dt = datetime.fromisoformat(deadline_str)
                        except ValueError:
                            pass
                    _LOGGER.info(
                        "Appliance '%s': restored PREPARING state (deadline=%s)",
                        device.name,
                        device.appliance_deadline_dt.strftime("%H:%M") if device.appliance_deadline_dt else "none",
                    )
                elif stored_app_state == APPLIANCE_STATE_RUNNING:
                    cycle_start = stored.get("appliance_cycle_start")
                    duration_s = (device.appliance_cycle_duration_minutes or 0) * 60
                    if cycle_start is not None and duration_s > 0:
                        elapsed_s = now_ts - float(cycle_start)
                        if elapsed_s >= duration_s:
                            # Cycle finished during downtime — mark as done and reset ready entity
                            device.appliance_state = APPLIANCE_STATE_DONE
                            _LOGGER.info(
                                "Appliance '%s': cycle ended during HA downtime (%.0f s ago) — marking DONE",
                                device.name, elapsed_s - duration_s,
                            )
                            if device.appliance_ready_entity:
                                self._hass.async_create_task(
                                    self._hass.services.async_call(
                                        "input_boolean", "turn_off",
                                        {"entity_id": device.appliance_ready_entity},
                                        blocking=False,
                                    )
                                )
                        else:
                            # Cycle still running — resume with original start time
                            device.appliance_state       = APPLIANCE_STATE_RUNNING
                            device.appliance_cycle_start = float(cycle_start)
                            device.is_on                 = True
                            device.turned_on_at          = float(cycle_start)
                            _LOGGER.info(
                                "Appliance '%s': restored RUNNING state (%.0f s / %.0f s elapsed)",
                                device.name, elapsed_s, duration_s,
                            )
                    else:
                        # No cycle_start or duration — can't safely resume, treat as DONE
                        device.appliance_state = APPLIANCE_STATE_DONE
                        _LOGGER.warning(
                            "Appliance '%s': was RUNNING but cycle_start/duration missing — marking DONE",
                            device.name,
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

        # Restore battery manual_mode
        if self.battery_device is not None:
            bat_stored = data.get("__battery__", {})
            if bat_stored.get("manual_mode", False):
                self.battery_device.manual_mode = True
                _LOGGER.debug("BatteryDevice: restored manual_mode=True")

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
                await self._async_save_device_data()
                if self._coordinator is not None:
                    await self._coordinator.async_request_refresh()
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
        device.appliance_state       = APPLIANCE_STATE_PREPARING
        device.appliance_deadline_dt = device._compute_auto_deadline(datetime.now())
        _LOGGER.info(
            "Appliance '%s': preparing — deadline auto %s",
            device.name,
            device.appliance_deadline_dt.strftime("%H:%M"),
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
        if not hasattr(self, "_store"):
            return
        data: dict = {}
        for device in self.devices:
            entry: dict = {"manual_mode": device.manual_mode}
            if device.device_type == DEVICE_TYPE_EV:
                entry["is_on"] = device.is_on
            elif device.device_type == DEVICE_TYPE_APPLIANCE:
                entry["appliance_state"] = device.appliance_state
                if device.appliance_deadline_dt is not None:
                    entry["appliance_deadline_dt"] = device.appliance_deadline_dt.isoformat()
                if device.appliance_cycle_start is not None:
                    entry["appliance_cycle_start"] = device.appliance_cycle_start
            elif device.device_type == DEVICE_TYPE_POOL:
                entry.update({
                    "date":             (device.pool_last_date or date.today()).isoformat(),
                    "minutes":          device.pool_daily_run_minutes,
                    "required_minutes": device.pool_required_minutes_today,
                    "pool_force_until":   device.pool_force_until,
                    "pool_inhibit_until": device.pool_inhibit_until,
                })
            data[device.name] = entry
        if self.battery_device is not None:
            data["__battery__"] = {"manual_mode": self.battery_device.manual_mode}
        await self._store.async_save(data)

    async def async_persist_device_state(self) -> None:
        """Public entry point for switch entities to trigger an immediate persist."""
        await self._async_save_device_data()

    async def async_force_start_appliance(self, hass: HomeAssistant, device_slug: str) -> bool:
        """Immediately start an appliance that is in PREPARING (waiting) state.

        Returns True if the appliance was started, False otherwise.
        Called by the helios.start_appliance service.
        """
        from homeassistant.util import slugify
        device = next(
            (d for d in self.devices if slugify(d.name) == device_slug),
            None,
        )
        if device is None:
            _LOGGER.warning("start_appliance: no device with slug '%s'", device_slug)
            return False
        if device.appliance_state != APPLIANCE_STATE_PREPARING:
            _LOGGER.warning(
                "start_appliance: '%s' is not in waiting state (state=%s)",
                device.name, device.appliance_state,
            )
            return False
        await self._async_start_appliance(hass, device, global_score=1.0, fit=1.0, urgency=1.0)
        return True

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
        battery_soc:        float | None = score_input.get("battery_soc")
        configured_allowance_w: float   = float(score_input.get("grid_allowance_w", 250.0))
        pv_power_w:         float       = score_input.get("pv_power_w",         0.0)
        house_power_w:      float       = score_input.get("house_power_w",      0.0)
        tempo_color:        str | None  = score_input.get("tempo_color")
        soc_reserve_rouge:  float       = float(score_input.get("soc_reserve_rouge", DEFAULT_BATTERY_SOC_RESERVE_ROUGE))
        soc_max:            float       = float(score_input.get("soc_max", 95.0))
        soc_min:            float       = float(score_input.get("soc_min", 20.0))

        # Red-day strict mode: when SOC is below the battery reserve, do not
        # activate NEW devices unless they fit within the PV surplus alone.
        # Already-ON devices are not affected — we don't cut them off mid-cycle.
        _red_strict = (
            tempo_color == TEMPO_RED
            and battery_soc is not None
            and battery_soc < soc_reserve_rouge
        )

        # SOC gate: when battery is below soc_min, block all new device activations.
        # The battery must be charged to its minimum level before devices compete
        # for PV surplus. Already-ON devices and must_run overrides are not affected.
        _soc_gate = (
            battery_soc is not None
            and battery_soc < soc_min
        )

        # Base context injected into every decision log entry
        _base_ctx: dict = {
            "battery_soc": battery_soc,
            "pv_w":        round(pv_power_w),
            "house_w":     round(house_power_w),
        }

        # Mode "Pleine" (SOC ≥ 96 %) : autoriser un léger tirage réseau pour
        # décharger la batterie avant qu'elle atteigne 100 % et perde en efficacité.
        grid_allowance_w: float = configured_allowance_w if (battery_soc is not None and battery_soc >= soc_max) else 0.0
        if grid_allowance_w:
            _LOGGER.info(
                "Dispatch: SOC=%.0f%% (Pleine, soc_max=%.0f%%) — tolérance réseau +%.0fW activée",
                battery_soc, soc_max, grid_allowance_w,
            )
        today  = date.today()
        now    = datetime.now().time()
        now_ts = time_mod.time()

        # ---- Update generic daily on-time counter (all devices) ----
        for device in self.devices:
            device.update_daily_on_time(self._scan_interval, today)

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

        # ---- Update BatteryDevice runtime state ----
        if self.battery_device is not None:
            self.battery_device.update(battery_soc, tempo_color == TEMPO_RED)

        # ---- Priority preemption for PREPARING appliances ----
        # If a high-priority appliance is ready to start (score+fit or urgency)
        # but can't fit because lower-priority interruptible devices are running,
        # turn off those devices to free budget within this cycle.
        # Preempted devices are tracked so the greedy loop doesn't immediately
        # re-activate them with the freed budget.
        preempted_this_cycle: set[ManagedDevice] = set()

        preparing_apps = [
            d for d in self.devices
            if d.device_type == DEVICE_TYPE_APPLIANCE
            and d.appliance_state == APPLIANCE_STATE_PREPARING
            and _helios_manages(d)
        ]
        for app in sorted(preparing_apps, key=lambda d: d.priority, reverse=True):
            urgency = app.urgency_modifier(reader)
            fit = ManagedDevice.compute_fit_score(app.power_w, surplus_w, bat_available_w, grid_allowance_w, tempo_color == TEMPO_RED)
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
                    app.power_w, surplus_w + freed_w, bat_available_w, grid_allowance_w, tempo_color == TEMPO_RED
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
                preempted_this_cycle.add(c)
            surplus_w += freed_w  # Make freed budget visible to appliance state machine

        # ---- Pre-pass: IDLE / RUNNING / DONE appliance states ----
        # These states don't compete for budget — handle them before scoring.
        for device in self.devices:
            if device.device_type != DEVICE_TYPE_APPLIANCE:
                continue
            if device.appliance_state == APPLIANCE_STATE_PREPARING:
                continue  # Handled by greedy loop below
            if _helios_manages(device):
                await self._async_handle_appliance(hass, device)

        # ---- Construction de la liste des candidats ----
        _now_dt = datetime.combine(date.today(), now)
        all_candidates: list = []

        for device in self.devices:
            if not _helios_manages(device):
                continue
            if device in preempted_this_cycle:
                continue
            if device.device_type == DEVICE_TYPE_APPLIANCE:
                if device.appliance_state in (APPLIANCE_STATE_IDLE, APPLIANCE_STATE_DONE):
                    continue  # Le pré-pass gère ces états
                # PREPARING et RUNNING : inclus pour le suivi budgétaire
            all_candidates.append(device)

        if self.battery_device is not None and not self.battery_device.is_manual(reader):
            if not self.battery_device.satisfied:
                all_candidates.append(self.battery_device)
            else:
                self.battery_device.is_on = False

        # ---- Phase 1 — Budget initial (surplus virtuel + batterie, sans tolérance réseau) ----
        remaining = surplus_w + bat_available_w

        # ---- Phase 2 — Appareils obligatoires ----
        obligatoire: set = set()

        for device in all_candidates:
            if isinstance(device, BatteryDevice):
                _urgency = device.urgency
            else:
                # Appareils RUNNING : maintenir actifs et déduire du budget
                if device.device_type == DEVICE_TYPE_APPLIANCE and device.appliance_state == APPLIANCE_STATE_RUNNING:
                    if device.is_on:
                        remaining -= device.actual_power_w(reader)
                        obligatoire.add(device)
                    continue

                # Hors plage horaire : ignoré (Phase 4 éteint si allumé et interruptible)
                if not device.is_in_allowed_window(now):
                    continue

                # Satisfait : éteindre si possible
                if device.is_satisfied(reader, now=_now_dt):
                    bypass_min_on = (
                        device.device_type == DEVICE_TYPE_WATER_HEATER
                        and device._is_off_peak(now)  # noqa: SLF001
                    )
                    if device.is_on and device.interruptible and (self._min_on_elapsed(device) or bypass_min_on):
                        await self._async_set_switch(hass, device, False, reason="satisfied", context=_base_ctx)
                    elif device.is_on and not self._min_on_elapsed(device):
                        # Satisfait mais min_on non écoulé → maintenir ON, protéger de la Phase 4
                        remaining -= device.actual_power_w(reader)
                        obligatoire.add(device)
                    continue

                _urgency = device.urgency_modifier(reader, now=_now_dt)

            if _urgency >= 1.0:
                # Allumage forcé par urgence
                _was_already_on = not isinstance(device, BatteryDevice) and device.is_on
                if not isinstance(device, BatteryDevice):
                    if device.device_type == DEVICE_TYPE_APPLIANCE and device.appliance_state == APPLIANCE_STATE_PREPARING:
                        _fit_p2 = compute_fit(device.power_w, remaining, bat_available_w, grid_allowance_w)
                        await self._async_start_appliance(hass, device, global_score, _fit_p2, _urgency)
                    elif not device.is_on:
                        await self._async_set_switch(hass, device, True, reason="urgency", context=_base_ctx)
                    device.last_urgency         = round(_urgency, 3)
                    device.last_effective_score = 1.0
                else:
                    device.is_on = True
                remaining -= device.actual_power_w(reader) if _was_already_on else device.power_w
                obligatoire.add(device)
            elif device.is_on and not self._min_on_elapsed(device):
                # min_on non écoulé : maintenir allumé
                remaining -= device.actual_power_w(reader)
                obligatoire.add(device)

        # ---- Phase 3 — Greedy (recalcul dynamique du fit à chaque itération) ----
        greedy_candidates: list = []
        for _d in all_candidates:
            if _d in obligatoire:
                continue
            if isinstance(_d, BatteryDevice):
                greedy_candidates.append(_d)
            else:
                if not _d.is_in_allowed_window(now):
                    continue
                if _d.is_satisfied(reader, now=_now_dt):
                    continue
                greedy_candidates.append(_d)
        selected: set = set()

        while greedy_candidates:
            # Recalculer fit et score effectif pour chaque candidat sur le remaining courant
            for d in greedy_candidates:
                if isinstance(d, BatteryDevice):
                    d.fit = 1.0
                else:
                    _pow = d.actual_power_w(reader) if d.is_on else d.power_w
                    _fit = compute_fit(_pow, remaining, bat_available_w, grid_allowance_w)
                    _urg = d.urgency_modifier(reader, now=_now_dt)
                    _pri = d.priority / 10.0
                    if d.device_type not in (
                        DEVICE_TYPE_EV, DEVICE_TYPE_WATER_HEATER, DEVICE_TYPE_HVAC,
                        DEVICE_TYPE_POOL, DEVICE_TYPE_APPLIANCE,
                    ):
                        _eff = 0.5 * _pri + 0.5 * _fit
                    else:
                        _eff = 0.4 * _pri + 0.3 * _fit + 0.3 * _urg
                    d.last_fit            = round(_fit, 3)
                    d.last_urgency        = round(_urg, 3)
                    d.last_priority_score = round(_pri, 3)
                    d.last_effective_score = round(_eff, 3)

            best = max(
                greedy_candidates,
                key=lambda d: d.effective_score if isinstance(d, BatteryDevice) else d.last_effective_score,
            )
            best_fit = best.fit if isinstance(best, BatteryDevice) else best.last_fit

            if best_fit <= 0.0:
                break  # Plus de budget utilisable

            _was_already_on = not isinstance(best, BatteryDevice) and best.is_on
            if isinstance(best, BatteryDevice):
                best.is_on = True
            elif (
                best.device_type == DEVICE_TYPE_APPLIANCE
                and best.appliance_state == APPLIANCE_STATE_PREPARING
                and not best.is_on
            ):
                await self._async_start_appliance(hass, best, global_score, best_fit, best.urgency_modifier(reader))
            elif best.is_on:
                pass  # Déjà allumé — retenu sans appel de service
            else:
                # Nouvelle activation — vérifier les gardes rouge/SOC
                if _red_strict and best.power_w > surplus_w:
                    _LOGGER.debug(
                        "Dispatch greedy: '%s' bloqué — red strict (power=%dW > surplus=%dW)",
                        best.name, best.power_w, surplus_w,
                    )
                    greedy_candidates.remove(best)
                    continue
                if _soc_gate:
                    _LOGGER.debug(
                        "Dispatch greedy: '%s' bloqué — SOC gate (SOC=%.0f%% < soc_min=%.0f%%)",
                        best.name, battery_soc, soc_min,
                    )
                    greedy_candidates.remove(best)
                    continue
                await self._async_set_switch(
                    hass, best, True,
                    reason="greedy",
                    context={
                        **_base_ctx,
                        "global_score":    round(global_score, 3),
                        "surplus_w":       round(surplus_w),
                        "bat_available_w": round(bat_available_w),
                        "fit":             round(best_fit, 3),
                    },
                )

            remaining -= best.actual_power_w(reader) if _was_already_on else best.power_w
            selected.add(best)
            greedy_candidates.remove(best)

        self.remaining_w = remaining

        # ---- Phase 4 — Extinction ----
        # Éteindre les appareils ON non retenus (ni obligatoires ni sélectionnés).
        # Exclure : appareils (machine d'état gère leur cycle) et BatteryDevice.
        for device in self.devices:
            if not device.is_on:
                continue
            if device in obligatoire or device in selected:
                continue
            if not _helios_manages(device):
                continue
            if device.device_type == DEVICE_TYPE_APPLIANCE:
                continue  # Machine d'état propriétaire
            if not device.interruptible:
                continue
            await self._async_set_switch(hass, device, False, reason="budget", context=_base_ctx)

    # ------------------------------------------------------------------
    # Appliance state machine
    # ------------------------------------------------------------------
    async def _async_handle_appliance(
        self,
        hass: HomeAssistant,
        device: ManagedDevice,
    ) -> None:
        """Handle IDLE, RUNNING and DONE appliance states.
        PREPARING is handled by the greedy allocation loop via _async_start_appliance."""
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
                device.turned_off_at            = now_ts
                device.appliance_cycle_start    = None
                device.appliance_low_power_since = None
                device.appliance_deadline_dt    = None
                _LOGGER.info("Appliance '%s': cycle complete", device.name)
                self.decision_log.append({
                    "ts":     datetime.now().isoformat(timespec="seconds"),
                    "device": device.name,
                    "action": "off",
                    "reason": "cycle_complete",
                })
                device.last_decision_reason = "cycle_complete"
                await self._async_save_device_data()
                if device.appliance_ready_entity:
                    await hass.services.async_call(
                        "input_boolean", "turn_off",
                        {"entity_id": device.appliance_ready_entity},
                        blocking=False,
                    )
            return False

        if device.appliance_state == APPLIANCE_STATE_DONE:
            device.appliance_state = APPLIANCE_STATE_IDLE
        return False

    async def _async_start_appliance(
        self,
        hass: HomeAssistant,
        device: ManagedDevice,
        global_score: float,
        fit: float,
        urgency: float,
    ) -> None:
        """Trigger the PREPARING → RUNNING transition for an appliance."""
        _LOGGER.info(
            "Appliance '%s': starting (score=%.2f fit=%.2f urgency=%.2f)",
            device.name, global_score, fit, urgency,
        )
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
        device.appliance_state       = APPLIANCE_STATE_RUNNING
        device.appliance_cycle_start = time_mod.time()
        device.is_on                 = True
        device.turned_on_at          = device.appliance_cycle_start
        self.decision_log.append({
            "ts":     datetime.now().isoformat(timespec="seconds"),
            "device": device.name,
            "action": "on",
            "reason": "appliance_start",
            "global_score": round(global_score, 3),
            "fit":          round(fit, 3),
            "urgency":      round(urgency, 3),
        })
        device.last_decision_reason = "appliance_start"

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
        device.last_decision_reason = reason or "unknown"
        device.last_reason          = reason or "unknown"
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
        if device.device_type == DEVICE_TYPE_EV:
            await self._async_save_device_data()
