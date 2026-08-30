# Session authentication and refresh-family source candidate

Status: source candidate. No session compatibility, security acceptance, production-readiness or
cutover credit is granted by this document.

## Implemented source boundary

The current branch adds two reusable components without yet changing the existing authority
endpoint credential contract:

1. a PostgreSQL/CockroachDB refresh-family repository over the authoritative
   `trnm_session_families` and `trnm_refresh_tokens` tables;
2. a strict epoch-routed HMAC JWT verifier and opaque refresh-credential parser for later server
   middleware integration.

The access-token verifier requires:

- exact `Bearer ` framing with no whitespace inside the token;
- an epoch key identifier and matching `trnm_kep` claim;
- a configured issuer and audience;
- `iat` and `exp` with a maximum 15-minute lifetime;
- a 32-character lowercase-hex user subject (`sub`);
- a 32-character lowercase-hex access-token ID (`jti`);
- a 32-character lowercase-hex session-family ID (`sid`);
- an unsigned session generation (`sgn`).

Legacy key fallback is disabled for this profile. Key material is bounded and redacted from debug
output. Refresh credentials use the form `<32 lowercase hex token id>.<opaque secret>`. The raw
credential is never persisted by the new repository; its SHA-256 digest is stored and compared.

## Transactional refresh-family behavior

All mutating repository operations use serializable transactions.

- Family creation inserts one family and one active refresh token atomically.
- Access verification requires exact user, family generation, active-token presence and no
  revocation reason.
- Rotation locks both presented-token and family rows, consumes the old token, inserts a new
  digest and advances family generation in one transaction.
- Reuse of a consumed, non-active or generation-stale token atomically revokes the family with the
  replay-detected reason and consumes any currently active replacement.
- Logout/administrative revocation clears the active token and consumes it in the same
  transaction.
- All identity-state failures use a generic unauthenticated domain failure to avoid disclosing
  whether a family, token or user exists.

An ambiguous response after a successful refresh commit can conservatively revoke the family on
retry. This is fail-closed but not user-transparent; a dedicated refresh-rotation receipt/idempotency
contract remains a required follow-up before production credit.

## Deliberately unchanged behavior

The existing `/v1/authority/*` and WebSocket vertical-slice paths continue to use their reviewed
candidate admin credential. This change does not silently reinterpret that credential as a user
access token. HTTP middleware, refresh/logout endpoints and socket revocation checks will be added
as a separate integration step with exact dual-database tests.

## Remaining blockers

- server configuration and protected HTTP middleware wiring;
- family bootstrap, refresh, logout and `me` endpoints;
- persistent WebSocket connection revocation notification;
- refresh-response idempotency for ambiguous commit responses;
- expiry cleanup and the schema representation for `RevocationReason::Expired`;
- dual-profile live database fault tests for rotate, replay and logout;
- key rotation/reload, compromise drills and independent security review;
- immutable Nakama/API/SDK differential evidence.

All SG and compatibility claims remain false.
