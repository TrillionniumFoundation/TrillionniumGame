from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_core_checker():
    spec = importlib.util.spec_from_file_location("check_storage_core", ROOT / "scripts/check-storage-core.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageIntegrityContractTests(unittest.TestCase):
    def test_write_callers_cannot_supply_internal_integrity_digest(self) -> None:
        source = (ROOT / "crates/trnm-storage-core/src/lib.rs").read_text(encoding="utf-8")
        write_block = source.split("pub struct WriteOperation {", 1)[1].split("}", 1)[0]
        self.assertNotIn("integrity_digest", write_block)
        self.assertNotIn("collision_witness", write_block)
        self.assertIn("CollisionWitness::from_request(&operation.value, &value)?", source)
        self.assertIn("integrity_digest: collision_witness.projection_digest()", source)

    def test_persistence_derives_and_rechecks_sha256(self) -> None:
        storage_root = ROOT / "crates/trnm-persistence-pg/src"
        source = (storage_root / "storage.rs").read_text(encoding="utf-8")
        source += "".join(
            path.read_text(encoding="utf-8")
            for path in sorted((storage_root / "storage_parts").glob("*.rs"))
        )
        self.assertIn("let projection_digest = IntegrityDigest::from_value(&projected).get();", source)
        self.assertIn("let request_digest = IntegrityDigest::from_value(&operation.value).get();", source)
        self.assertIn("verify_storage_integrity(&value, integrity_digest)?;", source)
        self.assertIn('data_loss("storage_integrity_digest_mismatch")', source)
        self.assertIn("CollisionWitness::from_request(&raw, &value)?", source)
        self.assertIn("witness.validate_projection(&version, &value)?;", source)
        self.assertIn('"value_projection_digest".to_owned()', source)
        self.assertIn("value_jsonb::TEXT", source)
        self.assertNotIn("ContentVersion::from_value(&value)", source)
        self.assertNotIn("operation.integrity_digest", source)

    def test_listing_cursor_binds_exact_actor_owner_and_key_scope(self) -> None:
        storage_root = ROOT / "crates/trnm-persistence-pg/src"
        source = (storage_root / "storage.rs").read_text(encoding="utf-8")
        source += "".join(
            path.read_text(encoding="utf-8")
            for path in sorted((storage_root / "storage_parts").glob("*.rs"))
        )
        self.assertIn(
            "type StorageListCursor = (Actor, Option<UserId>, StorageObjectKey);",
            source,
        )
        self.assertIn("after: Option<&StorageListCursor>", source)
        self.assertIn("type StorageListPage = (Vec<StorageObject>, Option<StorageListCursor>);", source)
        self.assertIn("cursor.0 != actor", source)
        self.assertIn("cursor.1 != owner", source)
        self.assertIn("cursor.2.collection() != collection", source)
        self.assertNotIn("after: Option<&StorageObjectKey>", source)

    def test_sha256_dependency_is_exact_and_registered(self) -> None:
        manifest = (ROOT / "crates/trnm-storage-core/Cargo.toml").read_text(encoding="utf-8")
        policy = (ROOT / "scripts/check-rust-foundation.py").read_text(encoding="utf-8")
        self.assertIn('sha2 = "=0.11.0"', manifest)
        self.assertIn("'crates/trnm-storage-core': {'sha2': '=0.11.0'", policy)

    def copy_core_contract(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        checker = load_core_checker()
        for relative in (
            "Cargo.toml", "contracts/storage/storage-vectors.json",
            "docs/status/STORAGE_CORE_STATUS.json",
            "docs/status/IMPLEMENTATION_INVENTORY.json", "docs/status/SOURCE_CANDIDATES.json",
            *checker.SOURCE_FILES, *checker.AUXILIARY_SOURCE_FILES,
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, path)
        return root

    def test_core_checker_inventories_every_projection_source_and_regression(self) -> None:
        checker = load_core_checker()
        result = checker.validate()
        self.assertEqual(result["source_files"], list(checker.SOURCE_FILES))
        self.assertEqual(result["auxiliary_source_files"], list(checker.AUXILIARY_SOURCE_FILES))
        self.assertEqual(result["projection_tests"], 11)
        self.assertEqual(result["rust_tests"], 30)
        self.assertEqual(result["vector_cases"], 15)
        self.assertFalse(result["cargo_executed_locally"])
        self.assertFalse(result["compatibility_credit"])

    def test_projection_modules_cannot_be_missing_unwired_or_uninventoried(self) -> None:
        checker = load_core_checker()
        for mutation in ("missing_module", "missing_tests", "unwired_module", "unwired_tests", "extra_module"):
            root = self.copy_core_contract()
            lib = root / checker.SOURCE_FILES[0]
            if mutation == "missing_module":
                (root / checker.SOURCE_FILES[1]).unlink()
            elif mutation == "missing_tests":
                (root / checker.SOURCE_FILES[2]).unlink()
            elif mutation == "unwired_module":
                lib.write_text(lib.read_text().replace("mod projection;", ""))
            elif mutation == "unwired_tests":
                lib.write_text(lib.read_text().replace("mod projection_tests;", ""))
            else:
                (root / "crates/trnm-storage-core/src/unreviewed.rs").write_text("// unlisted source\n")
            with self.subTest(mutation=mutation), self.assertRaises(checker.ValidationError):
                checker.validate(root)

    def test_raw_projection_version_receipt_and_witness_bindings_cannot_drift(self) -> None:
        checker = load_core_checker()
        mutations = (
            (0, "pub version: PublicVersion,", "pub version: ContentVersion,"),
            (0, "pub previous_version: Option<PublicVersion>,", "pub previous_version: Option<ContentVersion>,"),
            (0, "!witness.matches_request(&operation.value)", "!witness.matches_request(&object.value)"),
            (0, "integrity_digest: collision_witness.projection_digest()", "integrity_digest: collision_witness.request_digest()"),
            (0, "witness.validate_projection(&object.version, &object.value)?;", ""),
            (0, "let value = projector(&operation.value)?;", "let value = operation.value.clone();"),
            (1, "value.chars().take(33).count() > 32", "value.len() > 32"),
            (1, "request_version: ContentVersion::from_value(request)", "request_version: ContentVersion::from_value(projected_value)"),
            (1, "projection_digest: IntegrityDigest::from_value(projected_value)", "projection_digest: IntegrityDigest::from_value(request)"),
            (1, "version.as_str() != self.request_version.as_str()", "false"),
            (1, "!self.projection_digest.matches_value(projected_value)", "false"),
        )
        for index, before, after in mutations:
            root = self.copy_core_contract()
            path = root / checker.SOURCE_FILES[index]
            source = path.read_text()
            self.assertIn(before, source)
            path.write_text(source.replace(before, after))
            with self.subTest(mutation=before), self.assertRaises(checker.ValidationError):
                checker.validate(root)
        for field in ("request_digest", "request_len", "request_version", "projection_digest"):
            root = self.copy_core_contract()
            path = root / checker.SOURCE_FILES[1]
            source = path.read_text()
            header, body = source.split("pub struct CollisionWitness {", 1)
            path.write_text(header + "pub struct CollisionWitness {" + body.replace(f"    {field}:", f"    pub {field}:", 1))
            with self.subTest(public_field=field), self.assertRaisesRegex(checker.ValidationError, "private"):
                checker.validate(root)

    def test_request_and_projection_budgets_cannot_be_merged_or_overclaimed(self) -> None:
        checker = load_core_checker()
        for before, after in (
            ("16 * 1024 * 1024", "1024 * 1024"),
            ("StableCode::ResourceExhausted", "StableCode::DataLoss"),
        ):
            root = self.copy_core_contract()
            path = root / checker.SOURCE_FILES[1]
            path.write_text(path.read_text().replace(before, after))
            with self.subTest(mutation=before), self.assertRaises(checker.ValidationError):
                checker.validate(root)
        for field, value in (
            ("request_byte_limit", 16777216), ("projection_byte_limit", 1048576),
            ("native_renderer_implemented", True), ("accepted", True),
            ("compatibility_credit", True), ("database_durable", True),
        ):
            root = self.copy_core_contract()
            path = root / "contracts/storage/storage-vectors.json"
            vectors = json.loads(path.read_text())
            vectors["projection_source_candidate"][field] = value
            path.write_text(json.dumps(vectors))
            with self.subTest(field=field), self.assertRaises(checker.ValidationError):
                checker.validate(root)

    def test_old_vectors_and_every_projection_regression_remain_required(self) -> None:
        checker = load_core_checker()
        for name in sorted(checker.PROJECTION_TESTS):
            root = self.copy_core_contract()
            path = root / checker.SOURCE_FILES[2]
            path.write_text(path.read_text().replace(f"fn {name}()", f"fn removed_{name}()"))
            with self.subTest(test=name), self.assertRaisesRegex(checker.ValidationError, "missing Rust projection tests"):
                checker.validate(root)
        for case_id in sorted(checker.REQUIRED_VECTOR_CASES):
            root = self.copy_core_contract()
            path = root / "contracts/storage/storage-vectors.json"
            vectors = json.loads(path.read_text())
            vectors["cases"] = [case for case in vectors["cases"] if case["id"] != case_id]
            path.write_text(json.dumps(vectors))
            with self.subTest(vector=case_id), self.assertRaisesRegex(checker.ValidationError, "incomplete"):
                checker.validate(root)

    def test_machine_source_inventory_and_parity_claim_boundaries_cannot_drift(self) -> None:
        checker = load_core_checker()
        for mutation in ("component_missing_source", "component_missing_binary", "component_credit", "removed_parity", "candidate_missing", "candidate_missing_source", "candidate_accepted", "candidate_gap_closed", "candidate_credit"):
            root = self.copy_core_contract()
            inventory_path = root / "docs/status/IMPLEMENTATION_INVENTORY.json"
            candidates_path = root / "docs/status/SOURCE_CANDIDATES.json"
            inventory = json.loads(inventory_path.read_text())
            candidates = json.loads(candidates_path.read_text())
            component = next(row for row in inventory["components"] if row["id"] == "COMP-STORAGE")
            candidate = next(row for row in candidates["candidates"] if row["id"] == "SRC-STORAGE-PROJECTION-CORE-V3")
            if mutation == "component_missing_source":
                component["source_files"].pop()
            elif mutation == "component_missing_binary":
                component["auxiliary_source_files"] = []
            elif mutation == "component_credit":
                component["claim_credit"] = True
            elif mutation == "removed_parity":
                component["parity_ids"].pop()
            elif mutation == "candidate_missing":
                candidates["candidates"].remove(candidate)
            elif mutation == "candidate_missing_source":
                candidate["paths"].remove(checker.SOURCE_FILES[1])
            else:
                field = {"candidate_accepted": "accepted", "candidate_gap_closed": "gap_closed", "candidate_credit": "compatibility_credit"}[mutation]
                candidate[field] = True
            inventory_path.write_text(json.dumps(inventory))
            candidates_path.write_text(json.dumps(candidates))
            with self.subTest(mutation=mutation), self.assertRaises(checker.ValidationError):
                checker.validate(root)


if __name__ == "__main__":
    unittest.main()
