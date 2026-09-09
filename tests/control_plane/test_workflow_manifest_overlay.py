from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-required-workflow-runs.py"
SPEC = importlib.util.spec_from_file_location(
    "trnm_required_workflow_overlay_gate", SCRIPT
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


def requirement(
    workflow_id: int,
    name: str,
    path: str,
    sha: str,
) -> dict[str, object]:
    return {
        "workflow_id": workflow_id,
        "name": name,
        "path": path,
        "git_blob_sha1": sha,
        "allowed_events": ["pull_request"],
        "minimum_successful_execution_jobs": 1,
    }


def write_base(root: Path) -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    aggregate_source = "name: aggregate\non:\n  pull_request:\n"
    worker_source = "name: worker\non:\n  pull_request:\n"
    (workflows / "aggregate.yml").write_text(
        aggregate_source, encoding="utf-8"
    )
    (workflows / "worker.yml").write_text(
        worker_source, encoding="utf-8"
    )
    manifest_path = root / "docs" / "governance" / "REQUIRED_WORKFLOWS_V1.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "schema": GATE.SCHEMA,
        "repository": "owner/repository",
        "event": "pull_request",
        "requirements": {
            "reject_unlisted_exact_head_workflows": True,
        },
        "aggregate_workflow": {
            "workflow_id": 10,
            "path": ".github/workflows/aggregate.yml",
            "git_blob_sha1": GATE.blob_sha(workflows / "aggregate.yml"),
            "allowed_events": ["pull_request"],
            "excluded_from_external_collection": True,
        },
        "workflows": [
            requirement(
                20,
                "worker",
                ".github/workflows/worker.yml",
                GATE.blob_sha(workflows / "worker.yml"),
            )
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return manifest_path


def write_overlay(path: Path, value: dict[str, object]) -> None:
    value["overlay_sha256"] = GATE.canonical_overlay_digest(value)
    path.with_name(GATE.OVERLAY_FILENAME).write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


class WorkflowOverlayTests(unittest.TestCase):
    def test_replacement_and_addition_form_closed_composed_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_base(root)
            added = root / ".github" / "workflows" / "added.yml"
            added.write_text(
                "name: added\non:\n  pull_request:\n", encoding="utf-8"
            )
            worker = root / ".github" / "workflows" / "worker.yml"
            worker.write_text(
                "name: worker\non:\n  pull_request:\n# revision two\n",
                encoding="utf-8",
            )
            overlay = {
                "schema": GATE.OVERLAY_SCHEMA,
                "base_manifest_path": manifest_path.as_posix(),
                "base_manifest_blob_sha1": GATE.blob_sha(manifest_path),
                "repository": "owner/repository",
                "event": "pull_request",
                "replace_workflows": [
                    requirement(
                        20,
                        "worker",
                        ".github/workflows/worker.yml",
                        GATE.blob_sha(worker),
                    )
                ],
                "add_workflows": [
                    requirement(
                        30,
                        "added",
                        ".github/workflows/added.yml",
                        GATE.blob_sha(added),
                    )
                ],
                "remove_workflow_ids": [],
                "composed_external_workflow_count": 2,
            }
            write_overlay(manifest_path, overlay)
            composed = GATE.load_composed_manifest(manifest_path)
            self.assertEqual(len(composed.workflows), 2)
            self.assertEqual(
                GATE.verify_files(root, composed),
                [],
            )

    def test_base_manifest_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_base(root)
            overlay = {
                "schema": GATE.OVERLAY_SCHEMA,
                "base_manifest_path": manifest_path.as_posix(),
                "base_manifest_blob_sha1": "0" * 40,
                "repository": "owner/repository",
                "event": "pull_request",
                "replace_workflows": [],
                "add_workflows": [],
                "remove_workflow_ids": [],
                "composed_external_workflow_count": 1,
            }
            write_overlay(manifest_path, overlay)
            with self.assertRaisesRegex(ValueError, "base manifest drift"):
                GATE.load_composed_manifest(manifest_path)

    def test_replacement_cannot_change_workflow_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_base(root)
            overlay = {
                "schema": GATE.OVERLAY_SCHEMA,
                "base_manifest_path": manifest_path.as_posix(),
                "base_manifest_blob_sha1": GATE.blob_sha(manifest_path),
                "repository": "owner/repository",
                "event": "pull_request",
                "replace_workflows": [
                    requirement(
                        20,
                        "replacement",
                        ".github/workflows/replacement.yml",
                        "1" * 40,
                    )
                ],
                "add_workflows": [],
                "remove_workflow_ids": [],
                "composed_external_workflow_count": 1,
            }
            write_overlay(manifest_path, overlay)
            with self.assertRaisesRegex(ValueError, "not workflow identity"):
                GATE.load_composed_manifest(manifest_path)

    def test_overlay_digest_and_composed_count_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_base(root)
            overlay = {
                "schema": GATE.OVERLAY_SCHEMA,
                "base_manifest_path": manifest_path.as_posix(),
                "base_manifest_blob_sha1": GATE.blob_sha(manifest_path),
                "repository": "owner/repository",
                "event": "pull_request",
                "replace_workflows": [],
                "add_workflows": [],
                "remove_workflow_ids": [],
                "composed_external_workflow_count": 2,
            }
            write_overlay(manifest_path, overlay)
            with self.assertRaisesRegex(ValueError, "composed count mismatch"):
                GATE.load_composed_manifest(manifest_path)
            path = manifest_path.with_name(GATE.OVERLAY_FILENAME)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["composed_external_workflow_count"] = 1
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                GATE.load_composed_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
