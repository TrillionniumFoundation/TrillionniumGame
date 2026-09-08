#!/usr/bin/env python3
"""Require a stable human User in every declared protected environment."""
from pathlib import Path

source_path = Path("scripts/capture-repository-governance-readback.py")
test_path = Path("tests/control_plane/test_capture_repository_governance_readback.py")
source = source_path.read_text(encoding="utf-8")

before = '''def has_next_page(headers: dict[str, str]) -> bool:
    return 'rel="next"' in headers.get("link", "").lower()


class PacketTarget:
'''
after = '''def has_next_page(headers: dict[str, str]) -> bool:
    return 'rel="next"' in headers.get("link", "").lower()


def has_stable_human_environment_reviewer(rule: Any) -> bool:
    if not isinstance(rule, dict) or rule.get("type") != "required_reviewers":
        return False
    reviewers = rule.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers:
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "User"
        and isinstance(item.get("reviewer"), dict)
        and isinstance(item["reviewer"].get("id"), int)
        and item["reviewer"]["id"] > 0
        and isinstance(item["reviewer"].get("login"), str)
        and bool(item["reviewer"]["login"].strip())
        for item in reviewers
    )


class PacketTarget:
'''
if source.count(before) != 1:
    raise SystemExit("human-reviewer helper insertion point was not found exactly once")
source = source.replace(before, after)

before = '''        and any(
            isinstance(rule, dict) and rule.get("type") == "required_reviewers" and rule.get("reviewers")
            for rule in values[key].get("protection_rules", [])
        )
'''
after = '''        and any(
            has_stable_human_environment_reviewer(rule)
            for rule in values[key].get("protection_rules", [])
        )
'''
if source.count(before) != 1:
    raise SystemExit("environment-reviewer assertion was not found exactly once")
source = source.replace(before, after)
source_path.write_text(source, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
before = '''            {"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"id": 99}}]}]},
'''
after = '''            {"type": "required_reviewers", "reviewers": [
                {"type": "User", "reviewer": {"id": 99, "login": "independent-reviewer"}}
            ]}]},
'''
if tests.count(before) != 1:
    raise SystemExit("environment reviewer fixture was not found exactly once")
tests = tests.replace(before, after)

marker = '\n\nif __name__ == "__main__": unittest.main()'
addition = r'''

    def test_team_only_or_unstable_user_environment_reviewers_fail_closed(self):
        for reviewers in (
            [{"type": "Team", "reviewer": {"id": 77, "slug": "release-sre"}}],
            [{"type": "User", "reviewer": {"login": "missing-id"}}],
            [{"type": "User", "reviewer": {"id": 0, "login": "zero-id"}}],
            [{"type": "User", "reviewer": {"id": 99, "login": "   "}}],
        ):
            with self.subTest(reviewers=reviewers), tempfile.TemporaryDirectory() as directory:
                data = values()
                data["environment"]["protection_rules"][0]["reviewers"] = reviewers
                result = MODULE.capture(
                    FakeApi(data), Path(directory) / "packet",
                    "a" * 40, ["governance-audit"],
                )
                self.assertFalse(result["assertions"]["required_environments_have_reviewers"])
'''
if tests.count(marker) != 1:
    raise SystemExit("human-reviewer test insertion point was not found exactly once")
tests = tests.replace(marker, addition + marker)
test_path.write_text(tests, encoding="utf-8")
