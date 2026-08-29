#!/usr/bin/env python3
"""Build an exact, unreviewed evidence candidate from a successful automation run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]


class EvidenceError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{path}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{path}: top level must be an object")
    return value


def artifact(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        relative = path
    return {
        "path": str(relative),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def assertion_bools(value: Any) -> Iterable[bool]:
    if isinstance(value, bool):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from assertion_bools(child)
    elif isinstance(value, list):
        for child in value:
            yield from assertion_bools(child)


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EvidenceError(f"required automation environment {name} is absent")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--type", required=True)
    parser.add_argument("--gap", action="append", default=[])
    parser.add_argument("--claim", action="append", default=[])
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--limitation", action="append", default=[])
    parser.add_argument("--review-role", action="append", default=[])
    arguments = parser.parse_args()
    try:
        require(arguments.evidence_id.startswith("TG-EV-CAND-"), "evidence ID must use TG-EV-CAND- prefix")
        require(arguments.gap, "at least one gap ID is required")
        require(all(value.startswith("GAP-P") for value in arguments.gap), "invalid gap ID")
        require(arguments.command, "at least one exact command is required")
        require(arguments.limitation, "at least one limitation is required")
        require(arguments.review_role, "at least one independent review role is required")

        candidate_path = Path(arguments.candidate_manifest)
        result_path = Path(arguments.result)
        artifact_root = Path(arguments.artifact_root)
        output_path = Path(arguments.output)
        require(candidate_path.is_file(), "candidate manifest is missing")
        require(result_path.is_file(), "source result is missing")
        require(artifact_root.is_dir(), "artifact root is missing")
        candidate = load(candidate_path)
        source_result = load(result_path)

        repository = candidate.get("repository")
        commit = candidate.get("commit")
        tree = candidate.get("tree")
        require(repository == "TrillionniumFoundation/TrillionniumGame", "candidate repository mismatch")
        require(isinstance(commit, str) and len(commit) == 40, "candidate commit invalid")
        require(isinstance(tree, str) and len(tree) == 40, "candidate tree invalid")
        require(commit == required_environment("GITHUB_SHA"), "candidate commit does not match exact automation SHA")

        assertions = list(assertion_bools(source_result.get("assertions")))
        require(assertions, "source result contains no boolean assertions")
        require(all(assertions), "source result contains a failed boolean assertion")

        artifacts = []
        output_resolved = output_path.resolve()
        for path in sorted(artifact_root.rglob("*")):
            if not path.is_file() or path.resolve() == output_resolved:
                continue
            item = artifact(path)
            require(item["size_bytes"] > 0, f"empty artifact is forbidden: {item['path']}")
            artifacts.append(item)
        require(artifacts, "artifact root produced no non-empty artifacts")

        configuration = {
            "runner_os": required_environment("RUNNER_OS"),
            "runner_arch": required_environment("RUNNER_ARCH"),
            "workflow": required_environment("GITHUB_WORKFLOW"),
            "job": required_environment("GITHUB_JOB"),
            "database_profile": os.environ.get("TRNM_DATABASE_PROFILE"),
        }
        configuration_bytes = json.dumps(
            configuration,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        evidence = {
            "schema": "trillionnium.automation-evidence-candidate.v1",
            "evidence_id": arguments.evidence_id,
            "evidence_type": arguments.type,
            "project_id": "trillionnium-game",
            "gap_ids": sorted(set(arguments.gap)),
            "claim_ids": sorted(set(arguments.claim)),
            "candidate": {
                "repository": repository,
                "commit": commit,
                "tree": tree,
                "manifest_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            },
            "automation": {
                "provider": "github-actions",
                "workflow": required_environment("GITHUB_WORKFLOW"),
                "run_id": required_environment("GITHUB_RUN_ID"),
                "run_attempt": required_environment("GITHUB_RUN_ATTEMPT"),
                "job": required_environment("GITHUB_JOB"),
                "status": "completed",
                "conclusion": "success",
            },
            "environment": {
                **configuration,
                "configuration_sha256": hashlib.sha256(configuration_bytes).hexdigest(),
            },
            "commands": arguments.command,
            "result": {
                "source_result": artifact(result_path),
                "assertions_total": len(assertions),
                "assertions_passed": sum(assertions),
                "divergences": source_result.get("divergences", []),
            },
            "artifacts": artifacts,
            "limitations": arguments.limitation,
            "review_requirement": {
                "independent": True,
                "roles": sorted(set(arguments.review_role)),
                "decision": "pending",
            },
            "claims": {
                "automation_passed": True,
                "accepted": False,
                "gap_closed": False,
                "compatibility_credit": False,
                "production_ready": False,
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "automation-candidate-built",
                    "output": str(output_path),
                    "evidence_id": arguments.evidence_id,
                    "assertions_total": len(assertions),
                    "artifact_count": len(artifacts),
                    "accepted": False,
                    "gap_closed": False,
                    "compatibility_credit": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, EvidenceError) as error:
        print(f"automation evidence candidate build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
