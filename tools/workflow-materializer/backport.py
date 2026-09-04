#!/usr/bin/env python3
"""Materialize the same reviewed trigger contract for the 53-workflow parent.

This trusted wrapper changes only the generator's fixed source identity and
closed-set cardinality. The Wave-2-only minimum-job test and prose are omitted
because those profiles do not exist in the parent. Native parent qualification
still tests every actual registered workflow; no test is skipped at runtime.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

path = Path(__file__).resolve().with_name('driver.py')
data = path.read_bytes()
assert hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest() == '8f8c813dd9b1aab20f6fdb2b10853baf1c1004b0'
text = data.decode()
replacements = {
    "len(by_id) == 55 == overlay['composed_external_workflow_count']": "len(by_id) == 53 == overlay['composed_external_workflow_count']",
    "len({row['path'] for row in by_id.values()}) == 55 and len({row['name'] for row in by_id.values()}) == 55": "len({row['path'] for row in by_id.values()}) == 53 and len({row['name'] for row in by_id.values()}) == 53",
    "'required_external_count': 55": "'required_external_count': 53",
    "verifies every required PR trigger, and requires both source/unit and live jobs for the PostgreSQL TLS and CockroachDB retry lanes.": "verifies every required PR trigger, and retains the full registered parent workflow set.",
}
for old, new in replacements.items():
    assert text.count(old) == 1, 'trusted generator shape changed'
    text = text.replace(old, new, 1)
namespace = {'__name__': 'trusted_parent_materializer', '__file__': str(path)}
exec(compile(text, str(path), 'exec'), namespace)
namespace['BASE'] = 'fe86b6018414cbe50f01b5e83af72fbae02cd892'
namespace['BASE_TREE'] = 'c1c0b20925f77214fa361a28b7e439bb8fc6efea'
tests = namespace['TESTS']
parsed = ast.parse(tests)
matches = [node for node in ast.walk(parsed) if isinstance(node, ast.FunctionDef) and node.name == 'test_wave_two_requires_both_unit_and_live_execution_jobs']
assert len(matches) == 1
method = matches[0]
lines = tests.splitlines(keepends=True)
namespace['TESTS'] = ''.join(line for index, line in enumerate(lines, 1) if not method.lineno <= index <= method.end_lineno)
ast.parse(namespace['TESTS'])
namespace['main']()
