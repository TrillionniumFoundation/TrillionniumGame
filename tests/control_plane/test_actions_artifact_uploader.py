from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/upload-actions-artifact.py"


def load_module():
    spec = importlib.util.spec_from_file_location("upload_actions_artifact", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_token(scope: str) -> str:
    def segment(value: object) -> str:
        encoded = base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return encoded.rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment({'scp': scope})}.signature"


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _maximum: int = -1) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status


class ActionsArtifactUploaderTests(unittest.TestCase):
    def test_backend_ids_require_one_exact_results_scope(self) -> None:
        module = load_module()
        token = runtime_token("Actions.Results:run-123:job-456")
        self.assertEqual(
            module.backend_ids_from_runtime_token(token),
            ("run-123", "job-456"),
        )
        for scope in [
            "",
            "Actions.Results:run-only",
            "Actions.Results:a:b Actions.Results:c:d",
            "Other.Scope:a:b",
        ]:
            with self.assertRaises(module.ArtifactUploadError):
                module.backend_ids_from_runtime_token(runtime_token(scope))

    def test_results_url_and_artifact_inputs_fail_closed(self) -> None:
        module = load_module()
        self.assertEqual(
            module.results_origin("https://results.example/path"),
            "https://results.example",
        )
        for value in ["http://results.example", "https://user@results.example", "not-a-url"]:
            with self.assertRaises(module.ArtifactUploadError):
                module.results_origin(value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.tar.gz"
            empty.write_bytes(b"")
            with self.assertRaises(module.ArtifactUploadError):
                module.validate_artifact("valid-name", empty)
            data = root / "data.tar.gz"
            data.write_bytes(b"evidence")
            with self.assertRaises(module.ArtifactUploadError):
                module.validate_artifact("bad name", data)
            if hasattr(os, "symlink"):
                link = root / "link.tar.gz"
                try:
                    link.symlink_to(data)
                except OSError:
                    pass
                else:
                    with self.assertRaises(module.ArtifactUploadError):
                        module.validate_artifact("valid-name", link)

    def test_upload_uses_twirp_snake_case_put_blob_and_sha256_finalize(self) -> None:
        module = load_module()
        requests = []
        responses = [
            FakeResponse(
                200,
                json.dumps({
                    "ok": True,
                    "signed_upload_url": "https://blob.example/upload?sig=secret-value",
                }).encode("utf-8"),
            ),
            FakeResponse(201),
            FakeResponse(
                200,
                json.dumps({"ok": True, "artifact_id": "12345"}).encode("utf-8"),
            ),
        ]

        def opener(request, timeout):
            self.assertEqual(timeout, 60)
            requests.append(request)
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.tar.gz"
            path.write_bytes(b"raw-evidence")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = module.upload_artifact(
                    "outbox-evidence",
                    path,
                    "application/gzip",
                    runtime_token=runtime_token("Actions.Results:run-id:job-id"),
                    actions_results_url="https://results.example/some/path",
                    opener=opener,
                    sleeper=lambda _seconds: None,
                )

        self.assertEqual(result["artifact_id"], "12345")
        self.assertEqual(result["size_bytes"], len(b"raw-evidence"))
        self.assertIn("::add-mask::secret-value", output.getvalue())
        self.assertEqual(len(requests), 3)
        self.assertTrue(requests[0].full_url.endswith("/CreateArtifact"))
        create = json.loads(requests[0].data)
        self.assertEqual(create["workflow_run_backend_id"], "run-id")
        self.assertEqual(create["workflow_job_run_backend_id"], "job-id")
        self.assertEqual(create["version"], 7)
        self.assertEqual(create["mime_type"], "application/gzip")
        self.assertEqual(set(create), {
            "workflow_run_backend_id", "workflow_job_run_backend_id",
            "name", "version", "mime_type",
        })
        self.assertEqual(requests[1].get_method(), "PUT")
        self.assertEqual(requests[1].data, b"raw-evidence")
        self.assertEqual(requests[1].headers["X-ms-blob-type"], "BlockBlob")
        self.assertTrue(requests[2].full_url.endswith("/FinalizeArtifact"))
        finalize = json.loads(requests[2].data)
        self.assertEqual(finalize["size"], str(len(b"raw-evidence")))
        self.assertEqual(finalize["hash"], f"sha256:{result['sha256']}")


if __name__ == "__main__":
    unittest.main()
