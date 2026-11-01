"""Legacy source selection must match a disabled entry on this LocalTuya hub."""

from copy import deepcopy
from types import MappingProxyType
from unittest.mock import AsyncMock
import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from custom_components.localtuya.config_flow import LocalTuyaOptionsFlowHandler
from .test_device import device_config


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["active", "device", "hub", "domain", "missing"])
async def test_native_options_reject_wrong_legacy_source(
    tmp_path, monkeypatch, mismatch
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    monkeypatch.setattr(
        hass.config_entries, "async_setup", AsyncMock(return_value=False)
    )

    def make(domain, data, disabled):
        return ConfigEntry(
            domain=domain,
            title="Synthetic",
            data=data,
            options={},
            version=4,
            minor_version=1,
            source="user",
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
            disabled_by=disabled,
        )

    conf = device_config()
    local = make(
        "localtuya", {"devices": {conf["device_id"]: conf}}, ConfigEntryDisabler.USER
    )
    await hass.config_entries.async_add(local)
    legacy = make(
        "localtuya" if mismatch == "domain" else "tuya_feeder",
        {
            "device_id": "wrong" if mismatch == "device" else conf["device_id"],
            "localtuya_entry_id": "wrong" if mismatch == "hub" else local.entry_id,
        },
        None if mismatch == "active" else ConfigEntryDisabler.USER,
    )
    await hass.config_entries.async_add(legacy)
    flow = LocalTuyaOptionsFlowHandler(local)
    flow.hass = hass
    flow.handler = local.entry_id
    before = deepcopy(dict(local.data))
    result = await flow.async_step_configure_feeder(
        {
            "selected_device": conf["device_id"],
            "feeder_profile": "ft11w",
            "feeder_legacy_entry_id": (
                "missing" if mismatch == "missing" else legacy.entry_id
            ),
        }
    )
    assert (
        result["type"] == "abort" and result["reason"] == "unsupported_feeder_profile"
    )
    assert local.data == before
    await hass.async_stop()
