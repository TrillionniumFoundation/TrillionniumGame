#!/usr/bin/env python3
"""Reject generated, secret-like and unreviewed oversized files from the Git tree."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LARGE_FILE_EXCEPTIONS_PATH = ROOT / "config/repository-large-file-exceptions.json"
FORBIDDEN_DIRECTORY_NAMES = {
    "target",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "coverage",
    "artifacts",
    "run",
    "consolidation-evidence",
}
FORBIDDEN_SUFFIXES = {
    ".o",
    ".obj",
    ".a",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".rlib",
    ".rmeta",
    ".profraw",
    ".profdata",
    ".pyc",
    ".pyo",
    ".class",
    ".wasm",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}
FORBIDDEN_EXACT_NAMES = {
    ".env",
    ".DS_Store",
}
# Historical compressed execution backlog predates the structured exception
# registry. It remains an exact path rather than a prefix or wildcard.
LEGACY_ALLOWED_LARGE_PATHS = {
    "docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz",
}
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024
MAX_POLICY_EXCEPTION_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
WILDCARD_CHARACTERS = set("*?[]{}")


class HygieneError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HygieneError(message)


def canonical_relative_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and value, f"{label} must be a non-empty string")
    require(not (set(value) & WILDCARD_CHARACTERS), f"{label} may not contain wildcards")
    path = PurePosixPath(value)
    require(not path.is_absolute(), f"{label} must be relative")
    require(
        bool(path.parts) and all(part not in {"", ".", ".."} for part in path.parts),
        f"{label} is not canonical",
    )
    require(path.as_posix() == value, f"{label} must use canonical POSIX separators")
    return value


def load_large_file_exceptions(
    path: Path = LARGE_FILE_EXCEPTIONS_PATH,
) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HygieneError(f"cannot load large-file exception registry: {error}") from error
    require(isinstance(value, dict), "large-file exception registry root must be an object")
    require(
        value.get("schema") == "trillionnium.repository-large-file-exceptions.v1",
        "large-file exception registry schema mismatch",
    )
    require(value.get("project_id") == "trillionnium-game", "large-file project mismatch")

    policy = value.get("policy")
    require(isinstance(policy, dict), "large-file exception policy must be an object")
    require(policy.get("wildcards_allowed") is False, "large-file wildcards must be forbidden")
    require(policy.get("exact_path_required") is True, "exact large-file paths are required")
    require(
        policy.get("sha256_binding_required") is True,
        "large-file SHA-256 binding is required",
    )
    require(
        policy.get("secret_or_binary_suffix_exceptions_allowed") is False,
        "secret or binary suffix exceptions must be forbidden",
    )
    maximum = policy.get("maximum_exception_bytes")
    require(
        isinstance(maximum, int)
        and MAX_TRACKED_FILE_BYTES < maximum <= MAX_POLICY_EXCEPTION_BYTES,
        "large-file policy maximum is invalid",
    )

    claims = value.get("claims")
    require(isinstance(claims, dict), "large-file exception claims must be an object")
    required_false_claims = {
        "general_large_files_allowed",
        "digest_change_allowed_without_review",
        "runtime_artifacts_allowed",
        "secret_material_allowed",
        "compatibility_credit",
    }
    require(
        all(claims.get(key) is False for key in required_false_claims),
        "large-file exception claims must remain fail-closed",
    )

    rows = value.get("exceptions")
    require(isinstance(rows, list), "large-file exceptions must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"large-file exception {index} must be an object")
        relative = canonical_relative_path(row.get("path"), f"exception {index} path")
        require(relative not in result, f"duplicate large-file exception: {relative}")
        parsed = PurePosixPath(relative)
        require(
            not (set(parsed.parts) & FORBIDDEN_DIRECTORY_NAMES),
            f"large-file exception enters a generated directory: {relative}",
        )
        require(
            parsed.name not in FORBIDDEN_EXACT_NAMES
            and not parsed.name.startswith(".env.")
            and parsed.suffix.lower() not in FORBIDDEN_SUFFIXES,
            f"large-file exception cannot permit secret/binary material: {relative}",
        )
        digest = row.get("sha256")
        require(
            isinstance(digest, str) and SHA256.fullmatch(digest) is not None,
            f"large-file exception has invalid SHA-256: {relative}",
        )
        max_bytes = row.get("max_bytes")
        require(
            isinstance(max_bytes, int)
            and MAX_TRACKED_FILE_BYTES < max_bytes <= maximum,
            f"large-file exception has invalid max_bytes: {relative}",
        )
        require(
            row.get("content_type") == "application/json",
            f"only reviewable JSON may use the structured large-file exception: {relative}",
        )
        generator = canonical_relative_path(
            row.get("generator"),
            f"large-file exception generator for {relative}",
        )
        require(
            generator.startswith("scripts/") and generator.endswith(".py"),
            f"large-file exception generator must be a repository Python script: {relative}",
        )
        reason = row.get("reason")
        require(
            isinstance(reason, str) and len(reason.strip()) >= 40,
            f"large-file exception requires a substantive reason: {relative}",
        )
        result[relative] = dict(row)
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HygieneError(f"cannot enumerate tracked files: {exc}") from exc
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    try:
        files = tracked_files()
        exceptions = load_large_file_exceptions()
    except HygieneError as exc:
        print(f"repository hygiene failed: {exc}", file=sys.stderr)
        return 1

    violations: list[str] = []
    tracked = set(files)
    used_exceptions: set[str] = set()
    for relative in files:
        path = PurePosixPath(relative)
        names = set(path.parts)
        if names & FORBIDDEN_DIRECTORY_NAMES:
            violations.append(f"generated directory is tracked: {relative}")
        if path.name in FORBIDDEN_EXACT_NAMES or path.name.startswith(".env."):
            violations.append(f"secret/local-environment file is tracked: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"generated or secret-like suffix is tracked: {relative}")

        absolute = ROOT / relative
        if not absolute.is_file():
            continue
        size = absolute.stat().st_size
        if size <= MAX_TRACKED_FILE_BYTES or relative in LEGACY_ALLOWED_LARGE_PATHS:
            continue
        exception = exceptions.get(relative)
        if exception is None:
            violations.append(
                f"tracked file exceeds {MAX_TRACKED_FILE_BYTES} bytes without an explicit exception: "
                f"{relative} ({size} bytes)"
            )
            continue
        used_exceptions.add(relative)
        if size > exception["max_bytes"]:
            violations.append(
                f"large-file exception size exceeded: {relative} "
                f"({size} > {exception['max_bytes']} bytes)"
            )
        observed_digest = file_sha256(absolute)
        if observed_digest != exception["sha256"]:
            violations.append(
                f"large-file exception digest mismatch: {relative} "
                f"(expected {exception['sha256']}, observed {observed_digest})"
            )
        try:
            json.loads(absolute.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            violations.append(f"large-file exception is not valid UTF-8 JSON: {relative}: {error}")
        generator = ROOT / exception["generator"]
        if not generator.is_file() or exception["generator"] not in tracked:
            violations.append(
                f"large-file exception generator is missing or untracked: "
                f"{relative} -> {exception['generator']}"
            )

    for relative in sorted(set(exceptions) - used_exceptions):
        if relative not in tracked:
            violations.append(f"large-file exception points to an untracked path: {relative}")
            continue
        absolute = ROOT / relative
        if not absolute.is_file():
            violations.append(f"large-file exception path is not a regular file: {relative}")
        elif absolute.stat().st_size <= MAX_TRACKED_FILE_BYTES:
            violations.append(f"stale large-file exception is no longer necessary: {relative}")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    required_ignore_entries = {
        "/target/",
        "**/target/",
        "artifacts/",
        "run/",
        "__pycache__/",
        "*.py[cod]",
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
    }
    missing_ignore = sorted(required_ignore_entries - set(gitignore))
    if missing_ignore:
        violations.append(".gitignore missing required entries: " + ", ".join(missing_ignore))

    if violations:
        print("repository hygiene failed:", file=sys.stderr)
        for violation in sorted(set(violations)):
            print(f"- {violation}", file=sys.stderr)
        return 1

    print(
        "repository hygiene: OK "
        f"({len(files)} tracked files; {len(used_exceptions)} exact digest-bound large-file exception(s); "
        "no build outputs, runtime artifacts, secret-like files or unapproved oversized blobs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
