#!/usr/bin/env bash
set -euo pipefail

mode="${1:---dev}"
remote_name="${2:-}"
remote_url="${3:-}"
case "$mode" in
  --dev|--audit|--staged|--push) ;;
  *)
    printf 'ERROR: unsupported preflight mode: %s\n' "$mode" >&2
    exit 10
    ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo 'ERROR: not inside a Git repository' >&2
  exit 10
}
root=$(cd "$root" && pwd -P)
cd "$root"

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
boundary="$root/PROJECT_BOUNDARY.json"
project_id_file="$root/PROJECT_ID"

case "$mode" in
  --staged)
    git show :PROJECT_BOUNDARY.json >"$tmpdir/PROJECT_BOUNDARY.json" 2>/dev/null || {
      echo 'ERROR: PROJECT_BOUNDARY.json must exist in the index' >&2
      exit 10
    }
    git show :PROJECT_ID >"$tmpdir/PROJECT_ID" 2>/dev/null || {
      echo 'ERROR: PROJECT_ID must exist in the index' >&2
      exit 10
    }
    boundary="$tmpdir/PROJECT_BOUNDARY.json"
    project_id_file="$tmpdir/PROJECT_ID"
    ;;
  --push)
    git show HEAD:PROJECT_BOUNDARY.json >"$tmpdir/PROJECT_BOUNDARY.json" 2>/dev/null || {
      echo 'ERROR: PROJECT_BOUNDARY.json must exist in HEAD' >&2
      exit 10
    }
    git show HEAD:PROJECT_ID >"$tmpdir/PROJECT_ID" 2>/dev/null || {
      echo 'ERROR: PROJECT_ID must exist in HEAD' >&2
      exit 10
    }
    boundary="$tmpdir/PROJECT_BOUNDARY.json"
    project_id_file="$tmpdir/PROJECT_ID"
    ;;
esac

python3 -m json.tool "$boundary" >/dev/null
project_id=$(tr -d '\r\n' <"$project_id_file")
python3 - "$boundary" "$project_id" <<'PY'
import json
import sys

path, project_id = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
expected = {
    "schema": "trillionnium.project-boundary.v2",
    "project_id": "trillionnium-game",
    "current_repository": "TrillionniumFoundation/TrillionniumGame",
    "target_repository": "TrillionniumFoundation/TrillionniumGame",
    "repository_id": 1323087470,
    "lane": "game-backend-platform",
    "lifecycle": "plan-v3.1-current",
    "documentation_authority": "docs/DOCUMENTATION_AUTHORITY.json",
}
for key, expected_value in expected.items():
    if value.get(key) != expected_value:
        raise SystemExit(f"PROJECT_BOUNDARY.json {key} drifted")
if project_id != value["project_id"]:
    raise SystemExit("PROJECT_ID and PROJECT_BOUNDARY.json disagree")
policy = value.get("language_policy", {})
for key in (
    "go_server_allowed",
    "go_sidecar_allowed",
    "compiled_go_plugin_loader_allowed",
):
    if policy.get(key) is not False:
        raise SystemExit(f"language boundary must keep {key}=false")
claims = value.get("claims", {})
for key in ("drop_in_replacement", "production_ready", "nakama_retired"):
    if claims.get(key) is not False:
        raise SystemExit(f"project boundary must keep {key}=false")
PY

normalize_slug() {
  printf '%s' "$1" | sed -E \
    -e 's#^git@github\.com:#github.com/#' \
    -e 's#^ssh://git@github\.com/#github.com/#' \
    -e 's#^https?://github\.com/#github.com/#' \
    -e 's#\.git$##'
}

origin_url=$(git remote get-url origin 2>/dev/null || true)
origin_slug=$(normalize_slug "$origin_url")
if [[ -n "$origin_url" && "$origin_slug" != 'github.com/TrillionniumFoundation/TrillionniumGame' ]]; then
  printf 'ERROR: origin is not the canonical repository: %s\n' "$origin_url" >&2
  exit 10
fi

branch=$(git branch --show-current 2>/dev/null || true)
if [[ "$mode" != '--audit' ]]; then
  [[ -n "$branch" ]] || {
    echo 'ERROR: detached HEAD is not a development branch' >&2
    exit 10
  }
  if [[ "$branch" == 'main' ]]; then
    echo 'ERROR: direct development on protected main is forbidden' >&2
    exit 10
  fi
  if [[ ! "$branch" =~ ^(codex|feat|fix|docs|chore|integration|archive)/[A-Za-z0-9._/-]+$ ]]; then
    printf 'ERROR: branch does not match the current repository policy: %s\n' "$branch" >&2
    exit 10
  fi
fi

if [[ "$mode" == '--push' ]]; then
  while read -r local_ref local_sha remote_ref remote_sha; do
    [[ -n "${remote_ref:-}" ]] || continue
    if [[ "$remote_ref" == 'refs/heads/main' ]]; then
      echo 'ERROR: direct push to main is forbidden; use a reviewed pull request' >&2
      exit 10
    fi
    if [[ "$remote_ref" == refs/heads/* ]]; then
      pushed_branch=${remote_ref#refs/heads/}
      if [[ ! "$pushed_branch" =~ ^(codex|feat|fix|docs|chore|integration|archive)/[A-Za-z0-9._/-]+$ ]]; then
        printf 'ERROR: pushed branch does not match policy: %s\n' "$pushed_branch" >&2
        exit 10
      fi
    fi
    : "${local_ref:-}" "${local_sha:-}" "${remote_sha:-}"
  done
  printf 'remote=%s\nremote_url=%s\n' "${remote_name:-origin}" "${remote_url:-$origin_url}"
fi

python3 scripts/check-documentation-authority.py
python3 scripts/check-plan-v3-extension.py
python3 scripts/check-repository-hygiene.py

if [[ "$mode" == '--audit' && -n "$(git status --porcelain)" ]]; then
  echo 'ERROR: audit mode requires a clean worktree and index' >&2
  exit 10
fi

printf 'project_id=trillionnium-game\nrepository=TrillionniumFoundation/TrillionniumGame\nbranch=%s\nmode=%s\nstatus=passed\n' \
  "$branch" "$mode"
