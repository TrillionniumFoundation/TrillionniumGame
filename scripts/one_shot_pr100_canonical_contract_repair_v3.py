#!/usr/bin/env python3
from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    runpy.run_path(
        str(ROOT / "scripts/one_shot_pr100_canonical_contract_repair.py"),
        run_name="__main__",
    )
    subprocess.run(
        [
            "git",
            "checkout",
            "HEAD",
            "--",
            ".github/workflows/plan-contract.yml",
            ".github/workflows/prospective-merge-gate.yml",
            ".github/workflows/pr100-canonical-contract-repair-v2.yml",
            "scripts/one_shot_pr100_canonical_contract_repair.py",
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
