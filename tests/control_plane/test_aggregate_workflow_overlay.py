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
    "trnm_required_aggregate_overlay_gate", SCRIPT
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


def write_base(root: Path) -> tuple[Path, Path]:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    aggregate = workflows / "aggregate.yml"
    worker = workflows / "worker.yml"
    aggregate.write_text("name: aggregate\non:\n  pull_request:\n", encoding="utf-8")
    worker.write_text("name: worker\non:\n  pull_request:\n", encoding="utf-8")

    manifest_path = root / "docs" / "governance" / "REQUIRED_WORKFLOWS_V1.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "schema": GATE.SCHEMA,
        "repository": "owner/repository",
        "event": "pull_request",
        "requirements": {"reject_unlisted_exact_head_workflows": True},
        "aggregate_workflow": {
            "workflow_id": 10,
            "name": "aggregate",
            "path": ".github/workflows/aggregate.yml",
            "git_blob_sha1": GATE.blob_sha(aggregate),
            "allowed_events": ["pull_request"],
            "excluded_from_external_collection": True,
        },
        "workflows": [
            {
                "workflow_id": 20,
                "name": "worker",
                "path": ".github/workflows/worker.yml",
                "git_blob_sha1": GATE.blob_sha(worker),
                "allowed_events": ["pull_request"],
                "minimum_successful_execution_jobs": 1,
            }
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return manifest_path, aggregate


def write_aggregate_overlay(
    manifest_path: Path,
    aggregate: Path,
    *,
    workflow_id: int = 10,
    name: str = "aggregate",
    path: str = ".github/workflows/aggregate.yml",
    base_aggregate_sha: str,
) -> Path:
    value = {
        "schema": GATE.AGGREGATE_OVERLAY_SCHEMA,
        "base_manifest_path": manifest_path.as_posix(),
        "base_manifest_blob_sha1": GATE.blob_sha(manifest_path),
        "base_aggregate_blob_sha1": base_aggregate_sha,
        "repository": "owner/repository",
        "event": "pull_request",
        "aggregate_workflow": {
            "workflow_id": workflow_id,
            "name": name,
            "path": path,
            "git_blob_sha1": GATE.blob_sha(aggregate),
            "allowed_events": ["pull_request"],
            "minimum_successful_execution_jobs": 1,
        },
    }
    value["overlay_sha256"] = GATE.canonical_aggregate_overlay_digest(value)
    overlay_path = manifest_path.with_name(GATE.AGGREGATE_OVERLAY_FILENAME)
    overlay_path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return overlay_path


class AggregateWorkflowOverlayTests(unittest.TestCase):
    def test_replacement_updates_only_aggregate_definition_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, aggregate = write_base(root)
            original_sha = GATE.blob_sha(aggregate)
            aggregate.write_text(
                "name: aggregate\non:\n  pull_request:\n    types: [opened, edited, synchronize, reopened]\n",
                encoding="utf-8",
            )
            write_aggregate_overlay(
                manifest_path,
                aggregate,
                base_aggregate_sha=original_sha,
            )
            composed = GATE.load_composed_manifest(manifest_path)
            self.assertEqual(composed.aggregate.git_blob_sha1, GATE.blob_sha(aggregate))
            self.assertEqual(GATE.verify_files(root, composed), [])

    def test_base_aggregate_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, aggregate = write_base(root)
            aggregate.write_text(
                "name: aggregate\non:\n  pull_request:\n# changed\n",
                encoding="utf-8",
            )
            write_aggregate_overlay(
                manifest_path,
                aggregate,
                base_aggregate_sha="0" * 40,
            )
            with self.assertRaisesRegex(ValueError, "base aggregate drift"):
                GATE.load_composed_manifest(manifest_path)

    def test_aggregate_identity_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, aggregate = write_base(root)
            original_sha = GATE.Manifest.load(manifest_path).aggregate.git_blob_sha1
            aggregate.write_text(
                "name: aggregate\non:\n  pull_request:\n# changed\n",
                encoding="utf-8",
            )
            write_aggregate_overlay(
                manifest_path,
                aggregate,
                workflow_id=99,
                base_aggregate_sha=original_sha,
            )
            with self.assertRaisesRegex(ValueError, "not workflow identity"):
                GATE.load_composed_manifest(manifest_path)

    def test_aggregate_overlay_digest_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, aggregate = write_base(root)
            original_sha = GATE.blob_sha(aggregate)
            aggregate.write_text(
                "name: aggregate\non:\n  pull_request:\n# changed\n",
                encoding="utf-8",
            )
            overlay_path = write_aggregate_overlay(
                manifest_path,
                aggregate,
                base_aggregate_sha=original_sha,
            )
            raw = json.loads(overlay_path.read_text(encoding="utf-8"))
            raw["aggregate_workflow"]["minimum_successful_execution_jobs"] = 2
            overlay_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                GATE.load_composed_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
