#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "crates/trnm-presence-router-v2/src/lib.rs"
README = ROOT / "crates/trnm-presence-router-v2/README.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


lib = LIB.read_text(encoding="utf-8")
for marker in (
    "mod connection_actor;",
    "mod disconnect_journal;",
    "mod reconnect_cursor;",
    "mod router;",
    "SessionRouteCheckpoint",
    "SessionRouteNamespace",
    "SessionRouteRolloverProof",
    "DisconnectJournal",
    "DISCONNECT_VERIFIER_RECEIPTS_PER_ATTEMPT",
    "MAX_DISCONNECT_VERIFIER_RECEIPTS",
    "ReconnectJournal",
    "RequestHandle",
):
    if marker not in lib:
        raise SystemExit(f"auto-merged lib omits required marker: {marker}")
if any(marker in lib for marker in ("<<<<<<<", "=======", ">>>>>>>")):
    raise SystemExit("auto-merged lib retains conflict markers")

readme = subprocess.check_output(
    ["git", "show", ":3:crates/trnm-presence-router-v2/README.md"], cwd=ROOT
).decode("utf-8")
readme = replace_once(
    readme,
    "This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to deterministic in-process presence, connection ownership, session-generation fencing, and bounded revocation fanout. Source presence or a passing unit suite does not establish distributed delivery, durable revocation, Nakama compatibility, or production acceptance.\n",
    "This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to deterministic in-process presence, connection ownership, session-generation fencing, bounded revocation fanout, connection-actor admission, authenticated reconnect cursors and receipt-reconciled disconnect effects. Source presence or a passing unit suite does not establish distributed delivery, durable revocation, Nakama compatibility, or production acceptance.\n",
    "authority boundary",
)
readme = replace_once(
    readme,
    "- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;\n- fail-closed invariant checking before and after staged mutations.\n",
    "- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;\n- bounded actor queues, frame bytes, pending requests and aggregate payload memory;\n- generation-bound asynchronous request handles and authenticated reconnect cursors;\n- receipt-reconciled disconnect effects with archive and epoch-wide verifier-receipt reservations;\n- fail-closed invariant checking before and after staged mutations.\n",
    "responsibility expansion",
)
recovery = '''### Bounded recovery layers

`ConnectionActor` independently bounds frame admission, queued egress and pending request state. Every asynchronous response, cancellation and completion requires the exact live `RequestHandle`; numeric correlation reuse cannot let a delayed callback mutate a later admission. Drain preserves only the finite request set admitted before its fence.

`ReconnectJournal` authenticates each cursor and requires one exact journal, stream, session-generation and producer-epoch identity. Replay is contiguous and bounded; expired, ahead, tampered, wrong-key and retired-generation cursors fail closed.

`DisconnectJournal` binds every possible transport write to one immutable dispatch identity. After delivery becomes ambiguous, retry is forbidden until a trusted verifier accepts a typed exact outcome. One `Unknown` result may be retained and replayed exactly; a distinct second unknown fails without mutation. Admission reserves archive capacity and the worst-case verifier-receipt allowance. Each accepted receipt consumes one reservation before owner-map mutation, terminal archive releases unused reservation, and epoch advance clears the finite namespace only after all active records have left.

`DisconnectJournalConfig` rejects zero, excessive and arithmetically unsafe limits. Construction checks the complete `tombstone_capacity × max_attempts × receipts_per_attempt` product against the repository hard cap. Production reconnect authenticators and outcome verifiers must authenticate the complete typed identity; a caller boolean or nonzero digest is never proof.

'''
readme = replace_once(
    readme,
    "## Public contracts\n",
    recovery + "## Public contracts\n",
    "bounded recovery section",
)
readme = replace_once(
    readme,
    "- invalid, incoherent or unverified limit/rollover configuration.\n",
    "- invalid, incoherent or unverified limit/rollover configuration;\n- stale actor callbacks after correlation reuse and during drain;\n- authenticated reconnect replay and cross-identity cursor rejection;\n- bounded unknown outcomes, archive reservation, receipt-budget saturation and epoch recovery.\n",
    "test coverage",
)
readme = replace_once(
    readme,
    "This source candidate preserves deterministic in-process route semantics only. Durable revocation replay, live socket closure, distributed fanout, reconnect recovery, Nakama differential evidence, production capacity/endurance, and conflict-free specialist acceptance remain outside this module boundary. No local source or CI result may be transferred as proof of those external properties.\n",
    "This source candidate preserves deterministic in-process route and recovery semantics only. Durable revocation replay, live socket closure, distributed fanout, reconnect transport, durable receipt/checkpoint persistence, Nakama differential evidence, production capacity/endurance, and conflict-free specialist acceptance remain outside this module boundary. No local source or CI result may be transferred as proof of those external properties.\n",
    "compatibility boundary",
)
if any(marker in readme for marker in ("<<<<<<<", "=======", ">>>>>>>")):
    raise SystemExit("merged README retains conflict markers")
README.write_text(readme, encoding="utf-8")
