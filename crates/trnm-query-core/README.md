# trnm-query-core

Status: **module documentation; feasibility-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-query-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `matchmaker-query`

## Status and authority

This document is the current module-level engineering contract for `trnm-query-core`. Its authority is limited to the module boundary described here: **bounded query parser and evaluation subset**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `feasibility-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Lexing, parsing, complexity limits, and deterministic evaluation for the currently implemented query subset.

Non-goals: It does not provide complete Nakama grammar, search indexes, scoring, distributed execution, or cursor persistence.

## Architecture and dependencies

Matchmaker and storage search services may consume the AST after compatibility profile and cost controls are accepted.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Token, clause, nesting, and expression complexity are bounded. Syntax and evaluation errors are stable and deterministic.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Parsing is total over bounded input, rejects ambiguity, and cannot trigger unbounded recursion or allocation.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Queries are untrusted input. Resource limits, parser fuzzing, injection isolation, and data-visibility checks are mandatory.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-query-core --all-targets --locked
cargo clippy --package trnm-query-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Consumers emit parse/evaluation latency, complexity rejection, and result-limit metrics without query payload labels.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Immutable-oracle closure, complete grammar leaf mapping, search execution/scoring, and performance evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SCOPE-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
