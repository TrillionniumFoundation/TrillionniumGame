#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
bash scripts/project-preflight.sh --dev

python3 - "$root" <<'PY'
import base64
import copy
import json
import pathlib
import sys

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:
    raise SystemExit(
        "ERROR: Python package 'jsonschema' with Draft 2020-12 support is required; "
        "install it in an isolated environment with: python3 -m pip install 'jsonschema>=4.10'"
    ) from exc

root = pathlib.Path(sys.argv[1])
schema_dir = root / "contracts" / "v1"
expected = {
    "agent-command.schema.json",
    "archive-request.schema.json",
    "archive-response.schema.json",
    "command-rejected.schema.json",
    "complete-match-request.schema.json",
    "complete-match-response.schema.json",
    "create-match-request.schema.json",
    "create-match-response.schema.json",
    "evidence-request.schema.json",
    "evidence-response.schema.json",
    "health-response.schema.json",
    "join-metadata.schema.json",
    "match-completed.schema.json",
    "match-event.schema.json",
    "match-runtime-response.schema.json",
    "readiness-response.schema.json",
    "resume-match-request.schema.json",
    "resume-match-response.schema.json",
    "signed-match-authorization.schema.json",
    "terminal-facts.schema.json",
}
actual = {path.name for path in schema_dir.glob("*.schema.json")}
if actual != expected:
    raise SystemExit(f"contract schema set mismatch: expected {sorted(expected)}, got {sorted(actual)}")

canonical_logical_match_id = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
}
canonical_digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}

def walk_schema(value, location):
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            raise SystemExit(f"{location}: object schema does not reject unknown fields")
        ref = value.get("$ref")
        if ref is not None and (not isinstance(ref, str) or not ref.startswith("#/")):
            raise SystemExit(f"{location}: non-local JSON Schema reference {ref!r}")
        definitions = value.get("$defs", {})
        if "logicalMatchId" in definitions and definitions["logicalMatchId"] != canonical_logical_match_id:
            raise SystemExit(f"{location}: logicalMatchId rule drifted")
        if "digest" in definitions and definitions["digest"] != canonical_digest:
            raise SystemExit(f"{location}: digest rule drifted")
        for key, child in value.items():
            walk_schema(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_schema(child, f"{location}/{index}")

for name in sorted(expected):
    data = json.loads((schema_dir / name).read_text(encoding="utf-8"))
    if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise SystemExit(f"{name}: wrong JSON Schema dialect")
    expected_id = f"https://github.com/TrillionniumFoundation/Trillionnium-Nakama/contracts/v1/{name}"
    if data.get("$id") != expected_id:
        raise SystemExit(f"{name}: unexpected or missing $id")
    try:
        Draft202012Validator.check_schema(data)
    except Exception as exc:
        raise SystemExit(f"{name}: Draft 2020-12 metaschema validation failed: {exc}") from exc
    walk_schema(data, name)

for name in ("agent-command.schema.json", "match-event.schema.json"):
    schema = json.loads((schema_dir / name).read_text(encoding="utf-8"))
    payload = schema.get("properties", {}).get("payload", {})
    if payload.get("maxLength") != 87384 or payload.get("contentEncoding") != "base64":
        raise SystemExit(f"{name}: payload must preserve the 65,536-byte decoded wire limit")
archive_schema = json.loads((schema_dir / "archive-response.schema.json").read_text(encoding="utf-8"))
archive_payload = archive_schema.get("$defs", {}).get("matchEvent", {}).get("properties", {}).get("payload", {})
if archive_payload.get("maxLength") != 87384 or archive_payload.get("contentEncoding") != "base64":
    raise SystemExit("archive-response.schema.json: nested event payload wire limit drifted")

def validator(name):
    return Draft202012Validator(json.loads((schema_dir / name).read_text(encoding="utf-8")))

def expect_valid(name, value):
    errors = list(validator(name).iter_errors(value))
    if errors:
        raise SystemExit(f"{name}: positive instance rejected: {errors[0].message}")

def expect_invalid(name, value, case):
    if validator(name).is_valid(value):
        raise SystemExit(f"{name}: negative instance accepted ({case})")

zero_digest = "sha256:" + "0" * 64
zero_public_key = base64.b64encode(bytes(32)).decode("ascii")
zero_signature = base64.b64encode(bytes(64)).decode("ascii")
claim = {
    "schema": "trnm.match.authorization.v1",
    "authorization_id": "auth-1",
    "match_id": "match-1",
    "challenge_id": "challenge-1",
    "agent_id": "agent-1",
    "agent_did": "did:trnm:agent-1",
    "agent_key_id": "agent-key-1",
    "agent_public_key": zero_public_key,
    "subject_user_id": "user-1",
    "participant_slot": 1,
    "role": "challenger",
    "ruleset_hash": zero_digest,
    "dataset_hash": zero_digest,
    "challenge_snapshot_hash": zero_digest,
    "issued_at_unix": 1,
    "expires_at_unix": 2,
}
authorization_one = {"claim": claim, "issuer_key_id": "issuer-1", "signature": zero_signature}
authorization_two = copy.deepcopy(authorization_one)
authorization_two["claim"].update({
    "authorization_id": "auth-2", "agent_id": "agent-2",
    "agent_did": "did:trnm:agent-2", "agent_key_id": "agent-key-2",
    "subject_user_id": "user-2", "participant_slot": 2,
})
create_request = {
    "schema": "trnm.nakama.create-match.v1",
    "operator_token": "x" * 32,
    "authorizations": [authorization_one, authorization_two],
}
expect_valid("create-match-request.schema.json", create_request)
for count in (1, 3):
    candidate = copy.deepcopy(create_request)
    candidate["authorizations"] = ([authorization_one] if count == 1 else [authorization_one, authorization_two, authorization_two])
    expect_invalid("create-match-request.schema.json", candidate, f"{count} authorizations")
candidate = copy.deepcopy(create_request)
candidate["unexpected"] = True
expect_invalid("create-match-request.schema.json", candidate, "unknown top-level field")
candidate = copy.deepcopy(create_request)
candidate["authorizations"][0]["claim"]["unexpected"] = True
expect_invalid("create-match-request.schema.json", candidate, "unknown nested claim field")

evidence_request = {"schema": "trnm.nakama.get-evidence.v1", "logical_match_id": "match-1"}
expect_invalid("evidence-request.schema.json", evidence_request, "no access credential")
expect_valid("evidence-request.schema.json", {**evidence_request, "authorization_id": "auth-1"})
expect_valid("evidence-request.schema.json", {**evidence_request, "operator_token": "x" * 32})
expect_invalid("evidence-request.schema.json", {**evidence_request, "authorization_id": "auth-1", "unexpected": True}, "unknown field")

archive_request = {
    "schema": "trnm.nakama.get-archive.v1", "logical_match_id": "match-1",
    "after_sequence": 0, "authorization_id": "auth-1",
}
expect_valid("archive-request.schema.json", archive_request)
expect_valid("archive-request.schema.json", {
    "schema": "trnm.nakama.get-archive.v1", "logical_match_id": "match-1",
    "after_sequence": 2, "limit": 128, "operator_token": "x" * 32,
})
expect_invalid("archive-request.schema.json", {
    "schema": "trnm.nakama.get-archive.v1", "logical_match_id": "match-1",
    "authorization_id": "auth-1",
}, "missing cursor")
expect_invalid("archive-request.schema.json", {
    **archive_request, "operator_token": "x" * 32,
}, "multiple access credentials")
expect_invalid("archive-request.schema.json", {
    **archive_request, "authorization_id": "",
}, "empty access credential")
expect_invalid("archive-request.schema.json", {
    **archive_request, "limit": 0,
}, "zero limit")
expect_invalid("archive-request.schema.json", {
    **archive_request, "limit": 129,
}, "oversized limit")

expect_valid("join-metadata.schema.json", {"authorization_id": "auth-1"})
expect_invalid("join-metadata.schema.json", {"authorization_id": "auth-1", "participant_slot": 1}, "client-selected slot")
expect_valid("command-rejected.schema.json", {"schema": "trnm.match.command-rejected.v1", "command_id": "cmd-1", "reason": "sequence rejected"})
expect_invalid("command-rejected.schema.json", {"schema": "trnm.match.command-rejected.v1", "reason": "rejected", "event_hash": zero_digest}, "authoritative-looking extra field")

terminal_facts = {"result_code": "decisive", "winner_slot": 1, "outcome_hash": zero_digest}
completion = {
    "schema": "trnm.match.completed.v1", "commitment_id": zero_digest,
    "match_id": "match-1", "challenge_id": "challenge-1",
    "terminal_facts": terminal_facts, "event_count": 1,
    "event_root": zero_digest, "roster_root": zero_digest,
    "ruleset_hash": zero_digest, "dataset_hash": zero_digest,
    "challenge_snapshot_hash": zero_digest, "archive_hash": zero_digest,
    "completed_at_unix": 2, "authority_key_id": "authority-1",
    "signature": zero_signature,
}
expect_valid("match-completed.schema.json", completion)
candidate = copy.deepcopy(completion)
del candidate["terminal_facts"]
expect_invalid("match-completed.schema.json", candidate, "missing terminal_facts")
candidate = copy.deepcopy(completion)
candidate["terminal_facts"]["event_root"] = zero_digest
expect_invalid("match-completed.schema.json", candidate, "derived root inside terminal_facts")
evidence_response = {
    "schema": "trnm.nakama.evidence.v1", "logical_match_id": "match-1",
    "runtime_generation": 1, "completion": completion,
    "authority_public_key_base64": zero_public_key,
}
for name in ("evidence-response.schema.json", "complete-match-response.schema.json", "resume-match-response.schema.json"):
    expect_valid(name, evidence_response)
    candidate = copy.deepcopy(evidence_response)
    del candidate["completion"]["terminal_facts"]
    expect_invalid(name, candidate, "nested completion missing terminal_facts")

match_event = {
    "schema": "trnm.match.event.v1", "event_id": "event-1",
    "event_type": "participant_joined", "match_id": "match-1",
    "challenge_id": "challenge-1", "sequence": 1,
    "causation_id": "auth-1", "occurred_at_unix": 1,
    "participant_slot": 1, "match_version": 2,
    "payload_type": "trnm.participant.joined.v1", "payload": "AA==",
    "payload_hash": zero_digest, "event_hash": zero_digest,
}
roster = [
    {
        "participant_slot": 1, "subject_user_id": "user-1", "agent_id": "agent-1",
        "agent_did": "did:trnm:agent-1", "agent_key_id": "agent-key-1",
        "agent_key_hash": zero_digest, "role": "challenger",
    },
    {
        "participant_slot": 2, "subject_user_id": "user-2", "agent_id": "agent-2",
        "agent_did": "did:trnm:agent-2", "agent_key_id": "agent-key-2",
        "agent_key_hash": zero_digest, "role": "defender",
    },
]
participants = [
    {
        "participant_slot": 1, "authorization_id": "auth-1", "subject_user_id": "user-1",
        "agent_id": "agent-1", "joined": True, "last_command_sequence": 1,
    },
    {
        "participant_slot": 2, "authorization_id": "auth-2", "subject_user_id": "user-2",
        "agent_id": "agent-2", "joined": True, "last_command_sequence": 0,
    },
]
archive_response = {
    "schema": "trnm.nakama.archive.v1", "logical_match_id": "match-1",
    "external_match_id": "runtime-1", "runtime_generation": 1,
    "status": "active", "match_version": 2, "event_count": 1,
    "after_sequence": 0, "next_after_sequence": 1, "has_more": False,
    "events": [match_event], "roster": roster, "participants": participants,
}
expect_valid("archive-response.schema.json", archive_response)
candidate = copy.deepcopy(archive_response)
candidate["participants"].pop()
expect_invalid("archive-response.schema.json", candidate, "incomplete participant state")
candidate = copy.deepcopy(archive_response)
candidate["events"] = [copy.deepcopy(match_event) for _ in range(129)]
expect_invalid("archive-response.schema.json", candidate, "oversized event page")
candidate = copy.deepcopy(archive_response)
candidate["roster"][0]["authorization_id"] = "auth-1"
expect_invalid("archive-response.schema.json", candidate, "unknown roster field")

vectors = json.loads((root / "contracts" / "golden-vectors.json").read_text(encoding="utf-8"))
if vectors.get("schema") != "trnm.nakama.golden_vectors.v1":
    raise SystemExit("unsupported golden vector schema")
required_fixture_sections = {
    "fixture_notice", "keys", "source_digests", "authorizations", "command",
    "sealed_events", "event_merkle", "roster", "archive", "terminal_facts",
    "commitment", "completion",
}
missing = sorted(name for name in required_fixture_sections if not vectors.get(name))
if missing:
    raise SystemExit(f"golden fixture has missing or empty sections: {missing}")
if len(vectors["authorizations"]) != 2 or len(vectors["sealed_events"]) != 4:
    raise SystemExit("golden fixture must contain two authorizations and the four-event reachable archive")
if vectors["completion"].get("value", {}).get("event_count") != 4:
    raise SystemExit("golden completion must commit to all four reachable events")
print(f"{len(expected)} Draft 2020-12 schemas and positive/negative instances: ok")
PY

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is required for the independent cross-language golden fixture gate" >&2
  exit 1
fi
node scripts/verify-nakama-golden.mjs

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "ERROR: Docker access is required for the pinned Go 1.26.5 contract gate" >&2
  exit 1
fi

image='heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c'
"${docker_cmd[@]}" run --rm --read-only --entrypoint go \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  -e GOCACHE=/tmp/go-build -e GOMODCACHE=/tmp/go-mod -e GOTOOLCHAIN=local \
  -v "$root/runtime:/backend:ro" -v "$root/contracts:/contracts:ro" -w /backend "$image" \
  test -count=1 -mod=readonly ./internal/contract
