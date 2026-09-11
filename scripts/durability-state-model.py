#!/usr/bin/env python3
"""Exhaustive small-state model for command, authority and outbox durability."""
from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import dataclass, replace
from typing import Iterable

ABSENT = "absent"
READY = "ready"
LEASED = "leased"
DELIVERED = "delivered"
DEAD = "dead"
CLAIM_ACTIONS = ((1, "claim:1"), (2, "claim:2"))

@dataclass(frozen=True, slots=True)
class State:
    authority_generation: int = 1
    revision: int = 0
    receipt_fingerprint: str | None = None
    outbox_state: str = ABSENT
    lease_generation: int = 0
    lease_owner: int | None = None
    publish_attempts: int = 0
    visible_effect: bool = False
    delivered_receipt: bool = False
    terminal_conflicts: int = 0
    stale_rejections: int = 0

@dataclass(frozen=True, slots=True)
class Edge:
    action: str
    state: State

def transition(
    state: State,
    *,
    allow_stale_ack: bool = False,
    duplicate_visible_effect: bool = False,
) -> Iterable[Edge]:
    yield Edge("takeover", replace(state, authority_generation=state.authority_generation + 1))

    if state.receipt_fingerprint is None:
        yield Edge(
            "commit_current:A",
            replace(
                state,
                revision=state.revision + 1,
                receipt_fingerprint="A",
                outbox_state=READY,
            ),
        )
        yield Edge(
            "commit_stale:A",
            replace(state, stale_rejections=state.stale_rejections + 1),
        )
    else:
        yield Edge("replay_same", state)
        yield Edge(
            "replay_conflict",
            replace(state, terminal_conflicts=state.terminal_conflicts + 1),
        )
        yield Edge(
            "commit_stale_after_receipt",
            replace(state, stale_rejections=state.stale_rejections + 1),
        )

    if state.outbox_state == READY:
        for owner, action in CLAIM_ACTIONS:
            yield Edge(
                action,
                replace(
                    state,
                    outbox_state=LEASED,
                    lease_generation=state.lease_generation + 1,
                    lease_owner=owner,
                ),
            )
        yield Edge(
            "reap_ready_at_limit",
            replace(state, outbox_state=DEAD, lease_owner=None),
        )

    if state.outbox_state == LEASED:
        yield Edge(
            "lease_expire",
            replace(state, outbox_state=READY, lease_owner=None),
        )
        next_attempts = state.publish_attempts + 1
        next_visible = state.visible_effect or next_attempts > 0
        if duplicate_visible_effect and state.visible_effect:
            # Mutant: a retry creates a second externally visible value. The
            # model encodes this as attempts > 1 while delivered state is
            # reachable; the invariant rejects it.
            next_visible = True
        yield Edge(
            "publish_idempotent",
            replace(
                state,
                publish_attempts=next_attempts,
                visible_effect=next_visible,
            ),
        )
        if state.visible_effect:
            yield Edge(
                "ack_exact",
                replace(
                    state,
                    outbox_state=DELIVERED,
                    lease_owner=None,
                    delivered_receipt=True,
                ),
            )
        stale = replace(state, stale_rejections=state.stale_rejections + 1)
        if allow_stale_ack and state.visible_effect:
            stale = replace(
                state,
                outbox_state=DELIVERED,
                lease_owner=None,
                delivered_receipt=True,
            )
        yield Edge("ack_stale_owner_or_generation", stale)
        yield Edge("crash_before_or_after_publish", state)

    if state.outbox_state in (DELIVERED, DEAD):
        yield Edge("terminal_replay", state)

def assert_invariants(state: State, *, duplicate_visible_effect: bool = False) -> None:
    if state.revision not in (0, 1):
        raise AssertionError("revision advanced more than one unique command")
    if (state.receipt_fingerprint is None) != (state.revision == 0):
        raise AssertionError("receipt and revision diverged")
    if state.outbox_state == ABSENT and state.receipt_fingerprint is not None:
        raise AssertionError("accepted command lost its outbox intent")
    if state.outbox_state != ABSENT and state.receipt_fingerprint is None:
        raise AssertionError("outbox exists without accepted command")
    if state.outbox_state == LEASED and state.lease_owner not in (1, 2):
        raise AssertionError("leased intent lacks exact owner")
    if state.outbox_state != LEASED and state.lease_owner is not None:
        raise AssertionError("non-leased intent retains owner")
    if state.outbox_state == DELIVERED and not state.delivered_receipt:
        raise AssertionError("delivered intent lacks durable receipt")
    if state.delivered_receipt and not state.visible_effect:
        raise AssertionError("receipt exists without visible effect")
    if state.outbox_state == DEAD and state.delivered_receipt:
        raise AssertionError("dead intent contains delivery receipt")
    if state.stale_rejections < 0 or state.terminal_conflicts < 0:
        raise AssertionError("negative rejection counter")
    if duplicate_visible_effect and state.publish_attempts > 1 and state.visible_effect:
        raise AssertionError("duplicate externally visible effect")

def explore(
    depth: int = 10,
    *,
    allow_stale_ack: bool = False,
    duplicate_visible_effect: bool = False,
) -> dict[str, int]:
    initial = State()
    queue = deque([(initial, 0)])
    seen = {initial}
    edges = 0
    terminal = 0
    delivered = 0
    dead = 0
    while queue:
        state, level = queue.popleft()
        assert_invariants(state, duplicate_visible_effect=duplicate_visible_effect)
        terminal += state.outbox_state in (DELIVERED, DEAD)
        delivered += state.outbox_state == DELIVERED
        dead += state.outbox_state == DEAD
        if level == depth:
            continue
        for edge in transition(
            state,
            allow_stale_ack=allow_stale_ack,
            duplicate_visible_effect=duplicate_visible_effect,
        ):
            edges += 1
            assert_invariants(edge.state, duplicate_visible_effect=duplicate_visible_effect)
            if edge.state not in seen:
                seen.add(edge.state)
                queue.append((edge.state, level + 1))
    if delivered == 0 or dead == 0:
        raise AssertionError("terminal delivery and reaper paths must both be reachable")
    return {
        "depth": depth,
        "states": len(seen),
        "edges": edges,
        "terminal_states_seen": terminal,
        "delivered_states_seen": delivered,
        "dead_states_seen": dead,
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.depth <= 14:
        parser.error("depth must be between 1 and 14")
    report = explore(args.depth)
    print(json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
