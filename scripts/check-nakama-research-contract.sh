#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
bash scripts/project-preflight.sh --dev

python3 - "$root" <<'PY'
import hashlib
import json
import pathlib
import sys
from jsonschema import Draft202012Validator, RefResolver

root = pathlib.Path(sys.argv[1])
schema_dir = root / "contracts" / "research-session-v1"
expected = {
    "action-rejected.schema.json", "action.schema.json",
    "authorization-consumption-receipt.schema.json",
    "authorization-consumption-request.schema.json", "completion.schema.json",
    "completion-ingest-request.schema.json", "completion-receipt.schema.json",
    "event.schema.json", "join-metadata.schema.json", "rpc-request.schema.json",
    "rpc-response.schema.json", "signed-authorization.schema.json",
}
actual = {path.name for path in schema_dir.glob("*.schema.json")}
if actual != expected:
    raise SystemExit(f"research schema set differs: expected={sorted(expected)} actual={sorted(actual)}")

def walk(value, location):
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            raise SystemExit(f"{location}: object does not reject unknown fields")
        ref = value.get("$ref")
        if isinstance(ref, str) and "://" in ref:
            raise SystemExit(f"{location}: non-local schema reference {ref!r}")
        for key, child in value.items():
            walk(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk(child, f"{location}/{index}")

for name in sorted(expected):
    schema = json.loads((schema_dir / name).read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise SystemExit(f"{name}: wrong JSON Schema dialect")
    if schema.get("$id") != f"https://trillionnium.org/contracts/nakama/research-session-v1/{name}":
        raise SystemExit(f"{name}: wrong or missing canonical id")
    Draft202012Validator.check_schema(schema)
    walk(schema, name)

control_schema_dir = root / "contracts" / "research-control-v2"
expected_control = {
    "control.schema.json", "rpc-request.schema.json", "rpc-response.schema.json",
}
actual_control = {path.name for path in control_schema_dir.glob("*.schema.json")}
if actual_control != expected_control:
    raise SystemExit(f"research-control schema set differs: expected={sorted(expected_control)} actual={sorted(actual_control)}")
for name in sorted(expected_control):
    schema = json.loads((control_schema_dir / name).read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise SystemExit(f"research-control-v2/{name}: wrong JSON Schema dialect")
    if schema.get("$id") != f"https://trillionnium.org/contracts/nakama/research-control-v2/{name}":
        raise SystemExit(f"research-control-v2/{name}: wrong or missing canonical id")
    Draft202012Validator.check_schema(schema)
    walk(schema, f"research-control-v2/{name}")

spec_path = control_schema_dir / "spec.md"
if hashlib.sha256(spec_path.read_bytes()).hexdigest() != "5b6c51b2dc81307897b09b0cb16a233f88adddd82e9c1a8d7d918c82e9972839":
    raise SystemExit("research-control-v2 normative spec hash differs")

action = json.loads((schema_dir / "action.schema.json").read_text())
pairs = {
    (entry["properties"]["action_type"]["const"], entry["properties"]["payload_type"]["const"])
    for entry in action["oneOf"]
}
expected_pairs = {
    ("participant.ready", "trnm.research-session.ready.v1"),
    ("research.task.claimed", "trnm.paper-raid.task-claim.v1"),
    ("agent.proposal.submitted", "trnm.paper-raid.agent-proposal.v1"),
    ("artifact.manifest.published", "trnm.paper-raid.artifact-manifest.v1"),
    ("review.submitted", "trnm.paper-raid.review.v1"),
    ("checkpoint.recorded", "trnm.paper-raid.checkpoint.v1"),
    ("paper.release.acknowledged", "trnm.paper-raid.release-acknowledgement.v1"),
}
if pairs != expected_pairs:
    raise SystemExit("action/payload one-to-one whitelist drifted")

completion_ack = json.loads((schema_dir / "completion-receipt.schema.json").read_text())
for field in ("ruleset_hash", "challenge_snapshot_hash", "nakama_authority_key_id", "issuer_key_id", "signature"):
    if field not in completion_ack["required"]:
        raise SystemExit(f"completion ACK stopped requiring {field}")
consume_ack = json.loads((schema_dir / "authorization-consumption-receipt.schema.json").read_text())
if "session_roster_version" not in consume_ack["required"] or "signature" not in consume_ack["required"]:
    raise SystemExit("authorization consumption ACK lost epoch or signature")

fixture = json.loads((root / "contracts" / "research-session-golden-vectors.json").read_text())
if fixture.get("schema") != "trnm.nakama.research_session.golden_vectors.v1":
    raise SystemExit("research golden schema differs")
if len(fixture.get("authorizations", [])) != 3 or len(fixture.get("sealed_events", [])) != 11:
    raise SystemExit("research golden must use 3 participants and its full 11-event reachable archive")
if fixture["completion"]["value"]["event_count"] != len(fixture["sealed_events"]):
    raise SystemExit("research completion does not cover every golden event")
if not all(event["value"]["action_type"] == "paper.release.acknowledged"
           for event in fixture["sealed_events"][7:10]):
    raise SystemExit("golden release actions are not Agent acknowledgements")

callback = json.loads((root / "contracts" / "hepta-callback-golden-vectors.json").read_text())
if callback.get("schema") != "trnm.nakama.hepta_callback.golden_vectors.v1":
    raise SystemExit("Hepta callback golden schema differs")
source = callback.get("source_fixture", {})
if source.get("schema") != "hepta.paper_raid.golden_vectors.v2" or source.get("sha256") != "309584cc21a7169473a7bd37b93528edce4a3b248b313238cd81f6a7c3cad19d":
    raise SystemExit("Hepta callback golden lost its exact frozen source identity")
if callback.get("issuer", {}).get("public_key_base64") != "JfwyxHilpPhORVegNC4IJ4yINkG2FJvbC0qYarbCy3g=":
    raise SystemExit("Hepta callback golden issuer key differs")

control_fixture = json.loads((root / "contracts" / "research-control-golden-vectors.json").read_text())
if control_fixture.get("schema") != "trnm.nakama.research_control.golden_vectors.v2":
    raise SystemExit("research-control golden schema differs")
vectors = control_fixture.get("vectors", [])
if [(v.get("operation"), v.get("target_rpc")) for v in vectors] != [
    ("create", "trnm_research_session_create_v2"),
    ("resume", "trnm_research_session_resume_v2"),
    ("replace_roster", "trnm_research_session_replace_roster_v2"),
    ("complete", "trnm_research_session_complete_v2"),
]:
    raise SystemExit("research-control golden operation/RPC map differs")
for vector in vectors:
    if not vector.get("business_frame_base64") or not vector.get("payload_hash"):
        raise SystemExit(f"research-control {vector.get('operation')} does not publish its business frame and hash")
if control_fixture["keys"]["authorization_issuer"]["public_key_base64"] == control_fixture["keys"]["control_issuer"]["public_key_base64"]:
    raise SystemExit("research-control golden reuses its authorization issuer as control issuer")

# Build a closed local registry and validate every request fixture. This forces
# resolution of control.schema.json and the adjacent research-session-v1
# signed-authorization schema; network retrieval is neither needed nor allowed.
schema_store = {}
for path in sorted(schema_dir.glob("*.schema.json")) + sorted(control_schema_dir.glob("*.schema.json")):
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema_store[schema["$id"]] = schema
request_schema = json.loads((control_schema_dir / "rpc-request.schema.json").read_text(encoding="utf-8"))
request_validator = Draft202012Validator(
    request_schema,
    resolver=RefResolver.from_schema(request_schema, store=schema_store),
)
for vector in vectors:
    errors = sorted(request_validator.iter_errors(vector["request"]), key=lambda error: list(error.absolute_path))
    if errors:
        raise SystemExit(f"research-control {vector['operation']} fixture failed resolved schema validation: {errors[0].message}")
print(f"{len(expected)} Paper Raid schemas, {len(expected_control)} signed-control schemas, and semantic invariants: ok")
PY

command -v node >/dev/null
node scripts/verify-research-session-golden.mjs
node scripts/verify-hepta-callback-golden.mjs
node scripts/verify-research-control-golden.mjs

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "ERROR: Docker access is required for the pinned Go contract gate" >&2
  exit 1
fi

tmp=$(mktemp)
control_tmp=$(mktemp)
trap 'rm -f "$tmp" "$control_tmp"' EXIT
image='heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c'
"${docker_cmd[@]}" run --rm --read-only --entrypoint go \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  -e GOCACHE=/tmp/go-build -e GOMODCACHE=/tmp/go-mod -e GOTOOLCHAIN=local \
  -v "$root/runtime:/backend:ro" -w /backend "$image" \
  run -mod=readonly ./cmd/trnm-research-fixture >"$tmp"
cmp --silent "$tmp" contracts/research-session-golden-vectors.json || {
  echo "ERROR: tracked research golden differs from the pinned Go generator" >&2
  diff -u contracts/research-session-golden-vectors.json "$tmp" | sed -n '1,160p' >&2 || true
  exit 1
}
"${docker_cmd[@]}" run --rm --read-only --entrypoint go \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  -e GOCACHE=/tmp/go-build -e GOMODCACHE=/tmp/go-mod -e GOTOOLCHAIN=local \
  -v "$root/runtime:/backend:ro" -w /backend "$image" \
  run -mod=readonly ./cmd/trnm-research-control-fixture >"$control_tmp"
cmp --silent "$control_tmp" contracts/research-control-golden-vectors.json || {
  echo "ERROR: tracked research-control golden differs from the pinned Go generator" >&2
  diff -u contracts/research-control-golden-vectors.json "$control_tmp" | sed -n '1,160p' >&2 || true
  exit 1
}
echo "Paper Raid Go/JS golden and generated fixtures: PASS"
