#!/usr/bin/env python3
"""Repair two reviewed payload post-patch invariants fail-closed."""
from __future__ import annotations

import ast
import base64
import hashlib
import re
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "scripts/temporary-close-pr57-review-blockers.py"
FAILURE_MESSAGE = "app private drain state remains after shared-state patch"
OLD_APP_DRAIN_FIELD = (
    "    sessions: SessionApi,\n"
    "    draining: bool,\n"
    "    metrics: SharedAppMetrics,\n"
)
SHARED_METRICS_MARKER = (
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_metrics(\n"
)
TEST_ONLY_SHARED_METRICS_MARKER = (
    "    #[cfg(test)]\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_metrics(\n"
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
patch_app_functions = []
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    constants = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    if FAILURE_MESSAGE in constants:
        patch_app_functions.append(node)
if len(patch_app_functions) != 1:
    raise SystemExit(
        f"expected one reviewed patch_app function, found {len(patch_app_functions)}"
    )
patch_app = patch_app_functions[0]

matching_guards: list[ast.If] = []
for node in ast.walk(patch_app):
    if not isinstance(node, ast.If):
        continue
    constants = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    if FAILURE_MESSAGE in constants:
        matching_guards.append(node)
if len(matching_guards) != 1:
    raise SystemExit(
        f"expected one reviewed App residual-state guard, found {len(matching_guards)}"
    )
guard = matching_guards[0]
original_test = ast.unparse(guard.test)
expected_tests = {
    "'self.draining' in text or 'draining: bool' in text",
    '"self.draining" in text or "draining: bool" in text',
}
if original_test not in expected_tests:
    raise SystemExit(
        "reviewed App residual-state guard changed unexpectedly: " + original_test
    )
guard.test = ast.BoolOp(
    op=ast.Or(),
    values=[
        ast.Compare(
            left=ast.Constant(value="self.draining"),
            ops=[ast.In()],
            comparators=[ast.Name(id="text", ctx=ast.Load())],
        ),
        ast.Compare(
            left=ast.Constant(value=OLD_APP_DRAIN_FIELD),
            ops=[ast.In()],
            comparators=[ast.Name(id="text", ctx=ast.Load())],
        ),
    ],
)

constructor_calls: list[ast.Call] = []
for node in ast.walk(patch_app):
    if not isinstance(node, ast.Call):
        continue
    if not isinstance(node.func, ast.Name) or node.func.id != "replace_once":
        continue
    labels = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    if "app shared constructor" in labels:
        constructor_calls.append(node)
if len(constructor_calls) != 1:
    raise SystemExit(
        f"expected one reviewed App constructor replacement, found {len(constructor_calls)}"
    )
constructor_call = constructor_calls[0]
if len(constructor_call.args) < 4 or not isinstance(constructor_call.args[2], ast.Constant):
    raise SystemExit("reviewed App constructor replacement has unexpected argument shape")
new_constructor = constructor_call.args[2].value
if not isinstance(new_constructor, str):
    raise SystemExit("reviewed App constructor replacement is not a literal string")
if TEST_ONLY_SHARED_METRICS_MARKER in new_constructor:
    raise SystemExit("reviewed App shared-metrics constructor is already test-only")
if new_constructor.count(SHARED_METRICS_MARKER) != 1:
    raise SystemExit(
        "expected one shared-metrics constructor marker in reviewed replacement, found "
        f"{new_constructor.count(SHARED_METRICS_MARKER)}"
    )
constructor_call.args[2] = ast.Constant(
    value=new_constructor.replace(
        SHARED_METRICS_MARKER,
        TEST_ONLY_SHARED_METRICS_MARKER,
        1,
    )
)

ast.fix_missing_locations(tree)
PATCHER.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
print(
    "reviewed App drain guard narrowed and test-only shared-metrics constructor "
    "annotated before exact-head validation"
)
