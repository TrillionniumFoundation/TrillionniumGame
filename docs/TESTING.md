# P0 acceptance gates

The top-level command is:

```bash
bash scripts/check-nakama-p0.sh
```

It runs these gates in order:

1. `check-nakama-contract.sh`: JSON/schema sanity, fixed Merkle vectors, strict
   digest/framing/signature contract tests.
2. `check-nakama-core.sh`: two-slot roster, signed authorization, lifecycle,
   contiguous ordering, command idempotency/conflict, authoritative completion,
   and immutable evidence.
3. `check-nakama-restart.sh`: snapshot corruption/truncation failure, consumed
   authorization persistence, sequence continuity, resume, and byte-identical
   completion retries.
4. `check-nakama-compose-smoke.sh`: pinned plugin image build, secret fail-fast,
   isolated random-port stack, hardening inspection, plugin health/readiness,
   a real two-user authoritative-match black box, crash/resume durability,
   live K0-to-K1 public-registry/singleton-private authority separation, and readiness failure after
   database loss.

Both Compose gates run `lint-nakama-compose-env.sh` and a negative fixture
before rendering the model, so an obsolete
`TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS` member cannot be silently ignored.

The Compose gate pulls large pinned images on its first run. Set
`TRNM_NAKAMA_SKIP_COMPOSE=1` only for an explicitly documented inner-loop run;
such a run is not a complete P0 acceptance result.

Each test script uses `set -euo pipefail`, creates its own temporary files and
Compose project, and installs cleanup traps. The black-box client uses only the
official `@heroiclabs/nakama-js` `2.8.0` package locked by
`package-lock.json`; it never imports the runtime Go package or a sibling
repository.

## Live black-box sequence

The Compose gate proves behavior through Nakama's public HTTP and realtime
interfaces, rather than invoking a Go handler directly:

1. Authenticate two stable custom users and open two WebSocket sessions.
2. Independently reproduce the canonical length-prefixed framing in Node,
   create ephemeral Ed25519 issuer/agent keys, sign both admission snapshots,
   and call the operator create RPC.
3. Join both users with their bound authorization IDs. Send one valid signed
   command, replay the exact bytes, then prove that same-ID mutation and an
   out-of-order participant sequence are rejected.
4. Persist the first match under K0, send `SIGKILL` to Nakama only, require the
   stopped-stack zero-pending control preflight, delete the K0 private key, and
   restart with K0+K1 public keys and active private K1.
5. Resume the historical snapshot, continue it under K1, retrieve
   byte-identical evidence, and independently verify its K1 signature.
6. Seal a second K0 match as completed, pin its PostgreSQL storage
   version/value/hash, and prove completed resume, evidence, and archive reads
   all succeed byte-identically in a K1-active process with K0 public retained.
   This live phase does not claim that a read-only request persisted a new K1
   wrapper. Focused core tests separately construct and verify an actual
   K1-signed wrapper with an embedded K0 completion. Then remove K0 public and
   prove the same three read paths fail with the explicit missing-key error
   while the database bytes remain unchanged.
7. Stop PostgreSQL and require readiness to fail while Nakama liveness remains
   healthy.

Fixture private seeds and service secrets exist only in a mode-`0600` file
inside the gate's mode-`0700` temporary directory. Each K0/K1 prepare/resume
handoff is a separate mode-`0600` JSON file; the gate recursively rejects
private-key, seed, token, password, or session fields before each crash.
Cleanup removes the Compose project, volumes, locally built test image,
dependency copy, and temporary files even when an assertion fails.

Before starting the third-party JavaScript client, the gate removes all
inherited exported variables and builds a phase-specific allowlist. Health sees
only Nakama's public client/RPC test keys; prepare additionally receives its
ephemeral issuer/agent fixtures and operator token; resume receives only the
one agent key it uses, the operator token, and the externally pinned expected
Nakama authority identity. Database, session, console, and authority private
keys are never exposed to the client process.

The P0 gate is repository evidence, not a cross-repository release gate.
Integration stays `blocked` / `runnable=false` until compatible immutable Chain
and Hepta artifacts exist.

## Paper Raid acceptance gates

`make paper-raid-check` runs the additive research-session contract, pinned Go
race/vet, restart/outbox recovery, and real Compose black box. It covers 3-,
4-, and 5-member authorization epochs; independent Agent keys; all-ready
actions; disconnect/reconnect and exclusive cursor catch-up; same-human and
same-Agent single-slot key rotation; SIGKILL recovery; and independent
reconstruction of roster, event, archive, commitment, and completion-signature
facts.

The same gate validates the signed Paper Raid control v2 contract: strict JSON
Schemas; independently reconstructed Go and Node binary frames; stable golden
request/signature vectors for create, resume, roster replacement, and complete;
short claim lifetime and mutation rejection; durable exact replay; and conflict
on same-command/different-body reuse. It also proves that all four public v2
RPCs operate without exposing the legacy operator token to the client.

The research Compose gate uses a loopback-only ephemeral Hepta callback fixture
that signs the frozen consumption and completion ACK frames. It holds Hepta
down across local commitment and Nakama restart, requires retries to reuse the
exact request body/idempotency key, deliberately returns one signature-tampered
completion receipt, and proves Nakama remains pending until a valid signed ACK
is received and persisted. This is protocol black-box evidence, not a claim
that the separately versioned Hepta service or Chain finality path is deployed.

The Compose black box places deterministic, test-only barriers after durable
create/resume runtime creation and immediately before replace/complete match
signals. It sends `SIGKILL` at each barrier, restarts the same PostgreSQL-backed
stack, and requires recovery to apply each accepted command exactly once. The
barrier is inactive unless the explicit absolute
`TRNM_RESEARCH_CONTROL_TEST_FAILPOINT_FILE` path is configured; production
deployments must leave it unset.

The resume barrier also exercises the private-retirement ceremony while the
K0 command is pending: with Nakama stopped, the repeatable-read/read-only
preflight must return blocker status and identify K0. After exact K0 recovery
drains every command to `applied`, the same fenced preflight must report zero
blockers before the env fixture destroys K0 private material. The gate then
captures full PostgreSQL versions/values plus hashes for the K0-applied v3
control response, K0 snapshot, and completion outbox. Under K1 active with K0
public retained, the original complete request must replay the exact response
bytes without changing either row. After delivery finalizes, the gate pins a
new baseline, removes K0 public, and requires archive/snapshot, completion
evidence, and applied-control replay all to fail while both database rows and
versions remain byte-identical. Fresh 4- and 5-participant completions then
provide an independent K1-only signature proof. Focused Go tests separately
prove that neither the expected nor actual response authority can be rewritten
to cross-key reseal a pending K0 reservation.

The dual-schema bridge has a separate compatibility matrix. Focused tests build
an actual frozen v2 applied record and v2 response-seal signature, replay the
same request twice through the production control lookup, and compare every
stored value/version before and after. Removing its historical public key must
fail with the explicit verification-key sentinel and still produce zero writes.
A valid pending v2 fixture must fail both runtime load and activation assessment;
unknown schemas fail closed. An activation-error fixture exercises both RPC
families and both MatchInit entry points before storage. The offline activation
script is only structural evidence; a release gate must additionally start the
same candidate and require `trnm_ready_v1` so the full strict and cryptographic
database scan is part of the activation closure.
