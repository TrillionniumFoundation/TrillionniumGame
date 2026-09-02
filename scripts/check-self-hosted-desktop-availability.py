#!/usr/bin/env python3
"""Fail-closed contract for the protected-default-branch desktop probe."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

DEFAULT_PATH = Path(".github/workflows/self-hosted-desktop-availability.yml")

JOB_PREDICATE = """    if: >-
      github.event_name == 'issue_comment' &&
      github.repository == 'TrillionniumFoundation/TrillionniumGame' &&
      github.event.repository.default_branch == 'main' &&
      github.ref == 'refs/heads/main' &&
      github.ref_protected == true &&
      github.event.issue.number == 7 &&
      github.event.issue.pull_request == null &&
      github.event.comment.body == '/desktop-runner-probe' &&
      github.actor == 'Tomasrgbsf'
"""

REQUIRED_FRAGMENTS = (
    "on:\n  issue_comment:\n    types: [created]\n",
    "permissions: {}",
    "concurrency:\n  group: self-hosted-desktop-availability\n  cancel-in-progress: false",
    JOB_PREDICATE,
    "runs-on:\n      group: desktop\n      labels: desktop",
    "timeout-minutes: 5",
    'test "$GITHUB_EVENT_NAME" = issue_comment',
    'test "$GITHUB_REPOSITORY" = TrillionniumFoundation/TrillionniumGame',
    'test "$GITHUB_REF" = refs/heads/main',
    'test "$GITHUB_REF_PROTECTED" = true',
    'test "$GITHUB_ACTOR" = Tomasrgbsf',
    'test "$RUNNER_NAME" = desktop',
    'test "$RUNNER_OS" = Linux',
    'test "$RUNNER_ARCH" = X64',
    "status=desktop-runner-acquired",
    "trigger=issue_comment",
    "issue=7",
)

FORBIDDEN_FRAGMENTS = (
    "${{",
    "workflow_dispatch:",
    "repository_dispatch:",
    "pull_request:",
    "pull_request_target:",
    "push:",
    "schedule:",
    "workflow_call:",
    "inputs:",
    "env:",
    "uses:",
    "GITHUB_TOKEN",
    "GITHUB_EVENT_PATH",
    "secrets.",
    "curl ",
    "wget ",
    "git ",
    "ssh ",
    "scp ",
    "adb ",
    "nvidia-smi",
    "uname ",
    "printenv",
    "/proc/",
    "RUNNER_TEMP",
    "$HOME",
    "startsWith(",
    "contains(",
    "fromJSON(",
)


def validate(text: str) -> list[str]:
    """Return all contract violations without executing workflow content."""
    failures: list[str] = []
    if text.count("issue_comment:") != 1:
        failures.append("workflow must contain exactly one issue_comment trigger")
    if text.count("types: [created]") != 1:
        failures.append("only newly created comments may trigger the probe")
    if text.count("runs-on:") != 1:
        failures.append("workflow must contain exactly one runs-on boundary")
    if text.count("    if: >-") != 1:
        failures.append("workflow must contain exactly one job-level allocation predicate")
    if text.count("run: |") != 1:
        failures.append("workflow must contain exactly one bounded shell step")

    for fragment in REQUIRED_FRAGMENTS:
        if fragment not in text:
            failures.append(f"required fragment missing: {fragment!r}")
    for fragment in FORBIDDEN_FRAGMENTS:
        if fragment in text:
            failures.append(f"forbidden fragment present: {fragment!r}")

    if "permissions: {}" not in text or text.count("permissions:") != 1:
        failures.append("workflow token permissions must be exactly empty")
    if "group: desktop" not in text or "labels: desktop" not in text:
        failures.append("runner scheduling must require both desktop group and desktop label")

    predicate_position = text.find(JOB_PREDICATE)
    allocation_position = text.find("    runs-on:")
    shell_position = text.find("        run: |")
    if not (0 <= predicate_position < allocation_position < shell_position):
        failures.append("authorization predicate must precede runner allocation and shell execution")

    expected_receipt = (
        "status=desktop-runner-acquired\\n"
        "trigger=issue_comment\\n"
        "ref=refs/heads/main\\n"
        "issue=7\\n"
    )
    if expected_receipt not in text:
        failures.append("receipt must remain constant and contain no host or comment data")
    return failures


def assert_rejected(name: str, text: str) -> None:
    failures = validate(text)
    if not failures:
        raise AssertionError(f"hostile fixture unexpectedly accepted: {name}")


def self_test(baseline: str) -> None:
    baseline_failures = validate(baseline)
    if baseline_failures:
        raise AssertionError("baseline contract failed: " + "; ".join(baseline_failures))

    hostile: dict[str, str] = {
        "selected-ref-dispatch": baseline.replace(
            "  issue_comment:\n    types: [created]\n",
            "  workflow_dispatch:\n",
        ),
        "edited-comment-trigger": baseline.replace(
            "types: [created]", "types: [created, edited]", 1
        ),
        "missing-main-ref": baseline.replace(
            "github.ref == 'refs/heads/main'", "github.ref != 'refs/heads/main'", 1
        ),
        "unprotected-ref": baseline.replace(
            "github.ref_protected == true", "github.ref_protected == false", 1
        ),
        "wrong-default-branch": baseline.replace(
            "github.event.repository.default_branch == 'main'",
            "github.event.repository.default_branch != 'main'",
            1,
        ),
        "any-comment": baseline.replace(
            "github.event.comment.body == '/desktop-runner-probe'",
            "github.event.comment.body != ''",
            1,
        ),
        "prefix-command": baseline.replace(
            "github.event.comment.body == '/desktop-runner-probe'",
            "startsWith(github.event.comment.body, '/desktop-runner-probe')",
            1,
        ),
        "any-actor": baseline.replace(
            "github.actor == 'Tomasrgbsf'", "github.actor != ''", 1
        ),
        "wrong-issue": baseline.replace(
            "github.event.issue.number == 7", "github.event.issue.number > 0", 1
        ),
        "pull-request-comment": baseline.replace(
            "      github.event.issue.pull_request == null &&\n", "", 1
        ),
        "step-level-gate": baseline.replace("    if: >-\n", "      if: >-\n", 1),
        "comment-interpolation": baseline.replace(
            "printf 'status=desktop-runner-acquired\\ntrigger=issue_comment\\nref=refs/heads/main\\nissue=7\\n'",
            "printf '%s\\n' '${{ github.event.comment.body }}'",
            1,
        ),
        "missing-runner-group": baseline.replace("      group: desktop\n", "", 1),
        "broad-runner-label": baseline.replace(
            "      labels: desktop", "      labels: linux", 1
        ),
        "third-party-action": baseline.replace(
            "    steps:\n", "    steps:\n      - uses: actions/checkout@v6\n", 1
        ),
        "host-inventory": baseline.replace(
            "          printf 'status=desktop-runner-acquired\\ntrigger=issue_comment\\nref=refs/heads/main\\nissue=7\\n'",
            "          uname -a\n          adb devices -l\n",
            1,
        ),
        "network-egress": baseline.replace(
            "          printf 'status=desktop-runner-acquired\\ntrigger=issue_comment\\nref=refs/heads/main\\nissue=7\\n'",
            "          curl https://example.invalid\n",
            1,
        ),
        "token-permission": baseline.replace(
            "permissions: {}", "permissions:\n  contents: read", 1
        ),
    }
    for name, fixture in hostile.items():
        assert_rejected(name, fixture)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc


def print_failures(path: Path, failures: Iterable[str]) -> int:
    values = list(failures)
    if not values:
        print(f"self-hosted desktop availability contract: PASS ({path})")
        return 0
    for failure in values:
        print(f"ERROR: {failure}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    text = read_text(args.path)
    if args.self_test:
        self_test(text)
        print("self-hosted desktop availability hostile fixtures: PASS")
    return print_failures(args.path, validate(text))


if __name__ == "__main__":
    raise SystemExit(main())
