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
   a real two-user authoritative-match black box, crash/resume durability, and
   readiness failure after database loss.

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
4. Send `SIGKILL` to the Nakama container only. PostgreSQL and its named volume
   remain running. Restart Nakama, call the resume RPC, require a new external
   match ID/runtime generation, reconnect both users, and prove the pre-crash
   command still replays to the byte-identical event without incrementing the
   archive.
5. Continue the match, complete it through the operator RPC, retrieve evidence
   repeatedly, and require byte-identical responses. A separate Node Ed25519
   verifier reconstructs the completion framing, accepts the authority
   signature, and rejects a tampered completion.

Fixture private seeds and service secrets exist only in a mode-`0600` file
inside the gate's mode-`0700` temporary directory. The prepare/resume handoff is
a separate mode-`0600` JSON file; the gate recursively rejects private-key,
seed, token, password, or session fields before the crash. Cleanup removes the
Compose project, volumes, locally built test image, dependency copy, and both
temporary files even when an assertion fails.

Before starting the third-party JavaScript client, the gate removes all
inherited exported variables and builds a phase-specific allowlist. Health sees
only Nakama's public client/RPC test keys; prepare additionally receives its
ephemeral issuer/agent fixtures and operator token; resume receives only the
one agent key it uses, the operator token, and the externally pinned Nakama
authority identity. Database, session, console, and authority private keys are
never exposed to the client process.

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

The research Compose gate uses a loopback-only ephemeral Hepta callback fixture
that signs the frozen consumption and completion ACK frames. It holds Hepta
down across local commitment and Nakama restart, requires retries to reuse the
exact request body/idempotency key, deliberately returns one signature-tampered
completion receipt, and proves Nakama remains pending until a valid signed ACK
is received and persisted. This is protocol black-box evidence, not a claim
that the separately versioned Hepta service or Chain finality path is deployed.
