"""One-time legacy migration validates provenance; never sends packets."""

import pytest
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from .test_profile import meal
from .test_state import Device
from custom_components.localtuya import feeder
from custom_components.localtuya.core.ha_entities.pet_feeders import encode


def legacy():
    return {
        "raw": encode([meal()]),
        "revision": 3,
        "last_received_at": dt_util.utcnow().isoformat(),
        "uncertain": True,
        "error": "unconfirmed_timeout",
    }


@pytest.mark.asyncio
async def test_migration_preserves_original_receipt_and_unknown_feed(tmp_path):
    assert hasattr(feeder, "migrate_legacy_snapshot"), "bounded migration missing"
    old = legacy()
    native = feeder.migrate_legacy_snapshot(
        old, {"uncertain": True, "last_feed_at": old["last_received_at"]}
    )
    assert (
        native["last_received_at"] == old["last_received_at"]
        and native["revision"] == 3
    )
    assert native["uncertain"] and native["feed_uncertain"]
    hass = HomeAssistant(str(tmp_path))
    d = Device(hass)
    await Store(hass, 1, "localtuya.feeder.synthetic-device").async_save(native)
    c = feeder.FeederCapability(d)
    await c.start()
    assert c.snapshot_source == "restored" and c.feed_uncertain and c.uncertain
    assert c.last_feed_at == old["last_received_at"] and not d.writes
    await c.stop()


def test_migration_rejects_bad_or_missing_metadata():
    assert hasattr(feeder, "migrate_legacy_snapshot"), "bounded migration missing"
    for key, value in [
        ("raw", "bad"),
        ("revision", True),
        ("last_received_at", "2020-01-01"),
        ("uncertain", "false"),
    ]:
        saved = legacy()
        saved[key] = value
        with pytest.raises(ValueError):
            feeder.migrate_legacy_snapshot(saved, {"uncertain": False})
    assert feeder.migrate_legacy_snapshot(legacy(), None)["feed_uncertain"]


def bundle():
    from .test_device import device_config

    conf = device_config()
    return {
        "core_config_entries": {
            "data": {
                "entries": [
                    {
                        "entry_id": "native-entry",
                        "domain": "localtuya",
                        "data": {
                            "devices": {
                                "synthetic-device": conf,
                                "synthetic-other": {"untouched": True},
                            }
                        },
                    },
                    {
                        "entry_id": "legacy-entry",
                        "domain": "tuya_feeder",
                        "unique_id": "synthetic-device",
                        "data": {
                            "device_id": "synthetic-device",
                            "localtuya_entry_id": "native-entry",
                        },
                    },
                ]
            }
        },
        "legacy_schedule": {
            "key": "tuya_feeder.legacy-entry",
            "version": 1,
            "data": legacy(),
        },
        "legacy_feed": {
            "key": "tuya_feeder.feed.legacy-entry",
            "version": 1,
            "data": {"uncertain": True, "last_feed_at": None},
        },
        "entity_registry": {
            "data": {
                "entities": [
                    {
                        "entity_id": "sensor.synthetic_plan",
                        "platform": "tuya_feeder",
                        "config_entry_id": "legacy-entry",
                        "unique_id": "synthetic-device_meal_plan",
                    },
                    {
                        "entity_id": "sensor.synthetic_last_feed",
                        "platform": "tuya_feeder",
                        "config_entry_id": "legacy-entry",
                        "unique_id": "synthetic-device_last_feed",
                    },
                ]
            }
        },
    }


def test_offline_preparer_proposes_only_and_cli_protects_core(tmp_path, monkeypatch):
    import json, sys
    from copy import deepcopy
    from scripts.prepare_feeder_migration import prepare, main

    data = bundle()
    before = deepcopy(data)
    result = prepare(data, "synthetic-device", "native-entry", "legacy-entry")
    assert data == before and result["prepared_only"]
    assert result["registry_mapping"][0]["desired_entity_id"] == "sensor.synthetic_plan"
    assert result["expected_native_store"]["data"]["feed_uncertain"]
    data["native_store"] = {"version": 2}
    with pytest.raises(ValueError):
        prepare(data, "synthetic-device", "native-entry", "legacy-entry")
    source = tmp_path / "exported-bundle.json"
    source.write_text(json.dumps(before))
    command = [
        "prepare_feeder_migration.py",
        "--bundle",
        str(source),
        "--device-id",
        "synthetic-device",
        "--localtuya-entry",
        "native-entry",
        "--legacy-entry",
        "legacy-entry",
        "--acknowledge-offline-copy",
        "--out",
    ]
    out = tmp_path / "proposal.json"
    monkeypatch.setattr(sys, "argv", command + [str(out)])
    main()
    assert (
        json.loads(out.read_text())["prepared_only"]
        and out.stat().st_mode & 0o777 == 0o600
    )
    original = out.read_bytes()
    with pytest.raises(FileExistsError):
        main()
    assert out.read_bytes() == original
    monkeypatch.setattr(sys, "argv", command + [str(tmp_path / "core.config_entries")])
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / "core.config_entries").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination", ["absent", "existing", "corrupt", "newer", "active_legacy"]
)
async def test_runtime_one_time_migration_never_overwrites_or_dual_owns(
    destination, tmp_path, monkeypatch
):
    from types import MappingProxyType, SimpleNamespace
    from unittest.mock import AsyncMock
    import json
    from pathlib import Path
    from homeassistant.config_entries import (
        ConfigEntries,
        ConfigEntry,
        ConfigEntryDisabler,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    monkeypatch.setattr(
        hass.config_entries, "async_setup", AsyncMock(return_value=False)
    )
    old = ConfigEntry(
        domain="tuya_feeder",
        entry_id="legacy-entry",
        title="Synthetic legacy",
        data={"device_id": "synthetic-device"},
        options={},
        version=1,
        minor_version=1,
        source="user",
        unique_id="synthetic-device",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        disabled_by=(
            None if destination == "active_legacy" else ConfigEntryDisabler.USER
        ),
    )
    await hass.config_entries.async_add(old)
    d = Device(hass)
    d._device_config = SimpleNamespace(
        as_dict=lambda: {"feeder_legacy_entry_id": "legacy-entry"}
    )
    await Store(hass, 1, "tuya_feeder.legacy-entry").async_save(legacy())
    await Store(hass, 1, "tuya_feeder.feed.legacy-entry").async_save(
        {"uncertain": True, "last_feed_at": None}
    )
    c = feeder.FeederCapability(d)
    path = Path(c.store.path)
    if destination == "existing":
        payload = feeder.migrate_legacy_snapshot(
            legacy(), {"uncertain": False, "last_feed_at": None}
        )
        payload["revision"] = 8
        await c.store.async_save(payload)
    elif destination in ("corrupt", "newer"):
        path.write_text(
            json.dumps(
                {
                    "key": "localtuya.feeder.synthetic-device",
                    "version": 2 if destination == "newer" else 1,
                    "minor_version": 1,
                    "data": "invalid",
                }
            )
        )
    before = path.read_bytes() if path.exists() else None
    await c.start()
    if destination == "absent":
        assert c.snapshot_source == "restored" and c.revision == 3 and c.feed_uncertain
    elif destination == "existing":
        assert c.revision == 8 and not c.feed_uncertain
    elif destination == "active_legacy":
        assert c.closed and "duplicate_legacy_owner" in c.error
    else:
        assert c._storage_blocked and c.feed_uncertain and not c.ready
    await c.stop()
    if destination in ("corrupt", "newer"):
        assert path.read_bytes() == before
    assert not d.writes
