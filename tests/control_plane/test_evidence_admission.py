"""Retained evidence admission regressions; fixtures grant no real acceptance."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("test cannot load source module")
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


ADMISSION = module("trnm_evidence_admission_tests", ROOT / "scripts/evidence_admission.py")
POLICY = {
    "exact_target_commit_and_tree_required": True,
    "artifact_sha256_required": True,
    "independent_review_required": True,
    "self_approval_allowed": False,
    "expired_evidence_counts": False,
    "relay_evidence_requires_target_identity_validation": True,
    "logs_or_screenshots_without_archived_digest_count": False,
    "empty_or_skipped_execution_counts": False,
}


class RetainedFixture:
    def __init__(self, root: Path):
        self.root = root
        self.raw = b"nonempty deterministic test assertion output\n"
        self.artifact = {
            "name": "unit-output", "path": "retained/output.txt", "media_type": "text/plain",
            "sha256": ADMISSION.hashlib.sha256(self.raw).hexdigest(), "size_bytes": len(self.raw),
        }
        self.review = {
            "decision": "accepted", "reviewer_identity": "fixture-independent-reviewer",
            "reviewer_role": "compatibility-qa", "independent": True, "self_review": False,
            "reviewed_at": "2026-09-04T23:00:00Z", "reviewed_commit": "a" * 40,
            "reviewed_tree": "b" * 40,
        }
        target = {"repository": ADMISSION.REPOSITORY, "commit": "a" * 40, "tree": "b" * 40}
        self.entry = {
            "evidence_id": "TG-EV-FIXTURE-RETAINED-001", "evidence_type": "manifest",
            "status": "accepted", "compatibility_credit": True, "schema_valid": True,
            "target_identity_verified_by_current_repo": True, "target": target,
            "independent_review": self.review, "expires_at": "2026-09-06T00:00:00Z",
            "path": "docs/evidence/fixture.json", "artifacts": [self.artifact],
            "claim_ids": ["C0"], "gate_ids": ["GATE-FIXTURE"],
            "task_ids": ["TG-W0-001"], "gap_ids": ["GAP-P0-FIXTURE-001"],
            "parity_ids": [],
        }
        self.manifest = {
            "schema": "trillionnium.evidence.v1", "evidence_id": self.entry["evidence_id"],
            "evidence_type": "manifest", "status": "passed", "generated_by_automation": True,
            "candidate": {**target, "artifact_sha256": self.artifact["sha256"]},
            "upstream": {"repository": "heroiclabs/nakama", "commit": "c" * 40,
                         "tree": "d" * 40, "artifact_sha256": "e" * 64},
            "environment": {"environment_id": "synthetic-test-only", "os": "fixture-os",
                            "arch": "fixture-arch", "database": "none", "toolchain": ["fixture-python"],
                            "timezone": "UTC", "locale": "C", "configuration_sha256": "f" * 64},
            "fixtures": [], "commands": ["fixture command; not actual release evidence"],
            "started_at": "2026-09-04T20:00:00Z", "completed_at": "2026-09-04T21:00:00Z",
            "result": {"summary": "fixture", "assertions_total": 3, "assertions_passed": 3, "divergences": []},
            "artifacts": [self.artifact], "limitations": ["Synthetic fixture, not real acceptance."],
            "expires_at": self.entry["expires_at"], "review": self.review,
            **{key: self.entry[key] for key in ("claim_ids", "gate_ids", "task_ids", "gap_ids", "parity_ids")},
        }
        self.entry = copy.deepcopy(self.entry)
        self.manifest = copy.deepcopy(self.manifest)
        path = root / self.artifact["path"]
        path.parent.mkdir(parents=True)
        path.write_bytes(self.raw)
        schema = root / "docs/evidence/schemas/trillionnium-evidence-v1.schema.json"
        schema.parent.mkdir(parents=True)
        schema.write_bytes((ROOT / "docs/evidence/schemas/trillionnium-evidence-v1.schema.json").read_bytes())
        self.write()

    def write(self, manifest=None):
        (self.root / self.entry["path"]).write_text(json.dumps(self.manifest if manifest is None else manifest), encoding="utf-8")

    def index(self, row=None):
        return {"schema": "trillionnium.evidence-index.v1", "project_id": "trillionnium-game",
                "policy": POLICY.copy(), "entries": [self.entry if row is None else row], "accepted_entry_count": 1}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = RetainedFixture(Path(self.temp.name))
        self.root = self.fixture.root
        ADMISSION.validate_entry(self.fixture.entry, root=self.root, now=NOW)

    def reject(self, change, *, manifest=False):
        row = copy.deepcopy(self.fixture.entry)
        payload = copy.deepcopy(self.fixture.manifest)
        change(payload if manifest else row)
        self.fixture.write(payload)
        self.assertFalse(ADMISSION.entry_eligible(row, root=self.root, now=NOW))

    def test_valid_retained_entry_passes(self):
        self.assertTrue(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_manifest_is_not_a_no_credit_exception(self):
        for value in (False, None, 1, "true"):
            with self.subTest(value=value):
                self.reject(lambda r: r.update(compatibility_credit=value))

    def test_missing_required_entry_metadata_rejects(self):
        for key in ("status", "compatibility_credit", "schema_valid", "target_identity_verified_by_current_repo",
                    "target", "independent_review", "path", "artifacts", "gate_ids", "task_ids", "gap_ids"):
            with self.subTest(key=key):
                self.reject(lambda r: r.pop(key))

    def test_nonboolean_verification_flags_reject(self):
        for key in ("schema_valid", "target_identity_verified_by_current_repo"):
            for value in (1, "true", None, False):
                with self.subTest(key=key, value=value):
                    self.reject(lambda r: r.update({key: value}))

    def test_status_and_id_fail_closed(self):
        for value in ("passed", "valid", "pending", "revoked"):
            self.reject(lambda r: r.update(status=value))
        for value in ("TG-EV-", "other", 1, None):
            self.reject(lambda r: r.update(evidence_id=value))

    def test_review_decisions_are_not_interchangeable(self):
        for value in ("COMMENTED", "APPROVED", "pending", "needs-work", "rejected"):
            self.reject(lambda r: r["independent_review"].update(decision=value))

    def test_review_independence_pair_is_exact(self):
        for key in ("independent", "self_review"):
            for value in (None, 0, 1, "false", not self.fixture.review[key]):
                with self.subTest(key=key, value=value):
                    self.reject(lambda r: r["independent_review"].update({key: value}))

    def test_missing_review_binding_or_identity_rejects(self):
        for key in self.fixture.review:
            with self.subTest(key=key):
                self.reject(lambda r: r["independent_review"].pop(key))

    def test_empty_and_noncanonical_review_identity_or_role(self):
        for key in ("reviewer_identity", "reviewer_role"):
            for value in ("", " reviewer ", "reviewer\n", None):
                self.reject(lambda r: r["independent_review"].update({key: value}))

    def test_review_target_mismatch_rejects(self):
        for key in ("reviewed_commit", "reviewed_tree"):
            self.reject(lambda r: r["independent_review"].update({key: "f" * 40}))

    def test_future_review_rejects(self):
        self.reject(lambda r: r["independent_review"].update(reviewed_at="2027-01-01T00:00:00Z"))

    def test_expiry_boundary_and_malformed_expiry_reject(self):
        for value in ("2026-09-05T00:00:00Z", "2026-09-04T00:00:00Z", "2026-09-07", "bad", False):
            self.reject(lambda r: r.update(expires_at=value))

    def test_timezone_is_required_for_clock_and_timestamps(self):
        for value in ("2026-09-04T20:00:00", "2026-09-04", "2026-09-04T20:00:00+24:00"):
            self.reject(lambda r: r["independent_review"].update(reviewed_at=value))
        self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW.replace(tzinfo=None)))

    def test_foreign_or_nonhex_target_rejects(self):
        for key, value in (("repository", "someone/other"), ("commit", "g" * 40), ("tree", "B" * 40), ("tree", None)):
            self.reject(lambda r: r["target"].update({key: value}))

    def test_conflicting_aliases_reject(self):
        for key, value in (("claim_credit", False), ("claim_credit", 1), ("review", {"decision": "accepted"}),
                           ("candidate", {"repository": ADMISSION.REPOSITORY, "commit": "f" * 40, "tree": "b" * 40}),
                           ("validity", {"schema_valid": False}), ("manifest_path", "other.json")):
            self.reject(lambda r: r.update({key: value}))

    def test_identical_aliases_are_explicitly_compatible(self):
        row = copy.deepcopy(self.fixture.entry)
        row.update(review=row["independent_review"], candidate=row["target"], claim_credit=True)
        self.assertTrue(ADMISSION.entry_eligible(row, root=self.root, now=NOW))

    def test_artifact_digest_size_and_path_are_mandatory(self):
        for key in ("name", "path", "sha256", "size_bytes"):
            self.reject(lambda r: r["artifacts"][0].pop(key))
        for key, value in (("sha256", "wrong"), ("size_bytes", 0), ("size_bytes", True), ("size_bytes", -1),
                           ("size_bytes", ADMISSION.MAX_ARTIFACT_BYTES + 1)):
            self.reject(lambda r: r["artifacts"][0].update({key: value}))

    def test_artifact_byte_tamper_rejects(self):
        path = self.root / self.fixture.artifact["path"]
        path.write_bytes(b"X" + self.fixture.raw[1:])
        self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_artifact_truncation_or_absence_rejects(self):
        path = self.root / self.fixture.artifact["path"]
        path.write_bytes(b"short")
        self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))
        path.unlink()
        self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_path_traversal_absolute_and_noncanonical_paths_reject(self):
        for value in ("../outside", "/tmp/x", "retained//output.txt", "retained/../retained/output.txt", "retained\\output.txt"):
            self.reject(lambda r: r.update(path=value))

    def test_symlinked_artifact_is_rejected(self):
        path = self.root / self.fixture.artifact["path"]
        alternate = self.root / "other.txt"
        path.rename(alternate)
        path.symlink_to(alternate)
        self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_symlinked_parent_is_rejected(self):
        path = self.root / "retained"
        alternate = self.root / "other"
        path.rename(alternate)
        path.symlink_to(alternate, target_is_directory=True)
        self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_duplicate_artifacts_and_empty_set_reject(self):
        self.reject(lambda r: r.update(artifacts=[]))
        self.reject(lambda r: r["artifacts"].append(copy.deepcopy(r["artifacts"][0])))

    def test_manifest_metadata_must_match_index(self):
        for key, value in (("evidence_id", "TG-EV-FIXTURE-OTHER-001"), ("evidence_type", "unit"),
                           ("status", "failed"), ("generated_by_automation", False),
                           ("expires_at", None), ("gate_ids", ["GATE-OTHER"]),
                           ("gap_ids", ["GAP-P0-OTHER-001"])):
            self.reject(lambda m: m.update({key: value}), manifest=True)
        self.reject(lambda m: m["candidate"].update(commit="c" * 40), manifest=True)
        self.reject(lambda m: m["review"].update(reviewer_identity="other-reviewer"), manifest=True)

    def test_missing_manifest_fields_cannot_hide_behind_schema_valid(self):
        for key in ("commands", "environment", "upstream", "review", "result", "started_at", "artifacts"):
            self.reject(lambda m: m.pop(key), manifest=True)

    def test_schema_unknown_properties_and_patterns_reject(self):
        self.reject(lambda m: m.update(unsupported_claim=True), manifest=True)
        self.reject(lambda m: m["environment"].update(secret="value"), manifest=True)
        for value in ("TG-V4-002", "TG-V3-02", "TG-WX-001", "TG-V3-002-extra"):
            self.reject(lambda m, value=value: m.update(task_ids=[value]), manifest=True)
        for value in ("GAP-P3-FIXTURE-001", "GAP-P0-", "GAP-P0-fixture-001",
                      "GAP-P0-FIXTURE-001-extra_unsafe"):
            self.reject(lambda m, value=value: m.update(gap_ids=[value]), manifest=True)

    def test_schema_accepts_current_v3_roadmap_task_ids(self):
        row = copy.deepcopy(self.fixture.entry)
        manifest = copy.deepcopy(self.fixture.manifest)
        row["task_ids"] = ["TG-V3-002"]
        manifest["task_ids"] = ["TG-V3-002"]
        self.fixture.write(manifest)
        self.assertTrue(ADMISSION.entry_eligible(row, root=self.root, now=NOW))

    def test_zero_partial_or_boolean_assertion_counts_reject(self):
        for total, passed in ((0, 0), (3, 2), (3, 4), (True, True), (3, 3.0)):
            self.reject(lambda m: m["result"].update(assertions_total=total, assertions_passed=passed), manifest=True)

    def test_unresolved_critical_divergence_rejects(self):
        for severity in ("P0", "P1"):
            for status in ("open", "explained", "waived"):
                value = {"id": "fixture-divergence", "severity": severity, "category": "fixture",
                         "expected": 1, "observed": 2, "owner": "fixture", "status": status}
                self.reject(lambda m: m["result"].update(divergences=[value]), manifest=True)

    def test_time_order_must_include_review_after_execution(self):
        self.reject(lambda m: m.update(started_at="2026-09-04T22:00:00Z"), manifest=True)
        self.reject(lambda m: m.update(completed_at="2026-09-05T00:00:00Z"), manifest=True)

    def test_retained_artifact_set_must_match(self):
        self.reject(lambda m: m["artifacts"][0].update(sha256="0" * 64), manifest=True)
        self.reject(lambda m: m.update(artifacts=[]), manifest=True)

    def test_duplicate_json_keys_and_nonfinite_numbers_fail_closed(self):
        path = self.root / self.fixture.entry["path"]
        for raw in ('{"status":"passed","status":"passed"}', '{"value":NaN}', '{"value":Infinity}'):
            path.write_text(raw)
            self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_json_bounds_and_nonobject_payloads_reject(self):
        path = self.root / self.fixture.entry["path"]
        for raw in (b"", b"[]", b"\xff", b" " * (ADMISSION.MAX_JSON_BYTES + 1)):
            path.write_bytes(raw)
            self.assertFalse(ADMISSION.entry_eligible(self.fixture.entry, root=self.root, now=NOW))

    def test_unknown_schema_keyword_and_remote_ref_fail_closed(self):
        for schema in ({"unknownConstraint": True}, {"$ref": "https://example.invalid/schema"},
                       {"type": "string", "format": "unsupported"}):
            with self.assertRaises(ADMISSION.AdmissionError):
                ADMISSION.validate_schema("x", schema)

    def test_schema_local_ref_type_bool_and_array_constraints(self):
        schema = {"$defs": {"n": {"type": "integer", "minimum": 1}}, "$ref": "#/$defs/n"}
        ADMISSION.validate_schema(1, schema)
        for value in (True, 0, "1"):
            with self.assertRaises(ADMISSION.AdmissionError):
                ADMISSION.validate_schema(value, schema)
        with self.assertRaises(ADMISSION.AdmissionError):
            ADMISSION.validate_schema(["x", "x"], {"type": "array", "uniqueItems": True})

    def test_recursing_schema_is_bounded(self):
        with self.assertRaises(ADMISSION.AdmissionError):
            ADMISSION.validate_schema({}, {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"})

    def test_closed_gap_requires_types_mapping_and_no_external_dependency(self):
        row = self.fixture.entry
        gap = {"id": "GAP-P0-FIXTURE-001", "evidence_ids": [row["evidence_id"]], "required_evidence_types": ["manifest"], "external_dependency": None}
        evidence = {row["evidence_id"]: row}
        ADMISSION.validate_gap_evidence(gap, evidence, root=self.root, now=NOW)
        for changes in ({"required_evidence_types": ["manifest", "unit"]}, {"evidence_ids": []},
                        {"evidence_ids": ["unknown"]}, {"external_dependency": "review pending"},
                        {"id": "GAP-P3-FIXTURE-001"}):
            with self.subTest(changes=changes), self.assertRaises(ADMISSION.AdmissionError):
                ADMISSION.validate_gap_evidence({**gap, **changes}, evidence, root=self.root, now=NOW)

    def test_evidence_for_one_gap_cannot_close_another_gap(self):
        row = self.fixture.entry
        evidence = {row["evidence_id"]: row}
        other = {"id": "GAP-P0-OTHER-001", "evidence_ids": [row["evidence_id"]],
                 "required_evidence_types": ["manifest"], "external_dependency": None}
        with self.assertRaisesRegex(ADMISSION.AdmissionError, "not mapped to this gap"):
            ADMISSION.validate_gap_evidence(other, evidence, root=self.root, now=NOW)
        row["gap_ids"] = ["GAP-P0-OTHER-001"]
        self.fixture.manifest["gap_ids"] = ["GAP-P0-OTHER-001"]
        self.fixture.write()
        ADMISSION.validate_gap_evidence(other, evidence, root=self.root, now=NOW)


class GapScopeTests(unittest.TestCase):
    def setUp(self):
        self.register = ADMISSION.load_object(ROOT / "docs/status/GAP_REGISTER.json")

    def validate(self, value=None):
        return ADMISSION.validate_gap_scope(
            copy.deepcopy(self.register if value is None else value)
        )

    def test_repository_gap_scope_is_digest_bound(self):
        self.assertEqual(self.validate(), ADMISSION.GAP_SCOPE_SHA256)
        self.assertEqual(self.register["scope_version"], ADMISSION.GAP_SCOPE_VERSION)
        self.assertEqual(self.register["scope_sha256"], ADMISSION.GAP_SCOPE_SHA256)

    def test_gap_order_and_set_like_list_order_are_not_semantic(self):
        value = copy.deepcopy(self.register)
        value["gaps"].reverse()
        for row in value["gaps"]:
            for key in ADMISSION.GAP_SCOPE_LIST_KEYS:
                row[key].reverse()
        self.assertEqual(self.validate(value), ADMISSION.GAP_SCOPE_SHA256)

    def test_mutable_progress_fields_do_not_repin_gap_scope(self):
        value = copy.deepcopy(self.register)
        row = next(item for item in value["gaps"] if item["id"] == "GAP-P0-DATA-001")
        row["status"] = "in-progress"
        row["evidence_ids"] = ["TG-EV-SYNTHETIC-NOT-ADMITTED"]
        self.assertEqual(self.validate(value), ADMISSION.GAP_SCOPE_SHA256)
        governed = next(item for item in value["gaps"] if item["id"] == "GAP-P0-GOV-001")
        governed["status"] = "closed"
        governed["external_dependency"] = None
        self.assertEqual(self.validate(value), ADMISSION.GAP_SCOPE_SHA256)

    def test_semantic_gap_scope_cannot_be_shrunk_or_relabelled(self):
        mutations = {
            "severity": lambda row: row.update(severity="P2"),
            "owner": lambda row: row.update(owner_role="other-owner"),
            "claims": lambda row: row.update(blocking_claims=[]),
            "paths": lambda row: row.update(affected_paths=[]),
            "criteria": lambda row: row.update(close_criteria=["reduced criterion"]),
            "types": lambda row: row.update(required_evidence_types=["manifest"]),
            "issues": lambda row: row.update(issue_refs=[]),
            "external-contract": lambda row: row.update(
                external_dependency_contract="different external contract"
            ),
        }
        for label, mutate in mutations.items():
            value = copy.deepcopy(self.register)
            row = next(item for item in value["gaps"] if item["id"] == "GAP-P0-DATA-001")
            if label == "external-contract":
                row = next(item for item in value["gaps"] if item["id"] == "GAP-P0-GOV-001")
            mutate(row)
            with self.subTest(label=label), self.assertRaises(
                ADMISSION.AdmissionError
            ):
                self.validate(value)

    def test_gap_set_policy_and_declared_digest_are_closed_world(self):
        values = []
        removed = copy.deepcopy(self.register)
        removed["gaps"].pop()
        values.append(removed)
        policy = copy.deepcopy(self.register)
        policy["closure_policy"]["implementation_only_closes_gap"] = True
        values.append(policy)
        statuses = copy.deepcopy(self.register)
        statuses["status_values"].append("invented")
        values.append(statuses)
        declared = copy.deepcopy(self.register)
        declared["scope_sha256"] = "0" * 64
        values.append(declared)
        for value in values:
            with self.subTest(value=value.get("scope_sha256")), self.assertRaises(
                ADMISSION.AdmissionError
            ):
                self.validate(value)

    def test_unresolved_external_dependency_cannot_be_hidden(self):
        value = copy.deepcopy(self.register)
        row = next(item for item in value["gaps"] if item["id"] == "GAP-P0-GOV-001")
        row["external_dependency"] = None
        with self.assertRaisesRegex(
            ADMISSION.AdmissionError, "differs from immutable contract"
        ):
            self.validate(value)


class ConsumerWiringTests(unittest.TestCase):
    """Exercise the real entry points, not a second acceptance implementation."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = RetainedFixture(Path(self.temp.name))
        self.modules = {}
        for name in ("check-evidence-index", "derive-gap-status", "derive-gates", "check-gap-register", "check-status-transitions"):
            loaded = module("admission_consumer_" + name.replace("-", "_"), ROOT / ("scripts/" + name + ".py"))
            loaded.ROOT = self.fixture.root
            self.modules[name] = loaded

    def test_all_consumers_reject_the_old_minimal_manifest_bypass(self):
        row = {"evidence_id": "TG-EV-FIXTURE-BYPASS", "evidence_type": "manifest", "status": "accepted",
               "compatibility_credit": False, "review": {"decision": "accepted"}}
        self.assertFalse(self.modules["derive-gap-status"].accepted_evidence(row))
        self.assertFalse(self.modules["check-status-transitions"].evidence_is_accepted(row))
        with self.assertRaises(self.modules["derive-gates"].DerivationError):
            self.modules["derive-gates"].accepted_evidence([row], NOW)
        with self.assertRaises(self.modules["check-gap-register"].ValidationError):
            self.modules["check-gap-register"].validate_closed_evidence("GAP-P0-FIXTURE-001", "P0", ["manifest"], [row["evidence_id"]], {row["evidence_id"]: row})


    def test_gap_consumer_rejects_cross_gap_evidence(self):
        checker = self.modules["check-gap-register"]
        row = self.fixture.entry
        with self.assertRaisesRegex(checker.ValidationError, "not mapped to this gap"):
            checker.validate_closed_evidence(
                "GAP-P0-OTHER-001", "P0", ["manifest"],
                [row["evidence_id"]], {row["evidence_id"]: row},
            )

    def test_future_or_stale_reviews_never_count_in_any_consumer(self):
        for overrides in ({"self_review": True}, {"independent": False}, {"reviewed_commit": "f" * 40},
                          {"reviewed_tree": "f" * 40}, {"reviewed_at": "2099-01-01T00:00:00Z"}):
            row = copy.deepcopy(self.fixture.entry)
            row["independent_review"].update(overrides)
            with self.subTest(overrides=overrides):
                self.assertFalse(self.modules["derive-gap-status"].accepted_evidence(row))
                self.assertFalse(self.modules["check-status-transitions"].evidence_is_accepted(row))
                with self.assertRaises(self.modules["derive-gates"].DerivationError):
                    self.modules["derive-gates"].accepted_evidence([row], NOW)

    def test_index_requires_every_policy_key_and_retained_payload(self):
        checker = self.modules["check-evidence-index"]
        path = self.fixture.root / "index.json"
        for key in POLICY:
            index = self.fixture.index()
            index["policy"].pop(key)
            path.write_text(json.dumps(index))
            with self.subTest(key=key), self.assertRaises(checker.ValidationError):
                checker.validate(path, self.fixture.root, now=NOW)
        path.write_text(json.dumps(self.fixture.index()))
        self.assertEqual(checker.validate(path, self.fixture.root, now=NOW)["credited"], 1)
        (self.fixture.root / self.fixture.artifact["path"]).write_bytes(b"tampered")
        with self.assertRaises(checker.ValidationError):
            checker.validate(path, self.fixture.root, now=NOW)

    def test_accepted_status_without_credit_is_not_silently_ignored(self):
        checker = self.modules["check-evidence-index"]
        index = self.fixture.index(copy.deepcopy(self.fixture.entry))
        index["entries"][0]["compatibility_credit"] = False
        index["accepted_entry_count"] = 0
        path = self.fixture.root / "index.json"
        path.write_text(json.dumps(index))
        with self.assertRaises(checker.ValidationError):
            checker.validate(path, self.fixture.root, now=NOW)

    def test_index_aliases_and_duplicate_keys_cannot_hide_entries(self):
        checker = self.modules["check-evidence-index"]
        path = self.fixture.root / "index.json"
        index = self.fixture.index()
        index["evidence"] = []
        path.write_text(json.dumps(index))
        with self.assertRaises(checker.ValidationError):
            checker.validate(path, self.fixture.root, now=NOW)
        path.write_text('{"entries":[],"entries":[]}')
        with self.assertRaises(checker.ValidationError):
            checker.validate(path, self.fixture.root, now=NOW)

    def test_every_gap_consumer_invokes_shared_scope_validation(self):
        cases = (
            ("check-gap-register", "validate", ()),
            ("derive-gap-status", "derive", ()),
            ("derive-gates", "derive", ()),
            ("check-status-transitions", "validate_gaps", ({}, set())),
        )
        for name, function, arguments in cases:
            loaded = self.modules[name]
            error = (loaded.ValidationError if name in {"check-gap-register", "check-status-transitions"}
                     else loaded.GapDerivationError if name == "derive-gap-status"
                     else loaded.DerivationError)
            loaded.ROOT = ROOT
            with self.subTest(name=name), patch.object(
                loaded.EVIDENCE, "validate_gap_scope", side_effect=ValueError("scope sentinel")
            ), self.assertRaisesRegex(error, "scope sentinel"):
                getattr(loaded, function)(*arguments)

    def test_every_consumer_has_one_shared_admission_module(self):
        for name, loaded in self.modules.items():
            with self.subTest(name=name):
                self.assertEqual(Path(loaded.EVIDENCE.__file__).name, "evidence_admission.py")


if __name__ == "__main__":
    unittest.main()
