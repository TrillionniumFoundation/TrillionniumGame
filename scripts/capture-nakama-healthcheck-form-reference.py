#!/usr/bin/env python3
"""Reproduce bounded form fallback and byte-quoting source diagnostics.

No immutable-image, interceptor, database, lifecycle or acceptance credit is
produced. All Go execution uses the pinned source, vendor tree and toolchain.
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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    upstream, go = args.upstream.resolve(), args.go.resolve()
    contract = json.loads((ROOT / "contracts/http/nakama-healthcheck-form-v1.json").read_text())
    fixture = json.loads((ROOT / contract["fixtures"]).read_text())
    observed = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True, timeout=10).strip()
    if observed != PIN:
        raise SystemExit("upstream commit differs from the frozen baseline")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "apigrpc", "vendor"], cwd=upstream, check=True, timeout=10)
    if subprocess.check_output(["git", "ls-files", "--others", "--", "apigrpc", "vendor"], cwd=upstream, timeout=10):
        raise SystemExit("untracked gateway/vendor source is not permitted")
    for path, expected in contract["upstream"]["source_sha256"].items():
        if digest(upstream / path) != expected:
            raise SystemExit(f"upstream source mismatch: {path}")
    version = subprocess.check_output([str(go), "version"], text=True, timeout=10).strip()
    if not version.startswith("go version go1.26.5 "):
        raise SystemExit("reference requires Go 1.26.5")
    goroot = Path(subprocess.check_output([str(go), "env", "GOROOT"], text=True, timeout=10).strip())
    for path, expected in contract["upstream"]["go_source_sha256"].items():
        if digest(goroot / path) != expected:
            raise SystemExit(f"Go standard-library source mismatch: {path}")
    api = (upstream / "server/api.go").read_text()
    routing = api[api.index("func handleRoutingError("):]
    start = api.index("func (s *ApiServer) Healthcheck(")
    health = api[start:api.index("\n}\n", start) + 3]
    health = health.replace("(s *ApiServer)", "(s *referenceServer)", 1)
    if routing.count("\nfunc "):
        raise SystemExit("routing extraction boundary changed")
    harness = (ROOT / "tests/reference/nakama_healthcheck_form/main.go").read_text()
    with tempfile.TemporaryDirectory(prefix="nakama-health-form-reference-") as directory:
        source = Path(directory) / "main.go"
        source.write_text(api[:api.index("package server")] +
            "// Modification: extracted callbacks; health receiver renamed for a diagnostic main package.\n" +
            harness + "\n" + health + "\n" + routing)
        run = subprocess.run([str(go), "run", "-mod=vendor", str(source)], cwd=upstream,
            input=json.dumps(fixture["fixtures"]), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True, timeout=180,
            env={**os.environ, "GOTOOLCHAIN":"local", "GOWORK":"off", "GOPROXY":"off", "GOFLAGS":""})
    result = json.loads(run.stdout)
    expected_quote = {key: contract["go_quote_corpus"][key] for key in ("cases", "bytes", "sha256")}
    if result["fixtures"] != fixture["fixtures"] or result["go_quote_corpus"] != expected_quote or result["nonascii_method_mappings"] != contract["nonascii_method_mappings"]:
        raise SystemExit("original gateway, quote corpus or simple method mapping diverges")
    result.update({"upstream_commit":PIN,"go_version":version,"full_immutable_oracle":False,
        "security_interceptor_executed":False,"accepted":False,"compatibility_credit":False})
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print("PASS: 48 original gateway cases, 65536 exact byte-quote cases and bounded method mappings; source diagnostic only")


if __name__ == "__main__":
    main()
