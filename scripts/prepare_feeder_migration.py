"""Prepare a private OFFLINE migration proposal. Deliberately no apply mode.

Run in the HA-qualified development venv against a user-exported bundle. Never
point this at .storage; proposals cannot replace a core file or existing output.
"""

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.localtuya.core.ha_entities.pet_feeders import (
    supported,
    with_native_entities,
)
from custom_components.localtuya.feeder import migrate_legacy_snapshot


def prepare(bundle, device_id, local_entry_id, legacy_entry_id):
    """Return a proposal only; validate exact identity and leave input untouched."""
    entries = bundle["core_config_entries"]["data"]["entries"]
    local = next(e for e in entries if e["entry_id"] == local_entry_id)
    legacy = next(e for e in entries if e["entry_id"] == legacy_entry_id)
    if local["domain"] != "localtuya" or legacy["domain"] != "tuya_feeder":
        raise ValueError("Wrong entry domains")
    if (
        legacy["data"]["device_id"] != device_id
        or legacy["data"]["localtuya_entry_id"] != local_entry_id
    ):
        raise ValueError("Legacy binding does not match the requested native owner")
    config = deepcopy(local["data"]["devices"][device_id])
    config["feeder_profile"] = "ft11w"
    if not supported(config):
        raise ValueError("Unsupported exact model/schema; no proposal")
    config["entities"] = with_native_entities(config)
    config["feeder_legacy_entry_id"] = legacy_entry_id

    def store_data(name, key, required):
        envelope = bundle.get(name)
        if envelope is None and not required:
            return None
        if (
            type(envelope) is not dict
            or envelope.get("key") != key
            or envelope.get("version") != 1
        ):
            raise ValueError("Missing, wrong-key or unsupported source Store")
        return envelope["data"]

    schedule = store_data("legacy_schedule", f"tuya_feeder.{legacy_entry_id}", True)
    feed = store_data("legacy_feed", f"tuya_feeder.feed.{legacy_entry_id}", False)
    migrated = migrate_legacy_snapshot(schedule, feed)
    if bundle.get("native_store") is not None:
        raise ValueError(
            "Existing destination is never overwritten, even if corrupt/newer"
        )
    registry = bundle["entity_registry"]["data"]["entities"]
    mappings = []
    for old_suffix, new_unique in [
        ("meal_plan", f"local_{device_id}_1"),
        ("last_feed", f"local_{device_id}_last_feed"),
    ]:
        source = [
            r
            for r in registry
            if r.get("config_entry_id") == legacy_entry_id
            and r.get("platform") == "tuya_feeder"
            and r.get("unique_id") == f"{legacy['unique_id']}_{old_suffix}"
        ]
        if len(source) != 1:
            raise ValueError(
                "Missing or ambiguous legacy entity; resolve registry before migration"
            )
        existing = [
            r
            for r in registry
            if r.get("config_entry_id") == local_entry_id
            and r.get("platform") == "localtuya"
            and r.get("unique_id") == new_unique
        ]
        if len(existing) > 1:
            raise ValueError("Ambiguous native entity")
        mappings.append(
            {
                "legacy_entity_id": source[0]["entity_id"],
                "legacy_unique_id": source[0]["unique_id"],
                "native_unique_id": new_unique,
                "native_current_entity_id": (
                    existing[0]["entity_id"] if existing else None
                ),
                "desired_entity_id": source[0]["entity_id"],
                "operation": "After disabling legacy: archive its entity_id, then assign desired entity_id to the verified native entity. Do not change its unique_id by hand.",
            }
        )
    return {
        "prepared_only": True,
        "requires_physical_and_activation_approval": True,
        "source_bundle_sha256": hashlib.sha256(
            json.dumps(bundle, sort_keys=True).encode()
        ).hexdigest(),
        "localtuya_entry_id": local_entry_id,
        "device_id": device_id,
        "legacy_entry_to_disable": legacy_entry_id,
        "proposed_device_config": config,
        "expected_native_store": {
            "version": 1,
            "minor_version": 1,
            "key": f"localtuya.feeder.{device_id}",
            "data": migrated,
        },
        "registry_mapping": mappings,
        "protected_unchanged": [
            "all other device configs",
            "all other registry entries",
            "helpers",
            "automations",
            "unreviewed scripts",
            "HA runtime files",
        ],
        "gates": [
            "Back up and hash current config/registry/stores/callers; compare this proposal immediately before activation.",
            "Disable legacy entry before enabling native profile; do not run both owners.",
            "Install only reviewed tested code; verify existing four other LocalTuya devices and all existing entity IDs.",
            "Keep helper quantity selection; switch card to PUBLICNATIVEFEEDER.md. Do not run raw actuation scripts.",
            "Known legacy script label quantity 4 versus actual DP3 quantity 8 is unresolved. Do not guess or re-enable it.",
            "Native start copies validated legacy state once only into an absent destination; preserves uncertainty and original receipt time.",
            "Exercise read-only restored UI, service validation and unload before separately authorized physical feed/schedule checks.",
            "Only after physical acceptance retire old integration/domain/card fallback/scripts. Keep rollback backup; never resend unknown actions.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "device-id", "localtuya-entry", "legacy-entry", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--acknowledge-offline-copy", action="store_true", required=True
    )
    args = parser.parse_args()
    source = Path(args.bundle).resolve()
    out = Path(args.out)
    if (
        ".storage" in source.parts
        or ".storage" in out.resolve().parts
        or source.name.startswith("core.")
        or out.name.startswith("core.")
        or out.suffix != ".json"
        or out.is_symlink()
    ):
        parser.error(
            "Only exported bundles and a NEW non-core proposal JSON are allowed; never .storage"
        )
    proposal = prepare(
        json.loads(source.read_text()),
        args.device_id,
        args.localtuya_entry,
        args.legacy_entry,
    )
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(proposal, stream, indent=2)
        stream.write("\n")
    print("Private proposal prepared. No Home Assistant state was changed.")


if __name__ == "__main__":
    main()
