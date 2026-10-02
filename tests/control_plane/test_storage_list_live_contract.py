"""Owned shell-lane source regressions; no database or acceptance credit."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "storage_list_live_contract", ROOT / "scripts/check-trnm-server.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ENVIRONMENT = '''CARGO_TERM_COLOR=never \\
TRNM_REQUIRE_LIVE_DATABASE=1 \\
TRNM_DATABASE_URL="$database_url" \\
TRNM_DATABASE_PROFILE="$profile" \\
'''


def lane(package: str, target: str, selector: str, logfile: str,
         counter: str, marker: str, skipped: str) -> str:
    return ENVIRONMENT + f'''  cargo test -p {package} --locked {target} \\
    {selector} \\
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/{logfile}"
{counter}=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\\1/p' \\
    "$evidence/{logfile}"
)
[[ "${counter}" =~ ^[0-9]+$ ]]
test "${counter}" -eq 1
grep -Fxq "{marker} profile=${{profile}}" \\
  "$evidence/{logfile}"
if grep -Fq '{skipped}' "$evidence/{logfile}"; then
  echo 'synthetic fixture skipped' >&2
  exit 1
fi
'''


CANONICAL = lane(
    "trnm-server", "--lib",
    "runtime::storage_api_tests::canonical_storage_api_live_database",
    "canonical-storage-app.log", "canonical_storage_test_count",
    "canonical_storage_api_live_executed", "canonical_storage_api_live_skipped",
)
PROJECTION = lane(
    "trnm-persistence-pg", "--test authority_storage",
    "nakama_client_listing_modes_cursors_and_integrity_are_database_projected",
    "nakama-client-list-projection.log", "nakama_client_list_test_count",
    "nakama_client_list_projection_executed", "nakama_client_list_projection_skipped",
)
PREFIX = '''#!/usr/bin/env bash
set -euo pipefail
"$binary" migrate > "$evidence/migrate.log" 2>&1
'''
SUFFIX = '''cat > "$evidence/summary.json" <<EOF
{"schema":"synthetic-only","nakama_client_list_projection":true,"wire_compatible":false,"production_ready":false}
EOF
find "$evidence" -type f ! -name SHA256SUMS -print0 \\
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
'''
FIXTURE = PREFIX + CANONICAL + PROJECTION + SUFFIX


class StorageListLiveContractTests(unittest.TestCase):
    def reject(self, changed: str) -> None:
        self.assertNotEqual(changed, FIXTURE, "negative fixture did not change its input")
        with self.assertRaises(SystemExit):
            MODULE.validate_storage_live_harness(changed)

    def test_synthetic_owned_lanes_keep_exact_count_marker_and_seal(self) -> None:
        MODULE.validate_storage_live_harness(FIXTURE)

    def test_actual_harness_keeps_the_owned_lanes(self) -> None:
        MODULE.validate_storage_live_harness(
            (ROOT / "scripts/ci-trnm-server-live.sh").read_text(encoding="utf-8")
        )

    def test_required_environment_cannot_be_absent_or_optional(self) -> None:
        for token in ["TRNM_REQUIRE_LIVE_DATABASE=1", 'TRNM_DATABASE_URL="$database_url"',
                      'TRNM_DATABASE_PROFILE="$profile"']:
            with self.subTest(token=token):
                self.reject(FIXTURE.replace(token, "", 1))
        self.reject(FIXTURE.replace("TRNM_REQUIRE_LIVE_DATABASE=1", "TRNM_REQUIRE_LIVE_DATABASE=0"))

    def test_wrong_cargo_target_or_missing_exact_selector_is_rejected(self) -> None:
        self.reject(FIXTURE.replace("--lib", "--bin trnm-server", 1))
        self.reject(FIXTURE.replace("--test authority_storage", "--test session_response_loss", 1))
        self.reject(FIXTURE.replace("-- --exact", "--", 1))
        self.reject(FIXTURE.replace(
            "nakama_client_listing_modes_cursors_and_integrity_are_database_projected",
            "a_filter_that_discovers_zero_tests",
        ))

    def test_zero_multiple_failed_or_ignored_results_cannot_be_counted(self) -> None:
        for counter in ["canonical_storage_test_count", "nakama_client_list_test_count"]:
            with self.subTest(counter=counter):
                self.reject(FIXTURE.replace(f'test "${counter}" -eq 1', f'test "${counter}" -ge 0'))
                self.reject(FIXTURE.replace(f'test "${counter}" -eq 1', f'test "${counter}" -ge 1'))
                self.reject(FIXTURE.replace(f'[[ "${counter}" =~ ^[0-9]+$ ]]', "true"))
        self.reject(FIXTURE.replace("0 failed; 0 ignored;", "0 failed; 1 ignored;", 1))
        self.reject(FIXTURE.replace("0 failed; 0 ignored;", "1 failed; 0 ignored;", 1))

    def test_profile_marker_must_be_exact_and_use_the_same_log(self) -> None:
        self.reject(FIXTURE.replace("grep -Fxq", "grep -Fq", 1))
        self.reject(FIXTURE.replace("profile=${profile}", "profile=postgresql", 1))
        self.reject(FIXTURE.replace("nakama_client_list_projection_executed", "different_marker", 1))
        self.reject(FIXTURE.replace('"$evidence/nakama-client-list-projection.log"', '"$evidence/other.log"', 1))

    def test_skip_must_terminate_with_failure_even_when_a_success_marker_exists(self) -> None:
        self.reject(FIXTURE.replace("  exit 1", "  exit 0", 1))
        self.reject(FIXTURE.replace("  exit 1", "  true", 1))
        self.reject(FIXTURE.replace("nakama_client_list_projection_skipped", "unchecked_skip", 1))

    def test_comment_only_lane_and_duplicate_lane_cannot_supply_source_guards(self) -> None:
        commented = "\n".join("# " + line for line in PROJECTION.splitlines()) + "\n"
        self.reject(FIXTURE.replace(PROJECTION, commented))
        self.reject(FIXTURE.replace(PROJECTION, PROJECTION + PROJECTION))

    def test_lanes_must_follow_migration_and_canonical_application(self) -> None:
        self.reject(CANONICAL + PREFIX + PROJECTION + SUFFIX)
        self.reject(PREFIX + PROJECTION + CANONICAL + SUFFIX)

    def test_projection_summary_and_all_log_seal_must_follow_assertions(self) -> None:
        self.reject(FIXTURE.replace('"nakama_client_list_projection":true', '"nakama_client_list_projection":false'))
        self.reject(FIXTURE.replace('find "$evidence" -type f', 'find "$evidence" -type f ! -name nakama-client-list-projection.log'))
        self.reject(PREFIX + SUFFIX + CANONICAL + PROJECTION)

    def test_missing_pipefail_or_incomplete_command_is_rejected(self) -> None:
        self.reject(FIXTURE.replace("set -euo pipefail", "set -eu"))
        self.reject(FIXTURE + "continued \\\n")


if __name__ == "__main__":
    unittest.main()
