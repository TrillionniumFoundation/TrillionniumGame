#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
env_file=""
evidence_file=""
writers_fenced=false
compose_files=()

usage() {
  cat >&2 <<'EOF'
usage: check-nakama-research-control-activation.sh \
  --env-file FILE --evidence-file FILE --writers-fenced \
  [--compose-file FILE ...]

The preflight requires every Nakama writer to be externally fenced and proves
that the canonical Compose Nakama service is stopped. It then scans the shared
research-control collection in one repeatable-read, read-only transaction.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --env-file)
      (( $# >= 2 )) || { usage; exit 64; }
      env_file=$2
      shift 2
      ;;
    --evidence-file)
      (( $# >= 2 )) || { usage; exit 64; }
      evidence_file=$2
      shift 2
      ;;
    --compose-file)
      (( $# >= 2 )) || { usage; exit 64; }
      compose_files+=("$2")
      shift 2
      ;;
    --writers-fenced)
      writers_fenced=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [[ -z "$env_file" || -z "$evidence_file" || "$writers_fenced" != true ]]; then
  usage
  exit 64
fi
if [[ ! -f "$env_file" || -L "$env_file" ]]; then
  echo "ERROR: Compose env input must be a regular non-symlink file" >&2
  exit 64
fi
if ! python3 - "$env_file" <<'PY'
import os
import stat
import sys

info = os.lstat(sys.argv[1])
if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077:
    raise SystemExit(1)
PY
then
  echo "ERROR: Compose env input must be owner/root-owned and inaccessible to group/other" >&2
  exit 64
fi
bash "$root/scripts/lint-nakama-compose-env.sh" "$env_file" >/dev/null

if (( ${#compose_files[@]} == 0 )); then
  compose_files=("$root/compose.yaml")
fi
for compose_file in "${compose_files[@]}"; do
  if [[ ! -f "$compose_file" || -L "$compose_file" ]]; then
    echo "ERROR: Compose model must be a regular non-symlink file: $compose_file" >&2
    exit 64
  fi
done

evidence_parent=$(dirname "$evidence_file")
if [[ "$evidence_file" != /* || -e "$evidence_file" || -L "$evidence_file" ]]; then
  echo "ERROR: evidence output must be an absolute, pre-existing-free path" >&2
  exit 64
fi
if [[ ! -d "$evidence_parent" || -L "$evidence_parent" ]] ||
  [[ $(stat -c '%u' "$evidence_parent") != "$(id -u)" ]] ||
  (( (8#$(stat -c '%a' "$evidence_parent") & 8#022) != 0 )); then
  echo "ERROR: evidence parent must be caller-owned and not group/world writable" >&2
  exit 64
fi

env_metadata=$(python3 - "$env_file" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

names = {
    "TRNM_NAKAMA_AUTHORITY_KEY_ID",
    "TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS",
}
values = {}

def dotenv_value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'"):
            raise ValueError("unterminated single-quoted dotenv value")
        return raw[1:-1]
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid double-quoted dotenv value") from exc
        if not isinstance(value, str):
            raise ValueError("double-quoted dotenv value is not a string")
        return value
    # Compose treats a whitespace-prefixed # as an inline comment for an
    # unquoted value. Canonical JSON emitted by this project is compact, so no
    # meaningful registry byte is removed by this rule.
    raw = re.split(r"\s+#", raw, maxsplit=1)[0].rstrip()
    return raw

for raw_line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    stripped = raw_line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    name, raw_value = stripped.split("=", 1)
    name = name.strip()
    if name not in names:
        continue
    if name in values:
        raise SystemExit(f"{name} must occur exactly once")
    try:
        values[name] = dotenv_value(raw_value)
    except ValueError as exc:
        raise SystemExit(f"{name}: {exc}") from exc
if set(values) != names:
    raise SystemExit("authority key id and public registry must each occur exactly once")

active = values["TRNM_NAKAMA_AUTHORITY_KEY_ID"]
if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", active):
    raise SystemExit("TRNM_NAKAMA_AUTHORITY_KEY_ID is invalid")
try:
    registry = json.loads(values["TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS"])
except json.JSONDecodeError as exc:
    raise SystemExit("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS is not valid JSON") from exc
if not isinstance(registry, dict) or not registry:
    raise SystemExit("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS must be a non-empty JSON object")
if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key) or
       not isinstance(value, str) or not value for key, value in registry.items()):
    raise SystemExit("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS contains an invalid entry")
if active not in registry:
    raise SystemExit("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS does not contain the active authority id")
canonical = json.dumps(registry, sort_keys=True, separators=(",", ":")).encode("utf-8")
print(active)
print(",".join(sorted(registry)))
print(hashlib.sha256(canonical).hexdigest())
PY
)
mapfile -t env_metadata_lines <<<"$env_metadata"
if (( ${#env_metadata_lines[@]} != 3 )); then
	echo "ERROR: authority metadata parser returned an invalid result" >&2
	exit 65
fi
active_authority_key_id=${env_metadata_lines[0]}
authority_key_ids=${env_metadata_lines[1]}
authority_public_registry_sha256=${env_metadata_lines[2]}

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "ERROR: Docker access is required to inspect the fenced canonical stack" >&2
  exit 1
fi

compose=("${docker_cmd[@]}" compose --env-file "$env_file")
for compose_file in "${compose_files[@]}"; do
  compose+=(-f "$compose_file")
done
"${compose[@]}" config --quiet

if ! nakama_ids_output=$("${compose[@]}" ps --all -q nakama); then
	echo "ERROR: failed to enumerate canonical Nakama service containers" >&2
	exit 66
fi
mapfile -t nakama_ids <<<"$nakama_ids_output"
for nakama_id in "${nakama_ids[@]}"; do
	[[ -n "$nakama_id" ]] || continue
	nakama_running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$nakama_id")
	if [[ "$nakama_running" == true ]]; then
		echo "ERROR: Nakama is still running; fence every writer before the activation scan" >&2
		exit 66
	fi
done
if ! postgres_ids_output=$("${compose[@]}" ps --all -q postgres); then
	echo "ERROR: failed to enumerate canonical PostgreSQL service containers" >&2
	exit 66
fi
mapfile -t postgres_ids <<<"$postgres_ids_output"
postgres_running_count=0
for postgres_id in "${postgres_ids[@]}"; do
	[[ -n "$postgres_id" ]] || continue
	postgres_running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$postgres_id")
	if [[ "$postgres_running" == true ]]; then
		((postgres_running_count += 1))
	fi
done
if (( postgres_running_count != 1 )); then
	echo "ERROR: exactly one canonical PostgreSQL service must be running for the activation scan" >&2
	exit 66
fi

scratch=$(mktemp -d)
chmod 700 "$scratch"
raw="$scratch/activation-preflight.tsv"
evidence_tmp=$(mktemp "$evidence_parent/.nakama-control-activation.XXXXXX")
chmod 600 "$evidence_tmp"
cleanup() {
  status=$?
  trap - EXIT INT TERM
  rm -rf "$scratch"
  if [[ -f "$evidence_tmp" ]]; then
    rm -f "$evidence_tmp"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

"${compose[@]}" exec -T postgres \
  psql -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -U postgres -d trillionnium_nakama \
  -v "authority_key_ids=$authority_key_ids" -v "active_authority_key_id=$active_authority_key_id" >"$raw" <<'SQL'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT 'meta', COALESCE(to_regclass('public.storage')::text, ''),
       COALESCE((
         SELECT data_type
         FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'storage' AND column_name = 'value'
       ), ''),
       current_database(),
       (SELECT system_identifier::text FROM pg_control_system()),
       current_setting('server_version_num'),
       txid_current_snapshot()::text;

SELECT 'record', '', user_id::text, key, version,
       encode(convert_to(value::text, 'UTF8'), 'hex')
FROM public.storage
WHERE collection = 'trnm_research_control_v2'
ORDER BY user_id::text COLLATE "C", key COLLATE "C";

WITH controls AS (
  SELECT user_id, key, version, value
  FROM public.storage
  WHERE collection = 'trnm_research_control_v2'
), classified AS (
  SELECT
    CASE
      WHEN user_id IS DISTINCT FROM '00000000-0000-0000-0000-000000000000'::uuid THEN 'non_system_owner'
      WHEN jsonb_typeof(value) IS DISTINCT FROM 'object' THEN 'non_object_value'
      WHEN value->>'schema' IS NULL OR value->>'schema' NOT IN (
        'trnm.nakama.stored-research-control-command.v2',
        'trnm.nakama.stored-research-control-command.v3'
      ) THEN 'unsupported_schema'
      WHEN value->>'status' IS NULL OR value->>'status' NOT IN ('pending', 'applied') THEN 'invalid_status'
      WHEN value->>'command_id' IS NULL OR key IS DISTINCT FROM value->>'command_id' THEN 'storage_identity_mismatch'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v2' AND
           value->>'status' = 'pending' THEN 'legacy_v2_pending'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v3' AND (
        value->>'expected_response_authority_key_id' IS NULL OR
        value->>'expected_response_authority_key_id' !~ '^[A-Za-z0-9._:-]{1,128}$'
      ) THEN 'invalid_expected_authority'
      WHEN value->>'status' = 'pending' AND (
        value ? 'response_body_base64' OR value ? 'response_sha256' OR
        value ? 'response_authority_key_id' OR value ? 'response_signature_base64' OR
        value ? 'applied_at_unix'
      ) THEN 'pending_carries_response'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v3' AND
           value->>'status' = 'pending' AND
           value->>'expected_response_authority_key_id' IS DISTINCT FROM
             :'active_authority_key_id' THEN 'pending_inactive_authority'
      WHEN value->>'status' = 'applied' AND (
        COALESCE(value->>'response_body_base64', '') = '' OR
        COALESCE(value->>'response_sha256', '') = '' OR
        COALESCE(value->>'response_authority_key_id', '') = '' OR
        COALESCE(value->>'response_signature_base64', '') = '' OR
        NOT (value ? 'applied_at_unix')
      ) THEN 'invalid_applied_response'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v3' AND
           value->>'status' = 'applied' AND
           value->>'response_authority_key_id' IS DISTINCT FROM
             value->>'expected_response_authority_key_id' THEN 'applied_authority_mismatch'
      WHEN value->>'status' = 'applied' AND NOT (
        value->>'response_authority_key_id' = ANY(string_to_array(:'authority_key_ids', ','))
      ) THEN 'missing_public_verification_key'
      ELSE NULL
    END AS reason,
    user_id::text AS owner_id,
    key,
    version,
    encode(convert_to(value::text, 'UTF8'), 'hex') AS value_hex
  FROM controls
)
SELECT 'blocker', reason, owner_id, key, version, value_hex
FROM classified
WHERE reason IS NOT NULL
ORDER BY reason COLLATE "C", owner_id COLLATE "C", key COLLATE "C";

COMMIT;
SQL

if ! nakama_after_scan_ids_output=$("${compose[@]}" ps --all -q nakama); then
	echo "ERROR: failed to re-enumerate canonical Nakama service containers" >&2
	exit 66
fi
mapfile -t nakama_after_scan_ids <<<"$nakama_after_scan_ids_output"
for nakama_after_scan_id in "${nakama_after_scan_ids[@]}"; do
	[[ -n "$nakama_after_scan_id" ]] || continue
	nakama_after_scan_running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$nakama_after_scan_id")
	if [[ "$nakama_after_scan_running" == true ]]; then
		echo "ERROR: Nakama restarted during the activation scan; discard the result and re-fence every writer" >&2
		exit 66
	fi
done

activation_script_sha256=$(sha256sum "$root/scripts/check-nakama-research-control-activation.sh" | awk '{print $1}')
python3 - "$raw" "$evidence_tmp" "$active_authority_key_id" \
  "$authority_public_registry_sha256" "$activation_script_sha256" <<'PY'
import hashlib
import json
import pathlib
import sys

raw_path, output_path, active_key_id, registry_sha256, script_sha256 = sys.argv[1:]
raw = pathlib.Path(raw_path).read_bytes()
lines = raw.decode("utf-8").splitlines()
if not lines:
    raise SystemExit("activation preflight returned no storage metadata")
meta = lines[0].split("\t")
if len(meta) != 7 or meta[:3] != ["meta", "storage", "jsonb"]:
    raise SystemExit(f"unsupported Nakama storage schema: {meta!r}")
blockers = []
records = []
for line in lines[1:]:
    fields = line.split("\t")
    if len(fields) != 6 or fields[0] not in {"record", "blocker"}:
        raise SystemExit("activation preflight returned a malformed blocker row")
    value_bytes = bytes.fromhex(fields[5])
    json.loads(value_bytes)
    identity = {
        "owner_id": fields[2],
        "key": fields[3],
        "version": fields[4],
        "value_sha256": hashlib.sha256(value_bytes).hexdigest(),
    }
    if fields[0] == "record":
        if fields[1] != "":
            raise SystemExit("activation record row contains an unexpected reason")
        records.append(identity)
    else:
        blockers.append({"reason": fields[1], **identity})
if len(records) > 100_000:
    raise SystemExit("activation preflight exceeds its bounded row limit")
record_manifest = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
evidence = {
    "schema": "trnm.nakama.research-control-activation-preflight.v1",
    "external_writers_fenced_operator_assertion": True,
    "canonical_nakama_service_stopped_observed": True,
    "writer_fence_scope": "external-operator-assertion-plus-before-and-after-compose-observation",
    "transaction": "repeatable-read-read-only",
    "storage_table": "public.storage",
    "storage_value_type": "jsonb",
    "database_name": meta[3],
    "database_system_identifier": meta[4],
    "postgres_server_version_num": meta[5],
    "transaction_snapshot": meta[6],
    "active_authority_key_id": active_key_id,
    "authority_public_registry_sha256": registry_sha256,
    "activation_script_sha256": script_sha256,
    "scanned_control_row_count": len(records),
    "scanned_control_manifest_sha256": hashlib.sha256(record_manifest).hexdigest(),
    "structural_preflight_passed": not blockers,
    "candidate_startup_readiness_required": True,
    "candidate_startup_readiness_verified": False,
    "blocker_count": len(blockers),
    "blockers": blockers,
    "query_output_sha256": hashlib.sha256(raw).hexdigest(),
}
pathlib.Path(output_path).write_text(
    json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

mv -T "$evidence_tmp" "$evidence_file"
safe=$(python3 - "$evidence_file" <<'PY'
import json
import sys
print("true" if json.load(open(sys.argv[1], encoding="utf-8"))["structural_preflight_passed"] else "false")
PY
)
if [[ "$safe" != true ]]; then
  blocker_count=$(python3 - "$evidence_file" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["blocker_count"])
PY
  )
  echo "ERROR: research-control v3 activation has $blocker_count blocker(s) (evidence: $evidence_file)" >&2
  exit 3
fi

echo "Nakama research-control structural preflight: pass=true; candidate startup/readiness full validation remains required evidence=$evidence_file"
