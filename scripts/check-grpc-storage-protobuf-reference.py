#!/usr/bin/env python3
"""Compare retained protobuf vectors to the complete pinned upstream api.proto.

This is a schema-codec diagnostic, not Nakama process, DB or API execution.
The oracle is the complete hash-verified upstream schema, never the candidate
subset. No network fetch, output rewriting or normalizer is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BLOB = "ddd2744739a252c268b2be004ff0e45c498adb35"
CANDIDATE = ROOT / "crates/trnm-server/proto/nakama-healthcheck.proto"
FIXTURES = (
    (
        "contracts/grpc/nakama-storage-read-protobuf-fixtures.json",
        "trillionnium.grpc-storage-read-codec-fixtures.v1",
        {
            "empty-read": "ReadStorageObjectsRequest",
            "global-read": "ReadStorageObjectsRequest",
            "owner-duplicate-read": "ReadStorageObjectsRequest",
            "empty-response": "StorageObjects",
            "full-response": "StorageObjects",
            "global-epoch-response": "StorageObjects",
        },
    ),
    (
        "contracts/grpc/nakama-storage-mutation-protobuf-fixtures.json",
        "trillionnium.grpc-storage-mutation-codec-fixtures.v1",
        {
            "empty-write": "WriteStorageObjectsRequest",
            "write-default-permissions": "WriteStorageObjectsRequest",
            "write-explicit-zero-permissions": "WriteStorageObjectsRequest",
            "write-zero-read-omitted-write": "WriteStorageObjectsRequest",
            "write-omitted-read-zero-write": "WriteStorageObjectsRequest",
            "write-explicit-owner-permissions": "WriteStorageObjectsRequest",
            "write-public-read-owner-write": "WriteStorageObjectsRequest",
            "write-negative-permissions": "WriteStorageObjectsRequest",
            "write-out-of-api-range-permissions": "WriteStorageObjectsRequest",
            "write-max-int32-permissions": "WriteStorageObjectsRequest",
            "write-create-only-version": "WriteStorageObjectsRequest",
            "write-exact-strings-json": "WriteStorageObjectsRequest",
            "write-repeated-inputs": "WriteStorageObjectsRequest",
            "write-empty-object": "WriteStorageObjectsRequest",
            "empty-delete": "DeleteStorageObjectsRequest",
            "delete-without-version": "DeleteStorageObjectsRequest",
            "delete-literal-star-version": "DeleteStorageObjectsRequest",
            "delete-exact-version": "DeleteStorageObjectsRequest",
            "delete-repeated-inputs": "DeleteStorageObjectsRequest",
            "delete-empty-object": "DeleteStorageObjectsRequest",
            "empty-write-acks": "StorageObjectAcks",
            "write-acks-full": "StorageObjectAcks",
            "write-acks-epoch": "StorageObjectAcks",
            "write-acks-repeated": "StorageObjectAcks",
        },
    ),
)


def encode(protoc, include, schema, case):
    result = subprocess.run(
        [str(protoc), "--encode=nakama.api." + case["message"],
         "--proto_path=" + str(schema.parent),
         "--proto_path=" + str(include), schema.name],
        input=case["textproto"].encode(), capture_output=True, timeout=10, check=True,
    )
    return result.stdout.hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-api-proto", type=Path, required=True)
    parser.add_argument("--protoc", type=Path, required=True)
    parser.add_argument("--include", type=Path, required=True)
    args = parser.parse_args()
    upstream = args.upstream_api_proto.resolve()
    raw = upstream.read_bytes()
    identity = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if identity != BLOB:
        raise SystemExit("pinned complete upstream protobuf identity mismatch")
    protoc = args.protoc.resolve()
    include = args.include.resolve()
    version = subprocess.run(
        [str(protoc), "--version"], capture_output=True, text=True,
        timeout=10, check=True,
    ).stdout.strip()
    summaries = []
    for relative_path, schema, expected_cases in FIXTURES:
        path = ROOT / relative_path
        fixture = json.loads(path.read_text())
        cases = fixture["cases"]
        if (fixture["schema"] != schema or fixture["upstream_blob"] != BLOB
                or len(cases) != len(expected_cases)
                or {case["id"]: case["message"] for case in cases} != expected_cases):
            raise SystemExit("protobuf fixture inventory mismatch: " + relative_path)
        if "mutation" in schema and fixture["full_rpc_denominator"] != 85:
            raise SystemExit("protobuf fixture denominator mismatch: " + relative_path)
        if fixture["protoc"] != version:
            raise SystemExit("protobuf fixture toolchain mismatch: " + relative_path)
        for case in cases:
            if encode(protoc, include, upstream, case) != case["hex"]:
                raise SystemExit("protobuf reference divergence: " + case["id"])
            if encode(protoc, include, CANDIDATE, case) != case["hex"]:
                raise SystemExit("candidate protobuf divergence: " + case["id"])
        summaries.append({
            "path": relative_path,
            "cases": len(cases),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    print(json.dumps({
        "cases": sum(item["cases"] for item in summaries),
        "read_cases": summaries[0]["cases"],
        "mutation_cases": summaries[1]["cases"],
        "upstream_blob": identity,
        "protoc": version,
        "fixtures": summaries,
        "candidate_schema_sha256": hashlib.sha256(CANDIDATE.read_bytes()).hexdigest(),
        "scope": "complete-pinned-schema and candidate-subset codec comparison only",
        "native_database_executed": False,
        "oracle_process_executed": False,
        "api_validation_executed": False,
    }))


if __name__ == "__main__":
    main()
