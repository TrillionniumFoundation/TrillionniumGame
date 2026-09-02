#!/usr/bin/env python3
"""Fail-closed contract for the manual self-hosted desktop runner probe."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

DEFAULT_PATH = Path(".github/workflows/self-hosted-desktop-availability.yml")

REQUIRED_FRAGMENTS = (
    "on:\n  workflow_dispatch:\n",
    "permissions: {}",
    "concurrency:\n  group: self-hosted-desktop-availability\n  cancel-in-progress: false",
    "runs-on:\n      group: desktop\n      labels: desktop",
    "timeout-minutes: 5",
    'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
    'test "$GITHUB_REF" = refs/heads/main',
    'test "$RUNNER_NAME" = desktop',
    'test "$RUNNER_OS" = Linux',
    'test "$RUNNER_ARCH" = X64',
    "status=desktop-runner-acquired",
)

FORBIDDEN_FRAGMENTS = (
    "${{",
    "inputs:",
    "uses:",
    "pull_request:",
    "push:",
    "repository_dispatch:",
    "workflow_call:",
    "schedule:",
    "GITHUB_TOKEN",
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
)


def validate(text: str) -> list[str]:
    """Return all contract violations without executing workflow content."""
    failures: list[str] = []
    if text.count("workflow_dispatch:") != 1:
        failures.append("workflow must contain exactly one workflow_dispatch trigger")
    if text.count("runs-on:") != 1:
        failures.append("workflow must contain exactly one runs-on boundary")
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
    if "status=desktop-runner-acquired\\nref=refs/heads/main\\n" not in text:
        failures.append("receipt must remain constant and contain no host inventory")
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
        "expression-input": baseline.replace(
            "printf 'status=desktop-runner-acquired\\nref=refs/heads/main\\n'",
            "printf '%s\\n' '${{ inputs.reason }}'",
        ),
        "dispatch-input-block": baseline.replace(
            "  workflow_dispatch:\n",
            "  workflow_dispatch:\n    inputs:\n      reason:\n        required: false\n",
        ),
        "missing-runner-group": baseline.replace("      group: desktop\n", ""),
        "broad-runner-label": baseline.replace("      labels: desktop", "      labels: linux"),
        "third-party-action": baseline.replace(
            "    steps:\n",
            "    steps:\n      - uses: actions/checkout@v6\n",
        ),
        "host-inventory": baseline.replace(
            "          printf 'status=desktop-runner-acquired\\nref=refs/heads/main\\n'",
            "          uname -a\n          adb devices -l\n",
        ),
        "network-egress": baseline.replace(
            "          printf 'status=desktop-runner-acquired\\nref=refs/heads/main\\n'",
            "          curl https://example.invalid\n",
        ),
        "push-trigger": baseline.replace(
            "  workflow_dispatch:\n",
            "  workflow_dispatch:\n  push:\n    branches: [main]\n",
        ),
        "token-permission": baseline.replace(
            "permissions: {}",
            "permissions:\n  contents: read",
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
