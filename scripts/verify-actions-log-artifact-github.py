#!/usr/bin/env python3
"""Run the retained-log verifier against GitHub's line-wrapped Contents API.

GitHub's repository Contents API returns RFC 4648 base64 split across lines.
The core verifier deliberately uses ``validate=True``.  This adapter removes
ASCII whitespace from that transport field only, then delegates every identity,
Git blob, workflow/job, archive and evidence assertion to the core verifier.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "scripts/verify-actions-log-artifact.py"


def load_core() -> Any:
    spec = importlib.util.spec_from_file_location(
        "verify_actions_log_artifact_core", CORE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load retained-log verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_core()
_CORE_DECODE_CONTENTS = CORE.decode_contents


def decode_contents(payload: dict[str, Any], label: str) -> bytes:
    normalized = dict(payload)
    content = normalized.get("content")
    if isinstance(content, str):
        normalized["content"] = "".join(content.split())
    return _CORE_DECODE_CONTENTS(normalized, label)


def main() -> int:
    CORE.decode_contents = decode_contents
    return int(CORE.main())


if __name__ == "__main__":
    raise SystemExit(main())
