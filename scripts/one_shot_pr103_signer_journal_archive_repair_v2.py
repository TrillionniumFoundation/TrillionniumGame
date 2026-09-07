#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    runpy.run_path(
        str(ROOT / "scripts/one_shot_pr103_signer_journal_archive_repair.py"),
        run_name="__main__",
    )
    path = ROOT / "crates/trnm-token-crypto-provider/src/signer_journal.rs"
    text = path.read_text(encoding="utf-8")
    old = "matches!(Self::Confirmed { .. } | Self::Rejected { .. }, self)"
    new = "matches!(self, Self::Confirmed { .. } | Self::Rejected { .. })"
    if text.count(old) != 1:
        raise SystemExit(f"terminal pattern anchor count={text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
