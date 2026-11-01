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

## Supported configuration and migration

For an existing device, use the integration's **Configure → Enable native feeder
profile** options menu. Select the configured device and explicitly choose
`ft11w`. This validates the saved model/DP/quantity schema and preserves every
existing entity setting and all other devices. It adds the DP1 sensor only if
absent. It does not connect, discover, or send a feed/schedule during the flow.
Submitting to an enabled entry triggers its normal LocalTuya reload; a disabled
entry remains disabled until enabled through HA.

An optional **Disabled legacy entry to restore** selector records the existing
one-time migration source. It must be disabled, match the selected device, and
reference this LocalTuya config entry. On first native setup, the existing
capability copies the legacy HA Stores using HA's storage API. It preserves the
original receipt time, revision, last-feed time and uncertainty, marking the
plan **restored**, not freshly received. Existing/corrupt native state is never
overwritten. Do not remove the legacy config entry until this import succeeds;
removing it invokes the legacy integration's Store cleanup when installed.

A supported staged adoption needs no edits to `core.config_entries` or
`core.entity_registry`, stopped-file transaction tool, or watchdog override:

1. Back up the configuration, source, stores, registry customization and callers.
   Suspend feeding controls and retire unreviewed raw-actuation scripts.
2. Disable the legacy entry and LocalTuya entry through HA. Check unload results;
   failed unload is not permission to replace live code.
3. Install the reviewed fork through HACS while entries are disabled. If complete
   retirement in one Core restart is required, uninstall the old legacy domain's
   code before restart but retain its disabled config entry temporarily as the
   migration source. A disabled entry alone does not unregister domain services.
4. Restart Core with approval. With the old domain absent, its services cannot
   register. The temporarily orphaned disabled entry can cause an expected
   integration-not-found diagnostic until it is removed.
5. Run the supported options flow on the disabled LocalTuya entry, selecting the
   legacy source. Enable LocalTuya and verify the imported native state and all
   other devices. This is a separate integration lifecycle, not another Core restart.
6. Remove the disabled legacy entry through HA's config-entry API. Rename the new
   native entities to the now-vacated old entity IDs using the entity-registry API.
   Never mutate platform or unique IDs. Reapply reviewed customization explicitly.
7. Install/verify the paired native card and retire passive capture scaffolding
   using supported YAML configuration/reloads. Keep the quantity helper and layout.
   Do not write a schedule merely to manufacture a fresh DP1 receipt.
8. Verify native/legacy domain services, IDs, provenance, UI and unrelated devices.
   Physical feeds, schedule edits and clock actions need separate approval.

Do not publish real device bundles or source stores. Migration uses the supported
options flow above; no stopped-storage proposal or apply tools are required.

No production HA files, services, device connections or schedules were changed
by preparing this candidate.

## Offline contribution qualification

Run `python -m pytest feeder_tests -q --disable-socket --allow-unix-socket`
with `requirements_feeder_test.txt` in the pinned Python 3.14 environment. CI runs
this suite, including the five-device supported-options migration, Store import,
startup/reconnect write prohibition and registry-identity regression. The fixture
uses synthetic identities, documentation-only IP addresses and dummy keys; it
retains platform and DP parameters. Ordinary device editing is tested separately
through Home Assistant's real options manager. These tests do not qualify a live
Core restart, HACS installation or physical dispensing.

Ordinary edits retain the native profile and legacy Store reference. A selected
but invalid profile is a setup error, not permission to fall back to raw feeder
control. Correct its model, product, protocol and quantity mapping before retrying.
An unselected profile keeps normal LocalTuya behavior.

This contribution was developed with AI assistance (Hermes Agent by Nous Research,
OpenAI GPT-6.1). It needs independent maintainer review before upstream acceptance.
The integration remains GPL-3.0; adapted MIT material is attributed in
`NOTICE-feeder`. No service-contract change is introduced by these audit fixes.
