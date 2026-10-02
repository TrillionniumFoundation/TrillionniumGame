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

    def test_active_authority_digest_matches_the_complete_locked_rust_identity(self) -> None:
        chain = CHECKER.validated_migration_chain()
        self.assertEqual(chain["digest_algorithm"], "ordered-path-git-blob-sha256.v1")
        for profile, row in chain["profiles"].items():
            digest, files = CHECKER.migration_digest(f"migrations/{profile}")
            self.assertEqual(digest, row["chain_sha256"])
            self.assertEqual([path.relative_to(ROOT).as_posix() for path in files], row["ordered_paths"])
            self.assertEqual(len(files), 2)

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


if __name__ == "__main__":
    unittest.main()
