#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
bash scripts/project-preflight.sh --dev

python3 - "$root" <<'PY'
import json
import pathlib
import sys
from jsonschema import Draft202012Validator

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
print(f"{len(expected)} Paper Raid schemas and semantic invariants: ok")
PY

command -v node >/dev/null
node scripts/verify-research-session-golden.mjs
node scripts/verify-hepta-callback-golden.mjs

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
trap 'rm -f "$tmp"' EXIT
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
echo "Paper Raid Go/JS golden and generated fixture: PASS"
