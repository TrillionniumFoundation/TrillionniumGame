# ADR-0003: World command atomic sidecar commit

Status: proposed for exact-head review

## Decision

A verified World result is committed through an injected `worldcommand.CommitPersister`. The persister applies the original signed command to the Nakama core, produces the candidate core snapshot, and writes that snapshot together with the candidate World command journal in one version-fenced Nakama `StorageWrite` batch.

External World execution happens before this persister is invoked and outside the core mutex and storage/database transaction.

## Failure semantics

- Before the storage call, an error rolls the in-memory core back to its prior snapshot.
- A storage rejection or missing/malformed multi-object acknowledgement is treated as ambiguous; the runtime generation terminates and does not acknowledge success.
- Restart reloads the server-owned storage objects and resolves the authoritative result.
- The target path never falls back to legacy direct application.

## Consequences

This preserves the original signed command as the canonical event payload while storing deterministic World state/receipt material in a separate server-owned journal. It still requires deployed evidence that Nakama/PostgreSQL executes the multi-object batch atomically.

Trillionnium Chain is outside this ADR.
