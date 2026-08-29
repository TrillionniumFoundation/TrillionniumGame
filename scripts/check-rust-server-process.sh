#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

run_root=${TRNM_SERVER_SMOKE_ROOT:-run/server-process-smoke}
listen=${TRNM_SERVER_SMOKE_LISTEN:-127.0.0.1:17350}
rm -rf "$run_root"
mkdir -p "$run_root"

if ! cargo build \
  --manifest-path crates/trnm-server/Cargo.toml \
  --locked \
  --bin trnm-server-foundation \
  >"$run_root/build.log" 2>&1; then
  cat "$run_root/build.log" >&2
  exit 1
fi

binary=crates/trnm-server/target/debug/trnm-server-foundation
test -x "$binary"

"$binary" serve \
  --bind "$listen" \
  --workers 2 \
  --queue-capacity 16 \
  --max-request-bytes 4096 \
  >"$run_root/server.log" 2>"$run_root/server.err" &
pid=$!
cleanup() {
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

for _ in $(seq 1 100); do
  if grep -q 'trnm-server listening on ' "$run_root/server.err"; then
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    cat "$run_root/server.log" >&2 || true
    cat "$run_root/server.err" >&2 || true
    exit 1
  fi
  sleep 0.05
done
grep -q 'trnm-server listening on ' "$run_root/server.err"
grep -q 'compatibility_credit=false' "$run_root/server.err"

TRNM_SERVER_SMOKE_LISTEN="$listen" \
TRNM_SERVER_SMOKE_OUTPUT="$run_root/responses.json" \
python3 - <<'PY'
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

host, port_text = os.environ["TRNM_SERVER_SMOKE_LISTEN"].rsplit(":", 1)
address = (host, int(port_text))


def request(method: str, path: str, body: bytes = b"") -> dict[str, object]:
    headers = [
        f"{method} {path} HTTP/1.1",
        "host: localhost",
        f"content-length: {len(body)}",
        "connection: close",
    ]
    payload = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body
    with socket.create_connection(address, timeout=2.0) as connection:
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    head, response_body = raw.split(b"\r\n\r\n", 1)
    status = int(head.splitlines()[0].split()[1])
    return {
        "status": status,
        "body": json.loads(response_body.decode("utf-8")),
        "raw_length": len(raw),
    }


entity = bytes([1]) * 16
bootstrap = entity + (1).to_bytes(8, "big") + bytes([2]) * 32
command = b"".join(
    [
        entity,
        bytes([3]) * 16,
        bytes([4]) * 32,
        (0).to_bytes(8, "big"),
        (1).to_bytes(8, "big"),
        bytes([5]) * 32,
        bytes([6]) * 16,
        bytes([7]) * 32,
        bytes([8]) * 16,
        bytes([9]) * 32,
    ]
)
assert len(bootstrap) == 56
assert len(command) == 208
responses = {
    "health": request("GET", "/healthz"),
    "ready": request("GET", "/readyz"),
    "bootstrap": request("POST", "/v1/bootstrap", bootstrap),
    "applied": request("POST", "/v1/command", command),
    "duplicate": request("POST", "/v1/command", command),
}
assert responses["health"]["status"] == 200
assert responses["health"]["body"] == {"status": "ok"}
assert responses["ready"]["status"] == 200
assert responses["ready"]["body"] == {"status": "ready"}
assert responses["bootstrap"]["status"] == 201
assert responses["bootstrap"]["body"]["outcome"] == "created"
assert responses["applied"]["status"] == 200
assert responses["applied"]["body"]["outcome"] == "applied"
assert responses["applied"]["body"]["revision"] == 1
assert responses["applied"]["body"]["event_count"] == 1
assert responses["applied"]["body"]["outbox_count"] == 1
assert responses["duplicate"]["status"] == 200
assert responses["duplicate"]["body"]["outcome"] == "duplicate"
assert responses["duplicate"]["body"]["revision"] == 1
Path(os.environ["TRNM_SERVER_SMOKE_OUTPUT"]).write_text(
    json.dumps(responses, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

# The foundation process does not yet implement a production signal/drain
# contract. Stop it explicitly after bounded ingress and preserve that gap in
# the result instead of claiming graceful-shutdown evidence.
kill "$pid"
wait "$pid" 2>/dev/null || true
trap - EXIT

sha256sum \
  "$run_root/build.log" \
  "$run_root/server.log" \
  "$run_root/server.err" \
  "$run_root/responses.json" \
  > "$run_root/SHA256SUMS"
printf '%s\n' \
  'status=passed' \
  'process_ingress_verified=true' \
  'graceful_shutdown_verified=false' \
  'database_durability_verified=false' \
  'compatibility_credit=false' \
  'production_ready=false' \
  > "$run_root/result.txt"
