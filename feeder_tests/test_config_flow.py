"""Existing-device native options validate before updating any config/registry."""

from copy import deepcopy
from types import MappingProxyType
from unittest.mock import AsyncMock
import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from custom_components.localtuya.config_flow import (
    LocalTuyaOptionsFlowHandler,
    DEVICE_SCHEMA,
)
from .test_device import device_config


@pytest.mark.asyncio
async def test_existing_device_options_add_native_sensor_without_touching_others(
    tmp_path, monkeypatch
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
    conf = device_config()
    conf.pop("feeder_profile")
    others = {f"synthetic-other-{i}": {"unchanged": i} for i in range(4)}
    entry = ConfigEntry(
        domain="localtuya",
        title="Synthetic",
        data={"devices": {conf["device_id"]: conf, **others}},
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
    flow = LocalTuyaOptionsFlowHandler(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.editing_device = True
    flow.device_data = {**conf, "feeder_profile": "ft11w", "entities": []}
    flow.entities = deepcopy(conf["entities"])
    flow.dps_strings = conf["dps_strings"]
    original = deepcopy(entry.data["devices"])
    result = await flow.async_step_configure_entity(conf["entities"][0])
    assert result["type"] == "create_entry"
    new = entry.data["devices"][conf["device_id"]]
    assert new["feeder_profile"] == "ft11w" and new["entities"][-1]["id"] == "1"
    assert new["entities"][0]["max_value"] == 60
    assert {
        k: v for k, v in entry.data["devices"].items() if k != conf["device_id"]
    } == others
    # Contradictory product metadata must abort before registry/config update.
    before = deepcopy(dict(entry.data))
    flow = LocalTuyaOptionsFlowHandler(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.editing_device = True
    flow.device_data = {
        **conf,
        "feeder_profile": "ft11w",
        "product_key": "wrong",
        "entities": [],
    }
    flow.entities = deepcopy(conf["entities"])
    flow.dps_strings = conf["dps_strings"]
    assert (await flow.async_step_configure_entity(conf["entities"][0]))[
        "reason"
    ] == "unsupported_feeder_profile"
    assert entry.data == before


def test_device_option_is_explicit_and_default_disabled():
    conf = device_config()
    minimal = {
        k: conf[k]
        for k in ["friendly_name", "host", "device_id", "local_key", "protocol_version"]
    }
    assert DEVICE_SCHEMA(minimal)["feeder_profile"] == "none"
    assert (
        DEVICE_SCHEMA({**minimal, "feeder_profile": "ft11w"})["feeder_profile"]
        == "ft11w"
    )
