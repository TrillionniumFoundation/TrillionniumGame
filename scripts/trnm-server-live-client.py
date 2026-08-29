#!/usr/bin/env python3
"""Black-box client for the first Rust server/database vertical slice."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ENTITY = "01" * 16
INITIAL_STATE = "02" * 32
ADMIN_HEADER = "authorization"


def repeated(value: int, count: int) -> str:
    return f"{value:02x}" * count


def commit_payload(index: int, expected_revision: int) -> dict[str, Any]:
    base = 3 + (index - 1) * 8
    return {
        "entity_id": ENTITY,
        "command_id": repeated(base, 16),
        "fingerprint": repeated(base + 1, 32),
        "expected_revision": expected_revision,
        "authority_generation": 1,
        "next_state_digest": repeated(base + 2, 32),
        "committed_at_ms": 1000 + index,
        "event_id": repeated(base + 3, 16),
        "event_payload_digest": repeated(base + 4, 32),
        "intent_id": repeated(base + 5, 16),
        "intent_kind": "broadcast",
        "intent_payload_digest": repeated(base + 6, 32),
        "available_at_ms": 1000 + index,
    }


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body.decode("utf-8"))
        if not isinstance(value, dict):
            raise AssertionError("response JSON must be an object")
        return value


def receive_until(stream: socket.socket, marker: bytes, maximum: int) -> bytes:
    data = bytearray()
    while marker not in data:
        chunk = stream.recv(4096)
        if not chunk:
            raise AssertionError(f"connection closed before marker {marker!r}")
        data.extend(chunk)
        if len(data) > maximum:
            raise AssertionError("response header exceeds bounded test limit")
    return bytes(data)


def receive_exact(stream: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = stream.recv(length - len(data))
        if not chunk:
            raise AssertionError("connection closed before exact frame length")
        data.extend(chunk)
    return bytes(data)


def parse_http(raw: bytes) -> HttpResponse:
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise AssertionError("HTTP response lacks header terminator")
    lines = head.decode("ascii").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) != 3 or parts[0] != "HTTP/1.1":
        raise AssertionError(f"invalid response line: {lines[0]!r}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"invalid response header: {line!r}")
        key = name.strip().lower()
        if key in headers:
            raise AssertionError(f"duplicate response header: {key}")
        headers[key] = value.strip()
    expected = int(headers.get("content-length", "-1"))
    if expected != len(body):
        raise AssertionError(f"content length mismatch: expected {expected}, got {len(body)}")
    return HttpResponse(int(parts[1]), headers, body)


def request_bytes(
    method: str,
    target: str,
    host: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    values = {
        "Host": host,
        "Connection": "close",
        **(headers or {}),
    }
    if method == "POST":
        values["Content-Length"] = str(len(body))
    lines = [f"{method} {target} HTTP/1.1"]
    lines.extend(f"{name}: {value}" for name, value in values.items())
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def http_request(
    host: str,
    port: int,
    method: str,
    target: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> HttpResponse:
    with socket.create_connection((host, port), timeout=5) as stream:
        stream.settimeout(5)
        stream.sendall(request_bytes(method, target, f"{host}:{port}", headers, body))
        data = bytearray()
        while True:
            chunk = stream.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 2 * 1024 * 1024:
                raise AssertionError("HTTP response exceeds bounded test limit")
    return parse_http(bytes(data))


def compact(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def authorized_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def wait_ready(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            response = http_request(host, port, "GET", "/readyz")
            if response.status == 200 and response.json() == {"status": "ready"}:
                return
        except (OSError, AssertionError, ValueError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(0.1)
    raise AssertionError(f"server did not become ready: {last_error}")


def assert_error(response: HttpResponse, status: int, code: str) -> None:
    if response.status != status:
        raise AssertionError(f"expected HTTP {status}, got {response.status}: {response.body!r}")
    document = response.json()
    if document.get("code") != code:
        raise AssertionError(f"expected code {code!r}, got {document!r}")
    serialized = response.body.decode("utf-8")
    for private in ("database", "sqlstate", "fingerprint_invalid", "command_id_conflict"):
        if private in serialized.lower():
            raise AssertionError(f"private detail leaked in response: {private}")


def assert_receipt(
    response: HttpResponse,
    status: int,
    outcome: str,
    payload: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    if response.status != status:
        raise AssertionError(f"expected HTTP {status}, got {response.status}: {response.body!r}")
    document = response.json()
    expected = {
        "outcome": outcome,
        "entity_id": payload["entity_id"],
        "command_id": payload["command_id"],
        "fingerprint": payload["fingerprint"],
        "revision": revision,
        "state_digest": payload["next_state_digest"],
        "first_event_sequence": revision,
        "last_event_sequence": revision,
        "event_count": 1,
        "outbox": [payload["intent_id"]],
    }
    if document != expected:
        raise AssertionError(f"receipt mismatch:\nexpected={expected!r}\nobserved={document!r}")
    return document


def encode_client_frame(payload: bytes) -> bytes:
    mask = b"\x11\x22\x33\x44"
    length = len(payload)
    header = bytearray([0x81])
    if length <= 125:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask)
    header.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header)


def read_server_frame(stream: socket.socket) -> tuple[int, bytes]:
    head = receive_exact(stream, 2)
    if head[0] & 0x80 == 0 or head[0] & 0x70:
        raise AssertionError("server frame is fragmented or has reserved bits")
    if head[1] & 0x80:
        raise AssertionError("server frame must not be masked")
    marker = head[1] & 0x7F
    if marker <= 125:
        length = marker
    elif marker == 126:
        length = struct.unpack("!H", receive_exact(stream, 2))[0]
        if length < 126:
            raise AssertionError("noncanonical server frame length")
    else:
        length = struct.unpack("!Q", receive_exact(stream, 8))[0]
        if length <= 0xFFFF or length & (1 << 63):
            raise AssertionError("noncanonical server frame length")
    return head[0] & 0x0F, receive_exact(stream, length)


def websocket_commit(
    host: str,
    port: int,
    token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    key = base64.b64encode(bytes(range(16))).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + GUID).encode("ascii")).digest()
    ).decode("ascii")
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": key,
        "Sec-WebSocket-Protocol": "trnm.json.v1",
        "Authorization": f"Bearer {token}",
    }
    with socket.create_connection((host, port), timeout=5) as stream:
        stream.settimeout(5)
        stream.sendall(request_bytes("GET", "/v1/realtime", f"{host}:{port}", headers))
        raw_head = receive_until(stream, b"\r\n\r\n", 16 * 1024)
        head, separator, remainder = raw_head.partition(b"\r\n\r\n")
        if not separator or remainder:
            raise AssertionError("unexpected bytes after WebSocket handshake")
        lines = head.decode("ascii").split("\r\n")
        if lines[0] != "HTTP/1.1 101 Switching Protocols":
            raise AssertionError(f"WebSocket upgrade failed: {lines[0]!r}")
        response_headers = {
            name.strip().lower(): value.strip()
            for name, separator, value in (line.partition(":") for line in lines[1:])
            if separator
        }
        if response_headers.get("sec-websocket-accept") != expected_accept:
            raise AssertionError("WebSocket accept digest mismatch")
        if response_headers.get("sec-websocket-protocol") != "trnm.json.v1":
            raise AssertionError("WebSocket subprotocol mismatch")
        stream.sendall(encode_client_frame(compact(payload)))
        opcode, body = read_server_frame(stream)
        if opcode != 0x1:
            raise AssertionError(f"expected text frame, got opcode {opcode}")
        document = json.loads(body.decode("utf-8"))
        if not isinstance(document, dict):
            raise AssertionError("WebSocket response must be a JSON object")
        close_opcode, close_body = read_server_frame(stream)
        if close_opcode != 0x8 or close_body != b"\x03\xe8":
            raise AssertionError("expected normal WebSocket close frame")
        return document


def send_without_reading(
    host: str,
    port: int,
    token: str,
    payload: dict[str, Any],
) -> None:
    stream = socket.create_connection((host, port), timeout=5)
    stream.settimeout(5)
    stream.sendall(
        request_bytes(
            "POST",
            "/v1/authority/commit",
            f"{host}:{port}",
            authorized_headers(token),
            compact(payload),
        )
    )
    # Give the sequential server enough time to commit while deliberately never
    # receiving the response. Closing afterwards models an ambiguous client
    # outcome rather than a request that never arrived.
    time.sleep(1.0)
    stream.close()


def request_commit(host: str, port: int, token: str, payload: dict[str, Any]) -> HttpResponse:
    return http_request(
        host,
        port,
        "POST",
        "/v1/authority/commit",
        authorized_headers(token),
        compact(payload),
    )


def primary(host: str, port: int, token: str) -> dict[str, Any]:
    wait_ready(host, port)
    health = http_request(host, port, "GET", "/healthz")
    if health.status != 200 or health.json() != {"status": "ok"}:
        raise AssertionError("health response mismatch")

    bootstrap = {
        "entity_id": ENTITY,
        "authority_generation": 1,
        "state_digest": INITIAL_STATE,
        "updated_at_ms": 1000,
    }
    assert_error(
        http_request(
            host,
            port,
            "POST",
            "/v1/authority/bootstrap",
            {"Content-Type": "application/json"},
            compact(bootstrap),
        ),
        401,
        "unauthenticated",
    )
    created = http_request(
        host,
        port,
        "POST",
        "/v1/authority/bootstrap",
        authorized_headers(token),
        compact(bootstrap),
    )
    if created.status != 201:
        raise AssertionError(f"bootstrap failed: {created.status} {created.body!r}")
    head = created.json()
    if head.get("revision") != 0 or head.get("last_event_sequence") != 0:
        raise AssertionError(f"bootstrap head mismatch: {head!r}")

    first = commit_payload(1, 0)
    first_applied = assert_receipt(request_commit(host, port, token, first), 201, "applied", first, 1)
    first_duplicate = assert_receipt(request_commit(host, port, token, first), 200, "duplicate", first, 1)
    if {**first_applied, "outcome": "duplicate"} != first_duplicate:
        raise AssertionError("duplicate did not replay the exact durable receipt")

    changed = dict(first)
    changed["fingerprint"] = repeated(0xF0, 32)
    assert_error(request_commit(host, port, token, changed), 409, "already_exists")

    second = commit_payload(2, 1)
    second_document = websocket_commit(host, port, token, second)
    expected_second = {
        "outcome": "applied",
        "entity_id": second["entity_id"],
        "command_id": second["command_id"],
        "fingerprint": second["fingerprint"],
        "revision": 2,
        "state_digest": second["next_state_digest"],
        "first_event_sequence": 2,
        "last_event_sequence": 2,
        "event_count": 1,
        "outbox": [second["intent_id"]],
    }
    if second_document != expected_second:
        raise AssertionError(f"WebSocket receipt mismatch: {second_document!r}")

    third = commit_payload(3, 2)
    send_without_reading(host, port, token, third)
    third_duplicate = assert_receipt(
        request_commit(host, port, token, third),
        200,
        "duplicate",
        third,
        3,
    )

    metrics = http_request(host, port, "GET", "/metrics")
    text = metrics.body.decode("utf-8")
    for required in (
        "trnm_server_commands_applied_total 3",
        "trnm_server_command_replays_total 2",
        "trnm_server_ready 1",
    ):
        if required not in text:
            raise AssertionError(f"metrics missing {required!r}: {text}")

    assert_error(http_request(host, port, "POST", "/-/drain", body=b""), 401, "unauthenticated")
    still_ready = http_request(host, port, "GET", "/readyz")
    if still_ready.status != 200:
        raise AssertionError("unauthenticated drain changed readiness")
    drained = http_request(
        host,
        port,
        "POST",
        "/-/drain",
        {"Authorization": f"Bearer {token}"},
        b"",
    )
    if drained.status != 200 or drained.json() != {"status": "draining"}:
        raise AssertionError("authenticated drain failed")
    return {
        "phase": "primary",
        "http_applied": first_applied,
        "websocket_applied": second_document,
        "response_loss_replay": third_duplicate,
        "metrics_verified": True,
        "drain_verified": True,
    }


def restart(host: str, port: int, token: str) -> dict[str, Any]:
    wait_ready(host, port)
    receipts = []
    for index in (1, 2, 3):
        payload = commit_payload(index, index - 1)
        receipts.append(
            assert_receipt(
                request_commit(host, port, token, payload),
                200,
                "duplicate",
                payload,
                index,
            )
        )
    metrics = http_request(host, port, "GET", "/metrics").body.decode("utf-8")
    if "trnm_server_commands_applied_total 0" not in metrics:
        raise AssertionError("restart replay unexpectedly applied a command")
    if "trnm_server_command_replays_total 3" not in metrics:
        raise AssertionError("restart replay metric mismatch")
    drained = http_request(
        host,
        port,
        "POST",
        "/-/drain",
        {"Authorization": f"Bearer {token}"},
        b"",
    )
    if drained.status != 200:
        raise AssertionError("restart drain failed")
    return {
        "phase": "restart",
        "duplicate_receipts": receipts,
        "restart_replay_verified": True,
        "drain_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--phase", choices=("primary", "restart"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if len(args.token) < 32:
        raise SystemExit("test token must satisfy the server minimum")
    report = (
        primary(args.host, args.port, args.token)
        if args.phase == "primary"
        else restart(args.host, args.port, args.token)
    )
    report.update(
        {
            "schema": "trillionnium.server-live-client.v1",
            "host": args.host,
            "port": args.port,
            "compatibility_credit": False,
            "production_ready": False,
        }
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trnm-server live client failed: {error}", file=sys.stderr)
        raise SystemExit(1)
