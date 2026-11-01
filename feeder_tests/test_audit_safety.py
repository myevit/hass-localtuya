"""Audit regressions use actual HA flows and coordinator write boundaries."""

from copy import deepcopy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from homeassistant import loader
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryDisabler
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.exceptions import ServiceValidationError
from custom_components.localtuya.coordinator import TuyaDevice, HassLocalTuyaData
from .test_device import device, device_config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "no_cloud,disabled,field,value",
    [
        (True, True, "friendly_name", "Renamed feeder"),
        (False, True, "host", "192.0.2.9"),
        (True, False, "friendly_name", "Renamed feeder"),
    ],
)
@pytest.mark.parametrize("invalid", [False, True])
async def test_ordinary_edit_retains_native_ownership(
    tmp_path, monkeypatch, no_cloud, disabled, field, value, invalid
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
    monkeypatch.setattr(
        hass.config_entries, "async_setup", AsyncMock(return_value=False)
    )
    conf = device_config()
    conf["entities"][0]["friendly_name"] = "Portions"
    conf["entities"].append(
        {"id": "1", "platform": "sensor", "friendly_name": "Meal plan"}
    )
    conf["feeder_legacy_entry_id"] = "synthetic-legacy"
    if invalid:
        conf["product_key"] = "conflicting-product"
    entry = ConfigEntry(
        domain="localtuya",
        title="Synthetic",
        data={"devices": {conf["device_id"]: deepcopy(conf)}, "no_cloud": no_cloud},
        options={},
        version=4,
        minor_version=1,
        source="user",
        unique_id="synthetic-hub",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        disabled_by=ConfigEntryDisabler.USER if disabled else None,
    )
    await hass.config_entries.async_add(entry)
    hass.data["localtuya"] = {
        entry.entry_id: HassLocalTuyaData(SimpleNamespace(device_list={}), {})
    }
    mgr = hass.config_entries.options
    result = await mgr.async_init(entry.entry_id)
    result = await mgr.async_configure(
        result["flow_id"], {"next_step_id": "edit_device"}
    )
    result = await mgr.async_configure(
        result["flow_id"], {"selected_device": conf["device_id"]}
    )
    fields = {
        "friendly_name": conf["friendly_name"],
        "host": conf["host"],
        "local_key": conf["local_key"],
        "protocol_version": "3.5",
        "entities": ["3: Portions", "1: Meal plan"],
    }
    fields[field] = value
    before = deepcopy(dict(entry.data))
    remove = AsyncMock()
    from unittest.mock import Mock

    remove = Mock()
    monkeypatch.setattr(er.async_get(hass), "async_remove", remove)
    result = await mgr.async_configure(result["flow_id"], result["data_schema"](fields))
    forms = 0
    while result["type"] == "form" and result["step_id"] == "configure_entity":
        forms += 1
        schema = result["data_schema"]
        defaults = {
            key.schema: key.description["suggested_value"]
            for key in schema.schema
            if key.description and "suggested_value" in key.description
        }
        result = await mgr.async_configure(result["flow_id"], schema(defaults))
    assert forms == 2
    if invalid:
        assert (
            result["type"] == "abort"
            and result["reason"] == "unsupported_feeder_profile"
        )
        assert entry.data == before
        remove.assert_not_called()
        await hass.async_stop()
        return
    assert result["type"] == "create_entry"
    updated = entry.data["devices"][conf["device_id"]]
    assert updated["feeder_profile"] == "ft11w"
    assert updated["feeder_legacy_entry_id"] == "synthetic-legacy"
    assert updated[field] == value
    dev = TuyaDevice(hass, entry, updated)
    assert dev.feeder is not None
    with pytest.raises(ServiceValidationError):
        await dev.set_dp(8, "3")
    await hass.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["ft11w", "unknown"])
async def test_invalid_selected_profile_still_blocks_raw_writes(tmp_path, profile):
    hass = HomeAssistant(str(tmp_path))
    conf = device_config()
    conf.update(feeder_profile=profile, product_key="conflicting-product")
    dev = device(hass, conf)
    assert dev.feeder is None
    writes = AsyncMock()
    dev._interface = SimpleNamespace(set_dps=writes, is_connected=True)
    for dp in ("1", "2", "3"):
        with pytest.raises(ServiceValidationError):
            await dev.set_dp(8, dp)
        with pytest.raises(ServiceValidationError):
            await dev.set_dps({dp: 8})
    writes.assert_not_called()
    from custom_components.localtuya import async_setup_entry
    from homeassistant.exceptions import ConfigEntryError

    with pytest.raises(ConfigEntryError, match="Selected feeder profile is invalid"):
        await async_setup_entry(
            hass, SimpleNamespace(version=4, data={"devices": {dev.id: conf}})
        )
    await hass.async_stop()
