"""Shared platform restore ownership stays local to each device connection."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from homeassistant.core import HomeAssistant
from custom_components.localtuya import coordinator
from custom_components.localtuya.coordinator import HassLocalTuyaData, TuyaDevice
from custom_components.localtuya.number import async_setup_entry
from .test_device import device_config


@pytest.mark.asyncio
async def test_two_distinct_devices_restore_only_on_own_connect(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    configs = {}
    for i in (1, 2):
        conf = deepcopy(device_config())
        conf.pop("feeder_profile")
        conf.update(
            device_id=f"synthetic-{i}", host=f"192.0.2.{i}", mac=f"02:00:00:00:00:0{i}"
        )
        configs[conf["device_id"]] = conf
    entry = SimpleNamespace(entry_id="synthetic", data={"devices": configs})
    data = HassLocalTuyaData(None, {})
    hass.data["localtuya"] = {entry.entry_id: data}
    devices = [TuyaDevice(hass, entry, conf) for conf in configs.values()]
    data.devices.update({dev._device_config.host: dev for dev in devices})
    added = []
    await async_setup_entry(hass, entry, added.extend)
    assert len(added) == 2
    assert [dev._entities for dev in devices] == [[added[0]], [added[1]]]
    restores = []
    for i, entity in enumerate(added):
        monkeypatch.setattr(
            entity,
            "restore_state_when_connected",
            AsyncMock(side_effect=lambda i=i: restores.append(i)),
        )
    interfaces = []
    for dev in devices:
        interface = SimpleNamespace(
            is_connected=True,
            dispatched_dps={},
            enable_debug=Mock(),
            add_dps_to_request=Mock(),
            status=AsyncMock(return_value={"3": 1}),
            keep_alive=Mock(),
            reset=AsyncMock(),
            set_dps=AsyncMock(),
        )
        interfaces.append(interface)
    connect = AsyncMock(side_effect=[interfaces[0], interfaces[1], interfaces[0]])
    monkeypatch.setattr(coordinator, "pytuya_connect", connect)
    await devices[0].async_connect()
    assert restores == [0]
    await devices[1].async_connect()
    assert restores == [0, 1]
    devices[0]._interface = None
    await devices[0].async_connect()
    assert restores == [0, 1, 0]
    for interface in interfaces:
        interface.reset.assert_not_called()
        interface.set_dps.assert_not_called()
    for dev in devices:
        if dev._unsub_new_entity:
            dev._unsub_new_entity()
    await hass.async_stop()
