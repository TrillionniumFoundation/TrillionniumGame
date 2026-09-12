"""Process lane must validate current contracts, not obsolete prose.

These are narrow source/definition regressions. Native process execution,
workflow syntax, complete trigger validation and evidence admission remain
separate existing requirements.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ".github/workflows/rust-server-process-smoke.yml"
START = "      - name: Validate current canonical process and documentation boundary\n"
END = "      - name: Install exact Rust\n"
EXPECTED = (
    "        run: |",
    "          set -euo pipefail",
    "          python3 scripts/check-documentation-authority.py",
    "          python3 scripts/engineering_readiness.py",
    "          python3 scripts/check-rust-server-source-candidate.py",
)


def boundary(workflow: str) -> tuple[str, ...]:
    if workflow.count(START) != 1 or workflow.count(END) != 1:
        raise ValueError("one exact current process-boundary step is required")
    start = workflow.index(START) + len(START)
    end = workflow.index(END)
    if start >= end:
        raise ValueError("process checks must precede toolchain and execution")
    commands = tuple(workflow[start:end].splitlines())
    if commands != EXPECTED:
        raise ValueError("all current boundary checks must execute directly and fail closed")
    return commands


class ProcessDocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / WORKFLOW).read_text(encoding="utf-8")

    def reject_replacement(self, old: str, new: str) -> None:
        self.assertIn(old, self.workflow)
        with self.assertRaises(ValueError):
            boundary(self.workflow.replace(old, new, 1))

    def test_current_workflow_executes_real_contracts(self) -> None:
        self.assertEqual(boundary(self.workflow), EXPECTED)

    def test_document_authority_cannot_be_omitted(self) -> None:
        self.reject_replacement(EXPECTED[2] + "\n", "")

    def test_current_interface_inventory_cannot_be_omitted(self) -> None:
        self.reject_replacement(EXPECTED[3] + "\n", "")

    def test_source_contract_cannot_be_omitted(self) -> None:
        self.reject_replacement(EXPECTED[4] + "\n", "")

    def test_inventory_failure_cannot_be_suppressed(self) -> None:
        self.reject_replacement(EXPECTED[3], EXPECTED[3] + " || true")

    def test_commented_invocation_is_not_execution(self) -> None:
        self.reject_replacement(EXPECTED[3], "          # python3 scripts/engineering_readiness.py")

    def test_strict_shell_cannot_be_removed(self) -> None:
        # Replace only the owned block, not the earlier checkout's shell flags.
        block = START + "\n".join(EXPECTED) + "\n"
        self.reject_replacement(block, block.replace("          set -euo pipefail\n", ""))

    def test_obsolete_prose_grep_cannot_substitute(self) -> None:
        self.reject_replacement(
            EXPECTED[3],
            "          grep -q 'Standalone foundation source and process' docs/DEVELOPMENT.md",
        )

    def test_definition_pin_matches_actual_workflow_bytes(self) -> None:
        overlay = json.loads((ROOT / "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json").read_text())
        matches = [row for row in overlay["replace_workflows"] if row["workflow_id"] == 345512188]
        self.assertEqual(len(matches), 1)
        row = matches[0]
        self.assertEqual(row["path"], WORKFLOW)
        self.assertEqual(row["name"], "rust-server-process-smoke")
        self.assertEqual(row["allowed_events"], ["pull_request"])
        self.assertEqual(row["minimum_successful_execution_jobs"], 1)
        data = (ROOT / WORKFLOW).read_bytes()
        observed = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        self.assertEqual(row["git_blob_sha1"], observed)
        expected_digest = overlay.pop("overlay_sha256")
        actual_digest = hashlib.sha256(json.dumps(overlay, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(expected_digest, actual_digest)
        self.assertEqual(overlay["composed_external_workflow_count"], 55)
        self.assertEqual(overlay["remove_workflow_ids"], [])

    def test_native_execution_and_read_only_permissions_are_retained(self) -> None:
        for required in (
            "permissions:\n  contents: read\n",
            "    timeout-minutes: 20\n",
            "          rustup toolchain install 1.85.1 --profile minimal\n",
            "          rustup override set 1.85.1\n",
            "        run: bash scripts/check-rust-server-process.sh\n",
            "          test -e \"$target\"\n",
        ):
            self.assertIn(required, self.workflow)
        self.assertNotIn("continue-on-error:", self.workflow)

    def test_main_push_includes_new_contract_inputs(self) -> None:
        trigger = self.workflow.split("\npermissions:", 1)[0]
        self.assertIn("  pull_request:\n  push:\n", trigger)
        for path in (
            "scripts/engineering_readiness.py",
            "tests/control_plane/test_engineering_readiness.py",
            "tests/control_plane/test_process_documentation_contract.py",
            "docs/status/DOCUMENTATION_DEPTH.json",
            "docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json",
            "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json",
        ):
            self.assertIn("      - '" + path + "'\n", trigger)


if __name__ == "__main__":
    unittest.main()
