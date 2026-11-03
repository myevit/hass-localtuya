"""Restored native edit readiness uses the existing one-shot provenance grant."""

import asyncio
from datetime import time

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localtuya.feeder_entities import (
    AcknowledgeRestored,
    AddMeal,
    DeleteMeal,
    MealTime,
    MealPortions,
    MealEnabled,
    FeedButton,
    ManualPortions,
)
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from .test_profile import meal
from .test_state import state


@pytest.mark.parametrize("kind", ["delete", "add", "time", "portions", "enabled"])
@pytest.mark.asyncio
async def test_restored_native_edit_grant_consumed_before_single_write(tmp_path, kind):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal("08:17"), meal("21:39")])}, d.generation)
    await c.stop()
    c = type(c)(d)
    d.feeder = c
    await c.start()
    c.received({"4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    c.confirm_timeout = 0.02
    entity = {
        "delete": lambda: DeleteMeal(d, 0),
        "add": lambda: AddMeal(d),
        "time": lambda: MealTime(d, 0),
        "portions": lambda: MealPortions(d, 0),
        "enabled": lambda: MealEnabled(d, 0),
    }[kind]()
    assert not entity.available
    assert FeedButton(d).available and ManualPortions(d).available
    ack = AcknowledgeRestored(d)
    assert ack.available
    await ack.async_press()
    assert entity.available and entity.available  # read-only peek never consumes
    assert c.native_armed("restored") and not d.writes
    action = {
        "delete": lambda: entity.async_press(),
        "add": lambda: entity.async_press(),
        "time": lambda: entity.async_set_value(time(9, 23)),
        "portions": lambda: entity.async_set_native_value(17),
        "enabled": lambda: entity.async_turn_off(),
    }[kind]
    task = asyncio.create_task(action())
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    assert len(d.writes) == 1 and "1" in d.writes[0]
    assert not c.native_armed("restored") and not entity.available
    with pytest.raises(ServiceValidationError):
        await task  # unknown outcome cannot make the edit callable again
    assert not entity.available and not ack.available
    c.received(d.writes[0], d.generation)
    assert entity.available and not c.native_armed("restored")
    assert len(d.writes) == 1
    await c.stop()
