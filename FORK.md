# Personal LocalTuya fork

This repository is a user-directed personal fork of
[xZetsubou/hass-localtuya](https://github.com/xZetsubou/hass-localtuya),
not an upstream-endorsed release. The original GPL-3.0 license, source history,
authorship and attribution are retained. See `LICENSE` and the upstream project
for its existing documentation and contributors.

The extension is native, model-specific FT-11W feeder support within
LocalTuya: existing Home Assistant platforms, existing persistent LAN sessions,
validated device-owned meal plans and non-retrying manual-feed actions. It is
not a new Home Assistant feeder domain or a wrapper around another integration.

Development is AI-assisted by Hermes Agent (Nous Research), coordinated as TARS,
under the repository owner's direction and review. The candidate has offline
real-Home-Assistant and encrypted-protocol tests. It is not installed or
qualified on hardware. No upstream pull request or issue is part of this work.
See `PUBLICNATIVEFEEDER.md` for the native API and `NATIVEFEEDER.md` for testing
and migration gates. Adapted MIT-licensed codec/semantics retain their notice in
`NOTICE-feeder`; the original LocalTuya GPL-3.0 license is unchanged.

Only synthetic fixtures belong in this repository. Do not commit local keys,
addresses, household identifiers, actual schedules, runtime captures or private
migration inventories. Device product identifiers describe models, not homes.

The initial implementation base is upstream tag `2026.10.0`, commit
`7f3be1999f581863ffa984e86ed359857f8bcab1`. The fork's default branch remains
unchanged; implementation and tests belong on `feat/ft11w-feeder`.
