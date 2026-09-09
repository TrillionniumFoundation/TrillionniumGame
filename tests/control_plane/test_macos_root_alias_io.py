from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = load("evidence_alias_subject", str(ROOT / "scripts/evidence_admission.py"))
UPLOADER = load("uploader_alias_subject", str(ROOT / "scripts/upload-actions-artifact.py"))
REAL_LSTAT = Path.lstat
REAL_REALPATH = os.path.realpath
REAL_OPEN = os.open


class RootAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.json_path = self.root / "value.json"
        self.json_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        self.bin_path = self.root / "artifact.zip"
        self.bin_path.write_bytes(b"fixture")

    def alias_context(self, module, actual: Path, uid: int = 0):
        alias = "trnm-root-alias"
        fake = Path("/") / alias / Path(*actual.absolute().parts[1:])

        def lstat(subject: Path):
            if subject == Path("/") / alias:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=uid)
            return REAL_LSTAT(subject)

        def realpath(subject):
            if Path(subject) == Path("/") / alias:
                return "/"
            return REAL_REALPATH(subject)

        return patch.object(module.Path, "absolute", return_value=fake), \
            patch.object(module.Path, "lstat", autospec=True, side_effect=lstat), \
            patch.object(module.os.path, "realpath", side_effect=realpath)

    def test_root_owned_alias_preserves_evidence_bytes(self):
        contexts = self.alias_context(EVIDENCE, self.json_path)
        with contexts[0], contexts[1], contexts[2]:
            self.assertEqual(EVIDENCE.load_object(self.json_path), {"ok": True})

    def test_root_owned_alias_preserves_artifact_bytes(self):
        contexts = self.alias_context(UPLOADER, self.bin_path)
        with contexts[0], contexts[1], contexts[2]:
            self.assertEqual(
                UPLOADER.validate_artifact("fixture-packet", self.bin_path),
                b"fixture",
            )

    def test_untrusted_root_alias_is_rejected_before_descriptor_io(self):
        for module, path, action, error in (
            (EVIDENCE, self.json_path, lambda: EVIDENCE.load_object(self.json_path), EVIDENCE.AdmissionError),
            (UPLOADER, self.bin_path, lambda: UPLOADER.validate_artifact("fixture-packet", self.bin_path), UPLOADER.ArtifactUploadError),
        ):
            with self.subTest(module=module.__name__):
                contexts = self.alias_context(module, path, uid=501)
                with contexts[0], contexts[1], contexts[2], \
                        patch.object(module.os, "open", side_effect=AssertionError("must not open")):
                    with self.assertRaises(error):
                        action()

    def test_lower_level_symlink_remains_rejected(self):
        target = self.root / "target"
        target.mkdir()
        (target / "data.json").write_text('{"ok":true}', encoding="utf-8")
        linked = self.root / "linked"
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaises(EVIDENCE.AdmissionError):
            EVIDENCE.load_object(linked / "data.json")

        artifact = target / "artifact.zip"
        artifact.write_bytes(b"fixture")
        leaf = self.root / "leaf.zip"
        leaf.symlink_to(artifact)
        with self.assertRaises(UPLOADER.ArtifactUploadError):
            UPLOADER.validate_artifact("fixture-packet", leaf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
