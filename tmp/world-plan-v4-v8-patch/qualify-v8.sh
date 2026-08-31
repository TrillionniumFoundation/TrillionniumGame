#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
manifest="$world/trillionnium/Cargo.toml"
export_root="${3:-$PWD/export}"

campaign_patch=/tmp/campaign-v8.patch
rts_patch=/tmp/rts-v8.patch
cat "$control"/tmp/world-plan-v4-v8-patch/campaign.*.patchpart > "$campaign_patch"
cat "$control"/tmp/world-plan-v4-v8-patch/rts.*.patchpart > "$rts_patch"
test "$(sha256sum "$campaign_patch" | awk '{print $1}')" = e936d2b440ae4e76ef800b9a007a11bcda683942d1cdd0431ac25de92197a809
test "$(sha256sum "$rts_patch" | awk '{print $1}')" = ef6ffc3b163a0258aa64791059a16c8b386184f7ef7beddfdd4e2511233e9935
test "$(git -C "$world" rev-parse HEAD)" = 5605cfb8861aa923f69ff032ddbff7d035bccb0c
git -C "$world" apply --check "$campaign_patch"
git -C "$world" apply --check "$rts_patch"

cargo check --manifest-path "$manifest" --locked -p trnm-game-server --lib
generated="$(find "$world/trillionnium/target" -type f -path '*/out/trnm_game_server_lib_generated.rs' -print | head -n 1)"
test -n "$generated"
test -s "$generated"
cp "$generated" /tmp/trnm_game_server_lib_generated.rs

python3 - "$world" <<'PY'
from pathlib import Path
import sys

world = Path(sys.argv[1])
crate = world / "trillionnium/crates/trnm-game-server"
wrapper_path = crate / "src/lib.rs"
wrapper = wrapper_path.read_text(encoding="utf-8")
marker = "// The full reviewed server body is generated from src/lib.rs.in by build.rs."
if wrapper.count(marker) != 1:
    raise SystemExit("reviewed wrapper drifted")
header = wrapper.split(marker, 1)[0].rstrip()
generated = Path("/tmp/trnm_game_server_lib_generated.rs").read_text(encoding="utf-8")
if "trnm_game_server_lib_generated.rs" in generated or "include!(concat!(" in generated:
    raise SystemExit("generated body retained recursive authority")
wrapper_path.write_text(f"{header}\n\n{generated.lstrip()}", encoding="utf-8")

cargo_path = crate / "Cargo.toml"
cargo = cargo_path.read_text(encoding="utf-8")
build_line = 'build = "build.rs"\n'
if cargo.count(build_line) != 1:
    raise SystemExit("Cargo build declaration drifted")
cargo_path.write_text(cargo.replace(build_line, "", 1), encoding="utf-8")

cex_path = crate / "src/cex.rs"
cex = cex_path.read_text(encoding="utf-8")
old = (
    '            let host = url.host_str().unwrap_or_default();\n'
    '            let loopback = host.eq_ignore_ascii_case("localhost")\n'
    '                || host\n'
    '                    .parse::<IpAddr>()\n'
    '                    .is_ok_and(|address| address.is_loopback());\n'
)
new = (
    '            let host = url.host_str().unwrap_or_default();\n'
    "            let canonical_host = host.trim_start_matches('[').trim_end_matches(']').trim_end_matches('.');\n"
    '            let loopback = canonical_host.eq_ignore_ascii_case("localhost")\n'
    '                || canonical_host\n'
    '                    .parse::<IpAddr>()\n'
    '                    .is_ok_and(|address| address.is_loopback());\n'
)
if cex.count(old) == 1:
    cex_path.write_text(cex.replace(old, new, 1), encoding="utf-8")
elif new not in cex:
    raise SystemExit("CEX loopback normalization drifted")

toolchain = world / "rust-toolchain.toml"
text = toolchain.read_text(encoding="utf-8")
if 'channel = "stable"' in text:
    text = text.replace('channel = "stable"', 'channel = "1.98.0"', 1)
if 'channel = "1.98.0"' not in text:
    raise SystemExit("Rust toolchain drifted")
toolchain.write_text(text, encoding="utf-8")

for obsolete in (crate / "build.rs", crate / "src/lib.rs.in"):
    if not obsolete.is_file():
        raise SystemExit(f"missing obsolete source: {obsolete}")
    obsolete.unlink()
PY

git -C "$world" apply "$campaign_patch"
git -C "$world" apply "$rts_patch"
cargo fmt --manifest-path "$manifest" --all
cargo fmt --manifest-path "$manifest" --all -- --check

test ! -e "$world/trillionnium/crates/trnm-game-server/build.rs"
test ! -e "$world/trillionnium/crates/trnm-game-server/src/lib.rs.in"
! grep -q '^build = "build.rs"$' "$world/trillionnium/crates/trnm-game-server/Cargo.toml"
! grep -q 'trnm_game_server_lib_generated.rs' "$world/trillionnium/crates/trnm-game-server/src/lib.rs"
! grep -q 'settle_pending_matches(&settlement_state' "$world/trillionnium/crates/trnm-game-server/src/lib.rs"
! grep -q 'reconcile_economy(&state.cex' "$world/trillionnium/crates/trnm-game-server/src/lib.rs"
grep -q 'terminal settlement is owned by trnm-settlement-worker' "$world/trillionnium/crates/trnm-game-server/src/lib.rs"
git -C "$world" diff --check

cargo test --manifest-path "$manifest" --locked -p trnm-campaign-core --lib
cargo test --manifest-path "$manifest" --locked -p trnm-rts-sim --lib
cargo check --manifest-path "$manifest" --locked -p trnm-game-server --lib --bins
cargo clippy --manifest-path "$manifest" --locked -p trnm-campaign-core -p trnm-rts-sim --all-targets -- -D warnings
cargo clippy --manifest-path "$manifest" --locked -p trnm-game-server --lib --bins -- -D warnings

rm -rf "$export_root"
mkdir -p "$export_root/world/trillionnium/crates/trnm-game-server/src"
mkdir -p "$export_root/world/trillionnium/crates/trnm-campaign-core/src"
mkdir -p "$export_root/world/trillionnium/crates/trnm-rts-sim/src"
cp "$world/rust-toolchain.toml" "$export_root/world/rust-toolchain.toml"
cp "$world/trillionnium/crates/trnm-game-server/Cargo.toml" "$export_root/world/trillionnium/crates/trnm-game-server/Cargo.toml"
cp "$world/trillionnium/crates/trnm-game-server/src/lib.rs" "$export_root/world/trillionnium/crates/trnm-game-server/src/lib.rs"
cp "$world/trillionnium/crates/trnm-game-server/src/cex.rs" "$export_root/world/trillionnium/crates/trnm-game-server/src/cex.rs"
cp "$world/trillionnium/crates/trnm-campaign-core/src/lib.rs" "$export_root/world/trillionnium/crates/trnm-campaign-core/src/lib.rs"
cp "$world/trillionnium/crates/trnm-rts-sim/src/lib.rs" "$export_root/world/trillionnium/crates/trnm-rts-sim/src/lib.rs"
git -C "$world" diff --binary > "$export_root/world-v8-core.patch"
printf '%s\n' \
  'world_source_head=5605cfb8861aa923f69ff032ddbff7d035bccb0c' \
  "qualification_head=${GITHUB_SHA:-local}" \
  'rust_toolchain=1.98.0' \
  'campaign_patch_sha256=e936d2b440ae4e76ef800b9a007a11bcda683942d1cdd0431ac25de92197a809' \
  'rts_patch_sha256=ef6ffc3b163a0258aa64791059a16c8b386184f7ef7beddfdd4e2511233e9935' \
  > "$export_root/identity.txt"
find "$export_root" -type f -print0 | sort -z | xargs -0 sha256sum > "$export_root/SHA256SUMS"
printf 'TRNM_WORLD_V8_CORE_QUALIFICATION=PASS\n'
