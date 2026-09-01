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
        raise RuntimeError("cannot load GitHub Contents adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = load_adapter()


class GitHubContentsAdapterTests(unittest.TestCase):
    def test_line_wrapped_repository_content_is_normalized(self) -> None:
        expected = b"migration-lock\n"
        encoded = base64.b64encode(expected).decode("ascii")
        wrapped = "\n".join(encoded[index : index + 5] for index in range(0, len(encoded), 5))
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


if __name__ == "__main__":
    unittest.main()
