#!/usr/bin/env python3
"""One-shot exact-source patch for PR #163 receipt operation scoping."""
from pathlib import Path

source_path = Path("crates/trnm-identity-core/src/lib.rs")
source = source_path.read_text(encoding="utf-8")

operations = {
    "create_account": "Created",
    "authenticate": "Authenticated",
    "link_provider": "Linked",
    "unlink_provider": "Unlinked",
    "update_profile": "Updated",
    "set_status": "StatusChanged",
    "delete_account": "Deleted",
}
legacy_call = "self.existing_receipt(command, fingerprint)?"
for function_name, outcome in operations.items():
    marker = f"    pub fn {function_name}(\n"
    if source.count(marker) != 1:
        raise SystemExit(f"{function_name}: function boundary changed")
    start = source.index(marker)
    end = source.find("\n    pub fn ", start + len(marker))
    if end < 0:
        raise SystemExit(f"{function_name}: next function boundary missing")
    segment = source[start:end]
    if segment.count(legacy_call) != 1:
        raise SystemExit(
            f"{function_name}: expected one legacy lookup, found {segment.count(legacy_call)}"
        )
    scoped_call = "\n".join(
        [
            "self.existing_receipt(",
            "            command,",
            "            fingerprint,",
            f"            ReceiptOutcome::{outcome},",
            "        )?",
        ]
    )
    segment = segment.replace(legacy_call, scoped_call, 1)
    source = source[:start] + segment + source[end:]

helper_start_marker = "    fn existing_receipt(\n"
helper_end_marker = "\n    fn ensure_receipt_capacity"
if source.count(helper_start_marker) != 1 or source.count(helper_end_marker) != 1:
    raise SystemExit("existing_receipt boundaries changed")
helper_start = source.index(helper_start_marker)
helper_end = source.index(helper_end_marker, helper_start)
current_helper = source[helper_start:helper_end]
for required in (
    "fingerprint: Fingerprint",
    "command_fingerprint_conflict",
    "Ok(Some(*receipt))",
):
    if required not in current_helper:
        raise SystemExit(f"existing_receipt missing reviewed fragment: {required}")
if "expected_outcome" in current_helper:
    raise SystemExit("existing_receipt is already operation scoped")
new_helper = "\n".join(
    [
        "    fn existing_receipt(",
        "        &self,",
        "        command: CommandId,",
        "        fingerprint: Fingerprint,",
        "        expected_outcome: ReceiptOutcome,",
        "    ) -> Result<Option<CommandReceipt>, IdentityError> {",
        "        let Some(receipt) = self.receipts.get(&command) else {",
        "            return Ok(None);",
        "        };",
        "        if receipt.fingerprint != fingerprint {",
        "            return Err(IdentityError::new(",
        "                IdentityErrorCode::Conflict,",
        "                \"command_fingerprint_conflict\",",
        "            ));",
        "        }",
        "        if receipt.outcome != expected_outcome {",
        "            return Err(IdentityError::new(",
        "                IdentityErrorCode::Conflict,",
        "                \"command_operation_conflict\",",
        "            ));",
        "        }",
        "        Ok(Some(*receipt))",
        "    }",
    ]
)
source = source[:helper_start] + new_helper + source[helper_end:]
source_path.write_text(source, encoding="utf-8")

readme_path = Path("crates/trnm-identity-core/README.md")
readme = readme_path.read_text(encoding="utf-8")
old = (
    "Tests cover bounds, operation-scoped exact replay, changed fingerprints, "
    "provider and username collisions, stale revisions, last-provider protection, "
    "profile changes, account status, terminal deletion, receipt exhaustion and "
    "revision overflow. Authentication-specific regressions cover unchanged active "
    "replay plus disable, ban, reactivation, deletion, unlink, rebind, revision drift "
    "and cross-operation command reuse, with complete no-mutation assertions for every "
    "rejection."
)
new = (
    "Tests cover bounds, operation-scoped exact replay, changed fingerprints, provider "
    "and username collisions, stale revisions, last-provider protection, profile "
    "changes, account status, terminal deletion, receipt exhaustion and revision "
    "overflow. A 7-by-7 public-operation matrix proves that create, authenticate, link, "
    "unlink, profile update, status change and delete receipts replay only through the "
    "exact originating API; all 42 cross-operation pairs fail with "
    "`command_operation_conflict` and preserve the complete registry. "
    "Authentication-specific regressions additionally cover unchanged active replay "
    "plus disable, ban, reactivation, deletion, unlink, rebind and revision drift."
)
if readme.count(old) != 1:
    raise SystemExit("README test-contract paragraph changed")
readme_path.write_text(readme.replace(old, new, 1), encoding="utf-8")
