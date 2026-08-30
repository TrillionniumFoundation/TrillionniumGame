# Source-candidate promotion contract

Status: binding plan-v3 execution contract.

## Purpose

A source candidate proves only that a bounded implementation and its declared tests exist in the reviewed tree. It does not prove that the source compiles, executes, matches Nakama, survives failure, is independently reviewed, or is safe to promote.

This contract prevents source presence, issue closure, workflow definitions, local anecdotes or PR labels from being interpreted as gap closure.

## Promotion states

```text
source-candidate
  -> locally-verified
  -> remote-verified
  -> independently-reviewed
  -> accepted
```

A candidate may also become `rejected` or `superseded`. Promotion cannot skip a state unless an evidence bundle proves every skipped state's requirements and the transition checker accepts the event.

## Source-candidate requirements

A source candidate must declare:

- unique candidate ID;
- affected gap IDs;
- exact paths;
- source contract and claim boundary;
- deterministic local commands;
- unimplemented items;
- required next evidence;
- every compatibility, production, public-online and retirement claim as false.

## Locally verified

Requires a clean, exact checked-out commit/tree and:

- formatter success;
- unit/integration/property/fuzz commands required by the component;
- strict lints;
- generated candidate identity manifest;
- non-zero assertion/test accounting;
- retained logs and digests;
- no uncommitted materialization needed to make tests pass.

Local verification grants no merge or compatibility credit.

## Remote verified

Requires target-repository execution on the exact current PR head:

- non-empty workflow and check collections;
- terminal successful required lanes;
- exact checkout identity in logs;
- artifact IDs and SHA-256 digests;
- candidate manifest digest;
- explicit assertion totals;
- no skipped required prerequisite;
- evidence-index registration.

A relay may perform heavy work only when the target candidate manifest is verified by the target repository. Relay success alone is not remote verification.

## Independently reviewed

P0/P1 candidates require a reviewer who is not the implementation author and has the declared domain role. Review must bind:

- exact commit/tree and evidence IDs;
- decision and review timestamp;
- limitations and expiry;
- unresolved divergences;
- whether the reviewer examined source, test design, result artifacts and claim boundaries.

Self-approval, missing reviewer identity, stale approval after a new head, or review of an older artifact does not count.

## Accepted

A candidate becomes accepted only when:

1. every dependency gap is closed or explicitly non-blocking for the scoped claim;
2. all close criteria have machine assertions;
3. required evidence is current and valid;
4. no unexplained P0/P1 child divergence remains;
5. required independent reviews are accepted;
6. gate derivation admits the scoped transition;
7. external administrator state is read back when relevant;
8. public wording remains inside the accepted capability/profile boundary.

Accepted does not mean globally compatible. Claim scope must list denominator leaf IDs, environment/profile and evidence IDs.

## Forbidden shortcuts

The following never close a gap by themselves:

- a new file, crate, test, workflow, issue or PR;
- a green run for an older commit;
- an empty or skipped collection;
- a local command without retained exact-source evidence;
- a relay run against an unverified target;
- a manually edited status field;
- a reviewer who authored the implementation;
- a waiver for identity, authorization, ACL, money, sequence, durable effect, single authority or data corruption;
- merging into main.

## Machine enforcement

`scripts/check-source-candidate-boundaries.py` validates structured candidate contracts and forbids premature claims. `scripts/check-status-transitions.py`, the evidence index and `scripts/derive-gates.py` control promotion beyond the source-candidate state.
