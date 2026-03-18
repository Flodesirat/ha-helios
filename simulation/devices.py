"""Device model for the day simulation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class SimDevice:
    """A controllable device in the simulation."""

    name: str
    power_w: float

    # Scheduling
    allowed_start: float = 0.0    # hour
    allowed_end: float = 24.0     # hour
    priority: int = 5             # 1–10
    min_on_minutes: float = 0.0

    # Optional constraints
    run_quota_h: float | None = None   # pool: daily run-time target (hours)
    must_run_daily: bool = False       # water heater: legionella safety

    # Scoring weights (must sum ≈ 1.0)
    w_priority: float = 0.30
    w_fit: float = 0.40
    w_urgency: float = 0.30

    # ---- Runtime state (reset each simulation run) ----
    active: bool = field(default=False, init=False)
    _on_minutes: float = field(default=0.0, init=False, repr=False)   # minutes spent ON this cycle
    run_today_h: float = field(default=0.0, init=False)
    energy_kwh: float = field(default=0.0, init=False)
    energy_from_pv_kwh: float = field(default=0.0, init=False)

    # ---- Queries ----
    def in_window(self, hour: float) -> bool:
        if self.allowed_end > self.allowed_start:
            return self.allowed_start <= hour < self.allowed_end
        # Overnight window (e.g. 22h → 6h)
        return hour >= self.allowed_start or hour < self.allowed_end

    def satisfied(self) -> bool:
        if self.run_quota_h is not None:
            return self.run_today_h >= self.run_quota_h
        return False

    def min_on_respected(self) -> bool:
        """True when the device has been ON long enough to allow turn-off."""
        return self._on_minutes >= self.min_on_minutes

    # ---- State transitions ----
    def turn_on(self) -> None:
        if not self.active:
            self.active = True
            self._on_minutes = 0.0

    def turn_off(self) -> None:
        self.active = False
        self._on_minutes = 0.0

    def tick(self, step_minutes: float, pv_w: float, total_load_w: float) -> None:
        """Advance one simulation step."""
        if not self.active:
            return
        self._on_minutes += step_minutes
        step_h = step_minutes / 60.0
        self.run_today_h += step_h
        e = self.power_w * step_h / 1000.0
        self.energy_kwh += e
        pv_share = min(pv_w, total_load_w) / max(total_load_w, 1.0)
        self.energy_from_pv_kwh += e * pv_share


# ---------------------------------------------------------------------------
# Load from JSON / Default device set
# ---------------------------------------------------------------------------

def load_devices_from_json(path: str) -> list[SimDevice]:
    """Load a device list from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    devices = []
    for d in data:
        devices.append(SimDevice(
            name=d["name"],
            power_w=float(d["power_w"]),
            allowed_start=float(d.get("allowed_start", 0.0)),
            allowed_end=float(d.get("allowed_end", 24.0)),
            priority=int(d.get("priority", 5)),
            min_on_minutes=float(d.get("min_on_minutes", 0.0)),
            run_quota_h=float(d["run_quota_h"]) if "run_quota_h" in d else None,
            must_run_daily=bool(d.get("must_run_daily", False)),
            w_priority=float(d.get("w_priority", 0.30)),
            w_fit=float(d.get("w_fit", 0.40)),
            w_urgency=float(d.get("w_urgency", 0.30)),
        ))
    return devices


def default_devices() -> list[SimDevice]:
    return [
        SimDevice(
            name="Chauffe-eau",
            power_w=2000,
            allowed_start=8.0,
            allowed_end=18.0,
            priority=8,
            min_on_minutes=30,
            must_run_daily=True,
        ),
        SimDevice(
            name="Pompe piscine",
            power_w=800,
            allowed_start=8.0,
            allowed_end=20.0,
            priority=6,
            min_on_minutes=60,
            run_quota_h=5.0,
        ),
        SimDevice(
            name="Lave-vaisselle",
            power_w=1200,
            allowed_start=10.0,
            allowed_end=17.0,
            priority=4,
            min_on_minutes=90,
        ),
        SimDevice(
            name="Charge VE",
            power_w=3700,
            allowed_start=8.0,
            allowed_end=20.0,
            priority=7,
            min_on_minutes=120,
        ),
    ]
