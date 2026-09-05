"""Descriptor-bound retained I/O regressions; all data and reviews are synthetic.

Run on the supported POSIX development/CI host. No test is silently skipped on an
unsupported platform, and no synthetic fixture grants real evidence acceptance.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "retained_io_fixture_source", Path(__file__).with_name("test_evidence_admission.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load the existing retained-evidence fixtures")
FIXTURE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FIXTURE
SPEC.loader.exec_module(FIXTURE)
M = FIXTURE.ADMISSION
REAL_OPEN, REAL_READ, REAL_CLOSE = os.open, os.read, os.close


class RetainedIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.file = self.root / "result.bin"
        self.raw = b"synthetic retained output\n"
        self.file.write_bytes(self.raw)
        self.outside = self.base / "outside.bin"
        self.outside.write_bytes(self.raw)
        self.item = {"name": "output", "path": "result.bin",
                     "sha256": hashlib.sha256(self.raw).hexdigest(),
                     "size_bytes": len(self.raw)}
        self.assertTrue(M._SECURE_OPEN_SUPPORTED, "secure POSIX I/O is required")

    def verify(self):
        return M.verify_artifact(self.root, self.item)

    def replace_leaf_before_open(self, replacement):
        seen = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            if path == "result.bin" and not seen:
                seen.append(flags)
                self.file.unlink()
                replacement()
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        with patch.object(M.os, "open", side_effect=opener):
            with self.assertRaises(M.AdmissionError):
                self.verify()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0] & os.O_NOFOLLOW)
        self.assertTrue(seen[0] & os.O_NONBLOCK)

    def test_regular_artifact_passes_and_identity_api_is_unchanged(self):
        self.assertEqual(self.verify(), ("output", "result.bin", self.item["sha256"], len(self.raw)))

    def test_artifact_leaf_symlink_swap_is_rejected_at_open(self):
        self.replace_leaf_before_open(lambda: self.file.symlink_to(self.outside))

    def test_artifact_dangling_symlink_swap_is_rejected(self):
        self.replace_leaf_before_open(lambda: self.file.symlink_to(self.base / "absent"))

    def test_artifact_directory_swap_is_rejected_before_read(self):
        with patch.object(M.os, "read", side_effect=AssertionError("must not read a directory")):
            self.replace_leaf_before_open(self.file.mkdir)

    def test_artifact_fifo_swap_carries_nonblocking_flag_and_never_reads(self):
        with patch.object(M.os, "read", side_effect=AssertionError("must not read a FIFO")):
            self.replace_leaf_before_open(lambda: os.mkfifo(self.file))

    def test_fifo_does_not_hang_in_a_bounded_child_process(self):
        self.file.unlink()
        os.mkfifo(self.file)
        command = (
            "import importlib.util,pathlib;"
            f"s=importlib.util.spec_from_file_location('io', {str(ROOT/'scripts/evidence_admission.py')!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            f"m.load_object(pathlib.Path({str(self.file)!r}))"
        )
        result = subprocess.run([sys.executable, "-c", command], capture_output=True,
                                text=True, timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular file", result.stderr)

    def test_parent_symlink_swap_before_open_is_rejected(self):
        old = self.base / "held"
        outside = self.base / "outside-dir"
        outside.mkdir()
        (outside / "result.bin").write_bytes(self.raw)
        swapped = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            if path == "root" and not swapped:
                swapped.append(True)
                self.root.rename(old)
                self.root.symlink_to(outside, target_is_directory=True)
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        with patch.object(M.os, "open", side_effect=opener):
            with self.assertRaises(M.AdmissionError):
                self.verify()
        self.assertEqual(swapped, [True])

    def test_opened_parent_is_pinned_when_its_path_is_replaced(self):
        held = self.base / "held"
        outside = self.base / "outside-dir"
        outside.mkdir()
        (outside / "result.bin").write_bytes(b"must never read the replacement")
        swapped = []
        read_bytes = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
            if path == "root" and not swapped:
                swapped.append(True)
                self.root.rename(held)
                self.root.symlink_to(outside, target_is_directory=True)
            return descriptor
        def reader(descriptor, size):
            data = REAL_READ(descriptor, size)
            read_bytes.append(data)
            return data
        with patch.object(M.os, "open", side_effect=opener), patch.object(M.os, "read", side_effect=reader):
            self.verify()
        self.assertEqual(b"".join(read_bytes), self.raw)
        self.assertEqual(swapped, [True])

    def test_manifest_leaf_swap_cannot_bypass_entry_validation(self):
        fixture = FIXTURE.RetainedFixture(self.root)
        manifest = self.root / fixture.entry["path"]
        outside = self.base / "manifest-outside.json"
        outside.write_bytes(manifest.read_bytes())
        swapped = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            if path == "fixture.json" and not swapped:
                swapped.append(True)
                manifest.unlink()
                manifest.symlink_to(outside)
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        with patch.object(M.os, "open", side_effect=opener):
            self.assertFalse(M.entry_eligible(fixture.entry, root=self.root, now=FIXTURE.NOW))
        self.assertEqual(swapped, [True])

    def test_schema_leaf_swap_is_rejected(self):
        fixture = FIXTURE.RetainedFixture(self.root)
        schema = self.root / "docs/evidence/schemas/trillionnium-evidence-v1.schema.json"
        outside = self.base / "schema-outside.json"
        outside.write_bytes(schema.read_bytes())
        swapped = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            if path == schema.name and not swapped:
                swapped.append(True)
                schema.unlink()
                schema.symlink_to(outside)
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        with patch.object(M.os, "open", side_effect=opener):
            self.assertFalse(M.entry_eligible(fixture.entry, root=self.root, now=FIXTURE.NOW))
        self.assertEqual(swapped, [True])

    def test_direct_json_reader_rejects_symlinked_parent(self):
        directory = self.base / "actual"
        directory.mkdir()
        (directory / "a.json").write_text('{"value": 1}')
        linked = self.root / "link"
        linked.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(M.AdmissionError):
            M.load_object(linked / "a.json")

    def test_direct_json_reader_rejects_symlinked_leaf(self):
        data = self.root / "data.json"
        data.symlink_to(self.outside)
        with self.assertRaises(M.AdmissionError):
            M.load_object(data)

    def test_unsupported_platform_fails_before_io(self):
        with patch.object(M, "_SECURE_OPEN_SUPPORTED", False), patch.object(M.os, "open") as opener:
            with self.assertRaises(M.AdmissionError):
                self.verify()
        opener.assert_not_called()

    def test_unsafe_relative_paths_fail_before_io(self):
        for path in ("../outside.bin", "/tmp/outside.bin", "./result.bin", "a//result.bin", "a\\b", ""):
            with self.subTest(path=path), patch.object(M.os, "open") as opener:
                with self.assertRaises(M.AdmissionError):
                    M.verify_artifact(self.root, {**self.item, "path": path})
                opener.assert_not_called()

    def test_relative_root_is_supported(self):
        # Supply a relative root without '..' by changing to its parent.
        previous = Path.cwd()
        try:
            os.chdir(self.base)
            self.assertEqual(M.verify_artifact(Path("root"), self.item)[2], self.item["sha256"])
        finally:
            os.chdir(previous)

    def test_excessive_path_depth_is_rejected_before_io(self):
        target = Path("/").joinpath(*(["nested"] * M.MAX_PATH_COMPONENTS), "data.json")
        with patch.object(M.os, "open") as opener:
            with self.assertRaises(M.AdmissionError):
                M.load_object(target)
            opener.assert_not_called()

    def test_dotdot_in_absolute_input_is_rejected(self):
        with self.assertRaises(M.AdmissionError):
            M.load_object(self.root / ".." / "outside.bin")

    def mutate_during_read(self, mutate):
        changed = []
        def reader(descriptor, size):
            data = REAL_READ(descriptor, size)
            if data and not changed:
                changed.append(True)
                mutate()
            return data
        with patch.object(M.os, "read", side_effect=reader):
            with self.assertRaises(M.AdmissionError):
                self.verify()
        self.assertEqual(changed, [True])

    def test_in_place_same_length_mutation_after_read_is_rejected(self):
        self.mutate_during_read(lambda: self.file.write_bytes(b"x" * len(self.raw)))

    def test_metadata_change_after_read_is_rejected_even_when_digest_matches(self):
        before = self.file.stat()
        self.mutate_during_read(lambda: os.utime(self.file, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000)))

    def test_growth_during_read_is_rejected(self):
        def append():
            with self.file.open("ab") as stream:
                stream.write(b"extra")
        self.mutate_during_read(append)

    def test_truncation_during_read_is_rejected(self):
        self.mutate_during_read(lambda: self.file.write_bytes(b""))

    def test_unlink_during_read_is_rejected(self):
        self.mutate_during_read(self.file.unlink)

    def test_early_eof_is_not_success(self):
        with patch.object(M.os, "read", return_value=b""):
            with self.assertRaises(M.AdmissionError):
                self.verify()

    def test_wrong_size_is_rejected_before_read(self):
        self.item["size_bytes"] += 1
        with patch.object(M.os, "read", side_effect=AssertionError("no read expected")):
            with self.assertRaises(M.AdmissionError):
                self.verify()

    def test_open_flags_are_pinned_no_follow_and_noninheritable(self):
        calls = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
            self.assertFalse(os.get_inheritable(descriptor))
            calls.append((path, flags, dir_fd))
            return descriptor
        with patch.object(M.os, "open", side_effect=opener):
            self.verify()
        self.assertEqual(calls[0][0], os.sep)
        for path, flags, parent in calls:
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_CLOEXEC)
            if path == "result.bin":
                self.assertTrue(flags & os.O_NONBLOCK)
            else:
                self.assertTrue(flags & os.O_DIRECTORY)
            if path != os.sep:
                self.assertIsNotNone(parent)

    def assert_no_descriptor_leak(self, action, *, failure=False):
        active = set()
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
            active.add(descriptor)
            return descriptor
        def closer(descriptor):
            self.assertIn(descriptor, active)
            active.remove(descriptor)
            return REAL_CLOSE(descriptor)
        with patch.object(M.os, "open", side_effect=opener), patch.object(M.os, "close", side_effect=closer):
            if failure:
                with self.assertRaises(M.AdmissionError):
                    action()
            else:
                action()
        self.assertEqual(active, set())

    def test_descriptors_close_on_success(self):
        self.assert_no_descriptor_leak(self.verify)

    def test_descriptors_close_on_digest_mismatch(self):
        self.item["sha256"] = "0" * 64
        self.assert_no_descriptor_leak(self.verify, failure=True)

    def test_descriptors_close_on_missing_leaf(self):
        self.file.unlink()
        self.assert_no_descriptor_leak(self.verify, failure=True)

    def test_descriptors_close_on_read_error_and_diagnostics_are_redacted(self):
        def action():
            with patch.object(M.os, "read", side_effect=OSError("synthetic-sensitive-diagnostic")):
                try:
                    self.verify()
                except M.AdmissionError as error:
                    self.assertNotIn("synthetic-sensitive", str(error))
                    raise
        self.assert_no_descriptor_leak(action, failure=True)

    def test_descriptors_close_on_regular_file_type_rejection(self):
        self.file.unlink()
        self.file.mkdir()
        self.assert_no_descriptor_leak(self.verify, failure=True)

    def test_artifact_is_streamed_with_bounded_reads(self):
        raw = b"x" * (1024 * 1024 + 19)
        self.file.write_bytes(raw)
        self.item.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        requests = []
        def reader(descriptor, size):
            requests.append(size)
            return REAL_READ(descriptor, size)
        with patch.object(M.os, "read", side_effect=reader):
            self.verify()
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(0 < count <= 1024 * 1024 for count in requests))

    def test_json_empty_and_oversized_files_are_rejected_before_read(self):
        self.file.write_bytes(b"")
        for size in (0, 9):
            with self.subTest(size=size):
                self.file.write_bytes(b" " * size)
                with patch.object(M, "MAX_JSON_BYTES", 8), patch.object(M.os, "read") as reader:
                    with self.assertRaises(M.AdmissionError):
                        M.load_object(self.file)
                    reader.assert_not_called()

    def test_nullable_expiry_still_passes_without_changing_schema(self):
        fixture = FIXTURE.RetainedFixture(self.root)
        fixture.entry["expires_at"] = None
        fixture.manifest["expires_at"] = None
        fixture.write()
        M.validate_entry(fixture.entry, root=self.root, now=FIXTURE.NOW)

    def test_entry_wires_manifest_and_schema_to_retained_reader(self):
        tree = ast.parse((ROOT / "scripts/evidence_admission.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate_entry")
        calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "load_retained_object"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(ast.literal_eval(calls[1].args[1]),
                         "docs/evidence/schemas/trillionnium-evidence-v1.schema.json")


if __name__ == "__main__":
    unittest.main()
