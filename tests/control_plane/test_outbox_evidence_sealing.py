"""Regression coverage for final-attempt outbox evidence sealing."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci-outbox-final-attempt-reaper.sh"
WORKFLOW = ROOT / ".github/workflows/prospective-merge-gate.yml"


class OutboxEvidenceSealingTests(unittest.TestCase):
    def test_logger_is_joined_before_manifest_generation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        ordered = (
            "exec 3>&1 4>&2",
            'exec > >(tee "$evidence/logs/run.log" >&3) 2>&1',
            "tee_pid=$!",
            'cat "$evidence/result.env"',
            "exec 1>&3 2>&4",
            "exec 3>&- 4>&-",
            'wait "$tee_pid"',
            "find . -type f ! -path './files.sha256' -print0",
            "sha256sum --check files.sha256",
        )
        positions = []
        for marker in ordered:
            self.assertEqual(source.count(marker), 1, marker)
            positions.append(source.index(marker))
        self.assertEqual(positions, sorted(positions))

    def test_prospective_packet_checks_outbox_member_manifest(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        required = 'test -s "$outbox/files.sha256"'
        verified = '(cd "$outbox" && sha256sum --check files.sha256)'
        archive = 'archive="run/prospective-merge-${PROFILE}-${PROSPECTIVE_MERGE_SHA}.tar.gz"'
        self.assertEqual(source.count(required), 1)
        self.assertEqual(source.count(verified), 1)
        self.assertLess(source.index(required), source.index(verified))
        self.assertLess(source.index(verified), source.index(archive))

    @unittest.skipUnless(
        shutil.which("bash") and shutil.which("sha256sum"),
        "requires bash and GNU sha256sum",
    )
    def test_source_derived_seal_is_stable_and_fail_closed(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        setup_start = source.index("exec 3>&1 4>&2")
        setup_end = source.index("\n\ncontainer=", setup_start)
        setup = source[setup_start:setup_end]
        tail_start = source.index("printf 'status=passed\\nprofile=%s\\ncommit=%s\\n'")
        tail = source[tail_start:]

        with TemporaryDirectory() as directory:
            evidence = Path(directory) / "packet"
            evidence.mkdir()
            (evidence / "logs").mkdir()
            harness = "\n".join(
                (
                    "set -Eeuo pipefail",
                    "profile=postgresql",
                    f"commit={'a' * 40}",
                    f"evidence={str(evidence)!r}",
                    setup,
                    "printf 'fixture-log\\n'",
                    tail,
                )
            )
            completed = subprocess.run(
                ["bash", "-c", harness],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            manifest = evidence / "files.sha256"
            lines = manifest.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines)
            names = [line.split("  ", 1)[1] for line in lines]
            self.assertEqual(names, sorted(names))
            self.assertNotIn("./files.sha256", names)
            self.assertEqual(names, ["./logs/run.log", "./result.env"])

            for line in lines:
                expected, relative = line.split("  ", 1)
                actual = hashlib.sha256((evidence / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)

            with (evidence / "logs/run.log").open("ab") as stream:
                stream.write(b"post-seal mutation\n")
            rejected = subprocess.run(
                ["sha256sum", "--check", "files.sha256"],
                cwd=evidence,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("FAILED", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
