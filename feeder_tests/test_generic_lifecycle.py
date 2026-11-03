"""Generic entry lifecycle regressions with real HA and no network access."""

from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.localtuya.coordinator import TuyaDevice
from custom_components.localtuya.discovery import TuyaDiscovery
from .test_device import device_config


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_failed_platform_unload_preserves_active_runtime(
    tmp_path, monkeypatch, native
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
    configs = []
    for index in range(5):
        config = dict(
            device_config(),
            device_id=f"synthetic-device-{index}",
            host=f"192.0.2.{index + 1}",
            feeder_profile="ft11w" if native else "none",
        )
        configs.append(config)
    entry = ConfigEntry(
        domain="localtuya",
        title="Synthetic unload regression",
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
        unique_id="synthetic-unload-entry",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state.value == "loaded"
    runtime = hass.data["localtuya"][entry.entry_id]
    devices = dict(runtime.devices)
    callbacks = list(entry._on_unload)
    closes = []
    interfaces = []
    for dev in devices.values():
        interface = AsyncMock()
        interface.is_connected = True
        dev._interface = interface
        interfaces.append(interface)
        close = AsyncMock(wraps=dev.close)
        monkeypatch.setattr(dev, "close", close)
        closes.append(close)
    notifications = []
    if native:
        devices[configs[0]["host"]].feeder.subscribe(lambda: notifications.append(True))
    try:
        with monkeypatch.context() as patch:
            unload = AsyncMock(return_value=False)
            patch.setattr(hass.config_entries, "async_unload_platforms", unload)
            assert not await hass.config_entries.async_unload(entry.entry_id)
            unload.assert_awaited_once()
        assert hass.data["localtuya"][entry.entry_id] is runtime
        assert runtime.devices == devices
        assert entry._on_unload == callbacks
        for dev, interface, close in zip(devices.values(), interfaces, closes):
            close.assert_not_awaited()
            interface.close.assert_not_awaited()
            assert dev._interface is interface and dev.connected
            if native:
                assert not dev.feeder.closed
        if native:
            dev = devices[configs[0]["host"]]
            dev.feeder.received({"4": "standby"}, dev.generation)
            assert notifications
    finally:
        # HA marks FAILED_UNLOAD non-recoverable. Clean up the test directly;
        # do not claim a user-visible reload retry is supported by HA.
        from custom_components.localtuya import async_unload_entry

        assert await async_unload_entry(hass, entry)
        await entry._async_process_on_unload(hass)
        await hass.async_block_till_done()
        await hass.async_stop()
    assert entry.entry_id not in hass.data["localtuya"]
    for dev, interface in zip(devices.values(), interfaces):
        interface.close.assert_awaited_once()
        if native:
            assert dev.feeder.closed and not dev.feeder._listeners
