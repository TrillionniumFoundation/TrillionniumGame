#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

run_root=${TRNM_SERVER_SMOKE_ROOT:-run/server-process-smoke}
listen=${TRNM_SERVER_SMOKE_LISTEN:-127.0.0.1:17350}
token=${TRNM_SERVER_SMOKE_TOKEN:-0123456789abcdefghijklmnopqrstuvwxyz-._~}
rm -rf "$run_root"
mkdir -p "$run_root"

cargo build \
  --manifest-path crates/trnm-server/Cargo.toml \
  --locked \
  --bin trnm-server \
  >"$run_root/build.log" 2>&1

binary=crates/trnm-server/target/debug/trnm-server
test -x "$binary"

"$binary" serve \
  --listen "$listen" \
  --dev-token "$token" \
  --max-requests 5 \
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
  if grep -q 'source-candidate listening=' "$run_root/server.log"; then
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    cat "$run_root/server.log" >&2 || true
    cat "$run_root/server.err" >&2 || true
    exit 1
  fi
  sleep 0.05
done
grep -q 'source-candidate listening=' "$run_root/server.log"

TRNM_SERVER_SMOKE_LISTEN="$listen" \
TRNM_SERVER_SMOKE_TOKEN="$token" \
TRNM_SERVER_SMOKE_OUTPUT="$run_root/responses.json" \
python3 - <<'PY'
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

host, port_text = os.environ["TRNM_SERVER_SMOKE_LISTEN"].rsplit(":", 1)
address = (host, int(port_text))
token = os.environ["TRNM_SERVER_SMOKE_TOKEN"]


def request(method: str, path: str, body: str = "", authorization: str | None = None) -> dict[str, object]:
    headers = [
        f"{method} {path} HTTP/1.1",
        "host: localhost",
        f"content-length: {len(body.encode('utf-8'))}",
        "connection: close",
    ]
    if authorization is not None:
        headers.append(f"authorization: {authorization}")
    payload = ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")
    with socket.create_connection(address, timeout=2.0) as connection:
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks).decode("utf-8")
    head, response_body = raw.split("\r\n\r\n", 1)
    status = int(head.splitlines()[0].split()[1])
    return {"status": status, "body": json.loads(response_body), "raw_length": len(raw)}


responses = {
    "health": request("GET", "/healthz"),
    "ready": request("GET", "/readyz"),
    "unauthorized": request(
        "POST",
        "/v2/rpc/trnm_vertical_slice",
        '{"command":1,"expected_revision":0}',
    ),
    "applied": request(
        "POST",
        "/v2/rpc/trnm_vertical_slice",
        '{"command":1,"expected_revision":0}',
        f"Bearer {token}",
    ),
    "duplicate": request(
        "POST",
        "/v2/rpc/trnm_vertical_slice",
        '{"command":1,"expected_revision":0}',
        f"Bearer {token}",
    ),
}
assert responses["health"]["status"] == 200
assert responses["health"]["body"] == {"status": "ok"}
assert responses["ready"]["status"] == 200
assert responses["ready"]["body"] == {"status": "ready"}
assert responses["unauthorized"]["status"] == 401
assert responses["applied"]["status"] == 200
assert responses["applied"]["body"]["duplicate"] is False
assert responses["applied"]["body"]["revision"] == 1
assert responses["applied"]["body"]["event_count"] == 1
assert responses["applied"]["body"]["outbox_count"] == 1
assert responses["duplicate"]["status"] == 200
assert responses["duplicate"]["body"]["duplicate"] is True
assert responses["duplicate"]["body"]["revision"] == 1
Path(os.environ["TRNM_SERVER_SMOKE_OUTPUT"]).write_text(
    json.dumps(responses, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

wait "$pid"
trap - EXIT
grep -q 'trnm-server drained=true' "$run_root/server.log"
test ! -s "$run_root/server.err"

sha256sum \
  "$run_root/build.log" \
  "$run_root/server.log" \
  "$run_root/server.err" \
  "$run_root/responses.json" \
  > "$run_root/SHA256SUMS"
printf 'status=passed\ncompatibility_credit=false\nproduction_ready=false\n' \
  > "$run_root/result.txt"
