"""A successful process must still carry the complete authoritative identity."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("trnm_schema_identity", ROOT / "scripts/check-authoritative-schema-identity.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SchemaIdentityTests(unittest.TestCase):
    def setUp(self):
        self.chains = {"postgresql": {"chain_sha256": "a" * 64, "file_count": 2},
                       "cockroachdb": {"chain_sha256": "b" * 64, "file_count": 2}}
        self.value = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": "postgresql",
                      "schema_version": 2, "storage_writer_epoch": 2, "digest_algorithm": MODULE.ALGORITHM,
                      "chain_digest": "a" * 64, "table_count": 10, "compatibility_credit": False,
                      "source_commit": "a" * 40, "upgrade_source_commit": "a" * 40,
                      "migration_applied": True, "applied_steps": 2}

    def validate(self, value=None, mode="fresh", source="a" * 40):
        MODULE.validate_identity(self.value if value is None else value, profile="postgresql", chains=self.chains,
                                 schema_version=2, table_count=10, mode=mode, source_commit=source)

    def test_fresh_requires_the_entire_chain_and_both_apply_provenances(self):
        self.validate()
        for changes in [{"applied_steps": 1}, {"source_commit": "c" * 40}, {"upgrade_source_commit": "c" * 40},
                        {"migration_applied": False, "applied_steps": 0}]:
            with self.subTest(changes=changes), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | changes)

    def test_upgrade_preserves_old_provenance_but_checks_new_apply_identity(self):
        value = self.value | {"applied_steps": 1, "upgrade_source_commit": "c" * 40}
        self.validate(value, mode="upgrade", source="c" * 40)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(value, mode="upgrade", source="d" * 40)

    def test_readonly_verify_accepts_previous_apply_commit_without_rebinding(self):
        value = self.value | {"migration_applied": False, "applied_steps": 0}
        self.validate(value, mode="verify", source=None)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(value, mode="verify", source="c" * 40)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(self.value, mode="verify", source=None)

    def test_profile_digest_algorithm_version_epoch_and_claim_drift_reject(self):
        for changes in [{"profile": "cockroachdb"}, {"chain_digest": "b" * 64}, {"digest_algorithm": "legacy"},
                        {"schema_version": 1}, {"schema_version": 3}, {"storage_writer_epoch": 1},
                        {"storage_writer_epoch": 3}, {"table_count": 9}, {"compatibility_credit": True}]:
            with self.subTest(changes=changes), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | changes)

    def test_missing_fields_wrong_numeric_types_and_outcome_mismatch_reject(self):
        for key in self.value:
            value = copy.deepcopy(self.value)
            del value[key]
            with self.subTest(missing=key), self.assertRaises(MODULE.ValidationError):
                self.validate(value)
        for changes in [{"schema_version": True}, {"table_count": "10"}, {"migration_applied": 1},
                        {"applied_steps": True}, {"applied_steps": -1}, {"applied_steps": 3},
                        {"source_commit": "g" * 40}, {"upgrade_source_commit": "a" * 39}]:
            with self.subTest(changes=changes), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | changes)

    def test_raw_json_identity_rejects_duplicate_fields_before_validation(self):
        encoded = json.dumps(self.value)
        self.validate(MODULE.decode_identity_document(encoded.encode()))
        # Invalid earlier identity fields must not disappear when a later field
        # supplies the expected value. Identical and escaped keys also duplicate.
        for prefix in ['"profile":"cockroachdb",', '"profile":"postgresql",',
                       '"schema_version":1,', '"chain_digest":"stale",',
                       '"profi\\u006ce":"cockroachdb",']:
            document = "{" + prefix + encoded[1:]
            with self.subTest(prefix=prefix), self.assertRaises(MODULE.ValidationError):
                MODULE.decode_identity_document(document.encode())
        with self.assertRaises(MODULE.ValidationError):
            MODULE.decode_identity_document(b'{"extra":{"field":1,"field":2}}')

    def test_raw_identity_byte_budget_remains_bounded(self):
        with self.assertRaises(MODULE.ValidationError):
            MODULE.decode_identity_document(b" " * (MODULE.MAX_IDENTITY_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
