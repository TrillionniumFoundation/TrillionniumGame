from __future__ import annotations
import subprocess
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
class RepositoryCheckerTest(unittest.TestCase):
    def test_repository_checker(self) -> None:
        subprocess.run(["python3", "scripts/check-production-crypto-paths.py"], cwd=ROOT, check=True)
if __name__ == "__main__":
    unittest.main()
