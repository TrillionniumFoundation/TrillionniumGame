#!/usr/bin/env python3
"""Execute the pinned generated gateway for bounded method-routing fixtures.

This source diagnostic is not an immutable Nakama process or accepted wire
qualification. It changes no production source, database or authority gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    upstream = args.upstream.resolve()
    contract = json.loads((ROOT / "contracts/http/nakama-routing-v1.json").read_text())
    observed = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=upstream, text=True, timeout=10
    ).strip()
    if observed != PIN:
        raise SystemExit("upstream commit does not match the frozen baseline")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "apigrpc", "vendor"], cwd=upstream, check=True, timeout=10)
    if subprocess.check_output(["git", "ls-files", "--others", "--", "apigrpc", "vendor"], cwd=upstream, timeout=10):
        raise SystemExit("untracked generated gateway or vendor source is not permitted")
    for path, expected in contract["upstream"]["source_sha256"].items():
        if hashlib.sha256((upstream / path).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"upstream source identity mismatch: {path}")
    version = subprocess.check_output([str(args.go.resolve()), "version"], text=True, timeout=10).strip()
    if not version.startswith("go version go1.26.5 "):
        raise SystemExit("reference requires the pinned Go 1.26.5 toolchain")
    api = (upstream / "server/api.go").read_text()
    # The routing function is the final declaration in this pinned file.
    handler = api[api.index("func handleRoutingError("):]
    if handler.count("\nfunc "):
        raise SystemExit("upstream routing extraction boundary changed")
    harness = (ROOT / "tests/reference/nakama_routing/main.go").read_text()
    fixtures = contract["fixtures"]
    with tempfile.TemporaryDirectory(prefix="nakama-routing-reference-") as directory:
        source = Path(directory) / "main.go"
        source.write_text(api[:api.index("package server")] + "// Modification: extracted routing callback in a diagnostic main package.\n" + harness + "\n" + handler)
        process = subprocess.run(
            [str(args.go.resolve()), "run", "-mod=vendor", str(source)],
            cwd=upstream,
            input=json.dumps([{k: v for k, v in row.items() if k in {"method", "target"}} for row in fixtures]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=180,
            env={**os.environ, "GOTOOLCHAIN": "local", "GOWORK": "off", "GOPROXY": "off", "GOFLAGS": ""},
        )
    result = json.loads(process.stdout)
    if result != fixtures:
        raise SystemExit("original generated gateway diverges from the candidate fixture contract")
    args.output.write_text(json.dumps({
        "upstream_commit": PIN, "go_version": version,
        "source_sha256": contract["upstream"]["source_sha256"],
        "fixtures": result, "full_immutable_oracle": False,
        "security_interceptor_executed": False,
        "accepted": False, "compatibility_credit": False,
    }, indent=2) + "\n")
    print(f"PASS: {len(result)} pinned generated gateway routing cases; source diagnostic only")


if __name__ == "__main__":
    main()
