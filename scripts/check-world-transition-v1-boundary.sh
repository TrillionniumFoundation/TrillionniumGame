#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

fail() {
  printf 'TRNM Nakama World transition boundary failed: %s\n' "$*" >&2
  exit 1
}

required=(
  "contracts/world-transition-v1-consumer-lock.json"
  "contracts/world-transition-v1-adapter-status.json"
  "contracts/world-transition-v1.schema.json"
  "testdata/world-transition-v1/golden-vectors.json"
  "runtime/world_transition_v1/__init__.py"
  "runtime/world_transition_v1/__main__.py"
  "runtime/world_transition_v1/canonical.py"
  "runtime/world_transition_v1/contracts.py"
  "runtime/world_transition_v1/adapter.py"
  "runtime/world_transition_v1/shadow.py"
  "tools/emit_world_transition_v1_shadow_fixture.py"
  "tests/test_world_transition_v1.py"
  "docs/WORLD_TRANSITION_V1_ADAPTER.md"
  "docs/WORLD_TRANSITION_V1_SHADOW_RUNBOOK.md"
)
for path in "${required[@]}"; do
  [[ -f "$ROOT_DIR/$path" ]] || fail "missing required artifact: $path"
done

python3 -S - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
lock_path = root / "contracts/world-transition-v1-consumer-lock.json"
status_path = root / "contracts/world-transition-v1-adapter-status.json"
schema_path = root / "contracts/world-transition-v1.schema.json"
vectors_path = root / "testdata/world-transition-v1/golden-vectors.json"

lock = json.loads(lock_path.read_text(encoding="utf-8"))
status = json.loads(status_path.read_text(encoding="utf-8"))
if status.get("contract_version") != "trnm_nakama_world_transition_adapter_delivery_v1":
    raise SystemExit("adapter delivery contract drift")
if status.get("backlog_id") != "WORLD-P0-003":
    raise SystemExit("adapter backlog binding drift")
if status.get("status") not in {
    "implemented_pending_exact_head_ci",
    "implemented_python_and_go_pending_exact_head_ci",
    "verified_remote",
}:
    raise SystemExit("adapter delivery status is invalid")
if status.get("authority", {}).get("public_online_enabled") is not False:
    raise SystemExit("adapter delivery cannot enable public online")
# A7 is a first-party Go package already present in this repository and may be
# represented as implemented_pending_ci. A8 and A9 still require production
# store/callback integration and cross-repository evidence, so they must remain
# pending or blocked here.
external_rows = {
    "WORLD-P0-003-A8",
    "WORLD-P0-003-A9",
}
for row in status.get("acceptance", []):
    if row.get("id") in external_rows and row.get("state") not in {"pending", "blocked"}:
        raise SystemExit(f"external acceptance row overclaimed: {row.get('id')}")
if lock.get("contract_version") != "trnm_nakama_world_transition_consumer_lock_v1":
    raise SystemExit("consumer lock contract drift")
if lock.get("status") != "shadow_candidate" or lock.get("activation") != "shadow_only":
    raise SystemExit("consumer lock may be shadow_candidate/shadow_only only")
if lock.get("cross_repository_credit") is not False:
    raise SystemExit("Nakama source cannot grant cross-repository credit")

world = lock.get("world", {})
if world.get("repository") != "TrillionniumFoundation/Trillionnium-World":
    raise SystemExit("unexpected World repository")
if world.get("commit") != "0d7666d4d830fa8e56c78b23d438856064182535":
    raise SystemExit("World transition commit drift")
if world.get("tree") != "1619ae76fa62a5e67bc7ff94429c62eea35deb87":
    raise SystemExit("World transition tree drift")
if world.get("pull_request") != 21:
    raise SystemExit("World transition PR binding drift")
if world.get("contract_version") != "trnm_world_transition_v1":
    raise SystemExit("World contract version drift")

authority = lock.get("authority", {})
required_false = (
    "world_result_can_mutate_authority_context",
    "completion_signing_performed",
    "canonical_archive_root_produced",
    "chain_finality_claimed",
    "cex_settlement_performed",
    "public_online_enabled",
)
for key in required_false:
    if authority.get(key) is not False:
        raise SystemExit(f"authority control must remain false: {key}")
for key in (
    "nakama_context_required_before_request",
    "nakama_global_sequence_remains_external_to_world",
    "nakama_idempotency_remains_external_to_world",
    "world_transition_id_is_opaque_correlation_only",
):
    if authority.get(key) is not True:
        raise SystemExit(f"authority control must remain true: {key}")


def git_blob_sha(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


for label, local_path in (
    ("schema", schema_path),
    ("vectors", vectors_path),
):
    artifact = world["artifacts"][label]
    payload = local_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise SystemExit(f"vendored {label} sha256 drift")
    if git_blob_sha(payload) != artifact["git_blob_sha1"]:
        raise SystemExit(f"vendored {label} is not byte-exact World blob")
    expected_path = pathlib.Path(artifact["vendored_path"])
    if root / expected_path != local_path:
        raise SystemExit(f"vendored {label} path drift")

schema = json.loads(schema_path.read_text(encoding="utf-8"))
if schema.get("$defs", {}).get("request", {}).get("additionalProperties") is not False:
    raise SystemExit("World request schema must reject unknown fields")
if schema.get("$defs", {}).get("accepted", {}).get("additionalProperties") is not False:
    raise SystemExit("World accepted schema must reject unknown fields")
if schema.get("$defs", {}).get("rejected", {}).get("additionalProperties") is not False:
    raise SystemExit("World rejected schema must reject unknown fields")
if schema.get("$defs", {}).get("request", {}).get("properties", {}).get("contract_version", {}).get("const") != "trnm_world_transition_v1":
    raise SystemExit("World request contract drift")

vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
if vectors.get("schema_contract_version") != "trnm_world_transition_v1":
    raise SystemExit("World vector contract drift")
stable = set(vectors.get("stable_error_codes", []))
if len(stable) != 12 or "internal_unavailable" not in stable:
    raise SystemExit("World stable error catalogue drift")
for required in (
    "nakama_session_token",
    "nakama_private_key",
    "match_completed_v1",
    "global_event_cursor",
):
    if required not in set(vectors.get("forbidden_authority_keys", [])):
        raise SystemExit(f"World forbidden key missing: {required}")

runtime_root = root / "runtime/world_transition_v1"
forbidden_import_roots = {
    "asyncpg",
    "http",
    "psycopg",
    "requests",
    "socket",
    "sqlite3",
    "subprocess",
    "urllib",
}
for path in runtime_root.glob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in forbidden_import_roots:
                    raise SystemExit(f"{path.name}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".", 1)[0] in forbidden_import_roots:
                raise SystemExit(f"{path.name}: forbidden import {node.module}")
        elif isinstance(node, ast.Attribute) and node.attr in {
            "sign_completion",
            "load_private_key",
            "publish_finality",
            "settle_wallet",
        }:
            raise SystemExit(f"{path.name}: forbidden authority operation {node.attr}")

adapter_source = (runtime_root / "adapter.py").read_text(encoding="utf-8")
for marker in (
    "def prepare_world_transition(",
    "def prepared_from_canonical_request(",
    "def verify_world_result(",
):
    if marker not in adapter_source:
        raise SystemExit(f"adapter function missing: {marker}")
shadow_source = (runtime_root / "shadow.py").read_text(encoding="utf-8")
for marker in (
    '"cutover_authorized": False',
    '"public_online_enabled": False',
    '"canonical_completion_signing_performed": False',
):
    if marker not in shadow_source:
        raise SystemExit(f"shadow fail-closed marker missing: {marker}")
PY

PYTHONPATH="$ROOT_DIR" python3 -S - "$ROOT_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
from runtime.world_transition_v1 import (
    NakamaAuthorityContext,
    prepare_world_transition,
)

context = NakamaAuthorityContext(
    match_id="boundary-match",
    authorization_id="boundary-authorization",
    participant_roster_hash="3" * 64,
    match_version=1,
    global_event_sequence=77,
    command_idempotency_key="boundary-idempotency",
    ruleset_revision="trnm-rts-rules-v1",
    content_revision="first-contact-content-v1",
    expected_tick=4,
)
prepared = prepare_world_transition(
    context,
    previous_state_schema_id="trnm.rts.state.v1",
    previous_state={"tick": 4},
    command_schema_id="trnm.rts.order.v1",
    command={"kind": "hold"},
)
request = json.loads(prepared.canonical_request)
expected = {
    "command",
    "content_revision",
    "contract_version",
    "expected_tick",
    "previous_state",
    "ruleset_revision",
    "transition_id",
}
if set(request) != expected:
    raise SystemExit("Nakama emitted unexpected World request fields")
serialized = prepared.canonical_request
for forbidden in (
    "authorization_id",
    "command_idempotency_key",
    "global_event_sequence",
    "match_id",
    "match_version",
    "participant_roster_hash",
):
    if forbidden in serialized:
        raise SystemExit(f"Nakama authority context leaked to World: {forbidden}")
PY

printf '%s\n' 'TRNM Nakama World transition boundary passed.'
