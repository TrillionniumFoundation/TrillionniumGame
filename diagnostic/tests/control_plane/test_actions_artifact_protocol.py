"""Exercise real uploader requests against a strict ProtoJSON service fixture.

No credentials, remote service or acceptance decision is used by these tests.
The expected field set follows GitHub's generated ArtifactService v1 messages;
StringValue wrappers use the scalar JSON representation, not {"value": ...}.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/upload-actions-artifact.py"


def module_at(path=SCRIPT):
    spec = importlib.util.spec_from_file_location("trnm_protocol_uploader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("uploader is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_token():
    payload = base64.urlsafe_b64encode(json.dumps({"scp": "Actions.Results:run-x:job-y"}).encode()).decode().rstrip("=")
    return "fixture." + payload + ".signature"


class Response:
    def __init__(self, status, body=b""):
        self.status, self.body = status, body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, maximum):
        return self.body[:maximum]


class StrictService:
    def __init__(self, mime, *, transient=False, fail_phase=None):
        self.mime = mime
        self.transient = transient
        self.fail_phase = fail_phase
        self.requests = []
        self.create_payloads = []
        self.data = b""
        self.final = None

    def __call__(self, request, timeout):
        assert timeout == 60
        self.requests.append(request)
        if request.full_url.endswith("/CreateArtifact"):
            payload = json.loads(request.data)
            self.create_payloads.append(payload)
            expected = {"workflow_run_backend_id", "workflow_job_run_backend_id", "name", "version", "mime_type"}
            if set(payload) != expected or type(payload["mime_type"]) is not str:
                raise urllib.error.HTTPError(request.full_url, 400, "invalid ProtoJSON field", None, None)
            assert payload["mime_type"] == self.mime
            assert payload["version"] == 7 and type(payload["version"]) is int
            assert payload["workflow_run_backend_id"] == "run-x"
            assert payload["workflow_job_run_backend_id"] == "job-y"
            assert request.headers.get("Authorization") == "Bearer " + test_token()
            if self.fail_phase == "create":
                raise urllib.error.HTTPError(request.full_url, 400, "redacted error", None, None)
            if self.transient and len(self.create_payloads) == 1:
                return Response(503)
            return Response(200, json.dumps({"ok": True, "signed_upload_url": "https://blob.example.invalid/item?sig=fixture-secret"}).encode())
        if request.get_method() == "PUT":
            assert not any(k.lower() == "authorization" for k in request.headers)
            assert request.headers["Content-type"] == self.mime
            self.data = request.data
            assert request.headers["Content-length"] == str(len(self.data))
            assert request.headers["X-ms-blob-type"] == "BlockBlob"
            if self.fail_phase == "put":
                return Response(400)
            return Response(201)
        if request.full_url.endswith("/FinalizeArtifact"):
            assert self.data
            self.final = json.loads(request.data)
            assert set(self.final) == {"workflow_run_backend_id", "workflow_job_run_backend_id", "name", "size", "hash"}
            assert self.final["size"] == str(len(self.data))
            assert self.final["hash"] == "sha256:" + hashlib.sha256(self.data).hexdigest()
            if self.fail_phase == "finalize":
                return Response(200, b'{"ok":false}')
            return Response(200, b'{"ok":true,"artifact_id":"725"}')
        raise AssertionError("unexpected request")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.module = module_at()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "packet.zip"
        self.path.write_bytes(b"nonempty fixture archive bytes")

    def upload(self, service, mime="application/zip"):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.module.upload_artifact("review-packet", self.path, mime,
                runtime_token=test_token(), actions_results_url="https://results.example.invalid",
                opener=service, sleeper=lambda _: None)

    def test_zip_uses_top_level_scalar_mime_and_real_finalize(self):
        service = StrictService("application/zip")
        result = self.upload(service)
        self.assertEqual(result["artifact_id"], "725")
        self.assertEqual(service.data, self.path.read_bytes())
        self.assertEqual(result["sha256"], hashlib.sha256(service.data).hexdigest())
        self.assertEqual(len(service.requests), 3)

    def test_gzip_uses_same_protocol_not_a_separate_legacy_route(self):
        service = StrictService("application/gzip")
        self.upload(service, "application/gzip")
        self.assertEqual(service.create_payloads[0]["mime_type"], "application/gzip")

    def test_transient_retry_preserves_exact_create_fields(self):
        service = StrictService("application/zip", transient=True)
        self.upload(service)
        self.assertEqual(len(service.create_payloads), 2)
        self.assertEqual(service.create_payloads[0], service.create_payloads[1])

    def test_permanent_create_failure_has_no_fallback_or_put(self):
        service = StrictService("application/zip", fail_phase="create")
        with self.assertRaises(self.module.ArtifactUploadError):
            self.upload(service)
        self.assertEqual(len(service.requests), 1)
        self.assertFalse(service.data)

    def test_failed_put_never_finalizes(self):
        service = StrictService("application/zip", fail_phase="put")
        with self.assertRaises(self.module.ArtifactUploadError):
            self.upload(service)
        self.assertEqual(len(service.requests), 2)
        self.assertIsNone(service.final)

    def test_failed_finalize_returns_no_upload_receipt(self):
        service = StrictService("application/zip", fail_phase="finalize")
        with self.assertRaises(self.module.ArtifactUploadError):
            self.upload(service)
        self.assertIsNotNone(service.final)

    def test_signed_blob_put_never_receives_runtime_authorization(self):
        service = StrictService("application/zip")
        self.upload(service)
        self.assertNotIn("Authorization", service.requests[1].headers)
        self.assertIn("Authorization", service.requests[2].headers)

    def test_empty_artifact_prevents_every_network_call(self):
        self.path.write_bytes(b"")
        service = StrictService("application/zip")
        with self.assertRaises(self.module.ArtifactUploadError):
            self.upload(service)
        self.assertEqual(service.requests, [])

    def test_invalid_mime_prevents_every_network_call(self):
        service = StrictService("application/zip")
        with self.assertRaises(self.module.ArtifactUploadError):
            self.upload(service, "application/zip\nX-Injected: yes")
        self.assertEqual(service.requests, [])

    def test_legacy_nested_shape_is_rejected_by_strict_fixture(self):
        import urllib.request
        service = StrictService("application/zip")
        request = urllib.request.Request("https://results.example.invalid/CreateArtifact",
            data=json.dumps({"workflow_run_backend_id":"run-x", "workflow_job_run_backend_id":"job-y", "name":"review-packet", "version":7, "metadata":{"wrapper":{"mime_type":"application/zip"}}}).encode())
        with self.assertRaises(urllib.error.HTTPError) as raised:
            service(request, 60)
        self.assertEqual(raised.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
