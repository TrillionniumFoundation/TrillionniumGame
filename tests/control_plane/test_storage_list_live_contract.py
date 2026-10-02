"""Owned shell-lane source regressions; no database or acceptance credit."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
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
OPAQUE_MARKER = '''grep -Fxq "storage_opaque_conditions_live_executed profile=${profile} write_cases=15 delete_cases=18 batch_cases=2" \\
  "$evidence/canonical-storage-app.log"
'''
JSONB_MARKER = '''grep -Fxq "storage_jsonb_v3_live_executed profile=${profile} history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3" \\
  "$evidence/canonical-storage-app.log"
'''
NATIVE_INPUT_MARKER = '''test "$(grep -Ec "^storage_jsonb_v3_native_inputs profile=${profile} condition_sql=(accepted|rejected) payload_sql=(accepted|rejected) compatibility_credit=false$" "$evidence/canonical-storage-app.log")" -eq 1
'''
HOMOGENEOUS_APP_MARKER = 'grep -Fxq "storage_homogeneous_app_executed profile=${profile} write_occurrences=13 rollback_cases=3" "$evidence/canonical-storage-app.log"\ntest "$(grep -Ec \'^storage_homogeneous_app_executed \' "$evidence/canonical-storage-app.log")" -eq 1\n'
CANONICAL = CANONICAL.replace(
    "if grep -Fq 'canonical_storage_api_live_skipped'",
    OPAQUE_MARKER + JSONB_MARKER + NATIVE_INPUT_MARKER + HOMOGENEOUS_APP_MARKER + "if grep -Fq 'canonical_storage_api_live_skipped'"
)
PROJECTION = lane(
    "trnm-persistence-pg", "--test authority_storage",
    "nakama_client_listing_modes_cursors_and_integrity_are_database_projected",
    "nakama-client-list-projection.log", "nakama_client_list_test_count",
    "nakama_client_list_projection_executed", "nakama_client_list_projection_skipped",
)
OCC = lane(
    "trnm-persistence-pg", "--test authority_storage",
    "blind_storage_no_op_preserves_timestamp_after_acl_occ_and_integrity_checks",
    "storage-occ-precedence.log", "storage_occ_test_count",
    "storage_blind_write_timestamps_executed", "storage_blind_write_timestamps_skipped",
)
TIMESTAMPS = lane(
    "trnm-persistence-pg", "--test storage_timestamps",
    "storage_timestamps_database_clock_no_op_and_atomicity",
    "storage-timestamps.log", "storage_timestamps_test_count",
    "storage_timestamps_live_executed", "storage_timestamps_live_skipped",
)
NATIVE_JSONB = lane(
    "trnm-persistence-pg", "--test storage_jsonb",
    "storage_native_jsonb_versions_provenance_and_atomicity",
    "storage-native-jsonb.log", "storage_native_jsonb_test_count",
    "storage_native_jsonb_live_executed", "storage_native_jsonb_live_skipped",
)
NATIVE_JSONB_UNIQUE = '''test "$(grep -Fxc "storage_native_jsonb_live_executed profile=${profile}" "$evidence/storage-native-jsonb.log")" -eq 1
'''
NATIVE_JSONB = NATIVE_JSONB.replace(
    "if grep -Fq 'storage_native_jsonb_live_skipped'",
    NATIVE_JSONB_UNIQUE + "if grep -Fq 'storage_native_jsonb_live_skipped'",
)
V4_ACL = lane(
    "trnm-persistence-pg", "--test storage_permissions_v4",
    "storage_v4_raw_acl_domains_keys_and_operation_predicates_are_native",
    "storage-v4-acl.log", "storage_v4_acl_test_count",
    "storage_v4_acl_live_executed", "storage_v4_acl_live_skipped",
)
V4_ACL_UNIQUE = '''test "$(grep -Fxc "storage_v4_acl_live_executed profile=${profile}" "$evidence/storage-v4-acl.log")" -eq 1
'''
V4_ACL = V4_ACL.replace(
    "if grep -Fq 'storage_v4_acl_live_skipped'",
    V4_ACL_UNIQUE + "if grep -Fq 'storage_v4_acl_live_skipped'",
)
IMPORT_ANNEX = '''python3 scripts/materialize-pinned-storage-upstream.py \\
  --destination "$evidence/storage-source-upstream" \\
  > "$evidence/storage-v4-source-materialization.json"
python3 scripts/check-trnm-server.py \\
  --storage-import-source-archive "$evidence" --profile "$profile" \\
  --commit "$candidate_sha" --tree "$candidate_tree" \\
  > "$evidence/storage-v4-source-archive.log"
'''
IMPORT_ENVIRONMENT = ENVIRONMENT + '''TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" \\
TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY="$evidence_absolute/storage-source-upstream" \\
TRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha" \\
TRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree" \\
TRNM_STORAGE_IMPORT_EVIDENCE_ROOT="$evidence_absolute/storage-v4-import-packets" \\
'''
V4_IMPORT = lane(
    "trnm-persistence-pg", "--test storage_import_v4",
    "storage_v4_source_export_custody_resume_finish_and_tamper_are_native",
    "storage-v4-import.log", "storage_v4_import_test_count",
    "storage_v4_import_live_executed", "storage_v4_import_live_skipped",
).replace(ENVIRONMENT, IMPORT_ENVIRONMENT)
V4_IMPORT_UNIQUE = '''test "$(grep -Fxc "storage_v4_import_live_executed profile=${profile}" "$evidence/storage-v4-import.log")" -eq 1
'''
V4_IMPORT_TERMINAL = '''test "$(grep -Ec '^test result:' "$evidence/storage-v4-import.log")" -eq 1
'''
V4_IMPORT = V4_IMPORT.replace("if grep -Fq 'storage_v4_import_live_skipped'",
                            V4_IMPORT_UNIQUE + "if grep -Fq 'storage_v4_import_live_skipped'")
V4_IMPORT = V4_IMPORT.replace('grep -Fxq "storage_v4_import_live_executed',
                            V4_IMPORT_TERMINAL + 'grep -Fxq "storage_v4_import_live_executed')
DUPLICATE_ENVIRONMENT = ENVIRONMENT + 'TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" \\\nTRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY="$evidence_absolute/storage-source-upstream" \\\nTRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha" \\\nTRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree" \\\n'
DUPLICATES = lane(
    "trnm-persistence-pg", "--test storage_duplicate_batches", MODULE.STORAGE_DUPLICATE_SELECTOR,
    MODULE.STORAGE_DUPLICATE_LOG, "storage_duplicate_batches_test_count",
    "nakama_duplicate_batches_executed", "nakama_duplicate_batches_skipped",
).replace(ENVIRONMENT, DUPLICATE_ENVIRONMENT)
DUPLICATE_GUARDS = "".join(
    f'grep -Fxq "{marker} profile=${{profile}}{suffix}" "$evidence/{MODULE.STORAGE_DUPLICATE_LOG}"\n'
    f'test "$(grep -Ec \'^{marker} \' "$evidence/{MODULE.STORAGE_DUPLICATE_LOG}")" -eq 1\n'
    for marker, suffix in MODULE.STORAGE_DUPLICATE_MARKERS
)
DUPLICATE_TERMINAL = 'test "$(grep -Ec \'^test result:\' "$evidence/storage-duplicate-batches.log")" -eq 1\n'
DUPLICATES = (DUPLICATES[:DUPLICATES.index('grep -Fxq "nakama_duplicate_batches_executed')] +
              DUPLICATE_TERMINAL + DUPLICATE_GUARDS +
              DUPLICATES[DUPLICATES.index("if grep -Fq 'nakama_duplicate_batches_skipped'"):])

SCHEMA_FAMILIES = {
    "shapes": 8, "illegal_legacy": 9, "catalog_drift": 6,
    "partial_resume": 3, "metadata_validation": 9, "opaque_history": 6,
}


def schema_family_guard(family: str, count: int) -> str:
    return f'''grep -Fxq "schema_v3_{family}_executed profile=${{profile}} extra_cases={count}" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_{family}_executed ' "$evidence/schema-upgrade.log")" -eq 1
'''


SCHEMA = r'''CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test schema_upgrade \
    -- --nocapture --test-threads=1 2>&1 | tee "$evidence/schema-upgrade.log"
schema_upgrade_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/schema-upgrade.log"
)
[[ "$schema_upgrade_test_count" =~ ^[0-9]+$ ]]
test "$schema_upgrade_test_count" -eq 6
if grep -Fq 'developer-only live test skip' "$evidence/schema-upgrade.log"; then
  exit 1
fi
'''
SCHEMA = SCHEMA.replace("if grep -Fq 'developer-only live test skip'",
                        "".join(schema_family_guard(family, count) for family, count in SCHEMA_FAMILIES.items()) +
                        '''if grep -Eq 'schema_v3_[a-z_]+_skipped' "$evidence/schema-upgrade.log"; then
  exit 1
fi
''' +
                        "if grep -Fq 'developer-only live test skip'")

PREFIX = '''#!/usr/bin/env bash
set -euo pipefail
evidence_absolute=$(cd "$evidence" && pwd -P)
"$binary" migrate > "$evidence/migrate.log" 2>&1
'''
# The actual archive block is also executed against temporary files below.
# Reuse its shell layout here only to exercise independent source guard removal,
# reordering and weakening; this fixture supplies no database execution credit.
ACTUAL_HARNESS = (ROOT / "scripts/ci-trnm-server-live.sh").read_text(encoding="utf-8")
SCHEMA_V3 = ACTUAL_HARNESS[ACTUAL_HARNESS.index("schema_version=$(db_scalar"):
                           ACTUAL_HARNESS.index("begin_stage session-response-loss")]
SUFFIX = '''cat > "$evidence/summary.json" <<EOF
{"schema":"synthetic-only","nakama_client_list_projection":true,"storage_occ_precedence":true,"raw_version_conditions":true,"storage_jsonb_v3_projection":true,"storage_native_jsonb":true,"storage_v4_acl":true,"schema_v3_extra_cases":41,"schema_v3_case_families":{"shapes":8,"illegal_legacy":9,"catalog_drift":6,"partial_resume":3,"metadata_validation":9,"opaque_history":6},"storage_jsonb_v3_cases":{"history":6,"opaque_success":4,"no_op":2,"resource":1,"native_input":3},"schema_version":${schema_version},"storage_writer_epoch":${storage_writer_epoch},"authoritative_migrations_count":${authoritative_migrations_count},"wire_compatible":false,"compatibility_credit":false,"accepted":false,"production_ready":false}
EOF
find "$evidence" -type f ! -name SHA256SUMS -print0 \\
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
'''
SUFFIX = SUFFIX.replace('"storage_v4_acl":true,', '"storage_v4_acl":true,"storage_v4_import":true,"storage_homogeneous_batches":true,"storage_homogeneous_app":true,"storage_write_tail_drain":true,')
FIXTURE = PREFIX + CANONICAL + PROJECTION + OCC + TIMESTAMPS + NATIVE_JSONB + V4_ACL + IMPORT_ANNEX + V4_IMPORT + DUPLICATES + SCHEMA + SCHEMA_V3 + SUFFIX


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
        for counter in ["canonical_storage_test_count", "nakama_client_list_test_count",
                        "storage_occ_test_count", "storage_timestamps_test_count", "storage_native_jsonb_test_count", "storage_v4_acl_test_count", "storage_duplicate_batches_test_count"]:
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

    def test_occ_precedence_lane_cannot_be_omitted_skipped_or_relabelled(self) -> None:
        for changed in [
            FIXTURE.replace(OCC, ""),
            FIXTURE.replace(OCC, OCC + OCC),
            FIXTURE.replace(OCC, "\n".join("# " + line for line in OCC.splitlines()) + "\n"),
            FIXTURE.replace("blind_storage_no_op_preserves_timestamp_after_acl_occ_and_integrity_checks",
                            "a_filter_that_discovers_zero_tests"),
            FIXTURE.replace("storage_blind_write_timestamps_executed", "unrelated_marker"),
            FIXTURE.replace("storage_blind_write_timestamps_skipped", "unchecked_skip"),
            FIXTURE.replace('"$evidence/storage-occ-precedence.log"', '"$evidence/unrelated.log"', 1),
            FIXTURE.replace('"storage_occ_precedence":true', '"storage_occ_precedence":false'),
        ]:
            with self.subTest(changed=changed):
                self.reject(changed)

    def test_original_condition_execution_marker_and_summary_are_required(self) -> None:
        for changed in [
            FIXTURE.replace(OPAQUE_MARKER, ""),
            FIXTURE.replace(OPAQUE_MARKER, OPAQUE_MARKER + OPAQUE_MARKER),
            FIXTURE.replace(OPAQUE_MARKER, "\n".join("# " + line for line in OPAQUE_MARKER.splitlines()) + "\n"),
            FIXTURE.replace(OPAQUE_MARKER, OPAQUE_MARKER.replace("grep -Fxq", "grep -Fq")),
            FIXTURE.replace(OPAQUE_MARKER, OPAQUE_MARKER.replace("profile=${profile}", "profile=postgresql")),
            FIXTURE.replace(OPAQUE_MARKER, OPAQUE_MARKER.replace("write_cases=15", "write_cases=0")),
            FIXTURE.replace(OPAQUE_MARKER, OPAQUE_MARKER.replace("canonical-storage-app.log", "unrelated.log")),
            FIXTURE.replace('"raw_version_conditions":true', '"raw_version_conditions":false'),
            FIXTURE.replace(OPAQUE_MARKER, "") + OPAQUE_MARKER,
        ]:
            with self.subTest(changed=changed):
                self.reject(changed)

    def test_jsonb_cases_and_native_profile_observations_must_bind_the_same_fixture(self) -> None:
        for marker in (JSONB_MARKER, NATIVE_INPUT_MARKER):
            for changed in (
                FIXTURE.replace(marker, ""), FIXTURE.replace(marker, marker + marker),
                FIXTURE.replace(marker, "\n".join("# " + line for line in marker.splitlines()) + "\n"),
                FIXTURE.replace(marker, marker.replace("canonical-storage-app.log", "unrelated.log")),
                FIXTURE.replace(marker, marker.replace("profile=${profile}", "profile=postgresql")),
                FIXTURE.replace(marker, "") + marker,
            ):
                with self.subTest(marker=marker, changed=changed):
                    self.reject(changed)
        self.reject(FIXTURE.replace("history_cases=6", "history_cases=5", 1))
        self.reject(FIXTURE.replace("compatibility_credit=false$", "compatibility_credit=true$"))

    def test_real_schema_abi_and_three_archived_sql_files_are_required(self) -> None:
        for changed in (
            FIXTURE.replace('test "$schema_version" = 4', 'test "$schema_version" = 2'),
            FIXTURE.replace('test "$storage_writer_epoch" = 4', 'test "$storage_writer_epoch" = 2'),
            FIXTURE.replace('test "$v2_apply_source_commit" = "$candidate_sha"', "true"),
            FIXTURE.replace('test "$authoritative_migrations_count" -eq 4', 'test "$authoritative_migrations_count" -ge 2'),
            FIXTURE.replace("target.write_bytes(data)", "target.write_text('synthetic SQL')"),
            FIXTURE.replace("assert target.read_bytes()==data", "pass"),
            FIXTURE.replace("'sha256':hashlib.sha256(data).hexdigest()", "'sha256':'guessed'"),
            FIXTURE.replace("'size_bytes':len(data)", "'size_bytes':0"),
            FIXTURE.replace(SCHEMA_V3, "") + SCHEMA_V3,
        ):
            with self.subTest(changed=changed):
                self.reject(changed)
        self.reject(FIXTURE.replace('"native_input":3', '"native_input":0'))
        self.reject(FIXTURE.replace('"storage_jsonb_v3_projection":true', '"storage_jsonb_v3_projection":false'))
        self.reject(FIXTURE.replace('"accepted":false', '"accepted":true'))

    def test_all_schema_families_counts_and_unique_markers_are_required(self) -> None:
        self.assertEqual(MODULE.SCHEMA_V3_CASE_FAMILIES, SCHEMA_FAMILIES)
        self.assertEqual(sum(SCHEMA_FAMILIES.values()), 41)
        for family, count in SCHEMA_FAMILIES.items():
            guard = schema_family_guard(family, count)
            for changed in (
                FIXTURE.replace(guard, ""),
                FIXTURE.replace(guard, guard + guard),
                FIXTURE.replace(guard, guard.replace(f"extra_cases={count}", "extra_cases=0")),
                FIXTURE.replace(guard, guard.replace("grep -Fxq", "grep -Fq")),
                FIXTURE.replace(guard, guard.replace("-eq 1", "-ge 1")),
                FIXTURE.replace(guard, guard.replace("schema-upgrade.log", "unrelated.log")),
                FIXTURE.replace(guard, "") + guard,
            ):
                with self.subTest(family=family, changed=changed):
                    self.reject(changed)
        self.reject(FIXTURE.replace('"schema_v3_extra_cases":41', '"schema_v3_extra_cases":40'))
        self.reject(FIXTURE.replace('"metadata_validation":9', '"metadata_validation":8'))
        self.reject(FIXTURE.replace('test "$schema_upgrade_test_count" -eq 6', 'test "$schema_upgrade_test_count" -ge 0'))
        self.reject(FIXTURE.replace("developer-only live test skip", "unchecked_skip"))
        self.reject(FIXTURE.replace("schema_v3_[a-z_]+_skipped", "unchecked_family_skip"))

    def test_native_jsonb_fixture_cannot_be_claimed_without_its_exact_execution(self) -> None:
        for changed in (
            FIXTURE.replace(NATIVE_JSONB, ""), FIXTURE.replace(NATIVE_JSONB, NATIVE_JSONB + NATIVE_JSONB),
            FIXTURE.replace(NATIVE_JSONB, "\n".join("# " + line for line in NATIVE_JSONB.splitlines()) + "\n"),
            FIXTURE.replace("storage_native_jsonb_versions_provenance_and_atomicity", "filter_that_runs_zero_tests"),
            FIXTURE.replace(NATIVE_JSONB_UNIQUE, ""),
            FIXTURE.replace("storage_native_jsonb_live_skipped", "unchecked_native_skip"),
            FIXTURE.replace('"storage_native_jsonb":true', '"storage_native_jsonb":false'),
        ):
            with self.subTest(changed=changed):
                self.reject(changed)

    def test_source_and_prospective_workflows_require_their_own_native_packet_validation(self) -> None:
        for prospective, name, target, commit in (
            (False, "trnm-server-live.yml", "$evidence", "$CANDIDATE_SHA"),
            (True, "prospective-merge-gate.yml", "$server", "$PROSPECTIVE_MERGE_SHA"),
        ):
            source = (ROOT / ".github/workflows" / name).read_text()
            MODULE.validate_storage_live_workflow(source, prospective=prospective)
            for changed in (
                source.replace(f'--live-packet "{target}"', '--live-packet "$other_packet"'),
                source.replace(f'--commit "{commit}" --tree', '--commit "$OTHER_OBJECT" --tree'),
                source.replace("summary['schema_v3_extra_cases'] == 41", "summary['schema_v3_extra_cases'] >= 0"),
                source.replace("'storage_native_jsonb'", "'unchecked_native_fixture'"),
                source.replace("'metadata_validation': 9", "'metadata_validation': 0"),
            ):
                with self.subTest(workflow=name):
                    self.assertNotEqual(changed, source)
                    with self.assertRaises(SystemExit):
                        MODULE.validate_storage_live_workflow(changed, prospective=prospective)


    def test_v4_acl_lane_requires_exact_selector_unique_marker_and_closed_summary(self) -> None:
        for changed in (
            FIXTURE.replace(V4_ACL, ""), FIXTURE.replace(V4_ACL, V4_ACL + V4_ACL),
            FIXTURE.replace("storage_v4_raw_acl_domains_keys_and_operation_predicates_are_native", "zero_test_selector"),
            FIXTURE.replace(V4_ACL_UNIQUE, ""),
            FIXTURE.replace("storage_v4_acl_live_skipped", "unchecked_skip"),
            FIXTURE.replace('"storage_v4_acl":true', '"storage_v4_acl":false'),
        ):
            with self.subTest(changed=changed):
                self.reject(changed)

    def test_v4_import_requires_whole_source_materialization_and_exact_live_execution(self) -> None:
        for changed in (
            FIXTURE.replace('evidence_absolute=$(cd "$evidence" && pwd -P)\n', ""),
            FIXTURE.replace('$evidence_absolute/storage-', '$evidence/storage-'),
            FIXTURE.replace(IMPORT_ANNEX, ""), FIXTURE.replace(V4_IMPORT, ""),
            FIXTURE.replace(V4_IMPORT, V4_IMPORT + V4_IMPORT),
            FIXTURE.replace("--test storage_import_v4", "--test storage_permissions_v4"),
            FIXTURE.replace("storage_v4_source_export_custody_resume_finish_and_tamper_are_native", "zero_test_selector"),
            FIXTURE.replace(V4_IMPORT_UNIQUE, ""), FIXTURE.replace(V4_IMPORT_TERMINAL, ""),
            FIXTURE.replace("storage_v4_import_live_skipped", "unchecked_import_skip"),
            FIXTURE.replace('"storage_v4_import":true', '"storage_v4_import":false'),
            FIXTURE.replace('TRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha"', 'TRNM_STORAGE_TEST_PRODUCER_COMMIT="$other_sha"'),
            FIXTURE.replace('TRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree"', 'TRNM_STORAGE_TEST_PRODUCER_TREE="$other_tree"'),
            FIXTURE.replace('TRNM_STORAGE_IMPORT_EVIDENCE_ROOT="$evidence_absolute/storage-v4-import-packets"', 'TRNM_STORAGE_IMPORT_EVIDENCE_ROOT="$unretained"'),
        ):
            with self.subTest(changed=changed):
                self.reject(changed)

    def test_homogeneous_lane_and_app_marker_cannot_be_missing_relabelled_or_unsealed(self) -> None:
        for changed in (
            FIXTURE.replace(DUPLICATES, ""), FIXTURE.replace(DUPLICATES, DUPLICATES + DUPLICATES),
            FIXTURE.replace(DUPLICATES, "\n".join("# " + line for line in DUPLICATES.splitlines()) + "\n"),
            FIXTURE.replace(MODULE.STORAGE_DUPLICATE_SELECTOR, "filter_that_runs_zero_tests"),
            FIXTURE.replace("nakama_duplicate_batches_skipped", "unchecked_duplicate_skip"),
            FIXTURE.replace(DUPLICATE_TERMINAL, ""),
            FIXTURE.replace('"storage_homogeneous_batches":true', '"storage_homogeneous_batches":false'),
            FIXTURE.replace('"storage_homogeneous_app":true', '"storage_homogeneous_app":false'),
            FIXTURE.replace(HOMOGENEOUS_APP_MARKER, ""),
            FIXTURE.replace("write_occurrences=13 rollback_cases=3", "write_occurrences=12 rollback_cases=3"),
            FIXTURE.replace('find "$evidence" -type f', 'find "$evidence" -type f ! -name storage-duplicate-batches.log'),
        ):
            with self.subTest(changed=changed):
                self.reject(changed)
        for guard in DUPLICATE_GUARDS.splitlines():
            self.reject(FIXTURE.replace(guard + "\n", "", 1))
        for environment in DUPLICATE_ENVIRONMENT.splitlines():
            self.reject(FIXTURE.replace(DUPLICATES, DUPLICATES.replace(environment + "\n", "", 1)))

    def test_homogeneous_source_lock_and_policy_remain_bounded_truthful_and_bsd_attributed(self) -> None:
        contract = json.loads((ROOT / "contracts/storage/nakama-http-storage-v1.json").read_text())
        source_lock = json.loads((ROOT / "contracts/storage/nakama-sort-source-lock-v1.json").read_text())
        license_data = (ROOT / "third_party/go-sort/LICENSE").read_bytes()
        notice = (ROOT / "NOTICE").read_text()
        sorter = (ROOT / "crates/trnm-storage-core/src/nakama_sort.rs").read_text()
        def validate(c=contract, lock=source_lock, data=license_data, n=notice, sort=sorter):
            MODULE.validate_homogeneous_storage_contract(c, lock, data, n, sort)
        validate()
        for field, value in (("maximum_occurrences", 101), ("maximum_occurrences", True),
                             ("runtime_raw_owner_representation_parity", True), ("automatic_mutation_retry", True),
                             ("accepted", 0), ("accepted", True), ("write_ack_order", "sorted-execution-order"),
                             ("typed_mixed_duplicate_keys", "collapse"), ("batch_read_duplicate_keys", "allow"),
                             ("row_lock_policy", "prelock-all-unique-keys")):
            changed = json.loads(json.dumps(contract))
            changed["homogeneous_mutation_batches"][field] = value
            with self.subTest(field=field), self.assertRaises(SystemExit):
                validate(c=changed)
        for field, value in (("source_commit", "1" * 40), ("nakama_native_differential_accepted", True),
                             ("runtime_owner_representation_parity", 0), ("license_path", "NOTICE")):
            changed = {**source_lock, field: value}
            with self.subTest(lock_field=field), self.assertRaises(SystemExit):
                validate(lock=changed)
        for field, value in (("sha256", "1" * 64), ("git_blob_sha1", "2" * 40),
                             ("size", True), ("downloaded", 1)):
            changed = json.loads(json.dumps(source_lock)); changed["files"][2][field] = value
            with self.subTest(pin_field=field), self.assertRaises(SystemExit):
                validate(lock=changed)
        for changed in (license_data[:-1], license_data + b"tamper", b"BSD license"):
            with self.assertRaises(SystemExit): validate(data=changed)
        for marker in ("Copyright 2009", "Copyright 2022", "// Modified:", "if data.len() > 100"):
            with self.subTest(marker=marker), self.assertRaises(SystemExit):
                validate(sort=sorter.replace(marker, "removed", 1))
        with self.assertRaises(SystemExit): validate(n=notice.replace("Copyright 2009 and 2022", "Copyright omitted"))

    def test_homogeneous_source_seam_requires_occurrences_fresh_acl_and_original_receipts(self) -> None:
        inputs = [(ROOT / path).read_text() for path in (
            "crates/trnm-storage-core/src/lib.rs",
            "crates/trnm-persistence-pg/src/storage_parts/01_repository.rs",
            "crates/trnm-server/src/runtime/storage_api.rs",
            "crates/trnm-server/src/runtime/pool.rs",
            "crates/trnm-server/src/runtime/retry.rs",
        )]
        MODULE.validate_homogeneous_storage_source(*inputs)
        for index, marker in (
            (0, "nakama_sort::go1265_sort_ordinals"), (0, "receipts[ordinal] = Some(receipt)"),
            (1, "lock_storage_access(&mut transaction, operation.key())?"), (1, "if kind.is_none() {"),
            (1, "load_for_update(&mut transaction, operation.key(), self.profile)?"),
            (2, "repository.apply_storage_batch_nakama("),
            (3, "repository.apply_storage_batch_nakama_with_metadata("),
            (4, "apply_storage_batch_nakama(actor, operations, updated_at_ms, kind)"),
        ):
            changed = inputs.copy(); changed[index] = changed[index].replace(marker, "removed_homogeneous_seam")
            with self.subTest(index=index, marker=marker), self.assertRaises(SystemExit):
                MODULE.validate_homogeneous_storage_source(*changed)

        changed = inputs.copy(); changed[1] += "\nlock_nakama_storage_key(&mut transaction, &key)?;\n"
        with self.assertRaises(SystemExit): MODULE.validate_homogeneous_storage_source(*changed)


    def test_write_tail_seventh_marker_and_five_case_guard_are_required(self) -> None:
        self.assertEqual(len(MODULE.STORAGE_DUPLICATE_MARKERS), 7)
        self.assertEqual(MODULE.STORAGE_DUPLICATE_MARKERS[-1], (
            "nakama_write_tail_drain_executed",
            " held_wait_cases=2 early_reject_cases=3 fields=15",
        ))
        guard = ('grep -Fxq "nakama_write_tail_drain_executed profile=${profile} '
                 'held_wait_cases=2 early_reject_cases=3 fields=15" '
                 '"$evidence/storage-duplicate-batches.log"\n')
        unique = ('test "$(grep -Ec \'^nakama_write_tail_drain_executed \' '
                  '"$evidence/storage-duplicate-batches.log")" -eq 1\n')
        for changed in (
            FIXTURE.replace(guard, ""), FIXTURE.replace(unique, ""),
            FIXTURE.replace(guard, guard + guard),
            FIXTURE.replace("held_wait_cases=2", "held_wait_cases=1"),
            FIXTURE.replace("early_reject_cases=3", "early_reject_cases=4"),
            FIXTURE.replace('"storage_write_tail_drain":true', '"storage_write_tail_drain":false'),
            FIXTURE.replace('"storage_write_tail_drain":true,', ""),
        ):
            self.assertNotEqual(changed, FIXTURE)
            self.reject(changed)

    def test_write_tail_candidate_scope_and_typed_error_source_cannot_drift(self) -> None:
        repository = (ROOT / "crates/trnm-persistence-pg/src/storage_parts/01_repository.rs").read_text()
        pool_base = (ROOT / "crates/trnm-persistence-pg/src/pool_parts/base.rs").read_text()
        fixture = (ROOT / "crates/trnm-persistence-pg/tests/storage_duplicate_batches.rs").read_text()
        MODULE.validate_nakama_write_tail_source(repository, pool_base, fixture)
        for before, after in (
            ("first_write_rejection.get_or_insert(rejection)", "first_write_rejection = Some(rejection)"),
            ("if kind == Some(NakamaBatchKind::Write)", "if kind.is_some()"),
            ("Err(rejection) if rejection == write_permission_error()", "Err(rejection)"),
            ("VersionCheck::MustNotExist => Err(error(", "VersionCheck::MustNotExist => Ok(WriteStepValidation::Semantic(error("),
            ("validate_native_condition(", "removed_native_condition("),
            ("transaction.rollback().err().map(map_postgres_error)", "None"),
            ("if rollback_error.is_some() {", "if false {"),
            ("self.client.retire();", ""),
        ):
            self.assertIn(before, repository)
            with self.subTest(marker=before), self.assertRaises(SystemExit):
                MODULE.validate_nakama_write_tail_source(repository.replace(before, after), pool_base, fixture)
        for before in (
            "pub(crate) fn retire(&self)", "retired.store(true, Ordering::Release)",
            "connection.retired.load(Ordering::Acquire) || self.inner.has_broken(&mut connection.client)",
            "Self::Direct(_) => None",
        ):
            self.assertIn(before, pool_base)
            with self.subTest(pool_marker=before), self.assertRaises(SystemExit):
                MODULE.validate_nakama_write_tail_source(repository, pool_base.replace(before, "removed_retirement_seam"), fixture)
        for before in (
            "write_tail_drain::exercise(&url, profile, &collection);",
            "Case::AclWait,", "Case::ExactWait,", "Case::CreateOnlyStops,",
            "Case::AclNativeStops,", "Case::NativeStops,",
            "held_wait_cases=2 early_reject_cases=3 fields=15",
        ):
            self.assertIn(before, fixture)
            with self.subTest(fixture_marker=before), self.assertRaises(SystemExit):
                MODULE.validate_nakama_write_tail_source(repository, pool_base, fixture.replace(before, "removed_five_case_seam"))
        contract = json.loads((ROOT / "contracts/storage/nakama-http-storage-v1.json").read_text())
        lock = json.loads((ROOT / "contracts/storage/nakama-sort-source-lock-v1.json").read_text())
        license_data = (ROOT / "third_party/go-sort/LICENSE").read_bytes()
        notice = (ROOT / "NOTICE").read_text()
        sorter = (ROOT / "crates/trnm-storage-core/src/nakama_sort.rs").read_text()
        for field, value in (
            ("write_tail_semantic_conditions", ["all-storage-domain-errors"]),
            ("write_tail_primary_error", "always-overrides-outer-deadline"),
            ("write_tail_hard_errors", "continue-after-any-native-error"),
            ("write_tail_cleanup", "rollback-always-confirmed"),
            ("write_tail_rollback_failure", "direct-client-disabled"),
            ("native_write_prequeue_qualified", True),
            ("native_preparation_query_group_qualified", True),
            ("native_failing_occurrence_jsonb_bind_priority_qualified", True),
            ("conditional_exact_lock_footprint_qualified", True),
            ("native_isolation_retry_qualified", True),
        ):
            changed = json.loads(json.dumps(contract))
            changed["homogeneous_mutation_batches"][field] = value
            with self.subTest(field=field), self.assertRaises(SystemExit):
                MODULE.validate_homogeneous_storage_contract(changed, lock, license_data, notice, sorter)


class ActualNativeLaneShellTests(unittest.TestCase):
    """Run production shell guards with mock Cargo logs, never a database."""

    def test_import_directories_survive_cargo_crate_working_directory(self) -> None:
        setup = ACTUAL_HARNESS[ACTUAL_HARNESS.index("evidence_root="):
                               ACTUAL_HARNESS.index("server_port=")]
        block = ACTUAL_HARNESS[ACTUAL_HARNESS.index('begin_stage storage-v4-import "'):
                               ACTUAL_HARNESS.index("begin_stage storage-duplicate-batches")]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            crate = root / "crates" / "mock-persistence"
            crate.mkdir(parents=True)
            probe = root / "probe.py"
            probe.write_text('''import os
from pathlib import Path
source = Path(os.environ["TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY"])
target = Path(os.environ["TRNM_STORAGE_IMPORT_EVIDENCE_ROOT"])
assert source.is_absolute() and target.is_absolute()
assert source.parent == target.parent == Path(os.environ["EXPECTED_EVIDENCE"])
for name in ["initial-schema.sql", "core-storage.go", "LICENSE"]:
    assert (source / name).read_bytes() == b"synthetic path-boundary fixture"
target.mkdir()
(target / "observed.txt").write_text("synthetic-only; no database credit")
print("storage_v4_import_live_executed profile=postgresql")
print("test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s")
''')
            bootstrap = '''set -euo pipefail
profile=postgresql
database_url=synthetic-database-configuration
candidate_sha=1111111111111111111111111111111111111111
candidate_tree=2222222222222222222222222222222222222222
begin_stage() { return 0; }
cargo() { (cd "$MOCK_CRATE"; python3 "$MOCK_PROBE"); }
'''
            materialize = '''mkdir -p "$evidence/storage-source-upstream"
for member in initial-schema.sql core-storage.go LICENSE; do
  printf 'synthetic path-boundary fixture' > "$evidence/storage-source-upstream/$member"
done
'''
            for configured in ("run/evidence with spaces", str(root / "absolute evidence")):
                expected = (root / configured / "postgresql").resolve()
                env = {**os.environ, "TRNM_EVIDENCE_ROOT": configured,
                       "MOCK_CRATE": str(crate), "MOCK_PROBE": str(probe),
                       "EXPECTED_EVIDENCE": str(expected)}
                with self.subTest(configured=configured):
                    script = bootstrap + setup + materialize + block
                    result = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                            text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertTrue((expected / "storage-v4-import-packets/observed.txt").is_file())
                    self.assertFalse((crate / "run").exists())
                    # Reproduce the original relative paths against the same
                    # cwd transition, without changing archived file names.
                    old = script.replace('$evidence_absolute/storage-', '$evidence/storage-')
                    failure = subprocess.run(["bash", "-c", old], cwd=root, env=env,
                                             text=True, capture_output=True, timeout=10)
                    if not Path(configured).is_absolute():
                        self.assertNotEqual(failure.returncode, 0)
                        self.assertNotIn("storage_v4_import_live_executed", failure.stdout)
                    else:
                        self.assertEqual(failure.returncode, 0, failure.stdout + failure.stderr)

    def run_block(self, block: str, lines: list[str], status: int = 0) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "input.log").write_text("\n".join(lines) + "\n")
            bootstrap = '''set -euo pipefail
profile=postgresql
evidence=$MOCK_ROOT
evidence_absolute=$MOCK_ROOT
database_url=synthetic-database-configuration
candidate_sha=1111111111111111111111111111111111111111
candidate_tree=2222222222222222222222222222222222222222
begin_stage() { return 0; }
cargo() { cat "$MOCK_ROOT/input.log"; return "$MOCK_CARGO_STATUS"; }
'''
            finish = "printf 'synthetic-guards-passed-no-live-credit\\n'\n"
            return subprocess.run(["bash", "-c", bootstrap + block + finish],
                                  env={**os.environ, "MOCK_ROOT": str(root), "MOCK_CARGO_STATUS": str(status)},
                                  text=True, capture_output=True, timeout=10)

    def test_real_schema_block_rejects_missing_wrong_duplicate_skipped_and_bad_terminal_cases(self) -> None:
        block = ACTUAL_HARNESS[ACTUAL_HARNESS.index("begin_stage schema-upgrade"):
                               ACTUAL_HARNESS.index("begin_stage schema-verification")]
        markers = [f"schema_v3_{family}_executed profile=postgresql extra_cases={count}"
                   for family, count in SCHEMA_FAMILIES.items()]
        lines = markers + ["test result: ok. 6 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"]
        positive = self.run_block(block, lines)
        self.assertEqual(positive.returncode, 0, positive.stderr)
        for family_marker in markers:
            for changed in (
                [line for line in lines if line != family_marker],
                [line.replace(family_marker, family_marker.rsplit("=", 1)[0] + "=0") for line in lines],
                lines + [family_marker],
                [line.replace(family_marker, "test misleading-prefix ... " + family_marker) for line in lines],
            ):
                with self.subTest(family=family_marker):
                    result = self.run_block(block, changed)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("synthetic-guards-passed-no-live-credit", result.stdout)
        for changed in (
            lines + ["developer-only live test skip"],
            lines + ["schema_v3_shapes_skipped profile=postgresql"],
            [line.replace("6 passed", "0 passed") for line in lines],
            [line.replace("0 failed", "1 failed") for line in lines],
            [line.replace("0 ignored", "1 ignored") for line in lines],
        ):
            self.assertNotEqual(self.run_block(block, changed).returncode, 0)
        self.assertEqual(self.run_block(block, lines, status=37).returncode, 37)

    def test_real_native_jsonb_block_requires_exact_test_and_unique_execution(self) -> None:
        block = ACTUAL_HARNESS[ACTUAL_HARNESS.index("begin_stage storage-native-jsonb"):
                               ACTUAL_HARNESS.index("begin_stage storage-v4-acl")]
        marker = "storage_native_jsonb_live_executed profile=postgresql"
        lines = [marker, "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"]
        self.assertEqual(self.run_block(block, lines).returncode, 0)
        for changed in (
            lines[1:], lines + [marker], lines + ["storage_native_jsonb_live_skipped: optional database absent"],
            [line.replace("1 passed", "0 passed") for line in lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in lines],
        ):
            result = self.run_block(block, changed)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("synthetic-guards-passed-no-live-credit", result.stdout)

    def test_real_import_block_rejects_empty_missing_duplicate_skip_wrong_profile_and_terminal(self) -> None:
        block = ACTUAL_HARNESS[ACTUAL_HARNESS.index('begin_stage storage-v4-import "'):
                               ACTUAL_HARNESS.index("begin_stage storage-duplicate-batches")]
        marker = "storage_v4_import_live_executed profile=postgresql"
        lines = [marker, "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"]
        self.assertEqual(self.run_block(block, lines).returncode, 0)
        for changed in (
            [], lines[1:], lines + [marker], lines + [lines[-1]],
            lines + ["storage_v4_import_live_skipped"],
            lines + ["test result: FAILED. 0 passed; 1 failed; 0 ignored;"],
            [line.replace("1 passed", "0 passed") for line in lines],
            [line.replace("0 ignored", "1 ignored") for line in lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in lines],
            ["test misleading-prefix ... " + marker, lines[-1]],
        ):
            with self.subTest(lines=changed):
                result = self.run_block(block, changed)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("synthetic-guards-passed-no-live-credit", result.stdout)
        self.assertEqual(self.run_block(block, lines, status=37).returncode, 37)


    def test_real_duplicate_block_rejects_every_missing_wrong_extra_skipped_and_failed_observation(self) -> None:
        block = ACTUAL_HARNESS[ACTUAL_HARNESS.index("begin_stage storage-duplicate-batches"):
                               ACTUAL_HARNESS.index("begin_stage schema-upgrade")]
        markers = [f"{marker} profile=postgresql{suffix}" for marker, suffix in MODULE.STORAGE_DUPLICATE_MARKERS]
        terminal = "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"
        lines = markers + [terminal]
        self.assertEqual(self.run_block(block, lines).returncode, 0)
        for marker in markers:
            for changed in (
                [line for line in lines if line != marker], lines + [marker],
                lines + [marker.replace("profile=postgresql", "profile=cockroachdb")],
                [line.replace(marker, "test injected-prefix ... " + marker) for line in lines],
            ):
                with self.subTest(marker=marker, lines=changed):
                    result = self.run_block(block, changed)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("synthetic-guards-passed-no-live-credit", result.stdout)
        for changed in (
            [], lines + [terminal], lines + ["test result: FAILED. 0 passed; 1 failed; 0 ignored;"],
            lines + ["nakama_duplicate_batches_skipped reason=optional_database_absent"],
            [line.replace("1 passed", "0 passed") for line in lines],
            [line.replace("0 failed", "1 failed") for line in lines],
            [line.replace("0 ignored", "1 ignored") for line in lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in lines],
            [line.replace("held_wait_cases=2", "held_wait_cases=1") for line in lines],
            [line.replace("early_reject_cases=3", "early_reject_cases=2") for line in lines],
        ):
            with self.subTest(lines=changed):
                self.assertNotEqual(self.run_block(block, changed).returncode, 0)
        self.assertEqual(self.run_block(block, lines, status=37).returncode, 37)


    def test_real_canonical_block_requires_additional_homogeneous_marker_without_replacing_old_cases(self) -> None:
        block = ACTUAL_HARNESS[ACTUAL_HARNESS.index("begin_stage canonical-storage-app"):
                               ACTUAL_HARNESS.index("begin_stage nakama-client-list-projection")]
        app = "storage_homogeneous_app_executed profile=postgresql write_occurrences=13 rollback_cases=3"
        lines = [
            "canonical_storage_api_live_executed profile=postgresql",
            "storage_opaque_conditions_live_executed profile=postgresql write_cases=15 delete_cases=18 batch_cases=2",
            "storage_jsonb_v3_live_executed profile=postgresql history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3",
            "storage_jsonb_v3_native_inputs profile=postgresql condition_sql=rejected payload_sql=rejected compatibility_credit=false",
            app, "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s",
        ]
        self.assertEqual(self.run_block(block, lines).returncode, 0)
        for changed in (
            [line for line in lines if line != app], lines + [app],
            lines + [app.replace("profile=postgresql", "profile=cockroachdb")],
            [line.replace(app, "test injected-prefix ... " + app) for line in lines],
            [line.replace("write_occurrences=13", "write_occurrences=12") for line in lines],
            [line.replace("write_cases=15 delete_cases=18 batch_cases=2", "write_cases=0 delete_cases=0 batch_cases=0") for line in lines],
            lines + ["canonical_storage_api_live_skipped"],
        ):
            result = self.run_block(block, changed)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("synthetic-guards-passed-no-live-credit", result.stdout)
        self.assertEqual(self.run_block(block, lines, status=37).returncode, 37)


class StorageV3LivePacketContractTests(unittest.TestCase):
    """Actual validator against archived bytes, never a live database packet."""

    COMMIT = "1" * 40
    TREE = "2" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = "postgresql"
        self.summary = {
            "schema": "trillionnium.server-live-evidence.v1",
            "repository": "TrillionniumFoundation/TrillionniumGame",
            "profile": self.profile, "commit": self.COMMIT, "tree": self.TREE,
            **{field: True for field in (
                "check_config", "fresh_migration", "nakama_client_list_projection", "storage_occ_precedence",
                "raw_version_conditions", "storage_timestamps", "schema_upgrade", "storage_jsonb_v3_projection",
                "storage_native_jsonb", "storage_v4_acl", "storage_v4_import",
                "storage_homogeneous_batches", "storage_homogeneous_app", "storage_write_tail_drain",
                "health_ready", "unauthenticated_mutation_rejected", "http_bootstrap_commit_duplicate_conflict",
                "websocket_json_commit", "response_loss_exact_receipt_replay", "authenticated_drain",
                "process_restart_exact_receipt_replay",
            )},
            **{field: 3 for field in (
                "entity_revision",
                "event_sequence", "command_receipts", "events", "outbox_intents",
            )},
            **{field: 4 for field in ("schema_version", "storage_writer_epoch", "authoritative_migrations_count")},
            "storage_jsonb_v3_cases": {"history": 6, "opaque_success": 4, "no_op": 2, "resource": 1, "native_input": 3},
            "schema_v3_extra_cases": 41, "schema_v3_case_families": SCHEMA_FAMILIES.copy(),
            **{field: False for field in (
                "production_pitr", "multi_node", "wire_compatible", "compatibility_credit", "accepted", "production_ready",
            )},
        }
        self.lock = json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_text())
        self.identity = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": self.profile,
                         "schema_version": 4, "storage_writer_epoch": 4, "table_count": 12,
                         "digest_algorithm": "ordered-path-git-blob-sha256.v1",
                         "chain_digest": self.chain_digest(self.profile),
                         "migration_applied": False, "applied_steps": 0,
                         "source_commit": self.COMMIT, "upgrade_source_commit": self.COMMIT,
                         "v2_apply_source_commit": self.COMMIT, "v3_apply_source_commit": self.COMMIT, "compatibility_credit": False}
        archived = self.root / "authoritative-migrations"
        archived.mkdir()
        files = []
        for entry in self.lock["profiles"][self.profile]["ordered_files"]:
            data = (ROOT / entry["path"]).read_bytes()
            path = archived / Path(entry["path"]).name
            path.write_bytes(data)
            files.append({"path": entry["path"], "archive_path": str(path.relative_to(self.root)),
                          "git_blob_sha1": entry["git_blob_sha1"], "sha256": hashlib.sha256(data).hexdigest(),
                          "size_bytes": len(data)})
        self.manifest = {"schema": "trillionnium.server-live-schema-source.v1", "profile": self.profile,
                         "schema_version": 4, "storage_writer_epoch": 4, "ordered_files": files,
                         "compatibility_credit": False}
        for name, value in (("summary.json", self.summary), ("schema-identity.json", self.identity),
                            ("migration-lock.json", self.lock), ("authoritative-migrations.json", self.manifest)):
            self.write_json(name, value)
        self.log_lines = [
            "canonical_storage_api_live_executed profile=postgresql",
            "storage_opaque_conditions_live_executed profile=postgresql write_cases=15 delete_cases=18 batch_cases=2",
            "storage_jsonb_v3_live_executed profile=postgresql history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3",
            "storage_jsonb_v3_native_inputs profile=postgresql condition_sql=rejected payload_sql=rejected compatibility_credit=false",
            "storage_homogeneous_app_executed profile=postgresql write_occurrences=13 rollback_cases=3",
            "test result: ok. 1 passed; 0 failed; 0 ignored; 99 filtered out; finished in 0.01s",
        ]
        self.write_log(self.log_lines)
        self.schema_lines = [f"schema_v3_{family}_executed profile=postgresql extra_cases={count}"
                             for family, count in SCHEMA_FAMILIES.items()] + [
            "test result: ok. 6 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"
        ]
        self.native_lines = ["storage_native_jsonb_live_executed profile=postgresql",
                             "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"]
        self.write_named_log("schema-upgrade.log", self.schema_lines)
        self.write_named_log("storage-native-jsonb.log", self.native_lines)
        self.v4_acl_lines = ["storage_v4_acl_live_executed profile=postgresql",
                             "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"]
        self.write_named_log("storage-v4-acl.log", self.v4_acl_lines)
        self.v4_import_lines = ["storage_v4_import_live_executed profile=postgresql",
                                "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"]
        self.write_named_log("storage-v4-import.log", self.v4_import_lines)
        self.duplicate_lines = [f"{marker} profile=postgresql{suffix}" for marker, suffix in MODULE.STORAGE_DUPLICATE_MARKERS] + [
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out; finished in 0.01s"
        ]
        self.write_named_log(MODULE.STORAGE_DUPLICATE_LOG, self.duplicate_lines)
        # Synthetic authority bodies only exercise the real annex hash/path
        # validator without network acquisition. Production has no override.
        authority, exporter, query = MODULE.storage_import_source_authority()
        annex = self.root / "storage-source-upstream"
        annex.mkdir()
        members = []
        for name, source_path, _, _, _ in authority.MEMBERS:
            data = ("synthetic source annex, no oracle credit: " + name).encode()
            (annex / name).write_bytes(data)
            members.append((name, source_path, len(data), hashlib.sha256(data).hexdigest(),
                            hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()))
        self.source_authority = SimpleNamespace(MEMBERS=tuple(members), COMMIT=authority.COMMIT,
                                               TREE=authority.TREE, validate_bytes=authority.validate_bytes)
        patcher = mock.patch.object(MODULE, "storage_import_source_authority",
                                    return_value=(self.source_authority, exporter, query))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.import_source = MODULE.write_storage_import_source_archive(
            self.root, profile=self.profile, commit=self.COMMIT, tree=self.TREE)
        self.write_native_fixture()

    def write_native_fixture(self, *, total: int = 8, page_rows: int = 2) -> None:
        """Synthetic archived observations; this never executes or credits SQL.

        Use the public packet/frame formats independently of the validator.
        Fault tests below rewrite hashes too, so self-consistent JSON reports
        cannot hide changed native tuples, journal state, or producer identity.
        """
        self.assertTrue(1 <= total <= 8 and 1 <= page_rows <= 100)
        page_count = (total + page_rows - 1) // page_rows
        native = self.root / "storage-v4-import-packets"
        if native.exists():
            shutil.rmtree(native)
        packet = native / "packet"
        for name in ("upstream", "producer-source", "values"):
            (packet / name).mkdir(parents=True)
        authority, exporter, query = MODULE.storage_import_source_authority()
        for member in authority.MEMBERS:
            (packet / "upstream" / member[0]).write_bytes((self.root / "storage-source-upstream" / member[0]).read_bytes())
        (packet / "producer-source/exporter.rs").write_bytes(exporter)
        (packet / "source-query.sql").write_bytes(query)
        credit = {"compatibility_credit": False, "production_ready": False, "full_nakama_replacement": False}
        source = {"repository": "heroiclabs/nakama", "commit": authority.COMMIT, "tree": authority.TREE,
                  "profile": self.profile, "server_version": "synthetic-control-fixture",
                  "database_identity": "owned_synthetic_source", "snapshot_identity": "synthetic-snapshot-no-live-credit",
                  "isolation": "repeatable-read-read-only" if self.profile == "postgresql" else "serializable-read-only",
                  "execution_class": "native-source-ddl-fixture", "whole_table": True, "owner_references_valid": True}
        producer = {"repository": "TrillionniumFoundation/TrillionniumGame", "commit": self.COMMIT,
                    "tree": self.TREE, "source_sha256": hashlib.sha256(exporter).hexdigest(),
                    "binary_sha256": hashlib.sha256(b"synthetic executable, no build credit").hexdigest(),
                    "execution_id": "synthetic-native-control-fixture"}
        columns = [("collection", "varchar", 128, None), ("key", "varchar", 128, None),
                   ("user_id", "uuid", None, None), ("value", "jsonb", None, "'{}'::jsonb" if self.profile == "postgresql" else "'{}'"),
                   ("version", "varchar", 32, None), ("read", "int2", None, "1"), ("write", "int2", None, "1"),
                   ("create_time", "timestamptz", None, "now()"), ("update_time", "timestamptz", None, "now()")]
        constraints = []
        definitions = [("storage_pkey", "p", "PRIMARY KEY (collection, key, user_id)" if self.profile == "postgresql"
                        else "PRIMARY KEY (collection ASC, key ASC, user_id ASC)", [1, 2, 3]),
                       ("storage_user_id_fkey", "f", "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE", [3]),
                       ("storage_read_check" if self.profile == "postgresql" else "check_read", "c", "CHECK ((read >= 0))", [6]),
                       ("storage_write_check" if self.profile == "postgresql" else "check_write", "c", "CHECK ((write >= 0))", [7])]
        for name, kind, definition, indices in definitions:
            constraints.append({"name": name, "kind": kind, "validated": True, "definition": definition, "columns": indices,
                                "parent_columns": [1] if kind == "f" else None, "parent_schema": "public" if kind == "f" else None,
                                "parent_table": "users" if kind == "f" else None, "delete_action": "c" if kind == "f" else " "})
        self.write_native_json("packet/source-catalog.json", {
            "schema": "trillionnium.nakama-storage-source-catalog.v1", "profile": self.profile,
            "namespace": "public", "table": "storage", "table_type": "BASE TABLE", "table_kind": "r",
            "snapshot_identity": source["snapshot_identity"], "columns": [
                {"name": name, "udt": udt, "nullable": False, "character_maximum_length": size, "default_expression": default}
                for name, udt, size, default in columns], "constraints": constraints,
            "collation_binding": ["postgresql", "UTF8", "c", "C.UTF-8", "C.UTF-8", "default", "d", "true"]
                if self.profile == "postgresql" else ["cockroachdb", "UTF8", "uncollated"],
            "primary_key_columns": ["collection", "key", "user_id"], "owner_foreign_key_columns": ["user_id"],
            "owner_foreign_key_table": "users", "owner_foreign_key_parent_schema": "public",
            "owner_foreign_key_parent_columns": ["id"], "owner_foreign_key_delete_cascade": True,
            "owner_foreign_key_validated": True, "read_nonnegative_check_validated": True, "write_nonnegative_check_validated": True})
        # Already rendered native strings: never parse or normalize payloads.
        values = ["null", '"source string"', "true", '[null, true, 1, {"x": 2}]', "1200",
                  '{"a": 2, "b": 100}', '"escape 界 \\n"', "{}"]
        tokens = ["", "UPPERCASE", "*", "界" * 32, "not-hex", "legacytoken", "opaque\tversion", "opaque"]
        permissions = [0, 1, 2, 3, 32767, 2, 0, 32767]
        rows = []
        records = []
        for index, (value, token) in enumerate(zip(values[:total], tokens[:total])):
            row = {"collection": "" if index < 2 else "集合", "key": "" if index == 0 else f"key-{index}\n界",
                   "user_id": "11111111-1111-1111-1111-111111111111", "native_text": value,
                   "public_version": token, "read": permissions[index], "write": 32767 if index == 6 else index % 4,
                   "create_time": {"seconds": -1, "nanos": 123456000},
                   "update_time": {"seconds": 951827696, "nanos": 654321000}}
            if index == 7:
                row["create_time"], row["update_time"] = row["update_time"], row["create_time"]
            rows.append(row)
            data = value.encode()
            path = f"values/{index:08}.json"
            (packet / path).write_bytes(data)
            records.append({"ordinal": index, **{key: item for key, item in row.items() if key != "native_text"},
                            "value_path": path, "value_sha256": hashlib.sha256(data).hexdigest(), "value_bytes": len(data)})
        (packet / "rows.ndjson").write_bytes(b"".join(self.json_bytes(record) + b"\n" for record in records))
        members = [{"path": str(path.relative_to(packet)), "bytes": len(path.read_bytes()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path in sorted(packet.rglob("*")) if path.is_file()]
        self.write_native_json("packet/manifest.json", {"schema": "trillionnium.nakama-storage-native-export.v1",
            "project_id": "trillionnium-game", "completed": True, "source": source, "producer": producer,
            "total_rows": total, "page_rows": page_rows, "members": members})
        manifest = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
        self.write_native_json("packet/source-receipt.json", {"schema": "trillionnium.nakama-storage-source-receipt.v1",
            "manifest_sha256": manifest, "producer": producer, "source": source, "row_count": total,
            "completed": True, **credit})
        custody = hashlib.sha256((packet / "source-receipt.json").read_bytes()).hexdigest()
        self.write_native_json("custody-anchors.json", {"schema": "trillionnium.storage-import-native-custody-anchors.v1",
            "profile": self.profile, "producer_commit": self.COMMIT, "producer_tree": self.TREE,
            "producer_source_sha256": producer["source_sha256"], "producer_binary_sha256": producer["binary_sha256"],
            "execution_id": producer["execution_id"], "manifest_sha256": manifest, "receipt_sha256": custody, **credit})
        self.write_native_json("exporter-summary.json", {"schema": "trillionnium.storage-export-summary.v1",
            "profile": self.profile, "row_count": total, "page_count": page_count, "manifest_sha256": manifest,
            "receipt_sha256": custody, "producer_source_sha256": producer["source_sha256"],
            "producer_binary_sha256": producer["binary_sha256"], "execution_class": source["execution_class"], **credit})
        targets = [{**row, "projection_sha256": hashlib.sha256(row["native_text"].encode()).hexdigest(),
                    "value_origin": "nakama-export-unknown-request", "source_manifest_sha256": manifest,
                    "raw_value_is_null": True, "raw_digest_is_null": True, "updated_at_ms": 2468} for row in rows]
        self.write_native_json("source-rows.json", rows)
        self.write_native_json("target-rows.json", targets)
        self.write_native_json("native-rows.json", {"schema": "trillionnium.storage-import-native-rows.v1",
            "profile": self.profile, "source_path": "source-rows.json", "target_path": "target-rows.json",
            "source_sha256": hashlib.sha256((native / "source-rows.json").read_bytes()).hexdigest(),
            "target_sha256": hashlib.sha256((native / "target-rows.json").read_bytes()).hexdigest(),
            "exact_source_projection_and_metadata_preserved": True, "unknown_request_witnesses": True, **credit})
        def u64(value: int) -> bytes:
            return value.to_bytes(8, "big")
        def frame(value: str) -> bytes:
            data = value.encode()
            return u64(len(data)) + data
        hashes = []
        for record in records:
            data = b"trillionnium.storage-import-row.v1\0" + u64(record["ordinal"])
            data += b"".join(frame(record[field]) for field in ("collection", "key", "user_id", "public_version", "value_sha256"))
            data += b"".join(record[field].to_bytes(2, "big", signed=True) for field in ("read", "write"))
            for field in ("create_time", "update_time"):
                data += record[field]["seconds"].to_bytes(8, "big", signed=True) + record[field]["nanos"].to_bytes(4, "big")
            hashes.append(hashlib.sha256(data).digest())
        inventory = hashlib.sha256(b"trillionnium.storage-import-inventory.v1\0" + b"".join(hashes)).digest()
        guard = hashlib.sha256(b"synthetic separate target scope and schema/role binding").digest()
        prefix = hashlib.sha256(b"trillionnium.storage-import-empty-prefix.v1\0" + bytes.fromhex(manifest)
            + bytes.fromhex(custody) + inventory + guard + frame(self.profile) + frame(source["snapshot_identity"])
            + (2468).to_bytes(8, "big", signed=True) + u64(total) + u64(page_count)).digest()
        pages = []
        for index in range(page_count):
            first = index * page_rows
            count = min(page_rows, total - first)
            digest = hashlib.sha256(b"trillionnium.storage-import-page.v1\0" + bytes.fromhex(manifest)
                + u64(index) + u64(count) + b"".join(u64(ordinal) + hashes[ordinal] for ordinal in range(first, first + count))).digest()
            prefix = hashlib.sha256(b"trillionnium.storage-import-page-prefix.v1\0" + prefix + digest
                + u64(index) + u64(first) + u64(count)).digest()
            pages.append({"manifest_sha256": manifest, "page_index": index, "first_ordinal": first, "row_count": count,
                          "page_sha256": digest.hex(), "prefix_sha256": prefix.hex(), "audit_at_ms": 2468})
        job = {"singleton": 1, "manifest_sha256": manifest, "custody_sha256": custody,
               "source_inventory_sha256": inventory.hex(), "target_schema_guard_sha256": guard.hex(),
               "prefix_sha256": prefix.hex(), "source_profile": self.profile, "source_snapshot": source["snapshot_identity"],
               "audit_at_ms": 2468, "total_rows": total, "total_pages": page_count, "next_page": page_count, "committed_rows": total, "status": 1}
        self.write_native_json("import-journal.json", {"schema": "trillionnium.storage-import-native-journal.v1",
            "profile": self.profile, "jobs": [job], "pages": pages, **credit})
        def progress(next_page: int, completed: bool) -> dict:
            return {"schema": "trillionnium.storage-import-progress.v1", "manifest_sha256": manifest,
                    "next_page": next_page, "total_pages": page_count, "total_rows": total,
                    "committed_rows": min(total, next_page * page_rows),
                    "completed": completed, "target_identity_classification": "native-system-database-and-external-scope"
                    if self.profile == "postgresql" else "namespace-and-external-scope-only", **credit}
        receipts = [{"schema": "trillionnium.storage-import-page-receipt.v1",
            **{key: value for key, value in page.items() if key not in ("manifest_sha256", "audit_at_ms")},
            "progress": progress(index + 1, False)} for index, page in enumerate(pages)]
        self.write_native_json("lifecycle.json", {"schema": "trillionnium.storage-import-native-lifecycle.v1",
            "profile": self.profile, "begin": progress(0, False), "first_page": receipts[0], "resumed": progress(1, False),
            "remaining_pages": receipts[1:], "finished": progress(page_count, True), "verified": progress(page_count, True), **credit})
        for name, applied, steps in (("target-schema-migrate.json", True, 4), ("target-schema-verify.json", False, 0)):
            self.write_native_json(name, {**self.identity, "profile": self.profile, "chain_digest": self.chain_digest(self.profile),
                "migration_applied": applied, "applied_steps": steps})

    @staticmethod
    def json_bytes(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    def write_native_json(self, name: str, value: object) -> None:
        (self.root / "storage-v4-import-packets" / name).write_bytes(self.json_bytes(value))

    def native_json(self, name: str) -> object:
        return json.loads((self.root / "storage-v4-import-packets" / name).read_bytes())

    def rehash_native_rows(self, name: str, value: object) -> None:
        self.write_native_json(name, value)
        report = self.native_json("native-rows.json")
        field = "source_sha256" if name == "source-rows.json" else "target_sha256"
        report[field] = hashlib.sha256((self.root / "storage-v4-import-packets" / name).read_bytes()).hexdigest()
        self.write_native_json("native-rows.json", report)

    def rehash_native_packet(self) -> None:
        """Refresh attacker-controlled hashes, never change observed tuples.

        Deliberately leave the journal/prefix observations unchanged. Packet
        row/value/producer checks run before those, independently of hashes.
        """
        native = self.root / "storage-v4-import-packets"
        manifest = self.native_json("packet/manifest.json")
        for member in manifest["members"]:
            data = (native / "packet" / member["path"]).read_bytes()
            member.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.write_native_json("packet/manifest.json", manifest)
        manifest_hash = hashlib.sha256((native / "packet/manifest.json").read_bytes()).hexdigest()
        receipt = self.native_json("packet/source-receipt.json")
        receipt["manifest_sha256"] = manifest_hash
        self.write_native_json("packet/source-receipt.json", receipt)
        receipt_hash = hashlib.sha256((native / "packet/source-receipt.json").read_bytes()).hexdigest()
        for name in ("custody-anchors.json", "exporter-summary.json"):
            value = self.native_json(name)
            value.update(manifest_sha256=manifest_hash, receipt_sha256=receipt_hash)
            self.write_native_json(name, value)
        rows = self.native_json("target-rows.json")
        for row in rows:
            row["source_manifest_sha256"] = manifest_hash
        self.rehash_native_rows("target-rows.json", rows)

    def test_v4_import_main_packet_rejects_self_consistent_truncation_and_temporary_page_geometry(self) -> None:
        for total, page_rows in ((1, 1), (6, 2), (8, 1), (8, 4)):
            with self.subTest(total=total, page_rows=page_rows):
                # Produce an entire mutually consistent packet, observations,
                # hash anchors, journal and lifecycle. Changing only a count
                # would fail an unrelated hash check and miss this regression.
                self.write_native_fixture(total=total, page_rows=page_rows)
                native = self.root / "storage-v4-import-packets"
                manifest = self.native_json("packet/manifest.json")
                anchors = self.native_json("custody-anchors.json")
                self.assertEqual(anchors["manifest_sha256"], hashlib.sha256((native / "packet/manifest.json").read_bytes()).hexdigest())
                self.assertEqual(anchors["receipt_sha256"], hashlib.sha256((native / "packet/source-receipt.json").read_bytes()).hexdigest())
                for member in manifest["members"]:
                    data = (native / "packet" / member["path"]).read_bytes()
                    self.assertEqual((member["bytes"], member["sha256"]), (len(data), hashlib.sha256(data).hexdigest()))
                journal = self.native_json("import-journal.json")
                pages = (total + page_rows - 1) // page_rows
                self.assertEqual(len(self.native_json("source-rows.json")), total)
                self.assertEqual(len(self.native_json("target-rows.json")), total)
                self.assertEqual(len(journal["pages"]), pages)
                self.assertEqual(journal["jobs"][0]["committed_rows"], total)
                self.assertEqual(journal["jobs"][0]["next_page"], pages)
                self.assertEqual(journal["jobs"][0]["prefix_sha256"], journal["pages"][-1]["prefix_sha256"])
                lifecycle = self.native_json("lifecycle.json")
                self.assertEqual(len(lifecycle["remaining_pages"]), pages - 1)
                self.assertEqual(lifecycle["verified"]["committed_rows"], total)
                self.assertIs(lifecycle["verified"]["completed"], True)
                with self.assertRaisesRegex(SystemExit, "exactly eight rows in four two-row pages"):
                    self.validate()
        self.write_native_fixture()
        self.assertTrue(self.validate()["storage_v4_import"])

    def test_v4_import_native_closed_inventory_requires_every_observation_and_preimage(self) -> None:
        native = self.root / "storage-v4-import-packets"
        members = [str(path.relative_to(native)) for path in native.rglob("*") if path.is_file()]
        for name in members:
            with self.subTest(missing=name):
                path = native / name
                data = path.read_bytes()
                path.unlink()
                self.reject()
                path.write_bytes(data)
        for name in ("unclaimed.json", "packet/values/extra.json"):
            with self.subTest(extra=name):
                (native / name).write_text("{}")
                self.reject()
                (native / name).unlink()
        path = native / "target-rows.json"
        data = path.read_bytes()
        path.unlink()
        path.symlink_to(native / "source-rows.json")
        self.reject()
        path.unlink()
        path.write_bytes(data)
        self.assertTrue(self.validate()["storage_v4_import"])

    def test_v4_import_native_rejects_self_consistent_tuple_changes(self) -> None:
        mutations = (("public_version", None), ("public_version", "not-the-empty-source-token"),
                     ("native_text", " null "), ("read", True), ("read", 32767),
                     ("write", 2), ("create_time", {"seconds": -1, "nanos": 123457000}),
                     ("update_time", {"seconds": 951827696, "nanos": 654321001}),
                     ("value_origin", "write-request-bytes"), ("raw_value_is_null", False),
                     ("raw_digest_is_null", 1), ("source_manifest_sha256", "a" * 64),
                     ("projection_sha256", "b" * 64), ("updated_at_ms", 2469))
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                rows = self.native_json("target-rows.json")
                original = rows[0][field]
                rows[0][field] = value
                self.rehash_native_rows("target-rows.json", rows)
                self.reject()
                rows[0][field] = original
                self.rehash_native_rows("target-rows.json", rows)
        for changed in (self.native_json("target-rows.json")[:-1],
                        self.native_json("target-rows.json") + self.native_json("target-rows.json")[:1],
                        [self.native_json("target-rows.json")[0]] * 8):
            with self.subTest(inventory=len(changed)):
                self.rehash_native_rows("target-rows.json", changed)
                self.reject()
                self.write_native_fixture()

    def test_v4_import_native_rejects_normalized_packet_payload_and_untyped_rows_after_rehash(self) -> None:
        native = self.root / "storage-v4-import-packets"
        for field, value in (("read", True), ("public_version", None), ("ordinal", False),
                             ("value_bytes", True), ("collection", []),
                             ("create_time", {"seconds": -1, "nanos": 123456001})):
            with self.subTest(field=field):
                records = [json.loads(line) for line in (native / "packet/rows.ndjson").read_bytes().splitlines()]
                records[0][field] = value
                (native / "packet/rows.ndjson").write_bytes(b"".join(self.json_bytes(row) + b"\n" for row in records))
                self.rehash_native_packet()
                self.reject()
                self.write_native_fixture()
        (native / "packet/values/00000005.json").write_text('{"b": 100, "a": 2}')
        records = [json.loads(line) for line in (native / "packet/rows.ndjson").read_bytes().splitlines()]
        data = (native / "packet/values/00000005.json").read_bytes()
        records[5].update(value_bytes=len(data), value_sha256=hashlib.sha256(data).hexdigest())
        (native / "packet/rows.ndjson").write_bytes(b"".join(self.json_bytes(row) + b"\n" for row in records))
        self.rehash_native_packet()
        self.reject()

    def test_v4_import_native_custody_binds_actual_candidate_source_profile_and_packet(self) -> None:
        for field, value in (("producer_commit", "3" * 40), ("producer_tree", "4" * 40),
                             ("producer_source_sha256", "a" * 64), ("producer_binary_sha256", "b" * 64),
                             ("execution_id", "different-native-run"), ("profile", "cockroachdb"),
                             ("manifest_sha256", "c" * 64), ("receipt_sha256", "d" * 64),
                             ("compatibility_credit", True), ("production_ready", 0)):
            with self.subTest(field=field):
                value_json = self.native_json("custody-anchors.json")
                original = value_json[field]
                value_json[field] = value
                self.write_native_json("custody-anchors.json", value_json)
                self.reject()
                value_json[field] = original
                self.write_native_json("custody-anchors.json", value_json)
        native = self.root / "storage-v4-import-packets"
        (native / "packet/producer-source/exporter.rs").write_bytes(b"changed producer implementation")
        self.rehash_native_packet()
        self.reject()

    def test_v4_import_native_journal_validates_every_page_and_full_final_state(self) -> None:
        mutations = (("custody_sha256", "a" * 64), ("source_inventory_sha256", "b" * 64),
                     ("target_schema_guard_sha256", "c" * 64), ("prefix_sha256", "d" * 64),
                     ("source_snapshot", "different-snapshot"), ("audit_at_ms", 2469),
                     ("total_rows", 7), ("total_pages", 3), ("next_page", 3),
                     ("committed_rows", 7), ("status", 0), ("singleton", True))
        for field, value in mutations:
            with self.subTest(job=field):
                journal = self.native_json("import-journal.json")
                original = journal["jobs"][0][field]
                journal["jobs"][0][field] = value
                self.write_native_json("import-journal.json", journal)
                self.reject()
                journal["jobs"][0][field] = original
                self.write_native_json("import-journal.json", journal)
        for field, value in (("page_index", 2), ("first_ordinal", 3), ("row_count", True),
                             ("page_sha256", "e" * 64), ("prefix_sha256", "f" * 64), ("audit_at_ms", 2467)):
            with self.subTest(page=field):
                journal = self.native_json("import-journal.json")
                original = journal["pages"][1][field]
                journal["pages"][1][field] = value
                self.write_native_json("import-journal.json", journal)
                self.reject()
                journal["pages"][1][field] = original
                self.write_native_json("import-journal.json", journal)
        for replacement in ([], self.native_json("import-journal.json")["pages"][:-1],
                            list(reversed(self.native_json("import-journal.json")["pages"]))):
            journal = self.native_json("import-journal.json")
            original = journal["pages"]
            journal["pages"] = replacement
            self.write_native_json("import-journal.json", journal)
            self.reject()
            journal["pages"] = original
            self.write_native_json("import-journal.json", journal)

    def test_v4_import_native_lifecycle_rejects_early_completion_missing_receipts_and_forged_prefix(self) -> None:
        for stage, field, value in (("begin", "completed", True), ("resumed", "next_page", 2),
                                    ("finished", "completed", 1), ("verified", "committed_rows", 7),
                                    ("verified", "target_identity_classification", "unobserved-physical-cluster")):
            with self.subTest(stage=stage, field=field):
                lifecycle = self.native_json("lifecycle.json")
                original = lifecycle[stage][field]
                lifecycle[stage][field] = value
                self.write_native_json("lifecycle.json", lifecycle)
                self.reject()
                lifecycle[stage][field] = original
                self.write_native_json("lifecycle.json", lifecycle)
        for replacement in ([], [self.native_json("lifecycle.json")["first_page"]] * 3):
            lifecycle = self.native_json("lifecycle.json")
            original = lifecycle["remaining_pages"]
            lifecycle["remaining_pages"] = replacement
            self.write_native_json("lifecycle.json", lifecycle)
            self.reject()
            lifecycle["remaining_pages"] = original
            self.write_native_json("lifecycle.json", lifecycle)
        lifecycle = self.native_json("lifecycle.json")
        lifecycle["first_page"]["prefix_sha256"] = "a" * 64
        self.write_native_json("lifecycle.json", lifecycle)
        self.reject()

    def test_v4_import_target_schema_reports_require_fresh_full_chain_and_readonly_publisher_preservation(self) -> None:
        for name in ("target-schema-migrate.json", "target-schema-verify.json"):
            for field, value in (("schema_version", 3), ("storage_writer_epoch", 3), ("table_count", 10),
                                 ("chain_digest", "a" * 64), ("source_commit", "3" * 40),
                                 ("upgrade_source_commit", "4" * 40), ("v2_apply_source_commit", "5" * 40),
                                 ("v3_apply_source_commit", "6" * 40), ("applied_steps", True),
                                 ("migration_applied", 1), ("compatibility_credit", True)):
                with self.subTest(file=name, field=field):
                    report = self.native_json(name)
                    original = report[field]
                    report[field] = value
                    self.write_native_json(name, report)
                    self.reject()
                    report[field] = original
                    self.write_native_json(name, report)

    def test_v4_import_native_archive_rejects_duplicate_json_fields_and_bounded_file_overflow(self) -> None:
        path = self.root / "storage-v4-import-packets/custody-anchors.json"
        original = path.read_bytes()
        path.write_bytes(original[:-1] + b',"profile":"postgresql"}')
        self.reject()
        path.write_bytes(original)
        path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
        self.reject()
        path.write_bytes(original)
        self.assertTrue(self.validate()["storage_v4_import"])

    def test_v4_import_native_captured_catalog_is_closed_and_bound_to_supported_collation(self) -> None:
        for field, value in (("snapshot_identity", "other-snapshot"), ("table_type", "VIEW"),
                             ("collation_binding", ["postgresql", "UTF8", "i", "x", "x", "default", "d", "true"]),
                             ("collation_binding", ["postgresql", "UTF8", "c", "C", "C", "default", "d", "false"]),
                             ("owner_foreign_key_validated", 1), ("columns", []), ("constraints", [])):
            with self.subTest(field=field):
                catalog = self.native_json("packet/source-catalog.json")
                catalog[field] = value
                self.write_native_json("packet/source-catalog.json", catalog)
                self.rehash_native_packet()
                self.reject()
                self.write_native_fixture()
        catalog = self.native_json("packet/source-catalog.json")
        catalog["unobserved_index_ready"] = True
        self.write_native_json("packet/source-catalog.json", catalog)
        self.rehash_native_packet()
        self.reject()

    def test_v4_import_native_archive_bounds_total_bytes_and_entry_count(self) -> None:
        native = self.root / "storage-v4-import-packets"
        # Every member is within the per-file bound; aggregate growth fails
        # before trusting reports. These are synthetic bytes, never DB data.
        for index in range(17):
            (native / f"overflow-{index}.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        self.reject()
        self.write_native_fixture()
        for index in range(513):
            (native / f"entry-{index}.bin").touch()
        self.reject()

    def write_json(self, name: str, value: object) -> None:
        (self.root / name).write_text(json.dumps(value))

    def chain_digest(self, profile: str) -> str:
        digest = hashlib.sha256()
        for position, entry in enumerate(self.lock["profiles"][profile]["ordered_files"]):
            digest.update(position.to_bytes(8, "big"))
            digest.update(entry["path"].encode() + b"\0")
            digest.update(bytes.fromhex(entry["git_blob_sha1"]))
        return digest.hexdigest()

    def write_log(self, lines: list[str]) -> None:
        (self.root / "canonical-storage-app.log").write_text("\n".join(lines) + "\n")

    def write_named_log(self, name: str, lines: list[str]) -> None:
        (self.root / name).write_text("\n".join(lines) + "\n")

    def validate(self) -> dict[str, object]:
        return MODULE.validate_storage_live_packet(self.root, profile=self.profile, commit=self.COMMIT, tree=self.TREE)

    def reject(self) -> None:
        with self.assertRaises(SystemExit):
            self.validate()

    def test_actual_validator_checks_profile_sql_bytes_and_never_grants_credit(self) -> None:
        result = self.validate()
        self.assertEqual(result["authoritative_migrations_count"], 4)
        self.assertEqual(result["schema_v3_extra_cases"], 41)
        self.assertEqual(result["schema_v3_case_families"], SCHEMA_FAMILIES)
        self.assertIs(result["storage_native_jsonb"], True)
        self.assertIs(result["compatibility_credit"], False)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["production_ready"], False)
        arguments = ["--live-packet", str(self.root), "--profile", self.profile,
                     "--commit", self.COMMIT, "--tree", self.TREE]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(MODULE.main(arguments), 0)
        self.assertEqual(json.loads(output.getvalue()), result)

    def test_actual_harness_archive_builder_retains_all_four_source_sql_bytes(self) -> None:
        script = ACTUAL_HARNESS.split("<<'PY_STORAGE_SCHEMA_V3'\n", 1)[1].split("\nPY_STORAGE_SCHEMA_V3", 1)[0]
        for path in (self.root / "authoritative-migrations").iterdir():
            path.unlink()
        (self.root / "authoritative-migrations").rmdir()
        (self.root / "authoritative-migrations.json").unlink()
        executed = subprocess.run([sys.executable, "-", str(self.root), self.profile, self.COMMIT],
                                 input=script, text=True, capture_output=True, timeout=10, cwd=ROOT)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertEqual(self.validate()["authoritative_migrations_count"], 4)

    def test_cockroach_archive_uses_its_own_source_chain_and_observed_input_outcomes(self) -> None:
        self.profile = "cockroachdb"
        self.write_native_fixture()
        self.write_json("summary.json", {**self.summary, "profile": self.profile})
        self.write_json("storage-v4-import-source.json", {**self.import_source, "profile": self.profile})
        self.write_json("schema-identity.json", {**self.identity, "profile": self.profile,
                                                 "chain_digest": self.chain_digest(self.profile)})
        for path in (self.root / "authoritative-migrations").iterdir():
            path.unlink()
        (self.root / "authoritative-migrations").rmdir()
        (self.root / "authoritative-migrations.json").unlink()
        script = ACTUAL_HARNESS.split("<<'PY_STORAGE_SCHEMA_V3'\n", 1)[1].split("\nPY_STORAGE_SCHEMA_V3", 1)[0]
        executed = subprocess.run([sys.executable, "-", str(self.root), self.profile, self.COMMIT],
                                 input=script, text=True, capture_output=True, timeout=10, cwd=ROOT)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        lines = [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.log_lines]
        self.write_named_log("schema-upgrade.log", [line.replace("profile=postgresql", "profile=cockroachdb")
                                                   for line in self.schema_lines])
        self.write_named_log("storage-native-jsonb.log", [line.replace("profile=postgresql", "profile=cockroachdb")
                                                         for line in self.native_lines])
        self.write_named_log("storage-v4-acl.log", [line.replace("profile=postgresql", "profile=cockroachdb")
                                                   for line in self.v4_acl_lines])
        self.write_named_log("storage-v4-import.log", [line.replace("profile=postgresql", "profile=cockroachdb")
                                                      for line in self.v4_import_lines])
        self.write_named_log(MODULE.STORAGE_DUPLICATE_LOG,
                             [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.duplicate_lines])
        for condition, payload in (("accepted", "accepted"), ("accepted", "rejected"),
                                   ("rejected", "accepted"), ("rejected", "rejected")):
            with self.subTest(condition=condition, payload=payload):
                self.write_log([line.replace("condition_sql=rejected", "condition_sql=" + condition)
                                .replace("payload_sql=rejected", "payload_sql=" + payload) for line in lines])
                self.assertEqual(self.validate()["profile"], self.profile)

    def test_duplicate_json_fields_cannot_override_identity_or_claims(self) -> None:
        original = (self.root / "summary.json").read_text()
        for prefix in ('"schema_version":4,', '"accepted":false,', '"compatibility_credit":false,'):
            with self.subTest(prefix=prefix):
                (self.root / "summary.json").write_text("{" + prefix + original[1:])
                self.reject()

    def test_metadata_abi_and_real_historical_v2_publisher_are_not_guessed(self) -> None:
        for field, value in (("schema_version", 2), ("storage_writer_epoch", 2),
                             ("v2_apply_source_commit", "3" * 40), ("v3_apply_source_commit", "5" * 40), ("source_commit", "4" * 40)):
            with self.subTest(field=field):
                changed = {**self.identity, field: value}
                self.write_json("schema-identity.json", changed)
                self.reject()
        self.write_json("schema-identity.json", self.identity)
        self.assertEqual(self.validate()["schema_version"], 4)

    def test_schema_verify_report_cannot_omit_the_shared_envelope_digest_or_execution_outcome(self) -> None:
        for field in ("schema", "digest_algorithm", "chain_digest", "table_count", "migration_applied", "applied_steps"):
            changed = {key: value for key, value in self.identity.items() if key != field}
            with self.subTest(missing=field):
                self.write_json("schema-identity.json", changed)
                self.reject()
        self.write_json("schema-identity.json", self.identity)
        self.assertEqual(self.validate()["schema_version"], 4)

    def test_schema_packet_requires_the_complete_current_digest_and_exact_readonly_outcome(self) -> None:
        for changes in (
            {"schema": "trillionnium.partial-identity.v1"},
            {"chain_digest": "f" * 64}, {"chain_digest": "wrong"},
            {"chain_digest": self.chain_digest("cockroachdb")},
            {"digest_algorithm": "legacy-single-file-sha256"},
            {"table_count": 10}, {"table_count": 13}, {"table_count": True},
            {"migration_applied": True, "applied_steps": 3},
            {"migration_applied": True, "applied_steps": 0},
            {"migration_applied": False, "applied_steps": 1},
            {"migration_applied": 0}, {"applied_steps": False},
        ):
            with self.subTest(changes=changes):
                self.write_json("schema-identity.json", {**self.identity, **changes})
                self.reject()
        self.write_json("schema-identity.json", self.identity)
        self.assertEqual(self.validate()["schema_version"], 4)

    def test_schema_identity_shared_decoder_rejects_duplicate_fields_and_original_byte_overflow(self) -> None:
        original = json.dumps(self.identity)
        path = self.root / "schema-identity.json"
        for prefix in ('"chain_digest":"' + self.identity["chain_digest"] + '",',
                       '"migration_applied":false,', '"applied_steps":0,'):
            with self.subTest(prefix=prefix):
                path.write_text("{" + prefix + original[1:])
                self.reject()
        path.write_text(original + " " * (16 * 1024))
        self.reject()
        path.write_text(original)
        self.assertEqual(self.validate()["schema_version"], 4)

    def test_missing_truncated_or_altered_sql_is_rejected_even_with_new_digest(self) -> None:
        entry = self.manifest["ordered_files"][2]
        path = self.root / entry["archive_path"]
        original = path.read_bytes()
        for changed in (b"", original[:-1], original + b"\n-- synthetic mutation\n"):
            with self.subTest(size=len(changed)):
                path.write_bytes(changed)
                copied = json.loads(json.dumps(self.manifest))
                copied["ordered_files"][2]["sha256"] = hashlib.sha256(changed).hexdigest()
                copied["ordered_files"][2]["size_bytes"] = len(changed)
                self.write_json("authoritative-migrations.json", copied)
                self.reject()
        path.unlink()
        self.reject()

    def test_complete_ordered_profile_chain_digest_and_size_are_independent_guards(self) -> None:
        for field, value in (("git_blob_sha1", "0" * 40), ("sha256", "0" * 64), ("size_bytes", 0),
                             ("archive_path", "../private.sql"), ("path", "migrations/cockroachdb/0003_storage_jsonb_up.sql")):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(self.manifest))
                changed["ordered_files"][2][field] = value
                self.write_json("authoritative-migrations.json", changed)
                self.reject()
        for files in (self.manifest["ordered_files"][:2], list(reversed(self.manifest["ordered_files"]))):
            self.write_json("authoritative-migrations.json", {**self.manifest, "ordered_files": files})
            self.reject()

    def test_summary_case_counts_and_positive_credit_cannot_replace_execution(self) -> None:
        for field, value in (("schema_version", 2), ("storage_writer_epoch", 2), ("authoritative_migrations_count", 2),
                             ("storage_jsonb_v3_projection", False), ("compatibility_credit", True),
                             ("storage_native_jsonb", False), ("storage_v4_acl", False), ("schema_v3_extra_cases", 40),
                             ("schema_v3_extra_cases", True),
                             ("schema_v3_case_families", {**SCHEMA_FAMILIES, "metadata_validation": 8}),
                             ("schema_v3_case_families", {**SCHEMA_FAMILIES, "partial_resume": True}),
                             ("accepted", True), ("wire_compatible", True), ("production_ready", True),
                             ("storage_jsonb_v3_cases", {"history": 6, "opaque_success": 4, "no_op": 2, "resource": True, "native_input": 3})):
            with self.subTest(field=field):
                self.write_json("summary.json", {**self.summary, field: value})
                self.reject()

    def test_all_schema_family_actual_markers_and_exact_six_result_are_required(self) -> None:
        for marker in self.schema_lines[:-1]:
            for lines in (
                [line for line in self.schema_lines if line != marker],
                self.schema_lines + [marker],
                [line.replace(marker, marker.rsplit("=", 1)[0] + "=0") for line in self.schema_lines],
                [line.replace(marker, "test injected-prefix ... " + marker) for line in self.schema_lines],
            ):
                with self.subTest(marker=marker):
                    self.write_named_log("schema-upgrade.log", lines)
                    self.reject()
        for lines in (
            self.schema_lines + ["developer-only live test skip"],
            self.schema_lines + ["schema_v3_shapes_skipped profile=postgresql"],
            [line.replace("6 passed", "0 passed") for line in self.schema_lines],
            [line.replace("0 failed", "1 failed") for line in self.schema_lines],
            [line.replace("0 ignored", "1 ignored") for line in self.schema_lines],
            self.schema_lines + [self.schema_lines[-1]],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.schema_lines],
        ):
            self.write_named_log("schema-upgrade.log", lines)
            self.reject()
        self.write_named_log("schema-upgrade.log", self.schema_lines)
        (self.root / "schema-upgrade.log").unlink()
        self.reject()

    def test_native_jsonb_summary_without_real_single_fixture_log_is_rejected(self) -> None:
        for lines in (
            self.native_lines[1:], self.native_lines + [self.native_lines[0]],
            self.native_lines + ["storage_native_jsonb_live_skipped: optional database absent"],
            [line.replace("1 passed", "0 passed") for line in self.native_lines],
            [line.replace("0 ignored", "1 ignored") for line in self.native_lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.native_lines],
        ):
            self.write_named_log("storage-native-jsonb.log", lines)
            self.reject()
        (self.root / "storage-native-jsonb.log").unlink()
        self.reject()

    def test_v4_acl_summary_requires_a_real_single_required_fixture(self) -> None:
        for lines in (
            self.v4_acl_lines[1:], self.v4_acl_lines + [self.v4_acl_lines[0]],
            self.v4_acl_lines + ["storage_v4_acl_live_skipped: optional database absent"],
            [line.replace("1 passed", "0 passed") for line in self.v4_acl_lines],
            [line.replace("0 ignored", "1 ignored") for line in self.v4_acl_lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.v4_acl_lines],
        ):
            self.write_named_log("storage-v4-acl.log", lines)
            self.reject()
        (self.root / "storage-v4-acl.log").unlink()
        self.reject()

    def test_homogeneous_packet_requires_seven_unique_profile_markers_one_result_no_skip_and_truthful_summary(self) -> None:
        for marker in self.duplicate_lines[:-1]:
            for changed in (
                [line for line in self.duplicate_lines if line != marker], self.duplicate_lines + [marker],
                self.duplicate_lines + [marker.replace("profile=postgresql", "profile=cockroachdb")],
                [line.replace(marker, "test injected-prefix ... " + marker) for line in self.duplicate_lines],
            ):
                with self.subTest(marker=marker):
                    self.write_named_log(MODULE.STORAGE_DUPLICATE_LOG, changed); self.reject()
        for changed in (
            [], self.duplicate_lines + [self.duplicate_lines[-1]],
            self.duplicate_lines + ["nakama_duplicate_batches_skipped reason=optional_database_absent"],
            [line.replace("1 passed", "0 passed") for line in self.duplicate_lines],
            [line.replace("0 failed", "1 failed") for line in self.duplicate_lines],
            [line.replace("0 ignored", "1 ignored") for line in self.duplicate_lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.duplicate_lines],
            [line.replace("actual_batch_domain_code=InvalidArgument", "actual_batch_domain_code=Internal") for line in self.duplicate_lines],
            [line.replace("held_wait_cases=2", "held_wait_cases=1") for line in self.duplicate_lines],
            [line.replace("early_reject_cases=3", "early_reject_cases=2") for line in self.duplicate_lines],
            [line.replace("early_reject_cases=3 fields=15", "early_reject_cases=3 fields=14") for line in self.duplicate_lines],
        ):
            self.write_named_log(MODULE.STORAGE_DUPLICATE_LOG, changed); self.reject()
        self.write_named_log(MODULE.STORAGE_DUPLICATE_LOG, self.duplicate_lines)
        for field in ("storage_homogeneous_batches", "storage_homogeneous_app", "storage_write_tail_drain"):
            for value in (False, 1):
                self.write_json("summary.json", {**self.summary, field: value}); self.reject()
        self.write_json("summary.json", self.summary)
        app = "storage_homogeneous_app_executed profile=postgresql write_occurrences=13 rollback_cases=3"
        for changed in (
            [line for line in self.log_lines if line != app], self.log_lines + [app],
            self.log_lines + [app.replace("profile=postgresql", "profile=cockroachdb")],
            [line.replace(app, app.replace("13", "12")) for line in self.log_lines],
            [line.replace(app, "test injected-prefix ... " + app) for line in self.log_lines],
        ):
            self.write_log(changed); self.reject()
        self.write_log(self.log_lines)
        (self.root / MODULE.STORAGE_DUPLICATE_LOG).unlink(); self.reject()

    def test_v4_import_requires_terminal_execution_and_all_materialized_source_bytes(self) -> None:
        for lines in (
            [], self.v4_import_lines[1:], self.v4_import_lines + [self.v4_import_lines[0]],
            self.v4_import_lines + [self.v4_import_lines[-1]],
            self.v4_import_lines + ["storage_v4_import_live_skipped"],
            [line.replace("1 passed", "0 passed") for line in self.v4_import_lines],
            [line.replace("0 ignored", "1 ignored") for line in self.v4_import_lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.v4_import_lines],
        ):
            self.write_named_log("storage-v4-import.log", lines)
            self.reject()
        self.write_named_log("storage-v4-import.log", self.v4_import_lines)
        for field, value in (("storage_v4_import", False), ("storage_v4_import", 1)):
            self.write_json("summary.json", {**self.summary, field: value})
            self.reject()
        self.write_json("summary.json", self.summary)
        for relative in [entry["archive_path"] for entry in self.import_source["members"]]:
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b"tamper")
            self.reject()
            path.unlink()
            self.reject()
            path.write_bytes(original)
        for field, value in (("commit", "3" * 40), ("tree", "3" * 40), ("profile", "cockroachdb"),
                             ("compatibility_credit", True), ("accepted", True), ("scope", "native-proof")):
            self.write_json("storage-v4-import-source.json", {**self.import_source, field: value})
            self.reject()
        self.write_json("storage-v4-import-source.json", self.import_source)
        self.assertIs(self.validate()["storage_v4_import"], True)

    def test_v4_import_source_inventory_rejects_extra_indirect_duplicate_and_relabelled_bytes(self) -> None:
        for directory in ("storage-source-upstream", "storage-v4-import-source"):
            extra = self.root / directory / "undeclared.sql"
            extra.write_bytes(b"extra source bytes")
            self.reject()
            extra.unlink()
        manifest = json.loads(json.dumps(self.import_source))
        for members in (manifest["members"][:-1], manifest["members"] + [manifest["members"][0]],
                        list(reversed(manifest["members"]))):
            self.write_json("storage-v4-import-source.json", {**manifest, "members": members})
            self.reject()
        self.write_json("storage-v4-import-source.json", self.import_source)
        path = self.root / "storage-v4-import-source/exporter.rs"
        data = path.read_bytes()
        path.unlink()
        outside = self.root / "unsealed-exporter.rs"
        outside.write_bytes(data)
        path.symlink_to(outside)
        self.reject()

    def test_native_observations_and_exact_terminal_test_cannot_be_missing_or_duplicated(self) -> None:
        for lines in (
            self.log_lines[:2] + self.log_lines[3:], self.log_lines[:3] + self.log_lines[4:],
            self.log_lines + [self.log_lines[2]], self.log_lines + [self.log_lines[3]],
            [line.replace("condition_sql=rejected", "condition_sql=guessed") for line in self.log_lines],
            [line.replace("profile=postgresql", "profile=cockroachdb") for line in self.log_lines],
            [line.replace("1 passed; 0 failed; 0 ignored", "0 passed; 0 failed; 0 ignored") for line in self.log_lines],
            self.log_lines + ["canonical_storage_api_live_skipped profile=postgresql"],
        ):
            with self.subTest(lines=lines):
                self.write_log(lines)
                self.reject()

    def test_http_encoder_cannot_derive_versions_from_native_render_or_restrict_historical_shape(self) -> None:
        runtime = ROOT / "crates/trnm-server/src/runtime"
        source = (runtime / "storage_api.rs").read_text()
        fixture = (runtime / "storage_api_tests.rs").read_text() + (runtime / "storage_api_v3_live.rs").read_text()
        MODULE.validate_storage_projection_source(source, fixture)
        for changed in (
            source.replace("object.verify_integrity()", "object.ignore_integrity()"),
            source.replace("output.string(value)?", "output.string(value)?; ContentVersion::from_value(&object.value)"),
            source.replace("output.string(value)?", "output.string(value)?; serde_json::from_str::<serde_json::Value>(value)"),
            source.replace("!object.version.as_str().is_empty()", "true"),
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(SystemExit):
                    MODULE.validate_storage_projection_source(changed, fixture)


class ServerLiveFailureDiagnosticTests(unittest.TestCase):
    """Run the actual trap and early shell commands with a synthetic binary."""

    def run_early_shell(self, mode: str, *, helper_fails: bool = False) -> tuple[subprocess.CompletedProcess[str], dict[str, bytes]]:
        source = (ROOT / "scripts/ci-trnm-server-live.sh").read_text()
        stage_functions = source[source.index("begin_stage() {"):source.index("database_image() {")]
        cleanup = source[source.index("cleanup() {"):source.index("begin_stage database-start")]
        early = source[source.index('export TRNM_SERVER_BIND='):source.index("# This lane executes the canonical App")]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            evidence = root / "evidence"
            evidence.mkdir()
            (root / "scripts").mkdir()
            helper = root / "scripts/print-server-live-failure.py"
            helper.write_text("raise SystemExit(91)\n" if helper_fails else (ROOT / "scripts/print-server-live-failure.py").read_text())
            binary = root / "synthetic-server"
            binary.write_text('''#!/usr/bin/env bash
case "$1" in
check-config)
  case "$DIAGNOSTIC_TEST_MODE" in
    check-config) printf 'error password=%s admin_token="%s" url=%s\\n' 'trnm_live_password' "$TRNM_SERVER_ADMIN_TOKEN" "$TRNM_SERVER_DATABASE_URL"; exit 37 ;;
    check-config-output) echo 'wrong success text' ;;
    credential-check) echo 'trnm-server configuration valid'; echo 'password=trnm_live_password' ;;
    *) echo 'trnm-server configuration valid' ;;
  esac ;;
migrate)
  case "$DIAGNOSTIC_TEST_MODE" in
    migrate) printf 'migrate database_url=%s\\n' "$TRNM_SERVER_DATABASE_URL"; exit 37 ;;
    migrate-output) echo 'wrong migration success text' ;;
    *) echo 'trnm-server migration completed' ;;
  esac ;;
esac
''')
            binary.chmod(0o700)
            bootstrap = '''set -euo pipefail
profile=cockroachdb
root=$DIAGNOSTIC_TEST_ROOT
evidence="$root/evidence"
binary="$root/synthetic-server"
admin_token=$DIAGNOSTIC_TEST_ADMIN_TOKEN
database_url=$DIAGNOSTIC_TEST_DATABASE_URL
server_port=17350
candidate_sha=0000000000000000000000000000000000000000
container=synthetic-unused
server_pid=''
stage=initialize
diagnostic_logs=()
docker() { return 0; }
'''
            # A packet can only be reached after the real check-config/migrate
            # success and credential guards. It is synthetic, never evidence.
            finish = "printf 'synthetic-success-packet\\n' > \"$evidence/summary.json\"\nprintf 'synthetic-seal\\n' > \"$evidence/SHA256SUMS\"\n"
            env = {**os.environ, "DIAGNOSTIC_TEST_MODE": mode, "DIAGNOSTIC_TEST_ROOT": str(root),
                   "DIAGNOSTIC_TEST_ADMIN_TOKEN": "admin-'quoted-\"token-UNIQUE-3812",
                   "DIAGNOSTIC_TEST_DATABASE_URL": "postgresql://user:trnm_live_password@127.0.0.1:26257/private"}
            result = subprocess.run(["bash", "-c", bootstrap + stage_functions + cleanup + early + finish],
                                    env=env, capture_output=True, text=True, timeout=10)
            files = {p.name: p.read_bytes() for p in evidence.iterdir()}
            return result, files

    def test_early_failures_report_the_actual_stage_and_preserve_status(self) -> None:
        for mode, status in [("check-config", 37), ("check-config-output", 1), ("credential-check", 1),
                             ("migrate", 37), ("migrate-output", 1)]:
            with self.subTest(mode=mode):
                result, files = self.run_early_shell(mode)
                self.assertEqual(result.returncode, status, result.stderr)
                reports = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
                self.assertEqual(len(reports), 1)
                self.assertEqual((reports[0]["stage"], reports[0]["exit_code"]), (mode, status))
                self.assertNotIn("summary.json", files)
                self.assertNotIn("SHA256SUMS", files)
                self.assertNotIn("trnm_live_password", result.stdout + result.stderr)
                self.assertNotIn("admin-'quoted-\"token-UNIQUE-3812", result.stdout + result.stderr)

    def test_success_has_no_failure_diagnostics_and_keeps_guards(self) -> None:
        result, files = self.run_early_shell("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("summary.json", files)
        self.assertIn("SHA256SUMS", files)

    def test_failed_diagnostic_helper_cannot_replace_the_original_failure(self) -> None:
        result, files = self.run_early_shell("migrate", helper_fails=True)
        self.assertEqual(result.returncode, 37)
        self.assertNotIn("summary.json", files)

    def run_helper(self, paths: list[Path], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(ROOT / "scripts/print-server-live-failure.py"),
                               "--profile", "cockroachdb", "--stage", "migrate", "--status", "37", "--",
                               *(str(p) for p in paths)], env={**os.environ, **env},
                              capture_output=True, text=True, timeout=10)

    def test_literals_quotes_uris_fields_and_actions_commands_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            log = Path(name) / "migrate.log"
            admin = "admin-'quoted-\"token-SECRET-9241"
            password = "p'\"ass%word-SECRET-9263"
            url = "postgresql://user:" + password + "@host/private"
            log.write_text("plain " + admin + "\njson " + json.dumps(admin) + "\npassword alone " + password +
                           "\nurl " + url + "\nunknown password='other-private-value' tail-secret\n" +
                           'unknown admin_token="third-private-value" tail-secret\n' +
                           "dsn=redis://user:fourth-private-value@host\nhttps://user:fifth-private-value@host\n" +
                           "::error::injected workflow command\n")
            result = self.run_helper([log], {"TRNM_SERVER_ADMIN_TOKEN": admin, "TRNM_SERVER_DATABASE_URL": url})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(len(result.stderr.splitlines()), 1)
            report = json.loads(result.stderr)
            self.assertEqual(report["logs"][0]["log"], "migrate.log")
            self.assertNotIn(str(log.parent), result.stderr)
            for value in [admin, json.dumps(admin)[1:-1], password, "other-private-value", "third-private-value",
                          "fourth-private-value", "fifth-private-value", "tail-secret", "::error::"]:
                self.assertNotIn(value, result.stderr)
            self.assertIn("injected workflow command", "\n".join(report["logs"][0]["lines"]))

    def test_url_passwords_are_hidden_even_during_malformed_config_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            log = Path(name) / "check-config.log"
            for url, password in [("postgresql://user:raw%27%22pass-6821@host/db", "raw'\"pass-6821"),
                                  ("postgresql://user:raw[broken-password-6837@host/db", "raw[broken-password-6837")]:
                with self.subTest(url=url):
                    log.write_text("bare driver value " + password + "\n" + url + "\n")
                    result = self.run_helper([log], {"TRNM_SERVER_DATABASE_URL": url})
                    self.assertEqual(result.returncode, 0)
                    self.assertNotIn(password, result.stderr)
                    self.assertNotIn(url, result.stderr)

    def test_short_read_and_unterminated_last_line_cannot_publish_secret_fragments(self) -> None:
        spec = importlib.util.spec_from_file_location("server_live_failure_test_helper", ROOT / "scripts/print-server-live-failure.py")
        assert spec is not None and spec.loader is not None
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as name:
            log = Path(name) / "server-primary.log"
            secret = "token-SENSITIVE-FIRST-PREFIX-9472-REMAINING-9481"
            prefix = secret[:25]
            log.write_text("ordinary driver failure\n" + prefix)
            result = self.run_helper([log], {"TRNM_SERVER_ADMIN_TOKEN": secret})
            report = json.loads(result.stderr)["logs"][0]
            self.assertTrue(report["partial_last_line_discarded"])
            self.assertEqual(report["lines"], ["ordinary driver failure"])
            self.assertNotIn(prefix, result.stderr)
            log.write_text("ordinary driver failure\n" + secret + "\n")
            read_size = len(("ordinary driver failure\n" + prefix).encode())
            original_read = os.read
            with mock.patch.object(helper.os, "read", side_effect=lambda fd, size: original_read(fd, min(size, read_size))):
                short = helper.read_log(str(log), [secret])
            self.assertEqual(short["read_bytes"], read_size)
            self.assertTrue(short["partial_last_line_discarded"])
            self.assertEqual(short["lines"], ["ordinary driver failure"])

    def test_generic_uri_redaction_includes_single_character_schemes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            log = Path(name) / "migrate.log"
            log.write_text("unknown x://user:single-scheme-private-value@host\n")
            result = self.run_helper([log], {})
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("single-scheme-private-value", result.stderr)

    def test_secret_literals_cannot_disable_generic_credential_scrubbing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            log = Path(name) / "check-config.log"
            cases = [("password", "password=another-private-credential-9871\n"),
                     (":", "https://user:another-private-credential-9871@host/\n")]
            for secret, text in cases:
                with self.subTest(secret=secret):
                    log.write_text(text)
                    result = self.run_helper([log], {"TRNM_TEST_PASSWORD": secret})
                    self.assertEqual(result.returncode, 0)
                    self.assertNotIn("another-private-credential-9871", result.stderr)

    def test_short_secrets_cannot_recursively_expand_redaction_markers(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "bounded_live_failure", ROOT / "scripts/print-server-live-failure.py"
        )
        assert spec is not None and spec.loader is not None
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        raw = "a" * helper.MAX_READ_BYTES
        # Each generated marker includes the later literals. Rescanning it at
        # each pass would exceed the resource budget before serialization.
        actual = helper.redact(raw, ["a", "e", "d", "r", "<", ">"])
        self.assertLessEqual(len(actual), len(raw) * len(helper.REDACTED))
        self.assertEqual(actual.count(helper.REDACTED), len(raw))

    def test_tail_budgets_drop_partial_first_line_and_limit_both_logs(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            log = root / "migrate.log"
            partial_secret = "private-credential-suffix-that-must-disappear"
            log.write_bytes(b"x" * 70000 + partial_secret.encode() + b"\n" +
                            b"\n".join(f"retained-{i:03}".encode() for i in range(100)) + b"\n")
            other = root / "check-config.log"
            other.write_bytes(b"\xff" * 65536)
            result = self.run_helper([log, other], {})
            self.assertEqual(result.returncode, 0, result.stderr[:200])
            self.assertLessEqual(len(result.stderr.encode()), 1024 * 1024)
            report = json.loads(result.stderr)
            self.assertEqual(len(report["logs"]), 2)
            for entry in report["logs"]:
                self.assertLessEqual(entry["read_bytes"], 65536)
                self.assertLessEqual(len(entry["lines"]), 80)
            self.assertTrue(report["logs"][0]["partial_first_line_discarded"])
            self.assertNotIn(partial_secret, result.stderr)
            self.assertEqual(report["logs"][0]["lines"][0], "retained-020")
            rejected = self.run_helper([log, other, root / "third.log"], {})
            self.assertNotEqual(rejected.returncode, 0)

    def test_missing_symlink_and_fifo_logs_are_unavailable_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "target.log"
            target.write_text("untrusted symlink bytes")
            link = root / "link.log"
            link.symlink_to(target)
            fifo = root / "fifo.log"
            os.mkfifo(fifo)
            for paths in [[root / "missing.log", link], [fifo]]:
                result = self.run_helper(paths, {})
                self.assertEqual(result.returncode, 0)
                self.assertTrue(all(entry["unavailable"] for entry in json.loads(result.stderr)["logs"]))

    def test_actual_failure_source_contract_and_negative_stage_trap_bounds(self) -> None:
        harness = (ROOT / "scripts/ci-trnm-server-live.sh").read_text()
        helper = (ROOT / "scripts/print-server-live-failure.py").read_text()
        MODULE.validate_live_failure_diagnostics(harness, helper)
        harness_mutations = [
            harness.replace('begin_stage check-config "$evidence/check-config.log"', "", 1),
            harness.replace('begin_stage migrate "$evidence/check-config.log" "$evidence/migrate.log"', "", 1),
            harness.replace("if grep -Fq 'trnm_live_password'", "if grep -F 'trnm_live_password'", 1),
            harness.replace('report_failure "$status" || true', 'report_failure "$status"', 1),
            harness.replace('exit "$status"', "exit 0", 1),
            harness.replace("local status=$?", "local status=0", 1),
            harness.replace('TRNM_SERVER_ADMIN_TOKEN="$admin_token"', 'secret="$admin_token"', 1),
        ]
        for changed in harness_mutations:
            with self.subTest(harness_mutation=changed != harness):
                self.assertNotEqual(changed, harness)
                with self.assertRaises(SystemExit):
                    MODULE.validate_live_failure_diagnostics(changed, helper)
        helper_mutations = [helper.replace(old, new, 1) for old, new in [
            ("MAX_LOGS = 2", "MAX_LOGS = 3"),
            ("MAX_READ_BYTES = 65536", "MAX_READ_BYTES = 65537"),
            ("MAX_LINES = 80", "MAX_LINES = 81"),
            ("MAX_OUTPUT_BYTES = 1048576", "MAX_OUTPUT_BYTES = 1048577"),
            ("MAX_LOGS = 2", "MAX_LOGS = True"),
            ("MAX_LOGS = 2", "MAX_LOGS = int(os.environ.get('UNBOUNDED_LOGS', '2'))"),
            ("os.read(fd, MAX_READ_BYTES)", "os.read(fd, 9999999)"),
            ('data.partition(b"\\n")[2]', "data"),
            ("lines[-MAX_LINES:]", "lines"),
            ('pattern.sub(REDACTED, text)', "text"),
        ]]
        for changed in helper_mutations:
            with self.subTest(helper_mutation=changed != helper):
                self.assertNotEqual(changed, helper)
                with self.assertRaises(SystemExit):
                    MODULE.validate_live_failure_diagnostics(harness, changed)


if __name__ == "__main__":
    unittest.main()
