#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

evidence=${TRNM_EVIDENCE_ROOT:-consolidation-evidence/static}
expected_head=${TRNM_EXPECTED_HEAD:-}
verify_all_branches=${TRNM_VERIFY_ALL_BRANCHES:-1}
mkdir -p "$evidence/materialized"

if [[ -n "$expected_head" ]]; then
  test "$(git rev-parse HEAD)" = "$expected_head"
fi

git rev-parse HEAD > "$evidence/commit.txt"
git rev-parse HEAD^{tree} > "$evidence/tree-before-materialization.txt"
rustc --version --verbose > "$evidence/rustc-version.txt"
cargo --version --verbose > "$evidence/cargo-version.txt"
go version > "$evidence/go-version.txt"
python3 --version > "$evidence/python-version.txt" 2>&1

if [[ "$verify_all_branches" = 1 ]]; then
  git fetch --force origin '+refs/heads/*:refs/remotes/origin/*'
  : > "$evidence/branches.tsv"
  : > "$evidence/non-ancestors.tsv"
  while IFS= read -r ref; do
    branch=${ref#refs/remotes/origin/}
    [[ "$branch" == HEAD ]] && continue
    tip=$(git rev-parse "$ref")
    if git merge-base --is-ancestor "$tip" HEAD; then
      relation=ancestor
    else
      relation=NOT_ANCESTOR
      printf '%s\t%s\n' "$branch" "$tip" >> "$evidence/non-ancestors.tsv"
    fi
    printf '%s\t%s\t%s\n' "$branch" "$tip" "$relation" >> "$evidence/branches.tsv"
  done < <(git for-each-ref --format='%(refname)' refs/remotes/origin | sort)
  test ! -s "$evidence/non-ancestors.tsv"
fi

backlog=docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz
gzip -t "$backlog"
sha256sum "$backlog" | tee "$evidence/backlog-sha256.txt"
grep -q '^6a3b94c1c76a44b31966e2d5919aa3c5ebc87822fc6169377b174a4a3a50c114 ' \
  "$evidence/backlog-sha256.txt"
python3 scripts/read-backlog.py --summary | tee "$evidence/backlog-summary.json"

cargo generate-lockfile > "$evidence/cargo-generate-lockfile.log" 2>&1
cargo fmt --all > "$evidence/root-fmt-apply.log" 2>&1
isolated=(
  crates/trnm-token-jwt-adapter/Cargo.toml
  crates/trnm-token-jwt-adapter-gate/Cargo.toml
  crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml
  crates/trnm-presence-router-v2/Cargo.toml
)
for manifest in "${isolated[@]}"; do
  label=$(basename "$(dirname "$manifest")")
  cargo fmt --manifest-path "$manifest" > "$evidence/${label}-fmt-apply.log" 2>&1
done

git diff --binary -- . ":(exclude)$evidence" > "$evidence/materialization.patch"
git diff --name-only -- . ":(exclude)$evidence" > "$evidence/materialized-files.txt"
while IFS= read -r path; do
  [[ -z "$path" || ! -f "$path" ]] && continue
  mkdir -p "$evidence/materialized/$(dirname "$path")"
  cp "$path" "$evidence/materialized/$path"
done < "$evidence/materialized-files.txt"
git diff --check -- . ":(exclude)$evidence"

cargo fmt --all -- --check > "$evidence/root-fmt-check.log" 2>&1
cargo test --workspace --all-targets --locked 2>&1 | tee "$evidence/root-test.log"
cargo clippy --workspace --all-targets --locked -- -D warnings \
  2>&1 | tee "$evidence/root-clippy.log"
for manifest in "${isolated[@]}"; do
  label=$(basename "$(dirname "$manifest")")
  cargo fmt --manifest-path "$manifest" -- --check \
    > "$evidence/${label}-fmt-check.log" 2>&1
  cargo test --manifest-path "$manifest" --all-targets --locked \
    2>&1 | tee "$evidence/${label}-test.log"
  cargo clippy --manifest-path "$manifest" --all-targets --locked -- -D warnings \
    2>&1 | tee "$evidence/${label}-clippy.log"
done

python3 -m compileall -q scripts tests tools
checks=(
  scripts/check-plan.py
  scripts/check-status-transitions.py
  scripts/derive-gates.py
  scripts/check-schema-authority.py
  scripts/check-trnm-server.py
  scripts/check-rust-foundation.py
  scripts/check-storage-core.py
  scripts/check-persistence-core.py
  scripts/check-foundation-schema.py
  scripts/check-pgwire-persistence-adapter.py
  scripts/check-pgwire-backup-restore.py
  scripts/test-canonical-framing.py
  scripts/check-transport-core.py
  scripts/check-token-core.py
  scripts/check-presence-core.py
  scripts/verify-presence-router-v2.py
  scripts/check-query-core.py
)
for check in "${checks[@]}"; do
  label=$(basename "$check")
  python3 "$check" 2>&1 | tee "$evidence/${label}.log"
done
python3 -m unittest discover -s tests -p 'test_*.py' \
  2>&1 | tee "$evidence/unittest-discover.log"

python3 - <<'PY'
import json
from pathlib import Path

for path in sorted(Path('.').rglob('*.json')):
    if '.git' in path.parts or 'target' in path.parts or 'consolidation-evidence' in path.parts:
        continue
    json.loads(path.read_text(encoding='utf-8'))
PY
find scripts -type f -name '*.sh' -print0 | sort -z | xargs -0 -n1 bash -n
(cd runtime && go test -mod=readonly ./...) 2>&1 | tee "$evidence/go-test.log"

git rev-parse HEAD^{tree} > "$evidence/tree-after-materialization.txt"
find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
printf 'all_static_gates=success\n' > "$evidence/result.txt"
