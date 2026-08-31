#!/usr/bin/env python3
"""Patch the shared App drain fence and de-duplicate the reviewed payload call."""
from __future__ import annotations

import ast
import base64
import hashlib
import re
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


app = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs"
replace_once(
    app,
    "use std::sync::{Arc, Mutex};\n",
    "use std::sync::atomic::{AtomicBool, Ordering};\nuse std::sync::{Arc, Mutex};\n",
    "app atomic imports",
)
replace_once(app, "    draining: bool,\n", "    draining: Arc<AtomicBool>,\n", "app shared drain field")
replace_once(
    app,
    "    pub fn new(repository: R, admin_token: String) -> Self {\n"
    "        Self::with_shared_metrics(repository, admin_token, SharedAppMetrics::default())\n"
    "    }\n\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_metrics(\n"
    "        repository: R,\n"
    "        admin_token: String,\n"
    "        metrics: SharedAppMetrics,\n"
    "    ) -> Self {\n"
    "        Self {\n"
    "            repository,\n"
    "            admin_token,\n"
    "            sessions: SessionApi::default(),\n"
    "            draining: false,\n"
    "            metrics,\n"
    "        }\n"
    "    }\n",
    "    pub fn new(repository: R, admin_token: String) -> Self {\n"
    "        Self::with_shared_metrics(repository, admin_token, SharedAppMetrics::default())\n"
    "    }\n\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_metrics(\n"
    "        repository: R,\n"
    "        admin_token: String,\n"
    "        metrics: SharedAppMetrics,\n"
    "    ) -> Self {\n"
    "        Self::with_shared_state(\n"
    "            repository,\n"
    "            admin_token,\n"
    "            metrics,\n"
    "            Arc::new(AtomicBool::new(false)),\n"
    "        )\n"
    "    }\n\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_state(\n"
    "        repository: R,\n"
    "        admin_token: String,\n"
    "        metrics: SharedAppMetrics,\n"
    "        draining: Arc<AtomicBool>,\n"
    "    ) -> Self {\n"
    "        Self {\n"
    "            repository,\n"
    "            admin_token,\n"
    "            sessions: SessionApi::default(),\n"
    "            draining,\n"
    "            metrics,\n"
    "        }\n"
    "    }\n",
    "app shared-state constructors",
)
replace_once(
    app,
    "    pub const fn should_stop(&self) -> bool {\n        self.draining\n    }\n",
    "    pub fn should_stop(&self) -> bool {\n        self.draining.load(Ordering::Acquire)\n    }\n",
    "app should-stop fence",
)
text = app.read_text(encoding="utf-8")
if text.count("if self.draining {") != 3:
    raise SystemExit("app drain predicates changed unexpectedly")
text = text.replace("if self.draining {", "if self.draining.load(Ordering::Acquire) {")
if text.count("let ready = u8::from(!self.draining);") != 1:
    raise SystemExit("app readiness metric marker changed unexpectedly")
text = text.replace(
    "let ready = u8::from(!self.draining);",
    "let ready = u8::from(!self.draining.load(Ordering::Acquire));",
)
if text.count("self.draining = true;") != 1:
    raise SystemExit("app drain publication marker changed unexpectedly")
text = text.replace(
    "self.draining = true;",
    "self.draining.store(true, Ordering::Release);",
)
app.write_text(text, encoding="utf-8")

server = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs"
replace_once(
    server,
    "    let mut app = App::with_shared_metrics(repository, config.admin_token.clone(), metrics);\n",
    "    let mut app = App::with_shared_state(\n"
    "        repository,\n"
    "        config.admin_token.clone(),\n"
    "        metrics,\n"
    "        Arc::clone(&draining),\n"
    "    );\n",
    "server app shared drain wiring",
)

# The reviewed source payload also contains the same App-drain transformation.
# Verify its immutable digest, find the unique function containing the fail-closed
# App markers, and remove only the call to that function. The function definition
# remains in the temporary source for auditability; every other repair is unchanged.
patcher = ROOT / "scripts/temporary-close-pr57-review-blockers.py"
wrapper = patcher.read_text(encoding="utf-8")
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
source = payload.decode("utf-8")
tree = ast.parse(source, filename=str(patcher))
markers = (
    "app shared drain field",
    "app private drain state remains after shared-state patch",
)
app_patch_functions: set[str] = set()
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    constants = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    if any(any(marker in value for value in constants) for marker in markers):
        app_patch_functions.add(node.name)
if len(app_patch_functions) != 1:
    raise SystemExit(
        "expected one reviewed App patch function, found "
        + repr(sorted(app_patch_functions))
    )


class RemoveDuplicateAppPatchCall(ast.NodeTransformer):
    def __init__(self, names: set[str]) -> None:
        self.names = names
        self.removed = 0

    def visit_Expr(self, node: ast.Expr) -> ast.AST | None:
        node = self.generic_visit(node)
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in self.names
        ):
            self.removed += 1
            return None
        return node


transformer = RemoveDuplicateAppPatchCall(app_patch_functions)
transformed = transformer.visit(tree)
ast.fix_missing_locations(transformed)
if transformer.removed != 1:
    raise SystemExit(
        f"expected one reviewed App patch call, removed {transformer.removed}"
    )
remaining_calls = [
    node
    for node in ast.walk(transformed)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id in app_patch_functions
]
if remaining_calls:
    raise SystemExit("reviewed App patch call remains after AST de-duplication")
patcher.write_text(ast.unparse(transformed) + "\n", encoding="utf-8")
print(
    "process-shared App drain fence patched; de-duplicated reviewed payload call "
    + next(iter(app_patch_functions))
)
