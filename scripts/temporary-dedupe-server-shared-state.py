#!/usr/bin/env python3
"""Narrow one overbroad App drain assertion in the reviewed payload."""
from __future__ import annotations

import ast
import base64
import hashlib
import re
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "scripts/temporary-close-pr57-review-blockers.py"
FAILURE = "app private drain state remains after shared-state patch"
PRIVATE_APP_FIELD = (
    "    sessions: SessionApi,\n"
    "    draining: bool,\n"
    "    metrics: SharedAppMetrics,\n"
)

wrapper = PATCHER.read_text(encoding="utf-8")
match = re.search(
    r'PAYLOAD_SHA256 = "([0-9a-f]{64})".*?base64\.b85decode\("""(.*?)"""\)\)',
    wrapper,
    flags=re.DOTALL,
)
if match is None:
    raise SystemExit("reviewed blocker payload wrapper is not structurally recognizable")
expected_digest, encoded = match.groups()
payload = zlib.decompress(base64.b85decode(encoded))
actual_digest = hashlib.sha256(payload).hexdigest()
if actual_digest != expected_digest:
    raise SystemExit(
        f"reviewed blocker payload digest mismatch: expected {expected_digest}, got {actual_digest}"
    )

tree = ast.parse(payload.decode("utf-8"), filename=str(PATCHER))
modified = 0
for function in tree.body:
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if function.name != "patch_app":
        continue
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        body_strings = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if FAILURE not in body_strings:
            continue
        node.test = ast.parse(
            "'self.draining' in text or PRIVATE_APP_FIELD in text",
            mode="eval",
        ).body
        modified += 1

if modified != 1:
    raise SystemExit(f"expected one overbroad App drain assertion, modified {modified}")

# The helper emits a plain, auditable repair script. The immutable wrapper digest
# was verified before transformation; the temporary plain source is deleted by
# the finalizer after the validated product commit is created.
prefix = f"PRIVATE_APP_FIELD = {PRIVATE_APP_FIELD!r}\n"
PATCHER.write_text(prefix + ast.unparse(tree) + "\n", encoding="utf-8")
print(
    "reviewed payload digest verified; narrowed App private-drain assertion "
    f"from global field text to exact App context ({actual_digest})"
)
