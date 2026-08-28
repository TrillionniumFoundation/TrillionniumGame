# SG2 immutable oracle bootstrap implementation

Status: **implemented locally; remote Docker evidence blocked until Actions runs**  
Plan position: SG2 / `GATE-ORACLE`.

## Scope

This slice establishes only the immutable lane required by the oracle specification:

- Nakama OSS `v3.40.0`, commit/tree bound in `oracle-lock.json`;
- official Nakama image pinned by digest;
- PostgreSQL `17.6-alpine3.22` pinned by digest;
- isolated named volume and internal backend network;
- loopback-only client port with no PostgreSQL host publication;
- deterministic fixture-secret derivation without committed usable credentials;
- migration, process health and database table-count smoke;
- canonical evidence with lock/config/image/environment/result digests;
- mandatory false compatibility, SG2, production and public-online claims.

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
- rendered configuration containing fixture secrets is hashed and deleted;
- runtime facts and evidence contain image IDs and digests, not secret values;
- cleanup removes the disposable database unless `TRNM_KEEP_ORACLE=1` is explicitly set for diagnostics.

## Remaining SG2 work

- build and review the minimum instrumented source patch;
- pin its source tree and produced image digest;
- define injected clock/random/provider capture interfaces;
- build the normalizer registry with forbidden-field tests;
- run immutable and instrumented lanes from the same logical seed at least ten times;
- compare HTTP, gRPC, JSON/protobuf socket, DB effects, hooks, provider intents and metrics;
- resolve every P0/P1 divergence;
- obtain independent review and fresh exact-head evidence.
