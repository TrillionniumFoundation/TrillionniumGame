# Immutable / instrumented Oracle differential engine

Status: **candidate implementation; SG2 remains open**  
Stack position: extends the immutable Oracle and candidate normalizer work in parent PR #8.

## Purpose

This slice defines how independently captured immutable and instrumented Nakama observations are paired, normalized, compared, repeated and converted into typed evidence. It deliberately does not create or bless an instrumented Nakama image.

## Observation identity

Every observation binds:

- lane: `immutable` or `instrumented`;
- run ID;
- case ID;
- attempt number;
- exact input SHA-256;
- named output surfaces.

Pair comparison first requires identical case, attempt and input identity. Runtime-only `run_id`, `lane` and `attempt` are then removed from normalized stability hashes; they cannot create false nondeterminism.

## Surface severity

| Severity | Surfaces | Gate effect |
| --- | --- | --- |
| P0 | database effects, hooks, provider intents, durable events | stop merge and SG2 immediately |
| P1 | HTTP, gRPC, realtime, session, account | block compatibility gate |
| P2 | metrics and performance | block operational profile |
| P3 | explicitly non-contract diagnostic surfaces | record and review |

Missing surfaces, type changes, list-length changes and scalar differences are emitted with exact JSON paths. Pair identity mismatches, duplicate lanes, missing attempts and raw token material fail before evidence generation.

## Ten-run stability

A candidate corpus requires contiguous attempts `1..10` for both lanes for every case. Each lane must produce one normalized hash across all attempts. A lane with multiple hashes receives a P1 nondeterminism divergence.

Allowed normalizers come only from `config/oracle-normalizers.json`. Identity, authorization, error code and durable-effect differences remain forbidden by the parent registry. Raw token fields and compact JWT-shaped strings are rejected from observations and output evidence.

## Fail-closed claims

Even a zero-divergence synthetic or real corpus keeps all of these false:

```text
instrumented_equivalence
sg2_complete
compatibility_credit
production_ready
public_online
```

Independent review must still establish the instrumented image provenance, patch boundary, complete surface coverage, normalizer approval and exact repeated real corpus.

## Delivered validation

- eight unit tests;
- allowed clock-field normalization;
- P0 database-effect and P1 wire divergence classification;
- pair identity and missing-attempt rejection;
- token leakage rejection;
- ten-attempt stability verification;
- lane nondeterminism detection;
- synthetic clean corpus and malicious P0 corpus CLI paths;
- exact-head workflow artifact generation.

## Remaining work

- build and review the minimum instrumented Nakama source patch;
- capture immutable and instrumented observations from isolated databases;
- add clock/random/provider injection provenance;
- add gRPC, JSON/protobuf realtime, DB logical effects, hook order and provider intent capture;
- review every normalizer rule independently;
- run a representative real corpus ten times per lane;
- resolve every P0/P1 divergence;
- bind fresh artifacts and independent reviewers to `GATE-ORACLE`.
