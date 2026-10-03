"""Hostile scope tests for the schema-authority consumer scanner."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "scripts/check-schema-authority.py"


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "trnm_schema_authority_scope_tests", CHECKER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("schema authority checker unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()
BASELINE = Path("scripts/control_baselines/gap-register.v1.json")
SCHEMA_UPGRADE_SOURCE = ROOT / "crates/trnm-persistence-pg/tests/schema_upgrade.rs"
SCHEMA_UPGRADE_EXTENSION = ROOT / "crates/trnm-persistence-pg/tests/schema_upgrade_parts/v3.rs"


class SchemaAuthorityScopeTests(unittest.TestCase):
    def test_only_exact_immutable_gap_baseline_may_quote_quarantined_path(self) -> None:
        self.assertIn(BASELINE, CHECKER.IMMUTABLE_SPECIFICATION_REFERENCES)
        self.assertIn(BASELINE, CHECKER.ALLOWED_CONTROL_REFERENCES)
        self.assertNotIn(
            Path("scripts/control_baselines/unreviewed.json"),
            CHECKER.ALLOWED_CONTROL_REFERENCES,
        )

    def test_specification_quote_is_allowed_but_sibling_consumer_is_rejected(self) -> None:
        original_root = CHECKER.ROOT
        original_roots = CHECKER.CONSUMER_ROOTS
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                baseline = root / BASELINE
                baseline.parent.mkdir(parents=True)
                baseline.write_text(
                    '{"criterion":"database/schema/v2 is non-authoritative"}\n',
                    encoding="utf-8",
                )
                CHECKER.ROOT = root
                CHECKER.CONSUMER_ROOTS = [Path("scripts")]
                CHECKER.scan_forbidden_consumers()

                rogue = root / "scripts/control_baselines/unreviewed.json"
                rogue.write_text(
                    '{"consumer":"database/schema/v2"}\n', encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    CHECKER.ValidationError,
                    "scripts/control_baselines/unreviewed.json",
                ):
                    CHECKER.scan_forbidden_consumers()
        finally:
            CHECKER.ROOT = original_root
            CHECKER.CONSUMER_ROOTS = original_roots

    def test_repository_schema_authority_contract_passes(self) -> None:
        CHECKER.validate_authority()
        CHECKER.validate_quarantine()
        CHECKER.scan_forbidden_consumers()
        CHECKER.validate_sql_abi()
        CHECKER.validate_schema_upgrade_fixtures(
            SCHEMA_UPGRADE_SOURCE.read_text(), SCHEMA_UPGRADE_EXTENSION.read_text()
        )

    def test_active_authority_digest_matches_the_complete_locked_rust_identity(self) -> None:
        chain = CHECKER.validated_migration_chain()
        self.assertEqual(chain["schema_version"], 5)
        self.assertEqual(chain["default_runtime_schema_version"], 4)
        self.assertEqual(chain["digest_algorithm"], "ordered-path-git-blob-sha256.v1")
        for profile, row in chain["profiles"].items():
            digest, files = CHECKER.migration_digest(f"migrations/{profile}")
            self.assertEqual(digest, row["revision_prefixes"]["4"]["chain_sha256"])
            self.assertNotEqual(digest, row["chain_sha256"])
            self.assertEqual([path.relative_to(ROOT).as_posix() for path in files], row["revision_prefixes"]["4"]["ordered_paths"])
            self.assertEqual(len(files), 4)

    def test_native_profile_image_cannot_be_replaced_by_an_external_override(self) -> None:
        for profile in ("postgresql", "cockroachdb"):
            harness = (ROOT / f"scripts/ci-{profile}-semantic-recovery.sh").read_text()
            CHECKER.validate_pinned_database_image(harness, profile)
            variable = "POSTGRES_IMAGE" if profile == "postgresql" else "COCKROACH_IMAGE"
            for mutation in (
                harness.replace("config/database-test-images.json", "historical-images.json"),
                harness.replace('"$expected_image"', '"$' + variable + '"'),
            ):
                with self.subTest(profile=profile):
                    with self.assertRaises(CHECKER.ValidationError):
                        CHECKER.validate_pinned_database_image(mutation, profile)

    def test_native_schema_scenarios_preserve_six_tests_and_all_profile_markers(self) -> None:
        source = SCHEMA_UPGRADE_SOURCE.read_text()
        extension = SCHEMA_UPGRADE_EXTENSION.read_text()
        CHECKER.validate_schema_upgrade_fixtures(source, extension)
        for mutation in (
            source.replace("#[test]", "// removed required native test", 1),
            source.replace("v3_illegal_legacy_preflight_cases(&environment)", "unused_v3_cases(&environment)"),
            source.replace("identity.storage_writer_epoch, 4", "identity.storage_writer_epoch, 3"),
            source.replace("schema_writer_admin_barrier_executed", "removed_admin_barrier"),
        ):
            with self.subTest(source_mutation=mutation != source):
                self.assertNotEqual(mutation, source)
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_schema_upgrade_fixtures(mutation, extension)
        for mutation in (
            extension.replace("V3_SHAPE_CASES: usize = 8", "V3_SHAPE_CASES: usize = 7"),
            extension.replace("schema_v3_partial_resume_executed profile={}", "schema_v3_partial_resume_executed"),
            extension + "\n#[test]\nfn extra_test() {}\n",
        ):
            with self.subTest(extension_mutation=mutation != extension):
                self.assertNotEqual(mutation, extension)
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_schema_upgrade_fixtures(source, mutation)

    def test_illegal_legacy_cannot_skip_whole_database_snapshot_or_real_runner(self) -> None:
        source = SCHEMA_UPGRADE_SOURCE.read_text()
        extension = SCHEMA_UPGRADE_EXTENSION.read_text()
        prefix, body = extension.split("fn v3_illegal_legacy_preflight_cases", 1)
        body, suffix = body.split("fn v3_ready_catalog_drift_cases", 1)
        for before, after in (
            ("entire_database_snapshot(&mut inspector)", "metadata_only_snapshot(&mut inspector)"),
            ("StableCode::DataLoss", "StableCode::Internal"),
            (".unwrap_err()", ".unwrap()"),
            ('"non_utf8"', '"removed_non_utf8"'),
        ):
            mutation = body.replace(before, after)
            self.assertNotEqual(body, mutation)
            with self.subTest(before=before):
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_schema_upgrade_fixtures(
                        source, prefix + "fn v3_illegal_legacy_preflight_cases" + mutation +
                        "fn v3_ready_catalog_drift_cases" + suffix
                    )

    def test_actual_backfill_failure_cannot_be_relabelled_as_resume_proof(self) -> None:
        source = SCHEMA_UPGRADE_SOURCE.read_text()
        extension = SCHEMA_UPGRADE_EXTENSION.read_text()
        for before, after in (
            ("fixture.profile == DatabaseProfile::CockroachDb", "fixture.profile == DatabaseProfile::PostgreSql"),
            ("assert_eq!(after, before)", "assert_eq!(after.catalog, before.catalog)"),
            ("DROP CONSTRAINT fixture_stop_backfill", "DROP CONSTRAINT unrelated_check"),
            ('"database_constraint_violation"', '"unobserved_interruption"'),
            ("not a concurrency proof", "complete concurrency proof"),
        ):
            mutation = extension.replace(before, after)
            self.assertNotEqual(extension, mutation)
            with self.subTest(before=before):
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_schema_upgrade_fixtures(source, mutation)

    def test_historical_v2_identity_cannot_use_new_full_chain_digest(self) -> None:
        source = SCHEMA_UPGRADE_SOURCE.read_text()
        extension = SCHEMA_UPGRADE_EXTENSION.read_text()
        for digest in (
            "b063c33fce9a7c3c506f82c0204b545d8a5ef915b0ff234680fca15057c4a9df",
            "85892562d78571090202d5b2d6bb2941364614399a82542f4cf809370c0e4b0c",
        ):
            mutation = extension.replace(digest, "0" * 64)
            self.assertNotEqual(extension, mutation)
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(CHECKER.ValidationError, "historical v2 chain digest"):
                    CHECKER.validate_schema_upgrade_fixtures(source, mutation)


if __name__ == "__main__":
    unittest.main()
