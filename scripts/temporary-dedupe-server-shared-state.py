#!/usr/bin/env python3
"""Remove one duplicate server shared-state replacement after App prepatching."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "scripts/temporary-close-pr57-review-blockers.py"
MARKER = "server app shared state"

source = PATCHER.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(PATCHER))


class RemoveDuplicateReplacement(ast.NodeTransformer):
    def __init__(self) -> None:
        self.removed = 0

    def visit_Expr(self, node: ast.Expr) -> ast.AST | None:
        node = self.generic_visit(node)
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            return node
        strings = {
            child.value
            for child in ast.walk(node.value)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if any(MARKER in value for value in strings):
            self.removed += 1
            return None
        return node


transformer = RemoveDuplicateReplacement()
transformed = transformer.visit(tree)
ast.fix_missing_locations(transformed)
if transformer.removed != 1:
    raise SystemExit(
        f"expected one duplicate server shared-state replacement, removed {transformer.removed}"
    )
PATCHER.write_text(ast.unparse(transformed) + "\n", encoding="utf-8")
print("de-duplicated reviewed server app shared-state replacement")
