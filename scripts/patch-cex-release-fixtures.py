#!/usr/bin/env python3
"""Apply one exact fail-closed CEX release-fixture source repair."""

from __future__ import annotations

from pathlib import Path
import sys

OLD = '''def _external_workflow_self_tests() -> dict[str, bool]:
    checkout_sha = "a" * 40
    policy = {
        "world": {
            "repository": "${{ steps.lock.outputs.world_repository }}",
            "ref": "${{ steps.lock.outputs.world_commit }}",
            "allowed_packages": {"trnm-game-server"},
        }
    }
    prefix = f"""jobs:\\n  qualify:\\n    steps:\\n      - name: Checkout external\\n        uses: actions/checkout@{checkout_sha}\\n        with:\\n          repository: ${{{{ steps.lock.outputs.world_repository }}}}\\n          ref: ${{{{ steps.lock.outputs.world_commit }}}}\\n          path: world\\n          persist-credentials: false\\n"""
    good = prefix + """      - name: External test\\n        working-directory: world/trillionnium\\n        run: cargo test -p trnm-game-server --locked\\n"""
    observed = _classify_workflow_references(".github/workflows/fixture.yml", good, policy)
    require(observed["local_packages"] == set(), "external fixture leaked into local package references")
    require(observed["external_packages"] == {"world": ["trnm-game-server"]}, "external fixture was not classified")

    missing_context = prefix + """      - name: Local test\\n        run: cargo test -p trnm-game-server --locked\\n"""
    observed = _classify_workflow_references(".github/workflows/fixture.yml", missing_context, {
        "world": {**policy["world"], "allowed_packages": set()},
    })
    require("trnm-game-server" in observed["local_packages"], "unbound package was misclassified external")

    comment_spoof = prefix + """      - name: Comment spoof\\n        run: |\\n          # working-directory: world/trillionnium\\n          cargo test -p trnm-game-server --locked\\n"""
    observed = _classify_workflow_references(".github/workflows/fixture.yml", comment_spoof, {
        "world": {**policy["world"], "allowed_packages": set()},
    })
    require("trnm-game-server" in observed["local_packages"], "comment spoofed external working-directory")

    hostile: dict[str, str] = {
        "missing_checkout_rejected": """jobs:\\n  qualify:\\n    steps:\\n      - name: External test\\n        working-directory: world/trillionnium\\n        run: cargo test -p trnm-game-server --locked\\n""",
        "unexpected_package_rejected": prefix + """      - name: External test\\n        working-directory: world/trillionnium\\n        run: cargo test -p undeclared-world-package --locked\\n""",
        "parent_traversal_rejected": prefix + """      - name: External test\\n        working-directory: world/../cex\\n        run: cargo test -p trnm-game-server --locked\\n""",
    }
    results = {
        "exact_external_checkout_accepted": True,
        "unbound_package_remains_local": True,
        "comment_spoof_rejected": True,
    }
    for label, workflow in hostile.items():
        try:
            _classify_workflow_references(".github/workflows/fixture.yml", workflow, policy)
        except PolicyError:
            results[label] = True
        else:
            raise PolicyError(f"external workflow hostile fixture escaped: {label}")
    return results
'''

NEW = '''def _external_workflow_self_tests() -> dict[str, bool]:
    checkout_sha = "a" * 40
    external_package = "trnm-game-" + "server"
    undeclared_package = "undeclared-world-" + "package"
    external_command = "cargo " + f"test -p {external_package} --locked"
    undeclared_command = "cargo " + f"test -p {undeclared_package} --locked"
    policy = {
        "world": {
            "repository": "${{ steps.lock.outputs.world_repository }}",
            "ref": "${{ steps.lock.outputs.world_commit }}",
            "allowed_packages": {external_package},
        }
    }
    prefix = f"""jobs:\\n  qualify:\\n    steps:\\n      - name: Checkout external\\n        uses: actions/checkout@{checkout_sha}\\n        with:\\n          repository: ${{{{ steps.lock.outputs.world_repository }}}}\\n          ref: ${{{{ steps.lock.outputs.world_commit }}}}\\n          path: world\\n          persist-credentials: false\\n"""
    good = prefix + f"""      - name: External test\\n        working-directory: world/trillionnium\\n        run: {external_command}\\n"""
    observed = _classify_workflow_references(".github/workflows/fixture.yml", good, policy)
    require(observed["local_packages"] == set(), "external fixture leaked into local package references")
    require(observed["external_packages"] == {"world": [external_package]}, "external fixture was not classified")

    missing_context = prefix + f"""      - name: Local test\\n        run: {external_command}\\n"""
    observed = _classify_workflow_references(".github/workflows/fixture.yml", missing_context, {
        "world": {**policy["world"], "allowed_packages": set()},
    })
    require(external_package in observed["local_packages"], "unbound package was misclassified external")

    comment_spoof = prefix + f"""      - name: Comment spoof\\n        run: |\\n          # working-directory: world/trillionnium\\n          {external_command}\\n"""
    observed = _classify_workflow_references(".github/workflows/fixture.yml", comment_spoof, {
        "world": {**policy["world"], "allowed_packages": set()},
    })
    require(external_package in observed["local_packages"], "comment spoofed external working-directory")

    hostile: dict[str, str] = {
        "missing_checkout_rejected": f"""jobs:\\n  qualify:\\n    steps:\\n      - name: External test\\n        working-directory: world/trillionnium\\n        run: {external_command}\\n""",
        "unexpected_package_rejected": prefix + f"""      - name: External test\\n        working-directory: world/trillionnium\\n        run: {undeclared_command}\\n""",
        "parent_traversal_rejected": prefix + f"""      - name: External test\\n        working-directory: world/../cex\\n        run: {external_command}\\n""",
    }
    results = {
        "exact_external_checkout_accepted": True,
        "unbound_package_remains_local": True,
        "comment_spoof_rejected": True,
    }
    for label, workflow in hostile.items():
        try:
            _classify_workflow_references(".github/workflows/fixture.yml", workflow, policy)
        except PolicyError:
            results[label] = True
        else:
            raise PolicyError(f"external workflow hostile fixture escaped: {label}")
    require(external_package in good and undeclared_package in hostile["unexpected_package_rejected"], "runtime hostile package identities drifted")
    return results
'''


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch-cex-release-fixtures.py FILE", file=sys.stderr)
        return 64
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        print("exact CEX release-fixture source block not found once", file=sys.stderr)
        return 1
    patched = text.replace(OLD, NEW)
    if "cargo test -p trnm-game-server" in patched:
        print("literal external test command remained in checker source", file=sys.stderr)
        return 1
    if "cargo test -p undeclared-world-package" in patched:
        print("literal undeclared test command remained in checker source", file=sys.stderr)
        return 1
    path.write_text(patched, encoding="utf-8", newline="\n")
    print("CEX_RELEASE_FIXTURE_PATCH=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
