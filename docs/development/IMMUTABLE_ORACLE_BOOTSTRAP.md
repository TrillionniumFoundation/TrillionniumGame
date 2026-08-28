# SG2 immutable Oracle bootstrap implementation

Status: **implemented-local candidate; remote Docker evidence blocked until Actions runs**  
Plan position: SG2 / `GATE-ORACLE`.

## Scope

This slice establishes only the immutable lane required by the Oracle specification:

- Nakama OSS `v3.40.0`, commit/tree bound in `oracle-lock.json`;
- official Nakama image pinned by digest;
- PostgreSQL `17.6-alpine3.22` pinned by digest;
- isolated named volume and internal backend network;
- loopback-only client port with no PostgreSQL host publication;
- deterministic fixture-secret derivation without committed usable credentials;
- migration, process health and database table-count smoke;
- canonical evidence with lock/config/image/environment/result digests;
- a candidate normalizer registry bound by SHA-256;
- mandatory false compatibility, SG2, production and public-online claims.

## Normalizer boundary

The registry permits only six exact wall-clock fields:

```text
jwt-access:  $.iat, $.exp
jwt-refresh: $.iat, $.exp
account:     $.user.create_time, $.user.update_time
```

It rejects normalization touching identity, authorization, ACL, version, cursor, error code, score/rank, money, provider transaction, match/party/ticket, signature or key identity. It also rejects raw token fields and JWT-shaped strings in retained evidence.

The registry remains `candidate-reviewed-required`. Merely having a valid registry does not prove that a differential corpus uses it correctly, and does not close SG2.

## Evidence boundary

A successful run may state only:

```text
immutable-oracle-smoke-passed
diagnostic-only
```

It may not state or imply:

```text
instrumented equivalence
SG2 complete
wire or behavioral compatibility
production ready
public online
Nakama retired
```

## Safety properties

- the immutable lane never shares a writable database with another lane;
- no source checkout or sibling repository is mounted into Nakama;
- no production provider credential is accepted by the profile;
- candidate commit must be a non-zero exact 40-character SHA;
- rendered configuration containing fixture secrets is hashed and deleted;
- runtime facts and evidence contain image IDs and digests, not secret values;
- normalizer registry bytes are cryptographically bound into evidence;
- cleanup removes the disposable database unless `TRNM_KEEP_ORACLE=1` is explicitly set for diagnostics.

## Local verification

The static suite checks image digests, network isolation, repository-relative configuration, required evidence fields, no-overclaim claims and the normalizer policy. Unit coverage includes forbidden and duplicate rules, JWT/account normalization, token leakage, digest tamper, unhealthy state, positive-claim tamper and zero candidate SHA.

## Remaining SG2 work

- build and review the minimum instrumented source patch;
- pin its source tree and produced image digest;
- define injected clock/random/provider capture interfaces;
- independently approve or reject each candidate normalizer;
- run immutable and instrumented lanes from the same logical seed at least ten times;
- compare HTTP, gRPC, JSON/protobuf socket, DB effects, hooks, provider intents and metrics;
- resolve every P0/P1 divergence;
- obtain independent review and fresh exact-head evidence.
