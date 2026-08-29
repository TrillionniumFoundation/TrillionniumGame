# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import leaf, license_class, manifest, source_ref, strings_with_lines

GENERATOR = "trillionniumgame-provider-iap-denominator"

PROVIDER_FILES = [
    "social/social.go",
    "server/api_authenticate.go",
    "server/api_link.go",
    "server/api_unlink.go",
]
IAP_FILES = [
    "iap/iap.go",
    "iap/iap_samsung.go",
    "server/api_purchase.go",
    "server/api_subscription.go",
]

FUNC_RE = re.compile(r"(?m)^func\s+(?:\([^\n)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
TYPE_RE = re.compile(r"(?m)^type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface|[A-Za-z0-9_.*\[\]]+)")
DECL_RE = re.compile(r"(?m)^(const|var)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
HTTP_METHOD_RE = re.compile(r"\bhttp\.Method(Get|Post|Put|Patch|Delete|Head|Options)\b")


def _scan_file(root: Path, relative: str, denominator: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = (root / relative).read_bytes()
    text = data.decode("utf-8")
    leaves: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    domain = "provider" if denominator == "DEN-PROVIDERS" else "iap"
    owner = "identity-provider" if domain == "provider" else "payments"
    workstream = "W3" if domain == "provider" else "W12"

    license_state = license_class(data)
    if license_state != "apache-2.0":
        manual.append({"class": "restricted_source", "symbol": relative, "license_class": license_state, "source": source_ref(root, relative).to_dict()})
        return leaves, manual

    for match in FUNC_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        symbol = f"{relative}:{match.group(1)}"
        leaves.append(leaf("D8", f"{domain}_function", symbol, source_ref(root, relative, line, line), {"name": match.group(1)}, owner=owner, workstream=workstream, task="TG-W0-002"))
    for match in TYPE_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        symbol = f"{relative}:{match.group(1)}"
        leaves.append(leaf("D8", f"{domain}_type", symbol, source_ref(root, relative, line, line), {"name": match.group(1), "kind": match.group(2)}, owner=owner, workstream=workstream, task="TG-W0-002"))
    for match in DECL_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        symbol = f"{relative}:{match.group(2)}"
        leaves.append(leaf("D8", f"{domain}_{match.group(1)}", symbol, source_ref(root, relative, line, line), {"name": match.group(2), "kind": match.group(1)}, owner=owner, workstream=workstream, task="TG-W0-002"))
    for match in HTTP_METHOD_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        method = match.group(1).upper()
        symbol = f"{relative}:http-method:{method}@{line}:{match.start() - text.rfind(chr(10), 0, match.start())}"
        leaves.append(leaf("D8", f"{domain}_http_method_candidate", symbol, source_ref(root, relative, line, line), {"method": method}, owner=owner, workstream=workstream, task="TG-W0-002"))

    for value, line in strings_with_lines(text):
        lower = value.lower()
        item_class: str | None = None
        if value.startswith(("https://", "http://")):
            item_class = f"{domain}_endpoint_candidate"
        elif domain == "iap" and any(lower.lstrip().startswith(prefix) for prefix in ("select ", "insert ", "update ", "delete ")):
            item_class = "iap_database_statement_candidate"
        elif domain == "iap" and any(marker in lower for marker in ("purchase", "subscription", "refund", "renew", "void", "receipt", "transaction")):
            item_class = "iap_state_or_field_candidate"
        elif domain == "provider" and any(marker in lower for marker in ("apple", "google", "facebook", "steam", "gamecenter", "custom", "device", "email")) and len(value) < 256:
            item_class = "provider_identifier_candidate"
        if item_class:
            symbol = f"{relative}:{item_class}@{line}:{value[:80]}"
            leaves.append(leaf("D8", item_class, symbol, source_ref(root, relative, line, line), {"value": value}, owner=owner, workstream=workstream, task="TG-W0-002"))

    if not leaves:
        manual.append({"class": "empty_source_inventory", "symbol": relative})
    return leaves, manual


def extract_provider_iap(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    provider_leaves: list[dict[str, Any]] = []
    provider_manual: list[dict[str, Any]] = []
    iap_leaves: list[dict[str, Any]] = []
    iap_manual: list[dict[str, Any]] = []
    for relative in PROVIDER_FILES:
        leaves, manual = _scan_file(root, relative, "DEN-PROVIDERS")
        provider_leaves.extend(leaves)
        provider_manual.extend(manual)
    for relative in IAP_FILES:
        leaves, manual = _scan_file(root, relative, "DEN-IAP")
        iap_leaves.extend(leaves)
        iap_manual.extend(manual)

    provider_manual.append({"class": "provider_blackbox_matrix_required", "symbol": "all-providers", "reason": "Source inventory does not prove token validation, callback authenticity, retry, timeout, key rotation or sandbox behavior."})
    iap_manual.append({"class": "iap_blackbox_matrix_required", "symbol": "all-iap-providers", "reason": "Source inventory does not prove receipt authenticity, duplicate-value prevention, refund/void/renewal reconciliation or ambiguous outcomes."})
    providers = manifest("DEN-PROVIDERS", "D8", provider_leaves, provider_manual, generator=GENERATOR)
    iap = manifest("DEN-IAP", "D8", iap_leaves, iap_manual, generator=GENERATOR)
    provider_names = sorted({name for item in provider_leaves for name in re.findall(r"(?i)(apple|google|facebook|steam|gamecenter|samsung|huawei)", item["symbol"])})
    reconciliation = {"schema": "trillionnium.provider-iap-reconciliation.v1", "status": "candidate-unclassified", "provider_names_observed": provider_names, "provider_leaf_count": providers["leaf_count"], "iap_leaf_count": iap["leaf_count"], "sg1_eligible": False, "compatibility_credit": False}
    return providers, iap, reconciliation
