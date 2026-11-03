"""Real HA entry/platform registration, unload and reload; transport forbidden."""

from pathlib import Path
import asyncio
from types import MappingProxyType
from unittest.mock import AsyncMock
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant import loader
from custom_components.localtuya.coordinator import TuyaDevice
from custom_components.localtuya.discovery import TuyaDiscovery
from .test_device import device_config


@pytest.mark.asyncio
async def test_real_ha_setup_unload_reload_retains_registry_identity(
    tmp_path, monkeypatch
):
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).parents[1] / "custom_components", target_is_directory=True
    )
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    loader.async_setup(hass)
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    monkeypatch.setattr(TuyaDiscovery, "start", AsyncMock())
    monkeypatch.setattr(TuyaDevice, "async_connect", AsyncMock())
    conf = device_config()
    conf["entities"].append(
        {"id": "1", "platform": "sensor", "friendly_name": "Meal plan"}
    )
    entry = ConfigEntry(
        domain="localtuya",
        title="Synthetic",
        data={
            "devices": {conf["device_id"]: conf},
            "region": "eu",
            "client_id": "",
            "client_secret": "",
            "user_id": "",
            "no_cloud": True,
        },
        options={},
        version=4,
        minor_version=1,
        source="user",
        unique_id="synthetic-entry",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state.value == "loaded"
    registered = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {r.unique_id for r in registered} == {
        "local_synthetic-device_1",
        "local_synthetic-device_3",
        "local_synthetic-device_last_feed",
        *{
            f"local_synthetic-device_{key}"
            for key in (
                "manual_portions",
                "arm_feed",
                "feed",
                "add_meal",
                "acknowledge_restored",
                "delete_selection",
                "feeder_status",
                "feeder_error",
            )
        },
    }
    plan = next(r for r in registered if r.unique_id.endswith("_1"))
    state = hass.states.get(plan.entity_id)
    assert state.attributes["native_feeder_profile"] == "ft11w"
    dev = hass.data["localtuya"][entry.entry_id].devices[conf["host"]]
    from custom_components.localtuya.core import pytuya
    from custom_components.localtuya.core.ha_entities.pet_feeders import encode
    from .test_profile import meal
    from .test_protocol import incoming, Transport

    proto = pytuya.TuyaProtocol(dev.id, dev.local_key, 3.5, False, dev)
    proto.connection_made(Transport())
    proto.local_key = proto.dispatcher.local_key = b"fedcba9876543210"
    dev._interface = proto
    proto.data_received(
        incoming(
            proto,
            {"1": encode([meal()]), "4": "standby", "13": 0, "101": "strong_power"},
        )
    )
    c = dev.feeder
    c.cooldown = 0
    c.feed_timeout = 0.1
    c.confirm_timeout = 0.1
    schedule = asyncio.create_task(
        hass.services.async_call(
            "localtuya",
            "set_feeder_schedule",
            {
                "entity_id": plan.entity_id,
                "expected_revision": 1,
                "meals": [meal("10:00")],
            },
            blocking=True,
            return_response=True,
        )
    )
    while not proto.transport.writes and not schedule.done():
        await asyncio.sleep(0)
    proto.data_received(incoming(proto, {"14": 8}))
    assert not schedule.done() and c.revision == 1
    proto.data_received(incoming(proto, {"1": encode([meal("10:00")])}))
    assert (await schedule)["confirmed"]
    expected_frames = [{"1": encode([meal("10:00")])}]
    for service, fields, meals in [
        ("add_feeder_meal", {"meal": meal("22:00")}, [meal("10:00"), meal("22:00")]),
        (
            "update_feeder_meal",
            {"meal_index": 0, "meal": meal("11:00")},
            [meal("11:00"), meal("22:00")],
        ),
        ("delete_feeder_meal", {"meal_index": 1}, [meal("11:00")]),
    ]:
        count = len(proto.transport.writes)
        action = asyncio.create_task(
            hass.services.async_call(
                "localtuya",
                service,
                {
                    "entity_id": plan.entity_id,
                    "expected_revision": c.revision,
                    **fields,
                },
                blocking=True,
                return_response=True,
            )
        )
        while len(proto.transport.writes) == count and not action.done():
            await asyncio.sleep(0)
        expected_frames.append({"1": encode(meals)})
        proto.data_received(incoming(proto, expected_frames[-1]))
        assert (await action)["confirmed"]
    feed = asyncio.create_task(
        hass.services.async_call(
            "localtuya",
            "feed",
            {
                "entity_id": plan.entity_id,
                "portions": 8,
                "confirmed": True,
            },
            blocking=True,
            return_response=True,
        )
    )
    while len(proto.transport.writes) < 5 and not feed.done():
        await asyncio.sleep(0)
    proto.data_received(incoming(proto, {"3": 8}, pytuya.CMDType.CONTROL_NEW, 1))
    assert not feed.done()
    proto.data_received(incoming(proto, {"4": "feeding"}))
    proto.data_received(incoming(proto, {"14": 8}))
    assert (await feed)["reported"]
    frames = [
        pytuya.parser.unpack_message(w, hmac_key=proto.local_key, no_retcode=True)
        for w in proto.transport.writes
    ]
    import json

    assert [
        json.loads(f.payload[15:])["data"]["dps"] for f in frames
    ] == expected_frames + [{"3": 8}]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert dev.feeder.closed and not dev.feeder._listeners and not dev.feeder._saves
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(plan.entity_id).id == plan.id
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()
