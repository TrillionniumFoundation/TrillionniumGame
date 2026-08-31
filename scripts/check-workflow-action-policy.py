#!/usr/bin/env python3
"""Enforce repository-native, immutable-head workflow execution."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
USES = re.compile(r"^\s*-?\s*uses\s*:\s*(?P<value>\S.*)$")
WRITE_PERMISSION = re.compile(r"^\s*(?P<scope>[a-zA-Z0-9_-]+)\s*:\s*write\s*(?:#.*)?$")
MOVABLE_CANDIDATE_FETCH = 'refs/heads/${CANDIDATE_REF}'
IMMUTABLE_CANDIDATE_FETCH = '"${CANDIDATE_SHA}"'
ALLOWED_USES_PREFIXES = ("./",)
ALLOWED_WRITE_WORKFLOWS: set[str] = set()


def allowed_use(value: str) -> bool:
    return value.startswith(ALLOWED_USES_PREFIXES)


def main() -> int:
    failures: list[str] = []
    files = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
    if not files:
        print("workflow action policy failed: no workflow files", file=sys.stderr)
        return 1

    immutable_fetch_workflows = 0
    local_use_count = 0
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if "\r" in text or not text.endswith("\n"):
            failures.append(f"{relative}: workflow must be LF with trailing newline")
        for number, line in enumerate(text.splitlines(), 1):
            match = USES.match(line)
            if match:
                value = match.group("value").strip("'\"")
                if allowed_use(value):
                    local_use_count += 1
                else:
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
        if MOVABLE_CANDIDATE_FETCH in text:
            failures.append(
                f"{relative}: movable branch fetch is forbidden; fetch CANDIDATE_SHA directly"
            )

        has_candidate_fetch = (
            "CANDIDATE_SHA:" in text
            and 'git -C "$GITHUB_WORKSPACE" fetch' in text
        )
        if has_candidate_fetch:
            immutable_fetch_workflows += 1
            if IMMUTABLE_CANDIDATE_FETCH not in text:
                failures.append(
                    f"{relative}: exact candidate fetch does not contain {IMMUTABLE_CANDIDATE_FETCH}"
                )
            if 'rev-parse HEAD)" = "$CANDIDATE_SHA"' not in text:
                failures.append(f"{relative}: checked-out SHA is not asserted")

    if failures:
        print("workflow action policy failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "workflow action policy: OK "
        f"({len(files)} workflows; {immutable_fetch_workflows} immutable candidate fetchers; "
        f"{local_use_count} local action/reusable-workflow use(s); no external actions, "
        "movable candidate fetches or write permissions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
