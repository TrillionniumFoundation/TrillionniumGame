#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/postgresql-pitr-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/remote-mac-provider-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/remote-mac-base"
ORIGINAL_TARGET="$RUNNER_TEMP/remote-mac-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'Opaque remote MAC provider controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/remote-mac-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/35/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/remote-mac-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 240); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-postgresql-pitr.py" 2>/dev/null; then
      base_head=$candidate; break
    fi
  fi
  sleep 12
done
test -n "$base_head"
printf '%s\n' "$base_head" > "$BASE_HEAD_FILE"
target_head=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
printf '%s\n' "$target_head" > "$ORIGINAL_TARGET"
if [[ -n "$target_head" ]]; then
  git -C "$TARGET" fetch --no-tags origin "$target_head"
  git -C "$TARGET" checkout -B "$TARGET_BRANCH" FETCH_HEAD
  git -C "$TARGET" fetch --no-tags origin "$base_head"
  git -C "$TARGET" merge-base --is-ancestor "$base_head" HEAD
else
  git -C "$TARGET" fetch --no-tags origin "$base_head"
  git -C "$TARGET" checkout -B "$TARGET_BRANCH" FETCH_HEAD
fi

python3 -m py_compile "$CONTROLLER/tools/compose_remote_mac_provider.py"
python3 "$CONTROLLER/tools/compose_remote_mac_provider.py" "$TARGET"
git -C "$TARGET" diff --check
cd "$TARGET"
rustup toolchain install 1.85.1 --profile minimal --component rustfmt --component clippy
rustup override set 1.85.1
cargo generate-lockfile
cargo fmt --all
cargo fmt --all -- --check
python3 scripts/check-remote-mac-provider.py
python3 scripts/check-production-crypto-paths.py
python3 scripts/check-auth-provider-composition.py
python3 scripts/check-jwt-rustcrypto-backend.py
python3 scripts/check-schema-authority.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-rust-foundation.py
python3 scripts/check-rust-package-inventory.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
git add -A
git diff --cached --check

remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'security: add opaque remote MAC provider boundary'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
python3 "$CONTROLLER/tools/compose_remote_mac_provider.py" "$TARGET"
cargo generate-lockfile
cargo fmt --all
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft AC-1 security increment is stacked on the fully retained database evidence candidate; it does not create a second main-target line.

\`\`\`text
base_branch    ${BASE_BRANCH}
base_commit    ${base_head}
head_branch    ${TARGET_BRANCH}
head_commit    ${head}
head_tree      ${tree}
controller_run ${GITHUB_RUN_ID}
\`\`\`

The provider accepts only an opaque key reference, request identifier, purpose and bounded message/tag. It enforces a five-second maximum timeout, exact 32-byte verification tag, response correlation, redacted debug output and direct fail-closed transport errors. There is no field or constructor for raw key bytes and no local software fallback.

This is the application-side production-provider boundary, not a vendor KMS/HSM adapter. Live IAM, audit, rotation, revoke, latency and failure evidence plus independent cryptographic acceptance remain required.

\`\`\`text
opaque_remote_mac_provider_source_candidate=true
raw_key_bytes_enter_application_process=false
local_software_fallback_allowed=false
controller_qualified=true
exact_head_native_complete=false
production_adapter_implemented=false
live_kms_hsm_verified=false
accepted_evidence=false
independently_accepted=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'security/crypto: add opaque remote MAC/KMS provider boundary' --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title 'security/crypto: add opaque remote MAC/KMS provider boundary' --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/35/comments" \
  -f body="Opaque remote MAC provider boundary passed full control/Python and Rust all-target strict Clippy at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). The application path has no raw-key or software-fallback API. Vendor KMS/HSM implementation, live IAM/audit/rotation evidence and independent cryptographic acceptance remain false." >/dev/null
