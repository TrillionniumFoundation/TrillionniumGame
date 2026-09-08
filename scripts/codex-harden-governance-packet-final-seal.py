#!/usr/bin/env python3
"""Harden final packet sealing after the descriptor-relative custody patch."""
from pathlib import Path

source_path = Path("scripts/capture-repository-governance-readback.py")
test_path = Path("tests/control_plane/test_capture_repository_governance_readback.py")
source = source_path.read_text(encoding="utf-8")

replacements = [
    (
        '''        if name == "SHA256SUMS":
            expected = "".join(
                f"{record['sha256']}  {member}\\n"
                for member, record in sorted(self.records.items())
            ).encode()
            if data != expected:
                raise ReadbackError("SHA256SUMS does not match verified packet members")
''',
        '''        if name == "SHA256SUMS":
            for member in sorted(self.records):
                self._read_verified(member, allow_manifest=False)
            expected = "".join(
                f"{record['sha256']}  {member}\\n"
                for member, record in sorted(self.records.items())
            ).encode()
            if data != expected:
                raise ReadbackError("SHA256SUMS does not match verified packet members")
''',
    ),
    (
        '''        if name == "SHA256SUMS":
            self._check_exact_member_set()
            os.fsync(self.descriptor)
            self.close()
''',
        '''        if name == "SHA256SUMS":
            self._check_exact_member_set()
            for member in sorted(self.records):
                self._read_verified(member, allow_manifest=True)
            os.fsync(self.descriptor)
            self.close()
''',
    ),
    (
        '''    def read_verified(self, name: str) -> bytes:
        self._check_exact_member_set()
        record = self.records.get(name)
''',
        '''    def read_verified(self, name: str) -> bytes:
        return self._read_verified(name, allow_manifest=False)

    def _read_verified(self, name: str, *, allow_manifest: bool) -> bytes:
        self._check_exact_member_set()
        record = self.records.get(name)
''',
    ),
    (
        '''        if record is None or name == "SHA256SUMS":
            raise ReadbackError("packet member is not available for sealing")
''',
        '''        if record is None or (name == "SHA256SUMS" and not allow_manifest):
            raise ReadbackError("packet member is not available for sealing")
''',
    ),
]
for before, after in replacements:
    if source.count(before) != 1:
        raise SystemExit("expected final-seal source block was not found exactly once")
    source = source.replace(before, after)
source_path.write_text(source, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
marker = '\n\nif __name__ == "__main__": unittest.main()'
addition = r'''

    def test_packet_writer_rechecks_members_when_sealing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            manifest = "".join(
                f"{record['sha256']}  {name}\n"
                for name, record in sorted(writer.records.items())
            ).encode()
            (output / "one.json").write_bytes(b"evil\n")
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.write(writer / "SHA256SUMS", manifest)
            writer.close()
'''
if tests.count(marker) != 1:
    raise SystemExit("final-seal test insertion marker was not found exactly once")
tests = tests.replace(marker, addition + marker)
test_path.write_text(tests, encoding="utf-8")
