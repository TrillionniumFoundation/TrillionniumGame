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
MAX_ATTEMPTS = 2
CLAIM_ACTIONS = ((1, "claim:1"), (2, "claim:2"))


@dataclass(frozen=True, slots=True)
class State:
    authority_generation: int = 1
    revision: int = 0
    receipt_fingerprint: str | None = None
    outbox_state: str = ABSENT
    attempt: int = 0
    lease_generation: int = 0
    lease_owner: int | None = None
    last_lease_owner: int | None = None
    lease_expired: bool = False
    publish_calls: int = 0
    visible_effect_count: int = 0
    delivered_receipt: bool = False
    delivery_ack_owner: int | None = None
    delivery_ack_generation: int | None = None
    terminal_conflicts: int = 0
    stale_rejections: int = 0


@dataclass(frozen=True, slots=True)
class Edge:
    action: str
    state: State


def claim_edges(state: State) -> Iterable[Edge]:
    if state.attempt >= MAX_ATTEMPTS:
        return
    for owner, action in CLAIM_ACTIONS:
        yield Edge(
            action,
            replace(
                state,
                outbox_state=LEASED,
                attempt=state.attempt + 1,
                lease_generation=state.lease_generation + 1,
                lease_owner=owner,
                last_lease_owner=owner,
                lease_expired=False,
            ),
        )


def transition(
    state: State,
    *,
    allow_stale_ack: bool = False,
    duplicate_visible_effect: bool = False,
) -> Iterable[Edge]:
    yield Edge(
        "takeover",
        replace(state, authority_generation=state.authority_generation + 1),
    )

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
        yield from claim_edges(state)

    if state.outbox_state == LEASED:
        next_effect_count = state.visible_effect_count
        if next_effect_count == 0:
            next_effect_count = 1
        elif duplicate_visible_effect:
            # Mutant: a retry creates another externally visible value instead
            # of observing the existing stable intent identity.
            next_effect_count += 1
        yield Edge(
            "publish_idempotent",
            replace(
                state,
                publish_calls=state.publish_calls + 1,
                visible_effect_count=next_effect_count,
            ),
        )

        if state.visible_effect_count == 1:
            yield Edge(
                "ack_exact",
                replace(
                    state,
                    outbox_state=DELIVERED,
                    lease_owner=None,
                    lease_expired=False,
                    delivered_receipt=True,
                    delivery_ack_owner=state.lease_owner,
                    delivery_ack_generation=state.lease_generation,
                ),
            )

        stale = replace(state, stale_rejections=state.stale_rejections + 1)
        if allow_stale_ack and state.visible_effect_count == 1:
            # Mutant: an acknowledgement from a different owner and an older
            # generation incorrectly completes the current lease.
            wrong_owner = 2 if state.lease_owner == 1 else 1
            stale = replace(
                state,
                outbox_state=DELIVERED,
                lease_owner=None,
                lease_expired=False,
                delivered_receipt=True,
                delivery_ack_owner=wrong_owner,
                delivery_ack_generation=max(0, state.lease_generation - 1),
            )
        yield Edge("ack_stale_owner_or_generation", stale)
        yield Edge("crash_before_or_after_publish", state)

        if not state.lease_expired:
            yield Edge("lease_expire", replace(state, lease_expired=True))
        elif state.attempt < MAX_ATTEMPTS:
            # The durable row remains leased until a new owner atomically
            # replaces the expired owner/generation.
            yield from claim_edges(state)
        else:
            # This mirrors the current bounded source candidate: an expired
            # final lease becomes dead even when an idempotent external value
            # was already made visible before the crash. Such a state is not
            # promoted to delivered and remains an explicit ambiguity boundary.
            yield Edge(
                "reap_expired_at_limit",
                replace(
                    state,
                    outbox_state=DEAD,
                    lease_owner=None,
                    lease_expired=False,
                    delivered_receipt=False,
                    delivery_ack_owner=None,
                    delivery_ack_generation=None,
                ),
            )

    if state.outbox_state in (DELIVERED, DEAD):
        yield Edge("terminal_replay", state)


def assert_invariants(state: State) -> None:
    if state.revision not in (0, 1):
        raise AssertionError("revision advanced more than one unique command")
    if (state.receipt_fingerprint is None) != (state.revision == 0):
        raise AssertionError("receipt and revision diverged")
    if state.outbox_state == ABSENT and state.receipt_fingerprint is not None:
        raise AssertionError("accepted command lost its outbox intent")
    if state.outbox_state != ABSENT and state.receipt_fingerprint is None:
        raise AssertionError("outbox exists without accepted command")

    if state.visible_effect_count not in (0, 1):
        raise AssertionError("duplicate externally visible effect")
    if state.publish_calls < state.visible_effect_count:
        raise AssertionError("visible effect lacks publish attempt")

    if state.attempt == 0:
        if state.lease_generation != 0 or state.last_lease_owner is not None:
            raise AssertionError("unclaimed intent retains lease history")
    elif state.lease_generation == 0 or state.last_lease_owner not in (1, 2):
        raise AssertionError("claimed intent lacks lease history")

    if state.outbox_state == LEASED:
        if (
            state.lease_owner not in (1, 2)
            or state.lease_owner != state.last_lease_owner
        ):
            raise AssertionError("leased intent lacks exact owner")
        if state.attempt == 0 or state.attempt > MAX_ATTEMPTS:
            raise AssertionError("leased intent has invalid attempt")
    elif state.lease_owner is not None:
        raise AssertionError("non-leased intent retains owner")

    if state.lease_expired and state.outbox_state != LEASED:
        raise AssertionError("only a leased intent may be expired")

    if state.outbox_state == DELIVERED:
        if not state.delivered_receipt:
            raise AssertionError("delivered intent lacks durable receipt")
        if state.visible_effect_count != 1:
            raise AssertionError(
                "delivery receipt exists without exactly one visible effect"
            )
        if (
            state.delivery_ack_owner != state.last_lease_owner
            or state.delivery_ack_generation != state.lease_generation
        ):
            raise AssertionError("stale delivery acknowledgement")
    elif (
        state.delivered_receipt
        or state.delivery_ack_owner is not None
        or state.delivery_ack_generation is not None
    ):
        raise AssertionError("non-delivered intent retains delivery acknowledgement")

    if state.outbox_state == DEAD and state.delivered_receipt:
        raise AssertionError("dead intent contains delivery receipt")
    if state.stale_rejections < 0 or state.terminal_conflicts < 0:
        raise AssertionError("negative rejection counter")


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
    dead_with_visible_effect = 0
    idempotent_retry_states = 0

    while queue:
        state, level = queue.popleft()
        assert_invariants(state)
        terminal += state.outbox_state in (DELIVERED, DEAD)
        delivered += state.outbox_state == DELIVERED
        dead += state.outbox_state == DEAD
        dead_with_visible_effect += (
            state.outbox_state == DEAD and state.visible_effect_count == 1
        )
        idempotent_retry_states += (
            state.publish_calls > 1 and state.visible_effect_count == 1
        )
        if level == depth:
            continue
        for edge in transition(
            state,
            allow_stale_ack=allow_stale_ack,
            duplicate_visible_effect=duplicate_visible_effect,
        ):
            edges += 1
            assert_invariants(edge.state)
            if edge.state not in seen:
                seen.add(edge.state)
                queue.append((edge.state, level + 1))

    if delivered == 0 or dead == 0:
        raise AssertionError("terminal delivery and reaper paths must both be reachable")
    if dead_with_visible_effect == 0:
        raise AssertionError("crash-after-publish ambiguity path must be reachable")
    if idempotent_retry_states == 0:
        raise AssertionError("idempotent publish retry path must be reachable")

    return {
        "depth": depth,
        "states": len(seen),
        "edges": edges,
        "terminal_states_seen": terminal,
        "delivered_states_seen": delivered,
        "dead_states_seen": dead,
        "dead_with_visible_effect_states_seen": dead_with_visible_effect,
        "idempotent_retry_states_seen": idempotent_retry_states,
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
