#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

SOURCES = (
    (
        "heroiclabs/nakama",
        "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09",
        "server/match_common.go",
        "6b17452a50e323c0c4e06d130639bd1119bf0491",
    ),
    (
        "heroiclabs/nakama",
        "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09",
        "go.mod",
        "05584b9e0e80787e424428701281207b7cd3d881",
    ),
    (
        "blugelabs/query_string",
        "e2a05d85a1f2d8e34ce9b9863ce1f867c7d00288",
        "query_string.y",
        "4c3372068564c98e86e54a02e13d09679aab23d9",
    ),
    (
        "blugelabs/query_string",
        "e2a05d85a1f2d8e34ce9b9863ce1f867c7d00288",
        "query_string_lex.go",
        "bc6e578b05db4e9dcc2d90ad6966347208d4545c",
    ),
    (
        "blugelabs/query_string",
        "e2a05d85a1f2d8e34ce9b9863ce1f867c7d00288",
        "query_string_parser.go",
        "35e3f3047d06ebf338280e674d27841ad80c7656",
    ),
    (
        "blugelabs/query_string",
        "e2a05d85a1f2d8e34ce9b9863ce1f867c7d00288",
        "query_string_lex_test.go",
        "61a8239d16388a9a869ce81c99c582ea87d1c2fb",
    ),
    (
        "blugelabs/query_string",
        "e2a05d85a1f2d8e34ce9b9863ce1f867c7d00288",
        "query_string_parser_test.go",
        "4755c19ac5437d38b7fa753c9cfe231b1664ba15",
    ),
)
MAX_FILE_BYTES = 2 * 1024 * 1024


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = []

    for repository, commit, path, expected_blob in SOURCES:
        url = f"https://raw.githubusercontent.com/{repository}/{commit}/{path}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "TrillionniumGame-query-lock/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                data = response.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            print(f"query source fetch failed for {repository}/{path}: {exc}", file=sys.stderr)
            return 1
        if len(data) > MAX_FILE_BYTES:
            print(f"query source exceeds size limit: {repository}/{path}", file=sys.stderr)
            return 1
        actual_blob = git_blob_sha(data)
        if actual_blob != expected_blob:
            print(
                f"query source blob mismatch for {repository}/{path}: "
                f"expected {expected_blob}, got {actual_blob}",
                file=sys.stderr,
            )
            return 1
        destination = args.output / repository / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        evidence.append(
            {
                "repository": repository,
                "commit": commit,
                "path": path,
                "git_blob": actual_blob,
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )

    manifest = {
        "schema": "trillionnium.query-upstream-fetch-evidence.v1",
        "sources": evidence,
        "claims": {
            "query_behavior_compatible": False,
            "sg3_complete": False,
            "production_ready": False,
        },
    }
    (args.output / "evidence.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "query-upstream-lock-verified", "sources": len(evidence)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
