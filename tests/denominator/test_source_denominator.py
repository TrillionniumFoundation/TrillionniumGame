from __future__ import annotations

import unittest

from tools.denominator.source_manifest import ROOT, canonical_bytes, generate


class SourceDenominatorTests(unittest.TestCase):
    def test_repository_source_candidate_is_deterministic_and_fail_closed(self):
        first = generate(ROOT)
        second = generate(ROOT)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(first["denominator"], "DEN-SOURCE")
        self.assertGreaterEqual(first["leaf_count"], 30)
        self.assertEqual(first["unclassified_count"], first["leaf_count"])
        self.assertEqual(first["unreviewed_count"], first["leaf_count"])
        self.assertFalse(first["sg1_eligible"])
        self.assertFalse(first["compatibility_credit"])
        classes = {leaf["class"] for leaf in first["leaves"]}
        self.assertTrue(
            {
                "upstream_source_root",
                "upstream_source_object",
                "sdk_source_root",
                "database_test_image",
                "toolchain_or_dependency_lock",
            }.issubset(classes)
        )


if __name__ == "__main__":
    unittest.main()
