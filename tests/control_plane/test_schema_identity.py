"""A successful process must still carry the complete authoritative identity."""
import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("trnm_schema_identity", ROOT / "scripts/check-authoritative-schema-identity.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SchemaIdentityTests(unittest.TestCase):
    def setUp(self):
        self.chains = {"postgresql": {"chain_sha256": "a" * 64, "file_count": 3},
                       "cockroachdb": {"chain_sha256": "b" * 64, "file_count": 3}}
        self.value = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": "postgresql",
                      "schema_version": 3, "storage_writer_epoch": 3, "digest_algorithm": MODULE.ALGORITHM,
                      "chain_digest": "a" * 64, "table_count": 10, "compatibility_credit": False,
                      "source_commit": "a" * 40, "upgrade_source_commit": "a" * 40, "v2_apply_source_commit": "a" * 40,
                      "migration_applied": True, "applied_steps": 3}

    def validate(self, value=None, mode="fresh", source="a" * 40, from_version=None, prior=None):
        MODULE.validate_identity(self.value if value is None else value, profile="postgresql", chains=self.chains,
                                 schema_version=3, table_count=10, mode=mode, source_commit=source,
                                 from_version=from_version, v2_apply_source_commit=prior)

    def test_fresh_requires_the_entire_chain_and_both_apply_provenances(self):
        self.validate()
        for changes in [{"applied_steps": 1}, {"applied_steps": 2}, {"source_commit": "c" * 40}, {"upgrade_source_commit": "c" * 40},
                        {"v2_apply_source_commit": "c" * 40},
                        {"migration_applied": False, "applied_steps": 0}]:
            with self.subTest(changes=changes), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | changes)

    def test_upgrade_preserves_old_provenance_but_checks_new_apply_identity(self):
        value = self.value | {"applied_steps": 1, "upgrade_source_commit": "c" * 40}
        self.validate(value, mode="upgrade", source="c" * 40, from_version=2, prior="a" * 40)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(value, mode="upgrade", source="d" * 40, from_version=2, prior="a" * 40)

    def test_readonly_verify_accepts_previous_apply_commit_without_rebinding(self):
        value = self.value | {"migration_applied": False, "applied_steps": 0}
        self.validate(value, mode="verify", source=None)
        self.validate(value, mode="verify", source=None, prior="a" * 40)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(value, mode="verify", source="c" * 40)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(self.value, mode="verify", source=None)
        with self.assertRaises(MODULE.ValidationError):
            self.validate(value | {"v2_apply_source_commit": "c" * 40}, mode="verify", source=None, prior="a" * 40)

    def test_profile_digest_algorithm_version_epoch_and_claim_drift_reject(self):
        for changes in [{"profile": "cockroachdb"}, {"chain_digest": "b" * 64}, {"digest_algorithm": "legacy"},
                        {"schema_version": 1}, {"schema_version": 2}, {"schema_version": 4}, {"storage_writer_epoch": 1},
                        {"storage_writer_epoch": 2}, {"storage_writer_epoch": 4}, {"table_count": 9}, {"compatibility_credit": True}]:
            with self.subTest(changes=changes), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | changes)

    def test_missing_fields_wrong_numeric_types_and_outcome_mismatch_reject(self):
        for key in self.value:
            value = copy.deepcopy(self.value)
            del value[key]
            with self.subTest(missing=key), self.assertRaises(MODULE.ValidationError):
                self.validate(value)
        for changes in [{"schema_version": True}, {"table_count": "10"}, {"migration_applied": 1},
                        {"applied_steps": True}, {"applied_steps": -1}, {"applied_steps": 4},
                        {"source_commit": "g" * 40}, {"upgrade_source_commit": "a" * 39},
                        {"v2_apply_source_commit": None}, {"v2_apply_source_commit": "g" * 40}]:
            with self.subTest(changes=changes), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | changes)

    def test_raw_json_identity_rejects_duplicate_fields_before_validation(self):
        encoded = json.dumps(self.value)
        self.validate(MODULE.decode_identity_document(encoded.encode()))
        # Invalid earlier identity fields must not disappear when a later field
        # supplies the expected value. Identical and escaped keys also duplicate.
        for prefix in ['"profile":"cockroachdb",', '"profile":"postgresql",',
                       '"schema_version":1,', '"chain_digest":"stale",',
                       '"profi\\u006ce":"cockroachdb",', '"v2_apply_source_commit":"' + "c" * 40 + '",']:
            document = "{" + prefix + encoded[1:]
            with self.subTest(prefix=prefix), self.assertRaises(MODULE.ValidationError):
                MODULE.decode_identity_document(document.encode())
        with self.assertRaises(MODULE.ValidationError):
            MODULE.decode_identity_document(b'{"extra":{"field":1,"field":2}}')

    def test_raw_identity_byte_budget_remains_bounded(self):
        with self.assertRaises(MODULE.ValidationError):
            MODULE.decode_identity_document(b" " * (MODULE.MAX_IDENTITY_BYTES + 1))

    def test_v1_and_v2_upgrade_require_distinct_exact_suffix_lengths(self):
        from_v1 = self.value | {"applied_steps": 2, "upgrade_source_commit": "c" * 40, "v2_apply_source_commit": "c" * 40}
        self.validate(from_v1, mode="upgrade", source="c" * 40, from_version=1)
        from_v2 = self.value | {"applied_steps": 1, "upgrade_source_commit": "c" * 40}
        self.validate(from_v2, mode="upgrade", source="c" * 40, from_version=2, prior="a" * 40)
        for prior_version, report, expected_prior in ((1, from_v2, None), (2, from_v1, "c" * 40)):
            with self.subTest(from_version=prior_version), self.assertRaises(MODULE.ValidationError):
                self.validate(report, mode="upgrade", source="c" * 40, from_version=prior_version, prior=expected_prior)
        for previous in (None, 0, 3, True, "2"):
            with self.subTest(previous=previous), self.assertRaises(MODULE.ValidationError):
                self.validate(from_v2, mode="upgrade", source="c" * 40, from_version=previous, prior="a" * 40)
        for mode in ("any", "fresh", "verify"):
            with self.subTest(mode=mode), self.assertRaises(MODULE.ValidationError):
                self.validate(from_v2, mode=mode, source=None, from_version=2, prior="a" * 40)

    def test_v2_upgrade_requires_the_independently_known_prior_publisher(self):
        upgraded = self.value | {"applied_steps": 1, "upgrade_source_commit": "c" * 40}
        for prior in (None, "c" * 40, "bad", 7):
            with self.subTest(prior=prior), self.assertRaises(MODULE.ValidationError):
                self.validate(upgraded, mode="upgrade", source="c" * 40, from_version=2, prior=prior)
        changed = upgraded | {"v2_apply_source_commit": "d" * 40}
        with self.assertRaises(MODULE.ValidationError):
            self.validate(changed, mode="upgrade", source="c" * 40, from_version=2, prior="a" * 40)
        from_v1 = upgraded | {"applied_steps": 2}
        with self.assertRaises(MODULE.ValidationError):
            self.validate(from_v1, mode="upgrade", source="c" * 40, from_version=1)

    def test_fresh_provenance_consistency_is_required_without_expected_binary_sha(self):
        self.validate(source=None)
        for field in ("source_commit", "upgrade_source_commit", "v2_apply_source_commit"):
            with self.subTest(field=field), self.assertRaises(MODULE.ValidationError):
                self.validate(self.value | {field: "c" * 40}, source=None)
        for count in (1, 2, True, "3"):
            chains = copy.deepcopy(self.chains)
            chains["postgresql"]["file_count"] = count
            with self.subTest(source_count=count), self.assertRaises(MODULE.ValidationError):
                MODULE.validate_identity(self.value, profile="postgresql", chains=chains,
                                         schema_version=3, table_count=10, mode="fresh", source_commit="a" * 40)

    def test_cli_executes_explicit_upgrade_modes_and_preserved_prior_checks(self):
        chains, version, tables = MODULE.validated_source()
        for profile in ("postgresql", "cockroachdb"):
            base = self.value | {"profile": profile, "chain_digest": chains[profile]["chain_sha256"],
                                 "schema_version": version, "table_count": tables}
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "identity.json"

                def execute(report, *arguments):
                    path.write_text(json.dumps(report))
                    return subprocess.run(
                        ["python3", str(ROOT / "scripts/check-authoritative-schema-identity.py"), str(path), profile, *arguments],
                        capture_output=True, text=True, timeout=10,
                    )

                cases = (
                    (base, ("--mode", "fresh", "--source-commit", "a" * 40), True),
                    (base | {"applied_steps": 2, "upgrade_source_commit": "c" * 40, "v2_apply_source_commit": "c" * 40},
                     ("--mode", "upgrade", "--from-version", "1", "--source-commit", "c" * 40), True),
                    (base | {"applied_steps": 1, "upgrade_source_commit": "c" * 40},
                     ("--mode", "upgrade", "--from-version", "2", "--source-commit", "c" * 40, "--v2-apply-source-commit", "a" * 40), True),
                    (base | {"migration_applied": False, "applied_steps": 0, "upgrade_source_commit": "c" * 40},
                     ("--mode", "verify", "--v2-apply-source-commit", "a" * 40), True),
                    (base | {"applied_steps": 1, "upgrade_source_commit": "c" * 40},
                     ("--mode", "upgrade", "--from-version", "2", "--source-commit", "c" * 40), False),
                    (base | {"applied_steps": 1, "upgrade_source_commit": "c" * 40},
                     ("--mode", "upgrade", "--from-version", "1", "--source-commit", "c" * 40), False),
                    (base | {"migration_applied": False, "applied_steps": 0, "v2_apply_source_commit": "c" * 40},
                     ("--mode", "verify", "--v2-apply-source-commit", "a" * 40), False),
                )
                for report, arguments, success in cases:
                    with self.subTest(profile=profile, arguments=arguments):
                        result = execute(report, *arguments)
                        self.assertEqual(result.returncode == 0, success, result.stderr)
                        if success:
                            checked = json.loads(result.stdout)
                            self.assertEqual(checked["schema_version"], 3)
                            self.assertTrue(checked["identity_verified"])
                            self.assertFalse(checked["compatibility_credit"])
                        else:
                            self.assertFalse(result.stdout)


if __name__ == "__main__":
    unittest.main()
