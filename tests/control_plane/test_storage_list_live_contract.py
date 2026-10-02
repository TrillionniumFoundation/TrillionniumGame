"""Owned shell-lane source regressions; no database or acceptance credit."""
from __future__ import annotations

import importlib.util
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
CANONICAL = CANONICAL.replace(
    "if grep -Fq 'canonical_storage_api_live_skipped'", OPAQUE_MARKER + "if grep -Fq 'canonical_storage_api_live_skipped'"
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

PREFIX = '''#!/usr/bin/env bash
set -euo pipefail
"$binary" migrate > "$evidence/migrate.log" 2>&1
'''
SUFFIX = '''cat > "$evidence/summary.json" <<EOF
{"schema":"synthetic-only","nakama_client_list_projection":true,"storage_occ_precedence":true,"raw_version_conditions":true,"wire_compatible":false,"production_ready":false}
EOF
find "$evidence" -type f ! -name SHA256SUMS -print0 \\
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
'''
FIXTURE = PREFIX + CANONICAL + PROJECTION + OCC + TIMESTAMPS + SCHEMA + SUFFIX


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
                        "storage_occ_test_count", "storage_timestamps_test_count"]:
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
