# Immutable Nakama oracle bootstrap

This profile starts an **unmodified, digest-pinned Nakama 3.40.0 image** with a dedicated digest-pinned PostgreSQL 17.6 database. It is the first half of SG2's two-lane oracle design.

It does not contain an instrumented Nakama build, a Rust candidate, production data, provider credentials or shared writable state. The only published listener is Nakama port 7350 bound to `127.0.0.1`; PostgreSQL remains on the internal backend network.

Run locally from a clean checkout with Docker Compose:

```bash
scripts/oracle/run-immutable-smoke.sh run/immutable-oracle
```

The runner generates fixture-only secrets in a mode-0600 temporary environment file, performs migration and health checks, records exact container image IDs and environment facts, emits canonical evidence, and removes containers and volumes by default.

The evidence status `immutable-oracle-smoke-passed` is diagnostic only. It does not prove immutable/instrumented equivalence, protocol parity, data parity, SG2 completion, compatibility, production readiness or public-online approval.
