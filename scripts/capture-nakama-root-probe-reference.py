#!/usr/bin/env python3
"""Execute the original root/router/CORS subset, retaining every raw header.

Static header assertions are a bounded source profile, not an accepted wire
normalizer. Date and all other raw headers remain in the diagnostic artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    upstream, go = args.upstream.resolve(), args.go.resolve()
    contract = json.loads((ROOT / "contracts/http/nakama-root-probe-v1.json").read_text())
    fixture = json.loads((ROOT / contract["fixtures"]).read_text())
    if subprocess.check_output(["git","rev-parse","HEAD"], cwd=upstream, text=True, timeout=10).strip() != PIN:
        raise SystemExit("upstream commit mismatch")
    subprocess.run(["git","diff","--quiet","HEAD","--","vendor"], cwd=upstream, check=True, timeout=10)
    if subprocess.check_output(["git","ls-files","--others","--","vendor"], cwd=upstream, timeout=10):
        raise SystemExit("untracked vendor source is not permitted")
    for path, expected in contract["upstream"]["source_sha256"].items():
        if hashlib.sha256((upstream / path).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"source mismatch: {path}")
    version = subprocess.check_output([str(go),"version"], text=True, timeout=10).strip()
    if not version.startswith("go version go1.26.5 "):
        raise SystemExit("root reference requires Go 1.26.5")
    api = (upstream / "server/api.go").read_text()
    registration = next(line.strip() for line in api.splitlines() if 'grpcGatewayRouter.HandleFunc("/",' in line)
    options = [line.strip() for line in api.splitlines() if line.strip().startswith(("CORSHeaders :=", "CORSOrigins :=", "CORSMethods :=", "handlerWithCORS :="))]
    if len(options) != 4:
        raise SystemExit("CORS extraction boundary changed")
    harness = (ROOT / "tests/reference/nakama_root_probe/main.go").read_text()
    harness = harness.replace("// PINNED_ROOT_REGISTRATION", registration).replace("// PINNED_CORS_OPTIONS", "\n".join(options))
    with tempfile.TemporaryDirectory(prefix="nakama-root-reference-") as directory:
        source = Path(directory) / "main.go"
        source.write_text(api[:api.index("package server")] +
            "// Modification: exact root registration and fixed CORS options in a diagnostic main package.\n" + harness)
        result = subprocess.run([str(go),"run","-mod=vendor",str(source)], cwd=upstream,
            input=json.dumps(fixture["fixtures"]), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True, timeout=180,
            env={**os.environ,"GOTOOLCHAIN":"local","GOWORK":"off","GOPROXY":"off","GOFLAGS":""})
    observations = json.loads(result.stdout)
    if len(observations) != len(fixture["fixtures"]):
        raise SystemExit("root reference case count mismatch")
    for expected, observed in zip(fixture["fixtures"], observations, strict=True):
        if any(expected[key] != observed[key] for key in ("id","method","target","status","body")):
            raise SystemExit("root reference response mismatch")
        if expected["headers"] != observed["request_headers"]:
            raise SystemExit("root reference input mismatch")
        headers = observed["raw_headers"]
        if any(headers.get(name) != value for name, value in expected["asserted_headers"].items()) or any(name in headers for name in expected["absent_headers"]):
            raise SystemExit("root static header profile mismatch")
        if not headers.get("Date"):
            raise SystemExit("original Date witness missing")
    args.output.write_text(json.dumps({"upstream_commit":PIN,"go_version":version,"observations":observations,
        "raw_headers_retained":True,"full_immutable_oracle":False,"normalizer_accepted":False,
        "accepted":False,"compatibility_credit":False},indent=2)+"\n")
    print("PASS: nine original root/router/CORS cases; all raw headers retained; source diagnostic only")


if __name__ == "__main__":
    main()
