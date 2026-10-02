from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-migration-lock.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_migration_lock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationLockTests(unittest.TestCase):
    def copy_migrations(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / "migrations", root / "migrations")
        return root

    def test_authoritative_profiles_are_complete_and_distinct(self) -> None:
        result = load_module().validate()
        self.assertEqual(
            set(result["profiles"]),
            {"postgresql", "cockroachdb"},
        )
        self.assertTrue(result["source_identity_verified"])
        self.assertFalse(result["runtime_execution_verified"])
        self.assertFalse(result["compatibility_credit"])
        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(result["digest_algorithm"], "ordered-path-git-blob-sha256.v1")
        for row in result["profiles"].values():
            self.assertEqual(row["file_count"], 3)
            self.assertEqual(row["declared_action_count"], 22)
            self.assertEqual(row["revision_action_counts"], {"2": 6, "3": 16})
            self.assertEqual(row["digest_algorithm"], result["digest_algorithm"])
        self.assertNotEqual(
            result["profiles"]["postgresql"]["chain_sha256"],
            result["profiles"]["cockroachdb"]["chain_sha256"],
        )

    def test_digest_uses_exact_zero_based_binary_framing(self) -> None:
        module = load_module()
        expected_bytes = bytes(8) + b"a.sql\0" + bytes(20) + b"\0\0\0\0\0\0\0\1b.sql\0" + bytes([0xff]) * 20
        self.assertEqual(module.ordered_chain_digest([("a.sql", "00" * 20), ("b.sql", "ff" * 20)]),
                         hashlib.sha256(expected_bytes).hexdigest())

    def test_old_version_short_chain_reordered_and_blob_drift_are_rejected(self) -> None:
        module = load_module()
        for mutation in ("old_version", "previous_version", "bool_version", "short_chain", "reordered", "blob_drift", "path_escape"):
            root = self.copy_migrations()
            path = root / "migrations/MIGRATION_CHAIN.lock.json"
            value = json.loads(path.read_text())
            entries = value["profiles"]["postgresql"]["ordered_files"]
            if mutation == "old_version":
                value["schema_version"] = 1
            elif mutation == "previous_version":
                value["schema_version"] = 2
            elif mutation == "bool_version":
                value["schema_version"] = True
            elif mutation == "short_chain":
                entries.pop()
            elif mutation == "reordered":
                entries.reverse()
            elif mutation == "blob_drift":
                entries[1]["git_blob_sha1"] = "00" * 20
            else:
                entries[1]["path"] = "migrations/postgresql/../cockroachdb/0002_storage_timestamps_up.sql"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(module.ValidationError):
                module.validate(root)

    def rewrite_and_relock(self, root: Path, profile: str, revision: int, source: bytes) -> None:
        module = load_module()
        lock_path = root / "migrations/MIGRATION_CHAIN.lock.json"
        lock = json.loads(lock_path.read_text())
        entry = lock["profiles"][profile]["ordered_files"][revision - 1]
        (root / entry["path"]).write_bytes(source)
        entry["git_blob_sha1"] = module.git_blob_sha1(source)
        lock_path.write_text(json.dumps(lock), encoding="utf-8")

    def test_relocking_does_not_allow_rewriting_either_historical_revision(self) -> None:
        module = load_module()
        for profile in ("postgresql", "cockroachdb"):
            for revision in (1, 2):
                root = self.copy_migrations()
                entry = module.load_lock(root)["profiles"][profile]["ordered_files"][revision - 1]
                self.rewrite_and_relock(root, profile, revision, (root / entry["path"]).read_bytes() + b"-- rewritten history\n")
                with self.subTest(profile=profile, revision=revision):
                    with self.assertRaisesRegex(module.ValidationError, "frozen historical identity"):
                        module.validate(root)

    def test_native_actions_reject_relocked_sql_and_directive_drift(self) -> None:
        module = load_module()
        replacements = (
            ("-- trnm:backfill storage_jsonb_v3", "UPDATE trnm_storage_objects SET value_jsonb = NULL;"),
            ("-- trnm:backfill storage_jsonb_v3", "-- trnm:backfill arbitrary_sql"),
            ("-- trnm:action storage_native_backfill", "-- trnm:action unrelated_backfill"),
            ("value_jsonb JSONB;", "value_jsonb JSONB NOT NULL;"),
            ("public_version VARCHAR(32);", "public_version VARCHAR(33);"),
            ("value_bytes DROP NOT NULL;", "value_bytes SET NOT NULL;"),
            ("value_origin SET NOT NULL;", "value_origin DROP NOT NULL;"),
            ("octet_length(value_projection_digest) = 32", "octet_length(value_projection_digest) >= 0"),
            ("source_manifest_digest <> decode(repeat('0',64),'hex')", "TRUE"),
            ("length(v2_apply_source_commit) = 40", "length(v2_apply_source_commit) >= 0"),
        )
        for profile in ("postgresql", "cockroachdb"):
            for before, after in replacements:
                root = self.copy_migrations()
                path = root / f"migrations/{profile}/0003_storage_jsonb_up.sql"
                source = path.read_text(encoding="utf-8")
                self.assertIn(before, source)
                self.rewrite_and_relock(root, profile, 3, source.replace(before, after).encode())
                with self.subTest(profile=profile, mutation=before):
                    with self.assertRaisesRegex(module.ValidationError, "action grammar drift"):
                        module.validate(root)

    def test_native_actions_require_order_completeness_and_one_statement(self) -> None:
        module = load_module()
        for profile in ("postgresql", "cockroachdb"):
            original = (ROOT / f"migrations/{profile}/0003_storage_jsonb_up.sql").read_text()
            first = "-- trnm:action storage_value_jsonb\nALTER TABLE trnm_storage_objects ADD COLUMN value_jsonb JSONB;"
            second = "-- trnm:action storage_public_version\nALTER TABLE trnm_storage_objects ADD COLUMN public_version VARCHAR(32);"
            mutations = {
                "missing": original.replace(first, ""),
                "duplicate": original + "\n" + first + "\n",
                "reordered": original.replace(first, "TEMPORARY_SWAP").replace(second, first).replace("TEMPORARY_SWAP", second),
                "unmarked_sql": original + "SELECT 1;\n",
                "additional_sql": original.replace("-- trnm:backfill storage_jsonb_v3", "-- trnm:backfill storage_jsonb_v3\nUPDATE trnm_storage_objects SET value_bytes = NULL;"),
                "unknown_directive": original + "-- trnm:execute arbitrary\n",
                "raw_transaction": "BEGIN;\n" + original + "COMMIT;\n",
            }
            for mutation, source in mutations.items():
                root = self.copy_migrations()
                self.rewrite_and_relock(root, profile, 3, source.encode())
                with self.subTest(profile=profile, mutation=mutation):
                    with self.assertRaisesRegex(module.ValidationError, "action grammar drift"):
                        module.validate(root)

    def test_frozen_base_rules_and_exact_profile_inventory_remain_required(self) -> None:
        module = load_module()
        for mutation in ("base", "rollback", "listing_rule", "extra_sql", "missing_sql"):
            root = self.copy_migrations()
            path = root / "migrations/MIGRATION_CHAIN.lock.json"
            value = module.load_lock(root)
            if mutation == "base":
                value["generated_from_base"] = "00" * 20
            elif mutation == "rollback":
                value["rules"]["drop_based_production_rollback_allowed"] = True
            elif mutation == "listing_rule":
                value["rules"]["unlisted_sql_is_failure"] = False
            elif mutation == "extra_sql":
                (root / "migrations/postgresql/0004_unreviewed_up.sql").write_text("SELECT 1;\n")
            else:
                (root / "migrations/postgresql/0003_storage_jsonb_up.sql").unlink()
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(module.ValidationError):
                module.validate(root)


if __name__ == "__main__":
    unittest.main()
