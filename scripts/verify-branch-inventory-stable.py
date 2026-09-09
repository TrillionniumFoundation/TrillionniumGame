#!/usr/bin/env python3
"""Verify a retained branch snapshot while rejecting destructive live-ref drift.

The producer inventory is an exact immutable before-state. A verifier that runs
later must reject deletion or movement of every captured ref, but a newly added
branch cannot invalidate the already retained snapshot. This wrapper preserves
all validation in verify-branch-inventory-log.py and narrows the live comparison
to that safety property.

GitHub may re-run only the verifier job while reusing a successful producer from
an earlier attempt of the same workflow run. The retained producer artifact is
therefore bound to its own attempt, while the verifier remains bound to the
current attempt. A producer from a later attempt, another run, another head, or
a non-successful/non-exact producer job remains rejected.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "scripts/verify-branch-inventory-log.py"
MAX_CONCURRENT_ADDITIONS = 128


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location(
        "trillionnium_branch_inventory_base_verifier", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base verifier: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base()
ORIGINAL_REMOTE_REFS = BASE.remote_refs
ORIGINAL_VALIDATE_INVENTORY = BASE.validate_inventory
ORIGINAL_VERIFY = BASE.verify
ORIGINAL_PARSE = BASE.EMITTER.parse
ORIGINAL_VALIDATE_PRODUCER_JOB = BASE.validate_producer_job
_VERIFY_CONTEXT: dict[str, Any] | None = None


def retained_producer_attempt(
    name: object,
    *,
    head_sha: str,
    run_id: str,
    verifier_run_attempt: str,
    producer_job_attempt: object = None,
) -> str:
    """Validate and return the attempt bound into a retained producer name."""

    prefix = f"branch-inventory-{head_sha}-{run_id}-"
    BASE.require(
        isinstance(name, str) and name.startswith(prefix),
        "inventory envelope name mismatch",
    )
    attempt = name[len(prefix) :]
    BASE.require(
        BASE.RUN_ID.fullmatch(attempt) is not None,
        "inventory envelope attempt is invalid",
    )
    BASE.require(
        BASE.RUN_ID.fullmatch(verifier_run_attempt) is not None,
        "verifier run attempt is invalid",
    )
    BASE.require(
        int(attempt) <= int(verifier_run_attempt),
        "inventory producer attempt is newer than verifier attempt",
    )
    if producer_job_attempt is not None:
        BASE.require(
            isinstance(producer_job_attempt, (int, str))
            and not isinstance(producer_job_attempt, bool),
            "inventory producer job attempt is invalid",
        )
        normalized = str(producer_job_attempt)
        BASE.require(
            BASE.RUN_ID.fullmatch(normalized) is not None,
            "inventory producer job attempt is invalid",
        )
        BASE.require(
            attempt == normalized,
            "inventory producer job/envelope attempt mismatch",
        )
    return attempt


def _context() -> dict[str, Any]:
    BASE.require(_VERIFY_CONTEXT is not None, "inventory verifier context is absent")
    return _VERIFY_CONTEXT


def validate_attempt_scoped_producer(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain the base producer checks and record its own attempt when present."""

    job = ORIGINAL_VALIDATE_PRODUCER_JOB(jobs)
    attempt = job.get("run_attempt")
    if attempt is not None:
        _context()["producer_job_attempt"] = attempt
    return job


def parse_attempt_scoped_artifact(text: str) -> tuple[bytes, dict[str, object]]:
    """Parse an exact artifact and normalize only the base verifier comparison."""

    archive, envelope = ORIGINAL_PARSE(text)
    context = _context()
    producer_attempt = retained_producer_attempt(
        envelope.get("name"),
        head_sha=context["head_sha"],
        run_id=context["run_id"],
        verifier_run_attempt=context["verifier_run_attempt"],
        producer_job_attempt=context.get("producer_job_attempt"),
    )
    actual_name = envelope["name"]
    BASE.require(isinstance(actual_name, str), "inventory envelope name mismatch")
    context["producer_run_attempt"] = producer_attempt
    context["archive_name"] = actual_name

    # The base verifier historically compares the envelope with the verifier's
    # attempt. Normalize only that one comparison after independently proving
    # the producer's exact run/head/name/attempt relationship above.
    normalized = dict(envelope)
    normalized["name"] = (
        f"branch-inventory-{context['head_sha']}-{context['run_id']}-"
        f"{context['verifier_run_attempt']}"
    )
    return archive, normalized


def verify(
    *,
    token: str,
    repository: str,
    head_sha: str,
    run_id: str,
    run_attempt: str,
    output: Path,
) -> dict[str, Any]:
    """Run the base verifier with exact support for reused prior-attempt producers."""

    global _VERIFY_CONTEXT
    BASE.require(_VERIFY_CONTEXT is None, "nested inventory verification is forbidden")
    context: dict[str, Any] = {
        "head_sha": head_sha,
        "run_id": run_id,
        "verifier_run_attempt": run_attempt,
    }
    _VERIFY_CONTEXT = context
    previous_parse = BASE.EMITTER.parse
    previous_validate_producer = BASE.validate_producer_job
    BASE.EMITTER.parse = parse_attempt_scoped_artifact
    BASE.validate_producer_job = validate_attempt_scoped_producer
    try:
        summary = ORIGINAL_VERIFY(
            token=token,
            repository=repository,
            head_sha=head_sha,
            run_id=run_id,
            run_attempt=run_attempt,
            output=output,
        )
    finally:
        BASE.EMITTER.parse = previous_parse
        BASE.validate_producer_job = previous_validate_producer
        _VERIFY_CONTEXT = None

    producer_attempt = context.get("producer_run_attempt")
    archive_name = context.get("archive_name")
    BASE.require(
        isinstance(producer_attempt, str)
        and BASE.RUN_ID.fullmatch(producer_attempt) is not None,
        "inventory producer attempt was not retained",
    )
    BASE.require(
        isinstance(archive_name, str),
        "inventory producer archive name was not retained",
    )
    summary["producer_run_attempt"] = producer_attempt
    summary["archive_name"] = archive_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def snapshot_refs(inventory_bytes: bytes) -> list[tuple[str, str, str]]:
    try:
        value = json.loads(inventory_bytes)
    except json.JSONDecodeError as error:
        raise BASE.VerificationError("branch inventory is invalid JSON") from error
    BASE.require(isinstance(value, dict), "branch inventory must be an object")
    rows = value.get("branches")
    BASE.require(isinstance(rows, list) and rows, "inventory branch rows are empty")
    result: list[tuple[str, str, str]] = []
    names: set[str] = set()
    for row in rows:
        BASE.require(isinstance(row, dict), "inventory branch row must be an object")
        name = row.get("name")
        commit = row.get("commit")
        tree = row.get("tree")
        BASE.require(isinstance(name, str) and name, "inventory branch name missing")
        BASE.require(name not in names, f"duplicate inventory branch name: {name}")
        BASE.require(
            isinstance(commit, str) and BASE.SHA40.fullmatch(commit) is not None,
            f"invalid inventory commit for branch {name}",
        )
        BASE.require(
            isinstance(tree, str) and BASE.SHA40.fullmatch(tree) is not None,
            f"invalid inventory tree for branch {name}",
        )
        names.add(name)
        result.append((name, commit, tree))
    return sorted(result)


def compare_snapshot_to_live(
    captured: list[tuple[str, str, str]],
    live: list[tuple[str, str, str]],
) -> dict[str, Any]:
    captured_by_name = {name: (commit, tree) for name, commit, tree in captured}
    live_by_name = {name: (commit, tree) for name, commit, tree in live}
    BASE.require(
        len(captured_by_name) == len(captured),
        "captured remote refs contain duplicate names",
    )
    BASE.require(len(live_by_name) == len(live), "live remote refs contain duplicate names")

    missing = sorted(set(captured_by_name) - set(live_by_name))
    moved = sorted(
        name
        for name in set(captured_by_name) & set(live_by_name)
        if captured_by_name[name] != live_by_name[name]
    )
    additions = sorted(set(live_by_name) - set(captured_by_name))
    BASE.require(not missing, f"captured branch disappeared after inventory: {missing}")
    BASE.require(not moved, f"captured branch moved after inventory: {moved}")
    BASE.require(
        len(additions) <= MAX_CONCURRENT_ADDITIONS,
        "concurrent branch additions exceeded verification bound",
    )
    return {
        "captured_branch_count": len(captured),
        "live_branch_count": len(live),
        "concurrent_addition_count": len(additions),
        "concurrent_additions": [
            {
                "name": name,
                "commit": live_by_name[name][0],
                "tree": live_by_name[name][1],
            }
            for name in additions
        ],
        "concurrent_removed_branch_count": 0,
        "concurrent_moved_branch_count": 0,
        "captured_remote_refs_reverified": True,
    }


def validate_inventory(
    inventory_bytes: bytes,
    *,
    repository: str,
    head_sha: str,
) -> dict[str, Any]:
    captured = snapshot_refs(inventory_bytes)
    live = ORIGINAL_REMOTE_REFS()
    drift = compare_snapshot_to_live(captured, live)

    # Re-run every original inventory assertion against the immutable captured
    # set. Destructive live drift was rejected above; only bounded additions are
    # omitted from the earlier exact before-state by definition.
    previous = BASE.remote_refs
    BASE.remote_refs = lambda: captured
    try:
        observation = ORIGINAL_VALIDATE_INVENTORY(
            inventory_bytes,
            repository=repository,
            head_sha=head_sha,
        )
    finally:
        BASE.remote_refs = previous
    observation.update(drift)
    return observation


def main() -> int:
    BASE.validate_inventory = validate_inventory
    BASE.verify = verify
    return BASE.main()


if __name__ == "__main__":
    raise SystemExit(main())
