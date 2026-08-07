# Trillionnium Nakama

Independent real-time match and Paper Raid session authority for Trillionnium.

The active P0 slice, `nakama-authoritative-match-evidence-v1`, implements a
fixed two-participant authoritative match, durable command/event evidence,
restart/resume, authenticated cursor-based archive catch-up, and a Nakama-signed
`MatchCompletedV1`. Product gameplay remains in World; research
authorization/evaluation remains in Hepta; consensus, ingress, finality, and
proofs remain in Chain.

This is not yet a production or release claim. P0 is single-host, Integration
remains blocked, and real Hepta/Chain compatibility must be locked separately.

The additive `trnm_nakama_research_session_v1` surface implements the Paper
Raid P0/P2 slice: complete 3–5-member Hepta authorization epochs, durable
all-ready admission, signed and ordered external-Agent actions, reconnect and
cursor catch-up, single-slot Agent key rotation, independently recomputable
event/roster/archive roots, and a Nakama-signed cooperative completion. Agent
release acknowledgement is explicitly not human authorship consent. Exact
authorization-consumption and completion requests are retried until Nakama has
verified and durably stored a signed Hepta ACK. See
[contracts/RESEARCH_SESSION_V1.md](contracts/RESEARCH_SESSION_V1.md).

Paper Raid lifecycle control is exposed separately through the signed
`trnm_research_session_{create,resume,replace_roster,complete}_v2` RPCs. Each
request carries a short-lived Hepta Ed25519 control claim over the exact
domain-separated binary business frame. Accepted commands and their exact
responses are durable and idempotent across process death; the legacy operator
token is not accepted by these v2 RPCs. The control keys are a trust domain
independent from participant-authorization issuers and the Nakama completion
authority. See
[contracts/research-control-v2/spec.md](contracts/research-control-v2/spec.md).
Authority signing and historical verification use separate registries. New
snapshots and completions use only the active private key; restore resolves the
snapshot's embedded key id from the public verification registry, then
continues with the active signer. Canonical Compose requires the active
singleton private key plus the public registry; the public-registry fallback
derived from that singleton is available only through an explicit isolated
dev/test opt-in and is not a deployment contract.

Private-key rotation is an offline drain ceremony, not a live key swap. Fence
all Nakama writers, stop the service, and run
`scripts/check-nakama-authority-private-retirement.sh` before destroying a
retiring private key; any pending command reserved to that key blocks rotation.
See [docs/OPERATIONS.md](docs/OPERATIONS.md) for recovery and rollback.

Research-control storage activation is also fenced. The v3 writer preserves
valid applied v2 rows as immutable, public-key-verified exact-replay evidence;
it never rewrites their frozen response-signature domain. Pending v2 and any
unknown/malformed legacy row block startup mutations and readiness. Run
`scripts/check-nakama-research-control-activation.sh` while all writers are
stopped before enabling a v3 writer, then require the candidate's own readiness
scan; the offline script is only a structural preflight and explicitly records
that startup/readiness cryptographic validation is still outstanding. Because
the startup scan also requires every applied or pending control to resolve a
strictly verified durable session in the same read-only snapshot, partial
backup/restore state fails closed before the readiness write probe. The
preflight's stopped-service check is an instantaneous observation, so the
orchestrator must keep its external writer fence held through candidate
readiness. Because old strict-v2 binaries cannot read new v3 rows, the
Integration release must bump its storage epoch and must not auto-rollback
after v3 writes become possible.

## Start work

1. Work only from `/home/alex/projects/trillionnium-nakama`.
2. Run `bash scripts/project-preflight.sh`.
3. Create a focused `feature/nakama-*` branch.
4. Define or update a versioned contract before coupling another repository.

Run all local acceptance gates with:

```bash
bash scripts/check-nakama-p0.sh
```

Run the Paper Raid gates with:

```bash
make paper-raid-check
```

The gate validates the contract vectors, core and restart tests, a pinned image
build, hardened Compose startup on a random loopback port, liveness/readiness,
and the DB-loss readiness failure. See [docs/OPERATIONS.md](docs/OPERATIONS.md)
and [docs/TESTING.md](docs/TESTING.md).

No existing World game-server or legacy E2E implementation is canonical here;
older drafts are migration input only. Cross-repository communication uses the
versioned artifacts in `contracts/`, never sibling working-tree imports.
