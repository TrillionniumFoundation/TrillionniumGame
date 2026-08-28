# Plan audit correction log — 2026-08-28

This correction is part of the audit, not a compatibility claim.

## Corrected upstream identities

A post-commit verification against GitHub's exact `v3.40.0` contents API found that several Console/config/flags/migration blob SHAs had been manually transcribed incorrectly in the first v2 planning commit. The corrected values are now locked in `UPSTREAM_BASELINE.json` and asserted by `scripts/check-plan.py`:

- `console/console.proto`: `1f7ccf8e6dae3bc4c6c239ada23b1104002b917e`
- `console/console.swagger.json`: `8a51cb1e449a6c9392a162c92edd140e5d1aec04`
- `console/api.swagger.json`: `c8cf70d4b76af614f93a0683a3f0eb7a699674bb`
- `server/config.go`: `d9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6`
- `flags/flags.go`: `9c139f4fdb050e6f00a323854e0c88690a8f37ef`
- `flags/vars.go`: `c5253fb37de1d2ebfb70408c8f78965bf28840a0`
- `migrate/migrate.go`: `598138cbeb8dd2832f9746aa4cd9826cc0152e96`
- `migrate/sql` tree: `1eb2275e187a543b8203b7b809d0d246c4a2bb6e`

## Additional hardening

- Expanded denominator registries from 12 to 14 by adding explicit source and provider surfaces.
- Made fixture manifests mandatory in the evidence schema.
- Added dependency-cycle validation for the 120-task backlog.
- Replaced the unsafe new-repository publication path with a same-repository rename script that preserves repository ID.
- Created archive branch `archive/trillionnium-nakama-main-2026-08-28-7f0d4be` before the administrative rename.

This event demonstrates why SG1 requires machine extraction and why manually copied SHA values cannot close a product gate.
