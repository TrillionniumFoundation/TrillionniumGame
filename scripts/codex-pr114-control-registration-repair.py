#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-rust-server-slice.py"
OVERLAY = ROOT / "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json"
WORKFLOW = ROOT / ".github/workflows/w1-rust-server-vertical-slice.yml"
WORKFLOW_PATH = ".github/workflows/w1-rust-server-vertical-slice.yml"
WORKFLOW_ID = 345394392
WORKFLOW_NAME = "trillionnium-game-rust-server-vertical-slice"


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


checker = CHECKER.read_text(encoding="utf-8")
old = '        "sync_channel(queue_capacity)",\n'
new = '        "sync_channel::<QueuedConnection>(queue_capacity)",\n'
if checker.count(old) != 1:
    raise SystemExit(f"server-slice queue token drift: {checker.count(old)}")
checker = checker.replace(old, new, 1)
CHECKER.write_text(checker, encoding="utf-8")

raw = json.loads(OVERLAY.read_text(encoding="utf-8"))
if not isinstance(raw, dict):
    raise SystemExit("workflow overlay root must be an object")
replacements = raw.get("replace_workflows")
if not isinstance(replacements, list):
    raise SystemExit("workflow overlay replacements must be a list")
matches = [
    item
    for item in replacements
    if isinstance(item, dict)
    and item.get("workflow_id") == WORKFLOW_ID
    and item.get("name") == WORKFLOW_NAME
    and item.get("path") == WORKFLOW_PATH
]
if len(matches) != 1:
    raise SystemExit(f"workflow overlay replacement drift: {len(matches)}")
workflow_blob = git_blob_sha(WORKFLOW.read_bytes())
matches[0]["git_blob_sha1"] = workflow_blob
payload = dict(raw)
payload.pop("overlay_sha256", None)
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
raw["overlay_sha256"] = hashlib.sha256(encoded).hexdigest()
OVERLAY.write_text(
    json.dumps(raw, sort_keys=True, separators=(",", ":")),
    encoding="utf-8",
)
print(f"workflow_blob={workflow_blob}")
print(f"overlay_sha256={raw['overlay_sha256']}")
