"""Real HA storage/clock; synthetic transport only."""

import asyncio
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from .test_profile import meal


class Device:
    id = "synthetic-device"
    generation = object()
    feeder_ready = True

    def __init__(self, hass):
        self.hass = hass
        self.writes = []

    async def send_feeder_once(self, dps, generation, on_attempt):
        assert generation is self.generation
        on_attempt()
        self.writes.append(dps)


async def state(tmp_path):
    name = "custom_components.localtuya.feeder"
    assert find_spec(name), "TuyaDevice-owned capability missing"
    hass = HomeAssistant(str(tmp_path))
    device = Device(hass)
    c = import_module(name).FeederCapability(device)
    await c.start()
    c.confirm_timeout = 0.05
    c.feed_timeout = 0.05
    c.cooldown = 0
    return c, device


@pytest.mark.asyncio
async def test_schedule_waits_for_exact_receipt_and_preserves_sibling(tmp_path):
    c, d = await state(tmp_path)
    from custom_components.localtuya.core.ha_entities.pet_feeders import encode

    c.received({"1": encode([meal(), meal("22:00")])}, d.generation)
    task = asyncio.create_task(c.meal_action("update", 1, 0, meal("10:00")))
    while not d.writes:
        await asyncio.sleep(0)
    assert c.meals[0]["time"] == "12:34"
    assert d.writes == [{"1": encode([meal("10:00"), meal("22:00")])}]
    c.received({"14": 3}, d.generation)
    assert not task.done()
    c.received(d.writes[0], d.generation)
    assert (await task)["confirmed"]
    assert c.meals[1] == meal("22:00")
    await c.stop()


@pytest.mark.asyncio
async def test_timeout_persists_uncertainty_restart_never_resends(tmp_path):
    c, d = await state(tmp_path)
    from custom_components.localtuya.core.ha_entities.pet_feeders import encode

    c.received({"1": encode([meal()])}, d.generation)
    result = await c.set_schedule(1, [meal("10:00")])
    assert result["error"] == "unconfirmed_timeout"
    await c.stop()
    new = type(c)(d)
    await new.start()
    assert new.uncertain and new.snapshot_source == "restored"
    with pytest.raises(ServiceValidationError):
        await new.set_schedule(1, [meal("10:00")], True)
    assert len(d.writes) == 1
    await new.stop()


@pytest.mark.asyncio
async def test_feed_dp3_once_requires_post_start_report_not_echo_or_ack(tmp_path):
    c, d = await state(tmp_path)
    c.received({"4": "standby", "13": 0, "101": "strong_power", "14": 8}, d.generation)
    assert hasattr(c, "feed"), "native feed action missing"
    task = asyncio.create_task(c.feed(8, True))
    while not d.writes:
        await asyncio.sleep(0)
    assert d.writes == [{"3": 8}]
    c.received({"3": 8, "14": 8}, d.generation)
    assert not task.done()
    c.received({"4": "feeding", "14": 8}, d.generation)
    assert c.last_feed_at and not task.done()
    c.received({"14": 8}, d.generation)
    assert (await task)["reported"]
    assert not c.feed_uncertain
    await c.stop()


@pytest.mark.asyncio
async def test_feed_rejects_unconfirmed_or_invalid_quantity(tmp_path):
    c, d = await state(tmp_path)
    assert hasattr(c, "feed"), "native feed action missing"
    c.received({"4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    for portions, confirmed in [(1, False), (True, True), (0, True), (61, True)]:
        with pytest.raises(ServiceValidationError):
            await c.feed(portions, confirmed)
    assert d.writes == []
    await c.stop()
