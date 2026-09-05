# Isolated World read-only native diagnostic

This tooling-only branch is derived from the existing read-only ARM diagnostic
control at `016dd325ffe42a3b5195c9e5e3e92c4800032686`. It does not update that
branch, any product branch, or a World ref. It is an external diagnostic lane,
not a replacement for World's required CI or independent acceptance.

## Immutable inputs

- World head: `9a57222d1eacc7059e549df9c62a79046e8ae8ea`.
- World tree: `57b03d5ae782ee9e1e338afd34842ad8dbb653c9`.
- Uploader product: `c2944df15702de77e8bbe549158019f84a53a38e`.
- Uploader tree: `6a3ef7308f778920163617d53d537f817ed36ea3`.
- Uploader script blob: `8baac2cca878af822428831359ab00d15ab10b71`.
- Diagnostic Rust toolchain: `1.98.1`. This does not change the historical
  World artifact's `1.98.0` identity or transfer any of its qualification credit.

The existing descriptor-bound, no-redirect artifact transport is reused without
modification. Only its selected input packet and path are changed in the local
Node action. Product commands receive an environment allowlist without GitHub,
artifact or CEX credentials. All product fetches are public read-only Git.
The workflow uses repository `contents: read` and `actions: read` only.

## Ordered work and results

An exact source archive, raw commit, zero-delimited tree and closed byte index
are exported before product commands run. This allows the authoring environment
to verify and inspect a complete current candidate rather than a historical
synthetic overlay. Export does not publish source onto a product branch.

The native diagnostic executes project preflight before installing its explicit
toolchain, then records compiler identity, formatting, both CLI binary tests,
and strict game-server all-target Clippy. No `--fix`, source rewriting, template
replacement, dependency update or fixed-artifact import is performed. A failed
preflight/toolchain install stops later commands. Other command failures are
retained for diagnosis. Final workflow status is failure unless all six selected
commands pass and tracked source is unchanged. Logs are uploaded first, not
converted into a successful qualification result.

Child process groups have finite timeouts and 16 MiB log bounds. Source archives
and raw packet payloads are bounded at 128 MiB; compressed upload is bounded at
64 MiB. A bounded prefix is retained if a log budget fails, explicitly marked as
such. These controls do not attest arbitrary background/concurrent modification
or the trustworthiness of runner/third-party dependencies.

Every artifact sets `world_required_checks_satisfied`, `gap_closed`,
`independently_reviewed`, and `production_authorized` to false. No database,
GUI, custody, cross-host/endurance, deployment, commercial or reviewer evidence
is asserted. The previous blocked source-publication batch is not retried,
re-encoded or executed here. A diagnostic failure is returned, not bypassed.

## Control validation

Before publishing this control branch, 15 local Python tests exercised packet
hashes, refusal to overwrite, unsafe packet paths, credential-environment
exclusion, real subprocess success/failure/unavailability/timeout/log limits,
preflight/toolchain stop conditions, unchanged false acceptance flags and an
actual temporary-Git archive export. Mocked matrix results are control tests,
not Rust product results. Python syntax and Node syntax were checked locally.
Product Rust execution begins only after this branch's actual workflow starts.
