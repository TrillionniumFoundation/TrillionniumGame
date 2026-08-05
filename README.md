# Trillionnium Nakama

Independent real-time match authority for Trillionnium.

The active P0 slice, `nakama-authoritative-match-evidence-v1`, implements a
fixed two-participant authoritative match, durable command/event evidence,
restart/resume, authenticated cursor-based archive catch-up, and a Nakama-signed
`MatchCompletedV1`. Product gameplay remains in World; research
authorization/evaluation remains in Hepta; consensus, ingress, finality, and
proofs remain in Chain.

This is not yet a production or release claim. P0 is single-host, Integration
remains blocked, and real Hepta/Chain compatibility must be locked separately.

## Start work

1. Work only from `/home/alex/projects/trillionnium-nakama`.
2. Run `bash scripts/project-preflight.sh`.
3. Create a focused `feature/nakama-*` branch.
4. Define or update a versioned contract before coupling another repository.

Run all local acceptance gates with:

```bash
bash scripts/check-nakama-p0.sh
```

The gate validates the contract vectors, core and restart tests, a pinned image
build, hardened Compose startup on a random loopback port, liveness/readiness,
and the DB-loss readiness failure. See [docs/OPERATIONS.md](docs/OPERATIONS.md)
and [docs/TESTING.md](docs/TESTING.md).

No existing World game-server or legacy E2E implementation is canonical here;
older drafts are migration input only. Cross-repository communication uses the
versioned artifacts in `contracts/`, never sibling working-tree imports.
