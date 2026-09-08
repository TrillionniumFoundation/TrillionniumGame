#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
TARGET_BRANCH=${TARGET_BRANCH:-codex/architecture-gap-closure-2026-09-08}
TARGET_PR=${TARGET_PR:-116}
PLAN_V32_MODE=${PLAN_V32_MODE:-stage}
case "$PLAN_V32_MODE" in
  stage|validate) ;;
  *) printf 'unsupported PLAN_V32_MODE=%s\n' "$PLAN_V32_MODE" >&2; exit 2 ;;
esac
CONTROLLER="$GITHUB_WORKSPACE/controller"
PROVIDER="$GITHUB_WORKSPACE/provider"
TARGET="$GITHUB_WORKSPACE/target"
ORIGINAL_FILE="$RUNNER_TEMP/original-head"

report_failure() {
  status=$?
  trap - ERR
  original=$(cat "$ORIGINAL_FILE" 2>/dev/null || printf unknown)
  {
    printf 'Plan v3.2 convergence failed closed; target was not force-updated.\n\n```text\nmode=%s\nhead=%s\n' "$PLAN_V32_MODE" "$original"
    for name in control rust go; do
      file="$RUNNER_TEMP/${name}.log"
      if [[ -f "$file" ]]; then
        printf '\n=== %s ===\n' "$name"
        tail -n 140 "$file"
      fi
    done
    printf '\n```\n'
  } > "$RUNNER_TEMP/failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${TARGET_PR}/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$PROVIDER" "$TARGET"
git init "$PROVIDER"
git -C "$PROVIDER" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git -C "$PROVIDER" fetch --no-tags --depth=1 origin refs/heads/codex/provider-composition-transport-2026-09-09
git -C "$PROVIDER" checkout --detach FETCH_HEAD
python3 -m py_compile \
  "$CONTROLLER/tools/plan_v32_converge.py" \
  "$CONTROLLER/tools/finalize_plan_v32_contracts.py" \
  "$CONTROLLER/tools/finalize_plan_v32_regressions.py" \
  "$CONTROLLER/tools/finalize_plan_v32_regressions_v2.py" \
  "$PROVIDER/tools/compose_crypto_provider.py"

previous=''
stable=0
for _ in $(seq 1 8); do
  current=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
  test -n "$current"
  if [[ "$current" == "$previous" ]]; then stable=$((stable + 1)); else previous=$current; stable=1; fi
  if [[ "$stable" -ge 2 ]]; then break; fi
  sleep 5
done
test "$stable" -ge 2
printf '%s\n' "$previous" > "$ORIGINAL_FILE"

git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git -C "$TARGET" fetch --no-tags origin "$previous"
git -C "$TARGET" checkout -b "$TARGET_BRANCH" FETCH_HEAD
test "$(git -C "$TARGET" rev-parse HEAD)" = "$previous"

python3 "$CONTROLLER/tools/plan_v32_converge.py" "$TARGET"
python3 - <<'PY'
from pathlib import Path
path = Path("target/crates/trnm-persistence-pg/src/auth.rs")
text = path.read_text(encoding="utf-8")
if "PKey::hmac" in text and "pub fn from_provider(" in text:
    text = text.replace("pub fn from_provider(", "pub fn from_provider_legacy(", 1)
    path.write_text(text, encoding="utf-8")
PY
python3 "$PROVIDER/tools/compose_crypto_provider.py" "$TARGET"
python3 "$CONTROLLER/tools/finalize_plan_v32_contracts.py" "$TARGET"
python3 "$CONTROLLER/tools/finalize_plan_v32_regressions.py" "$TARGET"
python3 "$CONTROLLER/tools/finalize_plan_v32_regressions_v2.py" "$TARGET"
python3 - <<'PY'
from pathlib import Path
root = Path("target")
changed = []
for path in sorted(root.rglob("*.rs")):
    text = path.read_text(encoding="utf-8")
    if "new_from_slice" not in text or "use hmac" not in text or "KeyInit" in text:
        continue
    updated = text.replace("use hmac::{Hmac, Mac};", "use hmac::{Hmac, KeyInit, Mac};", 1)
    if updated == text:
        updated = text.replace("use hmac::{Mac, Hmac};", "use hmac::{Hmac, KeyInit, Mac};", 1)
    if updated == text:
        raise SystemExit(f"unable to add hmac::KeyInit import: {path}")
    path.write_text(updated, encoding="utf-8")
    changed.append(path.as_posix())
print({"hmac_key_init_imports": changed})
PY
git -C "$TARGET" diff --check
git -C "$TARGET" add -A

cd "$TARGET"
rustup toolchain install 1.85.1 --profile minimal --component rustfmt --component clippy
rustup override set 1.85.1
cargo generate-lockfile
cargo generate-lockfile --manifest-path crates/trnm-token-jwt-adapter-gate/Cargo.toml
cargo generate-lockfile --manifest-path crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml
cargo metadata --format-version 1 --no-deps > "$RUNNER_TEMP/metadata.json"

{
  set -euo pipefail
  python3 scripts/check-production-crypto-paths.py
  python3 scripts/check-jwt-rustcrypto-backend.py
  python3 scripts/check-workspace-convergence.py
  python3 scripts/check-single-server-authority.py
  python3 scripts/check-auth-provider-composition.py
  python3 scripts/check-documentation-authority.py
  python3 scripts/check-plan.py
  python3 scripts/check-rust-foundation.py
  python3 scripts/check-rust-package-inventory.py
  python3 scripts/check-trnm-server.py
  python3 -m compileall -q scripts tools tests
  python3 -m unittest discover -s tests -p 'test_*.py' -q
} 2>&1 | tee "$RUNNER_TEMP/control.log"

{
  set -euo pipefail
  cargo fmt --all
  cargo fmt --manifest-path crates/trnm-token-jwt-adapter-gate/Cargo.toml
  cargo fmt --manifest-path crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml
  cargo fmt --all -- --check
  cargo test --workspace --all-targets --locked
  cargo clippy --workspace --all-targets --locked -- -D warnings
  for manifest in crates/trnm-token-jwt-adapter-gate/Cargo.toml crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml; do
    cargo test --manifest-path "$manifest" --all-targets --locked
    cargo clippy --manifest-path "$manifest" --all-targets --locked -- -D warnings
  done
  cargo test -p trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server --locked
  cargo clippy -p trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server --locked -- -D warnings
  bash scripts/check-rust-server-process.sh
} 2>&1 | tee "$RUNNER_TEMP/rust.log"

{
  set -euo pipefail
  cd runtime
  test -z "$(gofmt -l .)"
  go test ./... -count=1
  go test -race ./... -count=1
  go vet ./...
} 2>&1 | tee "$RUNNER_TEMP/go.log"

original=$(cat "$ORIGINAL_FILE")
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$original"
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
git add -A
git diff --cached --check

if [[ "$PLAN_V32_MODE" == validate ]]; then
  git diff --quiet
  git diff --cached --quiet
  head=$(git rev-parse HEAD)
  tree=$(git rev-parse 'HEAD^{tree}')
  trap - ERR
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${TARGET_PR}/comments" \
    -f body="Plan v3.2 exact target qualification passed at source head \`${head}\` and tree \`${tree}\`: complete control-plane/Python, Rust root and isolated all-target strict Clippy/process, diagnostic compatibility server, and Go race/vet. This proves remote verification only; independent acceptance, complete Nakama parity, production infrastructure and all-gap closure remain separate facts." >/dev/null
  exit 0
fi

staging="docs/internal/plan-v32-workflow-staging"
rm -rf "$staging"
mkdir -p "$staging/files"
python3 - <<'PY'
from __future__ import annotations
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

root = Path.cwd()
staging = root / "docs/internal/plan-v32-workflow-staging"
output = subprocess.check_output(
    ["git", "diff", "--name-status", "HEAD", "--", ".github/workflows"],
    text=True,
).splitlines()
entries = []
for raw in output:
    fields = raw.split("\t")
    status = fields[0]
    if status.startswith(("R", "C")) or len(fields) != 2:
        raise SystemExit(f"unsupported workflow diff row: {raw}")
    path = fields[1]
    if not path.startswith(".github/workflows/"):
        raise SystemExit(f"workflow path escaped scope: {path}")
    row = {"status": status, "path": path}
    if status != "D":
        source = root / path
        if not source.is_file():
            raise SystemExit(f"generated workflow is missing: {path}")
        payload = source.read_bytes()
        destination = staging / "files" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        row["sha256"] = hashlib.sha256(payload).hexdigest()
        row["bytes"] = len(payload)
    entries.append(row)
if not entries:
    raise SystemExit("expected generated workflow changes were absent")
manifest = {
    "schema": "trillionnium.plan-v32-workflow-staging.v1",
    "target_branch": "codex/architecture-gap-closure-2026-09-08",
    "base_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "entries": entries,
    "claim_boundary": {
        "staging_is_acceptance": False,
        "all_gaps_closed": False,
        "production_ready": False,
    },
}
(staging / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, sort_keys=True))
PY

git restore --source=HEAD --staged --worktree -- .github/workflows
git add -A
git diff --cached --check
if git diff --cached --name-only | grep -q '^\.github/workflows/'; then
  printf 'workflow path remained in source-stage commit\n' >&2
  exit 1
fi
git commit -m 'architecture: stage fully verified Plan v3.2 convergence'
git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
head=$(git rev-parse HEAD)
tree=$(git rev-parse 'HEAD^{tree}')
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${TARGET_PR}/comments" \
  -f body="Plan v3.2 source convergence passed complete control-plane/Python, Rust all-target strict Clippy/process, diagnostic compatibility server, and Go race/vet, then fast-forwarded the non-workflow source stage to \`${head}\` (tree \`${tree}\`). Exact generated workflow bytes are staged under \`${staging}\` for connector publication; final exact-head qualification is still required after those bytes replace the active workflow paths. Independent acceptance and all-gap closure remain false." >/dev/null
