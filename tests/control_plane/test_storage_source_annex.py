"""Source-annex custody and output-boundary negatives; no native/oracle credit."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("storage_source_annex", ROOT / "scripts/materialize-pinned-storage-upstream.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StorageSourceAnnexTests(unittest.TestCase):
    def test_closed_members_match_pinned_apache_source_and_packet_authority(self):
        self.assertEqual([entry[0] for entry in MODULE.MEMBERS], ["initial-schema.sql", "core-storage.go", "LICENSE"])
        packet = (ROOT / "crates/trnm-persistence-pg/src/storage_import_parts/packet.rs").read_text()
        for name, path, size, sha, blob in MODULE.MEMBERS:
            self.assertIn(sha, packet)
            self.assertIn(str(size), packet)
            self.assertEqual(len(blob), 40)
        self.assertIn(MODULE.COMMIT, packet)
        self.assertIn(MODULE.TREE, packet)

    def test_length_sha_and_actual_git_blob_framing_each_bind_bytes(self):
        data = b"synthetic immutable source\n"
        member = ("LICENSE", "LICENSE", len(data), hashlib.sha256(data).hexdigest(),
                  hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest())
        self.assertEqual(MODULE.validate_bytes(data, member)["size_bytes"], len(data))
        for bad in [data + b"\n", data[:-1], b"X" + data[1:]]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                MODULE.validate_bytes(bad, member)
        with self.assertRaises(ValueError):
            MODULE.validate_bytes(data, member[:-1] + ("1" * 40,))

    def test_existing_or_symlink_destination_never_replaces_source_or_requests_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            owned = parent / "existing"
            owned.mkdir()
            original = owned / "LICENSE"
            original.write_bytes(b"preserve")
            alias = parent / "alias"
            alias.symlink_to(owned, target_is_directory=True)
            for destination in [owned, alias]:
                with self.subTest(destination=destination), mock.patch.object(MODULE.urllib.request, "urlopen") as download:
                    with self.assertRaises(FileExistsError):
                        MODULE.acquire(destination)
                    download.assert_not_called()
                    self.assertEqual(original.read_bytes(), b"preserve")

    def test_redirected_response_leaves_no_completed_source_files(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = "https://example.invalid/rewritten-source"
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            destination = Path(temporary) / "annex"
            with self.assertRaisesRegex(ValueError, "response_invalid"):
                MODULE.acquire(destination)
            response.read.assert_not_called()
            self.assertEqual(list(destination.iterdir()), [])

    def test_corrupt_first_source_never_publishes_any_member(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = f"https://raw.githubusercontent.com/heroiclabs/nakama/{MODULE.COMMIT}/{MODULE.MEMBERS[0][1]}"
        response.read.return_value = b"corrupt"
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            destination = Path(temporary) / "annex"
            with self.assertRaisesRegex(ValueError, "bytes_differ"):
                MODULE.acquire(destination)
            self.assertEqual(list(destination.iterdir()), [])
            response.read.assert_called_once_with(MODULE.MEMBERS[0][2] + 1)


if __name__ == "__main__":
    unittest.main()
