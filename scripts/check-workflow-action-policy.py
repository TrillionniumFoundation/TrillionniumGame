#!/usr/bin/env python3
"""Enforce repository-local or exact allowlisted immutable workflow actions."""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
USES = re.compile(r"^\s*-?\s*uses\s*:\s*(?P<value>\S.*)$")
WRITE_PERMISSION = re.compile(r"^\s*(?P<scope>[a-zA-Z0-9_-]+)\s*:\s*write\s*(?:#.*)?$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
MOVABLE_CANDIDATE_FETCH = 'refs/heads/${CANDIDATE_REF}'
IMMUTABLE_CANDIDATE_FETCH = '"${CANDIDATE_SHA}"'

# External actions remain denied by default. The only exception is one GitHub
# first-party action at one immutable commit, in one evidence workflow.
ALLOWED_LOCAL_USES_PREFIXES = ("./",)
ALLOWED_EXTERNAL_USES: dict[str, frozenset[str]] = {
    "actions/upload-artifact@043fb460e6257d1ca154e89a5e86196c74e480f8": frozenset(
        {".github/workflows/outbox-final-attempt-reaper.yml"}
    )
}
ALLOWED_WRITE_WORKFLOWS: set[str] = set()


def allowed_use(value: str, workflow: str | None = None) -> bool:
    if value.startswith(ALLOWED_LOCAL_USES_PREFIXES):
        return True
    allowed_workflows = ALLOWED_EXTERNAL_USES.get(value)
    if allowed_workflows is None:
        return False
    return workflow is None or workflow in allowed_workflows


def main() -> int:
    failures: list[str] = []
    files = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
    if not files:
        print("workflow action policy failed: no workflow files", file=sys.stderr)
        return 1

    for value, workflows in sorted(ALLOWED_EXTERNAL_USES.items()):
        if "@" not in value:
            failures.append(f"allowlisted action is missing immutable ref: {value}")
            continue
        owner_repo, reference = value.rsplit("@", 1)
        if not owner_repo.startswith("actions/") or not SHA40.fullmatch(reference):
            failures.append(
                f"allowlisted action is not a GitHub first-party 40-hex pin: {value}"
            )
        if not workflows:
            failures.append(f"allowlisted action has no bounded workflow: {value}")

    immutable_fetch_workflows = 0
    observed_external: Counter[tuple[str, str]] = Counter()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if "\r" in text or not text.endswith("\n"):
            failures.append(f"{relative}: workflow must be LF with trailing newline")
        for number, line in enumerate(text.splitlines(), 1):
            match = USES.match(line)
            if match:
                value = match.group("value").strip("'\"")
                if value.startswith(ALLOWED_LOCAL_USES_PREFIXES):
                    pass
                elif allowed_use(value, relative):
                    observed_external[(value, relative)] += 1
                else:
                    failures.append(
                        f"{relative}:{number}: unapproved external action/reusable "
                        f"workflow is forbidden: {value}"
                    )
            permission = WRITE_PERMISSION.match(line)
            if permission and relative not in ALLOWED_WRITE_WORKFLOWS:
                failures.append(
                    f"{relative}:{number}: write permission is forbidden: "
                    f"{permission.group('scope')}"
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
                    f"{relative}: exact candidate fetch does not contain "
                    f"{IMMUTABLE_CANDIDATE_FETCH}"
                )
            if 'rev-parse HEAD)" = "$CANDIDATE_SHA"' not in text:
                failures.append(f"{relative}: checked-out SHA is not asserted")

    expected_external = {
        (value, workflow)
        for value, workflows in ALLOWED_EXTERNAL_USES.items()
        for workflow in workflows
    }
    for key in sorted(expected_external):
        count = observed_external[key]
        if count != 1:
            failures.append(
                f"{key[1]}: expected exactly one use of {key[0]}, observed {count}"
            )

    if failures:
        print("workflow action policy failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "workflow action policy: OK "
        f"({len(files)} workflows; {immutable_fetch_workflows} immutable candidate "
        f"fetchers; {sum(observed_external.values())} exact first-party action use; "
        "no unapproved external actions, movable candidate fetches or write permissions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
