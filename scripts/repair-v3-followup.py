#!/usr/bin/env python3
"""One-shot bounded repair for exact-head workflow and parser drift.

The script is intentionally deterministic and self-deleting. It updates only
reviewed files, asserts exact replacement counts, restores the permanent
read-only Actions smoke workflow, and leaves compatibility claims fail closed.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github/workflows"
WORKFLOW_TARGETS = (
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
)

OLD_PARSE = '''    def parse(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.parse_scope("")
        return self.items, self.manual
'''
NEW_PARSE = '''    def parse(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            self.parse_scope("")
        except DenominatorError as exc:
            # The pinned declaration file contains constructs outside this
            # deliberately small parser. Preserve all successfully extracted
            # leaves and convert the unresolved suffix into an explicit manual
            # contract instead of dropping it or aborting the whole D4 lane.
            start = min(self.index, max(0, len(self.tokens) - 1))
            residual = self.tokens[start:]
            self.manual.append(
                {
                    "class": "typescript_parser_residual_manual_contract",
                    "symbol": "<parser-residual>",
                    "path": self.path,
                    "start_line": residual[0].line if residual else None,
                    "end_line": residual[-1].line if residual else None,
                    "signature": normalize_tokens(residual),
                    "reason": str(exc),
                    "token_count": len(residual),
                }
            )
            self.index = len(self.tokens)
        return self.items, self.manual
'''

OLD_CLEANUP = '''cleanup() {
  status=$?
  if [[ ${TRNM_KEEP_ORACLE:-0} != 1 ]]; then
    docker compose --env-file "$env_file" -f "$compose" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$work"
  exit "$status"
}
'''
NEW_CLEANUP = '''cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    docker compose --env-file "$env_file" -f "$compose" ps -a \
      >"$output/compose-ps.txt" 2>&1 || true
    docker compose --env-file "$env_file" -f "$compose" logs --no-color \
      >"$output/compose-logs.txt" 2>&1 || true
    printf 'immutable oracle failed; diagnostics: %s and %s\\n' \
      "$output/compose-ps.txt" "$output/compose-logs.txt" >&2
  fi
  if [[ ${TRNM_KEEP_ORACLE:-0} != 1 ]]; then
    docker compose --env-file "$env_file" -f "$compose" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$work"
  exit "$status"
}
'''

SMOKE = '''name: trillionnium-game-actions-unblock-smoke

on:
  pull_request:
  push:
    branches: [main]
    paths:
      - '.github/workflows/actions-unblock-smoke.yml'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: actions-unblock-smoke-${{ github.ref }}
  cancel-in-progress: false

jobs:
  repository-native-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Record exact execution identity without checkout or external actions
        shell: bash
        run: |
          set -euo pipefail
          printf 'repository=%s\\n' "$GITHUB_REPOSITORY"
          printf 'sha=%s\\n' "$GITHUB_SHA"
          printf 'ref=%s\\n' "$GITHUB_REF"
          printf 'event=%s\\n' "$GITHUB_EVENT_NAME"
          printf 'run_id=%s\\n' "$GITHUB_RUN_ID"
          test "$GITHUB_REPOSITORY" = 'TrillionniumFoundation/TrillionniumGame'
          test -n "$GITHUB_SHA"
          test -n "$GITHUB_RUN_ID"
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def repair_workflow_identity() -> tuple[int, int]:
    expression_count = 0
    shell_count = 0
    for name in WORKFLOW_TARGETS:
        path = WORKFLOW_ROOT / name
        text = path.read_text(encoding="utf-8")
        if "CANDIDATE_SHA:" not in text:
            raise RuntimeError(f"{path}: missing CANDIDATE_SHA environment")
        expression_count += text.count("${{ github.sha }}")
        shell_count += text.count("$GITHUB_SHA")
        text = text.replace(
            "${{ github.sha }}",
            "${{ github.event.pull_request.head.sha || github.sha }}",
        )
        text = text.replace("$GITHUB_SHA", "$CANDIDATE_SHA")
        path.write_text(text, encoding="utf-8")
    if expression_count < 1:
        raise RuntimeError("expected at least one github.sha expression replacement")
    if shell_count < 1:
        raise RuntimeError("expected at least one GITHUB_SHA shell replacement")
    return expression_count, shell_count


def main() -> int:
    expression_count, shell_count = repair_workflow_identity()
    replace_once(
        ROOT / "scripts/generate-runtime-denominator.py",
        OLD_PARSE,
        NEW_PARSE,
    )
    replace_once(
        ROOT / "scripts/oracle/run-immutable-smoke.sh",
        OLD_CLEANUP,
        NEW_CLEANUP,
    )
    (WORKFLOW_ROOT / "actions-unblock-smoke.yml").write_text(SMOKE, encoding="utf-8")
    Path(__file__).unlink()
    print(
        "v3 follow-up repair complete: "
        f"workflow expressions={expression_count}, shell references={shell_count}, "
        "runtime residual contract=1, oracle diagnostics=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
