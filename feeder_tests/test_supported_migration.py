"""Replacement five-device supported-API regression; synthetic identities only.

This is newly authored evidence, not recovered historical worker test source.
Descriptors retain the original platform/DP parameters, not household identities.
"""

import json
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from custom_components.localtuya import coordinator
from custom_components.localtuya.discovery import TuyaDiscovery
from .test_migration import legacy


def entry(domain, data, unique_id):
    return ConfigEntry(
        domain=domain,
        title="Synthetic",
        data=data,
        options={},
        version=4 if domain == "localtuya" else 1,
        minor_version=1,
        source="user",
        unique_id=unique_id,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        disabled_by=ConfigEntryDisabler.USER,
    )


@pytest.mark.asyncio
async def test_five_device_supported_adoption_and_reconnect(tmp_path, monkeypatch):
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
    configs = json.loads(
        (Path(__file__).parent / "fixtures/five_devices.json").read_text()
    )
    before = deepcopy(configs)
    native = entry(
        "localtuya",
        {
            "devices": {c["device_id"]: c for c in configs},
            "no_cloud": True,
            "region": "eu",
            "client_id": "",
            "client_secret": "",
            "user_id": "",
        },
        "synthetic-hub",
    )
    await hass.config_entries.async_add(native)
    old = entry(
        "tuya_feeder",
        {"device_id": "synthetic-5", "localtuya_entry_id": native.entry_id},
        "synthetic-5",
    )
    await hass.config_entries.async_add(old)
    schedule = Store(hass, 1, f"tuya_feeder.{old.entry_id}")
    receipt = legacy()
    await schedule.async_save(receipt)
    feed = Store(hass, 1, f"tuya_feeder.feed.{old.entry_id}")
    await feed.async_save({"uncertain": True, "last_feed_at": None})
    source = [Path(s.path).read_bytes() for s in (schedule, feed)]
    manager = hass.config_entries.options
    result = await manager.async_init(native.entry_id)
    result = await manager.async_configure(
        result["flow_id"], {"next_step_id": "configure_feeder"}
    )
    result = await manager.async_configure(
        result["flow_id"],
        {
            "selected_device": "synthetic-5",
            "feeder_profile": "ft11w",
            "feeder_legacy_entry_id": old.entry_id,
        },
    )
    assert result["type"] == "create_entry"
    assert native.disabled_by is ConfigEntryDisabler.USER
    assert old.disabled_by is ConfigEntryDisabler.USER
    assert [native.data["devices"][c["device_id"]] for c in configs[:4]] == before[:4]
    interfaces = []

    async def connect(*args):
        interface = SimpleNamespace(
            is_connected=True,
            can_write_once=False,
            connection_generation=object(),
            dispatched_dps={},
            enable_debug=Mock(),
            add_dps_to_request=Mock(),
            status=AsyncMock(
                return_value={"4": "standby", "13": 0, "101": "strong_power"}
            ),
            keep_alive=Mock(),
            reset=AsyncMock(),
            set_dps=AsyncMock(),
            set_dps_once=AsyncMock(),
            close=AsyncMock(),
        )
        interfaces.append(interface)
        return interface

    monkeypatch.setattr(coordinator, "pytuya_connect", connect)
    await hass.config_entries.async_set_disabled_by(native.entry_id, None)
    await hass.async_block_till_done()
    assert native.state.value == "loaded"
    devices = hass.data["localtuya"][native.entry_id].devices
    assert len(devices) == 5
    capability = devices["192.0.2.5"].feeder
    assert capability.revision == receipt["revision"]
    assert capability.last_received_at == receipt["last_received_at"]
    assert capability.feed_uncertain and capability.uncertain
    assert [Path(s.path).read_bytes() for s in (schedule, feed)] == source
    registry = er.async_get(hass)
    socket_rows = {
        r.entity_id: r.id
        for r in er.async_entries_for_config_entry(registry, native.entry_id)
        if any(r.unique_id.startswith(f"local_synthetic-{i}_") for i in range(1, 5))
    }
    assert len(socket_rows) == 8
    for dev in devices.values():
        dev._interface = None
        await dev.async_connect()
    assert len(interfaces) == 10
    await hass.config_entries.async_unload(native.entry_id)
    # A changed legacy source must not be imported over an existing native Store.
    await schedule.async_save(dict(receipt, revision=99))
    assert await hass.config_entries.async_setup(native.entry_id)
    await hass.async_block_till_done()
    assert (
        hass.data["localtuya"][native.entry_id].devices["192.0.2.5"].feeder.revision
        == receipt["revision"]
    )
    assert {
        r.entity_id: r.id
        for r in er.async_entries_for_config_entry(registry, native.entry_id)
        if r.entity_id in socket_rows
    } == socket_rows
    assert len(interfaces) == 15
    for interface in interfaces:
        interface.status.assert_awaited_once()
        interface.reset.assert_not_called()
        interface.set_dps.assert_not_called()
        interface.set_dps_once.assert_not_called()
    assert not hass.services.has_service("tuya_feeder", "feed")
    await hass.config_entries.async_unload(native.entry_id)
    await hass.async_stop()
