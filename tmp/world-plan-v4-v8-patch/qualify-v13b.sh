#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
export_root="${3:-$PWD/export}"

bash "$control/tmp/world-plan-v4-v8-patch/qualify-v13.sh" \
  "$world" "$control" "$export_root"

# Intent-to-add does not stage source content; it makes the unified diff include
# every newly created ownership part so the exported patch is complete.
git -C "$world" add -N .
git -C "$world" diff --binary > "$export_root/world-v13-source.patch"
test "$(grep -c '^diff --git ' "$export_root/world-v13-source.patch")" -ge 60
find "$export_root" -type f -print0 | sort -z | xargs -0 sha256sum > "$export_root/SHA256SUMS"
printf 'world_v13_complete_patch_sha256=%s\n' \
  "$(sha256sum "$export_root/world-v13-source.patch" | awk '{print $1}')"
printf 'TRNM_WORLD_V13B_COMPLETE_EXPORT=PASS\n'
