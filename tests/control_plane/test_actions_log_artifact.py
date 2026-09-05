from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/emit-actions-log-artifact.py"


class ActionsLogArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("emit_actions_log_artifact", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @staticmethod
    def timestamped(text: str, *, prefix: str = "2026-08-31T15:42:03") -> str:
        lines = text.splitlines()
        body = [
            f"{prefix}.{index:07d}Z {line}"
            for index, line in enumerate(lines, 1)
        ]
        return "\n".join(
            [
                "2026-08-31T15:42:02.0000001Z unrelated-before",
                *body,
                "2026-08-31T15:42:04.0000001Z unrelated-after",
            ]
        ) + "\n"

    def test_bare_binary_round_trip_preserves_size_sha_and_style(self) -> None:
        data = bytes(range(256)) * 7 + b"\x00\xfftrillionnium"
        text, metadata = self.module.envelope(
            data, "evidence-archive.tar.gz", len(data)
        )
        decoded, parsed = self.module.parse(text)
        self.assertEqual(decoded, data)
        self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(metadata["log_style"], self.module.BARE_LOG_STYLE)
        self.assertEqual(parsed["log_style"], self.module.BARE_LOG_STYLE)
        self.assertEqual(parsed["sha256"], metadata["sha256"])
        self.assertEqual(parsed["size"], len(data))
        self.assertGreater(parsed["data_line_count"], 1)

    def test_realistic_github_timestamped_log_round_trip(self) -> None:
        data = b"retained service bytes" * 17
        bare, _ = self.module.envelope(data, "postgresql.tar.gz", 4096)
        retained = self.timestamped(bare)
        decoded, metadata = self.module.parse(retained)
        self.assertEqual(decoded, data)
        self.assertEqual(metadata["log_style"], self.module.GITHUB_LOG_STYLE)
        self.assertEqual(metadata["source_begin_line"], 2)
        self.assertEqual(
            metadata["source_end_line"],
            len(bare.splitlines()) + 1,
        )
        self.assertRegex(
            str(metadata["first_log_timestamp"]),
            r"^2026-08-31T15:42:03\.\d{7}Z$",
        )

    def test_empty_oversized_and_unsafe_names_fail_closed(self) -> None:
        with self.assertRaises(self.module.EnvelopeError):
            self.module.envelope(b"", "empty.tar.gz", 1)
        with self.assertRaises(self.module.EnvelopeError):
            self.module.envelope(b"ab", "large.tar.gz", 1)
        with self.assertRaises(self.module.EnvelopeError):
            self.module.envelope(b"a", "unsafe name", 1)

    def test_missing_duplicate_corrupt_and_noncanonical_chunks_fail(self) -> None:
        text, _ = self.module.envelope(b"archive" * 40, "archive.tar.gz", 4096)
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(text.replace("TRNM_LOG_ARTIFACT_END", "MISSING_END"))
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(text + text)
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(text.replace("YXJjaGl2", "YXJjaGl="))
        lines = text.splitlines()
        lines[1] = lines[1][:-1]
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse("\n".join(lines) + "\n")

    def test_mixed_prefix_malformed_timestamp_and_forged_marker_fail(self) -> None:
        text, _ = self.module.envelope(b"archive", "archive.tar.gz", 100)
        retained = self.timestamped(text)
        mixed = retained.replace(
            "2026-08-31T15:42:03.0000002Z TRNM_LOG_ARTIFACT_B64 ",
            "TRNM_LOG_ARTIFACT_B64 ",
            1,
        )
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(mixed)
        malformed = retained.replace(
            "2026-08-31T15:42:03.0000001Z",
            "2026-08-31T15:42:03.000001Z",
            1,
        )
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(malformed)
        forged = retained.replace(
            "2026-08-31T15:42:02.0000001Z unrelated-before",
            "2026-08-31T15:42:02.0000001Z prefix TRNM_LOG_ARTIFACT_BEGIN forged",
        )
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(forged)

    def test_interrupted_outside_and_nonmonotonic_envelopes_fail(self) -> None:
        text, _ = self.module.envelope(b"archive" * 20, "archive.tar.gz", 4096)
        retained = self.timestamped(text)
        lines = retained.splitlines()
        lines.insert(3, "2026-08-31T15:42:03.0000003Z unrelated-interruption")
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse("\n".join(lines) + "\n")
        outside = retained + (
            "2026-08-31T15:42:05.0000001Z "
            "TRNM_LOG_ARTIFACT_B64 YQ==\n"
        )
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(outside)
        reversed_lines = retained.replace(
            "15:42:03.0000002Z", "15:42:03.0000000Z", 1
        )
        with self.assertRaises(self.module.EnvelopeError):
            self.module.parse(reversed_lines)

    def test_cli_writes_bound_metadata_and_reconstructable_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.tar.gz"
            metadata_path = root / "metadata.json"
            archive.write_bytes(b"deterministic archive bytes")
            stdout = io.StringIO()
            stderr = io.StringIO()
            environment = {
                "GITHUB_REPOSITORY": "TrillionniumFoundation/TrillionniumGame",
                "CANDIDATE_SHA": "a" * 40,
                "GITHUB_RUN_ID": "123",
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_JOB": "live-profile",
            }
            arguments = [
                str(SCRIPT),
                str(archive),
                "--name",
                "archive.tar.gz",
                "--max-bytes",
                "1024",
                "--metadata-json",
                str(metadata_path),
            ]
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch(
                "sys.argv", arguments
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = self.module.main()
            self.assertEqual(status, 0, stderr.getvalue())
            decoded, parsed = self.module.parse(stdout.getvalue())
            self.assertEqual(decoded, archive.read_bytes())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["sha256"], parsed["sha256"])
            self.assertEqual(metadata["log_style"], self.module.BARE_LOG_STYLE)
            self.assertEqual(metadata["repository"], environment["GITHUB_REPOSITORY"])
            self.assertEqual(metadata["commit"], environment["CANDIDATE_SHA"])
            self.assertEqual(metadata["run_id"], "123")
            self.assertFalse(metadata["claims"]["compatibility_credit"])
            self.assertFalse(metadata["claims"]["production_ready"])


if __name__ == "__main__":
    unittest.main()
