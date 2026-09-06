#!/usr/bin/env python3
"""Derive the reviewed v7 source-wave shell sequence from v5.

The source v5 workflow remains the auditable long-form implementation. This tool
performs narrowly-scoped, count-checked rewrites and emits seven executable shell
fragments. It fails closed when any reviewed anchor drifts.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def extract_run_scripts(source: Path) -> list[str]:
    lines = source.read_text(encoding="utf-8").splitlines()
    scripts: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index] != "        run: |":
            index += 1
            continue
        index += 1
        body: list[str] = []
        while index < len(lines):
            line = lines[index]
            if line and not line.startswith("          "):
                break
            body.append(line[10:] if line.startswith("          ") else "")
            index += 1
        scripts.append("\n".join(body) + "\n")
    if len(scripts) != 7:
        raise SystemExit(f"expected seven reviewed scripts, observed {len(scripts)}")
    return scripts


def replace_once(script: str, old: str, new: str, label: str) -> str:
    count = script.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: expected exactly one reviewed anchor, observed {count}"
        )
    return script.replace(old, new, 1)


def rewrite(scripts: list[str]) -> list[str]:
    scripts[0] = replace_once(
        scripts[0],
        'extended_base=$(gh api "repos/${GITHUB_REPOSITORY}/pulls/92" --jq \' .base.sha\')'.replace("' .", "'."),
        'extended_base="$SOURCE_BASE_SHA"',
        "remove nonexistent PR #92 base lookup",
    )
    scripts[0] = replace_once(
        scripts[0],
        'extended_head=$(gh api "repos/${GITHUB_REPOSITORY}/pulls/92" --jq \' .head.sha\')'.replace("' .", "'."),
        'extended_head="$SOURCE_BASE_SHA"',
        "remove nonexistent PR #92 head lookup",
    )
    scripts[0] = replace_once(
        scripts[0],
        'test -n "$plan_sha" && test -n "$plan_ref"',
        (
            'test "$plan_sha" = "$PLAN_HEAD_SHA"\n'
            'test "$plan_ref" = codex/branch-evidence-closure-2026-09-02'
        ),
        "bind singular Plan PR identity to exact current head",
    )
    if "pulls/92" in scripts[0]:
        raise SystemExit("nonexistent PR #92 dependency survived rewrite")

    continuation = chr(92)
    hardening_fetch = (
        '"$HARDENING_SHA:refs/remotes/source/hardening" ' + continuation
    )
    scripts[1] = replace_once(
        scripts[1],
        hardening_fetch,
        (
            hardening_fetch
            + "\n"
            + '    "$CRYPTO_SHA:refs/remotes/source/crypto" '
            + continuation
            + "\n"
            + '    "$MIGRATION_SHA:refs/remotes/source/migration" '
            + continuation
        ),
        "fetch immutable crypto and migration heads",
    )
    scripts[1] = replace_once(
        scripts[1],
        "for ref in outbox session realtime server hardening; do",
        "for ref in outbox session realtime crypto migration server hardening; do",
        "verify every immutable feature head descends from source-wave base",
    )

    scripts[2] = replace_once(
        scripts[2],
        'git checkout "$SERVER_SHA" -- crates/trnm-server',
        (
            'git checkout "$CRYPTO_SHA" -- crates/trnm-token-crypto-provider\n'
            'git checkout "$MIGRATION_SHA" -- \\\n'
            '  crates/trnm-persistence-core/src/lib.rs \\\n'
            '  crates/trnm-persistence-core/src/migration_fence.rs\n'
            'git checkout "$SERVER_SHA" -- crates/trnm-server'
        ),
        "compose crypto and migration source waves",
    )
    scripts[2] = replace_once(
        scripts[2],
        "apply-canonical-server-authority-v5.py",
        "apply-canonical-server-authority-v7.py",
        "select latest canonical authority transformer",
    )
    return scripts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_workflow", type=Path)
    parser.add_argument("target_directory", type=Path)
    args = parser.parse_args()

    if not args.source_workflow.is_file():
        raise SystemExit(f"missing source workflow: {args.source_workflow}")
    args.target_directory.mkdir(parents=True, exist_ok=True)

    scripts = rewrite(extract_run_scripts(args.source_workflow))
    for number, body in enumerate(scripts, 1):
        path = args.target_directory / f"v7-{number}.sh"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o700)
    print("v7 reviewed shell sequence emitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
