# Runtime readiness and drain contract

Status: design and acceptance contract. It does not prove that `trnm-server` currently implements this lifecycle.

## Lifecycle

```text
starting
 -> migrations-verified
 -> dependencies-ready
 -> serving
 -> draining
 -> stopped
```

A process may become ready only after:

- typed configuration has been parsed and redacted;
- the exact production migration-chain identity has been verified;
- required database pools and key providers are healthy;
- protocol listeners are bound;
- background supervisors are running;
- no fatal startup divergence exists.

## Drain ordering

1. mark readiness false;
2. reject new ownership and long-lived sessions;
3. stop accepting new HTTP/gRPC/WebSocket work according to the approved protocol behavior;
4. signal connection actors and authority owners;
5. wait only within bounded deadlines;
6. stop schedulers and outbox leasing;
7. allow already committed outbox records to remain reclaimable;
8. flush telemetry without blocking durable correctness;
9. close pools and listeners;
10. exit with a stable class.

A drain timeout must not delete durable work, transfer authority without generation fencing, acknowledge uncommitted commands or report a clean shutdown when recovery is required.

## Readiness versus health

- liveness/health proves only that the process can execute its health loop;
- readiness proves that it may receive the declared traffic class;
- degraded dependencies can keep liveness true while readiness is false;
- a source-candidate route must not use a 200 readiness response as production evidence.

## Required evidence

- process signal and repeated-signal matrix;
- in-flight HTTP/gRPC requests;
- open WebSocket connections and slow consumers;
- active authority owners;
- leased outbox records;
- database disconnect during drain;
- deadline expiration and forced process termination;
- restart and reclaim;
- node replacement and stale-generation rejection;
- exact metrics/logs and no secret leakage.

This contract is part of `GAP-P0-SERVER-001` and `GATE-OPERATIONS`; it cannot close either without exact-head execution and independent SRE review.