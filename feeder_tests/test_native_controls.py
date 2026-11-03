"""Native entity contracts against real HA; all data and transports synthetic."""

import asyncio
import base64
from datetime import time, timezone
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localtuya.const import PLATFORMS
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from .test_profile import meal
from .test_state import state


def test_time_is_runtime_only():
    from custom_components.localtuya.const import RUNTIME_PLATFORMS

    assert "time" in RUNTIME_PLATFORMS
    assert "time" not in PLATFORMS.values()


@pytest.mark.asyncio
async def test_native_time_preserves_all_other_bytes(tmp_path):
    from custom_components.localtuya.feeder_entities import MealTime

    c, d = await state(tmp_path)
    d.feeder = c
    baseline = [meal(), meal("21:17")]
    c.received({"1": encode(baseline)}, d.generation)
    entity = MealTime(d, 0)
    task = asyncio.create_task(entity.async_set_value(time(9, 23)))
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    expected = [dict(baseline[0], time="09:23"), baseline[1]]
    assert d.writes == [{"1": encode(expected)}]
    assert entity.native_value == time(12, 34)  # no optimistic confirmation
    c.received(d.writes[0], d.generation)
    await task
    await c.stop()


@pytest.mark.asyncio
async def test_local_selection_then_deliberate_single_feed(tmp_path):
    from custom_components.localtuya.feeder_entities import ManualPortions, FeedButton

    c, d = await state(tmp_path)
    d.feeder = c
    c.received(
        {"1": encode([meal()]), "4": "standby", "13": 0, "101": "strong_power"},
        d.generation,
    )
    number, button = ManualPortions(d), FeedButton(d)
    await number.async_set_native_value(8.0)
    assert c.manual_portions == 8 and not d.writes
    await number.async_set_native_value(9.0)
    assert not d.writes
    task = asyncio.create_task(button.async_press())
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    assert d.writes == [{"3": 9}]
    with pytest.raises(ServiceValidationError):
        await button.async_press()
    c.received({"4": "feeding"}, d.generation)
    c.received({"14": 9}, d.generation)
    await task
    assert not c._native_arms
    await c.stop()


@pytest.mark.parametrize(
    "value", [True, False, 0, 61, 1.2, float("nan"), float("inf"), "8"]
)
@pytest.mark.asyncio
async def test_native_numbers_reject_invalid_values_without_write(tmp_path, value):
    from custom_components.localtuya.feeder_entities import ManualPortions, MealPortions

    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()])}, d.generation)
    for entity in (ManualPortions(d), MealPortions(d, 0)):
        with pytest.raises(ServiceValidationError):
            await entity.async_set_native_value(value)
    assert not d.writes
    await c.stop()


@pytest.mark.parametrize(
    "value", [time(1, 2, 3), time(1, 2, microsecond=1), time(1, 2, tzinfo=timezone.utc)]
)
@pytest.mark.asyncio
async def test_time_rejects_lossy_inputs(tmp_path, value):
    from custom_components.localtuya.feeder_entities import MealTime

    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()])}, d.generation)
    with pytest.raises(ServiceValidationError):
        await MealTime(d, 0).async_set_value(value)
    assert not d.writes
    await c.stop()


@pytest.mark.parametrize(
    "field,value,offset", [("portions", 17.0, 3), ("enabled", False, 4)]
)
@pytest.mark.asyncio
async def test_native_single_field_byte_preservation(tmp_path, field, value, offset):
    from custom_components.localtuya.feeder_entities import MealPortions, MealEnabled

    c, d = await state(tmp_path)
    d.feeder = c
    raw = encode([meal("09:13"), meal("22:51")])
    c.received({"1": raw}, d.generation)
    entity = MealPortions(d, 0) if field == "portions" else MealEnabled(d, 0)
    action = (
        entity.async_set_native_value(value)
        if field == "portions"
        else entity.async_turn_off()
    )
    task = asyncio.create_task(action)
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    before, after = base64.b64decode(raw), base64.b64decode(d.writes[0]["1"])
    assert (
        before[:offset] == after[:offset]
        and before[offset + 1 :] == after[offset + 1 :]
    )
    c.received(d.writes[0], d.generation)
    await task
    await c.stop()


@pytest.mark.asyncio
async def test_delete_target_revision_and_compaction(tmp_path):
    from custom_components.localtuya.feeder_entities import DeleteMeal

    c, d = await state(tmp_path)
    d.feeder = c
    baseline = [meal("08:17"), meal("12:39"), meal("21:18")]
    c.received({"1": encode(baseline)}, d.generation)
    stale = DeleteMeal(d, 1)
    c.received({"1": encode(baseline)}, d.generation)
    with pytest.raises(ServiceValidationError):
        await stale.async_press()
    assert not d.writes
    task = asyncio.create_task(DeleteMeal(d, 1).async_press())
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    assert d.writes == [{"1": encode([baseline[0], baseline[2]])}]
    c.received(d.writes[0], d.generation)
    await task
    assert c.meals[1] == baseline[2] and not c._native_arms
    assert not d.writes[0].get("3")
    c.received({"1": encode([baseline[0]])}, d.generation)
    with pytest.raises(ServiceValidationError):
        await DeleteMeal(d, 0).async_press()
    assert len(d.writes) == 1
    await c.stop()


@pytest.mark.asyncio
async def test_add_disabled_and_capacity(tmp_path):
    from custom_components.localtuya.feeder_entities import AddMeal

    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()])}, d.generation)
    task = asyncio.create_task(AddMeal(d).async_press())
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    from custom_components.localtuya.core.ha_entities.pet_feeders import decode

    records = decode(d.writes[0]["1"])
    assert records[0] == meal() and records[-1] == dict(
        meal("00:00"), portions=1, enabled=False
    )
    c.received(d.writes[0], d.generation)
    await task
    c.received({"1": encode([meal()] * 10)}, d.generation)
    with pytest.raises(ServiceValidationError):
        await AddMeal(d).async_press()
    assert len(d.writes) == 1
    await c.stop()


@pytest.mark.asyncio
async def test_restored_ack_is_exact_one_use_and_never_persisted(tmp_path):
    from custom_components.localtuya.feeder_entities import (
        AcknowledgeRestored,
        MealTime,
    )

    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()])}, d.generation)
    await c.stop()
    c = type(c)(d)
    d.feeder = c
    await c.start()
    c.confirm_timeout = 0.02
    assert c.snapshot_source == "restored"
    with pytest.raises(ServiceValidationError):
        await MealTime(d, 0).async_set_value(time(8, 17))
    await AcknowledgeRestored(d).async_press()
    assert c.native_armed("restored") and c.snapshot_source == "restored"
    c.revision += 1
    c.publish()
    assert not c.native_armed("restored")
    await AcknowledgeRestored(d).async_press()
    c.disconnected()
    assert not c._native_arms
    c.received({"4": "standby"}, d.generation)
    await AcknowledgeRestored(d).async_press()
    task = asyncio.create_task(MealTime(d, 0).async_set_value(time(8, 17)))
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    assert not c.native_armed("restored")
    c.received(d.writes[0], d.generation)
    await task
    await c.stop()
    assert not c._native_arms
    assert "_native_arms" not in await c.store.async_load()


@pytest.mark.asyncio
async def test_stale_entity_baseline_and_feed_reservation_revision_race(
    tmp_path, monkeypatch
):
    from custom_components.localtuya.feeder_entities import MealTime, FeedButton

    c, d = await state(tmp_path)
    d.feeder = c
    c.received(
        {"1": encode([meal()]), "4": "standby", "13": 0, "101": "strong_power"},
        d.generation,
    )
    stale = MealTime(d, 0)
    c.received({"1": encode([meal("08:17")])}, d.generation)
    with pytest.raises(ServiceValidationError):
        await stale.async_set_value(time(9, 31))
    original = c.save

    async def changed_while_saving():
        await original()
        c.received({"1": encode([meal("19:27")])}, d.generation)

    monkeypatch.setattr(c, "save", changed_while_saving)
    with pytest.raises(ServiceValidationError, match="changed_before_send_no_write"):
        await FeedButton(d).async_press()
    assert not d.writes and not c._native_arms
    monkeypatch.setattr(c, "save", original)
    await c.stop()
