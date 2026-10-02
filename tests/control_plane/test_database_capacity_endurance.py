from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-database-capacity-endurance.py"
FINALIZER = ROOT / "scripts/finalize-database-endurance-ledger.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


class DatabaseCapacityEnduranceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load(CHECKER, "capacity_checker")
        cls.finalizer = load(FINALIZER, "endurance_finalizer")

    def capacity(
        self,
        *,
        index: int,
        requested: int = 21_600,
        observed: int | None = None,
        start: int | None = None,
        failed: int = 0,
        commit: str = "a" * 40,
        tree: str = "b" * 40,
        profile: str = "postgresql",
        database_identity: str = "e" * 64,
        image: str = "postgres:18@sha256:" + "d" * 64,
        clients: int = 4,
        threads: int = 2,
        v2_apply_source_commit: str = "3" * 40,
        v3_apply_source_commit: str = "4" * 40,
    ) -> dict:
        observed = requested if observed is None else observed
        start = 1_800_000_000 + index * 21_600 if start is None else start
        chains, version, tables = self.finalizer.IDENTITY.validated_source()
        schema_profile = profile if profile in chains else "postgresql"
        return {
            "schema": "trillionnium.database-capacity-segment.v1",
            "profile": profile,
            "database_endpoint_redacted": "<redacted>",
            "database_logical_id_sha256": database_identity,
            "client_image_reference": image,
            "candidate_commit": commit,
            "candidate_tree": tree,
            "workload_sha256": "c" * 64,
            "migration_lock_sha256": hashlib.sha256((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_bytes()).hexdigest(),
            "schema_identity": {
                "schema": "trillionnium.authoritative-schema-report.v1", "profile": schema_profile,
                "schema_version": version, "storage_writer_epoch": 4,
                "chain_digest": chains[schema_profile]["chain_sha256"],
                "digest_algorithm": "ordered-path-git-blob-sha256.v1",
                "source_commit": "1" * 40, "upgrade_source_commit": "2" * 40,
                # An explicit synthetic previous-revision publisher, independent
                # of the candidate that only performs read-only verification.
                "v2_apply_source_commit": v2_apply_source_commit,
                "v3_apply_source_commit": v3_apply_source_commit,
                "migration_applied": False, "applied_steps": 0, "table_count": tables,
                "compatibility_credit": False,
            },
            "requested_duration_seconds": requested,
            "observed_duration_seconds": observed,
            "started_epoch_seconds": start,
            "finished_epoch_seconds": start + observed,
            "clients": clients,
            "threads": threads,
            "transactions": 100,
            "failed_transactions": failed,
            "latency_average_ms": 1.0,
            "transactions_per_second": 10.0,
            "stdout_sha256": "f" * 64,
            "stderr_sha256": "0" * 64,
            "claim_boundary": {
                "capacity_target_accepted": False,
                "performance_accepted": False,
                "endurance_complete": False,
                "independently_accepted": False,
                "production_ready": False,
            },
        }

    def write_segment(
        self,
        root: Path,
        index: int,
        previous: str,
        capacity: dict | None = None,
    ) -> Path:
        capacity = self.capacity(index=index) if capacity is None else capacity
        value = {
            "schema": "trillionnium.database-endurance-segment.v1",
            "segment_index": index,
            "previous_segment_sha256": previous,
            "capacity_manifest_sha256": hashlib.sha256(canonical(capacity)).hexdigest(),
            "capacity_manifest": capacity,
        }
        path = root / f"{index:02d}.json"
        path.write_bytes(canonical(value))
        return path

    def four_segment_ledger(self, root: Path, **capacity_overrides):
        paths = []
        previous = "GENESIS"
        previous_finished = 1_800_000_000
        for index in range(4):
            capacity = self.capacity(
                index=index,
                start=previous_finished,
                **capacity_overrides,
            )
            path = self.write_segment(root, index, previous, capacity)
            paths.append(path)
            previous = hashlib.sha256(path.read_bytes()).hexdigest()
            previous_finished = capacity["finished_epoch_seconds"]
        return paths

    def test_24h_contiguous_exact_ledger_passes_without_acceptance_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.four_segment_ledger(Path(temporary))
            report = self.finalizer.validate(paths, "24h")
            self.assertEqual(report["requested_duration_seconds"], 86_400)
            self.assertEqual(report["observed_duration_seconds"], 86_400)
            self.assertEqual(report["maximum_segment_gap_seconds"], 0)
            self.assertEqual(report["schema_identity"], self.capacity(index=0)["schema_identity"])
            self.assertEqual(report["schema_identity"]["v2_apply_source_commit"], "3" * 40)
            self.assertEqual(report["schema_identity"]["v3_apply_source_commit"], "4" * 40)
            self.assertNotEqual(report["schema_identity"]["v2_apply_source_commit"], report["candidate_commit"])
            self.assertFalse(any(report["claim_boundary"].values()))

    def test_index_previous_digest_failure_and_short_duration_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.write_segment(root, 0, "GENESIS")
            bad = self.write_segment(root, 2, "0" * 64)
            with self.assertRaisesRegex(self.finalizer.ValidationError, "segment gap"):
                self.finalizer.validate([first, bad], "24h")
            with self.assertRaisesRegex(
                self.finalizer.ValidationError, "requested endurance duration"
            ):
                self.finalizer.validate([first], "24h")

    def test_failed_transaction_and_exact_identity_changes_are_rejected(self):
        mutations = (
            ({"failed": 1}, "failed transactions"),
            ({"commit": "short"}, "candidate commit"),
            ({"tree": "short"}, "candidate tree"),
            ({"profile": "other"}, "profile"),
            ({"database_identity": "0" * 64}, "database identity changed"),
            ({"image": "postgres:18"}, "digest pinned"),
            ({"clients": 5}, "client count changed"),
            ({"threads": 1}, "thread count changed"),
        )
        for overrides, message in mutations:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = self.four_segment_ledger(root)
                previous = hashlib.sha256(paths[0].read_bytes()).hexdigest()
                capacity = self.capacity(index=1, start=1_800_021_600, **overrides)
                paths[1] = self.write_segment(root, 1, previous, capacity)
                previous = hashlib.sha256(paths[1].read_bytes()).hexdigest()
                for index in (2, 3):
                    base = self.capacity(index=index)
                    paths[index] = self.write_segment(root, index, previous, base)
                    previous = hashlib.sha256(paths[index].read_bytes()).hexdigest()
                with self.assertRaisesRegex(self.finalizer.ValidationError, message):
                    self.finalizer.validate(paths, "24h")

    def test_manifest_tamper_is_rejected_by_nested_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.four_segment_ledger(root)
            value = json.loads(paths[1].read_text(encoding="utf-8"))
            value["capacity_manifest"]["transactions"] = 999
            paths[1].write_bytes(canonical(value))
            with self.assertRaisesRegex(
                self.finalizer.ValidationError, "capacity manifest digest"
            ):
                self.finalizer.validate(paths, "24h")

    def test_wall_clock_overhead_cannot_inflate_requested_endurance(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.four_segment_ledger(
                Path(temporary), requested=21_300, observed=21_600
            )
            with self.assertRaisesRegex(
                self.finalizer.ValidationError, "requested endurance duration"
            ):
                self.finalizer.validate(paths, "24h")

    def test_overlap_and_excessive_inter_segment_gap_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.four_segment_ledger(root)
            for start, message in (
                (1_800_021_599, "overlaps"),
                (1_800_021_600 + 901, "gap too large"),
            ):
                with self.subTest(start=start):
                    previous = hashlib.sha256(paths[0].read_bytes()).hexdigest()
                    capacity = self.capacity(index=1, start=start)
                    paths[1] = self.write_segment(root, 1, previous, capacity)
                    previous = hashlib.sha256(paths[1].read_bytes()).hexdigest()
                    for index in (2, 3):
                        next_capacity = self.capacity(
                            index=index,
                            start=capacity["finished_epoch_seconds"]
                            + (index - 2) * 21_600,
                        )
                        paths[index] = self.write_segment(
                            root, index, previous, next_capacity
                        )
                        previous = hashlib.sha256(paths[index].read_bytes()).hexdigest()
                    with self.assertRaisesRegex(self.finalizer.ValidationError, message):
                        self.finalizer.validate(paths, "24h")

    def test_source_contract_cli_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("capacity/endurance source contract: OK", result.stdout)

    def test_mixed_or_missing_schema_identity_cannot_form_an_endurance_ledger(self):
        for mutation in ("missing", "old_epoch", "missing_prior", "invalid_prior", "changed_prior", "missing_v3_prior", "invalid_v3_prior", "changed_v3_prior", "wrong_chain", "wrong_algorithm", "mutation_claim", "changed_provenance", "wrong_lock"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = self.four_segment_ledger(root)
                capacity = self.capacity(index=1)
                schema = capacity["schema_identity"]
                if mutation == "missing":
                    capacity.pop("schema_identity")
                elif mutation == "old_epoch":
                    schema["storage_writer_epoch"] = 2
                elif mutation == "missing_prior":
                    schema.pop("v2_apply_source_commit")
                elif mutation == "invalid_prior":
                    schema["v2_apply_source_commit"] = "short"
                elif mutation == "changed_prior":
                    schema["v2_apply_source_commit"] = "4" * 40
                elif mutation == "missing_v3_prior":
                    schema.pop("v3_apply_source_commit")
                elif mutation == "invalid_v3_prior":
                    schema["v3_apply_source_commit"] = "short"
                elif mutation == "changed_v3_prior":
                    schema["v3_apply_source_commit"] = "5" * 40
                elif mutation == "wrong_chain":
                    schema["chain_digest"] = "e" * 64
                elif mutation == "wrong_algorithm":
                    schema["digest_algorithm"] = "raw-content-historical.v1"
                elif mutation == "mutation_claim":
                    schema["migration_applied"] = True
                    schema["applied_steps"] = 2
                elif mutation == "changed_provenance":
                    schema["upgrade_source_commit"] = "3" * 40
                else:
                    capacity["migration_lock_sha256"] = "e" * 64
                previous = hashlib.sha256(paths[0].read_bytes()).hexdigest()
                paths[1] = self.write_segment(root, 1, previous, capacity)
                previous = hashlib.sha256(paths[1].read_bytes()).hexdigest()
                for index in (2, 3):
                    paths[index] = self.write_segment(root, index, previous)
                    previous = hashlib.sha256(paths[index].read_bytes()).hexdigest()
                with self.assertRaises(self.finalizer.ValidationError):
                    self.finalizer.validate(paths, "24h")

    def test_capacity_workload_preserves_create_time_and_requires_schema_verification(self):
        plan = json.loads(self.checker.PLAN.read_text())
        workload = self.checker.WORKLOAD.read_text()
        smoke = self.checker.SMOKE.read_text()
        segment = self.checker.SEGMENT.read_text()
        finalize = self.checker.FINALIZE.read_text()
        for candidate in (
            workload.replace("update_time = now()", "create_time = now()"),
            workload.replace("now(), now()", ":payload, :payload"),
            workload.replace("create_time, update_time", "create_time"),
        ):
            with self.assertRaises(self.checker.ValidationError):
                self.checker.validate(plan, candidate, smoke, segment, finalize)
        with self.assertRaises(self.checker.ValidationError):
            self.checker.validate(plan, workload, smoke.replace("apply-authoritative-schema.sh", "removed-schema-verifier"), segment, finalize)

    def test_schema_identity_json_cannot_have_ambiguous_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.four_segment_ledger(Path(temporary))
            value = paths[0].read_text()
            ambiguous = value.replace('"storage_writer_epoch":4', '"storage_writer_epoch":3,"storage_writer_epoch":4')
            self.assertNotEqual(value, ambiguous)
            paths[0].write_text(ambiguous)
            with self.assertRaisesRegex(self.finalizer.ValidationError, "duplicate JSON key storage_writer_epoch"):
                self.finalizer.validate(paths, "24h")

    def test_actual_capacity_manifest_code_preserves_full_verified_schema_provenance(self):
        # Execute the real manifest producer with captured schema input and
        # synthetic pgbench metrics; this test does not execute a database.
        source = self.checker.SMOKE.read_text(encoding="utf-8")
        self.assertEqual(source.count("<<'PY'\n"), 1)
        program = source.split("<<'PY'\n", 1)[1].removesuffix("PY\n")
        for profile in ("postgresql", "cockroachdb"):
            for context in ("upgraded", "fresh"):
                with self.subTest(profile=profile, context=context), tempfile.TemporaryDirectory() as temporary:
                    evidence = Path(temporary)
                    capacity = self.capacity(index=0, profile=profile)
                    identity = capacity["schema_identity"]
                    if context == "fresh":
                        for publisher in ("source_commit", "upgrade_source_commit", "v2_apply_source_commit", "v3_apply_source_commit"):
                            identity[publisher] = capacity["candidate_commit"]
                    (evidence / "schema-identity.json").write_bytes(canonical(identity))
                    (evidence / "pgbench.stdout").write_text(
                        "number of transactions actually processed: 100\n"
                        "number of failed transactions: 0\n"
                        "latency average = 1.0 ms\n"
                        "tps = 10.0\n", encoding="utf-8",
                    )
                    (evidence / "pgbench.stderr").write_text("", encoding="utf-8")
                    environment = dict(os.environ, TRNM_DATABASE_LOGICAL_ID="synthetic-capacity-fixture")
                    completed = subprocess.run(
                        [sys.executable, "-c", program, str(ROOT), str(evidence), profile,
                         capacity["client_image_reference"], "21600", "21600", "1800000000", "1800021600",
                         "4", "2", capacity["candidate_commit"], capacity["candidate_tree"]],
                        cwd=ROOT, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=30, check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    produced = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
                    self.assertEqual(produced["schema_identity"], identity)
                    self.assertEqual(produced["schema_identity"]["storage_writer_epoch"], 4)
                    self.assertEqual(produced["schema_identity"]["v2_apply_source_commit"], identity["v2_apply_source_commit"])
                    self.assertEqual(produced["migration_lock_sha256"], capacity["migration_lock_sha256"])
                    self.finalizer.validate_capacity_manifest(produced, evidence / "manifest.json")


if __name__ == "__main__":
    unittest.main()
