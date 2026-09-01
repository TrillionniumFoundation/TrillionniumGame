from __future__ import annotations

import base64
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-actions-log-artifact-github.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location(
        "verify_actions_log_artifact_github", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load GitHub transport adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = load_adapter()


class GitHubContentsAdapterTests(unittest.TestCase):
    def test_line_wrapped_repository_content_is_normalized(self) -> None:
        expected = b"migration-lock\n"
        encoded = base64.b64encode(expected).decode("ascii")
        wrapped = "\n".join(
            encoded[index : index + 5] for index in range(0, len(encoded), 5)
        )
        payload = {"type": "file", "encoding": "base64", "content": wrapped}
        self.assertEqual(ADAPTER.decode_contents(payload, "lock"), expected)

    def test_non_whitespace_corruption_still_fails_closed(self) -> None:
        payload = {"type": "file", "encoding": "base64", "content": "YWJj$A=="}
        with self.assertRaises(ADAPTER.CORE.VerificationError):
            ADAPTER.decode_contents(payload, "lock")

    def test_non_file_transport_still_fails_closed(self) -> None:
        payload = {"type": "dir", "encoding": "base64", "content": "YWJj"}
        with self.assertRaises(ADAPTER.CORE.VerificationError):
            ADAPTER.decode_contents(payload, "lock")


class GitHubLogRedirectTests(unittest.TestCase):
    def test_signed_cross_origin_request_contains_no_repository_token(self) -> None:
        location = (
            "https://results.example.blob.core.windows.net/actions/log.txt"
            "?sv=2026-01-01&sig=signed"
        )
        request = ADAPTER._unsigned_redirect_request(location)
        self.assertEqual(request.full_url, location)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("X-github-api-version"))
        self.assertEqual(request.get_header("Accept"), "application/octet-stream")

    def test_github_api_request_contains_the_repository_token(self) -> None:
        request = ADAPTER._github_log_request(
            "secret-token", "https://api.github.com/repos/o/r/actions/jobs/1/logs"
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")

    def test_redirect_rejects_plaintext_credentials_and_fragments(self) -> None:
        invalid = [
            "http://storage.example/log.txt?sig=x",
            "https://user:password@storage.example/log.txt?sig=x",
            "https://storage.example/log.txt?sig=x#fragment",
            "not-a-url",
        ]
        for location in invalid:
            with self.subTest(location=location):
                with self.assertRaises(ADAPTER.CORE.VerificationError):
                    ADAPTER._unsigned_redirect_request(location)

    def test_redacted_location_drops_signed_query(self) -> None:
        location = "https://storage.example/path/log.txt?sig=secret&se=tomorrow"
        self.assertEqual(
            ADAPTER._redacted_location(location),
            "https://storage.example/path/log.txt",
        )


if __name__ == "__main__":
    unittest.main()
