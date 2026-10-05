#!/usr/bin/env python3
"""Compare retained protobuf vectors to the complete pinned upstream api.proto.

This is a schema-codec diagnostic, not Nakama process, DB or API execution.
No network fetch, generated subset, output rewriting or normalizer is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts/grpc/nakama-storage-read-protobuf-fixtures.json"
BLOB = "ddd2744739a252c268b2be004ff0e45c498adb35"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-api-proto", type=Path, required=True)
    parser.add_argument("--protoc", type=Path, required=True)
    parser.add_argument("--include", type=Path, required=True)
    args = parser.parse_args()
    raw = args.upstream_api_proto.read_bytes()
    identity = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if identity != BLOB:
        raise SystemExit("pinned complete upstream protobuf identity mismatch")
    fixture = json.loads(FIXTURE.read_text())
    if fixture["upstream_blob"] != BLOB or len(fixture["cases"]) != 6:
        raise SystemExit("protobuf fixture inventory mismatch")
    for case in fixture["cases"]:
        result = subprocess.run(
            [str(args.protoc.resolve()), "--encode=nakama.api." + case["message"],
             "--proto_path=" + str(args.upstream_api_proto.parent.resolve()),
             "--proto_path=" + str(args.include.resolve()), args.upstream_api_proto.name],
            input=case["textproto"].encode(), capture_output=True, timeout=10, check=True,
        )
        if result.stdout.hex() != case["hex"]:
            raise SystemExit("protobuf reference divergence: " + case["id"])
    print(json.dumps({"cases": 6, "upstream_blob": identity,
                      "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                      "scope": "complete-pinned-schema codec comparison only",
                      "native_database_executed": False, "oracle_process_executed": False}))


if __name__ == "__main__":
    main()
