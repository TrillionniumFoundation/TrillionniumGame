#!/usr/bin/env python3
"""Seal bounded outbox evidence against the complete candidate schema chain.

This checks source/runtime identity and retained bytes; it grants no independent
review, compatibility or production acceptance. --archive verifies the generated
archive without resealing the already promoted result.env.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[1]


class SealingError(RuntimeError):
    """Missing, stale or ambiguous retained evidence."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise SealingError(reason)


def load_checker(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SOURCE_ROOT / relative)
    require(spec is not None and spec.loader is not None, "shared evidence checker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = load_checker("outbox_seal_archive", "scripts/verify-actions-log-artifact.py")
SCHEMAS = load_checker("outbox_seal_identity", "scripts/check-authoritative-schema-identity.py")
MIGRATIONS = VERIFIER.MIGRATIONS
BINDING = VERIFIER.BINDING


def git_value(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=SOURCE_ROOT, text=True).strip()


def candidate_identity(commit: str, tree: str, repository: str, run_id: str, run_attempt: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "invalid candidate commit")
    require(re.fullmatch(r"[0-9a-f]{40}", tree) is not None, "invalid candidate tree")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None
            and len(repository) <= 200, "invalid candidate repository")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id) is not None
            and re.fullmatch(r"[1-9][0-9]{0,9}", run_attempt) is not None, "invalid execution identity")
    require(git_value("rev-parse", "HEAD") == commit, "candidate commit mismatch while sealing evidence")
    require(git_value("rev-parse", "HEAD^{tree}") == tree, "candidate tree mismatch while sealing evidence")


def retained_files(root: Path) -> list[Path]:
    require(root.is_dir() and not root.is_symlink(), "profile evidence root must be a regular directory")
    for ancestor in root.parents:
        require(not ancestor.is_symlink(), "profile evidence ancestor symlink is forbidden")
    files: list[Path] = []
    size = 0
    for index, path in enumerate(root.rglob("*"), 1):
        require(index <= VERIFIER.MAX_ARCHIVE_ENTRIES, "profile evidence exceeds entry budget")
        mode = path.lstat().st_mode
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode), "nonregular profile evidence is forbidden")
        if stat.S_ISREG(mode):
            relative = path.relative_to(root).as_posix()
            require("\n" not in relative and "\r" not in relative, "newline is forbidden in evidence path")
            length = path.stat().st_size
            require(length <= VERIFIER.MAX_ARCHIVE_BYTES, "profile evidence file exceeds byte budget")
            size += length
            require(size <= VERIFIER.MAX_RETAINED_BYTES, "profile evidence exceeds expanded byte budget")
            files.append(path)
    require(bool(files), "profile evidence directory has no files")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def source_binding(profile: str):
    token = BINDING.verify_binding(SOURCE_ROOT, profile=profile)
    for row in BINDING.binding_document(token)["full_source_inventory"]:
        require(row["git_blob_sha1"] == git_value("rev-parse", "HEAD:" + row["path"]),
                "complete authoritative source differs from committed candidate")
    return token


def seal_profile(root: Path, *, profile: str, commit: str, tree: str,
                 repository: str, run_id: str, run_attempt: str,
                 workflow_context: str = "outbox") -> dict[str, Any]:
    candidate_identity(commit, tree, repository, run_id, run_attempt)
    producer = VERIFIER.producer_identity(profile, workflow_context)
    retained_files(root)  # Reject symlinks/devices before reading or replacing files.
    verified_binding = source_binding(profile)
    proof = BINDING.binding_document(verified_binding)
    binding = BINDING.operational_binding(verified_binding)
    validation = proof["source_selection"]["source"]["complete_validation"]
    lock_bytes = (SOURCE_ROOT / BINDING.SOURCE.LOCK_PATH).read_bytes()
    require((root / "migration-chain.lock.json").read_bytes() == lock_bytes,
            "retained migration lock differs from candidate source")
    report = VERIFIER.strict_object((root / "migration-chain-validation.json").read_bytes(), "retained chain validation")
    require(BINDING.same(report, validation), "retained complete migration validation differs from candidate source")
    schema = SCHEMAS.decode_identity_document((root / "schema-identity.json").read_bytes())
    SCHEMAS.validate_identity(schema, profile=profile, selection=BINDING.selection_token(verified_binding),
                              mode="fresh", source_commit=commit)
    result = VERIFIER.parse_env((root / "result.env").read_bytes(), "profile result")
    require(result == {"status": "passed", "profile": profile, "commit": commit},
            "profile result is not exact passed evidence")
    if not (root / BINDING.SIDECAR).exists():
        BINDING.write_annex(verified_binding, SOURCE_ROOT, root, commit=commit, tree=tree)
    BINDING.validate_annex_directory(verified_binding, root, commit=commit, tree=tree)
    for entry in binding["ordered_files"]:
        payload = (SOURCE_ROOT / entry["path"]).read_bytes()
        require(MIGRATIONS.git_blob_sha1(payload) == entry["git_blob_sha1"], "locked migration blob changed while sealing")
        retained = root / entry["path"]
        retained.parent.mkdir(parents=True, exist_ok=True)
        retained.write_bytes(payload)
        require(MIGRATIONS.git_blob_sha1(retained.read_bytes()) == entry["git_blob_sha1"], "retained migration blob differs")
    actual = sorted(path.relative_to(root).as_posix() for path in (root / "migrations").rglob("*") if path.is_file())
    require(actual == sorted(entry["path"] for entry in binding["ordered_files"]),
            "retained profile migration inventory differs from complete lock")
    (root / "result.env").write_text(f"status=passed\nprofile={profile}\ncommit={commit}\ntree={tree}\n", encoding="utf-8")
    identity = {"repository": repository, "commit": commit, "tree": tree, "profile": profile,
                "image": binding["image"], "run_id": run_id, "run_attempt": run_attempt,
                "evidence_run_id": f"{run_id}-{run_attempt}-{profile}",
                **producer,
                **BINDING.identity_fields(verified_binding),
                **{key: binding[key] for key in ("migration_lock", "schema_version", "storage_writer_epoch",
                                                "chain_digest", "digest_algorithm")}}
    (root / "identity.env").write_text("".join(f"{key}={value}\n" for key, value in identity.items()), encoding="utf-8")
    manifest = root / "files.sha256"
    manifest.unlink(missing_ok=True)
    files = retained_files(root)
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(root).as_posix()}" for path in files]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # The manifest itself consumes one entry and retained payload bytes. Use
    # the same budgets as the shared archive verifier on the finished root.
    retained_files(root)
    return {"profile": profile, "commit": commit, "tree": tree, "file_count": len(files),
            "schema_version": schema["schema_version"], "chain_digest": schema["chain_digest"],
            "compatibility_credit": False, "production_ready": False}


def verify_archive(archive: Path, *, profile: str, commit: str, tree: str,
                   repository: str, run_id: str, run_attempt: str,
                   workflow_context: str = "outbox") -> dict[str, Any]:
    candidate_identity(commit, tree, repository, run_id, run_attempt)
    VERIFIER.producer_identity(profile, workflow_context)
    require(stat.S_ISREG(archive.lstat().st_mode), "archive must be a regular file")
    require(0 < archive.stat().st_size <= VERIFIER.MAX_ARCHIVE_BYTES, "archive exceeds byte budget")
    binding = source_binding(profile)
    payload = archive.read_bytes()
    # The shared validator owns decompression/member budgets, the exact file
    # manifest and all profile migration inventory/blob checks for both entrypoints.
    VERIFIER.validate_archive(payload, repository=repository, head_sha=commit, head_tree=tree,
                              run_id=run_id, run_attempt=run_attempt, profile=profile, binding=binding,
                              workflow_context=workflow_context)
    return {"profile": profile, "complete_chain_archive_verified": True,
            "compatibility_credit": False, "production_ready": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=VERIFIER.PROFILES)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--repository", default=os.environ.get("CANDIDATE_REPOSITORY", os.environ.get("GITHUB_REPOSITORY")))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    parser.add_argument("--workflow-context", choices=["outbox", "prospective"], default="outbox")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        require(all(isinstance(value, str) for value in (args.repository, args.run_id, args.run_attempt)),
                "execution environment missing")
        identity = dict(profile=args.profile, commit=args.commit, tree=args.tree, repository=args.repository,
                        run_id=args.run_id, run_attempt=args.run_attempt, workflow_context=args.workflow_context)
        result = verify_archive(args.archive, **identity) if args.archive else seal_profile(args.root, **identity)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"outbox evidence rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
