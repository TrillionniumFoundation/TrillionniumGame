#!/usr/bin/env python3
"""One-shot exact-head Surface 2 rebind and receipt-scope repair.

This file exists only on an operations branch. It never enters the product
candidate tree and refuses stale heads, unexpected conflicts, or changed source
shapes.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = os.environ["REPOSITORY"]
SOCIAL_BRANCH = os.environ["SOCIAL_BRANCH"]
SOCIAL_HEAD = os.environ["SOCIAL_HEAD"]
IDENTITY_BRANCH = os.environ["IDENTITY_BRANCH"]
IDENTITY_HEAD = os.environ["IDENTITY_HEAD"]
GH_TOKEN = os.environ["GH_TOKEN"]
PUBLIC_REMOTE = f"https://github.com/{REPOSITORY}.git"


def run(
    *args: str,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def output(*args: str, cwd: Path | None = None) -> str:
    completed = run(*args, cwd=cwd, capture=True)
    return completed.stdout.strip()


def remote_head(branch: str) -> str:
    text = output("git", "ls-remote", PUBLIC_REMOTE, f"refs/heads/{branch}")
    if not text:
        raise RuntimeError(f"missing remote branch: {branch}")
    return text.split()[0]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def patch_function_replay(
    root: Path,
    relative: str,
    name: str,
    actor: str,
    outcomes: list[str],
) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    marker = f"    pub fn {name}(\n"
    require(text.count(marker) == 1, f"{relative}: function boundary changed for {name}")
    start = text.index(marker)
    next_public = text.find("\n    pub fn ", start + len(marker))
    block_end = len(text) if next_public == -1 else next_public
    block = text[start:block_end]
    old_call = "        if let Some(receipt) = self.existing_receipt(command, fingerprint)? {"
    require(block.count(old_call) == 1, f"{relative}:{name}: legacy replay call changed")
    outcome_lines = ",\n".join(
        f"                ReceiptOutcome::{value}" for value in outcomes
    )
    replacement = (
        "        if let Some(receipt) = self.existing_receipt(\n"
        "            command,\n"
        "            fingerprint,\n"
        f"            {actor},\n"
        "            &[\n"
        f"{outcome_lines},\n"
        "            ],\n"
        "        )? {"
    )
    block = block.replace(old_call, replacement, 1)
    path.write_text(text[:start] + block + text[block_end:], encoding="utf-8")


def patch_social_source(repo: Path) -> None:
    root = repo / "crates/trnm-social-core/src"
    helper_path = root / "parts/16_registry_receipts.rs"
    helper = helper_path.read_text(encoding="utf-8")
    start_marker = "    fn existing_receipt(\n"
    end_marker = "\n    fn finish_command(\n"
    require(helper.count(start_marker) == 1, "receipt helper start changed")
    require(helper.count(end_marker) == 1, "receipt helper end changed")
    start = helper.index(start_marker)
    end = helper.index(end_marker, start)
    old = helper[start:end]
    require(
        '"social_command_fingerprint_mismatch"' in old,
        "receipt helper no longer matches expected source",
    )
    new = '''    fn existing_receipt(
        &self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        actor: AccountId,
        allowed_outcomes: &[ReceiptOutcome],
    ) -> Result<Option<CommandReceipt>, SocialError> {
        let Some(receipt) = self.receipts.get(&command).copied() else {
            return Ok(None);
        };
        if receipt.fingerprint != fingerprint {
            return Err(SocialError::new(
                SocialErrorCode::Conflict,
                "social_command_fingerprint_mismatch",
            ));
        }
        if receipt.actor != actor {
            return Err(SocialError::new(
                SocialErrorCode::Conflict,
                "social_command_actor_mismatch",
            ));
        }
        if !allowed_outcomes.contains(&receipt.outcome) {
            return Err(SocialError::new(
                SocialErrorCode::Conflict,
                "social_command_operation_mismatch",
            ));
        }
        Ok(Some(receipt))
    }
'''
    helper_path.write_text(
        helper[:start] + new.rstrip("\n") + helper[end:], encoding="utf-8"
    )

    mappings: dict[str, list[tuple[str, str, list[str]]]] = {
        "parts/10_registry_friend_request.rs": [
            ("send_friend_request", "actor", ["FriendRequestSent"]),
            ("accept_friend_request", "actor", ["FriendRequestAccepted"]),
        ],
        "parts/11_registry_friend_graph.rs": [
            ("remove_friend", "actor", ["FriendRemoved"]),
            ("block_user", "actor", ["UserBlocked"]),
            ("unblock_user", "actor", ["UserUnblocked"]),
        ],
        "parts/12_registry_group_join.rs": [
            ("create_group", "request.creator", ["GroupCreated"]),
            ("join_group", "actor", ["GroupJoined", "GroupJoinRequested"]),
            ("approve_group_join", "actor", ["GroupJoinApproved"]),
        ],
        "parts/13_registry_group_admin.rs": [
            ("change_group_role", "actor", ["GroupRoleChanged"]),
            ("leave_group", "actor", ["GroupLeft"]),
            ("ban_group_member", "actor", ["GroupMemberBanned"]),
            ("unban_group_member", "actor", ["GroupMemberUnbanned"]),
        ],
        "parts/14_registry_messages.rs": [
            ("send_group_message", "request.sender", ["ChatMessageSent"]),
        ],
        "parts/15_registry_notifications.rs": [
            (
                "create_notification",
                "request.sender.unwrap_or(request.recipient)",
                ["NotificationCreated"],
            ),
            ("mark_notification_read", "recipient", ["NotificationMarkedRead"]),
            ("delete_notification", "recipient", ["NotificationDeleted"]),
        ],
    }
    patched = 0
    for relative, functions in mappings.items():
        for name, actor, outcomes in functions:
            patch_function_replay(root, relative, name, actor, outcomes)
            patched += 1
    require(patched == 16, f"unexpected replay call count: {patched}")
    remaining = sum(
        path.read_text(encoding="utf-8").count(
            "existing_receipt(command, fingerprint)"
        )
        for path in (root / "parts").glob("*.rs")
    )
    require(remaining == 0, f"legacy receipt calls remain: {remaining}")

    invariant_path = root / "parts/17_registry_invariants.rs"
    invariant = invariant_path.read_text(encoding="utf-8")
    old_invariant = """        for (command, receipt) in &self.receipts {
            if receipt.command != *command || receipt.revision == 0 || receipt.revision > self.revision {
                return Err(invariant_error());
            }
"""
    new_invariant = """        for (command, receipt) in &self.receipts {
            if receipt.command != *command
                || !self.users.contains(&receipt.actor)
                || receipt.revision == 0
                || receipt.revision > self.revision
            {
                return Err(invariant_error());
            }
"""
    require(invariant.count(old_invariant) == 1, "receipt invariant source changed")
    invariant_path.write_text(
        invariant.replace(old_invariant, new_invariant, 1), encoding="utf-8"
    )

    lib_path = root / "lib.rs"
    lib = lib_path.read_text(encoding="utf-8")
    include_marker = '    include!("tests/05_config.rs");\n'
    require(lib.count(include_marker) == 1, "test include marker changed")
    require("tests/06_receipt_scope.rs" not in lib, "receipt scope test already included")
    lib_path.write_text(
        lib.replace(
            include_marker,
            include_marker + '    include!("tests/06_receipt_scope.rs");\n',
            1,
        ),
        encoding="utf-8",
    )

    test_path = root / "tests/06_receipt_scope.rs"
    require(not test_path.exists(), "receipt scope test already exists")
    test_path.write_text(
        '''#[test]
fn exact_replay_is_scoped_to_operation_and_actor() {
    let mut registry = registry_with_users(3);
    let receipt = registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .unwrap();
    let unchanged = registry.clone();
    let replay = registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .unwrap();
    assert_eq!(receipt, replay);
    assert_eq!(unchanged, registry);

    let before_operation = registry.clone();
    let error = registry
        .block_user(command(1), fingerprint(1), account(1), account(2))
        .unwrap_err();
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(error.reason(), "social_command_operation_mismatch");
    assert_eq!(before_operation, registry);

    let before_actor = registry.clone();
    let error = registry
        .send_friend_request(command(1), fingerprint(1), account(3), account(2))
        .unwrap_err();
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(error.reason(), "social_command_actor_mismatch");
    assert_eq!(before_actor, registry);
}

#[test]
fn join_group_replay_accepts_both_operation_outcomes_only() {
    let mut registry = registry_with_users(2);
    registry
        .create_group(
            command(1),
            fingerprint(1),
            CreateGroupRequest::new(
                group(1),
                account(1),
                GroupName::new("approval").unwrap(),
                GroupJoinMode::AdminApproval,
                10,
            ),
        )
        .unwrap();
    let receipt = registry
        .join_group(command(2), fingerprint(2), group(1), account(2))
        .unwrap();
    assert_eq!(receipt.outcome(), ReceiptOutcome::GroupJoinRequested);
    let snapshot = registry.clone();
    let replay = registry
        .join_group(command(2), fingerprint(2), group(1), account(2))
        .unwrap();
    assert_eq!(receipt, replay);
    assert_eq!(snapshot, registry);

    let error = registry
        .leave_group(command(2), fingerprint(2), group(1), account(2))
        .unwrap_err();
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(error.reason(), "social_command_operation_mismatch");
    assert_eq!(snapshot, registry);
}
''',
        encoding="utf-8",
    )


def patch_docs_and_state(repo: Path) -> None:
    readme_path = repo / "crates/trnm-social-core/README.md"
    readme = readme_path.read_text(encoding="utf-8")
    replacements = {
        "A social command ID is idempotent only with the exact original fingerprint. Exact replay returns the original receipt and produces no duplicate state or outbox intent. Reuse with a changed fingerprint fails as a conflict without mutation.": "A social command ID is idempotent only for the exact original fingerprint, actor and operation family. Exact replay returns the original receipt and produces no duplicate state or outbox intent. Cross-actor or cross-operation reuse fails as a conflict even when a caller repeats the same fingerprint; reuse with a changed fingerprint also fails without mutation. Trusted adapters must derive the fingerprint from the canonical complete command, including every target, group, message, notification and payload field, rather than accepting an arbitrary caller-selected value.",
        "Validation, unknown identity, self-relationship, collision, stale command fingerprint, capacity exhaustion, permission failure, last-superadmin protection, duplicate message/notification identity and checked counter overflow fail before committing the candidate.": "Validation, unknown identity, self-relationship, collision, stale command fingerprint, actor mismatch, operation mismatch, capacity exhaustion, permission failure, last-superadmin protection, duplicate message/notification identity and checked counter overflow fail before committing the candidate. Receipt invariants require every recorded actor to remain a registered user.",
        "The focused corpus covers exact receipt replay, changed fingerprints, friendship transitions, mutual blocking, keyset pagination, approval joins, role and last-superadmin rules, bans, chat sequencing, cursor scope, notification ordering/read/delete, capacity failures, checked limits and payload redaction.": "The focused corpus covers actor-and-operation-scoped exact receipt replay, changed fingerprints, friendship transitions, mutual blocking, keyset pagination, approval joins, role and last-superadmin rules, bans, chat sequencing, cursor scope, notification ordering/read/delete, capacity failures, checked limits and payload redaction. Hostile receipt tests prove that a command/fingerprint pair cannot be replayed through another actor or operation and that the two valid `join_group` outcomes remain in one operation family.",
    }
    for old, new in replacements.items():
        require(readme.count(old) == 1, f"social README contract changed: {old[:48]}")
        readme = readme.replace(old, new, 1)
    readme_path.write_text(readme, encoding="utf-8")

    inventory_path = repo / "docs/status/IMPLEMENTATION_INVENTORY.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    rows = [row for row in inventory["components"] if row.get("id") == "COMP-SOCIAL-CORE"]
    require(len(rows) == 1, "social implementation inventory row missing or duplicated")
    row = rows[0]
    old = "exact command receipt replay and changed-fingerprint rejection"
    require(row["implemented"].count(old) == 1, "social receipt inventory changed")
    row["implemented"][row["implemented"].index(old)] = (
        "actor-and-operation-scoped exact command receipt replay with changed-fingerprint rejection"
    )
    inventory["generated_at"] = "2026-09-10"
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    state_path = repo / "docs/status/CURRENT_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["observed_at"] = "2026-09-10T08:15:00Z"
    state["authority"].update(
        {
            "audited_main_commit": "425ee015d53800f3e2c2936f97ded9cdbacf7f14",
            "audited_main_tree": "ed47de87507feeef60795c997bce3a6e88699e9d",
            "candidate_branch": SOCIAL_BRANCH,
            "candidate_pull_request": 164,
            "candidate_commit": None,
            "candidate_tree": None,
            "candidate_identity_rule": "The tracked state binds stacked Plan v3.2 Surface 2 to pull request 164 without guessing its self-referential final commit. Surface 1 must remain the exact base ancestor; GitHub head/tree and actual prospective-merge metadata are execution authority, and any parent, head, base or tree movement requires fresh qualification.",
            "last_admitted_pull_request": 168,
        }
    )
    state["repository_governance"]["status"] = (
        "Plan v3.2 convergence and post-merge truth are admitted on main. Surface 1 and stacked Surface 2 are source candidates undergoing exact-object qualification; external governance and conflict-free specialist acceptance remain open."
    )
    state["implementation"]["identity_core_source_candidate"] = True
    state["implementation"]["social_core_source_candidate"] = True
    state["implementation"]["social_receipt_scope_source_repaired"] = True
    state["active_priority"] = [
        "complete exact-head and prospective-merge qualification and conflict-free review for Plan v3.2 Surface 1",
        "qualify stacked Plan v3.2 Surface 2 only against the exact admitted Surface 1 parent",
        "retain and independently accept each post-merge main packet",
        "read back complete branch and ruleset review freshness CODEOWNER conversation and bypass policy",
        "provision conflict-free security database protocol realtime compatibility and SRE reviewers",
        "classify and independently lock every D0-D8 leaf and obtain separate global SG1 acceptance",
        "complete the remaining storage competition multiplayer protocol RTAPI Runtime Console provider IAP migration HA endurance canary cutover and retirement surfaces",
    ]
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def write_registration_test(repo: Path) -> None:
    path = repo / "tests/control_plane/test_social_module_registration.py"
    require(not path.exists(), "social registration regression already exists")
    path.write_text(
        '''from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-documentation-authority.py"
SOCIAL_ID = "trnm-social-core"
SOCIAL_PATH = "crates/trnm-social-core"


def load_checker():
    spec = importlib.util.spec_from_file_location("documentation_authority", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SocialModuleRegistrationTests(unittest.TestCase):
    def test_social_module_is_bound_in_every_current_inventory(self) -> None:
        cargo = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
        self.assertEqual(cargo["workspace"]["members"].count(SOCIAL_PATH), 1)

        package = json.loads(
            (ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(package["workspace"]["members"].count(SOCIAL_PATH), 1)

        modules = json.loads(
            (ROOT / "docs/status/MODULE_DOCUMENTATION.json").read_text(
                encoding="utf-8"
            )
        )
        module_rows = [row for row in modules["modules"] if row.get("id") == SOCIAL_ID]
        self.assertEqual(len(module_rows), 1)
        self.assertEqual(module_rows[0]["path"], SOCIAL_PATH)
        self.assertEqual(module_rows[0]["manifest"], f"{SOCIAL_PATH}/Cargo.toml")
        self.assertEqual(module_rows[0]["documentation"], f"{SOCIAL_PATH}/README.md")
        self.assertFalse(module_rows[0]["claim_credit"])

        implementation = json.loads(
            (ROOT / "docs/status/IMPLEMENTATION_INVENTORY.json").read_text(
                encoding="utf-8"
            )
        )
        component_rows = [
            row
            for row in implementation["components"]
            if row.get("id") == "COMP-SOCIAL-CORE"
        ]
        self.assertEqual(len(component_rows), 1)
        self.assertEqual(component_rows[0]["path"], SOCIAL_PATH)
        self.assertFalse(component_rows[0]["claim_credit"])

        state = json.loads(
            (ROOT / "docs/status/CURRENT_STATE.json").read_text(encoding="utf-8")
        )
        self.assertIs(state["implementation"]["social_core_source_candidate"], True)

    def _minimal_root(self, directory: str):
        root = Path(directory)
        target = root / SOCIAL_PATH
        target.mkdir(parents=True)
        shutil.copy2(ROOT / SOCIAL_PATH / "Cargo.toml", target / "Cargo.toml")
        shutil.copy2(ROOT / SOCIAL_PATH / "README.md", target / "README.md")

        registry = json.loads(
            (ROOT / "docs/status/MODULE_DOCUMENTATION.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(row for row in registry["modules"] if row["id"] == SOCIAL_ID)
        registry["modules"] = [copy.deepcopy(row)]
        registry["summary"] = {
            "module_count": 1,
            "documented_count": 1,
            "root_workspace_count": 1,
            "isolated_workspace_count": 0,
            "undocumented_count": 0,
        }
        registry_path = root / "docs/status/MODULE_DOCUMENTATION.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return root, registry_path, registry

    def test_social_registration_removal_and_path_substitution_fail_closed(self) -> None:
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root, registry_path, registry = self._minimal_root(directory)
            documented, summary = checker.validate_module_registry(root, registry_path)
            self.assertEqual(len(documented), 1)
            self.assertEqual(summary["module_count"], 1)

            removed = copy.deepcopy(registry)
            removed["modules"] = []
            removed["summary"].update(
                module_count=0,
                documented_count=0,
                root_workspace_count=0,
                undocumented_count=0,
            )
            registry_path.write_text(
                json.dumps(removed, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                checker.ValidationError, "module registry must contain modules"
            ):
                checker.validate_module_registry(root, registry_path)

            substituted = copy.deepcopy(registry)
            substituted["modules"][0]["path"] = "crates/trnm-identity-core"
            registry_path.write_text(
                json.dumps(substituted, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                checker.ValidationError, "path must be crates/trnm-social-core"
            ):
                checker.validate_module_registry(root, registry_path)


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def main() -> None:
    require(remote_head(SOCIAL_BRANCH) == SOCIAL_HEAD, "social branch moved")
    require(remote_head(IDENTITY_BRANCH) == IDENTITY_HEAD, "identity branch moved")

    root = Path(tempfile.mkdtemp(prefix="social-rebind-"))
    try:
        run("git", "init", str(root))
        run("git", "-C", str(root), "remote", "add", "origin", PUBLIC_REMOTE)
        run("git", "-C", str(root), "fetch", "--no-tags", "origin", SOCIAL_HEAD, IDENTITY_HEAD)
        run("git", "-C", str(root), "checkout", "--detach", SOCIAL_HEAD)
        require(output("git", "rev-parse", "HEAD", cwd=root) == SOCIAL_HEAD, "wrong social head")
        run("git", "config", "user.name", "github-actions[bot]", cwd=root)
        run(
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
            cwd=root,
        )

        merge = run(
            "git", "merge", "--no-commit", "--no-ff", IDENTITY_HEAD,
            cwd=root, check=False,
        )
        if merge.returncode != 0:
            conflicts = output(
                "git", "diff", "--name-only", "--diff-filter=U", cwd=root
            ).splitlines()
            require(
                conflicts == ["docs/status/CURRENT_STATE.json"],
                f"unexpected merge conflicts: {conflicts}",
            )
            run(
                "git", "checkout", "--theirs", "--", "docs/status/CURRENT_STATE.json",
                cwd=root,
            )
            run("git", "add", "docs/status/CURRENT_STATE.json", cwd=root)
        require(
            not output("git", "diff", "--name-only", "--diff-filter=U", cwd=root),
            "unresolved merge conflict",
        )
        identity_source = (root / "crates/trnm-identity-core/src/lib.rs").read_text(
            encoding="utf-8"
        )
        require("authentication_state_changed" in identity_source, "identity repair missing")
        require(
            "authentication_replay_after_unlink_or_rebind_is_denied_atomically"
            in identity_source,
            "identity hostile regression missing",
        )

        patch_social_source(root)
        patch_docs_and_state(root)
        write_registration_test(root)

        run("cargo", "fmt", "--all", cwd=root)
        run("cargo", "fmt", "--all", "--", "--check", cwd=root)
        run(
            "cargo", "test", "--package", "trnm-identity-core", "--package",
            "trnm-social-core", "--all-targets", "--locked", cwd=root,
        )
        run(
            "cargo", "clippy", "--package", "trnm-identity-core", "--package",
            "trnm-social-core", "--all-targets", "--locked", "--", "-D", "warnings",
            cwd=root,
        )
        run(
            sys.executable, "-m", "unittest",
            "tests.control_plane.test_social_module_registration", "-v", cwd=root,
        )
        run(
            sys.executable, "-m", "unittest",
            "tests.control_plane.test_documentation_authority", "-v", cwd=root,
        )
        for checker in (
            "scripts/check-documentation-authority.py",
            "scripts/check-rust-package-inventory.py",
            "scripts/check-workspace-convergence.py",
            "scripts/check-plan.py",
        ):
            run(sys.executable, checker, cwd=root)
        run("git", "diff", "--check", cwd=root)

        run("git", "add", "-A", cwd=root)
        run("git", "diff", "--cached", "--check", cwd=root)
        run(
            "git", "commit", "-m",
            "merge(social): rebind Surface 2 and scope receipt replay",
            cwd=root,
        )
        parents = output("git", "rev-list", "--parents", "-n1", "HEAD", cwd=root).split()
        require(len(parents) == 3, f"expected merge commit, got {len(parents) - 1} parents")
        require(set(parents[1:]) == {SOCIAL_HEAD, IDENTITY_HEAD}, "unexpected merge parents")
        require(remote_head(SOCIAL_BRANCH) == SOCIAL_HEAD, "social branch moved before push")
        require(remote_head(IDENTITY_BRANCH) == IDENTITY_HEAD, "identity branch moved before push")

        authenticated = f"https://x-access-token:{GH_TOKEN}@github.com/{REPOSITORY}.git"
        run("git", "remote", "set-url", "origin", authenticated, cwd=root)
        run("git", "push", "origin", f"HEAD:refs/heads/{SOCIAL_BRANCH}", cwd=root)
        print(f"published_head={output('git', 'rev-parse', 'HEAD', cwd=root)}")
        print(f"published_tree={output('git', 'rev-parse', 'HEAD^{{tree}}', cwd=root)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
