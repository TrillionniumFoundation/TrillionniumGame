#!/usr/bin/env python3
"""Build the exact four-file outbox evidence-sealing transaction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCRIPT = Path("scripts/ci-outbox-final-attempt-reaper.sh")
WORKFLOW = Path(".github/workflows/prospective-merge-gate.yml")
MANIFEST = Path("docs/governance/REQUIRED_WORKFLOWS_V1.json")
OVERLAY = Path("docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json")
EXPECTED = {
    SCRIPT: "51df09d34772f57e1518cee72fbd0786bb41b97e",
    WORKFLOW: "88d50bd599ee796b91a9395a768f126383603847",
    MANIFEST: "733ac856776d326c02995d7ef12709aac4460008",
    OVERLAY: "b2304256ca6107e9150b61ab62c6971057731e03",
}


def git_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def overlay_digest(value: dict[str, object]) -> str:
    payload = dict(value)
    payload.pop("overlay_sha256", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    before: dict[Path, bytes] = {}
    for path, expected in EXPECTED.items():
        data = path.read_bytes()
        observed = git_blob(data)
        if observed != expected:
            raise SystemExit(f"{path}: expected {expected}, observed {observed}")
        before[path] = data

    script = before[SCRIPT].decode()
    old_logger = 'exec > >(tee "$evidence/logs/run.log") 2>&1\n'
    new_logger = (
        "exec 3>&1 4>&2\n"
        'exec > >(tee "$evidence/logs/run.log" >&3) 2>&1\n'
        "tee_pid=$!\n"
    )
    old_tail = (
        "printf 'status=passed\\nprofile=%s\\ncommit=%s\\n' \"$profile\" \"$commit\" >\"$evidence/result.env\"\n"
        'find "$evidence" -type f -print0 | sort -z | xargs -0 sha256sum >"$evidence/files.sha256"\n'
        'cat "$evidence/result.env"\n'
    )
    new_tail = (
        "printf 'status=passed\\nprofile=%s\\ncommit=%s\\n' \"$profile\" \"$commit\" >\"$evidence/result.env\"\n"
        'cat "$evidence/result.env"\n\n'
        "# Stop the process-substitution logger before calculating retained-member\n"
        "# digests. Otherwise run.log can grow after it is hashed, and redirecting the\n"
        "# manifest creates files.sha256 early enough for it to hash itself.\n"
        "exec 1>&3 2>&4\n"
        "exec 3>&- 4>&-\n"
        'wait "$tee_pid"\n'
        "(\n"
        '  cd "$evidence"\n'
        "  find . -type f ! -path './files.sha256' -print0 \\\n"
        "    | LC_ALL=C sort -z | xargs -0 sha256sum >files.sha256\n"
        "  sha256sum --check files.sha256\n"
        ")\n"
    )
    if script.count(old_logger) != 1 or script.count(old_tail) != 1:
        raise SystemExit("reviewed outbox anchors drifted")
    SCRIPT.write_text(script.replace(old_logger, new_logger, 1).replace(old_tail, new_tail, 1))

    workflow = before[WORKFLOW].decode()
    identity = '          test -s "$outbox/identity.env"\n'
    verify = '          sha256sum --check "$server/SHA256SUMS"\n'
    if workflow.count(identity) != 1 or workflow.count(verify) != 1:
        raise SystemExit("reviewed prospective workflow anchors drifted")
    workflow = workflow.replace(identity, identity + '          test -s "$outbox/files.sha256"\n', 1)
    workflow = workflow.replace(verify, verify + '          (cd "$outbox" && sha256sum --check files.sha256)\n', 1)
    WORKFLOW.write_text(workflow)
    workflow_blob = git_blob(WORKFLOW.read_bytes())

    manifest = json.loads(before[MANIFEST])
    rows = [row for row in manifest["workflows"] if row.get("path") == WORKFLOW.as_posix()]
    if len(rows) != 1 or rows[0].get("workflow_id") != 347178559:
        raise SystemExit("prospective workflow manifest row drifted")
    if rows[0].get("git_blob_sha1") != EXPECTED[WORKFLOW]:
        raise SystemExit("old prospective workflow pin drifted")
    rows[0]["git_blob_sha1"] = workflow_blob
    MANIFEST.write_bytes(canonical(manifest))
    manifest_blob = git_blob(MANIFEST.read_bytes())

    overlay = json.loads(before[OVERLAY])
    if overlay.get("base_manifest_blob_sha1") != EXPECTED[MANIFEST]:
        raise SystemExit("old base-manifest overlay pin drifted")
    if overlay.get("overlay_sha256") != overlay_digest(overlay):
        raise SystemExit("existing overlay digest is invalid")
    overlay["base_manifest_blob_sha1"] = manifest_blob
    overlay["overlay_sha256"] = overlay_digest(overlay)
    OVERLAY.write_bytes(canonical(overlay))

    report = {
        "source_head": "7deee4377cff401854647151842c582f038feef1",
        "changes": {
            path.as_posix(): {
                "before": EXPECTED[path],
                "after": git_blob(path.read_bytes()),
                "bytes": len(path.read_bytes()),
            }
            for path in (SCRIPT, WORKFLOW, MANIFEST, OVERLAY)
        },
        "overlay_sha256": overlay["overlay_sha256"],
        "claim_boundary": {
            "accepted_evidence": False,
            "gap_closed": False,
            "production_ready": False,
        },
    }
    output = Path("run/outbox-evidence-sealing-export")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
