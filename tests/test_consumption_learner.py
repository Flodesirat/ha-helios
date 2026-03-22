"""Tests for ConsumptionLearner (EMA base-load profile).

Covers:
- EMA update formula
- Slot clamping at day boundaries
- Negative net_base_w clamped to 0
- Cold-start from fallback function
- Cold-start flat default (no fallback)
- as_base_load_fn returns correct values and is a snapshot (isolated from mutations)
- Persistence round-trip (save → load)
- sample_count tracking
- profile is None before async_load
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.helios.consumption_learner import ConsumptionLearner, SLOTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_learner(alpha: float = 0.1) -> ConsumptionLearner:
    """Build a ConsumptionLearner with a mock Store (no real HA)."""
    hass = MagicMock()
    learner = ConsumptionLearner.__new__(ConsumptionLearner)
    learner._hass = hass
    learner._alpha = alpha
    learner._profile = None
    learner._sample_count = 0
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_delay_save = MagicMock()
    store.async_save = AsyncMock()
    learner._store = store
    return learner


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------

class TestColdStart:

    @pytest.mark.asyncio
    async def test_no_storage_no_fallback_uses_flat_300w(self):
        learner = _make_learner()
        await learner.async_load(fallback_fn=None)

        assert learner.profile is not None
        assert len(learner.profile) == SLOTS
        assert all(v == 300.0 for v in learner.profile)
        assert learner.sample_count == 0

    @pytest.mark.asyncio
    async def test_no_storage_with_fallback_uses_fallback(self):
        learner = _make_learner()
        # Flat 500 W fallback
        await learner.async_load(fallback_fn=lambda _: 500.0)

        assert all(v == 500.0 for v in learner.profile)
        assert learner.sample_count == 0

    @pytest.mark.asyncio
    async def test_fallback_negative_clamped_to_zero(self):
        """Fallback values < 0 must be clamped to 0 during initialisation."""
        learner = _make_learner()
        await learner.async_load(fallback_fn=lambda _: -100.0)

        assert all(v == 0.0 for v in learner.profile)

    @pytest.mark.asyncio
    async def test_loads_from_storage_when_available(self):
        learner = _make_learner()
        stored_profile = [float(i) for i in range(SLOTS)]
        learner._store.async_load = AsyncMock(return_value={
            "profile": stored_profile,
            "sample_count": 42,
        })

        await learner.async_load(fallback_fn=lambda _: 999.0)

        assert learner.profile == stored_profile
        assert learner.sample_count == 42

    @pytest.mark.asyncio
    async def test_ignores_storage_with_wrong_length(self):
        """Corrupt / old storage data with wrong length falls back to fallback_fn."""
        learner = _make_learner()
        learner._store.async_load = AsyncMock(return_value={
            "profile": [100.0] * 100,   # wrong length
            "sample_count": 5,
        })

        await learner.async_load(fallback_fn=lambda _: 200.0)

        assert all(v == 200.0 for v in learner.profile)
        assert learner.sample_count == 0


# ---------------------------------------------------------------------------
# EMA update
# ---------------------------------------------------------------------------

class TestUpdate:

    @pytest.mark.asyncio
    async def test_ema_formula(self):
        """profile[slot] = α × new + (1-α) × old."""
        learner = _make_learner(alpha=0.1)
        await learner.async_load(fallback_fn=lambda _: 400.0)

        learner.update(slot=0, net_base_w=600.0)

        expected = 0.1 * 600.0 + 0.9 * 400.0
        assert abs(learner.profile[0] - expected) < 1e-9

    @pytest.mark.asyncio
    async def test_other_slots_unchanged(self):
        learner = _make_learner(alpha=0.1)
        await learner.async_load(fallback_fn=lambda _: 400.0)

        learner.update(slot=5, net_base_w=600.0)

        for i, v in enumerate(learner.profile):
            if i != 5:
                assert v == 400.0

    @pytest.mark.asyncio
    async def test_negative_input_clamped_to_zero(self):
        learner = _make_learner(alpha=0.5)
        await learner.async_load(fallback_fn=lambda _: 400.0)

        learner.update(slot=10, net_base_w=-200.0)

        # net_base_w clamped to 0 → 0.5×0 + 0.5×400 = 200
        assert abs(learner.profile[10] - 200.0) < 1e-9

    @pytest.mark.asyncio
    async def test_slot_wraps_modulo_288(self):
        """Slot indices outside [0, 287] wrap correctly."""
        learner = _make_learner(alpha=0.1)
        await learner.async_load(fallback_fn=lambda _: 400.0)

        learner.update(slot=SLOTS, net_base_w=600.0)   # slot 288 → wraps to 0
        expected = 0.1 * 600.0 + 0.9 * 400.0
        assert abs(learner.profile[0] - expected) < 1e-9

    @pytest.mark.asyncio
    async def test_sample_count_increments(self):
        learner = _make_learner()
        await learner.async_load()

        assert learner.sample_count == 0
        learner.update(slot=0, net_base_w=300.0)
        assert learner.sample_count == 1
        learner.update(slot=1, net_base_w=300.0)
        assert learner.sample_count == 2

    @pytest.mark.asyncio
    async def test_update_before_load_is_noop(self):
        """Calling update() before async_load() must not raise."""
        learner = _make_learner()
        # profile is None — must be a no-op
        learner.update(slot=0, net_base_w=500.0)
        assert learner.profile is None
        assert learner.sample_count == 0


# ---------------------------------------------------------------------------
# as_base_load_fn
# ---------------------------------------------------------------------------

class TestAsBaseLoadFn:

    @pytest.mark.asyncio
    async def test_returns_correct_slot_value(self):
        learner = _make_learner()
        await learner.async_load(fallback_fn=lambda _: 0.0)
        # Set a known value at slot 12 (= 1h00)
        learner._profile[12] = 750.0

        fn = learner.as_base_load_fn()
        assert fn(1.0) == 750.0   # hour=1.0 → slot = int(1.0 * 12) = 12

    @pytest.mark.asyncio
    async def test_fn_is_snapshot_not_live(self):
        """Mutating the profile after taking a snapshot must not affect the fn."""
        learner = _make_learner()
        await learner.async_load(fallback_fn=lambda _: 300.0)

        fn = learner.as_base_load_fn()
        original = fn(0.0)

        # Mutate profile in-place
        learner._profile[0] = 9999.0

        assert fn(0.0) == original   # snapshot is unaffected

    @pytest.mark.asyncio
    async def test_returns_flat_300_when_profile_is_none(self):
        learner = _make_learner()
        # profile not loaded
        fn = learner.as_base_load_fn()
        assert fn(0.0) == 300.0
        assert fn(12.5) == 300.0

    @pytest.mark.asyncio
    async def test_hour_wraps_at_midnight(self):
        """Hour 24 should map to slot 0."""
        learner = _make_learner()
        await learner.async_load(fallback_fn=lambda _: 0.0)
        learner._profile[0] = 123.0

        fn = learner.as_base_load_fn()
        assert fn(24.0) == 123.0   # int(24*12) % 288 = 288 % 288 = 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:

    @pytest.mark.asyncio
    async def test_schedule_save_calls_delay_save(self):
        learner = _make_learner()
        await learner.async_load()

        learner.schedule_save()

        learner._store.async_delay_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_schedule_save_noop_when_profile_none(self):
        learner = _make_learner()
        # profile is None — must not raise and must not call delay_save
        learner.schedule_save()
        learner._store.async_delay_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_serialize_contains_profile_and_count(self):
        learner = _make_learner()
        await learner.async_load(fallback_fn=lambda _: 200.0)
        learner._sample_count = 17

        data = learner._serialize()

        assert "profile" in data
        assert "sample_count" in data
        assert data["sample_count"] == 17
        assert len(data["profile"]) == SLOTS
        assert all(v == 200.0 for v in data["profile"])
