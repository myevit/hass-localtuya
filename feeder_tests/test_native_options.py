"""Supported options flow changes only the selected native device configuration."""

from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock
import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from .test_device import device_config


@pytest.mark.asyncio
@pytest.mark.parametrize("restore_legacy", [False, True])
async def test_native_profile_via_real_options_manager(
    tmp_path, monkeypatch, restore_legacy
):
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).parents[1] / "custom_components", target_is_directory=True
    )
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    loader.async_setup(hass)
    await hass.config_entries.async_initialize()
    monkeypatch.setattr(
        hass.config_entries, "async_setup", AsyncMock(return_value=False)
    )
    conf = device_config()
    conf.pop("feeder_profile")
    data = {
        "devices": {
            conf["device_id"]: conf,
            **{f"other-{i}": {"unchanged": i} for i in range(4)},
        },
        "no_cloud": False,
    }
    entry = ConfigEntry(
        domain="localtuya",
        title="Synthetic",
        data=deepcopy(data),
        options={},
        version=4,
        minor_version=1,
        source="user",
        unique_id="synthetic-hub",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        disabled_by=ConfigEntryDisabler.USER,
    )
    await hass.config_entries.async_add(entry)
    if restore_legacy:
        legacy = ConfigEntry(
            domain="tuya_feeder",
            title="Synthetic legacy",
            data={"device_id": conf["device_id"], "localtuya_entry_id": entry.entry_id},
            options={},
            version=1,
            minor_version=1,
            source="user",
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
            disabled_by=ConfigEntryDisabler.USER,
        )
        await hass.config_entries.async_add(legacy)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    assert "configure_feeder" in result["menu_options"]
    result = await manager.async_configure(
        result["flow_id"], {"next_step_id": "configure_feeder"}
    )
    assert result["step_id"] == "configure_feeder"
    payload = {"selected_device": conf["device_id"], "feeder_profile": "ft11w"}
    if restore_legacy:
        payload["feeder_legacy_entry_id"] = legacy.entry_id
    assert result["data_schema"](payload) == payload
    result = await manager.async_configure(result["flow_id"], payload)
    assert result["type"] == "create_entry"
    updated = entry.data["devices"][conf["device_id"]]
    assert updated["feeder_profile"] == "ft11w"
    if restore_legacy:
        assert updated["feeder_legacy_entry_id"] == legacy.entry_id
    assert updated["entities"][:-1] == conf["entities"]
    assert updated["entities"][-1] == {
        "id": "1",
        "platform": "sensor",
        "friendly_name": "Meal plan",
    }
    assert {
        k: v
        for k, v in updated.items()
        if k not in ("entities", "feeder_profile", "feeder_legacy_entry_id")
    } == {k: v for k, v in conf.items() if k != "entities"}
    assert {
        k: v for k, v in entry.data["devices"].items() if k != conf["device_id"]
    } == {k: v for k, v in data["devices"].items() if k != conf["device_id"]}
    # Revalidation is against fresh entry data, not the form's earlier snapshot.
    result = await manager.async_init(entry.entry_id)
    result = await manager.async_configure(
        result["flow_id"], {"next_step_id": "configure_feeder"}
    )
    changed = deepcopy(dict(entry.data))
    changed["devices"][conf["device_id"]]["product_key"] = "unsupported"
    hass.config_entries.async_update_entry(entry, data=changed)
    before = deepcopy(dict(entry.data))
    result = await manager.async_configure(result["flow_id"], payload)
    assert (
        result["type"] == "abort" and result["reason"] == "unsupported_feeder_profile"
    )
    assert entry.data == before
    await hass.async_stop()
