#!/usr/bin/env bash
set -Eeuo pipefail

owner="${TRILLIONNIUM_GITHUB_OWNER:-TrillionniumFoundation}"
source_name="${TRILLIONNIUM_SOURCE_REPOSITORY:-Trillionnium-Nakama}"
target_name="${TRILLIONNIUM_TARGET_REPOSITORY:-TrillionniumGame}"
expected_id="${TRNM_EXPECTED_REPOSITORY_ID:-1323087470}"
confirm="${TRNM_REPOSITORY_RENAME_CONFIRM:-}"
expected_confirm="rename-${source_name}-to-${target_name}"
source_repo="${owner}/${source_name}"
target_repo="${owner}/${target_name}"

fail() { printf 'repository rename verification refused: %s\n' "$*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || fail "GitHub CLI is required"
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"

read_identity() {
  local repository=$1
  local document
  document="$(gh api "repos/${repository}")" || return 1
  python3 -c 'import json,sys; value=json.load(sys.stdin); print(value["id"], value["full_name"])' \
    <<<"$document"
}

verify_target() {
  local identity target_id target_full_name target_main
  identity="$(read_identity "$target_repo")" || return 1
  read -r target_id target_full_name <<<"$identity"
  target_main="$(gh api "repos/${target_repo}/git/ref/heads/main" --jq '.object.sha')"
  [[ "$target_id" == "$expected_id" ]] || fail "repository ID mismatch: ${target_id}"
  [[ "$target_full_name" == "$target_repo" ]] || fail "repository full name mismatch: ${target_full_name}"
  printf 'Repository rename verified: %s id=%s main=%s\n' \
    "$target_repo" "$target_id" "$target_main"
}

# The normal post-transition path is read-only and idempotent.
if verify_target; then
  exit 0
fi

# A settings mutation is permitted only when the canonical target is absent and
# an operator supplies the exact, repository-specific confirmation token.
[[ "$confirm" == "$expected_confirm" ]] || \
  fail "target is not verifiable; set TRNM_REPOSITORY_RENAME_CONFIRM=${expected_confirm} only for the original rename"

source_json="$(gh api "repos/${source_repo}")" || fail "source repository is not accessible"
source_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$source_json")"
source_main="$(gh api "repos/${source_repo}/git/ref/heads/main" --jq '.object.sha')"
[[ "$source_id" == "$expected_id" ]] || fail "repository ID mismatch: ${source_id}"
if gh api "repos/${target_repo}" >/dev/null 2>&1; then
  fail "target repository name is occupied by an unexpected repository"
fi

printf 'Renaming repository ID %s from %s to %s; main=%s\n' \
  "$source_id" "$source_repo" "$target_repo" "$source_main"
gh api --method PATCH "repos/${source_repo}" -f name="$target_name" >/dev/null

target_json="$(gh api "repos/${target_repo}")" || fail "renamed repository is not accessible"
target_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$target_json")"
target_full_name="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["full_name"])' <<<"$target_json")"
target_main="$(gh api "repos/${target_repo}/git/ref/heads/main" --jq '.object.sha')"
[[ "$target_id" == "$source_id" ]] || fail "repository ID changed across rename"
[[ "$target_full_name" == "$target_repo" ]] || fail "repository full name mismatch: ${target_full_name}"
[[ "$target_main" == "$source_main" ]] || fail "main changed across rename"
printf 'Repository rename completed and verified: %s id=%s main=%s\n' \
  "$target_repo" "$target_id" "$target_main"
