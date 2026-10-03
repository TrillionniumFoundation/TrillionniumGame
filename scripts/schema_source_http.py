"""POSIX-only, cancellable immutable GitHub source transport.

The trusted parent owns an absolute deadline. The isolated stdlib worker owns
one HTTPS request; credentials travel only through bounded stdin. No worker
error contains a remote response, request token or original OS exception.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import weakref
from pathlib import Path

FILE_JSON_BYTES = 1024 * 1024
TREE_JSON_BYTES = 8 * 1024 * 1024
ERROR_BODY_BYTES = 64 * 1024
INPUT_BYTES = 16 * 1024
TOKEN_BYTES = 4096
URL_BYTES = 2048
HEADER_BYTES = 128
STDERR_BYTES = 4096
ABSOLUTE_SECONDS = 30
REAP_SECONDS = 5
MAX_COLLECTION_REQUESTS = 2 + 18
COLLECTION_SECONDS = MAX_COLLECTION_REQUESTS * ABSOLUTE_SECONDS + REAP_SECONDS
MAX_ACTIVE_DEADLINES = 64
CHUNK_BYTES = 64 * 1024
MAGIC = b"TRNM_SOURCE_HTTP_V1\n"
BUDGETS = {"file": FILE_JSON_BYTES, "tree": TREE_JSON_BYTES}
CONTROL_PATHS = frozenset((
    "migrations/MIGRATION_CHAIN.lock.json", "docs/development/SCHEMA_AUTHORITY.json",
    "scripts/check-migration-lock.py", "scripts/schema_source_selection.py",
    "scripts/schema_evidence_binding.py", "config/database-test-images.json",
    "scripts/check-authoritative-schema-identity.py", "scripts/capture-schema-source-selection.py",
))
MESSAGES = {
    "input": "invalid source worker input",
    "network": "GitHub source request failed",
    "body-budget": "GitHub source HTTP byte budget exceeded",
    "length": "GitHub source invalid or truncated Content-Length",
    "encoding": "GitHub source encoded HTTP body forbidden",
    "redirect": "GitHub source redirect forbidden",
    "internal": "source worker failed",
    "deadline": "GitHub source absolute request deadline exceeded",
    "deadline-cap": "source deadline ownership budget exceeded",
    "pipe-budget": "source worker pipe byte budget exceeded",
    "worker-io": "source worker pipe failed",
    "worker-stderr": "source worker emitted an unexpected diagnostic",
    "protocol": "invalid source worker response",
    "start": "source worker could not start",
    "cleanup": "source worker could not be reaped",
    "platform": "source HTTP supervision requires POSIX",
}


class SourceHttpError(ValueError):
    def __init__(self, code: str, status: int | None = None):
        self.code = code if code in MESSAGES or code == "http" else "internal"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(f"GitHub source request failed: HTTP {self.status}" if self.code == "http" and self.status is not None else MESSAGES.get(self.code, MESSAGES["internal"]))


def _deadline_api():
    issued = weakref.WeakKeyDictionary()
    lock = threading.RLock()
    class Deadline:
        __slots__ = ('__weakref__',)
        def __new__(cls, *args, **kwargs):
            raise TypeError('source_deadline_requires_issuance')
        def __init_subclass__(cls, **kwargs):
            raise TypeError('source_deadline_cannot_be_subclassed')
        def __reduce__(self):
            raise TypeError('source_deadline_not_serializable')
    def clock():
        now = time.monotonic()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise SourceHttpError('deadline')
        return now
    def record(token):
        if type(token) is not Deadline or token not in issued:
            raise SourceHttpError('input')
        row = issued[token]
        if row[2] is not None and (type(row[2]) is not Deadline or row[2] not in issued):
            raise SourceHttpError('input')
        return row
    def remaining_at(expiration):
        budget = expiration - clock()
        if not math.isfinite(budget) or budget <= 0:
            raise SourceHttpError('deadline')
        return budget
    def start(*, purpose='request', parent=None):
        now = clock()
        if type(purpose) is not str or purpose not in ('request', 'collection'):
            raise SourceHttpError('input')
        expiration = now + (ABSOLUTE_SECONDS if purpose == 'request' else COLLECTION_SECONDS)
        with lock:
            if parent is not None:
                parent_row = record(parent)
                if purpose != 'request' or parent_row[1] != 'collection':
                    raise SourceHttpError('input')
                remaining_at(parent_row[0])
                expiration = min(expiration, parent_row[0])
            if not math.isfinite(expiration):
                raise SourceHttpError('deadline')
            if len(issued) >= MAX_ACTIVE_DEADLINES:
                raise SourceHttpError('deadline-cap')
            token = object.__new__(Deadline)
            issued[token] = (expiration, purpose, parent)
        try:
            remaining_at(expiration)
            return token
        except BaseException:
            release(token)
            raise
    def remaining(token):
        with lock:
            expiration = record(token)[0]
        return remaining_at(expiration)
    def release(token):
        with lock:
            if type(token) is not Deadline:
                raise SourceHttpError('input')
            issued.pop(token, None)
    def finish(token, *, release_owned=False):
        with lock:
            expiration = record(token)[0]
            if release_owned:
                del issued[token]
        # Check after explicit release, retaining only the fixed expiry locally.
        remaining_at(expiration)
    def acquire(existing=None):
        if existing is None:
            return start(), True
        with lock:
            purpose = record(existing)[1]
        if purpose == 'collection':
            return start(parent=existing), True
        remaining(existing)
        return existing, False
    def count():
        with lock:
            return len(issued)
    return start, remaining, release, finish, acquire, count


# These are ordinary in-process ownership tokens, not a reflection sandbox.
# Callers choose only fixed request/collection purposes, never a float or TTL.
# Explicit release is mandatory internally; weak ownership is a safety fallback.
(start_deadline, remaining_deadline, release_deadline, finish_deadline,
 acquire_request_deadline, active_deadline_count) = _deadline_api()
del _deadline_api


def validate_url(url: str, budget: str) -> None:
    if type(url) is not str or len(url.encode("utf-8")) > URL_BYTES or type(budget) is not str or budget not in BUDGETS:
        raise SourceHttpError("input")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.fragment:
        raise SourceHttpError("input")
    prefix = re.fullmatch(r"/repos/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})/(.+)", parsed.path)
    if prefix is None:
        raise SourceHttpError("input")
    suffix = prefix[3]
    if budget == "tree":
        allowed = re.fullmatch(r"git/trees/[0-9a-f]{40}", suffix) is not None and parsed.query == "recursive=1"
    elif re.fullmatch(r"git/commits/[0-9a-f]{40}", suffix) is not None:
        allowed = not parsed.query
    elif suffix.startswith("contents/"):
        path = suffix[len("contents/"):]
        allowed = (path in CONTROL_PATHS or re.fullmatch(r"migrations/(postgresql|cockroachdb)/[0-9]{4}_[a-z0-9_]+[.]sql", path) is not None) and re.fullmatch(r"ref=[0-9a-f]{40}", parsed.query) is not None
    else:
        allowed = False
    if not allowed:
        raise SourceHttpError("input")


def make_input(token: str, url: str, budget: str) -> bytes:
    validate_url(url, budget)
    if type(token) is not str or not token or len(token) > TOKEN_BYTES or any(not 33 <= ord(c) <= 126 for c in token):
        raise SourceHttpError("input")
    raw = json.dumps({"version": 1, "token": token, "url": url, "budget": budget}, separators=(",", ":")).encode("ascii")
    if len(raw) > INPUT_BYTES:
        raise SourceHttpError("input")
    return raw


def parse_input(raw: bytes) -> dict:
    if type(raw) is not bytes or len(raw) > INPUT_BYTES:
        raise SourceHttpError("input")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SourceHttpError("input")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(SourceHttpError("input")))
        if type(value) is not dict or set(value) != {"version", "token", "url", "budget"} or type(value["version"]) is not int or value["version"] != 1:
            raise SourceHttpError("input")
        make_input(value["token"], value["url"], value["budget"])
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError, KeyError):
        raise SourceHttpError("input") from None


class RejectRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise SourceHttpError("redirect")
    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def open_source_url(request, *, timeout):
    # No environment proxy or global opener. Redirect handlers close without
    # consuming an unbounded redirect body or forwarding Authorization.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirect())
    return opener.open(request, timeout=timeout)


def read_response(response, maximum: int) -> bytes:
    headers = response.headers if response.headers is not None else {}
    declared = headers.get("Content-Length")
    if declared is not None:
        if type(declared) is not str or re.fullmatch(r"[0-9]{1,20}", declared) is None:
            raise SourceHttpError("length")
        if int(declared) > maximum:
            raise SourceHttpError("body-budget")
    if headers.get("Content-Encoding", "identity") != "identity":
        raise SourceHttpError("encoding")
    raw = response.read(maximum + 1)
    if type(raw) is not bytes or len(raw) > maximum:
        raise SourceHttpError("body-budget")
    if declared is not None and len(raw) != int(declared):
        raise SourceHttpError("length")
    return raw


def perform_request(request: dict) -> bytes:
    # Revalidate even an in-process caller; the standalone worker accepts only
    # a closed input document and a reviewed endpoint/budget combination.
    parse_input(make_input(request["token"], request["url"], request["budget"]))
    maximum = BUDGETS[request["budget"]]
    wire = urllib.request.Request(request["url"], headers={
        "Accept": "application/vnd.github+json", "Authorization": "Bearer " + request["token"],
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "trillionnium-outbox-log-verifier/2",
    })
    try:
        with open_source_url(wire, timeout=ABSOLUTE_SECONDS) as response:
            return read_response(response, maximum)
    except urllib.error.HTTPError as error:
        try:
            read_response(error, ERROR_BODY_BYTES)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise SourceHttpError("http", error.code) from None
        finally:
            error.close()
        raise SourceHttpError("http", error.code) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise SourceHttpError("network") from None


def ok_envelope(raw: bytes) -> bytes:
    return MAGIC + f"OK {len(raw)}\n".encode("ascii") + raw


def error_envelope(error: SourceHttpError) -> bytes:
    return MAGIC + (f"ERR http {error.status}\n" if error.code == "http" and error.status is not None else f"ERR {error.code}\n").encode("ascii")


def decode_envelope(raw: bytes, maximum: int) -> bytes:
    if not raw.startswith(MAGIC):
        raise SourceHttpError("protocol")
    header, separator, body = raw[len(MAGIC):].partition(b"\n")
    if not separator or len(MAGIC) + len(header) + 1 > HEADER_BYTES:
        raise SourceHttpError("protocol")
    matched = re.fullmatch(rb"OK ([0-9]{1,8})", header)
    if matched:
        size = int(matched[1])
        if size > maximum or len(body) != size:
            raise SourceHttpError("protocol")
        return body
    matched = re.fullmatch(rb"ERR ([a-z-]+)(?: ([0-9]{3}))?", header)
    if not matched or body:
        raise SourceHttpError("protocol")
    code = matched[1].decode("ascii")
    status = int(matched[2]) if matched[2] else None
    if code not in MESSAGES and code != "http" or code == "http" and (status is None or not 100 <= status <= 599) or code != "http" and status is not None:
        raise SourceHttpError("protocol")
    raise SourceHttpError(code, status)


def _kill_and_reap(process) -> None:
    failed = type(process.pid) is not int or process.pid <= 0
    try:
        if not failed:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        failed = True
    try:
        process.wait(timeout=REAP_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        raise SourceHttpError("cleanup") from None
    if failed:
        raise SourceHttpError("cleanup")


def _fetch_request(token: str, url: str, *, budget: str, deadline) -> bytes:
    """Private worker supervision under an already owned request deadline."""
    remaining_deadline(deadline)
    if os.name != "posix":
        raise SourceHttpError("platform")
    input_bytes = make_input(token, url, budget)
    maximum = BUDGETS[budget]
    process = None
    selector = None
    completed = False
    result = None
    try:
        command = [sys.executable, "-I", "-S", "-u", str(Path(__file__).resolve())]
        options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       bufsize=0, close_fds=True, start_new_session=True,
                       env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        # All bounded setup is complete. Never launch an already expired job.
        remaining_deadline(deadline)
        try:
            process = subprocess.Popen(command, **options)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise SourceHttpError("start") from None
        if type(process.pid) is not int or process.pid <= 0:
            raise SourceHttpError("start")
        selector = selectors.DefaultSelector()
        for label, pipe, events in (("stdin", process.stdin, selectors.EVENT_WRITE),
                                    ("stdout", process.stdout, selectors.EVENT_READ),
                                    ("stderr", process.stderr, selectors.EVENT_READ)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, events, label)
        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": maximum + HEADER_BYTES, "stderr": STDERR_BYTES}
        input_offset = 0
        while selector.get_map():
            remaining = remaining_deadline(deadline)
            ready = selector.select(remaining)
            remaining_deadline(deadline)
            for key, _events in ready:
                remaining_deadline(deadline)
                pipe = key.fileobj
                try:
                    if key.data == "stdin":
                        written = os.write(pipe.fileno(), input_bytes[input_offset:input_offset + 4096])
                        if written <= 0:
                            raise SourceHttpError("worker-io")
                        input_offset += written
                        if input_offset == len(input_bytes):
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        output = outputs[key.data]
                        part = os.read(pipe.fileno(), min(CHUNK_BYTES, limits[key.data] - len(output) + 1))
                        if len(output) + len(part) > limits[key.data]:
                            raise SourceHttpError("pipe-budget")
                        if part:
                            output.extend(part)
                        else:
                            selector.unregister(pipe)
                            pipe.close()
                except (BlockingIOError, InterruptedError):
                    continue
        remaining = remaining_deadline(deadline)
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise SourceHttpError("deadline") from None
        remaining_deadline(deadline)
        completed = True
        if type(returncode) is not int or returncode != 0:
            raise SourceHttpError("protocol")
        if outputs["stderr"]:
            raise SourceHttpError("worker-stderr")
        result = decode_envelope(bytes(outputs["stdout"]), maximum)
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, SourceHttpError):
            raise
        raise SourceHttpError("worker-io") from None
    finally:
        close_failed = False
        try:
            if selector is not None:
                selector.close()
        except OSError:
            close_failed = True
        try:
            if process is not None and not completed:
                _kill_and_reap(process)
        finally:
            if process is not None:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            close_failed = True
        if close_failed:
            raise SourceHttpError("worker-io") from None
    # Protocol decoding and every selector/pipe close have completed. Late
    # bytes are never a successful delivery, even when the worker was reaped.
    remaining_deadline(deadline)
    return result


def fetch(token: str, url: str, *, budget: str, deadline=None) -> bytes:
    """Deliver source bytes only before a fixed request/parent expiry."""
    deadline, owned = acquire_request_deadline(deadline)
    try:
        result = _fetch_request(token, url, budget=budget, deadline=deadline)
        finish_deadline(deadline, release_owned=owned)
    except BaseException:
        if owned:
            release_deadline(deadline)
        raise
    return result


def worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(INPUT_BYTES + 1)
        request = parse_input(raw)
        body = perform_request(request)
        if type(body) is not bytes or len(body) > BUDGETS[request["budget"]]:
            raise SourceHttpError("body-budget")
        result = ok_envelope(body)
    except SourceHttpError as error:
        result = error_envelope(error)
    except Exception:
        result = error_envelope(SourceHttpError("internal"))
    try:
        offset = 0
        while offset < len(result):
            chunk = result[offset:offset + CHUNK_BYTES]
            written = sys.stdout.buffer.write(chunk)
            if type(written) is not int or not 0 < written <= len(chunk):
                return 1
            offset += written
        sys.stdout.buffer.flush()
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(worker_main())
