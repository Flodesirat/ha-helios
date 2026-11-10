"""Binary sensors — timestamp attributes."""
from __future__ import annotations

from custom_components.helios.binary_sensor import _epoch_to_iso


def test_epoch_to_iso_is_utc():
    assert _epoch_to_iso(0) == "1970-01-01T00:00:00+00:00"
    assert _epoch_to_iso(None) is None
