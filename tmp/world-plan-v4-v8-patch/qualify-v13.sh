#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
export_root="${3:-$PWD/export}"
manifest="$world/trillionnium/Cargo.toml"
partitioner="$control/tmp/world-plan-v4-v8-patch/partition-semantic-rust.py"
compat="$control/tmp/world-plan-v4-v8-patch/apply-partition-compat.py"

# First reproduce the exact green v12 source candidate. This leaves the tested
# unpartitioned candidate in the World working tree.
bash "$control/tmp/world-plan-v4-v8-patch/qualify-v12.sh" \
  "$world" "$control" "$export_root"

# Decompose only at parsed item boundaries and by named ownership domains.
python3 "$partitioner" "$world" game-server
python3 "$partitioner" "$world" campaign
python3 "$partitioner" "$world" rts
python3 "$compat" "$world"

python3 - "$world" <<'PY'
from pathlib import Path
import hashlib
import json
import sys

root = Path(sys.argv[1])
expected = {
    "trnm-game-server": {
        "authority_foundation", "configuration_and_migrations", "terminal_recovery",
        "operations_boundary", "fleet_fencing", "identity", "application",
        "http_routing", "readiness", "product_api", "actor_runtime",
        "campaign_persistence", "tests",
    },
    "trnm-campaign-core": {
        "contracts_and_domain", "authored_content", "campaign_state",
        "campaign_commands", "rts_mapping", "save_slots", "player_settings",
        "campaign_storage", "economy_commands", "tests",
    },
    "trnm-rts-sim": {
        "contracts_and_primitives", "mission_runtime", "simulation_helpers",
        "replay", "checkpoint_storage", "tests",
    },
}
for crate, sections in expected.items():
    src = root / "trillionnium/crates" / crate / "src"
    manifest_path = src / "lib_parts/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("semantic_generation") is not False:
        raise SystemExit(f"{crate}: semantic generation was not prohibited")
    if set(manifest.get("sections", [])) != sections:
        raise SystemExit(f"{crate}: ownership sections drifted")
    listed = set()
    for field in ("parts", "nested_test_parts"):
        for record in manifest.get(field, []):
            relative = record["path"]
            path = src / relative
            data = path.read_bytes()
            if len(data) != record["bytes"]:
                raise SystemExit(f"{crate}: byte length drifted for {relative}")
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise SystemExit(f"{crate}: SHA-256 drifted for {relative}")
            if len(data) > 60_000:
                raise SystemExit(f"{crate}: oversized source part {relative}")
            listed.add(path.resolve())
    discovered = {path.resolve() for path in (src / "lib_parts").rglob("*.rs")}
    if listed != discovered:
        raise SystemExit(f"{crate}: manifest/file-set drifted")
    wrapper = (src / "lib.rs").read_text(encoding="utf-8")
    if "Ownership section:" not in wrapper or "OUT_DIR" in wrapper:
        raise SystemExit(f"{crate}: wrapper is not an ordinary ownership map")
print("semantic source ownership manifests: PASS")
PY

cargo fmt --manifest-path "$manifest" --all
cargo fmt --manifest-path "$manifest" --all -- --check
git -C "$world" diff --check

# Re-run behavior, source contracts and compilation on the exact partitioned tree.
cargo test --manifest-path "$manifest" --locked -p trnm-campaign-core --lib
cargo test --manifest-path "$manifest" --locked -p trnm-rts-sim --lib
cargo test --manifest-path "$manifest" --locked -p trnm-game-server --lib
for target in \
  direct_source_bundle \
  direct_cex_source_contract \
  settlement_game_server_boundary \
  settlement_fault_model \
  settlement_worker_contract \
  settlement_runtime_v2_contract; do
  cargo test --manifest-path "$manifest" --locked -p trnm-game-server --test "$target"
done
cargo check --manifest-path "$manifest" --locked -p trnm-game-server --all-targets
cargo clippy --manifest-path "$manifest" --locked \
  -p trnm-campaign-core -p trnm-rts-sim -p trnm-game-server \
  --all-targets -- -D warnings

# Re-run the deterministic cross-implementation transition contract.
python3 "$world/scripts/check-trnm-world-transition-conformance.py"
contract="$world/trillionnium/contracts/trnm-world-transition-v1/Cargo.toml"
cargo test --manifest-path "$contract" --locked
cargo clippy --manifest-path "$contract" --all-targets --locked -- -D warnings

git -C "$world" diff --check

# Seal only the exact source tree that passed all v13 gates.
rm -rf "$export_root"
mkdir -p "$export_root"
git -C "$world" diff --binary > "$export_root/world-v13-source.patch"
python3 - "$world" "$export_root" <<'PY'
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

world = Path(sys.argv[1])
out = Path(sys.argv[2])
files = []
for status_line in subprocess.check_output(
    ["git", "-C", str(world), "status", "--porcelain=v1", "-z"],
).split(b"\0"):
    if not status_line:
        continue
    status = status_line[:2].decode("ascii")
    relative = status_line[3:].decode("utf-8")
    if " -> " in relative:
        relative = relative.split(" -> ", 1)[1]
    path = world / relative
    record = {"path": relative, "status": status}
    if path.is_file():
        data = path.read_bytes()
        record.update({
            "bytes": len(data),
            "git_blob_sha1": subprocess.check_output(
                ["git", "-C", str(world), "hash-object", relative], text=True
            ).strip(),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    files.append(record)
manifest = {
    "schema": "trnm_world_plan_v4_v13_source_candidate",
    "source_world_head": subprocess.check_output(
        ["git", "-C", str(world), "rev-parse", "HEAD"], text=True
    ).strip(),
    "source_world_tree": subprocess.check_output(
        ["git", "-C", str(world), "rev-parse", "HEAD^{tree}"], text=True
    ).strip(),
    "qualification_head": os.environ.get("GITHUB_SHA", "local"),
    "rust_toolchain": "1.98.0",
    "gates": [
        "campaign-46-tests",
        "rts-34-tests",
        "game-server-unit-and-source-contract-tests",
        "game-server-all-target-check",
        "strict-clippy-all-targets",
        "semantic-source-ownership-manifests",
        "transition-cross-implementation-conformance",
    ],
    "files": sorted(files, key=lambda record: record["path"]),
}
(out / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
(out / "identity.txt").write_text(
    "\n".join([
        f"source_world_head={manifest['source_world_head']}",
        f"source_world_tree={manifest['source_world_tree']}",
        f"qualification_head={manifest['qualification_head']}",
        "rust_toolchain=1.98.0",
        "qualification=PASS",
        "",
    ]),
    encoding="utf-8",
)
PY
find "$export_root" -type f -print0 | sort -z | xargs -0 sha256sum > "$export_root/SHA256SUMS"
printf 'TRNM_WORLD_V13_PARTITIONED_QUALIFICATION=PASS\n'
