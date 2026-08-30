# PostgreSQL/CockroachDB operational transport controls

Status: source candidate. This document grants no compatibility, durability, availability,
production-readiness or cutover credit.

## Implemented boundary

The canonical `trnm-server` database path now uses a bounded `r2d2` pool rather than retaining
one process-wide blocking client. Each request acquires a connection with a bounded timeout,
applies an explicit session policy, executes one repository operation, and returns the
connection to the pool.

Two transport modes are accepted:

- `plaintext-candidate`: requires `TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE=1`; intended only for
  loopback CI and local evidence profiles.
- `verify-full`: requires TLS and preserves the connector's certificate-chain and hostname
  verification. A custom root may be supplied. Client identity material is accepted only as a
  certificate/PKCS#8 private-key pair. No invalid-certificate or invalid-hostname bypass exists.

TLS is configured with a minimum protocol version of TLS 1.2. Secret URLs, admin tokens and
private-key paths/material are redacted from debug output.

## Pool and timeout configuration

| Variable | Default | Bounds |
| --- | ---: | --- |
| `TRNM_SERVER_DATABASE_POOL_MAX_SIZE` | 8 | 1–256 |
| `TRNM_SERVER_DATABASE_POOL_MIN_IDLE` | 1 | 0–max size |
| `TRNM_SERVER_DATABASE_POOL_ACQUIRE_TIMEOUT_MS` | 2000 | 10–120000 |
| `TRNM_SERVER_DATABASE_POOL_IDLE_TIMEOUT_MS` | 60000 | 1000–3600000 |
| `TRNM_SERVER_DATABASE_POOL_MAX_LIFETIME_MS` | 900000 | idle timeout–86400000 |
| `TRNM_SERVER_DATABASE_STATEMENT_TIMEOUT_MS` | 5000 | 50–600000 |
| `TRNM_SERVER_DATABASE_LOCK_TIMEOUT_MS` | 1000 | 10–statement timeout |
| `TRNM_SERVER_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS` | 5000 | 50–600000 |

Every checkout applies `application_name` and `statement_timeout`. PostgreSQL additionally
receives `lock_timeout` and `idle_in_transaction_session_timeout`. Configuration, acquisition
and session-policy failures are fail-closed stable domain failures.

Serializable retries retain a total elapsed-time and attempt budget. Safe-backoff retries now
use bounded half-to-full jitter, reducing synchronized retry waves while remaining inside the
declared total budget.

## Metrics

The `/metrics` endpoint exposes:

- current pool maximum, open and idle connections;
- checkout attempts, checkout timeouts and session-policy failures;
- database operation attempts, retries, exhausted retry budgets and cumulative retry sleep.

These are process-local source metrics. They are not an SLO report.

## Remaining blockers

The following remain mandatory before `TG-V3-021` or `GAP-P1-PG-001` can close:

1. cancellation of an already-running blocking SQL operation on request deadline or shutdown;
2. TLS certificate rotation/reload evidence and expiry/failure fault injection;
3. pool saturation, connection churn and database failover tests on both authoritative profiles;
4. independently reviewed timeout/retry policy and accepted exact-head evidence;
5. production topology, HA, PITR, capacity and endurance proof.

The pool/TLS source implementation therefore remains `source-candidate`; all product claims
remain false.
