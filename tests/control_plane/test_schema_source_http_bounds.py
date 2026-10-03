"""Source custody transport budgets; urllib responses are always pure mocks."""
from __future__ import annotations

import base64
import copy
import http.client
import importlib.util
import io
import json
import socket
import unittest
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/control_plane"))
from source_http_mock import mock_source_process
SPEC = importlib.util.spec_from_file_location("bounded_source_http", ROOT / "scripts/verify-actions-log-artifact.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HEAD = "a" * 40
TREE = "b" * 40
REPOSITORY = "TrillionniumFoundation/TrillionniumGame"
URL = "https://api.github.com/repos/" + REPOSITORY + "/git/commits/" + HEAD


class Response(io.BytesIO):
    def __init__(self, raw: bytes, headers=None):
        super().__init__(raw)
        self.headers = {} if headers is None else headers
        self.read_sizes = []

    def read(self, size=-1):
        if size < 0:
            raise AssertionError("unbounded transport read")
        self.read_sizes.append(size)
        return super().read(size)


def content(raw=b"abc", path="migrations/postgresql/0001_initial_up.sql"):
    return {"path": path, "type": "file", "encoding": "base64", "size": len(raw),
            "sha": MODULE.git_blob_sha1(raw), "content": base64.b64encode(raw).decode("ascii")}


class SourceHttpBoundsTests(unittest.TestCase):
    def request(self, raw, *, maximum=None, headers=None):
        response = Response(raw, headers)
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", return_value=response) as opened:
            try:
                result = MODULE.request_source_json("mock-secret-token", URL,
                    maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES if maximum is None else maximum)
            finally:
                self.assertTrue(response.closed)
                self.assertEqual(opened.call_args.kwargs, {"timeout": MODULE.SOURCE_HTTP_TIMEOUT_SECONDS})
                self.assertEqual(opened.call_count, 1)
                self.last_response = response
        return result

    def documents(self):
        token = MODULE.BINDING.verify_binding(ROOT, profile="postgresql")
        files = {row["path"]: (ROOT / row["path"]).read_bytes()
                 for row in MODULE.BINDING.binding_document(token)["full_source_inventory"]}
        tree = {"sha": TREE, "truncated": False, "tree": [
            {"path": path, "type": "blob", "mode": "100644", "size": len(raw),
             "sha": MODULE.git_blob_sha1(raw)} for path, raw in files.items()]}
        return files, tree

    def fetch(self, files, tree, *, include_commit=False):
        responses = []
        def open_mock(request, timeout):
            self.assertEqual(timeout, MODULE.SOURCE_HTTP_TIMEOUT_SECONDS)
            parsed = urllib.parse.urlsplit(request.full_url)
            self.assertEqual(parsed.netloc, "api.github.com")
            if parsed.path.endswith("/git/commits/" + HEAD):
                document = {"sha": HEAD, "tree": {"sha": TREE}}
            elif parsed.path.endswith("/git/trees/" + TREE):
                self.assertEqual(parsed.query, "recursive=1")
                document = tree
            else:
                self.assertEqual(urllib.parse.parse_qs(parsed.query), {"ref": [HEAD]})
                path = urllib.parse.unquote(parsed.path.split("/contents/", 1)[1])
                document = content(files[path], path)
            raw = json.dumps(document, separators=(",", ":")).encode()
            response = Response(raw, {"Content-Length": str(len(raw))})
            responses.append(response)
            return response
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=open_mock):
            try:
                return MODULE.fetch_profile_bindings("mock-token", REPOSITORY, HEAD,
                    head_tree=None if include_commit else TREE)
            finally:
                self.assertTrue(all(r.closed for r in responses))
                self.responses = responses

    def test_valid_json_is_read_with_maximum_plus_one_and_closed_before_parse(self):
        response = Response(b'{"ok":true}', {"Content-Length": "11"})
        loads = MODULE.json.loads
        def closed_parser(*args, **kwargs):
            self.assertTrue(response.closed)
            return loads(*args, **kwargs)
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", return_value=response) as opened, \
                mock.patch.object(MODULE.json, "loads", side_effect=closed_parser):
            self.assertEqual(MODULE.request_source_json("mock-token", URL, maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES), {"ok": True})
        self.assertEqual(opened.call_args.kwargs, {"timeout": MODULE.SOURCE_HTTP_TIMEOUT_SECONDS})
        self.last_response = response
        self.assertEqual(self.last_response.read_sizes, [MODULE.MAX_SOURCE_FILE_JSON_BYTES + 1])

    def test_body_exact_http_budget_is_allowed(self):
        raw = b'{"ok":true}' + b" " * (MODULE.MAX_SOURCE_FILE_JSON_BYTES - 11)
        self.assertEqual(self.request(raw), {"ok": True})

    def test_body_one_over_budget_rejected_before_json_parser(self):
        raw = b"x" * (MODULE.MAX_SOURCE_FILE_JSON_BYTES + 500)
        with mock.patch.object(MODULE.json, "loads", side_effect=AssertionError("must not parse over-budget body")):
            with self.assertRaisesRegex(MODULE.VerificationError, "HTTP byte budget"):
                self.request(raw)
        self.assertEqual(self.last_response.read_sizes, [MODULE.MAX_SOURCE_FILE_JSON_BYTES + 1])

    def test_oversized_content_length_rejected_without_body_read(self):
        with self.assertRaisesRegex(MODULE.VerificationError, "HTTP byte budget"):
            self.request(b"{}", headers={"Content-Length": str(MODULE.MAX_SOURCE_FILE_JSON_BYTES + 1)})
        self.assertEqual(self.last_response.read_sizes, [])

    def test_malformed_length_or_encoded_http_body_rejected_and_closed(self):
        for headers in ({"Content-Length": "-1"}, {"Content-Length": "1.0"},
                        {"Content-Length": "9" * 21}, {"Content-Length": 2},
                        {"Content-Encoding": "gzip"}):
            with self.subTest(headers=headers), self.assertRaises(MODULE.VerificationError):
                self.request(b"{}", headers=headers)
            self.assertEqual(self.last_response.read_sizes, [])

    def test_truncated_length_rejected_before_parse(self):
        with self.assertRaisesRegex(MODULE.VerificationError, "truncated"):
            self.request(b"{}", headers={"Content-Length": "3"})

    def test_duplicate_nested_keys_and_nonfinite_numbers_are_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"nested":{"x":1,"x":2}}',
                    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(MODULE.VerificationError):
                self.request(raw)

    def test_invalid_utf8_depth_and_nonobject_json_are_rejected(self):
        for raw in (b'\xff', b'{"x":', b'[]', b'null', b'{"x":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'):
            with self.subTest(size=len(raw)), self.assertRaises(MODULE.VerificationError):
                self.request(raw)

    def test_json_depth_budget_is_exact_and_quoted_escaped_brackets_do_not_count(self):
        for depth in (MODULE.MAX_SOURCE_JSON_DEPTH, MODULE.MAX_SOURCE_JSON_DEPTH + 1):
            raw = b'{"x":' + b'[' * (depth - 1) + b'0' + b']' * (depth - 1) + b'}'
            if depth == MODULE.MAX_SOURCE_JSON_DEPTH:
                self.assertIn("x", self.request(raw))
            else:
                with self.assertRaises(MODULE.VerificationError):
                    self.request(raw)
        text = '"[\\' * 1000
        self.assertEqual(self.request(json.dumps({"x": text}).encode()), {"x": text})

    def test_unreviewed_http_budget_rejected_before_transport(self):
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=AssertionError("transport forbidden")):
            for maximum in (True, 0, 1, MODULE.MAX_SOURCE_TREE_JSON_BYTES + 1):
                with self.subTest(maximum=maximum), self.assertRaises(MODULE.VerificationError):
                    MODULE.request_source_json("token", URL, maximum=maximum)

    def test_http_error_body_is_bounded_and_closed_without_remote_diagnostic(self):
        response = Response(b"remote-private-diagnostic")
        error = urllib.error.HTTPError(URL, 403, "Forbidden", response.headers, response)
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=error):
            with self.assertRaisesRegex(MODULE.VerificationError, "HTTP 403") as raised:
                MODULE.request_source_json("mock-secret-token", URL, maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES)
        self.assertNotIn("remote-private", str(raised.exception))
        self.assertNotIn("mock-secret", str(raised.exception))
        self.assertEqual(response.read_sizes, [MODULE.MAX_SOURCE_ERROR_BYTES + 1])
        self.assertTrue(response.closed)

    def test_oversized_error_body_and_header_fail_closed_with_bounded_read(self):
        for header in ({}, {"Content-Length": str(MODULE.MAX_SOURCE_ERROR_BYTES + 1)}):
            response = Response(b"x" * (MODULE.MAX_SOURCE_ERROR_BYTES + 100), header)
            error = urllib.error.HTTPError(URL, 500, "Internal", header, response)
            with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=error):
                with self.assertRaisesRegex(MODULE.VerificationError, "HTTP byte budget"):
                    MODULE.request_source_json("token", URL, maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES)
            self.assertTrue(response.closed)
            self.assertEqual(response.read_sizes, [] if header else [MODULE.MAX_SOURCE_ERROR_BYTES + 1])

    def test_error_body_without_headers_or_with_read_failure_is_closed(self):
        for read_error in (None, socket.timeout("error-body timeout"), http.client.IncompleteRead(b"partial")):
            response = Response(b"{}")
            error = urllib.error.HTTPError(URL, 403, "Forbidden", None, response)
            with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=error), \
                    mock.patch.object(response, "read", side_effect=read_error, wraps=response.read) as read:
                with self.subTest(error=type(read_error)), self.assertRaisesRegex(MODULE.VerificationError, "HTTP 403"):
                    MODULE.request_source_json("token", URL, maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES)
                self.assertEqual(read.call_args.args, (MODULE.MAX_SOURCE_ERROR_BYTES + 1,))
            self.assertTrue(response.closed)

    def test_url_errors_are_finite_single_attempt_failures(self):
        for error in (urllib.error.URLError("mock failure"), socket.timeout("timeout"), http.client.IncompleteRead(b"partial")):
            with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=error) as opened:
                with self.subTest(error=type(error)), self.assertRaises(MODULE.VerificationError):
                    MODULE.request_source_json("token", URL, maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES)
                self.assertEqual(opened.call_count, 1)

    def test_read_timeout_or_partial_http_failure_closes_response_without_retry(self):
        for error in (socket.timeout("read timeout"), http.client.IncompleteRead(b"partial")):
            response = Response(b"")
            with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(response, "read", side_effect=error) as read, \
                    mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", return_value=response) as opened:
                with self.subTest(error=type(error)), self.assertRaises(MODULE.VerificationError):
                    MODULE.request_source_json("token", URL, maximum=MODULE.MAX_SOURCE_FILE_JSON_BYTES)
                self.assertEqual(read.call_args.args, (MODULE.MAX_SOURCE_FILE_JSON_BYTES + 1,))
                self.assertEqual(opened.call_count, 1)
            self.assertTrue(response.closed)

    def test_declared_file_size_exact_integer_budget_before_base64_decode(self):
        payload = content()
        with mock.patch.object(MODULE.base64, "b64decode", side_effect=AssertionError("decode must not run")):
            for size in (True, False, 3.0, "3", -1, None, MODULE.BINDING.MAX_FILE_BYTES + 1):
                with self.subTest(size=size), self.assertRaisesRegex(MODULE.VerificationError, "declared"):
                    MODULE.decode_source_contents(payload | {"size": size}, payload["path"], maximum=MODULE.BINDING.MAX_FILE_BYTES)

    def test_encoded_length_budget_checked_before_whitespace_removal_or_decode(self):
        payload = content()
        with mock.patch.object(MODULE.base64, "b64decode", side_effect=AssertionError("decode must not run")):
            for encoded in ("A" * 10000, "\n" * 10000, "AAAAAA", "AAAA\n\n"):
                with self.subTest(length=len(encoded)), self.assertRaises(MODULE.VerificationError):
                    MODULE.decode_source_contents(payload | {"content": encoded}, payload["path"], maximum=3)

    def test_base64_padding_declared_size_prevents_decoded_overrun(self):
        payload = content(b"ab")
        with mock.patch.object(MODULE.base64, "b64decode", side_effect=AssertionError("decode must not run")):
            with self.assertRaisesRegex(MODULE.VerificationError, "padding"):
                MODULE.decode_source_contents(payload | {"content": "YWJj"}, payload["path"], maximum=2)

    def test_maximum_file_decodes_only_within_exact_size_and_http_budget(self):
        raw = b"x" * MODULE.BINDING.MAX_FILE_BYTES
        payload = content(raw)
        encoded = payload["content"]
        payload["content"] = "\n".join(encoded[i:i + 60] for i in range(0, len(encoded), 60)) + "\n"
        self.assertLess(len(json.dumps(payload).encode()), MODULE.MAX_SOURCE_FILE_JSON_BYTES)
        self.assertEqual(MODULE.decode_source_contents(payload, payload["path"], maximum=len(raw)), raw)

    def test_invalid_base64_and_noncanonical_pad_bits_fail_closed(self):
        payload = content(b"ab")
        for encoded in ("!!!!", "YWL=", "YWJ=", "YW\xff=", "YW I=", "YWI\r"):
            with self.subTest(encoded=encoded), self.assertRaises(MODULE.VerificationError):
                MODULE.decode_source_contents(payload | {"content": encoded}, payload["path"], maximum=2)

    def test_file_path_type_and_encoding_are_bound(self):
        payload = content()
        for field, value in (("path", "../different"), ("type", "symlink"), ("encoding", "none"), ("content", True), ("content", "")):
            with self.subTest(field=field), self.assertRaises(MODULE.VerificationError):
                MODULE.decode_source_contents(payload | {field: value}, payload["path"], maximum=3)

    def test_remaining_annex_budget_is_applied_before_decode(self):
        payload = content(b"abc")
        with mock.patch.object(MODULE.base64, "b64decode", side_effect=AssertionError("decode must not run")):
            with self.assertRaises(MODULE.VerificationError):
                MODULE.decode_source_contents(payload, payload["path"], maximum=2)

    def test_blob_mismatch_rejected_after_bounded_http_read(self):
        payload = content() | {"sha": "c" * 40}
        response = Response(json.dumps(payload).encode())
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", return_value=response):
            with self.assertRaisesRegex(MODULE.VerificationError, "Git blob"):
                MODULE.fetch_source_exact_file("token", REPOSITORY, HEAD, payload["path"], maximum=3)
        self.assertTrue(response.closed)
        self.assertEqual(response.read_sizes, [MODULE.MAX_SOURCE_FILE_JSON_BYTES + 1])

    def test_full_tree_and_commit_fetch_use_actual_bounded_json_helpers(self):
        files, tree = self.documents()
        bindings = self.fetch(files, tree, include_commit=True)
        self.assertEqual(set(bindings), set(MODULE.PROFILES))
        self.assertEqual(len(self.responses), 20)
        for profile, token in bindings.items():
            proof = MODULE.BINDING.binding_document(token)
            self.assertEqual(proof["full_source_inventory_count"], 18)
            self.assertEqual(proof["source_selection"]["source"]["profiles"][profile]["file_count"], 5)
            self.assertEqual(len(MODULE.BINDING.operational_binding(token)["ordered_files"]), 4)
            self.assertFalse(proof["claims"]["accepted"])

    def test_tree_exact_50000_entries_fits_budget_and_keeps_all18_source_files(self):
        files, tree = self.documents()
        tree["tree"].extend({"path": "x/" + str(i), "sha": "c" * 40, "mode": "100644", "type": "blob", "size": 0}
                            for i in range(MODULE.MAX_SOURCE_TREE_ENTRIES - len(tree["tree"])))
        self.assertLess(len(json.dumps(tree, separators=(",", ":")).encode()), MODULE.MAX_SOURCE_TREE_JSON_BYTES)
        bindings = self.fetch(files, tree)
        self.assertEqual(len(bindings), 2)
        self.assertEqual(self.responses[0].read_sizes, [MODULE.MAX_SOURCE_TREE_JSON_BYTES + 1])

    def test_tree_50001_entries_rejected_before_any_contents_fetch(self):
        files, tree = self.documents()
        tree["tree"].extend({"path": "x/" + str(i)} for i in range(MODULE.MAX_SOURCE_TREE_ENTRIES + 1 - len(tree["tree"])))
        with self.assertRaisesRegex(MODULE.VerificationError, "oversized"):
            self.fetch(files, tree)
        self.assertEqual(len(self.responses), 1)

    def test_tree_body_budget_rejected_before_json_parse(self):
        response = Response(b"x" * (MODULE.MAX_SOURCE_TREE_JSON_BYTES + 1))
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", return_value=response), \
                mock.patch.object(MODULE.json, "loads", side_effect=AssertionError("tree parse forbidden")):
            with self.assertRaisesRegex(MODULE.VerificationError, "HTTP byte budget"):
                MODULE.fetch_profile_bindings("token", REPOSITORY, HEAD, head_tree=TREE)
        self.assertEqual(response.read_sizes, [MODULE.MAX_SOURCE_TREE_JSON_BYTES + 1])
        self.assertTrue(response.closed)

    def test_tree_size_types_blob_modes_and_denominator_are_not_relaxed(self):
        files, tree = self.documents()
        for field, value in (("size", True), ("size", -1), ("size", MODULE.BINDING.MAX_FILE_BYTES + 1),
                             ("sha", "D" * 40), ("mode", "120000"), ("type", "tree")):
            altered = copy.deepcopy(tree)
            altered["tree"][0][field] = value
            with self.subTest(field=field), self.assertRaises(MODULE.VerificationError):
                self.fetch(files, altered)
            self.assertEqual(len(self.responses), 1)
        for altered in (tree | {"truncated": 0}, tree | {"sha": "c" * 40}, tree | {"tree": tree["tree"][:-1]},
                        tree | {"tree": tree["tree"] + [tree["tree"][0]]}):
            with self.assertRaises(MODULE.VerificationError):
                self.fetch(files, altered)
            self.assertEqual(len(self.responses), 1)

    def test_total_declared_annex_budget_rejected_before_any_large_download(self):
        files, tree = self.documents()
        paths = sorted(files)
        for path in paths[:5]:
            files[path] = b"x" * MODULE.BINDING.MAX_FILE_BYTES
        for entry in tree["tree"]:
            entry["size"] = len(files[entry["path"]])
            entry["sha"] = MODULE.git_blob_sha1(files[entry["path"]])
        with self.assertRaisesRegex(MODULE.VerificationError, "declared byte bound"):
            self.fetch(files, tree)
        self.assertEqual(len(self.responses), 1)  # Only the bounded tree response.

    def test_invalid_head_or_tree_rejected_before_any_remote_request(self):
        with mock_source_process(MODULE.SOURCE_HTTP), mock.patch.object(MODULE.SOURCE_HTTP, "open_source_url", side_effect=AssertionError("transport forbidden")):
            for head, tree in ((True, TREE), ("A" * 40, TREE), (HEAD, True), (HEAD, "B" * 40)):
                with self.subTest(head=head, tree=tree), self.assertRaises(MODULE.VerificationError):
                    MODULE.fetch_profile_bindings("token", REPOSITORY, head, head_tree=tree)


if __name__ == "__main__":
    unittest.main()
