#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / "scripts/codex-pr104-verifier-receipt-budget.py"), run_name="__main__")
source = ROOT / "crates/trnm-presence-router-v2/src/disconnect_journal.rs"
text = source.read_text(encoding="utf-8")
line = "        let verifier_receipt_reservation = self.require_new_receipt_reservation()?;\n"
if text.count(line) != 1:
    raise SystemExit("receipt reservation line count drift")
text = text.replace(line, "", 1)
anchor = (
    "        if self.records.len() >= self.config.active_capacity {\n"
    "            return Err(DisconnectJournalError::ActiveCapacityExceeded {\n"
)
if text.count(anchor) != 1:
    raise SystemExit("active-capacity anchor drift")
text = text.replace(line, "", 1) if line in text else text
text = text.replace(
    anchor,
    line + anchor,
    1,
)
source.write_text(text, encoding="utf-8")
