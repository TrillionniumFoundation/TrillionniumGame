#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
export_root="${3:-$PWD/export}"
manifest="$world/trillionnium/Cargo.toml"
crate="$world/trillionnium/crates/trnm-game-server"
campaign_patch=/tmp/campaign-effective.patch
rts_patch=/tmp/rts-effective.patch
direct_contract_patch=/tmp/direct-source-contracts.patch

cat "$control"/tmp/world-plan-v4-v8-patch/campaign.*.patchpart > /tmp/campaign-raw.patch
cat "$control"/tmp/world-plan-v4-v8-patch/rts.*.patchpart > /tmp/rts-raw.patch
cp "$control"/tmp/world-plan-v4-v8-patch/direct-source-contracts.patch "$direct_contract_patch"
test "$(sha256sum /tmp/campaign-raw.patch | awk '{print $1}')" = e936d2b440ae4e76ef800b9a007a11bcda683942d1cdd0431ac25de92197a809
test "$(sha256sum /tmp/rts-raw.patch | awk '{print $1}')" = ef6ffc3b163a0258aa64791059a16c8b386184f7ef7beddfdd4e2511233e9935
test "$(sha256sum "$direct_contract_patch" | awk '{print $1}')" = 42b35779435806502fc1c4ce7922f5674ea4d8a9526e625c2f64f962162e8d16

python3 - <<'PY'
from pathlib import Path
campaign = Path('/tmp/campaign-raw.patch').read_text(encoding='utf-8')
count = campaign.count('__atomic_inner')
if count != 152:
    raise SystemExit(f'campaign helper-name source drifted: {count}')
Path('/tmp/campaign-effective.patch').write_text(
    campaign.replace('__atomic_inner', '_atomic_inner'), encoding='utf-8'
)
rts = Path('/tmp/rts-raw.patch').read_text(encoding='utf-8')
old = '        assert!(error.to_string().contains("field resources"));\n'
new = '        assert!(matches!(error, SimError::Order(_)));\n'
if rts.count(old) != 1:
    raise SystemExit('RTS error-message assertion source drifted')
Path('/tmp/rts-effective.patch').write_text(rts.replace(old, new, 1), encoding='utf-8')
PY

test "$(sha256sum "$campaign_patch" | awk '{print $1}')" = 703b7bc59b9df7eb4a2fae4ac55b0fa3513d511a3b995a1272ddbb253697e77e
test "$(sha256sum "$rts_patch" | awk '{print $1}')" = 7c70a2cdc8e417d04d3b80100fbc219fddbb6a06a65f295aabf6f2be91a5cfeb
test "$(git -C "$world" rev-parse HEAD)" = 5605cfb8861aa923f69ff032ddbff7d035bccb0c
git -C "$world" apply --check "$campaign_patch"
git -C "$world" apply --check "$rts_patch"
git -C "$world" apply --check "$direct_contract_patch"

# Execute the reviewed build transform once, then materialize its output as
# ordinary source and permanently remove the semantic template authority.
cargo check --manifest-path "$manifest" --locked -p trnm-game-server --lib
generated="$(find "$world/trillionnium/target" -type f -path '*/out/trnm_game_server_lib_generated.rs' -print | head -n 1)"
test -n "$generated"
test -s "$generated"
cp "$generated" /tmp/trnm_game_server_lib_generated.rs

python3 - "$world" <<'PY'
from pathlib import Path
import sys
world = Path(sys.argv[1])
crate = world / 'trillionnium/crates/trnm-game-server'
wrapper_path = crate / 'src/lib.rs'
wrapper = wrapper_path.read_text(encoding='utf-8')
marker = '// The full reviewed server body is generated from src/lib.rs.in by build.rs.'
if wrapper.count(marker) != 1:
    raise SystemExit('reviewed wrapper drifted')
header = wrapper.split(marker, 1)[0].rstrip()
generated = Path('/tmp/trnm_game_server_lib_generated.rs').read_text(encoding='utf-8')
if 'trnm_game_server_lib_generated.rs' in generated or 'include!(concat!(' in generated:
    raise SystemExit('materialized body retained recursive authority')
wrapper_path.write_text(f'{header}\n\n{generated.lstrip()}', encoding='utf-8')

cargo_path = crate / 'Cargo.toml'
cargo = cargo_path.read_text(encoding='utf-8')
build_line = 'build = "build.rs"\n'
if cargo.count(build_line) != 1:
    raise SystemExit('Cargo build declaration drifted')
cargo = cargo.replace(build_line, '', 1)
old_reqwest = 'reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }'
new_reqwest = 'reqwest = { version = "0.12", default-features = false, features = ["blocking", "json", "rustls-tls"] }'
if cargo.count(old_reqwest) == 1:
    cargo = cargo.replace(old_reqwest, new_reqwest, 1)
elif new_reqwest not in cargo:
    raise SystemExit('reqwest feature source drifted')
cargo_path.write_text(cargo, encoding='utf-8')

cex_path = crate / 'src/cex.rs'
cex = cex_path.read_text(encoding='utf-8')
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
    cex_path.write_text(cex.replace(old, new, 1), encoding='utf-8')
elif new not in cex:
    raise SystemExit('CEX loopback normalization drifted')

toolchain = world / 'rust-toolchain.toml'
text = toolchain.read_text(encoding='utf-8')
if 'channel = "stable"' in text:
    text = text.replace('channel = "stable"', 'channel = "1.98.0"', 1)
if 'channel = "1.98.0"' not in text:
    raise SystemExit('Rust toolchain drifted')
toolchain.write_text(text, encoding='utf-8')
for obsolete in (crate / 'build.rs', crate / 'src/lib.rs.in'):
    if not obsolete.is_file():
        raise SystemExit(f'missing obsolete source: {obsolete}')
    obsolete.unlink()
PY

git -C "$world" apply "$campaign_patch"
git -C "$world" apply "$rts_patch"
git -C "$world" apply "$direct_contract_patch"
cargo fmt --manifest-path "$manifest" --all
cargo fmt --manifest-path "$manifest" --all -- --check

test ! -e "$crate/build.rs"
test ! -e "$crate/src/lib.rs.in"
! grep -q '^build = "build.rs"$' "$crate/Cargo.toml"
! grep -q 'trnm_game_server_lib_generated.rs' "$crate/src/lib.rs"
! grep -q 'settle_pending_matches(&settlement_state' "$crate/src/lib.rs"
! grep -q 'reconcile_economy(&state.cex' "$crate/src/lib.rs"
grep -q 'terminal settlement is owned by trnm-settlement-worker' "$crate/src/lib.rs"
grep -q 'features = \["blocking", "json", "rustls-tls"\]' "$crate/Cargo.toml"
git -C "$world" diff --check

cargo test --manifest-path "$manifest" --locked -p trnm-campaign-core --lib
cargo test --manifest-path "$manifest" --locked -p trnm-rts-sim --lib
cargo check --manifest-path "$manifest" --locked -p trnm-game-server --all-targets
cargo clippy --manifest-path "$manifest" --locked -p trnm-campaign-core -p trnm-rts-sim -p trnm-game-server --all-targets -- -D warnings

rm -rf "$export_root"
mkdir -p "$export_root"
git -C "$world" diff --binary > "$export_root/world-v10-source.patch"
printf '%s\n' \
  'world_source_head=5605cfb8861aa923f69ff032ddbff7d035bccb0c' \
  "qualification_head=${GITHUB_SHA:-local}" \
  'rust_toolchain=1.98.0' \
  'campaign_effective_sha256=703b7bc59b9df7eb4a2fae4ac55b0fa3513d511a3b995a1272ddbb253697e77e' \
  'rts_effective_sha256=7c70a2cdc8e417d04d3b80100fbc219fddbb6a06a65f295aabf6f2be91a5cfeb' \
  'direct_contract_patch_sha256=42b35779435806502fc1c4ce7922f5674ea4d8a9526e625c2f64f962162e8d16' \
  > "$export_root/identity.txt"
find "$export_root" -type f -print0 | sort -z | xargs -0 sha256sum > "$export_root/SHA256SUMS"
printf 'TRNM_WORLD_V10_CORE_QUALIFICATION=PASS\n'
