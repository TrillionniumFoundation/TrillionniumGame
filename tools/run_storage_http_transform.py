#!/usr/bin/env python3
"""Reassemble, verify and execute the deterministic storage HTTP transform."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

PARTS = (
    ("part-00.b64", "31aa32cf5b98a9665c94ef724b04e8301eb3499e61ef80d7693e831d481da481", 3500),
    ("part-01.b64", "bbd2f9987ba557582819ac414a402e6c7670e022508f732e9fa81b57ce2c3e7c", 3500),
    ("part-02.b64", "08c11c53d5cb380ce71f4d320ca99c45a0f08c9d5dc31860c265bb2a1a1224ec", 3500),
    ("part-03.b64", "fa4355138d6badff7c6573310e68086ef6b791671090276a28b44adbd0c34de8", 3500),
    ("part-04.b64", "f0fb6522c6afc8d337feaaa7f2d889156cfd963d7d9bd1d895f9e7016bd741ef", 3500),
    ("part-05.b64", "2eeb8f9828d3e8f92317a019c4c8c547e9bed20f5f7fd38ed358dcbb037b3a99", 3500),
    ("part-06.b64", "d37ea4090ad2d6306697b5c61d3188cd08d6bca9582c01eb33e0ffbd2f062e9d", 8),
)
ENCODED_BYTES = 21008
ENCODED_SHA256 = "b28f20a99191d56ea5f929f50d6f80e93206d30bf645e681dacb0cfdeb0f6658"
SCRIPT_BYTES = 89626
SCRIPT_SHA256 = "ceb3f3e938606134ab6040b6415c39e3088df5da6a775e3c56058a7bcd164739"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def reconstruct(parts_root: Path) -> bytes:
    encoded_parts: list[bytes] = []
    for name, expected_sha, expected_bytes in PARTS:
        path = parts_root / name
        payload = path.read_bytes()
        if len(payload) != expected_bytes:
            raise RuntimeError(
                f"{path}: byte length {len(payload)} != {expected_bytes}"
            )
        observed_sha = sha256(payload)
        if observed_sha != expected_sha:
            raise RuntimeError(
                f"{path}: SHA-256 {observed_sha} != {expected_sha}"
            )
        encoded_parts.append(payload)

    encoded = b"".join(encoded_parts)
    if len(encoded) != ENCODED_BYTES or sha256(encoded) != ENCODED_SHA256:
        raise RuntimeError("composed base64 payload identity mismatch")
    try:
        source = gzip.decompress(base64.b64decode(encoded, validate=True))
    except (ValueError, gzip.BadGzipFile) as error:
        raise RuntimeError("storage transform payload decode failed") from error
    if len(source) != SCRIPT_BYTES or sha256(source) != SCRIPT_SHA256:
        raise RuntimeError("decoded storage transform identity mismatch")
    if not source.startswith(b"#!/usr/bin/env python3\n"):
        raise RuntimeError("decoded storage transform has an invalid header")
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--parts-root",
        type=Path,
        default=Path(__file__).with_name("storage_http_transform"),
    )
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    if not (root / ".git").is_dir():
        raise RuntimeError(f"target is not a Git worktree: {root}")

    source = reconstruct(arguments.parts_root.resolve())
    with tempfile.TemporaryDirectory(prefix="trnm-storage-http-transform-") as directory:
        script = Path(directory) / "compose_storage_http_api.py"
        script.write_bytes(source)
        subprocess.run([sys.executable, "-m", "py_compile", str(script)], check=True)
        subprocess.run([sys.executable, str(script), str(root)], check=True)
    print(
        {
            "schema": "trillionnium.storage-http-transform.v1",
            "script_sha256": SCRIPT_SHA256,
            "script_bytes": SCRIPT_BYTES,
            "target": str(root),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
