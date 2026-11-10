"""Grid regulation for batteries in Drive mode — pure logic, no Home Assistant.

``PassiveRegulator`` comes from the marstek-smartmeter project
(``emulator/marstek_api.py``), where it was tuned by replaying recorded days.
``split_setpoint`` spreads its setpoint over several batteries.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


class PassiveRegulator:
    """Battery setpoint regulated on the grid power (+ = import).

    Each grid reading is corrected at once and in full: the new setpoint is
    the power the battery was *applying when the grid was measured* plus the
    error to the target. (A delay, a gain below 1 or a deadband were tried on
    a replayed day: they cost about 20 % more import without reducing export.)

    The base is the setpoint applied when the grid was measured, not the
    latest one sent: with a slow meter (~10-15 s) a reading often predates
    the last command, and adding it to that command would apply the same
    error twice. A setpoint counts as applied *settle* seconds after it was
    accepted.

    The grid is regulated towards *target* (W, + = import): a small positive
    target keeps drawing a little from the grid rather than risking export.

    The setpoint (+ = discharge, − = charge) is clamped to the power limits
    and, when the state of charge is known, to its levels: no discharge at or
    below *min_soc*, no charge at or above *max_soc* (%).
    """

    def __init__(
        self,
        max_discharge: float = 2500.0,
        max_charge: float = 2500.0,
        settle: float = 1.0,
        target: float = 0.0,
        min_soc: float = 20.0,
        max_soc: float = 100.0,
    ) -> None:
        self.max_discharge = max_discharge
        self.max_charge = max_charge
        self.settle = settle
        self.target = target
        self.soc: float | None = None
        self.min_soc = min_soc
        self.max_soc = max_soc
        self.setpoint = 0.0
        self.last_base = 0.0
        self.last_grid = 0.0
        # (monotonic time, power) of the setpoints the battery accepted.
        self._accepted: list[tuple[float, float]] = [(float("-inf"), 0.0)]

    def clamp(self, power: float) -> float:
        high, low = self.max_discharge, -self.max_charge
        if self.soc is not None and self.soc <= self.min_soc:
            high = min(high, 0.0)
        if self.soc is not None and self.soc >= self.max_soc:
            low = max(low, 0.0)
        return max(low, min(high, power))

    def accepted(self, power: float, at: float | None = None) -> None:
        """Record that the battery accepted *power*."""
        self._accepted.append((time.monotonic() if at is None else at, power))
        del self._accepted[:-20]

    def applied(self, at: float | None = None) -> float:
        """The setpoint the battery was delivering at *at* (default: now)."""
        at = time.monotonic() if at is None else at
        applied = self._accepted[0][1]
        for accepted_at, power in self._accepted:
            if accepted_at <= at - self.settle:
                applied = power
        return applied

    def update(self, grid: float, at: float | None = None) -> float:
        """Correct for *grid* (W, + = import) measured at *at*; return the setpoint."""
        at = time.monotonic() if at is None else at
        self.last_grid = grid
        self.last_base = self.applied(at)
        self.setpoint = self.clamp(self.last_base + grid - self.target)
        return self.setpoint

    def resync(self, battery_power: float) -> None:
        """Restart from the power the battery actually delivers (bumpless start)."""
        self.setpoint = self.clamp(battery_power)
        self._accepted = [(float("-inf"), self.setpoint)]


@dataclass
class BatteryLimits:
    """What one battery may do right now (W, both ≥ 0)."""

    max_discharge: float
    max_charge: float

    def clamp(self, power: float) -> float:
        return max(-self.max_charge, min(self.max_discharge, power))


def battery_limits(
    max_discharge: float,
    max_charge: float,
    soc: float | None,
    min_soc: float,
    max_soc: float,
    allow_discharge: bool = True,
) -> BatteryLimits:
    """Limits of one battery given its SOC: none in a direction its level forbids."""
    discharge = max_discharge
    charge = max_charge
    if not allow_discharge or (soc is not None and soc <= min_soc):
        discharge = 0.0
    if soc is not None and soc >= max_soc:
        charge = 0.0
    return BatteryLimits(max(0.0, discharge), max(0.0, charge))


def split_setpoint(total: float, limits: list[BatteryLimits]) -> list[float]:
    """Spread *total* evenly over the batteries, each part clamped to its limits.

    Each battery gets total / N. What a battery cannot take (empty, full, at
    its power limit) goes to the others, evenly again: an even split alone
    would never converge, since the regulator's base would stay at the part
    the free batteries already deliver.
    """
    parts = [0.0] * len(limits)
    open_ = list(range(len(limits)))
    remaining = total
    while open_ and abs(remaining) > 1e-9:
        share = remaining / len(open_)
        still_open = []
        for i in open_:
            wanted = parts[i] + share
            parts[i] = limits[i].clamp(wanted)
            if parts[i] == wanted:
                still_open.append(i)
        remaining = total - sum(parts)
        if len(still_open) == len(open_):
            break
        open_ = still_open
    return parts
