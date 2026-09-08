#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts/codex-pr113-refresh-world-cas.py"
text = source.read_text(encoding="utf-8")
old = '    workflow = replace_exact(workflow, "world-v13k-2026-09-08", NEW_REQUEST_ID, 5, "request identifier")\n'
new = (
    '    workflow = replace_exact(\n'
    '        workflow,\n'
    '        "inputs.world_publication_request_id == \'world-v13k-2026-09-08\'",\n'
    '        f"inputs.world_publication_request_id == \'{NEW_REQUEST_ID}\'",\n'
    '        2,\n'
    '        "request dispatch identifier",\n'
    '    )\n'
    '    workflow = replace_exact(\n'
    '        workflow,\n'
    '        "      REQUEST_ID: world-v13k-2026-09-08\\n",\n'
    '        f"      REQUEST_ID: {NEW_REQUEST_ID}\\n",\n'
    '        1,\n'
    '        "request environment identifier",\n'
    '    )\n'
)
if text.count(old) != 1:
    raise SystemExit(f"request-id replacement anchor drift: {text.count(old)}")
source.write_text(text.replace(old, new, 1), encoding="utf-8")
runpy.run_path(str(source), run_name="__main__")
