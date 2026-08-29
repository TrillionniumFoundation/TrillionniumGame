#!/usr/bin/env python3
"""Reject generated, secret-like and oversized files from the reviewed Git tree."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
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
ALLOWED_LARGE_PREFIXES = {
    "docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz",
}
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024


class HygieneError(RuntimeError):
    pass


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
    except HygieneError as exc:
        print(f"repository hygiene failed: {exc}", file=sys.stderr)
        return 1

    violations: list[str] = []
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
        if absolute.is_file():
            size = absolute.stat().st_size
            if size > MAX_TRACKED_FILE_BYTES and relative not in ALLOWED_LARGE_PREFIXES:
                violations.append(
                    f"tracked file exceeds {MAX_TRACKED_FILE_BYTES} bytes without an explicit exception: "
                    f"{relative} ({size} bytes)"
                )

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
        f"({len(files)} tracked files; no build outputs, runtime artifacts, secret-like files or unapproved oversized blobs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
