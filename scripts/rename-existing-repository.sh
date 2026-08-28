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

fail() { printf 'repository rename refused: %s\n' "$*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || fail "GitHub CLI is required"
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"
[[ "$confirm" == "$expected_confirm" ]] || fail "set TRNM_REPOSITORY_RENAME_CONFIRM=${expected_confirm}"

source_json="$(gh api "repos/${source_repo}")" || fail "source repository is not accessible"
source_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$source_json")"
source_main="$(gh api "repos/${source_repo}/git/ref/heads/main" --jq '.object.sha')"
[[ "$source_id" == "$expected_id" ]] || fail "repository ID mismatch: ${source_id}"
if gh api "repos/${target_repo}" >/dev/null 2>&1; then
  fail "target repository name is already occupied"
fi

printf 'Renaming repository ID %s from %s to %s; main=%s\n' "$source_id" "$source_repo" "$target_repo" "$source_main"
gh api --method PATCH "repos/${source_repo}" -f name="$target_name" >/dev/null

target_json="$(gh api "repos/${target_repo}")" || fail "renamed repository is not accessible"
target_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$target_json")"
target_full_name="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["full_name"])' <<<"$target_json")"
target_main="$(gh api "repos/${target_repo}/git/ref/heads/main" --jq '.object.sha')"
[[ "$target_id" == "$source_id" ]] || fail "repository ID changed across rename"
[[ "$target_full_name" == "$target_repo" ]] || fail "repository full name mismatch: ${target_full_name}"
[[ "$target_main" == "$source_main" ]] || fail "main changed across rename"
printf 'Repository rename verified: %s id=%s main=%s\n' "$target_repo" "$target_id" "$target_main"
