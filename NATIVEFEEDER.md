# Native FT-11W candidate: development and staged adoption

This branch adds a native **LocalTuya** capability, not another HA integration.
It is based on upstream tag `2026.10.0` / commit
`7f3be1999f581863ffa984e86ed359857f8bcab1`. Later fork-master changes are excluded.
The runtime manifest is intentionally not presented as a released/installed
upgrade. Read `PUBLICNATIVEFEEDER.md` for the card/service contract.

## Scope and opt-in

Enable `feeder_profile: ft11w` in the existing LocalTuya device options only
for model `FT-11W` or `M&C Smart Feeder Titan WIFI`. If a product key is present,
it must be `gh0lmtxb5fumm0ad`; contradictory metadata rejects the profile. Require
direct Wi-Fi protocol 3.5, exact DP code mapping and a number DP3 schema of
1..60 / scale 1 / step 1 / restore-on-reconnect false. Generic `cwwsq` category
alone never enables the feature. The default profile is `none`.

The native descriptor validates the saved model/schema, retains configured
entities, and adds a standard DP1 sensor if absent. Existing DP1 sensor unique
IDs are retained. Only native DP1 selection bypasses the electrical Base64
sensor heuristic. All other LocalTuya devices retain generic behavior. Existing
DP2/DP3 registry entities are not silently removed, but their actuator setters
are deliberately blocked on the enabled native profile: use typed native
services. Other configured diagnostics/settings retain their original behavior.
No clock DP, timezone ownership, partial-week support, refresh timer, additional
TCP connection, compatibility domain or new runtime dependency is introduced.

## Offline qualification

Use isolated development environments, never install into HA host packages:

```sh
python3.14 -m venv .venv-feeder
.venv-feeder/bin/python -m pip install -r requirements_feeder_test.txt
.venv-feeder/bin/python -m pytest feeder_tests -q --disable-socket --allow-unix-socket
```

The native lane uses **real HA 2026.10.4**, including config-entry setup/unload/
reload, entity/device registries, standard sensor platforms, Store persistence
and service calls. Real Tuya 3.5 authenticated/encrypted framing and dispatch
run against an in-memory recording transport. IPv4/IPv6 sockets are forbidden;
AF_UNIX is allowed only for the asyncio event-loop self-pipe. No LAN/cloud query,
physical feed or schedule write is a test prerequisite. Source tests cover exact
profile rejection, codecs, whole-plan sibling preservation, genuine identical
receipts versus cache replay, ACKs, explicit one-shot publication/type changes,
raw-actuation bypass guards, readiness, concurrent taps, uncertainty/restart,
late evidence recovery, shutdown cleanup, restored acknowledgements and migration.

The upstream regression lane remains on its own pinned HA 2025.1.0/Python 3.13
fixture environment (`requirements_test.txt`). Run it separately:

```sh
python3.13 -m venv .venv-regression
.venv-regression/bin/python -m pip install -r requirements_test.txt pytest-socket
.venv-regression/bin/python -m pytest tests -q --disable-socket --allow-unix-socket
```

Do not mix the suites: old upstream fixtures replace global asyncio functions
and use an older ConfigEntry constructor. Their failure under HA 2026.10.4 is a
fixture incompatibility, not proof about the new native capability. The native
CI lane is `.github/workflows/native-feeder.yml`; CI is prepared, not claimed to
have run on GitHub until an actual run is recorded.

## State and failure semantics

The device owns a single capability and action lock. Services resolve a loaded
DP1 sensor through the native registry/config-entry identity. The protocol's
optional exact-frame hook runs before legacy cache merging; ordinary legacy
status/event behavior is unchanged. ACKs and optimistic/merged cache updates do
not constitute observations. Each protocol connection has a new generation.

Schedule and manual-feed reservations are saved **before** publication in a
private, atomically written HA Store (`localtuya.feeder.<device-id>`). The Store
contains the full raw snapshot, revision, original receipt time, uncertainty
and last-feed timestamp. Revisions protect against observed local changes, not
unreported app edits. Restores retain original time and require explicit action
acknowledgement. Writes are never replayed after restart.

The one-shot primitive checks an already negotiated 3.5 session and publishes
exactly once without reconnect, pacing queue, exchange retry or DP batching.
Its attempt callback marks the ambiguity boundary immediately before
`transport.write`. Return means publication only, not receipt or dispensing.
A schedule needs a matching later DP1 frame; feeding needs a post-send genuine
idle-to-feeding edge and a distinct later matching DP14 report. Unknown outcomes
remain guarded across restart. Standby and elapsed time do not unlock them.
Late correctly correlated same-generation evidence can recover without sending
anything. After a restart, an unknown physical result requires operator-led
recovery, not another feed command or automatic guard reset.

The upstream protocol stream parser is otherwise unchanged. Packet loss or
existing framing limitations can produce an honest unconfirmed result; this
feature deliberately does not hide them with a physical-command retry.

## Migration: prepared only, no apply mode

`scripts/prepare_feeder_migration.py` reads a private **exported bundle**, validates
bindings/schema/provenance and produces a new mode-0600 proposal. It cannot
replace a core file, write to `.storage`, overwrite a proposal, or call HA. The
CLI has no apply option. Only synthetic CLI fixtures have been exercised here.

The bundle has these keys:

- `core_config_entries`: an exported HA config-entries Store envelope;
- `entity_registry`: an exported entity-registry Store envelope;
- `legacy_schedule`: `tuya_feeder.<legacy-entry-id>` envelope (version 1);
- `legacy_feed`: `tuya_feeder.feed.<legacy-entry-id>` envelope, if present;
- optional `native_store`: if present, preparation refuses to overwrite it.

Use the CLI's `--help` for identifiers/output flags. Do not include a real
bundle or proposal in a public commit. The result includes the exact proposed
device config, validated state and registry entity-ID mapping; it is not a
permission to apply those changes. It also protects all unrelated devices,
registry entries, helpers, automations and unreviewed scripts.

A staged, reviewed adoption must:

1. Back up/hash current config, registry, stores, scripts and card/callers.
2. Resolve each old plan/last-feed entity ID to its new LocalTuya unique ID.
   Archive the disabled legacy entity ID before assigning that desired ID to
   the native entity; do not hand-edit unique IDs or clobber an existing native
   registry row. Preserve helper IDs and automation references.
3. Disable the legacy `tuya_feeder` config entry before enabling the native
   profile. Native setup/action readiness rejects a second active owner.
4. Apply the reviewed target-device config only, including optional
   `feeder_legacy_entry_id` from the proposal. On first native startup, a disabled,
   matching legacy entry can provide a bounded one-time Store migration. Native
   state must be absent; corrupt/newer/existing destinations are not replaced.
   Preserve source receipt time/revision and both uncertainty guards. Missing
   legacy feed state becomes unknown, not permission to feed.
5. Install the reviewed candidate and paired native card using separately
   approved deployment procedures. Verify restored read-only state, device-page
   last feed, typed-service registration, unload/reload cleanup and all other
   LocalTuya devices before physical acceptance.
6. Keep raw actuation scripts disabled. A known legacy label quantity **4**
   versus actual DP3 quantity **8** remains unresolved; do not guess the intended
   dose, silently rewrite it, or re-enable it during migration.
7. Obtain explicit physical approval for one confirmed feed/schedule trial.
   Observe protocol and physical outcomes separately; never resend an unknown.
8. Only after acceptance retire the legacy integration/domain, fallback card
   paths and stale scripts. Rollback restores archived configs/entities/stores,
   not commands. Frontend migration, production activation, legacy retirement
   and hardware qualification are separate remaining gates.

No production HA files, services, device connections or schedules were changed
by preparing this candidate.
