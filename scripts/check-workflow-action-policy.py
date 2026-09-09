#!/usr/bin/env python3
"""Enforce repository-native, immutable-head GitHub Actions workflows."""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
USES = re.compile(r"^\s*-?\s*uses\s*:\s*(?P<value>\S.*)$")
WRITE_PERMISSION = re.compile(
    r"^\s*(?P<scope>[a-zA-Z0-9_-]+)\s*:\s*write\s*(?:#.*)?$"
)
MAPPING_KEY = re.compile(
    r"^(?P<indent> *)(?P<sequence>-\s+)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*|'[^']+'|\"[^\"]+\")"
    r"\s*:(?P<value>.*)$"
)
BLOCK_SCALAR = re.compile(r"^[>|][1-9]?[+-]?(?:\s+#.*)?$")
MOVABLE_CANDIDATE_FETCH = "refs/heads/${CANDIDATE_REF}"
IMMUTABLE_CANDIDATE_FETCH = '"${CANDIDATE_SHA}"'
PROSPECTIVE_WORKFLOW = ".github/workflows/prospective-merge-gate.yml"
PROSPECTIVE_MERGE_REF = (
    "+refs/pull/${PR_NUMBER}/merge:refs/remotes/origin/prospective-merge"
)
ALLOWED_USES_PREFIXES = ("./",)
ALLOWED_WRITE_WORKFLOWS: set[str] = set()
REQUIRED_ROOT_KEYS = ("name", "on", "jobs")


def allowed_use(value: str) -> bool:
    return value.startswith(ALLOWED_USES_PREFIXES)


def _unquote_key(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _join_shell_continuations(text: str) -> str:
    """Normalize reviewed backslash-newline shell continuations for matching."""

    return re.sub(r"\\\n[ \t]*", "", text)


def prospective_merge_fetch_failures(relative: str, text: str) -> list[str]:
    """Validate the one approved exact prospective-merge fetch profile.

    The pull-request merge ref is movable, so the workflow must bind the
    checked-out object to the immutable event ``github.sha`` and independently
    validate ordered base/head parents. Merely mentioning the merge ref is not
    sufficient.
    """

    if relative != PROSPECTIVE_WORKFLOW:
        return [
            f"{relative}: prospective merge fetch is allowed only in "
            f"{PROSPECTIVE_WORKFLOW}"
        ]

    failures: list[str] = []
    normalized = _join_shell_continuations(text)
    required = {
        "event merge SHA": "PROSPECTIVE_MERGE_SHA: ${{ github.sha }}",
        "base SHA": "SOURCE_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
        "head SHA": "SOURCE_HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
        "pull request number": "PR_NUMBER: ${{ github.event.pull_request.number }}",
        "exact merge ref": PROSPECTIVE_MERGE_REF,
        "checked-out merge assertion": (
            'rev-parse HEAD)" = "$PROSPECTIVE_MERGE_SHA"'
        ),
        "ordered-parent verifier": "scripts/check-prospective-merge-identity.py",
    }
    for label, marker in required.items():
        if marker not in normalized:
            failures.append(f"{relative}: prospective merge fetch lacks {label}")

    checkout_count = text.count(
        'git -C "$GITHUB_WORKSPACE" checkout --detach'
    )
    workspace_enter_count = text.count('cd "$GITHUB_WORKSPACE"')
    if checkout_count < 1:
        failures.append(f"{relative}: prospective merge workflow has no checkout")
    if workspace_enter_count < checkout_count:
        failures.append(
            f"{relative}: every recreated prospective checkout must enter "
            f"GITHUB_WORKSPACE (checkouts={checkout_count}, "
            f"enters={workspace_enter_count})"
        )
    return failures


def workflow_structure_failures(text: str) -> list[str]:
    """Return duplicate-key and minimal root-shape failures.

    This is intentionally a strict lexical validator for the repository's
    workflow subset. It does not attempt to implement YAML; it catches the
    class of silent duplicate mapping keys that previously produced a
    zero-job startup failure, while treating every sequence item as its own
    mapping scope and ignoring block-scalar bodies.
    """

    failures: list[str] = []
    stack: list[tuple[int, str]] = []
    sequence_indexes: defaultdict[int, int] = defaultdict(int)
    first_seen: dict[tuple[tuple[str, ...], int, str], int] = {}
    root_counts: defaultdict[str, int] = defaultdict(int)
    block_parent_indent: int | None = None

    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            failures.append(f"line {number}: tabs are forbidden in indentation")
            continue
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        if block_parent_indent is not None:
            if indent > block_parent_indent:
                continue
            block_parent_indent = None

        while stack and stack[-1][0] >= indent:
            stack.pop()

        match = MAPPING_KEY.match(raw)
        if match is None:
            if raw.lstrip().startswith("- "):
                sequence_indexes[indent] += 1
                stack.append((indent, f"@{indent}:{sequence_indexes[indent]}"))
            continue

        key = _unquote_key(match.group("key"))
        value = match.group("value").strip()
        is_sequence_item = match.group("sequence") is not None

        if is_sequence_item:
            sequence_indexes[indent] += 1
            sequence_token = f"@{indent}:{sequence_indexes[indent]}"
            path = tuple(token for _, token in stack) + (sequence_token,)
            key_indent = indent + 2
        else:
            path = tuple(token for _, token in stack)
            key_indent = indent
            if indent == 0:
                root_counts[key] += 1

        identity = (path, key_indent, key)
        previous = first_seen.get(identity)
        if previous is not None:
            failures.append(
                f"line {number}: duplicate mapping key {key!r} "
                f"(first declared on line {previous})"
            )
        else:
            first_seen[identity] = number

        if is_sequence_item:
            stack.append((indent, f"@{indent}:{sequence_indexes[indent]}"))

        if not value:
            stack.append((key_indent, key))
        elif BLOCK_SCALAR.match(value):
            block_parent_indent = indent

    for key in REQUIRED_ROOT_KEYS:
        count = root_counts[key]
        if count != 1:
            failures.append(
                f"root key {key!r} must appear exactly once (found {count})"
            )
    return failures


def main() -> int:
    failures: list[str] = []
    files = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(
        WORKFLOW_ROOT.glob("*.yaml")
    )
    if not files:
        print("workflow action policy failed: no workflow files", file=sys.stderr)
        return 1

    immutable_fetch_workflows = 0
    prospective_merge_workflows = 0
    local_use_count = 0
    workflow_names: dict[str, str] = {}
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if "\r" in text or not text.endswith("\n"):
            failures.append(f"{relative}: workflow must be LF with trailing newline")

        for structural_failure in workflow_structure_failures(text):
            failures.append(f"{relative}: {structural_failure}")

        name_match = re.search(r"^name:\s*(?P<name>.+?)\s*$", text, re.MULTILINE)
        if name_match:
            workflow_name = name_match.group("name").strip("'\"")
            previous = workflow_names.get(workflow_name)
            if previous is not None:
                failures.append(
                    f"{relative}: duplicate workflow name {workflow_name!r}; "
                    f"already used by {previous}"
                )
            else:
                workflow_names[workflow_name] = relative

        for number, line in enumerate(text.splitlines(), 1):
            match = USES.match(line)
            if match:
                value = match.group("value").strip("'\"")
                if allowed_use(value):
                    local_use_count += 1
                else:
                    failures.append(
                        f"{relative}:{number}: external action/reusable workflow "
                        f"is forbidden: {value}"
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
            failures.append(
                f"{relative}: persistent checkout credentials are forbidden"
            )
        if MOVABLE_CANDIDATE_FETCH in text:
            failures.append(
                f"{relative}: movable branch fetch is forbidden; "
                "fetch CANDIDATE_SHA directly"
            )

        is_prospective = (
            relative == PROSPECTIVE_WORKFLOW
            or "PROSPECTIVE_MERGE_SHA:" in text
            or PROSPECTIVE_MERGE_REF in text
        )
        if is_prospective:
            prospective_merge_workflows += 1
            failures.extend(prospective_merge_fetch_failures(relative, text))
            continue

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

    if failures:
        print("workflow action policy failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "workflow action policy: OK "
        f"({len(files)} workflows; {immutable_fetch_workflows} immutable "
        f"candidate fetchers; {prospective_merge_workflows} exact prospective "
        f"merge fetcher(s); {local_use_count} local action/reusable-workflow "
        "use(s); unique workflow names and mapping keys; no external actions, "
        "movable candidate fetches or write permissions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
