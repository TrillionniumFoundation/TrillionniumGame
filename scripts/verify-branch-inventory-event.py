#!/usr/bin/env python3
"""Compatibility entry point for event-aware branch-inventory verification.

The stable verifier now owns the canonical event-admission contract. This module
re-exports that implementation for existing callers and tests without carrying a
second policy copy.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STABLE_PATH = ROOT / "scripts/verify-branch-inventory-stable.py"


def load_stable() -> Any:
    spec = importlib.util.spec_from_file_location(
        "trillionnium_branch_inventory_stable_compatibility_entry", STABLE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load stable verifier: {STABLE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STABLE = load_stable()
BASE = STABLE.BASE
validate_event_scoped_run = STABLE.validate_event_scoped_run


def main() -> int:
    return STABLE.main()


if __name__ == "__main__":
    raise SystemExit(main())
