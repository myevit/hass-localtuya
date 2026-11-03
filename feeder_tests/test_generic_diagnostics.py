"""Device diagnostics never export synthetic local keys, including nested data."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from custom_components.localtuya.coordinator import HassLocalTuyaData
from custom_components.localtuya.diagnostics import async_get_device_diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud_enabled", [False, True])
async def test_device_diagnostics_redacts_nested_local_keys(tmp_path, cloud_enabled):
    hass = HomeAssistant(str(tmp_path))
    # Synthetic credentials only; deliberately unlike a household device key.
    secrets = [f"synthetic-private-key-{index}" for index in range(6)]
    config = {
        "local_key": secrets[0],
        "friendly_name": "Synthetic diagnostic device",
        "entities": [{"id": "3", "nested": {"local_key": secrets[1]}}],
        "extra": {"local_key": secrets[2]},
    }
    cloud = {
        "local_key": secrets[3],
        "ip": "192.0.2.1",
        "metadata": [{"local_key": secrets[4]}],
    }
    discovery = {"gwId": "synthetic-device", "nested": {"local_key": secrets[5]}}
    saved = {
        "devices": {"synthetic-device": config, "unrelated": {"local_key": "other"}}
    }
    before = deepcopy((saved, cloud, discovery))
    entry = SimpleNamespace(entry_id="synthetic-entry", data=saved)
    api = SimpleNamespace(
        device_list={"synthetic-device": cloud} if cloud_enabled else {},
        async_get_device_functions=AsyncMock(),
    )
    hass.data["localtuya"] = {
        entry.entry_id: HassLocalTuyaData(api, {}),
        "discovery": SimpleNamespace(devices={"synthetic-device": discovery}),
    }
    device = SimpleNamespace(identifiers={("localtuya", "local_synthetic-device")})
    try:
        result = await async_get_device_diagnostics(hass, entry, device)
        exported = json.dumps(result)
        assert all(secret not in exported for secret in secrets)
        assert result["device_config"]["local_key"] == REDACTED
        assert result["device_config"]["extra"]["local_key"] == REDACTED
        assert result["device_config"]["entities"][0]["nested"]["local_key"] == REDACTED
        assert result["device_config"]["friendly_name"] == config["friendly_name"]
        assert result["Discovered_Devices"]["gwId"] == discovery["gwId"]
        assert result["Discovered_Devices"]["nested"]["local_key"] == REDACTED
        assert (saved, cloud, discovery) == before
        assert "unrelated" not in exported
        if cloud_enabled:
            api.async_get_device_functions.assert_awaited_once_with("synthetic-device")
            assert result["device_cloud_info"]["local_key"] == REDACTED
            assert result["device_cloud_info"]["metadata"][0]["local_key"] == REDACTED
            assert result["device_cloud_info"]["ip"] != cloud["ip"]
        else:
            api.async_get_device_functions.assert_not_awaited()
            assert "device_cloud_info" not in result
    finally:
        await hass.async_stop()
