#!/usr/bin/env python3
"""Repair final-attempt crash capture and retained evidence transport."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ARTIFACT_PIN = "043fb460e6257d1ca154e89a5e86196c74e480f8"
UPLOAD_ARTIFACT_USE = f"actions/upload-artifact@{UPLOAD_ARTIFACT_PIN}"
OUTBOX_WORKFLOW = ".github/workflows/outbox-final-attempt-reaper.yml"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_expected_crash_capture() -> None:
    path = ROOT / "scripts/ci-outbox-final-attempt-reaper.sh"
    replace_once(
        path,
        """  set +e
  \"$worker_bin\" run-once >\"$scenario/worker-crash.stdout\" 2>\"$scenario/worker-crash.stderr\"
  crash_status=$?
  set -e
""",
        """  # A command used as an `if` condition is exempt from `errexit` and the
  # inherited ERR trap. Capture the intentional failpoint status without
  # suppressing fail-fast behavior for any surrounding command.
  if \"$worker_bin\" run-once >\"$scenario/worker-crash.stdout\" 2>\"$scenario/worker-crash.stderr\"; then
    crash_status=0
  else
    crash_status=$?
  fi
""",
        "expected failpoint status capture",
    )


def patch_outbox_workflow() -> None:
    path = ROOT / OUTBOX_WORKFLOW
    text = path.read_text(encoding="utf-8")

    trigger_pair = (
        "      - 'scripts/upload-actions-artifact.py'\n"
        "      - 'tests/control_plane/test_actions_artifact_uploader.py'\n"
    )
    expanded_pair = (
        trigger_pair
        + "      - 'scripts/check-workflow-action-policy.py'\n"
        + "      - 'tests/control_plane/test_workflow_action_policy.py'\n"
    )
    count = text.count(trigger_pair)
    if count != 2:
        raise SystemExit(
            "outbox workflow path filters: expected uploader trigger pair twice, "
            f"got {count}"
        )
    text = text.replace(trigger_pair, expanded_pair)

    old_contract = (
        "          grep -q 'possible_lost_effect_declared' scripts/ci-outbox-final-attempt-reaper.sh\n"
        "          python3 -m py_compile scripts/upload-actions-artifact.py\n"
        "          python3 -m unittest tests.control_plane.test_actions_artifact_uploader -v\n"
    )
    new_contract = (
        "          grep -q 'possible_lost_effect_declared' scripts/ci-outbox-final-attempt-reaper.sh\n"
        "          grep -Fq 'if \"$worker_bin\" run-once >\"$scenario/worker-crash.stdout\"' scripts/ci-outbox-final-attempt-reaper.sh\n"
        f"          grep -Fq '{UPLOAD_ARTIFACT_USE}' .github/workflows/outbox-final-attempt-reaper.yml\n"
        "          python3 -m py_compile scripts/upload-actions-artifact.py\n"
        "          python3 -m unittest tests.control_plane.test_actions_artifact_uploader tests.control_plane.test_workflow_action_policy -v\n"
    )
    if text.count(old_contract) != 1:
        raise SystemExit("outbox source-contract block changed unexpectedly")
    text = text.replace(old_contract, new_contract, 1)

    marker = "      - name: Seal and retain raw diagnostic archive\n"
    if text.count(marker) != 1:
        raise SystemExit("outbox archive step marker changed unexpectedly")
    prefix, _, _ = text.partition(marker)
    replacement = """      - name: Prepare raw diagnostic archive
        if: always()
        id: archive
        shell: bash
        env:
          PROFILE: ${{ matrix.profile }}
        run: |
          set -euo pipefail
          root="run/outbox-final-attempt-reaper/${PROFILE}"
          mkdir -p "$root"
          archive="run/outbox-final-attempt-reaper-${PROFILE}-${CANDIDATE_SHA}.tar.gz"
          tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
            -czf "$archive" -C "$root" .
          archive_sha=$(sha256sum "$archive" | awk '{print $1}')
          artifact_name="outbox-final-attempt-reaper-${PROFILE}-${CANDIDATE_SHA}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
          printf 'archive_sha256=%s\n' "$archive_sha"
          find "$root" -type f -print0 | sort -z | xargs -0 -r sha256sum
          {
            printf 'archive_path=%s\n' "$archive"
            printf 'archive_sha256=%s\n' "$archive_sha"
            printf 'artifact_name=%s\n' "$artifact_name"
          } >> "$GITHUB_OUTPUT"
          {
            printf '### Outbox final-attempt boundaries %s\n\n' "$PROFILE"
            printf -- '- candidate: `%s@%s`\n' "$CANDIDATE_REPOSITORY" "$CANDIDATE_SHA"
            printf -- '- run: `%s`\n' "$GITHUB_RUN_ID"
            printf -- '- raw archive SHA-256: `%s`\n' "$archive_sha"
            printf -- '- crash before publish may lose the external effect: `true`\n'
            printf -- '- compatibility credit: `false`\n'
            printf -- '- production ready: `false`\n'
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Retain raw diagnostic archive
        if: always() && steps.archive.outcome == 'success'
        id: retain
        uses: __UPLOAD_ARTIFACT_USE__
        with:
          name: ${{ steps.archive.outputs.artifact_name }}
          path: ${{ steps.archive.outputs.archive_path }}
          if-no-files-found: error
          compression-level: 0
          retention-days: 30
          overwrite: false
          include-hidden-files: false

      - name: Record retained artifact identity
        if: always() && steps.archive.outcome == 'success' && steps.retain.outcome == 'success'
        shell: bash
        env:
          PROFILE: ${{ matrix.profile }}
          RAW_ARCHIVE_SHA256: ${{ steps.archive.outputs.archive_sha256 }}
          ARTIFACT_ID: ${{ steps.retain.outputs.artifact-id }}
          ARTIFACT_URL: ${{ steps.retain.outputs.artifact-url }}
          ARTIFACT_DIGEST: ${{ steps.retain.outputs.artifact-digest }}
        run: |
          set -euo pipefail
          [[ "$RAW_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]
          [[ "$ARTIFACT_ID" =~ ^[0-9]+$ ]]
          test -n "$ARTIFACT_URL"
          test -n "$ARTIFACT_DIGEST"
          {
            printf -- '- retained artifact ID: `%s`\n' "$ARTIFACT_ID"
            printf -- '- retained artifact URL: `%s`\n' "$ARTIFACT_URL"
            printf -- '- service artifact digest: `%s`\n' "$ARTIFACT_DIGEST"
            printf -- '- raw archive digest remains separately bound: `%s`\n' "$RAW_ARCHIVE_SHA256"
          } >> "$GITHUB_STEP_SUMMARY"
""".replace("__UPLOAD_ARTIFACT_USE__", UPLOAD_ARTIFACT_USE)
    path.write_text(prefix + replacement, encoding="utf-8")


def patch_workflow_policy() -> None:
    path = ROOT / "scripts/check-workflow-action-policy.py"
    source = '''#!/usr/bin/env python3
"""Enforce repository-local or exact allowlisted immutable workflow actions."""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
USES = re.compile(r"^\\s*-?\\s*uses\\s*:\\s*(?P<value>\\S.*)$")
WRITE_PERMISSION = re.compile(r"^\\s*(?P<scope>[a-zA-Z0-9_-]+)\\s*:\\s*write\\s*(?:#.*)?$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
MOVABLE_CANDIDATE_FETCH = 'refs/heads/${CANDIDATE_REF}'
IMMUTABLE_CANDIDATE_FETCH = '"${CANDIDATE_SHA}"'

# External actions remain denied by default. The only exception is one GitHub
# first-party action at one immutable commit, in one evidence workflow.
ALLOWED_LOCAL_USES_PREFIXES = ("./",)
ALLOWED_EXTERNAL_USES: dict[str, frozenset[str]] = {
    "__UPLOAD_ARTIFACT_USE__": frozenset(
        {".github/workflows/outbox-final-attempt-reaper.yml"}
    )
}
ALLOWED_WRITE_WORKFLOWS: set[str] = set()


def allowed_use(value: str, workflow: str | None = None) -> bool:
    if value.startswith(ALLOWED_LOCAL_USES_PREFIXES):
        return True
    allowed_workflows = ALLOWED_EXTERNAL_USES.get(value)
    if allowed_workflows is None:
        return False
    return workflow is None or workflow in allowed_workflows


def main() -> int:
    failures: list[str] = []
    files = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
    if not files:
        print("workflow action policy failed: no workflow files", file=sys.stderr)
        return 1

    for value, workflows in sorted(ALLOWED_EXTERNAL_USES.items()):
        if "@" not in value:
            failures.append(f"allowlisted action is missing immutable ref: {value}")
            continue
        owner_repo, reference = value.rsplit("@", 1)
        if not owner_repo.startswith("actions/") or not SHA40.fullmatch(reference):
            failures.append(
                f"allowlisted action is not a GitHub first-party 40-hex pin: {value}"
            )
        if not workflows:
            failures.append(f"allowlisted action has no bounded workflow: {value}")

    immutable_fetch_workflows = 0
    observed_external: Counter[tuple[str, str]] = Counter()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if "\\r" in text or not text.endswith("\\n"):
            failures.append(f"{relative}: workflow must be LF with trailing newline")
        for number, line in enumerate(text.splitlines(), 1):
            match = USES.match(line)
            if match:
                value = match.group("value").strip("'\\\"")
                if value.startswith(ALLOWED_LOCAL_USES_PREFIXES):
                    pass
                elif allowed_use(value, relative):
                    observed_external[(value, relative)] += 1
                else:
                    failures.append(
                        f"{relative}:{number}: unapproved external action/reusable "
                        f"workflow is forbidden: {value}"
                    )
            permission = WRITE_PERMISSION.match(line)
            if permission and relative not in ALLOWED_WRITE_WORKFLOWS:
                failures.append(
                    f"{relative}:{number}: write permission is forbidden: "
                    f"{permission.group('scope')}"
                )
        if "pull_request_target:" in text:
            failures.append(f"{relative}: pull_request_target is forbidden")
        if "persist-credentials: true" in text:
            failures.append(f"{relative}: persistent checkout credentials are forbidden")
        if MOVABLE_CANDIDATE_FETCH in text:
            failures.append(
                f"{relative}: movable branch fetch is forbidden; fetch CANDIDATE_SHA directly"
            )

        has_candidate_fetch = (
            "CANDIDATE_SHA:" in text
            and 'git -C "$GITHUB_WORKSPACE" fetch' in text
        )
        if has_candidate_fetch:
            immutable_fetch_workflows += 1
            if IMMUTABLE_CANDIDATE_FETCH not in text:
                failures.append(
                    f"{relative}: exact candidate fetch does not contain "
                    f"{IMMUTABLE_CANDIDATE_FETCH}"
                )
            if 'rev-parse HEAD)" = "$CANDIDATE_SHA"' not in text:
                failures.append(f"{relative}: checked-out SHA is not asserted")

    expected_external = {
        (value, workflow)
        for value, workflows in ALLOWED_EXTERNAL_USES.items()
        for workflow in workflows
    }
    for key in sorted(expected_external):
        count = observed_external[key]
        if count != 1:
            failures.append(
                f"{key[1]}: expected exactly one use of {key[0]}, observed {count}"
            )

    if failures:
        print("workflow action policy failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "workflow action policy: OK "
        f"({len(files)} workflows; {immutable_fetch_workflows} immutable candidate "
        f"fetchers; {sum(observed_external.values())} exact first-party action use; "
        "no unapproved external actions, movable candidate fetches or write permissions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.replace("__UPLOAD_ARTIFACT_USE__", UPLOAD_ARTIFACT_USE)
    path.write_text(source, encoding="utf-8")


def patch_workflow_policy_tests() -> None:
    path = ROOT / "tests/control_plane/test_workflow_action_policy.py"
    source = '''from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-workflow-action-policy.py"
EXACT_USE = "__UPLOAD_ARTIFACT_USE__"
OUTBOX_WORKFLOW = ".github/workflows/outbox-final-attempt-reaper.yml"


class WorkflowActionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "check_workflow_action_policy", SCRIPT
        )
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_local_and_exact_first_party_action_are_allowed(self) -> None:
        self.assertTrue(
            self.module.allowed_use("./.github/actions/local", OUTBOX_WORKFLOW)
        )
        self.assertTrue(self.module.allowed_use(EXACT_USE, OUTBOX_WORKFLOW))

    def test_exact_action_is_bound_to_only_the_evidence_workflow(self) -> None:
        self.assertFalse(
            self.module.allowed_use(EXACT_USE, ".github/workflows/plan-contract.yml")
        )

    def test_mutable_near_miss_and_other_external_actions_are_rejected(self) -> None:
        self.assertFalse(
            self.module.allowed_use("actions/upload-artifact@v7", OUTBOX_WORKFLOW)
        )
        self.assertFalse(
            self.module.allowed_use(EXACT_USE[:-1] + "0", OUTBOX_WORKFLOW)
        )
        self.assertFalse(
            self.module.allowed_use(
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                OUTBOX_WORKFLOW,
            )
        )
        self.assertFalse(
            self.module.allowed_use(
                "third-party/example@043fb460e6257d1ca154e89a5e86196c74e480f8",
                OUTBOX_WORKFLOW,
            )
        )

    def test_external_allowlist_is_exact_first_party_immutable_sha(self) -> None:
        for value, workflows in self.module.ALLOWED_EXTERNAL_USES.items():
            owner_repo, reference = value.rsplit("@", 1)
            self.assertTrue(owner_repo.startswith("actions/"))
            self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{40}", reference))
            self.assertEqual(workflows, frozenset({OUTBOX_WORKFLOW}))

    def test_repository_policy_passes(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = self.module.main()
        self.assertEqual(status, 0, stderr.getvalue())
        self.assertIn("workflow action policy: OK", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
'''.replace("__UPLOAD_ARTIFACT_USE__", UPLOAD_ARTIFACT_USE)
    path.write_text(source, encoding="utf-8")


def patch_documentation() -> None:
    path = ROOT / "docs/development/OUTBOX_FINAL_ATTEMPT_REAPER.md"
    replace_once(
        path,
        """The deterministic raw archive is retained through the native Actions Results
artifact service and finalized with its SHA-256 digest. Logs or summaries
without that retained archive receive no evidence credit.
""",
        f"""The deterministic raw `.tar.gz` archive is hashed before upload and retained by
GitHub's first-party `actions/upload-artifact` action pinned to immutable commit
`{UPLOAD_ARTIFACT_PIN}`. The summary binds the raw archive SHA-256 separately from
the service artifact ID, URL and digest. Logs or summaries without the retained
archive and those identities receive no evidence credit.
""",
        "outbox retained-evidence documentation",
    )


def main() -> None:
    patch_expected_crash_capture()
    patch_outbox_workflow()
    patch_workflow_policy()
    patch_workflow_policy_tests()
    patch_documentation()
    print(
        "outbox final-attempt expected exits and retained evidence transport patched; "
        f"artifact action pinned to {UPLOAD_ARTIFACT_PIN}"
    )


if __name__ == "__main__":
    main()
