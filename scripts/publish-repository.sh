#!/usr/bin/env bash
set -euo pipefail

owner="${TRILLIONNIUM_GITHUB_OWNER:-TrillionniumFoundation}"
name="${TRILLIONNIUM_GITHUB_REPOSITORY:-TrillionniumGame}"
visibility="${TRILLIONNIUM_GITHUB_VISIBILITY:-private}"
repo="${owner}/${name}"

case "$visibility" in
  private|public|internal) ;;
  *) echo "TRILLIONNIUM_GITHUB_VISIBILITY must be private, public, or internal" >&2; exit 64 ;;
esac

command -v gh >/dev/null 2>&1 || {
  echo "GitHub CLI (gh) is required" >&2
  exit 69
}

gh auth status >/dev/null
root="$(git rev-parse --show-toplevel)"
cd "$root"

python3 scripts/check-plan.py
git diff --quiet
git diff --cached --quiet

if gh repo view "$repo" >/dev/null 2>&1; then
  echo "Repository already exists: $repo" >&2
  exit 73
fi

gh repo create "$repo" \
  "--$visibility" \
  --description "Full Rust reimplementation of the Nakama OSS game backend" \
  --source . \
  --remote origin \
  --push

printf 'Published %s at commit %s\n' "$repo" "$(git rev-parse HEAD)"
