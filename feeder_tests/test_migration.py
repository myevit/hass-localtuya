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
