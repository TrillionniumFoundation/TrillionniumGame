#!/usr/bin/env python3
"""Run retained branch-inventory verification with event-scoped admission.

The underlying verifier was originally restricted to pull-request runs, while the
repository workflow also runs after protected-main pushes and by explicit manual
dispatch. This wrapper preserves every original repository, head, attempt, job,
artifact and branch-snapshot assertion and changes only the event admission rule:

* pull_request runs retain the original verifier unchanged;
* push and workflow_dispatch runs are accepted only for the main branch;
* a push run must not be attached to a pull request;
* every other event or branch fails closed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STABLE_PATH = ROOT / "scripts/verify-branch-inventory-stable.py"
NON_PR_EVENTS = {"push", "workflow_dispatch"}


def load_stable() -> Any:
    spec = importlib.util.spec_from_file_location(
        "trillionnium_branch_inventory_stable_event_wrapper", STABLE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load stable verifier: {STABLE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STABLE = load_stable()
BASE = STABLE.BASE
ORIGINAL_VALIDATE_RUN = BASE.validate_run


def validate_event_scoped_run(
    run: dict[str, Any],
    *,
    repository: str,
    head_sha: str,
    run_id: str,
    run_attempt: str,
) -> None:
    """Preserve the exact run contract while admitting only intended events."""

    event = run.get("event")
    if event == "pull_request":
        ORIGINAL_VALIDATE_RUN(
            run,
            repository=repository,
            head_sha=head_sha,
            run_id=run_id,
            run_attempt=run_attempt,
        )
        return

    BASE.require(
        event in NON_PR_EVENTS,
        "workflow event must be pull_request, push, or workflow_dispatch",
    )
    BASE.require(
        run.get("head_branch") == "main",
        f"{event} branch inventory must target main",
    )
    if event == "push":
        pull_requests = run.get("pull_requests")
        BASE.require(
            isinstance(pull_requests, list) and not pull_requests,
            "push branch inventory must not be attached to a pull request",
        )

    normalized = dict(run)
    normalized["event"] = "pull_request"
    ORIGINAL_VALIDATE_RUN(
        normalized,
        repository=repository,
        head_sha=head_sha,
        run_id=run_id,
        run_attempt=run_attempt,
    )


def main() -> int:
    previous = BASE.validate_run
    BASE.validate_run = validate_event_scoped_run
    try:
        return STABLE.main()
    finally:
        BASE.validate_run = previous


if __name__ == "__main__":
    raise SystemExit(main())
