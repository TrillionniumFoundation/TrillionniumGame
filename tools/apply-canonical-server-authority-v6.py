#!/usr/bin/env python3
"""Run the full canonical-authority transformer and apply the strict-Clippy repair."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
TOOLING = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent


def main() -> int:
    transformer = TOOLING / "apply-canonical-server-authority-v5.py"
    if not transformer.is_file():
        transformer = TOOLING / "tools/apply-canonical-server-authority-v5.py"
    if not transformer.is_file():
        raise SystemExit("missing v5 canonical-authority transformer")
    subprocess.run([sys.executable, str(transformer), str(ROOT), str(TOOLING)], check=True)

    path = ROOT / "crates/trnm-presence-router-v2/src/session_registry.rs"
    text = path.read_text(encoding="utf-8")
    anchor = "#[derive(Clone, Debug)]\npub struct SessionRouteRegistry {"
    if anchor in text:
        text = text.replace(
            anchor,
            "#[derive(Clone, Debug, Default)]\npub struct SessionRouteRegistry {",
            1,
        )
    elif "#[derive(Clone, Debug, Default)]\npub struct SessionRouteRegistry {" not in text:
        raise SystemExit("SessionRouteRegistry derive anchor drift")

    pattern = re.compile(
        r"\nimpl Default for SessionRouteRegistry \{\n"
        r"    fn default\(\) -> Self \{\n"
        r"        Self \{.*?\n"
        r"        \}\n"
        r"    \}\n"
        r"\}\n",
        re.S,
    )
    text, count = pattern.subn("\n", text, count=1)
    if count not in {0, 1}:
        raise SystemExit(f"unexpected manual Default count={count}")
    if count == 0 and "impl Default for SessionRouteRegistry" in text:
        raise SystemExit("manual SessionRouteRegistry Default shape drift")
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    print("canonical authority v6: strict-Clippy session normalization applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
