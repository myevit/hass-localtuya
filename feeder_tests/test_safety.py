"""Concurrency, provenance and uncertain-outcome safety matrix."""

import asyncio
import json
import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from custom_components.localtuya.feeder import FeederCapability
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from .test_profile import meal
from .test_state import state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cause", ["timeout", "disconnect", "conflict", "cancel", "transport_error"]
)
async def test_schedule_uncertain_no_resend(cause, tmp_path):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    if cause == "transport_error":

        async def fail(dps, generation, on_attempt):
            on_attempt()
            d.writes.append(dps)
            raise OSError("synthetic")

        d.send_feeder_once = fail
    task = asyncio.create_task(c.set_schedule(1, [meal("11:00")]))
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    if cause == "disconnect":
        c.disconnected()
    if cause == "conflict":
        c.received({"1": encode([meal("13:00")])}, d.generation)
    if cause == "cancel":
        task.cancel()
    if cause == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert not (await task)["confirmed"]
    assert c.uncertain and len(d.writes) == 1
    with pytest.raises(ServiceValidationError):
        await c.set_schedule(c.revision, [meal("11:00")])
    await c.stop()
    new = FeederCapability(d)
    await new.start()
    assert new.uncertain and len(d.writes) == 1
    # A genuine later complete snapshot reconciles a schedule, not a feed.
    new.received({"1": encode([meal("13:00")])}, d.generation)
    assert not new.uncertain
    await new.stop()


@pytest.mark.asyncio
async def test_full_replacement_cannot_delete_final_meal(tmp_path, monkeypatch):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    await asyncio.gather(*c._saves)
    before = (c.raw, c.meals, c.revision, c.uncertain, c.error, c._target)
    saves = []
    save = c.save

    async def record_save():
        saves.append(True)
        await save()

    monkeypatch.setattr(c, "save", record_save)
    with pytest.raises(ServiceValidationError, match="final meal"):
        await c.set_schedule(1, [])
    assert not d.writes and not saves
    assert (c.raw, c.meals, c.revision, c.uncertain, c.error, c._target) == before
    assert c._operation is None and not c.lock.locked()

    task = asyncio.create_task(c.set_schedule(1, [meal("11:00")]))
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    c.received(d.writes[0], d.generation)
    assert (await task)["confirmed"]
    c.received({"1": ""}, d.generation)
    assert c.meals == [] and c.raw == "" and not c.uncertain
    assert (await c.set_schedule(c.revision, []))["error"] == "unchanged_no_write"
    assert len(d.writes) == 1
    await c.stop()


@pytest.mark.asyncio
async def test_before_send_reservation_revision_and_generation_recheck(tmp_path):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    await asyncio.gather(*c._saves)
    save = c.save

    async def race():
        await save()
        if c._operation:
            c.received({"1": encode([meal("13:00")])}, d.generation)

    c.save = race
    result = await c.set_schedule(1, [meal("11:00")])
    assert result["error"] == "changed_before_send_no_write" and not d.writes
    c.save = save
    await c.stop()


@pytest.mark.asyncio
async def test_invalid_report_during_reservation_remains_fail_closed(tmp_path):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    await asyncio.gather(*c._saves)
    save = c.save

    async def race():
        await save()
        if c._operation:
            c.received({"1": "invalid"}, d.generation)

    c.save = race
    result = await c.set_schedule(1, [meal("11:00")])
    assert not d.writes and c.uncertain and c.error
    c.save = save
    await c.stop()


@pytest.mark.asyncio
async def test_crash_safe_reservation_exists_before_transport(tmp_path):
    c, d = await state(tmp_path)
    c.received({"4": "standby", "13": 0, "101": "strong_power"}, d.generation)

    async def fail_before_send(dps, generation, on_attempt):
        data = json.loads(c.store.path and open(c.store.path).read())["data"]
        assert data["feed_uncertain"] is True
        # Simulate process death with no finalization by restoring this reservation
        restored = FeederCapability(d)
        await restored.start()
        assert restored.feed_uncertain
        on_attempt()
        d.writes.append(dps)
        raise OSError("synthetic")

    d.send_feeder_once = fail_before_send
    result = await c.feed(8, True)
    assert result["state"] == "unknown" and c.feed_uncertain and len(d.writes) == 1
    c.received({"4": "standby", "14": 8}, d.generation)
    assert c.feed_uncertain
    with pytest.raises(ServiceValidationError):
        await c.feed(8, True)
    await c.stop()


@pytest.mark.asyncio
async def test_late_post_start_report_recovers_without_any_command(tmp_path):
    c, d = await state(tmp_path)
    c.received({"4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    assert (await c.feed(8, True))["state"] == "unknown"
    c.received({"4": "feeding"}, d.generation)
    assert c.feed_uncertain
    c.received({"14": 8}, d.generation)
    assert not c.feed_uncertain and c.feeding_state == "reported" and len(d.writes) == 1
    await c.stop()


@pytest.mark.asyncio
async def test_last_feed_only_same_generation_actual_edge(tmp_path):
    c, d = await state(tmp_path)
    c.received({"4": "feeding"}, d.generation)
    assert c.last_feed_at is None
    c.received({"14": 8}, d.generation)
    assert c.last_feed_at is None
    c.received({"4": "standby"}, d.generation)
    c.received({"4": "feeding", "14": 8}, d.generation)
    stamp = c.last_feed_at
    assert stamp
    c.received({"4": "feeding"}, d.generation)
    assert c.last_feed_at == stamp
    d.generation = object()
    c.received({"4": "feeding"}, d.generation)
    assert c.last_feed_at == stamp
    c.received({"4": "standby"}, object())  # obsolete socket frame cannot prime
    c.received({"4": "feeding"}, d.generation)
    assert c.last_feed_at == stamp
    await c.stop()
    new = FeederCapability(d)
    await new.start()
    assert new.last_feed_at == stamp
    await new.stop()


@pytest.mark.asyncio
async def test_concurrent_taps_reject_instead_of_queue_and_pending_releases(tmp_path):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    statuses = []
    c.subscribe(lambda: statuses.append(c.lock.locked()))
    task = asyncio.create_task(c.set_schedule(1, [meal("11:00")]))
    while not d.writes:
        await asyncio.sleep(0)
    with pytest.raises(ServiceValidationError):
        await c.set_schedule(1, [meal("14:00")])
    c.received(d.writes[0], d.generation)
    assert (await task)["confirmed"]
    assert statuses[-1] is False
    await c.stop()


@pytest.mark.asyncio
async def test_restored_snapshot_requires_explicit_acknowledgement(tmp_path):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    await c.stop()
    c = FeederCapability(d)
    await c.start()
    c.confirm_timeout = 0.1
    with pytest.raises(ServiceValidationError):
        await c.set_schedule(c.revision, [meal("14:00")])
    task = asyncio.create_task(c.set_schedule(c.revision, [meal("14:00")], True))
    while not d.writes:
        await asyncio.sleep(0)
    c.received(d.writes[0], d.generation)
    assert (await task)["confirmed"]
    await c.stop()


@pytest.mark.asyncio
async def test_shutdown_cancels_owned_transaction_and_retains_guard(tmp_path):
    c, d = await state(tmp_path)
    c.received({"4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    task = asyncio.create_task(c.feed(8, True))
    while not d.writes:
        await asyncio.sleep(0)
    await c.stop()
    assert task.cancelled() and c.closed and c.feed_uncertain
    assert c._operation is None and not c._saves and not c._listeners
    restored = FeederCapability(d)
    await restored.start()
    assert restored.feed_uncertain and len(d.writes) == 1
    await restored.stop()


@pytest.mark.asyncio
async def test_receipt_revision_and_time_are_not_cache_freshness(tmp_path):
    c, d = await state(tmp_path)
    raw = encode([meal()])
    c.received({"1": raw}, d.generation)
    stamp = c.last_received_at
    c.received({"4": "standby", "14": 8}, d.generation)
    assert c.revision == 1 and c.last_received_at == stamp
    c.received({"1": raw}, d.generation)
    assert c.revision == 2
    stamp = c.last_received_at
    await c.stop()
    restored = FeederCapability(d)
    await restored.start()
    assert (
        restored.revision == 2
        and restored.last_received_at == stamp
        and restored.snapshot_source == "restored"
    )
    await restored.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("4", "feeding"), ("13", 1), ("13", False), ("101", "weak_power")]
)
async def test_manual_feed_conservative_readiness_no_actuation(field, value, tmp_path):
    c, d = await state(tmp_path)
    c.received(
        {**{"4": "standby", "13": 0, "101": "strong_power"}, field: value}, d.generation
    )
    with pytest.raises(ServiceValidationError):
        await c.feed(8, True)
    assert not d.writes
    await c.stop()
