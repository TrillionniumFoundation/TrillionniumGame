#!/usr/bin/env python3
"""Acquire the closed immutable source annex before native fixture execution.

Source bytes and licensing are retained verbatim. This is neither a Nakama
server execution nor authority to access production player data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
MEMBERS = (
    ("initial-schema.sql", "migrate/sql/20180103142001_initial_schema.sql", 9530,
     "aa128be66236fca4255db9c674bb2ca630cee3eaf3c8927bdeb23f528bc10d01",
     "e046424f0bcd47030e4a93fce0c0623657224550"),
    ("core-storage.go", "server/core_storage.go", 32454,
     "e9632afa6b83e5692bcc149e59c23d35c3a6acfe68a9913ec78c5c27ccc50502",
     "23cb77e991124629c9c780ac981e6393313f0769"),
    ("LICENSE", "LICENSE", 11358,
     "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
     "d645695673349e3947e8e5ae42332d0ac3164cd7"),
)


def validate_bytes(data: bytes, member: tuple) -> dict:
    name, path, size, sha256, blob = member
    actual_blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256 or actual_blob != blob:
        raise ValueError("pinned_storage_source_bytes_differ")
    return {"name": name, "upstream_path": path, "size_bytes": size,
            "sha256": sha256, "git_blob_sha1": blob}


def acquire(destination: Path) -> dict:
    # Never replace an existing annex, including an incomplete earlier attempt.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    deadline = time.monotonic() + 90
    entries = []
    for member in MEMBERS:
        name, path, size, _, _ = member
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("pinned_storage_source_deadline_exceeded")
        url = f"https://raw.githubusercontent.com/heroiclabs/nakama/{COMMIT}/{path}"
        with urllib.request.urlopen(url, timeout=min(15, remaining)) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("pinned_storage_source_response_invalid")
            data = response.read(size + 1)
        if time.monotonic() >= deadline:
            raise ValueError("pinned_storage_source_deadline_exceeded")
        entry = validate_bytes(data, member)
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        entries.append(entry)
    return {"schema": "trillionnium.storage-source-annex.v1", "repository": "heroiclabs/nakama",
            "commit": COMMIT, "tree": TREE, "members": entries,
            "server_execution_credit": False, "compatibility_credit": False, "accepted": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = acquire(args.destination)
    except (OSError, ValueError) as error:
        # Fixed public source URLs contain no credentials; retain only a stable
        # reason because networking/OS exception text may include caller paths.
        reason = str(error) if isinstance(error, ValueError) else "pinned_storage_source_acquisition_failed"
        raise SystemExit(reason)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
