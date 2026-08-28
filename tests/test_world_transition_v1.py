from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime.world_transition_v1.adapter import (
    _accepted_facts,
    _outcome_hash,
    prepare_world_transition,
    prepared_from_canonical_request,
    verify_world_result,
)
from runtime.world_transition_v1.canonical import (
    CanonicalJsonError,
    canonical_dumps,
    loads_canonical,
)
from runtime.world_transition_v1.contracts import (
    CONTRACT_VERSION,
    STABLE_ERROR_CODES,
    TRANSITION_HASH_DOMAIN,
    CanonicalPayload,
    NakamaAuthorityContext,
    TransitionContractError,
    domain_hash,
)
from runtime.world_transition_v1.shadow import (
    ShadowObservation,
    compare_jsonl,
    compare_observations,
)


ROOT = Path(__file__).resolve().parents[1]
WORLD_REVISION = "0d7666d4d830fa8e56c78b23d438856064182535"


def context(**overrides: object) -> NakamaAuthorityContext:
    values: dict[str, object] = {
        "match_id": "match-0001",
        "authorization_id": "authorization-0001",
        "participant_roster_hash": "3" * 64,
        "match_version": 7,
        "global_event_sequence": 9001,
        "command_idempotency_key": "idempotency-0001",
        "ruleset_revision": "trnm-rts-rules-v1",
        "content_revision": "first-contact-content-v1",
        "expected_tick": 120,
    }
    values.update(overrides)
    return NakamaAuthorityContext(**values)  # type: ignore[arg-type]


def prepared(**context_overrides: object):
    return prepare_world_transition(
        context(**context_overrides),
        previous_state_schema_id="trnm.rts.state.v1",
        previous_state={"tick": 120, "units": [{"hp": 10, "id": "alpha"}]},
        command_schema_id="trnm.rts.order.v1",
        command={"kind": "hold", "unit_id": "alpha"},
    )


def accepted_result(prepared_transition, *, with_outcome: bool = True) -> str:
    next_state = CanonicalPayload.from_value(
        {"tick": 121, "units": [{"hp": 10, "id": "alpha"}]},
        schema_id="trnm.rts.state.v1",
        maximum_bytes=2 * 1024 * 1024,
        label="next_state",
    )
    replay = CanonicalPayload.from_value(
        {"applied_command_ids": [prepared_transition.command_id], "tick": 121},
        schema_id="trnm.rts.replay.v1",
        maximum_bytes=2 * 1024 * 1024,
        label="replay_material",
    )
    outcome = (
        CanonicalPayload.from_value(
            {"result": "held", "score": 10},
            schema_id="trnm.rts.outcome.v1",
            maximum_bytes=512 * 1024,
            label="outcome_material",
        )
        if with_outcome
        else None
    )
    result = {
        "content_revision": prepared_transition.context.content_revision,
        "contract_version": CONTRACT_VERSION,
        "next_state": next_state.to_wire(),
        "next_tick": 121,
        "outcome_material": outcome.to_wire() if outcome else None,
        "previous_state_hash": prepared_transition.previous_state_hash,
        "replay_material": replay.to_wire(),
        "request_hash": prepared_transition.request_hash,
        "ruleset_revision": prepared_transition.context.ruleset_revision,
        "transition_id": prepared_transition.transition_id,
        "world_outcome_hash": (
            _outcome_hash(
                prepared_transition.context.ruleset_revision,
                prepared_transition.context.content_revision,
                outcome,
            )
            if outcome
            else None
        ),
        "world_transition_hash": "",
    }
    result["world_transition_hash"] = domain_hash(
        TRANSITION_HASH_DOMAIN, canonical_dumps(_accepted_facts(result))
    )
    return canonical_dumps(result)


def rejected_result(
    prepared_transition,
    *,
    code: str = "domain_rejected",
    retryable: bool = False,
) -> str:
    return canonical_dumps(
        {
            "code": code,
            "contract_version": CONTRACT_VERSION,
            "detail": "deterministic rules rejected the command",
            "request_hash": prepared_transition.request_hash,
            "retryable": retryable,
            "transition_id": prepared_transition.transition_id,
        }
    )


class CanonicalJsonTests(unittest.TestCase):
    def test_exact_canonical_profile(self) -> None:
        value = {"a": [1, True, None], "b": {"c": "text"}}
        encoded = canonical_dumps(value)
        self.assertEqual(
            encoded, '{"a":[1,true,null],"b":{"c":"text"}}'
        )
        self.assertEqual(loads_canonical(encoded), value)

    def test_alternate_json_representations_fail_closed(self) -> None:
        invalid = [
            '{"b":1,"a":2}',
            '{"a": 1}',
            '{"a":1,"a":2}',
            '{"a":1.0}',
            '{"a":-0}',
            '{"a":"\\u0061"}',
            '{"a":9223372036854775808}',
            '1',
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(CanonicalJsonError):
                loads_canonical(raw, root_container=True)


class AdapterTests(unittest.TestCase):
    def test_preparation_is_stable_and_hides_authority_context(self) -> None:
        first = prepared()
        second = prepared()
        self.assertEqual(first.canonical_request, second.canonical_request)
        self.assertEqual(first.request_hash, second.request_hash)
        self.assertNotIn("match_id", first.request)
        self.assertNotIn("global_event_sequence", first.request)
        self.assertNotIn("participant_roster_hash", first.request)
        changed = prepared(global_event_sequence=9002)
        self.assertNotEqual(first.transition_id, changed.transition_id)
        self.assertNotEqual(first.command_id, changed.command_id)

    def test_reconstructs_persisted_request_from_exact_context(self) -> None:
        original = prepared()
        replayed = prepared_from_canonical_request(
            original.context, original.canonical_request
        )
        self.assertEqual(replayed.request_hash, original.request_hash)
        self.assertEqual(replayed.transition_id, original.transition_id)
        with self.assertRaises(TransitionContractError):
            prepared_from_canonical_request(
                context(global_event_sequence=9002),
                original.canonical_request,
            )

    def test_verifies_accepted_world_material_without_changing_authority(self) -> None:
        item = prepared()
        verified = verify_world_result(item, accepted_result(item))
        self.assertEqual(verified.disposition, "accepted")
        self.assertEqual(
            verified.context.global_event_sequence,
            item.context.global_event_sequence,
        )
        self.assertEqual(
            verified.context.command_idempotency_key,
            item.context.command_idempotency_key,
        )
        self.assertIsNotNone(verified.world_transition_hash)
        self.assertIsNotNone(verified.world_outcome_hash)

    def test_tampering_and_authority_smuggling_fail_closed(self) -> None:
        item = prepared()
        raw = accepted_result(item)
        value = json.loads(raw)
        value["next_state"]["canonical_json"]["tick"] = 122
        with self.assertRaisesRegex(
            TransitionContractError, "payload hash mismatch"
        ):
            verify_world_result(item, canonical_dumps(value))

        value = json.loads(raw)
        value["world_transition_hash"] = "0" * 64
        with self.assertRaisesRegex(
            TransitionContractError, "transition hash mismatch"
        ):
            verify_world_result(item, canonical_dumps(value))

        value = json.loads(raw)
        value["next_state"]["canonical_json"]["global_event_cursor"] = 12
        value["next_state"]["sha256"] = hashlib.sha256(
            canonical_dumps(
                value["next_state"]["canonical_json"]
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(
            TransitionContractError, "forbidden authority"
        ):
            verify_world_result(item, canonical_dumps(value))

    def test_rejection_uses_stable_code_and_retry_policy(self) -> None:
        item = prepared()
        rejected = verify_world_result(item, rejected_result(item))
        self.assertEqual(rejected.disposition, "rejected")
        self.assertEqual(rejected.error_code, "domain_rejected")
        self.assertFalse(rejected.retryable)
        with self.assertRaisesRegex(
            TransitionContractError, "retryable disagrees"
        ):
            verify_world_result(
                item, rejected_result(item, retryable=True)
            )
        transient = verify_world_result(
            item,
            rejected_result(
                item, code="internal_unavailable", retryable=True
            ),
        )
        self.assertTrue(transient.retryable)


class ShadowTests(unittest.TestCase):
    def observation(
        self,
        fixture_id: str = "fixture-0001",
        implementation_id: str = "world-reference",
        revision: str = WORLD_REVISION,
    ) -> ShadowObservation:
        item = prepared()
        verified = verify_world_result(item, accepted_result(item))
        return ShadowObservation.from_verified(
            verified,
            fixture_id=fixture_id,
            implementation_id=implementation_id,
            implementation_revision=revision,
            duration_micros=100,
        )

    def test_exact_observations_match_but_never_authorize_cutover(self) -> None:
        world = self.observation()
        candidate = self.observation(
            implementation_id="nakama-adapter",
            revision="1" * 40,
        )
        report = compare_observations(world, candidate)
        self.assertEqual(report["status"], "matched")
        self.assertTrue(
            report["promotion_eligible_for_integration_review"]
        )
        self.assertFalse(report["cutover_authorized"])
        self.assertFalse(
            report["canonical_completion_signing_performed"]
        )

    def test_hash_divergence_is_typed_and_blocks_promotion(self) -> None:
        world = self.observation()
        candidate_wire = self.observation(
            implementation_id="nakama-adapter",
            revision="1" * 40,
        ).to_wire()
        candidate_wire["next_state_hash"] = "0" * 64
        candidate = ShadowObservation.from_wire(candidate_wire)
        report = compare_observations(world, candidate)
        self.assertEqual(report["status"], "diverged")
        self.assertFalse(
            report["promotion_eligible_for_integration_review"]
        )
        self.assertEqual(
            report["divergences"][0]["code"], "state_hash_mismatch"
        )

    def test_jsonl_missing_fixture_fails_closed(self) -> None:
        world = self.observation()
        candidate = self.observation(
            implementation_id="nakama-adapter",
            revision="1" * 40,
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            world_path = directory / "world.jsonl"
            candidate_path = directory / "candidate.jsonl"
            world_path.write_text(
                canonical_dumps(world.to_wire()) + "\n", encoding="utf-8"
            )
            candidate_path.write_text("", encoding="utf-8")
            summary = compare_jsonl(world_path, candidate_path)
            self.assertEqual(summary["status"], "diverged")
            self.assertEqual(
                summary["reports"][0]["divergences"][0]["code"],
                "missing_candidate_fixture",
            )


class VendoredWorldContractTests(unittest.TestCase):
    def test_world_vectors_are_reproduced_independently(self) -> None:
        vectors = json.loads(
            (
                ROOT / "testdata/world-transition-v1/golden-vectors.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            vectors["schema_contract_version"], CONTRACT_VERSION
        )
        for vector in vectors["sha256_core_vectors"]:
            self.assertEqual(
                hashlib.sha256(
                    vector["input_utf8"].encode("utf-8")
                ).hexdigest(),
                vector["expected_sha256"],
            )
        for vector in vectors["payload_vectors"]:
            canonical = canonical_dumps(vector["canonical_json"])
            self.assertEqual(canonical, vector["expected_canonical_utf8"])
            self.assertEqual(
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                vector["expected_sha256"],
            )
        for vector in vectors["request_vectors"]:
            self.assertEqual(
                canonical_dumps(vector["request"]),
                vector["expected_canonical_json"],
            )
        for vector in vectors["accepted_facts_vectors"]:
            self.assertEqual(
                canonical_dumps(vector["accepted_facts"]),
                vector["expected_canonical_json"],
            )
        self.assertEqual(
            frozenset(vectors["stable_error_codes"]),
            STABLE_ERROR_CODES,
        )

    def test_consumer_lock_is_exact_and_non_creditable(self) -> None:
        lock = json.loads(
            (
                ROOT / "contracts/world-transition-v1-consumer-lock.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(lock["status"], "shadow_candidate")
        self.assertEqual(
            lock["world"]["commit"],
            WORLD_REVISION,
        )
        self.assertEqual(
            lock["world"]["tree"],
            "1619ae76fa62a5e67bc7ff94429c62eea35deb87",
        )
        self.assertFalse(lock["authority"]["completion_signing_performed"])
        self.assertFalse(lock["authority"]["public_online_enabled"])
        self.assertFalse(lock["cross_repository_credit"])


if __name__ == "__main__":
    unittest.main()
