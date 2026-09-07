#!/usr/bin/env python3
"""Run the reviewed v7 server-authority migration without rewriting gap scope."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
TOOLING = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent
PROTECTED_PATHS = (
    "docs/status/GAP_REGISTER.json",
    "scripts/control_baselines/gap-register.v1.json",
    "scripts/gap_register_scope_policy.py",
)
EXPECTED_SCOPE_BLOB = "577cfb3a97b6b6b98b8ecd3182991910f3645296"


def git_blob(payload: bytes) -> str:
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).hexdigest()


def read_snapshot() -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative in PROTECTED_PATHS:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"protected gap-scope path is unavailable: {relative}")
        snapshot[relative] = path.read_bytes()
    for relative in PROTECTED_PATHS[:2]:
        actual = git_blob(snapshot[relative])
        if actual != EXPECTED_SCOPE_BLOB:
            raise SystemExit(
                f"protected gap-scope input drift for {relative}: "
                f"expected={EXPECTED_SCOPE_BLOB} actual={actual}"
            )
    if snapshot[PROTECTED_PATHS[0]] != snapshot[PROTECTED_PATHS[1]]:
        raise SystemExit("live gap register and immutable baseline differ before migration")
    return snapshot


def restore(snapshot: dict[str, bytes]) -> None:
    for relative, payload in snapshot.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        if path.read_bytes() != payload:
            raise SystemExit(f"protected gap-scope restoration failed: {relative}")
    for relative in PROTECTED_PATHS[:2]:
        actual = git_blob((ROOT / relative).read_bytes())
        if actual != EXPECTED_SCOPE_BLOB:
            raise SystemExit(
                f"protected gap-scope output drift for {relative}: "
                f"expected={EXPECTED_SCOPE_BLOB} actual={actual}"
            )


def main() -> int:
    transformer = TOOLING / "apply-canonical-server-authority-v7.py"
    if not transformer.is_file():
        transformer = TOOLING / "tools/apply-canonical-server-authority-v7.py"
    if not transformer.is_file():
        raise SystemExit("missing v7 canonical-authority transformer")

    snapshot = read_snapshot()
    subprocess.run([sys.executable, str(transformer), str(ROOT), str(TOOLING)], check=True)
    restore(snapshot)

    # Validate the exact non-shrinkable scope after the migration. This check is
    # intentionally separate from source-authority success; neither grants gap
    # closure, compatibility, or production credit.
    checker = ROOT / "scripts/check-gap-register.py"
    if not checker.is_file():
        raise SystemExit("missing gap-register validator")
    subprocess.run([sys.executable, str(checker)], cwd=ROOT, check=True)
    print("canonical authority v8: immutable gap scope preserved and validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
