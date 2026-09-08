#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = "crates/trnm-presence-router-v2/src/lib.rs"
README_PATH = "crates/trnm-presence-router-v2/README.md"


def stage3(path: str) -> str:
    return subprocess.check_output(["git", "show", f":3:{path}"], cwd=ROOT).decode("utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


lib = stage3(LIB_PATH)
lib = replace_once(
    lib,
    '''//! Strict bounded realtime routing primitives.
//!
//! Connection-owned presence records are generation fenced. The optional
//! session registry composes those records with a monotonic session-generation
//! revocation high-water so logout, credential reset, refresh replay, or an
//! administrator revocation can retire every bound socket without permitting a
//! stale generation to rejoin.
''',
    '''//! Strict bounded realtime routing, ownership and recovery primitives.
//!
//! Connection-owned presence records are generation fenced. The session
//! registry composes them with monotonic revocation high-water and a verified
//! namespace rollover checkpoint. Connection actors, authenticated reconnect
//! cursors and receipt-reconciled disconnect effects are separately bounded and
//! fail closed across stale callbacks, response loss, reconnect and drain.
''',
    "module contract",
)
lib = replace_once(
    lib,
    "mod router;\n",
    "mod connection_actor;\nmod disconnect_journal;\nmod reconnect_cursor;\nmod router;\n",
    "module declarations",
)
lib = replace_once(
    lib,
    "pub use router::{MutationDisposition, PresenceDelta, PresenceError, PresenceRouter};\n",
    '''pub use connection_actor::{
    ConnectionActor, ConnectionActorConfig, ConnectionActorError, ConnectionActorState,
    CorrelationId, InboundFrame, OutboundFrame, PendingRequest, RequestHandle,
    MAX_CONNECTION_ACTOR_BUFFER_BYTES, MAX_CONNECTION_ACTOR_FRAME_BYTES,
    MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
};
pub use disconnect_journal::{
    DisconnectArchiveTombstone, DisconnectDispatchBinding, DisconnectIntentId, DisconnectJournal,
    DisconnectJournalConfig, DisconnectJournalEpoch, DisconnectJournalError, DisconnectJournalId,
    DisconnectOperation, DisconnectOutcomeEvidence, DisconnectOutcomeKind,
    DisconnectOutcomeVerifier, DisconnectRecord, DisconnectState, DisconnectUnknownEvidence,
    LeaseToken, ReconciliationDisposition, RetryDisposition, WorkerId,
    DISCONNECT_VERIFIER_RECEIPTS_PER_ATTEMPT, MAX_DISCONNECT_ACTIVE_RECORDS,
    MAX_DISCONNECT_ATTEMPTS, MAX_DISCONNECT_TOMBSTONES, MAX_DISCONNECT_VERIFIER_RECEIPTS,
};
pub use reconnect_cursor::{
    ReconnectCursor, ReconnectCursorAuthenticator, ReconnectEvent, ReconnectIdentity,
    ReconnectJournal, ReconnectJournalConfig, ReconnectJournalError, ReconnectJournalId,
    ReconnectProducerEpoch, ReconnectSessionGeneration, ReconnectStreamId,
    MAX_RECONNECT_JOURNAL_CAPACITY,
};
pub use router::{MutationDisposition, PresenceDelta, PresenceError, PresenceRouter};
''',
    "recovery exports",
)
(ROOT / LIB_PATH).write_text(lib, encoding="utf-8")

readme = stage3(README_PATH)
readme = replace_once(
    readme,
    "This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to deterministic in-process presence, connection ownership, session-generation fencing, and bounded revocation fanout. Source presence or a passing unit suite does not establish distributed delivery, durable revocation, Nakama compatibility, or production acceptance.\n",
    "This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to deterministic in-process presence, connection ownership, session-generation fencing, bounded revocation fanout, connection-actor admission, authenticated reconnect cursors and receipt-reconciled disconnect effects. Source presence or a passing unit suite does not establish distributed delivery, durable revocation, Nakama compatibility, or production acceptance.\n",
    "authority boundary",
)
readme = replace_once(
    readme,
    "- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;\n- fail-closed invariant checking before and after staged mutations.\n",
    "- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;\n- bounded inbound, outbound, pending-request, frame-byte and aggregate actor payload budgets;\n- actor-issued request handles binding correlation IDs to unique admission generations;\n- authenticated reconnect cursors bound to journal, stream, session generation and producer epoch;\n- disconnect dispatch fencing across journal epoch, intent, operation, socket generation, attempt, worker lease and endpoint;\n- an epoch-wide verifier-receipt budget reserved at admission and consumed before owner-map mutation;\n- fail-closed invariant checking before and after staged mutations.\n",
    "responsibility expansion",
)
readme = replace_once(
    readme,
    "It does not own network transport, identity-provider issuance, durable revocation storage, cluster membership, cross-node fanout, reconnect orchestration, or actual socket termination.\n",
    "It does not own network transport, identity-provider issuance, durable revocation storage, cluster membership, cross-node fanout, production receipt verification, durable checkpoint custody, reconnect transport orchestration, or actual socket termination.\n",
    "non-ownership boundary",
)
readme = replace_once(
    readme,
    "Revocation and connection high-waters are never opportunistically evicted. Long-lived deployments may compact only through a quiescent namespace rollover: every mutation carries the active `SessionRouteNamespace`; a trusted verifier must accept a proof binding the current registry/router revisions, retained cardinalities, a durable checkpoint digest and a producer-barrier digest; active bindings and presence entries must be zero. The rollover advances the namespace monotonically, emits a restartable `SessionRouteCheckpoint`, resets the bounded high-water window, and permanently rejects delayed messages from retired namespaces.\n",
    '''Revocation and connection high-waters are never opportunistically evicted. Long-lived deployments may compact only through a quiescent namespace rollover: every mutation carries the active `SessionRouteNamespace`; a trusted verifier must accept a proof binding the current registry/router revisions, retained cardinalities, a durable checkpoint digest and a producer-barrier digest; active bindings and presence entries must be zero. The rollover advances the namespace monotonically, emits a restartable `SessionRouteCheckpoint`, resets the bounded high-water window, and permanently rejects delayed messages from retired namespaces.

### Bounded recovery layers

`ConnectionActor` independently bounds frame admission, queued egress and pending request state. Every asynchronous response, cancellation and completion requires the exact live `RequestHandle`; numeric correlation reuse cannot let a delayed callback mutate a later admission. Drain preserves only the finite request set admitted before its fence.

`ReconnectJournal` authenticates each cursor and requires one exact journal/stream/session-generation/producer-epoch identity. Replay is contiguous and bounded; expired, ahead, tampered, wrong-key and retired-generation cursors fail closed.

`DisconnectJournal` binds every possible transport write to one immutable dispatch identity. After delivery becomes ambiguous, retry is forbidden until a trusted verifier accepts a typed exact outcome. One `Unknown` result may be retained and replayed exactly; a distinct second unknown fails without mutation. Admission reserves archive capacity and the worst-case verifier-receipt allowance. Every accepted receipt consumes one reservation before owner-map mutation, terminal archive releases unused reservation, and epoch advance clears the finite receipt namespace only after all active records have left.
''',
    "bounded recovery architecture",
)
readme = replace_once(
    readme,
    "Default and hard maxima are exported as constants. Resource exhaustion is a stable, no-mutation error. Public serialized fields, configuration mappings, and externally observable error classes are change-controlled.\n",
    "`ConnectionActorConfig`, `ReconnectJournalConfig` and `DisconnectJournalConfig` also reject zero, excessive and arithmetically unsafe limits. Disconnect construction checks the complete `tombstone_capacity × max_attempts × receipts_per_attempt` product against the repository hard cap.\n\nDefault and hard maxima are exported as constants. Resource exhaustion is a stable, no-mutation error. Public serialized fields, configuration mappings, and externally observable error classes are change-controlled.\n",
    "public recovery limits",
)
readme = replace_once(
    readme,
    "Capacity checks use checked projected post-transition cardinalities before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, exhausted, wrong-namespace or unverified rollover operations leave route state, indexes, revisions and deltas unchanged.\n",
    "Capacity checks use checked projected post-transition cardinalities before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, exhausted, wrong-namespace, wrong-handle, wrong-cursor, wrong-dispatch, receipt-reuse or unverified rollover operations leave route state, queues, indexes, revisions, counters, deltas and receipt owners unchanged.\n",
    "failure immutability",
)
readme = replace_once(
    readme,
    "Secrets, raw tokens, user payloads, receipts, and provider credentials must not appear in logs or metric labels. Revocation high-water is an enforcement input; it is not proof that a durable identity authority issued the revocation.\n",
    "Reconnect authenticators and disconnect outcome verifiers are trust-bearing interfaces. Production adapters must authenticate the complete typed identity and must not treat a nonzero digest or caller boolean as proof. Receipt ownership prevents cross-intent reuse only inside the retained epoch; durable rollover requires an authenticated checkpoint and absence protocol outside this in-memory candidate.\n\nSecrets, raw tokens, user payloads, receipts, and provider credentials must not appear in logs or metric labels. Revocation high-water is an enforcement input; it is not proof that a durable identity authority issued the revocation.\n",
    "security boundary",
)
readme = replace_once(
    readme,
    "- invalid, incoherent or unverified limit/rollover configuration.\n",
    "- invalid, incoherent or unverified limit/rollover configuration;\n- stale actor response/cancel/complete callbacks after correlation reuse and during drain;\n- authenticated contiguous reconnect replay and cross-journal/stream/session/epoch rejection;\n- bounded unknown disconnect outcomes, exact replay, archive reservation and old-epoch rejection;\n- aggregate verifier-receipt reservation, consumption, saturation, no-mutation failure and epoch recovery.\n",
    "test coverage",
)
readme = replace_once(
    readme,
    "Adapters must expose bounded cardinalities, generation mismatch rejection, revocation fanout, reconnect outcomes, capacity saturation, and invariant failures without high-cardinality labels. Readiness, drain behavior, durable revocation replay, and recovery must be specified before production use.\n",
    "Adapters must expose bounded cardinalities, generation mismatch rejection, revocation fanout, actor saturation, cursor rejection, disconnect reconciliation, receipt-budget saturation, reconnect outcomes, capacity saturation, and invariant failures without high-cardinality labels. Readiness, drain behavior, durable revocation replay, production verifier behavior, authenticated epoch rollover, and recovery must be specified before production use.\n",
    "operations contract",
)
readme = replace_once(
    readme,
    "This source candidate preserves deterministic in-process route semantics only. Durable revocation replay, live socket closure, distributed fanout, reconnect recovery, Nakama differential evidence, production capacity/endurance, and conflict-free specialist acceptance remain outside this module boundary. No local source or CI result may be transferred as proof of those external properties.\n",
    "This source candidate preserves deterministic in-process route and recovery semantics only. Durable revocation replay, live socket closure, distributed fanout, reconnect transport, durable receipt/checkpoint persistence, Nakama differential evidence, production capacity/endurance, and conflict-free specialist acceptance remain outside this module boundary. No local source or CI result may be transferred as proof of those external properties.\n",
    "compatibility boundary",
)
(ROOT / README_PATH).write_text(readme, encoding="utf-8")
