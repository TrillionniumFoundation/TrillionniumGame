#!/usr/bin/env python3
"""One-shot deterministic migration for new workflows rejected at startup.

The repository's required workflow policy currently permits repository source
and runner-provided tools only. This script replaces immutable but externally
hosted checkout/upload actions in the newly introduced workflow set with:

* an exact PR-head/commit fetch performed by git; and
* a log-retained SHA-256 diagnostic manifest with no compatibility credit.

It is intentionally bounded to a reviewed file list and exact replacement
counts so it cannot silently rewrite unrelated workflows.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
TARGETS = {
    "branch-inventory.yml",
    "candidate-identity-manifest.yml",
    "database-runtime-policy-source.yml",
    "denominator-review-bundle.yml",
    "jwt-crypto-provider-source.yml",
    "jwt-provider-adapter-source.yml",
    "pg-server-response-loss.yml",
    "pg-server-vertical-slice.yml",
    "pull-request-contract.yml",
    "repository-governance-contract.yml",
    "rust-server-process-smoke.yml",
    "source-candidate-boundaries.yml",
    "storage-version-source-candidate.yml",
    "v3-source-and-scope-gate.yml",
    "websocket-wire-source.yml",
}
CHECKOUT = re.compile(r"^actions/checkout@[0-9a-f]{40}$")
UPLOAD = re.compile(r"^actions/upload-artifact@[0-9a-f]{40}$")
EXPECTED_CHECKOUT_STEPS = 18
EXPECTED_UPLOAD_STEPS = 5


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def action_in(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("uses:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
        if stripped.startswith("- uses:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
    return None


def step_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip()
        if not (stripped.startswith("- name:") or stripped.startswith("- uses:")):
            index += 1
            continue
        step_indent = indentation(lines[index])
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            candidate_stripped = candidate.lstrip()
            candidate_indent = indentation(candidate)
            if candidate.strip() and candidate_indent < step_indent:
                break
            if (
                candidate_indent == step_indent
                and (candidate_stripped.startswith("- name:") or candidate_stripped.startswith("- uses:"))
            ):
                break
            end += 1
        blocks.append((index, end))
        index = end
    return blocks


def checkout_block(indent: int) -> list[str]:
    lead = " " * indent
    prop = " " * (indent + 2)
    body = " " * (indent + 4)
    return [
        f"{lead}- name: Fetch exact candidate without external actions",
        f"{prop}shell: bash",
        f"{prop}run: |",
        f"{body}set -euo pipefail",
        f"{body}test -n \"$CANDIDATE_REPOSITORY\"",
        f"{body}test -n \"$CANDIDATE_SHA\"",
        f"{body}rm -rf \"$GITHUB_WORKSPACE\"",
        f"{body}git init \"$GITHUB_WORKSPACE\"",
        f"{body}git -C \"$GITHUB_WORKSPACE\" remote add origin \\",
        f"{body}  \"https://github.com/${{CANDIDATE_REPOSITORY}}.git\"",
        f"{body}git -C \"$GITHUB_WORKSPACE\" fetch --no-tags --depth=1 origin \\",
        f"{body}  \"${{CANDIDATE_SHA}}\"",
        f"{body}git -C \"$GITHUB_WORKSPACE\" checkout --detach FETCH_HEAD",
        f"{body}test \"$(git -C \"$GITHUB_WORKSPACE\" rev-parse HEAD)\" = \"$CANDIDATE_SHA\"",
    ]


def upload_path(block: list[str]) -> str:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("path:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if not value or value == "|":
                raise RuntimeError("multiline or empty upload path is not supported")
            return value
    raise RuntimeError("upload-artifact step has no path")


def diagnostic_block(indent: int, target: str) -> list[str]:
    lead = " " * indent
    prop = " " * (indent + 2)
    body = " " * (indent + 4)
    return [
        f"{lead}- name: Record exact-head diagnostic digest (no artifact credit)",
        f"{prop}if: always()",
        f"{prop}shell: bash",
        f"{prop}run: |",
        f"{body}set -euo pipefail",
        f"{body}target='{target}'",
        f"{body}test -e \"$target\"",
        f"{body}manifest=\"$RUNNER_TEMP/diagnostic-SHA256SUMS\"",
        f"{body}if [[ -d \"$target\" ]]; then",
        f"{body}  mapfile -d '' files < <(find \"$target\" -type f -print0 | sort -z)",
        f"{body}  test \"${{#files[@]}}\" -gt 0",
        f"{body}  sha256sum \"${{files[@]}}\" | tee \"$manifest\"",
        f"{body}else",
        f"{body}  sha256sum \"$target\" | tee \"$manifest\"",
        f"{body}fi",
        f"{body}printf '### Diagnostic evidence only\\n\\n' >> \"$GITHUB_STEP_SUMMARY\"",
        f"{body}printf 'This job did not upload a persistent artifact and grants no compatibility or production credit.\\n\\n```text\\n' >> \"$GITHUB_STEP_SUMMARY\"",
        f"{body}cat \"$manifest\" >> \"$GITHUB_STEP_SUMMARY\"",
        f"{body}printf '```\\n' >> \"$GITHUB_STEP_SUMMARY\"",
    ]


def ensure_candidate_env(lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    if "CANDIDATE_REPOSITORY:" in text and "CANDIDATE_SHA:" in text:
        return lines
    insertion = next((index for index, line in enumerate(lines) if line == "jobs:"), None)
    if insertion is None:
        raise RuntimeError("workflow has no top-level jobs key")
    env = [
        "env:",
        "  CANDIDATE_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name || github.repository }}",
        "  CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
        "",
    ]
    return lines[:insertion] + env + lines[insertion:]


def migrate(path: Path) -> tuple[int, int]:
    original = path.read_text(encoding="utf-8")
    if "\r" in original or not original.endswith("\n"):
        raise RuntimeError(f"{path}: expected LF text with trailing newline")
    lines = original.splitlines()
    checkout_count = 0
    upload_count = 0
    for start, end in reversed(step_blocks(lines)):
        block = lines[start:end]
        action = action_in(block)
        if action is None:
            continue
        indent = indentation(lines[start])
        if CHECKOUT.fullmatch(action):
            lines[start:end] = checkout_block(indent)
            checkout_count += 1
        elif UPLOAD.fullmatch(action):
            lines[start:end] = diagnostic_block(indent, upload_path(block))
            upload_count += 1
    lines = ensure_candidate_env(lines)
    updated = "\n".join(lines) + "\n"
    path.write_text(updated, encoding="utf-8")
    return checkout_count, upload_count


def main() -> int:
    missing = sorted(name for name in TARGETS if not (WORKFLOW_ROOT / name).is_file())
    if missing:
        raise SystemExit(f"missing target workflows: {', '.join(missing)}")
    checkout_total = 0
    upload_total = 0
    for name in sorted(TARGETS):
        checkout_count, upload_count = migrate(WORKFLOW_ROOT / name)
        checkout_total += checkout_count
        upload_total += upload_count
        print(f"{name}: checkout={checkout_count} upload={upload_count}")
    if checkout_total != EXPECTED_CHECKOUT_STEPS:
        raise SystemExit(
            f"expected {EXPECTED_CHECKOUT_STEPS} checkout replacements, got {checkout_total}"
        )
    if upload_total != EXPECTED_UPLOAD_STEPS:
        raise SystemExit(
            f"expected {EXPECTED_UPLOAD_STEPS} upload replacements, got {upload_total}"
        )
    print(
        f"native workflow migration complete: {checkout_total} checkout and "
        f"{upload_total} upload steps replaced"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
