#!/usr/bin/env python3
"""Bind the server source checker to the exact split authority-storage test set."""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one target, found {count}")
    return text.replace(old, new, 1)


def apply(root: Path) -> None:
    checker = root / "scripts/check-trnm-server.py"
    text = checker.read_text(encoding="utf-8")
    replacements = (
        (
            'SERVER_ROOT = PERSISTENCE_ROOT / "bin"\nMODULE_ROOT = SERVER_ROOT / "trnm_server"\nREQUIRED_FILES = {\n',
            'SERVER_ROOT = PERSISTENCE_ROOT / "bin"\nMODULE_ROOT = SERVER_ROOT / "trnm_server"\nAUTHORITY_STORAGE_ROOT = ROOT / "crates/trnm-persistence-pg/tests/authority_storage.rs"\nAUTHORITY_STORAGE_PARTS = tuple(\n    AUTHORITY_STORAGE_ROOT.parent / "authority_storage_parts" / name\n    for name in ("00_helpers.rs", "01_authority.rs", "02_storage_batch.rs", "03_storage_list.rs")\n)\nREQUIRED_FILES = {\n',
            "authority-storage constants",
        ),
        (
            '    ROOT / "crates/trnm-persistence-pg/tests/authority_storage.rs",\n',
            '    AUTHORITY_STORAGE_ROOT,\n    *AUTHORITY_STORAGE_PARTS,\n',
            "authority-storage required files",
        ),
        (
            '    pool_source = "\\n".join(\n        [sources[pool_root_key], *(sources[part.relative_to(ROOT)] for part in POOL_PARTS)]\n    )\n    combined = "\\n".join(sources.values())\n',
            '    pool_source = "\\n".join(\n        [sources[pool_root_key], *(sources[part.relative_to(ROOT)] for part in POOL_PARTS)]\n    )\n\n    authority_storage_root_key = AUTHORITY_STORAGE_ROOT.relative_to(ROOT)\n    expected_authority_storage_root = "\\n".join(\n        f\'include!("authority_storage_parts/{part.name}");\'\n        for part in AUTHORITY_STORAGE_PARTS\n    ) + "\\n"\n    if sources[authority_storage_root_key] != expected_authority_storage_root:\n        fail("authority_storage.rs must remain the exact four-part include authority")\n    authority_storage_source = "\\n".join(\n        [\n            sources[authority_storage_root_key],\n            *(sources[part.relative_to(ROOT)] for part in AUTHORITY_STORAGE_PARTS),\n        ]\n    )\n    combined = "\\n".join(sources.values())\n',
            "authority-storage composition validation",
        ),
        (
            '    test_names = set(re.findall(r"fn\\s+([a-z0-9_]+)\\s*\\(\\)\\s*\\{", combined))\n',
            '    test_names = set(\n        re.findall(\n            r"fn\\s+([a-z0-9_]+)\\s*\\(\\)\\s*\\{",\n            combined + "\\n" + authority_storage_source,\n        )\n    )\n',
            "split-test discovery",
        ),
    )
    for old, new, label in replacements:
        text = replace_exact(text, old, new, label)
    checker.write_text(text, encoding="utf-8")

    test_path = root / "tests/control_plane/test_trnm_server_dependency_contract.py"
    tests = test_path.read_text(encoding="utf-8")
    anchor = '    def test_real_main_still_rejects_missing_required_source(self):\n'
    inserted = '''    def test_authority_storage_include_authority_is_exact_and_ordered(self):
        expected = "\\n".join(
            f'include!("authority_storage_parts/{part.name}");'
            for part in self.server.AUTHORITY_STORAGE_PARTS
        ) + "\\n"
        self.assertEqual(self.server.AUTHORITY_STORAGE_ROOT.read_text(encoding="utf-8"), expected)

        original = Path.read_text
        mutations = (
            expected.replace('include!("authority_storage_parts/03_storage_list.rs");\\n', ""),
            expected.replace(
                'include!("authority_storage_parts/01_authority.rs");\\n'
                'include!("authority_storage_parts/02_storage_batch.rs");\\n',
                'include!("authority_storage_parts/02_storage_batch.rs");\\n'
                'include!("authority_storage_parts/01_authority.rs");\\n',
            ),
            expected + 'include!("authority_storage_parts/99_unreviewed.rs");\\n',
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                def replaced(path, *args, **kwargs):
                    if path == self.server.AUTHORITY_STORAGE_ROOT:
                        return mutation
                    return original(path, *args, **kwargs)

                with patch.object(Path, "read_text", replaced):
                    with self.assertRaisesRegex(SystemExit, "exact four-part include authority"):
                        self.server.main()

    def test_authority_storage_parts_are_required_source_inputs(self):
        expected = {
            "00_helpers.rs",
            "01_authority.rs",
            "02_storage_batch.rs",
            "03_storage_list.rs",
        }
        self.assertEqual({path.name for path in self.server.AUTHORITY_STORAGE_PARTS}, expected)
        self.assertTrue(set(self.server.AUTHORITY_STORAGE_PARTS) <= self.server.REQUIRED_FILES)

'''
    tests = replace_exact(tests, anchor, inserted + anchor, "hostile regression insertion")
    test_path.write_text(tests, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    apply(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
