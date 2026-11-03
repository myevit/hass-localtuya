# Native FT-11W controls

These ordinary Home Assistant entities are added only to a validated, explicitly
selected `ft11w` device profile. No card, helper, second connection or second
scheduler is required. The existing plan sensor ID and attributes, and all
`localtuya` feeder actions used by the optional card, remain compatible.

## Device controls

- **Meal plan** is an ordinary (not diagnostic) count sensor. Its existing
  attributes retain the last reported schedule and provenance.
- Each reported **Meal slot N** has **time**, **portions**, **enabled**, and
  **Delete** entities. Time is minute-resolution **feeder-local** time, not HA
  scheduling or a device clock/timezone control. Only every-day plans are supported.
- **Add disabled meal** appends one every-day record with one portion, disabled,
  at the first unused minute starting at `00:00`. This is an intentional schedule
  write. Edit its time and portions, then explicitly enable it. Ten slots maximum.
  Existing duplicate times remain valid and are not rewritten or deduplicated.
- To delete, select the target in **Arm deletion of meal slot (30 seconds)**,
  then press that slot's **Delete** button within 30 seconds. Other slots cannot
  use its confirmation. The grant is consumed even if the operation fails. The
  last remaining slot cannot be deleted; disable it instead.
- **Manual feed portions** restores a local selection (1–60, default 1). Editing
  or restoring it never sends DP3. Turn on **Arm feed selected portions
  (30 seconds)**, then press **Feed selected portions now**. This captures one
  exact quantity, consumes the arm and sends at most one command. Changing the
  quantity or schedule invalidates the arm. There is no automatic retry.
- **Acknowledge restored plan for next edit** authorizes one mutation of exactly
  the displayed restored revision for 30 seconds. It does not label it received,
  clear uncertainty, or override a malformed/blocked baseline. Revision, raw plan,
  source or connection changes, disconnect and unload invalidate the grant.
- **Feeder status**, **Feeder error** and **Last feed** report controller outcomes.
  Last feed remains the genuine observed feeding-start edge, not proof of full
  dispensing. Button timestamps mean a press, not device confirmation. Unknown
  outcomes are not invitations to retry; inspect the feeder.

Arming is local, volatile and short-lived; it never restores after a restart.
All controls use the existing serialized controller, whole-plan validation,
pre-send revision/generation checks, persistent reservation, single-send
transport and matching report confirmation. Merely displaying controls, startup,
reconnect, local quantity restoration and read-only updates issue no DP1/2/3 write.
Existing raw DP1/2/3 write bypasses remain blocked on selected profiles.

## Positional identity and native form limitations

Firmware records have no persistent meal identifiers. **Slot N means protocol
position N**, not a named meal. Deleting a lower slot shifts later records into
lower slot entities. Automations therefore target positions. No UUID matching is
invented. Only reported slots are created (four entities each, maximum forty).
Previously known absent slots become unavailable and integration-hidden; when
they return, only integration-owned hiding is cleared. User hiding/disable choices
and registry identities are retained. Unknown/malformed reports do not infer
slot deletion.

Native HA time/number/switch forms do not transmit a browser-displayed revision.
The backend captures the last published entity view before awaiting and checks
its current revision again before sending. It **cannot detect every stale human
intent in a more-info dialog** after HA has refreshed that entity in the
background. Reopen/recheck after plan changes, especially deletion/compaction.
The optional card retains its stronger explicit `expected_revision` contract.
Native button entities do not provide a custom parameterized confirmation modal;
the explicit select/arm-then-button interactions above are the native alternative.

Time dispatch is runtime-only for entries containing the native capability; it
is deliberately absent from the generic DP platform chooser/catalogue.
Unselected devices retain their existing platforms and behavior.

## Qualification and activation

The offline suites use real HA platform/service/registry lifecycle with synthetic
identities and blocked TCP/UDP. They cover native time/number/switch/button/select
dispatch, byte preservation, target-bound confirmations, revision races, two
feeders plus an unselected device, dynamic slot visibility, local restoration,
unload/reload and legacy compatibility. The existing optional-card Chromium
contract can be exercised unchanged against this backend.

Development tests do not establish installed native frontend or physical feeder
acceptance. Installation/Core restart requires separate approval; no live
feeding, schedule replacement or clock/timezone mutation is part of this change.

Implementation was AI-assisted and requires independent review before release.
