#!/usr/bin/env bash
set -uo pipefail

mode="${1:---dev}"
push_remote="${2:-}"
push_url="${3:-}"
case "$mode" in
  --dev|--audit|--staged|--push) ;;
  *)
    echo "ERROR: unsupported preflight mode: $mode" >&2
    exit 10
    ;;
esac

errors=0
warnings=0

error() {
  printf 'ERROR: %s\n' "$*" >&2
  errors=$((errors + 1))
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
  warnings=$((warnings + 1))
}

root_logical=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "ERROR: not inside a Git repository" >&2
  exit 10
}
root=$(cd "$root_logical" && pwd -P)
invoked_logical=$(pwd -L)
invoked_physical=$(pwd -P)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

policy_file="$root/PROJECT_BOUNDARY.json"
case "$mode" in
  --staged)
    if ! git -C "$root" show :PROJECT_BOUNDARY.json >"$tmpdir/PROJECT_BOUNDARY.json" 2>/dev/null; then
      echo "ERROR: PROJECT_BOUNDARY.json must exist in the index" >&2
      exit 10
    fi
    if ! project_id=$(git -C "$root" show :PROJECT_ID 2>/dev/null | tr -d '\r\n'); then
      echo "ERROR: PROJECT_ID must exist in the index" >&2
      exit 10
    fi
    policy_file="$tmpdir/PROJECT_BOUNDARY.json"
    ;;
  --push)
    if ! git -C "$root" show HEAD:PROJECT_BOUNDARY.json >"$tmpdir/PROJECT_BOUNDARY.json" 2>/dev/null; then
      echo "ERROR: PROJECT_BOUNDARY.json must exist in HEAD" >&2
      exit 10
    fi
    if ! project_id=$(git -C "$root" show HEAD:PROJECT_ID 2>/dev/null | tr -d '\r\n'); then
      echo "ERROR: PROJECT_ID must exist in HEAD" >&2
      exit 10
    fi
    policy_file="$tmpdir/PROJECT_BOUNDARY.json"
    ;;
  *)
    if [[ ! -f "$root/PROJECT_ID" || ! -f "$policy_file" ]]; then
      echo "ERROR: missing PROJECT_ID or PROJECT_BOUNDARY.json" >&2
      exit 10
    fi
    project_id=$(tr -d '\r\n' <"$root/PROJECT_ID")
    ;;
esac

json_value() {
  python3 - "$policy_file" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    value = json.load(fh)
for part in sys.argv[2].split("."):
    if not isinstance(value, dict) or part not in value:
        print("")
        raise SystemExit(0)
    value = value[part]
if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, (str, int, float)):
    print(value)
else:
    print(json.dumps(value, separators=(",", ":")))
PY
}

if ! python3 -m json.tool "$policy_file" >/dev/null 2>&1; then
  echo "ERROR: invalid PROJECT_BOUNDARY.json" >&2
  exit 10
fi

boundary_id=$(json_value project_id)
canonical_dir=$(json_value canonical_dir)
lane=$(json_value lane)
lifecycle=$(json_value lifecycle)
development=$(json_value development)
remote_policy=$(json_value remote.policy)
canonical_slug=$(json_value remote.canonical_slug)
target_slug=$(json_value remote.target_slug)
legacy_slug=$(json_value remote.legacy_slug)
upstream_required=$(json_value remote.upstream_required)
branch_regex=$(json_value branch.development_regex)
deny_regex=$(json_value deny_changed_paths_regex)
baseline_commit=$(json_value baseline_commit)
branch=$(git -C "$root" branch --show-current 2>/dev/null || true)

printf 'project_id=%s\nlane=%s\nphysical_root=%s\ninvoked_path=%s\nbranch=%s\n' \
  "$project_id" "$lane" "$root" "$invoked_logical" "$branch"

[[ "$project_id" == "$boundary_id" ]] || error "PROJECT_ID and PROJECT_BOUNDARY.json disagree"
if [[ "$(basename "$root")" != "$canonical_dir" ]]; then
  common_dir=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
  common_root=""
  [[ "$(basename "$common_dir")" == ".git" ]] && common_root=$(dirname "$common_dir")
  if [[ "$(basename "$common_root")" == "$canonical_dir" ]]; then
    warn "running from an approved linked worktree of $canonical_dir"
  else
    error "physical root is neither $canonical_dir nor one of its linked worktrees"
  fi
fi
if [[ "$invoked_logical" != "$invoked_physical" ]]; then
  warn "invoked through a symlink; use the canonical physical root: $root"
fi
case "$invoked_logical" in
  /home/alex/.openclaw/workspace/*)
    warn "the old OpenClaw workspace path is compatibility-only; reopen from $root"
    ;;
esac

expected_lane=""
case "$project_id" in
  hepta-control-plane) expected_lane="hepta-control-plane" ;;
  trillionnium-world) expected_lane="game-product" ;;
  trillionnium-chain) expected_lane="chain-consensus" ;;
  trillionnium-openra-rts) expected_lane="openra-spike" ;;
  trnm-matrix-approx-2019) expected_lane="archive-reference" ;;
  trillionnium-nakama) expected_lane="nakama-realtime" ;;
  trillionnium-integration) expected_lane="cross-repo-integration" ;;
  *) error "unknown project id: $project_id" ;;
esac
[[ "$lane" == "$expected_lane" ]] || error "unexpected lane: $lane (expected $expected_lane)"

normalize_slug() {
  printf '%s' "$1" | sed -E \
    -e 's#^git@github\.com:#github.com/#' \
    -e 's#^ssh://git@github\.com/#github.com/#' \
    -e 's#^https?://github\.com/#github.com/#' \
    -e 's#\.git$##'
}

origin_url=$(git -C "$root" remote get-url origin 2>/dev/null || true)
origin_slug=$(normalize_slug "$origin_url")
printf 'origin=%s\n' "${origin_url:-(none)}"

if [[ "$upstream_required" == "true" && -z "$origin_url" ]]; then
  error "origin is required by repository policy"
fi
if [[ -n "$canonical_slug" && -n "$origin_url" && "$origin_slug" != "github.com/$canonical_slug" ]]; then
  error "origin does not match canonical repository $canonical_slug"
fi
if [[ "$remote_policy" == "blocked-until-split" && -n "$legacy_slug" && "$origin_slug" == "github.com/$legacy_slug" ]]; then
  warn "fetch still uses the shared legacy World/Chain remote; pushes are blocked until split"
fi
if [[ "$remote_policy" == "bootstrap" && -n "$origin_url" && -z "$target_slug" ]]; then
  warn "bootstrap repository has an unapproved remote"
fi
if [[ "$remote_policy" == "local-only" && -n "$origin_url" ]]; then
  error "local-only repository must not have a remote"
fi

if [[ "$lifecycle" != "archived" && "$mode" != "--audit" ]]; then
  protected=$(python3 - "$policy_file" "$branch" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
print("true" if sys.argv[2] in data.get("branch", {}).get("protected", []) else "false")
PY
)
  if [[ -z "$branch" ]]; then
    error "detached HEAD is not an allowed development branch"
  elif [[ "$protected" == "true" ]]; then
    error "development on protected branch '$branch' is disabled; create a lane-prefixed branch"
  elif [[ -z "$branch_regex" || ! "$branch" =~ $branch_regex ]]; then
    error "branch '$branch' does not match lane policy: $branch_regex"
  fi
fi

case "$project_id" in
  hepta-control-plane)
    [[ "$origin_slug" == "github.com/TrillionniumFoundation/CEX" ]] || error "unexpected CEX origin"
    [[ -f "$root/Cargo.toml" ]] || error "missing CEX Cargo workspace"
    if matches=$(cd "$root" && rg -l --glob 'scripts/**' --glob 'deploy/**' '\.\./Trillionnium/' . 2>/dev/null); then
      warn "operational files still use the World compatibility alias: $(printf '%s' "$matches" | tr '\n' ' ')"
    fi
    ;;
  trillionnium-world)
    [[ -f "$root/trillionnium/Cargo.toml" ]] || error "missing World Cargo workspace"
    rg -q 'lane = "game-product"' "$root/trillionnium/Cargo.toml" || error "World game-product lane marker missing"
    if matches=$(cd "$root" && rg -l --glob 'scripts/**' --glob 'trillionnium/scripts/**' '\.\./CEX/' . 2>/dev/null); then
      warn "operational files still use the CEX compatibility alias: $(printf '%s' "$matches" | tr '\n' ' ')"
    fi
    ;;
  trillionnium-chain)
    [[ -f "$root/trillionnium/Cargo.toml" ]] || error "missing Chain Cargo workspace"
    rg -q '"crates/trnm-consensus-app"' "$root/trillionnium/Cargo.toml" || error "canonical consensus app missing"
    rg -q '"crates/trnm-runtime"' "$root/trillionnium/Cargo.toml" || error "canonical runtime missing"
    ;;
  trillionnium-openra-rts)
    lock="$root/ENGINE_SOURCE_LOCK.json"
    if [[ ! -f "$lock" ]]; then
      error "missing ENGINE_SOURCE_LOCK.json"
    else
      lock_data=$(python3 - "$lock" <<'PY'
import json
import re
import sys
with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
commit = data.get("commit")
patches = data.get("patches", [])
if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
    print("BLOCKED")
elif not isinstance(patches, list) or not patches or not all(isinstance(x, str) and x for x in patches):
    print("INVALID")
else:
    print(commit)
    for patch in patches:
        print(patch)
PY
)
      lock_head=$(printf '%s\n' "$lock_data" | sed -n '1p')
      if [[ "$lock_head" == "BLOCKED" ]]; then
        error "OpenRA engine source commit is not locked; engine development remains blocked"
      elif [[ "$lock_head" == "INVALID" || -z "$lock_head" ]]; then
        error "invalid ENGINE_SOURCE_LOCK.json"
      elif [[ ! -d "$root/engine/.git" ]]; then
        error "locked engine checkout is not a Git repository"
      else
        engine_head=$(git -C "$root/engine" rev-parse HEAD 2>/dev/null || true)
        [[ "$engine_head" == "$lock_head" ]] || error "engine checkout does not match locked commit"
        git -C "$root/engine" diff --quiet --ignore-submodules -- 2>/dev/null || error "engine checkout is dirty; patch replay must use a clean tree"
        while IFS= read -r patch_rel; do
          [[ -f "$root/$patch_rel" ]] || {
            error "locked patch is missing: $patch_rel"
            continue
          }
          git -C "$root/engine" apply --check "$root/$patch_rel" >/dev/null 2>&1 || error "patch is not replayable on locked engine: $patch_rel"
        done < <(printf '%s\n' "$lock_data" | tail -n +2)
      fi
    fi
    ;;
  trnm-matrix-approx-2019)
    if [[ -n "$origin_url" && "$origin_slug" != "github.com/ZhengJianwei2/TRNM" ]]; then
      error "unexpected archive origin"
    fi
    if [[ "$mode" != "--audit" ]]; then
      error "archive/reference repository: development and push are disabled"
    fi
    if [[ -z "$baseline_commit" ]] || ! git -C "$root" cat-file -e "$baseline_commit^{commit}" 2>/dev/null; then
      error "archive baseline commit is missing"
    elif ! git -C "$root" merge-base --is-ancestor "$baseline_commit" HEAD 2>/dev/null; then
      error "archive HEAD is not descended from the frozen baseline"
    else
      while IFS= read -r changed; do
        [[ -n "$changed" ]] || continue
        if [[ ! "$changed" =~ ^(.githooks/(pre-commit|pre-push)|AGENTS.md|PROJECT_BOUNDARY.json|PROJECT_BOUNDARY.md|PROJECT_ID|scripts/project-preflight.sh)$ ]]; then
          error "archive changed after baseline outside governance files: $changed"
        fi
      done < <(git -C "$root" diff --name-only --no-renames "$baseline_commit"..HEAD)
    fi
    if [[ "$mode" == "--audit" && -n "$(git -C "$root" status --porcelain)" ]]; then
      error "archive audit requires a clean worktree and index"
    fi
    ;;
  trillionnium-nakama)
    [[ -f "$root/docs/BOUNDARIES.md" ]] || error "Nakama boundary document missing"
    ;;
  trillionnium-integration)
    lock_source="$root/components.lock.json"
    if [[ "$mode" == "--staged" ]]; then
      if git -C "$root" show :components.lock.json >"$tmpdir/components.lock.json" 2>/dev/null; then
        lock_source="$tmpdir/components.lock.json"
      else
        error "components.lock.json must exist in the index"
      fi
    elif [[ "$mode" == "--push" ]]; then
      if git -C "$root" show HEAD:components.lock.json >"$tmpdir/components.lock.json" 2>/dev/null; then
        lock_source="$tmpdir/components.lock.json"
      else
        error "components.lock.json must exist in HEAD"
      fi
    fi
    if [[ ! -f "$lock_source" ]]; then
      error "integration component lock missing"
    else
      lock_check=$(python3 - "$lock_source" <<'PY'
import json
import re
import sys
with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
issues = []
if data.get("schema") != 1:
    issues.append("schema must be 1")
if data.get("legacy_chain_allowed") is not False:
    issues.append("legacy_chain_allowed must be explicitly false")
if data.get("canonical_chain_path") != ["CometBFT", "trnm-consensus-app", "trnm-runtime", "AppHash"]:
    issues.append("canonical_chain_path is not canonical")
if data.get("status") not in {"blocked", "ready"}:
    issues.append("status must be blocked or ready")
if data.get("status") == "blocked" and data.get("runnable") is not False:
    issues.append("blocked lock must set runnable=false")
components = data.get("components")
if not isinstance(components, list) or not components:
    issues.append("components must be a non-empty list")
else:
    for component in components:
        if not isinstance(component, dict):
            issues.append("component entry must be an object")
            continue
        repo = component.get("repository")
        if isinstance(repo, str) and repo.startswith("/"):
            issues.append(f"{component.get('project_id')}: absolute sibling repository path is forbidden")
        revision = component.get("revision")
        if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", str(revision)):
            issues.append(f"{component.get('project_id')}: revision must be an immutable commit")
if issues:
    print("; ".join(issues))
    raise SystemExit(1)
print(data.get("status"))
PY
)
      lock_rc=$?
      if [[ $lock_rc -ne 0 ]]; then
        error "invalid components.lock.json: $lock_check"
      elif [[ "$lock_check" == "blocked" ]]; then
        warn "integration lock is a blocked audit snapshot, not a runnable release gate"
      fi
    fi
    legacy_output=$(cd "$root" && rg -l --hidden \
      --glob '!.git/**' --glob '!docs/archive/**' --glob '!scripts/project-preflight.sh' \
      'trnm-chain-(node|validator|cli)' . 2>/dev/null)
    legacy_rc=$?
    if [[ $legacy_rc -eq 0 ]]; then
      error "active integration surface references a forbidden legacy harness: $(printf '%s' "$legacy_output" | tr '\n' ' ')"
    elif [[ $legacy_rc -ne 1 ]]; then
      error "failed to scan integration tree for legacy harness references"
    fi
    ;;
esac

old_path_output=$(cd "$root" && rg -l --hidden --glob '!.git/**' '/home/qian/\.openclaw/workspace' . 2>/dev/null)
old_path_rc=$?
if [[ $old_path_rc -eq 0 ]]; then
  warn "tracked or active files still contain old /home/qian workspace paths: $(printf '%s' "$old_path_output" | tr '\n' ' ')"
elif [[ $old_path_rc -ne 1 ]]; then
  warn "could not complete old-path scan"
fi

while IFS= read -r finding; do
  [[ -n "$finding" ]] && error "$finding"
done < <(python3 - "$root" "$mode" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import tomllib

root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]

def run(*args, binary=False):
    return subprocess.check_output(args, cwd=root, text=not binary)

def listed_manifests():
    if mode == "--push":
        raw = run("git", "ls-tree", "-r", "--name-only", "-z", "HEAD", binary=True)
    elif mode == "--staged":
        raw = run(
            "git", "ls-files", "-c", "-z", "--",
            "Cargo.toml", ":(glob)**/Cargo.toml", binary=True
        )
    else:
        raw = run(
            "git", "ls-files", "-co", "--exclude-standard", "-z", "--",
            "Cargo.toml", ":(glob)**/Cargo.toml", binary=True
        )
    return sorted({
        p.decode("utf-8", "surrogateescape")
        for p in raw.split(b"\0") if p and p.endswith(b"Cargo.toml")
    })

def manifest_bytes(rel):
    if mode == "--staged":
        return run("git", "show", f":{rel}", binary=True)
    if mode == "--push":
        return run("git", "show", f"HEAD:{rel}", binary=True)
    return (root / rel).read_bytes()

def dependency_tables(data):
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        table = data.get(section)
        if isinstance(table, dict):
            yield section, table
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        table = workspace.get("dependencies")
        if isinstance(table, dict):
            yield "workspace.dependencies", table
    target = data.get("target")
    if isinstance(target, dict):
        for target_name, target_data in target.items():
            if not isinstance(target_data, dict):
                continue
            for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                table = target_data.get(section)
                if isinstance(table, dict):
                    yield f"target.{target_name}.{section}", table
    patch = data.get("patch")
    if isinstance(patch, dict):
        for source, table in patch.items():
            if isinstance(table, dict):
                yield f"patch.{source}", table
    replace = data.get("replace")
    if isinstance(replace, dict):
        yield "replace", replace

for rel in listed_manifests():
    try:
        data = tomllib.loads(manifest_bytes(rel).decode("utf-8-sig"))
    except subprocess.CalledProcessError:
        continue
    except Exception as exc:
        print(f"cannot parse Cargo manifest {rel}: {exc}")
        continue
    for section, table in dependency_tables(data):
        for package, spec in table.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
                continue
            raw_path = Path(spec["path"])
            target = raw_path.resolve() if raw_path.is_absolute() else (root / rel).parent.joinpath(raw_path).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                print(f"Cargo path dependency leaves this Git root: {rel} [{section}.{package}] -> {target}")
PY
)

if [[ "$mode" == "--staged" && "${ALLOW_BOUNDARY_MIGRATION:-0}" != "1" && -n "$deny_regex" ]]; then
  while IFS= read -r -d '' staged_path; do
    if [[ "$staged_path" =~ $deny_regex ]]; then
      error "staged path crosses this project's ownership boundary: $staged_path"
    fi
  done < <(git -C "$root" diff --cached --name-only --no-renames -z)
fi

topic_path=$(git -C "$root" rev-parse --path-format=absolute --git-path PROJECT_TOPIC 2>/dev/null || true)
changed_tmp="$tmpdir/changed"
{
  git -C "$root" diff --name-only --no-renames -z
  git -C "$root" diff --cached --name-only --no-renames -z
  git -C "$root" ls-files --others --exclude-standard -z
} | sort -zu >"$changed_tmp"

if [[ -s "$changed_tmp" && "$lifecycle" != "archived" ]]; then
  if [[ ! -f "$topic_path" ]]; then
    error "dirty worktree has no Git-local PROJECT_TOPIC"
  else
    topic_project=$(sed -n 's/^project_id=//p' "$topic_path" | head -1)
    topic_name=$(sed -n 's/^topic=//p' "$topic_path" | head -1)
    topic_base=$(sed -n 's/^base_head=//p' "$topic_path" | head -1)
    [[ "$topic_project" == "$project_id" ]] || error "PROJECT_TOPIC belongs to a different project"
    [[ -n "$topic_name" ]] || error "PROJECT_TOPIC has no topic"
    if [[ -z "$topic_base" ]] || ! git -C "$root" cat-file -e "$topic_base^{commit}" 2>/dev/null; then
      error "PROJECT_TOPIC base_head is missing or invalid"
    elif ! git -C "$root" merge-base --is-ancestor "$topic_base" HEAD 2>/dev/null; then
      error "PROJECT_TOPIC base_head is not an ancestor of HEAD"
    fi

    mapfile -t allowed_files < <(sed -n 's/^allowed_file=//p' "$topic_path")
    mapfile -t allowed_dirs < <(sed -n 's/^allowed_dir=//p' "$topic_path")
    mapfile -t allowed_globs < <(sed -n 's/^allowed_glob=//p' "$topic_path")
    valid_rules=0
    for rule in "${allowed_files[@]}" "${allowed_dirs[@]}" "${allowed_globs[@]}"; do
      if [[ -z "$rule" || "$rule" == /* || "$rule" == ".." || "$rule" == ../* || "$rule" == */../* || "$rule" == */.. ]]; then
        error "PROJECT_TOPIC contains an unsafe or empty path rule"
      else
        valid_rules=$((valid_rules + 1))
      fi
    done
    for dir in "${allowed_dirs[@]}"; do
      [[ -z "$dir" || "$dir" == */ ]] || error "allowed_dir must end with '/': $dir"
    done
    [[ $valid_rules -gt 0 ]] || error "PROJECT_TOPIC has no valid allowed_file/allowed_dir/allowed_glob rules"

    while IFS= read -r -d '' changed; do
      allowed=0
      for file in "${allowed_files[@]}"; do
        [[ -n "$file" && "$changed" == "$file" ]] && allowed=1
      done
      for dir in "${allowed_dirs[@]}"; do
        [[ -n "$dir" && "$dir" == */ && "$changed" == "$dir"* ]] && allowed=1
      done
      for glob in "${allowed_globs[@]}"; do
        [[ -n "$glob" && "$changed" == $glob ]] && allowed=1
      done
      [[ $allowed -eq 1 ]] || error "dirty path is outside PROJECT_TOPIC: $changed"
    done <"$changed_tmp"
  fi
elif [[ ! -s "$changed_tmp" && -f "$topic_path" ]]; then
  warn "worktree is clean but PROJECT_TOPIC still exists"
fi

if [[ "$mode" == "--push" ]]; then
  actual_push_url="$push_url"
  [[ -n "$actual_push_url" ]] || actual_push_url=$(git -C "$root" remote get-url --push "${push_remote:-origin}" 2>/dev/null || true)
  actual_push_slug=$(normalize_slug "$actual_push_url")
  case "$remote_policy" in
    required)
      if [[ -z "$canonical_slug" || "$actual_push_slug" != "github.com/$canonical_slug" ]]; then
        error "actual push URL is not the approved canonical repository: $actual_push_url"
      fi
      ;;
    blocked-until-split)
      error "push is blocked until this project has its own approved remote"
      ;;
    local-only)
      error "push is disabled for this local-only experiment"
      ;;
    read-only-upstream)
      error "push is disabled for this archived repository"
      ;;
    bootstrap)
      if [[ -z "$target_slug" ]]; then
        error "push is disabled until a bootstrap target_slug is approved"
      elif [[ "$actual_push_slug" != "github.com/$target_slug" ]]; then
        error "actual push URL is not the approved bootstrap repository"
      fi
      ;;
    *)
      error "unknown remote policy: $remote_policy"
      ;;
  esac
fi

printf 'warnings=%d errors=%d\n' "$warnings" "$errors"
(( errors == 0 ))
