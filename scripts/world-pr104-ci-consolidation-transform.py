#!/usr/bin/env python3
"""Consolidate the World candidate CI surface into six non-overlapping workflows."""

from __future__ import annotations

import argparse
from pathlib import Path
import textwrap


OBSOLETE_WORKFLOWS = (
    "trnm-world-authority-cutover.yml",
    "trnm-world-cex-sequence-50-qualification.yml",
    "trnm-world-gap-closure-v4.yml",
    "trnm-world-repository-contract-v2.yml",
    "trnm-world-v4-final-gates.yml",
    "trnm-world-v5-closure-contract.yml",
    "trnm-world-v6-admission.yml",
    "world-pr-native-admission-v1.yml",
)

CHECKER = r'''#!/usr/bin/env python3
"""Reject mutable, overlapping, unpinned, or documentation-drifted World CI."""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PINNED_USE = re.compile(r"^\s*-?\s*uses:\s*[^@\s]+@([0-9a-f]{40})\s*(?:#.*)?$")
FORBIDDEN = (
    (re.compile(r"contents:\s*write"), "workflows must not write repository contents"),
    (re.compile(r"persist-credentials:\s*true"), "checkout credentials must not persist"),
    (re.compile(r"pull_request_target:"), "untrusted PR code must not run with target privileges"),
    (re.compile(r"clippy\s+--fix"), "CI must not rewrite Rust source"),
    (re.compile(r"\bgit\s+push(?:\s|$)"), "CI must not push source or tags"),
    (re.compile(r"\bgit\s+commit(?:\s|$)"), "CI must not create source commits"),
    (re.compile(r"\bgit\s+tag(?:\s|$)"), "CI must not mint verified tags"),
    (re.compile(r"\bgh\s+pr\s+merge(?:\s|$)"), "CI must not merge pull requests"),
    (re.compile(r"\bupdate-ref\b"), "CI must not move Git refs"),
)
REQUIRED_DOCS = {
    "docs/development/TRILLIONNIUM_WORLD_DEVELOPMENT_PLAN_2026-08-29.md",
    "docs/development/trillionnium-world-development-plan-2026-08-29.json",
    "docs/development/trnm-world-gap-closure-ledger-v4.json",
    "docs/development/trnm-world-module-decomposition-v1.md",
    "docs/development/trnm-world-testing-strategy-v2.md",
    "docs/protocol/trnm-world-transition-v1.md",
    "docs/protocol/schemas/trnm-world-transition-v1.schema.json",
    "docs/protocol/vectors/trnm-world-transition-v1.json",
    "docs/protocol/vectors/trnm-world-transition-negative-v1.json",
    "docs/status/v4-candidate-v1.json",
    "docs/status/v4-candidate-v1.schema.json",
    "docs/catalog.json",
    "docs/adr/0003-world-domain-authority-and-nakama-canonical-online.md",
    "docs/architecture/cex-world-authority-cutover-v1.md",
    "docs/contracts/trillionnium-world-authority-cutover-v1.json",
    "docs/contracts/trillionnium-world-authority-provenance-v1.json",
    "docs/modules/world-authority/README.md",
    "trillionnium/crates/world-authority/Cargo.toml",
    "trillionnium/crates/world-authority/README.md",
    "scripts/check-trnm-world-documentation.py",
    "scripts/check-trnm-world-transition-conformance.py",
    "scripts/check-trnm-world-v4-candidate.py",
    "scripts/test-trnm-world-v4-candidate-negative.py",
    "scripts/check-trnm-world-detailed-documentation.py",
    "scripts/test-trnm-world-detailed-documentation.py",
    "scripts/check-trnm-world-authority-documentation.py",
    "scripts/test-trnm-world-authority-documentation.py",
    "scripts/check-trnm-world-authority-cutover.sh",
    "scripts/check-trnm-world-authority-postgres-installation.sh",
}


def fail(message: str) -> None:
    raise SystemExit(f"TRNM World CI integrity: FAIL: {message}")


# Closed-world current workflow inventory. Historical V4/V5 workflows and
# duplicate owners of protected contexts are deliberately absent.
WORKFLOW_JOBS = {
    "trnm-world-module-documentation.yml": {
        "module-documentation": "trnm-world/module-documentation",
    },
    "trnm-world-postgres-cutover.yml": {
        "postgres-cutover": "trnm-world-postgres-cutover",
    },
    "trnm-world-repository-contract-v3.yml": {
        "repository-contract": "trnm-world-v6/repository-contract",
    },
    "trnm-world-rts-intake-contract.yml": {
        "intake-contract": "trnm-world-v6/rts-intake-contract",
    },
    "trnm-world-v6-admission-v2.yml": {
        "truth": "trnm-world-v6/truth-head-and-merge",
        "source": "trnm-game-ci",
        "postgres": "trnm-world-p0-boundaries",
        "package": "trnm-world-status-evidence",
        "required": "trnm-world-v6/required-v2",
    },
    "trnm-world-v6-external-evidence-contract.yml": {
        "external-evidence-contract": "trnm-world-v6/external-evidence-contract",
    },
}
PROTECTED_CONTEXT_OWNERS = {
    "trnm-game-ci": "trnm-world-v6-admission-v2.yml",
    "trnm-world-p0-boundaries": "trnm-world-v6-admission-v2.yml",
    "trnm-world-status-evidence": "trnm-world-v6-admission-v2.yml",
}


def _read_workflow(path: pathlib.Path) -> str:
    if path.is_symlink() or not path.is_file():
        fail(f"workflow is missing or linked: {path.name}")
    with path.open("rb") as handle:
        data = handle.read(256 * 1024 + 1)
    if not data.strip() or len(data) > 256 * 1024:
        fail(f"workflow is empty or exceeds the 256 KiB budget: {path.name}")
    return data.decode("utf-8")


def workflow_inventory(root: pathlib.Path) -> dict[str, str]:
    """Validate the closed current workflow set and unique static contexts."""
    folder = root / ".github/workflows"
    if folder.is_symlink() or not folder.is_dir():
        fail("workflow directory is missing or linked")
    files = sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])
    actual_files = {path.name for path in files}
    expected_files = set(WORKFLOW_JOBS)
    if actual_files != expected_files:
        fail(
            "active workflow inventory differs from reviewed mapping; "
            f"missing={sorted(expected_files - actual_files)}, "
            f"extra={sorted(actual_files - expected_files)}"
        )

    contexts: dict[str, str] = {}
    texts: dict[str, str] = {}
    for path in files:
        text = _read_workflow(path)
        texts[path.name] = text
        if not re.search(r"(?m)^permissions:\n  contents: read\s*$", text):
            fail(f"{path.name} must declare global read-only contents permission")
        if len(re.findall(r"(?m)^\s*permissions:", text)) != 1:
            fail(f"{path.name} has a missing/ambiguous permissions block")
        if re.search(r"(?m)^\s*[a-z_-]+:\s*(?:write|write-all)\s*(?:#.*)?$", text):
            fail(f"{path.name} declares write permission")
        if "runs-on: ubuntu-latest" in text:
            fail(f"{path.name} uses mutable ubuntu-latest")
        for pattern, reason in FORBIDDEN:
            if pattern.search(text):
                fail(f"{path.name}: {reason} ({pattern.pattern})")
        for line_number, line in enumerate(text.splitlines(), 1):
            if "uses:" in line and PINNED_USE.match(line) is None:
                fail(f"{path.name}:{line_number} action is not pinned to 40 hex characters")
        if text.count("\njobs:\n") != 1:
            fail(f"{path.name} must have one ordinary jobs mapping")
        parts = re.split(r"(?m)^  ([A-Za-z_][A-Za-z_0-9-]*):\n", text.split("\njobs:\n", 1)[1])
        identifiers = parts[1::2]
        if len(identifiers) != len(set(identifiers)):
            fail(f"{path.name} has duplicate job identifiers")
        actual: dict[str, str] = {}
        for name, body in zip(identifiers, parts[2::2]):
            names = re.findall(r"(?m)^    name: ([a-z0-9][a-z0-9/-]*)\s*$", body)
            if len(names) != 1:
                fail(f"{path.name}/{name} must have one static job name")
            context = names[0]
            if context in contexts:
                fail(f"duplicate check context {context}: {contexts[context]} and {path.name}")
            contexts[context] = path.name
            actual[name] = context
        if actual != WORKFLOW_JOBS[path.name]:
            fail(f"{path.name} job/context ownership differs from reviewed mapping")

    for context, owner in PROTECTED_CONTEXT_OWNERS.items():
        if contexts.get(context) != owner:
            fail(f"protected context {context} is not uniquely owned by {owner}")

    admission = texts["trnm-world-v6-admission-v2.yml"]
    trigger_block = admission.split("\npermissions:\n", 1)[0]
    if "\n  pull_request:\n" not in trigger_block or "\n  workflow_dispatch:\n" not in trigger_block:
        fail("canonical V6 admission must run unconditionally on pull_request and workflow_dispatch")
    if re.search(r"(?m)^    paths(?:-ignore)?:", trigger_block):
        fail("canonical V6 pull-request admission cannot be path filtered")
    for marker in (
        "HEAD_SHA:",
        "BASE_SHA:",
        "MERGE_SHA:",
        'rev-parse HEAD^1)" = "$BASE_SHA"',
        'rev-parse HEAD^2)" = "$HEAD_SHA"',
        "run-trnm-world-v6-qualification-v2.sh",
    ):
        if marker not in admission:
            fail(f"canonical V6 admission is missing exact-object marker: {marker}")
    if admission.count("run-trnm-world-v6-qualification-v2.sh") < 8:
        fail("canonical V6 admission does not qualify truth/source/postgres/package on both objects")

    return contexts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument(
        "--workflows-only",
        action="store_true",
        help="static inventory only; no document, candidate or execution credit",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        contexts = workflow_inventory(root)
        if not args.workflows_only:
            for relative in sorted(REQUIRED_DOCS):
                path = root / relative
                if path.is_symlink() or not path.is_file() or not path.read_text(encoding="utf-8").strip():
                    fail(f"required current/historical contract is missing or empty: {relative}")
            for relative, arguments in (
                ("scripts/check-trnm-world-documentation.py", [str(root)]),
                ("scripts/check-trnm-world-detailed-documentation.py", [str(root)]),
                ("scripts/test-trnm-world-detailed-documentation.py", []),
                ("scripts/check-trnm-world-authority-documentation.py", [str(root)]),
                ("scripts/test-trnm-world-authority-documentation.py", []),
                ("scripts/check-trnm-world-v4-candidate.py", []),
                ("scripts/test-trnm-world-v4-candidate-negative.py", []),
            ):
                result = subprocess.run(
                    [sys.executable, str(root / relative), *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                    cwd=root,
                    timeout=180,
                )
                if result.returncode != 0:
                    fail(relative + " failed: " + (result.stderr.strip() or result.stdout.strip()))
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        fail(str(error))
    scope = (
        "workflow inventory only"
        if args.workflows_only
        else "workflow inventory, current documentation and historical schema"
    )
    print(
        f"TRNM World CI integrity: PASS ({len(WORKFLOW_JOBS)} workflows, "
        f"{len(contexts)} unique contexts; {scope}; no hosted/governance evidence)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

TESTS = r'''#!/usr/bin/env python3
"""Offline regressions of the consolidated World workflow inventory."""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SCRIPT = pathlib.Path(__file__).with_name("check-trnm-world-ci-integrity.py")
ROOT = SCRIPT.resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_integrity", SCRIPT)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
ADMISSION = "trnm-world-v6-admission-v2.yml"
MODULE = "trnm-world-module-documentation.yml"
POSTGRES = "trnm-world-postgres-cutover.yml"
REPOSITORY = "trnm-world-repository-contract-v3.yml"
RTS = "trnm-world-rts-intake-contract.yml"
EXTERNAL = "trnm-world-v6-external-evidence-contract.yml"


class WorkflowInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.folder = self.root / ".github/workflows"
        shutil.copytree(ROOT / ".github/workflows", self.folder)

    def edit(self, name, before, after):
        path = self.folder / name
        source = path.read_text()
        self.assertIn(before, source)
        path.write_text(source.replace(before, after, 1))

    def reject(self):
        with self.assertRaises(SystemExit):
            checker.workflow_inventory(self.root)

    def test_reviewed_six_workflows_have_ten_unique_contexts(self):
        contexts = checker.workflow_inventory(self.root)
        self.assertEqual(len(contexts), 10)
        self.assertEqual(contexts["trnm-game-ci"], ADMISSION)
        self.assertEqual(contexts["trnm-world-p0-boundaries"], ADMISSION)
        self.assertEqual(contexts["trnm-world-status-evidence"], ADMISSION)
        self.assertEqual(contexts["trnm-world-v6/truth-head-and-merge"], ADMISSION)
        self.assertEqual(contexts["trnm-world-v6/required-v2"], ADMISSION)
        self.assertEqual(contexts["trnm-world/module-documentation"], MODULE)
        self.assertEqual(contexts["trnm-world-postgres-cutover"], POSTGRES)
        self.assertEqual(contexts["trnm-world-v6/repository-contract"], REPOSITORY)
        self.assertEqual(contexts["trnm-world-v6/rts-intake-contract"], RTS)
        self.assertEqual(contexts["trnm-world-v6/external-evidence-contract"], EXTERNAL)

    def test_ephemeral_commit_tree_is_allowed_but_source_commit_is_not(self):
        checker.workflow_inventory(self.root)
        original = (self.folder / POSTGRES).read_text()
        self.assertIn("git commit-tree", original)
        (self.folder / POSTGRES).write_text(original + "\n# forbidden: git commit candidate\n")
        self.reject()

    def test_removed_workflow_rejected(self):
        (self.folder / ADMISSION).unlink()
        self.reject()

    def test_extra_workflow_rejected(self):
        (self.folder / "unreviewed.yml").write_text("name: surprise\n")
        self.reject()

    def test_extra_yaml_extension_rejected(self):
        (self.folder / "unreviewed.yaml").write_text("name: surprise\n")
        self.reject()

    def test_linked_workflow_rejected(self):
        path = self.folder / ADMISSION
        copy = self.root / "outside.yml"
        path.rename(copy)
        path.symlink_to(copy)
        self.reject()

    def test_linked_workflow_directory_rejected(self):
        original = self.root / "outside"
        self.folder.rename(original)
        self.folder.symlink_to(original, target_is_directory=True)
        self.reject()

    def test_empty_oversized_and_non_utf8_workflow_rejected(self):
        path = self.folder / ADMISSION
        for content in (b"", b" \n", b" " * (256 * 1024 + 1), b"\xff"):
            path.write_bytes(content)
            with self.subTest(content=content[:4]):
                with self.assertRaises((SystemExit, UnicodeError)):
                    checker.workflow_inventory(self.root)

    def test_primary_context_cannot_move_to_narrower_workflow(self):
        self.edit(ADMISSION, "name: trnm-game-ci", "name: trnm-world-v6/source-head-and-merge")
        self.edit(MODULE, "name: trnm-world/module-documentation", "name: trnm-game-ci")
        self.reject()

    def test_duplicate_primary_context_rejected(self):
        self.edit(MODULE, "name: trnm-world/module-documentation", "name: trnm-game-ci")
        self.reject()

    def test_duplicate_aggregate_context_rejected(self):
        self.edit(EXTERNAL, "name: trnm-world-v6/external-evidence-contract", "name: trnm-world-v6/required-v2")
        self.reject()

    def test_missing_job_rejected(self):
        self.edit(ADMISSION, "  source:\n", "  renamed-source:\n")
        self.reject()

    def test_duplicate_job_identifier_rejected(self):
        path = self.folder / ADMISSION
        path.write_text(path.read_text() + "\n  source:\n    name: trnm-world-v6/other\n")
        self.reject()

    def test_duplicate_jobs_mapping_rejected(self):
        path = self.folder / ADMISSION
        path.write_text(path.read_text() + "\njobs:\n  other:\n    name: trnm-world-v6/other\n")
        self.reject()

    def test_missing_static_job_name_rejected(self):
        self.edit(ADMISSION, "    name: trnm-game-ci\n", "")
        self.reject()

    def test_dynamic_name_rejected(self):
        self.edit(ADMISSION, "name: trnm-game-ci", "name: ${{ matrix.context }}")
        self.reject()

    def test_quoted_or_commented_marker_cannot_supply_job_name(self):
        self.edit(ADMISSION, "    name: trnm-game-ci", "    # name: trnm-game-ci")
        self.reject()

    def test_missing_read_permission_rejected(self):
        self.edit(ADMISSION, "permissions:\n  contents: read\n", "")
        self.reject()

    def test_write_permissions_rejected(self):
        original = (self.folder / ADMISSION).read_text()
        for value in ("contents: write", "contents:  write # unauthorized", "issues: write"):
            (self.folder / ADMISSION).write_text(original.replace("contents: read", value))
            with self.subTest(value=value):
                self.reject()

    def test_job_permission_override_rejected(self):
        self.edit(ADMISSION, "    runs-on: ubuntu-24.04", "    permissions:\n      contents: read\n    runs-on: ubuntu-24.04")
        self.reject()

    def test_mutable_action_rejected(self):
        self.edit(ADMISSION, "actions/checkout@11d5960a326750d5838078e36cf38b85af677262", "actions/checkout@v4")
        self.reject()

    def test_mutable_runner_rejected(self):
        self.edit(ADMISSION, "runs-on: ubuntu-24.04", "runs-on: ubuntu-latest")
        self.reject()

    def test_privileged_trigger_rejected(self):
        self.edit(ADMISSION, "  pull_request:", "  pull_request_target:")
        self.reject()

    def test_path_filtered_canonical_admission_rejected(self):
        self.edit(ADMISSION, "  pull_request:\n", "  pull_request:\n    paths:\n      - scripts/**\n")
        self.reject()

    def test_missing_exact_merge_parent_check_rejected(self):
        self.edit(ADMISSION, '            test "$(git rev-parse HEAD^2)" = "$HEAD_SHA"\n', "")
        self.reject()

    def test_source_mutation_and_retained_credentials_rejected(self):
        original = (self.folder / ADMISSION).read_text()
        for marker in ("git push", "git commit", "git tag", "gh pr merge", "update-ref", "clippy --fix", "persist-credentials: true"):
            (self.folder / ADMISSION).write_text(original + "\n# forbidden: " + marker + "\n")
            with self.subTest(marker=marker):
                self.reject()

    def test_all_other_reviewed_workflow_names_are_required(self):
        for name in sorted(set(checker.WORKFLOW_JOBS) - {ADMISSION}):
            with self.subTest(name=name):
                data = (self.folder / name).read_bytes()
                (self.folder / name).unlink()
                self.reject()
                (self.folder / name).write_bytes(data)

    def test_cli_narrow_mode_does_not_claim_full_validation(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "--workflows-only"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workflow inventory only", result.stdout)
        self.assertIn("no hosted/governance evidence", result.stdout)

    def test_default_mode_does_not_skip_missing_documentation(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required current/historical contract is missing", result.stderr)

    def test_child_failure_and_timeout_are_not_swallowed(self):
        for relative in checker.REQUIRED_DOCS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture input, not real evidence\n")
        with patch.object(sys, "argv", [str(SCRIPT), "--root", str(self.root)]):
            for outcome in (
                subprocess.CompletedProcess([], 1, "", "fixture failed"),
                subprocess.TimeoutExpired("fixture", 180),
            ):
                with self.subTest(outcome=type(outcome).__name__):
                    kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                    with patch.object(checker.subprocess, "run", **kwargs):
                        with self.assertRaises(SystemExit):
                            checker.main()

    def test_inventory_check_does_not_write_source(self):
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        checker.workflow_inventory(self.root)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    workflows = root / ".github/workflows"

    for name in OBSOLETE_WORKFLOWS:
        path = workflows / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"obsolete workflow missing or unsafe: {name}")
        path.unlink()

    admission = workflows / "trnm-world-v6-admission-v2.yml"
    replace_once(admission, "name: trnm-world-v6/source-head-and-merge", "name: trnm-game-ci", "source context")
    replace_once(admission, "name: trnm-world-v6/postgres-head-and-merge", "name: trnm-world-p0-boundaries", "postgres context")
    replace_once(admission, "name: trnm-world-v6/package-head-and-merge", "name: trnm-world-status-evidence", "package context")

    repository = workflows / "trnm-world-repository-contract-v3.yml"
    replace_once(repository, "name: trnm-world-v5/repository-contract-v3", "name: trnm-world-v6/repository-contract", "repository context")
    replace_once(
        repository,
        "      - fix/world-plan-v4-development-closure-20260831\n",
        "      - 'fix/world-*'\n      - 'integration/world-*'\n",
        "repository push branches",
    )

    rts = workflows / "trnm-world-rts-intake-contract.yml"
    replace_once(rts, "name: trnm-world-v4/rts-intake-contract", "name: trnm-world-v6/rts-intake-contract", "RTS context")
    replace_once(rts, "      - fix/world-plan-v4-development-closure-20260831\n", "      - main\n      - 'fix/world-*'\n      - 'integration/world-*'\n", "RTS push branches")
    replace_once(rts, "      RUST_TOOLCHAIN: 1.98.0", "      RUST_TOOLCHAIN: 1.98.1", "RTS toolchain")

    (root / "scripts/check-trnm-world-ci-integrity.py").write_text(textwrap.dedent(CHECKER), encoding="utf-8")
    (root / "scripts/test-trnm-world-ci-integrity.py").write_text(textwrap.dedent(TESTS), encoding="utf-8")

    current = sorted(path.name for path in workflows.iterdir() if path.suffix in {".yml", ".yaml"})
    expected = sorted(
        (
            "trnm-world-module-documentation.yml",
            "trnm-world-postgres-cutover.yml",
            "trnm-world-repository-contract-v3.yml",
            "trnm-world-rts-intake-contract.yml",
            "trnm-world-v6-admission-v2.yml",
            "trnm-world-v6-external-evidence-contract.yml",
        )
    )
    if current != expected:
        raise RuntimeError(f"workflow consolidation drift: current={current}, expected={expected}")
    print("WORLD_CI_CONSOLIDATION_TRANSFORM=PASS workflows=6 contexts=10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
