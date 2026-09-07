#!/usr/bin/env python3
from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    runpy.run_path(
        str(ROOT / "scripts/one_shot_pr104_recovery_state_repair.py"),
        run_name="__main__",
    )
    path = ROOT / "crates/trnm-presence-router-v2/src/disconnect_journal.rs"
    text = path.read_text(encoding="utf-8")
    old = "DisconnectState::Indeterminate { binding }\n                if binding.worker"
    new = "DisconnectState::Indeterminate { binding, .. }\n                if binding.worker"
    if text.count(old) != 1:
        raise SystemExit(f"indeterminate match anchor count={text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    subprocess.run(
        [
            "git",
            "checkout",
            "HEAD",
            "--",
            ".github/workflows/pr104-recovery-state-repair.yml",
            "scripts/one_shot_pr104_recovery_state_repair.py",
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
