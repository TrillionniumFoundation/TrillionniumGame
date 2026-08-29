#!/usr/bin/env python3
"""Generate a fail-closed exact-candidate identity and control-plane manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPOSITORY = "TrillionniumFoundation/TrillionniumGame"


class ManifestError(RuntimeError):
    pass


def run_git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise ManifestError(
            f"git {' '.join(arguments)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def digest_file(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def ordered_files(root: Path, suffixes: Iterable[str]) -> list[Path]:
    accepted = set(suffixes)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in accepted
    )


def aggregate_digest(files: list[dict[str, object]]) -> str:
    state = hashlib.sha256()
    for item in files:
        state.update(str(item["path"]).encode("utf-8"))
        state.update(b"\0")
        state.update(str(item["sha256"]).encode("ascii"))
        state.update(b"\n")
    return state.hexdigest()


def build_manifest() -> dict[str, object]:
    commit = run_git("rev-parse", "HEAD")
    tree = run_git("rev-parse", "HEAD^{tree}")
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD")
    require_hex(commit, 40, "commit")
    require_hex(tree, 40, "tree")

    expected_head = os.environ.get("TRNM_EXPECTED_HEAD") or os.environ.get("GITHUB_SHA")
    if expected_head and expected_head != commit:
        raise ManifestError(
            f"checked-out commit {commit} does not match required head {expected_head}"
        )
    github_repository = os.environ.get("GITHUB_REPOSITORY")
    if github_repository and github_repository != CANONICAL_REPOSITORY:
        raise ManifestError(
            f"GITHUB_REPOSITORY={github_repository!r}; expected {CANONICAL_REPOSITORY!r}"
        )

    migration_profiles: dict[str, object] = {}
    for profile in ("postgresql", "cockroachdb"):
        migration_root = ROOT / "migrations" / profile
        if not migration_root.is_dir():
            raise ManifestError(f"missing migration profile: {profile}")
        files = [digest_file(path) for path in ordered_files(migration_root, {".sql"})]
        if not files:
            raise ManifestError(f"migration profile {profile} is empty")
        migration_profiles[profile] = {
            "files": files,
            "ordered_chain_sha256": aggregate_digest(files),
        }

    required_paths = [
        "CURRENT_PLAN.md",
        "PROJECT_BOUNDARY.json",
        "Cargo.toml",
        "Cargo.lock",
        "runtime/go.mod",
        "docs/status/CURRENT_STATE.json",
        "docs/status/EXECUTION_STATUS.json",
        "docs/status/GAP_REGISTER.json",
        "docs/status/IMPLEMENTATION_INVENTORY.json",
        "docs/status/PRODUCT_GATES.json",
        "docs/development/SCHEMA_AUTHORITY.json",
        "docs/development/COMPATIBILITY_DIVERGENCES.json",
        "docs/evidence/index.json",
        ".github/workflows/trillionnium-game-merge-gate.yml",
    ]
    control_files = []
    for relative in required_paths:
        path = ROOT / relative
        if not path.is_file():
            raise ManifestError(f"missing candidate control file: {relative}")
        control_files.append(digest_file(path))

    source_candidates = []
    for relative in (
        "crates/trnm-server",
        "crates/trnm-storage-nakama-version",
        "crates/trnm-token-jwt-adapter",
        "crates/trnm-persistence-core",
        "crates/trnm-persistence-pg",
    ):
        source_root = ROOT / relative
        if not source_root.is_dir():
            raise ManifestError(f"missing source candidate: {relative}")
        files = [
            digest_file(path)
            for path in ordered_files(
                source_root,
                {".rs", ".toml", ".lock"},
            )
            if "target" not in path.parts
        ]
        source_candidates.append(
            {
                "path": relative,
                "files": files,
                "source_sha256": aggregate_digest(files),
            }
        )

    return {
        "schema": "trillionnium.candidate-manifest.v1",
        "project_id": "trillionnium-game",
        "repository": CANONICAL_REPOSITORY,
        "commit": commit,
        "tree": tree,
        "branch": branch,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_identity": {
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "github_workflow": os.environ.get("GITHUB_WORKFLOW"),
            "github_job": os.environ.get("GITHUB_JOB"),
        },
        "environment": {
            "python": platform.python_version(),
            "os": platform.platform(),
            "architecture": platform.machine(),
        },
        "migration_profiles": migration_profiles,
        "control_files": control_files,
        "control_plane_sha256": aggregate_digest(control_files),
        "source_candidates": source_candidates,
        "claim_boundary": {
            "exact_source_identity_recorded": True,
            "source_validation_executed": False,
            "independently_reviewed": False,
            "compatibility_credit": False,
            "production_ready": False,
            "public_online": False,
        },
    }


def require_hex(value: str, length: int, label: str) -> None:
    if len(value) != length or any(character not in "0123456789abcdef" for character in value):
        raise ManifestError(f"invalid {label}: {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="run/candidate/candidate-manifest.json")
    arguments = parser.parse_args()
    try:
        manifest = build_manifest()
        output = ROOT / arguments.output
        output.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        output.write_text(serialized, encoding="utf-8")
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        print(
            json.dumps(
                {
                    "status": "generated",
                    "output": str(output.relative_to(ROOT)),
                    "sha256": digest,
                    "commit": manifest["commit"],
                    "tree": manifest["tree"],
                    "compatibility_credit": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, ManifestError) as error:
        print(f"candidate manifest generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
