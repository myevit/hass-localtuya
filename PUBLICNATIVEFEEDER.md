# Public native FT-11W contract (candidate API v1)

This is the API for the paired `tuya-feeder-card`. There is **no** runtime
`tuya_feeder` dependency, compatibility service domain, or `Platform.FEEDER`.
The fork is based on LocalTuya 2026.10.0; this candidate is not installed.

## Entity selection and identity

Select a `sensor` whose entity-registry platform is `localtuya` and whose
`native_feeder_profile` attribute is exactly `ft11w`. Do not select arbitrary
Base64 sensors. The configured DP1 sensor is the action target. Its unique ID
is `local_<device-id>_1` and it belongs to the existing LocalTuya physical device
(identifier `local_<device-id>`). Its native value is the valid meal count, or
unknown when no valid snapshot exists. Last feed is a separate standard HA
`timestamp` sensor with unique ID `local_<device-id>_last_feed` on that same device.
Entity IDs are assigned by HA; do not hardcode household names in code.

## Attributes

- `native_feeder_profile`: `ft11w`.
- `meals`: complete ordered list of `{days, time, portions, enabled}`; empty if
  invalid/unknown. Check `schedule_valid` to distinguish an actual empty plan.
- `revision`: integer, advanced by each genuine DP1 frame, even identical frames;
  NOT advanced by other DPs, ACKs, optimistic writes, restores or cache replay.
- `last_received_at`: original ISO timezone-aware receipt time, or null.
- `snapshot_source`: `unknown`, `received`, or `restored`. `received` means locally
  observed, **not** guaranteed current relative to unreported app edits.
- `schedule_valid`, `supports_schedule_write`, `supports_restored_schedule_write`,
  `requires_restored_snapshot_acknowledgement`: booleans.
- `pending`, `error`: common reservation and schedule result/error.
- `manual_feed_capability`, `supports_manual_feed`: model support and current
  conservative readiness respectively.
- `feeding_pending`, `feeding_state` (`idle`, `command_pending`, `feeding`,
  `reported`, `unknown`), `feeding_error`, `feeding_portions`,
  `feeding_requested_at`, `feeding_observed_at`, `feeding_reported_at`.
- `last_feed_at`, `last_feed_source`: observed genuine DP4 feeding-start timestamp
  and `local_dp4_feeding_edge`, or null. This is NOT physical completion proof.
- `supported_days_mode`: `every_day`; `max_meals`: 10; `max_portions`: 60.
- `observation_kind`, `warning`: preserve last-reported/clock limitations in UI.

Restored plans remain visible. A restored edit requires explicit per-action
acknowledgement, not a permanently saved bypass. Uncertainty remains fail-closed.

## Services: domain `localtuya`

Each accepts exactly one `entity_id` string pointing at the loaded native plan
sensor. No lists, arbitrary device IDs, DP identifiers, or snapshot import service.
All support optional HA service responses. Use response-capable calls; without a
requested response, an unsuccessful attempted action raises a validation error.

| Service | Required additional fields |
|---|---|
| `set_feeder_schedule` | `expected_revision`, complete `meals` list |
| `add_feeder_meal` | `expected_revision`, complete `meal` |
| `update_feeder_meal` | `expected_revision`, zero-based `meal_index`, complete `meal` |
| `delete_feeder_meal` | `expected_revision`, zero-based `meal_index` |
| `feed` | `portions` integer 1..60, `confirmed: true` |

All four schedule services accept optional
`acknowledge_restored_snapshot: true` (default false). `expected_revision` is a
strict integer from the snapshot shown when the edit began, not a newly read
revision silently substituted at submit. Booleans/strings/floats are not integers.
A meal has exactly these fields:

```json
{"days":["monday","tuesday","wednesday","thursday","friday","saturday","sunday"],"time":"12:34","portions":3,"enabled":true}
```

Only all seven lowercase weekdays, strict `HH:MM` feeder-local time, integer
1..60 portions and actual boolean enable flags are accepted. No partial-week
claim, clock setting or timezone conversion. CRUD preserves sibling RAW records
and writes the complete plan. Deleting the final meal is rejected: disable it.

Schedule response: `{confirmed: boolean, revision: integer, error: string|null}`.
`unchanged_no_write` is a successful no-op; it is not receipt confirmation.
Confirmation requires a later matching exact DP1 frame in the same connection.
A timeout, conflicting report or disconnect never retries or optimistically
changes the observed meals. Another genuine snapshot can reconcile the schedule.

Feed response: `{command_attempted, reported, state, error, requested_at,
reported_at}`. Capture the chosen quantity in the confirmation dialog and submit
that immutable value with `confirmed: true`; never reread a changing helper after
confirmation. This makes **one DP3 write**, never a follow-up DP2 pulse. ACK,
DP3 echo and stale DP14 do not confirm dispensing. A same-generation post-send
idle-to-feeding DP4 edge plus a later matching DP14 report is required for
`reported`. Reported is protocol evidence, not a promise about physical grams.
Duplicate taps, concurrent edits, missing readiness and unknown outcomes reject.
Unknown feeding outcomes survive restart and never auto-resend. Standby and
time alone never reset them; hardware/operator recovery is a separate gate.

## UI migration

Keep card tag/config, existing layout, selection helper, native confirmation
UI, toggles and silent pending/error treatment. Change only target capability
filter and service domain/names above. No old-domain fallback. Do not call raw
`set_dp`, number DP3, DP2 switches or legacy scripts: native profile guards those
actuation bypasses. Preserve user-facing entity IDs by the separately reviewed
staged registry reassignment, not by hardcoding an ID in this package.

Ordinary LAN GET may omit DP1; revision cannot protect against unreported external
edits. Clock/timezone ownership remains unknown and is NOT fixed by this fork.
Physical action qualification and production activation need separate approval.
