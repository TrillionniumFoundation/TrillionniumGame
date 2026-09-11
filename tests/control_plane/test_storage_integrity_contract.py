from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StorageIntegrityContractTests(unittest.TestCase):
    def test_write_callers_cannot_supply_internal_integrity_digest(self) -> None:
        source = (ROOT / "crates/trnm-storage-core/src/lib.rs").read_text(encoding="utf-8")
        write_block = source.split("pub struct WriteOperation {", 1)[1].split("}", 1)[0]
        self.assertNotIn("integrity_digest", write_block)
        self.assertIn("let integrity_digest = IntegrityDigest::from_value(&operation.value);", source)

    def test_persistence_derives_and_rechecks_sha256(self) -> None:
        storage_root = ROOT / "crates/trnm-persistence-pg/src"
        source = (storage_root / "storage.rs").read_text(encoding="utf-8")
        source += "".join(
            path.read_text(encoding="utf-8")
            for path in sorted((storage_root / "storage_parts").glob("*.rs"))
        )
        self.assertIn("let integrity_digest = IntegrityDigest::from_value(&operation.value);", source)
        self.assertIn("verify_storage_integrity(&value, integrity_digest)?;", source)
        self.assertIn('data_loss("storage_integrity_digest_mismatch")', source)
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


if __name__ == "__main__":
    unittest.main()
