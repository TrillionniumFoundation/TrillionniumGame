"""Product-gate freshness regressions; synthetic evidence grants no acceptance."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("test cannot load source module")
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


DERIVE = load("trnm_gate_freshness_derivation_tests", ROOT / "scripts/derive-gates.py")
FIXTURE = load(
    "trnm_gate_freshness_retained_fixture",
    ROOT / "tests/control_plane/test_evidence_admission.py",
)
COMPLETED = datetime(2026, 9, 4, 21, tzinfo=timezone.utc)
BOUNDARY = COMPLETED + timedelta(days=30)


class GateFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = FIXTURE.RetainedFixture(self.root)
        self.original_root = DERIVE.ROOT
        self.addCleanup(setattr, DERIVE, "ROOT", self.original_root)
        DERIVE.ROOT = self.root

        self.fixture.entry["expires_at"] = None
        self.fixture.manifest["expires_at"] = None
        self.fixture.entry["independent_review"]["reviewed_at"] = "2026-09-04T23:00:00Z"
        self.fixture.manifest["review"]["reviewed_at"] = "2026-09-04T23:00:00Z"
        self.fixture.manifest["started_at"] = "2026-09-04T20:00:00Z"
        self.fixture.manifest["completed_at"] = "2026-09-04T21:00:00Z"
        self.fixture.write()
        self.write_json("docs/status/GAP_REGISTER.json", {"gaps": []})
        self.write_gate(30)
        self.write_index([self.fixture.entry])

    def write_json(self, relative: str, value: object) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_gate(self, freshness: object = 30, *, include: bool = True,
                   evidence_types: list[str] | None = None) -> None:
        gate = {
            "id": "GATE-FIXTURE",
            "depends_on": [],
            "blocking_gap_ids": [],
            "evidence_types": evidence_types or ["manifest"],
        }
        if include:
            gate["freshness_days"] = freshness
        self.write_json("docs/status/PRODUCT_GATES.json", {"gates": [gate]})

    def write_index(self, rows: list[dict[str, object]]) -> None:
        index = self.fixture.index()
        index["entries"] = rows
        index["accepted_entry_count"] = len(rows)
        self.write_json("docs/evidence/index.json", index)

    def result(self, now: datetime) -> dict[str, object]:
        return DERIVE.derive(now=now)["gates"]["GATE-FIXTURE"]

    def second_entry(self, *, completed: str, evidence_type: str = "unit") -> dict[str, object]:
        row = copy.deepcopy(self.fixture.entry)
        manifest = copy.deepcopy(self.fixture.manifest)
        evidence_id = "TG-EV-FIXTURE-RETAINED-002"
        raw = b"second deterministic synthetic assertion output\n"
        artifact = {
            "name": "unit-output-2",
            "path": "retained/output-2.txt",
            "media_type": "text/plain",
            "sha256": DERIVE.EVIDENCE.hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
        path = self.root / artifact["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        row.update(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            path="docs/evidence/fixture-2.json",
            artifacts=[artifact],
        )
        manifest.update(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            artifacts=[artifact],
            started_at="2026-10-01T19:00:00Z",
            completed_at=completed,
        )
        reviewed_at = "2026-10-01T22:00:00Z"
        row["independent_review"]["reviewed_at"] = reviewed_at
        manifest["review"]["reviewed_at"] = reviewed_at
        manifest["candidate"]["artifact_sha256"] = artifact["sha256"]
        target = self.root / row["path"]
        target.write_text(json.dumps(manifest), encoding="utf-8")
        return row

    def test_validate_entry_returns_the_same_securely_validated_manifest(self) -> None:
        value = DERIVE.EVIDENCE.validate_entry(
            self.fixture.entry, root=self.root, now=BOUNDARY
        )
        self.assertEqual(value, self.fixture.manifest)

    def test_exact_completion_boundary_remains_fresh(self) -> None:
        gate = self.result(BOUNDARY)
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["freshness_days"], 30)
        self.assertEqual(gate["freshness_cutoff"], COMPLETED.isoformat())
        self.assertEqual(
            gate["accepted_evidence_ids"], [self.fixture.entry["evidence_id"]]
        )
        self.assertEqual(gate["stale_evidence_ids"], [])

    def test_one_second_beyond_boundary_is_stale_and_cannot_pass(self) -> None:
        gate = self.result(BOUNDARY + timedelta(seconds=1))
        self.assertEqual(gate["status"], "open")
        self.assertEqual(gate["missing_evidence_types"], ["manifest"])
        self.assertEqual(gate["accepted_evidence_ids"], [])
        self.assertEqual(
            gate["stale_evidence_ids"], [self.fixture.entry["evidence_id"]]
        )

    def test_unlimited_gate_still_requires_normal_evidence_admission(self) -> None:
        self.write_gate(None)
        gate = self.result(datetime(2036, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(gate["status"], "passed")
        self.assertIsNone(gate["freshness_days"])
        self.assertIsNone(gate["freshness_cutoff"])
        self.assertEqual(gate["stale_evidence_ids"], [])

    def test_missing_boolean_zero_negative_fractional_and_text_policies_fail(self) -> None:
        cases = [
            (None, False), (True, True), (0, True), (-1, True), (1.5, True),
            ("30", True), (DERIVE.MAX_GATE_FRESHNESS_DAYS + 1, True),
        ]
        for value, include in cases:
            with self.subTest(value=value, include=include):
                self.write_gate(value, include=include)
                with self.assertRaises(DERIVE.DerivationError):
                    DERIVE.derive(now=BOUNDARY)

    def test_stale_type_does_not_hide_missing_fresh_type(self) -> None:
        fresh = self.second_entry(completed="2026-10-01T20:00:00Z")
        self.write_gate(30, evidence_types=["manifest", "unit"])
        self.write_index([self.fixture.entry, fresh])
        gate = self.result(BOUNDARY + timedelta(seconds=1))
        self.assertEqual(gate["status"], "open")
        self.assertEqual(gate["missing_evidence_types"], ["manifest"])
        self.assertEqual(gate["accepted_evidence_ids"], [fresh["evidence_id"]])
        self.assertEqual(
            gate["stale_evidence_ids"], [self.fixture.entry["evidence_id"]]
        )

    def test_current_repository_snapshot_still_fails_closed_without_credit(self) -> None:
        DERIVE.ROOT = ROOT
        derived = DERIVE.derive(now=datetime(2026, 9, 5, 16, tzinfo=timezone.utc))
        DERIVE.check_snapshot(derived)
        self.assertEqual(derived["summary"]["passed"], 0)
        self.assertEqual(derived["accepted_evidence_count"], 0)
        for detail in derived["gates"].values():
            self.assertIn("freshness_days", detail)
            self.assertIn("stale_evidence_ids", detail)


if __name__ == "__main__":
    unittest.main()
