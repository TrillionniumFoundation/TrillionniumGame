"""Owned shell-lane source regressions; no database or acceptance credit."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
CANONICAL = CANONICAL.replace(
    "if grep -Fq 'canonical_storage_api_live_skipped'",
    OPAQUE_MARKER + JSONB_MARKER + NATIVE_INPUT_MARKER + "if grep -Fq 'canonical_storage_api_live_skipped'"
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
"$binary" migrate > "$evidence/migrate.log" 2>&1
'''
# The actual archive block is also executed against temporary files below.
# Reuse its shell layout here only to exercise independent source guard removal,
# reordering and weakening; this fixture supplies no database execution credit.
ACTUAL_HARNESS = (ROOT / "scripts/ci-trnm-server-live.sh").read_text(encoding="utf-8")
SCHEMA_V3 = ACTUAL_HARNESS[ACTUAL_HARNESS.index("schema_version=$(db_scalar"):
                           ACTUAL_HARNESS.index("begin_stage session-response-loss")]
SUFFIX = '''cat > "$evidence/summary.json" <<EOF
{"schema":"synthetic-only","nakama_client_list_projection":true,"storage_occ_precedence":true,"raw_version_conditions":true,"storage_jsonb_v3_projection":true,"storage_native_jsonb":true,"schema_v3_extra_cases":41,"schema_v3_case_families":{"shapes":8,"illegal_legacy":9,"catalog_drift":6,"partial_resume":3,"metadata_validation":9,"opaque_history":6},"storage_jsonb_v3_cases":{"history":6,"opaque_success":4,"no_op":2,"resource":1,"native_input":3},"schema_version":${schema_version},"storage_writer_epoch":${storage_writer_epoch},"authoritative_migrations_count":${authoritative_migrations_count},"wire_compatible":false,"compatibility_credit":false,"accepted":false,"production_ready":false}
EOF
find "$evidence" -type f ! -name SHA256SUMS -print0 \\
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
'''
FIXTURE = PREFIX + CANONICAL + PROJECTION + OCC + TIMESTAMPS + NATIVE_JSONB + SCHEMA + SCHEMA_V3 + SUFFIX


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
                        "storage_occ_test_count", "storage_timestamps_test_count", "storage_native_jsonb_test_count"]:
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
            FIXTURE.replace('test "$schema_version" = 3', 'test "$schema_version" = 2'),
            FIXTURE.replace('test "$storage_writer_epoch" = 3', 'test "$storage_writer_epoch" = 2'),
            FIXTURE.replace('test "$v2_apply_source_commit" = "$candidate_sha"', "true"),
            FIXTURE.replace('test "$authoritative_migrations_count" -eq 3', 'test "$authoritative_migrations_count" -ge 2'),
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


class ActualNativeLaneShellTests(unittest.TestCase):
    """Run production shell guards with mock Cargo logs, never a database."""

    def run_block(self, block: str, lines: list[str], status: int = 0) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "input.log").write_text("\n".join(lines) + "\n")
            bootstrap = '''set -euo pipefail
profile=postgresql
evidence=$MOCK_ROOT
database_url=synthetic-database-configuration
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
                               ACTUAL_HARNESS.index("begin_stage schema-upgrade")]
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
                "storage_native_jsonb",
                "health_ready", "unauthenticated_mutation_rejected", "http_bootstrap_commit_duplicate_conflict",
                "websocket_json_commit", "response_loss_exact_receipt_replay", "authenticated_drain",
                "process_restart_exact_receipt_replay",
            )},
            **{field: 3 for field in (
                "schema_version", "storage_writer_epoch", "authoritative_migrations_count", "entity_revision",
                "event_sequence", "command_receipts", "events", "outbox_intents",
            )},
            "storage_jsonb_v3_cases": {"history": 6, "opaque_success": 4, "no_op": 2, "resource": 1, "native_input": 3},
            "schema_v3_extra_cases": 41, "schema_v3_case_families": SCHEMA_FAMILIES.copy(),
            **{field: False for field in (
                "production_pitr", "multi_node", "wire_compatible", "compatibility_credit", "accepted", "production_ready",
            )},
        }
        self.lock = json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_text())
        self.identity = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": self.profile,
                         "schema_version": 3, "storage_writer_epoch": 3, "table_count": 10,
                         "digest_algorithm": "ordered-path-git-blob-sha256.v1",
                         "chain_digest": self.chain_digest(self.profile),
                         "migration_applied": False, "applied_steps": 0,
                         "source_commit": self.COMMIT, "upgrade_source_commit": self.COMMIT,
                         "v2_apply_source_commit": self.COMMIT, "compatibility_credit": False}
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
                         "schema_version": 3, "storage_writer_epoch": 3, "ordered_files": files,
                         "compatibility_credit": False}
        for name, value in (("summary.json", self.summary), ("schema-identity.json", self.identity),
                            ("migration-lock.json", self.lock), ("authoritative-migrations.json", self.manifest)):
            self.write_json(name, value)
        self.log_lines = [
            "canonical_storage_api_live_executed profile=postgresql",
            "storage_opaque_conditions_live_executed profile=postgresql write_cases=15 delete_cases=18 batch_cases=2",
            "storage_jsonb_v3_live_executed profile=postgresql history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3",
            "storage_jsonb_v3_native_inputs profile=postgresql condition_sql=rejected payload_sql=rejected compatibility_credit=false",
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
        self.assertEqual(result["authoritative_migrations_count"], 3)
        self.assertEqual(result["schema_v3_extra_cases"], 41)
        self.assertEqual(result["schema_v3_case_families"], SCHEMA_FAMILIES)
        self.assertIs(result["storage_native_jsonb"], True)
        self.assertIs(result["compatibility_credit"], False)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["production_ready"], False)
        command = [sys.executable, str(ROOT / "scripts/check-trnm-server.py"), "--live-packet", str(self.root),
                   "--profile", self.profile, "--commit", self.COMMIT, "--tree", self.TREE]
        executed = subprocess.run(command, text=True, capture_output=True, timeout=10)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertEqual(json.loads(executed.stdout), result)

    def test_actual_harness_archive_builder_retains_all_three_source_sql_bytes(self) -> None:
        script = ACTUAL_HARNESS.split("<<'PY_STORAGE_SCHEMA_V3'\n", 1)[1].split("\nPY_STORAGE_SCHEMA_V3", 1)[0]
        for path in (self.root / "authoritative-migrations").iterdir():
            path.unlink()
        (self.root / "authoritative-migrations").rmdir()
        (self.root / "authoritative-migrations.json").unlink()
        executed = subprocess.run([sys.executable, "-", str(self.root), self.profile, self.COMMIT],
                                 input=script, text=True, capture_output=True, timeout=10, cwd=ROOT)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertEqual(self.validate()["authoritative_migrations_count"], 3)

    def test_cockroach_archive_uses_its_own_source_chain_and_observed_input_outcomes(self) -> None:
        self.profile = "cockroachdb"
        self.write_json("summary.json", {**self.summary, "profile": self.profile})
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
        for condition, payload in (("accepted", "accepted"), ("accepted", "rejected"),
                                   ("rejected", "accepted"), ("rejected", "rejected")):
            with self.subTest(condition=condition, payload=payload):
                self.write_log([line.replace("condition_sql=rejected", "condition_sql=" + condition)
                                .replace("payload_sql=rejected", "payload_sql=" + payload) for line in lines])
                self.assertEqual(self.validate()["profile"], self.profile)

    def test_duplicate_json_fields_cannot_override_identity_or_claims(self) -> None:
        original = (self.root / "summary.json").read_text()
        for prefix in ('"schema_version":3,', '"accepted":false,', '"compatibility_credit":false,'):
            with self.subTest(prefix=prefix):
                (self.root / "summary.json").write_text("{" + prefix + original[1:])
                self.reject()

    def test_metadata_abi_and_real_historical_v2_publisher_are_not_guessed(self) -> None:
        for field, value in (("schema_version", 2), ("storage_writer_epoch", 2),
                             ("v2_apply_source_commit", "3" * 40), ("source_commit", "4" * 40)):
            with self.subTest(field=field):
                changed = {**self.identity, field: value}
                self.write_json("schema-identity.json", changed)
                self.reject()
        self.write_json("schema-identity.json", self.identity)
        self.assertEqual(self.validate()["schema_version"], 3)

    def test_schema_verify_report_cannot_omit_the_shared_envelope_digest_or_execution_outcome(self) -> None:
        for field in ("schema", "digest_algorithm", "chain_digest", "table_count", "migration_applied", "applied_steps"):
            changed = {key: value for key, value in self.identity.items() if key != field}
            with self.subTest(missing=field):
                self.write_json("schema-identity.json", changed)
                self.reject()
        self.write_json("schema-identity.json", self.identity)
        self.assertEqual(self.validate()["schema_version"], 3)

    def test_schema_packet_requires_the_complete_current_digest_and_exact_readonly_outcome(self) -> None:
        for changes in (
            {"schema": "trillionnium.partial-identity.v1"},
            {"chain_digest": "f" * 64}, {"chain_digest": "wrong"},
            {"chain_digest": self.chain_digest("cockroachdb")},
            {"digest_algorithm": "legacy-single-file-sha256"},
            {"table_count": 9}, {"table_count": 11}, {"table_count": True},
            {"migration_applied": True, "applied_steps": 3},
            {"migration_applied": True, "applied_steps": 0},
            {"migration_applied": False, "applied_steps": 1},
            {"migration_applied": 0}, {"applied_steps": False},
        ):
            with self.subTest(changes=changes):
                self.write_json("schema-identity.json", {**self.identity, **changes})
                self.reject()
        self.write_json("schema-identity.json", self.identity)
        self.assertEqual(self.validate()["schema_version"], 3)

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
        self.assertEqual(self.validate()["schema_version"], 3)

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
                             ("storage_native_jsonb", False), ("schema_v3_extra_cases", 40),
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
