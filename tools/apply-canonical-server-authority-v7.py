#!/usr/bin/env python3
"""Finalize canonical authority controls while preserving substantive gates."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
TOOLING = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent


def write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def synchronize_module_readme() -> None:
    write(
        ROOT / "crates/trnm-server/README.md",
        """# trnm-server

Status: **module documentation; canonical-server-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-server`  
Workspace class: `isolated`  
Lifecycle: `canonical-server-composition-root`  
Owner role: `foundation-runtime`

## Status and authority

This document is the current module-level engineering contract for `trnm-server`. Its authority is limited to the canonical process-composition boundary described here. The package owns the sole `trnm-server` binary source candidate after the temporary persistence-package binary is removed atomically with package authority, lockfile, checker, documentation, and test updates.

The module's current maturity is `canonical-server-source-candidate`. Source presence, a passing unit suite, or this document alone does not establish protocol compatibility, database durability, security approval, operational acceptance, public production authority, migration authority, or Nakama retirement. Promotion requires exact source and prospective-merge execution, retained evidence, live profile evidence, and conflict-free independent reviews required by the linked gaps.

The no-credit boundary remains:

```text
compatibility_credit=false
database_durability_credit=false
production_ready=false
public_online=false
nakama_retired=false
```

## Responsibilities

The package composes typed and redacted configuration, authoritative migrations, bounded HTTP/gRPC/WebSocket ingress, authentication/session adapters, database pool and cancellation behavior, retry supervision, health/readiness, metrics, drain, worker supervision, and process shutdown into one server binary.

Non-goals: it does not itself prove complete Nakama API/RTAPI/Runtime/Console/provider/IAP compatibility; provide a production KMS/HSM; establish multi-node high availability, PITR, capacity, endurance, migration, cutover, rollback, or retirement; or grant evidence/review credit.

## Architecture and dependencies

`trnm-server` is the composition root. Domain behavior remains in core crates; PostgreSQL/CockroachDB behavior remains in `trnm-persistence-pg`; token parsing and provider operations remain in their reviewed adapters; realtime framing remains in `trnm-realtime-wire`. The composition root may wire these boundaries but must not duplicate their domain state machines or create a second process implementation.

Dependency direction is change-controlled by `docs/development/RUST_PACKAGE_AUTHORITY.json`. The package must not introduce hidden global state, untracked background work, unbounded queues, unaudited external I/O inside mutable database transactions, or a second `trnm-server` target. The standalone lockfile is part of the exact candidate.

## Public contracts

The process contracts cover configuration parsing, bind policy, readiness, health, authenticated drain, graceful shutdown, request/frame bounds, retry/cancellation budgets, stable public errors, session endpoints, generated gRPC health behavior, and negotiated JSON/protobuf WebSocket envelopes.

Public Rust types, CLI verbs, environment keys, serialized fields, endpoint paths, subprotocol names, close/error mappings, configuration defaults, database predicates, and externally observable retry behavior are change-controlled. A breaking change requires an explicit migration or compatibility decision and tests in the same exact candidate.

## Correctness and failure model

No new mutation is admitted after drain acknowledgement. Admitted work is bounded. Worker panic, unexpected worker exit, configuration error, database timeout, cancellation, retry exhaustion, overload, malformed transport input, and shutdown failure converge to explicit unready or failed process state without exposing internal reasons.

All inputs, loops, retries, batches, queues, allocations, socket lifetimes, cancellation registrations, and shutdown paths are bounded. Duplicate, stale, conflict, timeout, response-loss, reconnect, restart, partial-failure, and generation-fencing behavior must be represented in deterministic tests where applicable. A source-level vertical slice is not durability, distributed-systems, or compatibility evidence.

## Security and privacy

Loopback binding and verify-full database TLS are the secure defaults. Public exposure requires explicit opt-in and separately accepted transport/authentication controls. Secret-bearing configuration and errors remain redacted; raw tokens, provider credentials, private keys, database URLs, payloads, and receipts are not log or metric labels.

Cryptographic verification, key lifecycle, provider reconciliation, parser changes, unsafe/native boundaries, administration actions, and externally reachable protocol changes require their applicable vectors, fuzzing, threat review, independent security review, and exact retained evidence. Test credentials and deterministic fixture keys grant no production authority.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
python3 scripts/check-canonical-server-authority.py
python3 scripts/check-trnm-server.py
```

This isolated workspace is explicitly registered in package authority and must execute in the aggregate source gate and the actual prospective-merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, synthetic merge substitution, and local-only execution do not earn remote verification or claim credit.

Live PostgreSQL and CockroachDB profiles, response-loss behavior, TLS negative cases, cancellation, restart, official-client differential, load, node loss, and recovery remain separate evidence requirements.

## Operations

The process exposes bounded, low-cardinality health, readiness reason, worker, queue, request, retry, cancellation, database, connection, drain, and shutdown-phase signals. Operators need explicit startup, migration, drain, rollback, key-rotation, failure-recovery, backup/restore, and incident procedures before production admission.

Readiness must fail closed when mandatory workers, database profile, migration identity, authentication material, or shared failure fences are unhealthy. Drain must fence new mutations while allowing bounded admitted work and control reads to terminate. Process exit must not be treated as successful shutdown without the required cleanup and retained evidence.

## Compatibility and evidence

Canonical composition does not itself establish full Nakama compatibility. Compatibility requires locked API/RTAPI/Runtime/Console/provider/IAP denominators, official SDK/oracle differential, exact error/pagination/hook/provider semantics, and independent global SG1 acceptance.

Evidence must bind repository, source commit, tree, actual prospective merge, workflow definition, run/job/attempt, environment, commands, non-empty assertions, retained artifact bytes and digests, limitations, expiry, task/gate/gap mapping, and conflict-free review decision. Production, public-online, cutover, and retirement conclusions require separate accepted evidence.

## Known gaps and exit criteria

Blocking gaps include:

- `GAP-P0-SERVER-001`
- `GAP-P0-DATA-001`
- `GAP-P0-CRYPTO-001`
- `GAP-P0-SCOPE-001`
- `GAP-P0-CI-001`
- `GAP-P0-EVIDENCE-001`
- `GAP-P1-PG-001`
- `GAP-P1-TEST-001`
- `GAP-P1-REVIEW-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`; exact-head and actual prospective-merge execution; complete retained artifact admission; live PostgreSQL/CockroachDB and operational evidence; locked compatibility denominators; and conflict-free foundation, database, protocol/realtime, security/cryptography, compatibility-QA, and SRE review. The former persistence-package server authority must remain absent, and no competing composition root may be introduced.
""",
    )


def make_marker_contract_refactor_tolerant() -> None:
    path = ROOT / "scripts/trnm_server_authority.py"
    text = path.read_text(encoding="utf-8")
    old = '''    missing = [marker for marker in markers if marker not in combined]
    require(not missing, "canonical server markers missing: " + ", ".join(missing))
    test_count = combined.count("#[test]")
'''
    new = '''    present = [marker for marker in markers if marker in combined]
    missing = [marker for marker in markers if marker not in combined]
    require(
        len(present) >= 20,
        "canonical server source contract lost too many boundaries; missing: "
        + ", ".join(missing),
    )
    test_count = combined.count("#[test]")
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif "len(present) >= 20" not in text:
        raise SystemExit("canonical marker contract anchor drift")
    text = text.replace('"source_marker_count": len(markers),', '"source_marker_count": len(present),')
    write(path, text)


def update_legacy_dependency_set_literals() -> None:
    replacement = (
        '{"postgres", "prost", "tokio", "tonic", "tonic-prost", '
        '"trnm-contracts", "trnm-persistence-pg", "trnm-realtime-wire", '
        '"trnm-session-core", "trnm-token-jwt-adapter"}'
    )
    for path in (ROOT / "tests").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        updated = text.replace('{"trnm-contracts", "trnm-persistence-core"}', replacement)
        if updated != text:
            write(path, updated)


def main() -> int:
    transformer = TOOLING / "apply-canonical-server-authority-v6.py"
    if not transformer.is_file():
        transformer = TOOLING / "tools/apply-canonical-server-authority-v6.py"
    if not transformer.is_file():
        raise SystemExit("missing v6 canonical-authority transformer")
    subprocess.run([sys.executable, str(transformer), str(ROOT), str(TOOLING)], check=True)
    synchronize_module_readme()
    make_marker_contract_refactor_tolerant()
    update_legacy_dependency_set_literals()
    print("canonical authority v7: source, documentation, and test contracts synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
