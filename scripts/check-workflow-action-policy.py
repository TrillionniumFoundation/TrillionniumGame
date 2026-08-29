#!/usr/bin/env python3
"""Fail closed when repository workflows depend on blocked external actions."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
USES = re.compile(r"^\s*-?\s*uses\s*:\s*(?P<value>\S.*)$")
WRITE_PERMISSION = re.compile(r"^\s*(?P<scope>[a-zA-Z0-9_-]+)\s*:\s*write\s*(?:#.*)?$")

# This repository currently runs under an organization policy that has rejected
# otherwise immutable external actions before any job was created. Until that
# administrator policy is changed and independently evidenced, every required
# workflow must execute from repository source and runner-provided tools.
ALLOWED_USES_PREFIXES = ("./",)
ALLOWED_WRITE_WORKFLOWS: set[str] = set()


def main() -> int:
    failures: list[str] = []
    files = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
    if not files:
        print("workflow action policy failed: no workflow files", file=sys.stderr)
        return 1

    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if "\r" in text or not text.endswith("\n"):
            failures.append(f"{relative}: workflow must be LF with trailing newline")
        for number, line in enumerate(text.splitlines(), 1):
            match = USES.match(line)
            if match:
                value = match.group("value").strip("'\"")
                if not value.startswith(ALLOWED_USES_PREFIXES):
                    failures.append(
                        f"{relative}:{number}: external action/reusable workflow is forbidden: {value}"
                    )
            permission = WRITE_PERMISSION.match(line)
            if permission and relative not in ALLOWED_WRITE_WORKFLOWS:
                failures.append(
                    f"{relative}:{number}: write permission is forbidden: {permission.group('scope')}"
                )
        if "pull_request_target:" in text:
            failures.append(f"{relative}: pull_request_target is forbidden")
        if "persist-credentials: true" in text:
            failures.append(f"{relative}: persistent checkout credentials are forbidden")

    if failures:
        print("workflow action policy failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"workflow action policy: OK ({len(files)} workflows, no external actions or write permissions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
