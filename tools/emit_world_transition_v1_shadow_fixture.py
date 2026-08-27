#!/usr/bin/env python3
"""Emit exact-head synthetic shadow fixtures for the Nakama World adapter.

These records prove contract wiring and fail-closed comparison only. They do not
replace a production World/Nakama dual-observation corpus.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.world_transition_v1.adapter import (
    _accepted_facts,
    _outcome_hash,
    prepare_world_transition,
    verify_world_result,
)
from runtime.world_transition_v1.canonical import canonical_dumps
from runtime.world_transition_v1.contracts import (
    CONTRACT_VERSION,
    MAX_OUTCOME_BYTES,
    MAX_REPLAY_BYTES,
    MAX_STATE_BYTES,
    TRANSITION_HASH_DOMAIN,
    CanonicalPayload,
    NakamaAuthorityContext,
    domain_hash,
    sha256_hex,
)
from runtime.world_transition_v1.shadow import (
    ShadowObservation,
    compare_jsonl,
)

EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")
LOCK_PATH = ROOT / "contracts/world-transition-v1-consumer-lock.json"


def context(suffix: str, sequence: int) -> NakamaAuthorityContext:
    return NakamaAuthorityContext(
        match_id=f"match-{suffix}",
        authorization_id=f"authorization-{suffix}",
        participant_roster_hash=("3" if suffix == "accepted" else "4") * 64,
        match_version=1,
        global_event_sequence=sequence,
        command_idempotency_key=f"idempotency-{suffix}",
        ruleset_revision="trnm-rts-rules-v1",
        content_revision="first-contact-content-v1",
        expected_tick=120,
    )


def accepted_result(prepared) -> str:
    next_state = CanonicalPayload.from_value(
        {"tick": 121, "units": [{"hp": 10, "id": "alpha"}]},
        schema_id="trnm.rts.state.v1",
        maximum_bytes=MAX_STATE_BYTES,
        label="next_state",
    )
    replay = CanonicalPayload.from_value(
        {"applied_command_ids": [prepared.command_id], "tick": 121},
        schema_id="trnm.rts.replay.v1",
        maximum_bytes=MAX_REPLAY_BYTES,
        label="replay_material",
    )
    outcome = CanonicalPayload.from_value(
        {"result": "held", "score": 10},
        schema_id="trnm.rts.outcome.v1",
        maximum_bytes=MAX_OUTCOME_BYTES,
        label="outcome_material",
    )
    result = {
        "content_revision": prepared.context.content_revision,
        "contract_version": CONTRACT_VERSION,
        "next_state": next_state.to_wire(),
        "next_tick": 121,
        "outcome_material": outcome.to_wire(),
        "previous_state_hash": prepared.previous_state_hash,
        "replay_material": replay.to_wire(),
        "request_hash": prepared.request_hash,
        "ruleset_revision": prepared.context.ruleset_revision,
        "transition_id": prepared.transition_id,
        "world_outcome_hash": _outcome_hash(
            prepared.context.ruleset_revision,
            prepared.context.content_revision,
            outcome,
        ),
        "world_transition_hash": "",
    }
    result["world_transition_hash"] = domain_hash(
        TRANSITION_HASH_DOMAIN, canonical_dumps(_accepted_facts(result))
    )
    return canonical_dumps(result)


def rejected_result(prepared) -> str:
    return canonical_dumps(
        {
            "code": "domain_rejected",
            "contract_version": CONTRACT_VERSION,
            "detail": "synthetic fixture rejection",
            "request_hash": prepared.request_hash,
            "retryable": False,
            "transition_id": prepared.transition_id,
        }
    )


def write_json(path: Path, value) -> None:
    path.write_text(canonical_dumps(value) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nakama-revision", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if EXACT_SHA.fullmatch(args.nakama_revision) is None:
        raise SystemExit("nakama revision must be exact lowercase 40-hex")

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    world_revision = lock["world"]["commit"]
    if EXACT_SHA.fullmatch(world_revision) is None:
        raise SystemExit("World lock revision is not exact")

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    world_lines: list[str] = []
    candidate_lines: list[str] = []
    fixture_manifest: list[dict[str, str]] = []

    fixtures = [
        ("accepted", context("accepted", 9001), accepted_result),
        ("rejected", context("rejected", 9002), rejected_result),
    ]
    for suffix, authority_context, result_builder in fixtures:
        prepared = prepare_world_transition(
            authority_context,
            previous_state_schema_id="trnm.rts.state.v1",
            previous_state={
                "tick": 120,
                "units": [{"hp": 10, "id": "alpha"}],
            },
            command_schema_id="trnm.rts.order.v1",
            command={"kind": "hold", "unit_id": "alpha"},
        )
        raw_result = result_builder(prepared)
        verified = verify_world_result(prepared, raw_result)
        fixture_id = f"synthetic-{suffix}-0001"
        world = ShadowObservation.from_verified(
            verified,
            fixture_id=fixture_id,
            implementation_id="world-transition-v1-reference",
            implementation_revision=world_revision,
            duration_micros=0,
        )
        candidate = ShadowObservation.from_verified(
            verified,
            fixture_id=fixture_id,
            implementation_id="nakama-world-transition-v1-adapter",
            implementation_revision=args.nakama_revision,
            duration_micros=0,
        )
        world_lines.append(canonical_dumps(world.to_wire()))
        candidate_lines.append(canonical_dumps(candidate.to_wire()))

        request_path = output / f"{fixture_id}.request.json"
        result_path = output / f"{fixture_id}.result.json"
        context_path = output / f"{fixture_id}.context.json"
        request_path.write_text(prepared.canonical_request + "\n", encoding="utf-8")
        result_path.write_text(raw_result + "\n", encoding="utf-8")
        write_json(
            context_path,
            {
                "authorization_id": authority_context.authorization_id,
                "command_idempotency_key": authority_context.command_idempotency_key,
                "content_revision": authority_context.content_revision,
                "expected_tick": authority_context.expected_tick,
                "global_event_sequence": authority_context.global_event_sequence,
                "match_id": authority_context.match_id,
                "match_version": authority_context.match_version,
                "participant_roster_hash": authority_context.participant_roster_hash,
                "ruleset_revision": authority_context.ruleset_revision,
            },
        )
        fixture_manifest.append(
            {
                "context_sha256": sha256_hex(context_path.read_bytes()),
                "fixture_id": fixture_id,
                "request_sha256": sha256_hex(request_path.read_bytes()),
                "result_sha256": sha256_hex(result_path.read_bytes()),
            }
        )

    world_path = output / "world-observations.jsonl"
    candidate_path = output / "nakama-observations.jsonl"
    world_path.write_text("\n".join(world_lines) + "\n", encoding="utf-8")
    candidate_path.write_text(
        "\n".join(candidate_lines) + "\n", encoding="utf-8"
    )
    summary = compare_jsonl(world_path, candidate_path)
    write_json(output / "shadow-summary.json", summary)
    report = {
        "authority": {
            "canonical_archive_root_produced": False,
            "completion_signing_performed": False,
            "global_ordering_owned_by_world": False,
            "public_online_enabled": False,
        },
        "contract_version": "trnm_nakama_world_transition_fixture_report_v1",
        "fixture_kind": "synthetic_contract_fixture",
        "fixtures": fixture_manifest,
        "limitations": [
            "The World and Nakama observations are generated from committed contract fixtures, not production runtime traffic.",
            "A matched report is eligible only for Integration review; it does not authorize cutover.",
            "Production runtime language, restart recovery, canonical roots and completion signing remain separate Nakama work.",
        ],
        "nakama_revision": args.nakama_revision,
        "shadow_status": summary["status"],
        "world_revision": world_revision,
    }
    write_json(output / "fixture-report.json", report)
    print(canonical_dumps(report))
    return 0 if summary["status"] == "matched" else 2


if __name__ == "__main__":
    raise SystemExit(main())
