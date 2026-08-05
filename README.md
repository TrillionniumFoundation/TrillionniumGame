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
