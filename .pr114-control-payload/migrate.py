#!/usr/bin/env python3
"""Apply the exact PR #114 composition-root control migration to a checkout."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import zlib

EXPECTED_PATHS = {
    "crates/trnm-server/README.md",
    "contracts/server/vertical-slice-v1.json",
    "contracts/server/rust-server-vertical-slice.v1.json",
    "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json",
    "scripts/check-rust-server-source-candidate.py",
    "scripts/check-rust-server-vertical-slice.py",
    "scripts/check-rust-server-slice.py",
    "tests/control_plane/test_rust_server_slice.py",
}

README_SECTIONS = """## Correctness and failure model

- Command admission preserves typed validation, idempotency keys, bounded retry budgets and explicit ambiguous-commit handling.
- Accepted work is not reported as durable until the PostgreSQL transaction and acknowledgement fence have both succeeded.
- Cancellation is propagated through the bounded repository wrapper; stale or failed work does not become a success claim.
- The extracted composition root remains a source candidate until its database and protocol evidence is independently accepted.

## Security and privacy

- Database URLs, administrator tokens, session keys and client credentials are redacted from diagnostics.
- Non-loopback listeners and plaintext database transport require explicit candidate-only opt-in.
- HTTP body size, read/write deadlines, listener workers, queue depth and WebSocket message counts are bounded.
- No production credential, personal data set, custody key or protected environment secret is stored in this crate.

## Build and test

- Use Rust 1.85.1 with the isolated `Cargo.lock`.
- Required source checks are `cargo fmt --check`, all-target tests and strict Clippy.
- The bounded process smoke verifies configuration parsing, redaction and fail-closed startup without claiming live database ingress.
- PostgreSQL and CockroachDB live lanes, protocol differentials and prospective-merge execution remain separate required evidence.

## Compatibility and evidence

- These routes are not a complete Nakama API, gRPC gateway or RTAPI implementation.
- Source equivalence to the persistence-owned process does not transfer canonical authority or prove deployed behavior.
- Compatibility, database durability, SG4, production, public-online, cutover and retirement claims require retained exact-object evidence and conflict-free specialist review.
- A green unit or smoke result alone cannot close `GAP-P0-SERVER-001`, `GAP-P0-DATA-001` or `GAP-P0-CRYPTO-001`.

"""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--process-script", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    options = arguments()
    encoded = "".join(options.payload.read_text(encoding="utf-8").splitlines())
    payload = json.loads(zlib.decompress(base64.b64decode(encoded)))
    if not isinstance(payload, dict) or set(payload) != EXPECTED_PATHS:
        raise RuntimeError("PR114 control payload path drift")

    for relative, content in payload.items():
        if not isinstance(content, str) or not content.endswith("\n"):
            raise RuntimeError(f"noncanonical payload: {relative}")
        path = Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    process_target = Path("scripts/check-rust-server-process.sh")
    process_target.write_bytes(options.process_script.read_bytes())

    readme_path = Path("crates/trnm-server/README.md")
    readme = readme_path.read_text(encoding="utf-8")
    anchor = "## Known gaps and exit criteria\n"
    if readme.count(anchor) != 1:
        raise RuntimeError("README known-gap anchor drift")
    for heading in (
        "## Correctness and failure model",
        "## Security and privacy",
        "## Build and test",
        "## Compatibility and evidence",
    ):
        if heading in readme:
            raise RuntimeError(f"README section already present: {heading}")
    readme_path.write_text(
        readme.replace(anchor, README_SECTIONS + anchor, 1), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
