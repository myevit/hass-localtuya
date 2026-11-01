"""All public native actions via the real HA service registry."""

import asyncio
from importlib import import_module
from types import SimpleNamespace
import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from .test_state import Device
from .test_profile import meal


@pytest.mark.asyncio
async def test_typed_services_validate_exact_target_and_use_owned_state(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    feeder = import_module("custom_components.localtuya.feeder")
    assert hasattr(feeder, "register_services"), "native typed services missing"
    d = Device(hass)
    d.feeder = feeder.FeederCapability(d)
    c = d.feeder
    await c.start()
    c.confirm_timeout = 0.1
    c.feed_timeout = 0.1
    c.cooldown = 0
    from homeassistant.helpers import device_registry as dr

    dr.async_setup(hass)
    await dr.async_load(hass)
    registry = er.async_get(hass)
    await registry.async_load()
    record = registry.async_get_or_create(
        "sensor",
        "localtuya",
        "local_synthetic-device_1",
        suggested_object_id="synthetic_meal_plan",
    )
    c.entity_id = record.entity_id
    hass.data["localtuya"] = {
        "synthetic-entry": SimpleNamespace(devices={"192.0.2.1": d})
    }
    feeder.register_services(hass)
    c.received({"1": encode([meal(), meal("22:00")])}, d.generation)

    async def call(name, fields):
        fields = {"entity_id": record.entity_id, **fields}
        task = asyncio.create_task(
            hass.services.async_call(
                "localtuya", name, fields, blocking=True, return_response=True
            )
        )
        previous = len(d.writes)
        while len(d.writes) == previous and not task.done():
            await asyncio.sleep(0)
        if len(d.writes) > previous:
            c.received(d.writes[-1], d.generation)
        return await task

    assert (
        await call(
            "set_feeder_schedule",
            dict(expected_revision=1, meals=[meal("11:00"), meal("22:00")]),
        )
    )["confirmed"]
    assert (
        await call(
            "add_feeder_meal", dict(expected_revision=c.revision, meal=meal("23:00"))
        )
    )["confirmed"]
    assert (
        await call(
            "update_feeder_meal",
            dict(expected_revision=c.revision, meal_index=1, meal=meal("21:00")),
        )
    )["confirmed"]
    assert (
        await call(
            "delete_feeder_meal", dict(expected_revision=c.revision, meal_index=2)
        )
    )["confirmed"]
    count = len(d.writes)
    for fields in [
        dict(expected_revision=True, meals=[]),
        dict(expected_revision=c.revision, meals=[], entity_id="sensor.missing"),
        dict(expected_revision=c.revision, meals=[], unexpected=True),
    ]:
        with pytest.raises((ServiceValidationError, vol.Invalid)):
            await call("set_feeder_schedule", fields)
    assert len(d.writes) == count
    c.received({"4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    task = asyncio.create_task(
        hass.services.async_call(
            "localtuya",
            "feed",
            {"entity_id": record.entity_id, "portions": 8, "confirmed": True},
            blocking=True,
            return_response=True,
        )
    )
    while len(d.writes) == count and not task.done():
        await asyncio.sleep(0)
    assert d.writes[-1] == {"3": 8}
    c.received({"4": "feeding"}, d.generation)
    c.received({"14": 8}, d.generation)
    assert (await task)["reported"]
    await c.stop()
    with pytest.raises(ServiceValidationError):
        await call("add_feeder_meal", dict(expected_revision=c.revision, meal=meal()))
