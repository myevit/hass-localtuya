"""Real HA native services and registry, isolated synthetic transport only."""

from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant import loader
from homeassistant.helpers import device_registry as dr, entity_registry as er
from custom_components.localtuya.coordinator import TuyaDevice
from custom_components.localtuya.discovery import TuyaDiscovery
from custom_components.localtuya.core import pytuya
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from .test_device import device_config
from .test_profile import meal
from .test_protocol import Transport


@pytest.mark.asyncio
async def test_real_ha_native_service_dispatch_two_feeders_and_reload(
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
    configs = [
        device_config(),
        dict(
            device_config(),
            device_id="synthetic-second",
            host="192.0.2.2",
            friendly_name="Second synthetic feeder",
        ),
    ]
    configs.append(
        dict(
            device_config(),
            feeder_profile="none",
            device_id="synthetic-legacy",
            host="192.0.2.3",
            friendly_name="Synthetic legacy device",
        )
    )
    for config in configs:
        config["entities"].append(
            {"id": "1", "platform": "sensor", "friendly_name": "Meal plan"}
        )
    entry = ConfigEntry(
        domain="localtuya",
        title="Synthetic native controls",
        data={
            "devices": {c["device_id"]: c for c in configs},
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
        unique_id="synthetic-native-entry",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    registry = er.async_get(hass)
    ghosts = []
    for config in configs:
        for domain, key in (("switch", "arm_feed"), ("select", "delete_selection")):
            ghosts.append(registry.async_get_or_create(
                domain, "localtuya", f"local_{config['device_id']}_{key}",
                config_entry=entry,
            ))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.value == "loaded"
    all_devices = list(hass.data["localtuya"][entry.entry_id].devices.values())
    devices = [device for device in all_devices if device.feeder]
    assert len(devices) == 2 and all_devices[2].feeder is None
    writes = [[], []]
    for index, dev in enumerate(devices):
        proto = pytuya.TuyaProtocol(dev.id, dev.local_key, 3.5, False, dev)
        proto.connection_made(Transport())
        proto.local_key = b"fedcba9876543210"
        dev._interface = proto
        dev.feeder.cooldown = 0

        async def send(dps, generation, on_attempt, dev=dev, index=index):
            assert generation is dev.generation
            on_attempt()
            writes[index].append(dps)
            if "1" in dps:
                dev.feeder.received(dps, generation)
            else:
                dev.feeder.received({"4": "feeding"}, generation)
                dev.feeder.received({"14": dps["3"]}, generation)

        monkeypatch.setattr(dev, "send_feeder_once", send)
        dev.feeder.received(
            {
                "1": encode([meal("08:17"), meal("21:39")]),
                "4": "standby",
                "13": 0,
                "101": "strong_power",
            },
            dev.generation,
        )
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    def eid(dev, domain, key):
        result = registry.async_get_entity_id(
            domain, "localtuya", f"local_{dev.id}_{key}"
        )
        assert result is not None
        return result

    dev = devices[0]

    async def call(domain, service, key, **fields):
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": eid(dev, domain, key), **fields},
            blocking=True,
        )
        await hass.async_block_till_done()

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert all(registry.async_get(g.entity_id) is None for g in ghosts[:4])
    assert all(registry.async_get(g.entity_id) is not None for g in ghosts[4:])
    entries = [r for r in entries if r not in ghosts[4:]]
    assert len(entries) == 38  # 18 per supported feeder; two unchanged legacy entities
    assert {
        r.unique_id
        for r in entries
        if r.unique_id.startswith("local_synthetic-legacy_")
    } == {"local_synthetic-legacy_1", "local_synthetic-legacy_3"}
    raw = registry.async_get(eid(dev, "number", "3"))
    assert hass.states.get(raw.entity_id).state == "unavailable"
    assert raw.hidden_by is None  # no registry preference is overwritten
    assert "blocked_reason" not in hass.states.get(eid(all_devices[2], "number", "3")).attributes
    assert registry.async_get(eid(dev, "sensor", "1")).entity_category is None
    assert hass.states.get(eid(dev, "time", "meal_slot_1_time")).state == "08:17:00"
    assert (
        "Meal slot 1 time" in hass.states.get(eid(dev, "time", "meal_slot_1_time")).name
    )
    await call("time", "set_value", "meal_slot_1_time", time="09:23:00")
    await call("number", "set_value", "meal_slot_1_portions", value=17)
    await call("switch", "turn_off", "meal_slot_2_enabled")
    assert devices[1].feeder.meals == [meal("08:17"), meal("21:39")] and not writes[1]
    assert dev.feeder.meals[0] == dict(meal("09:23"), portions=17)
    assert dev.feeder.meals[1] == dict(meal("21:39"), enabled=False)
    await call("button", "press", "add_meal")
    assert not dev.feeder.meals[2]["enabled"]
    third = registry.async_get(eid(dev, "time", "meal_slot_3_time"))
    assert third.hidden_by is None
    await call("button", "press", "meal_slot_2_delete")
    assert dev.feeder.meals[1]["time"] == "00:00"
    assert hass.states.get(third.entity_id).state == "unavailable"
    assert (
        registry.async_get(third.entity_id).hidden_by
        == er.RegistryEntryHider.INTEGRATION
    )
    await call("button", "press", "add_meal")
    assert registry.async_get(third.entity_id).id == third.id
    assert registry.async_get(third.entity_id).hidden_by is None
    registry.async_update_entity(third.entity_id, hidden_by=er.RegistryEntryHider.USER)
    await call("number", "set_value", "manual_portions", value=8)
    assert all("3" not in command for command in writes[0])
    await call("button", "press", "feed")
    assert [command for command in writes[0] if "3" in command] == [{"3": 8}]
    assert not dev.feeder._native_arms
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(not d.feeder._listeners and not d.feeder._native_arms for d in devices)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = list(hass.data["localtuya"][entry.entry_id].devices.values())
    assert restored[0].feeder.manual_portions == 8
    assert not restored[0].feeder._native_arms
    assert registry.async_get(third.entity_id).hidden_by == er.RegistryEntryHider.USER
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 44  # includes two preserved unselected-device ghosts
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()
