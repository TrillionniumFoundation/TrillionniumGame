from __future__ import annotations

import json
import runpy
from pathlib import Path


def replace_exact(path_text: str, old: str, new: str, expected: int = 1) -> None:
    path = Path(path_text)
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != expected:
        raise SystemExit(
            f"{path_text}: expected {expected} anchors, found {count} for {old!r}"
        )
    path.write_text(source.replace(old, new, expected), encoding="utf-8")


patch_path = Path(".github/tmp/apply-session-foundation.py")
patch_source = patch_path.read_text(encoding="utf-8")
start_marker = "for checker in (\n"
end_marker = (
    "\nreplace_exact(\n"
    '    "scripts/check-trnm-server.py",\n'
    "    '''REQUIRED_FILES"
)
start = patch_source.find(start_marker)
end = patch_source.find(end_marker, start)
if start < 0 or end < 0 or patch_source.find(start_marker, start + 1) >= 0:
    raise SystemExit("ambiguous dependency-patch loop in session patch")
replacement = 'replace_exact(\n    "scripts/check-rust-foundation.py",\n    """    "crates/trnm-persistence-pg": {\n        "native-tls": "=0.2.18",\n        "postgres": "=0.19.14",\n        "postgres-native-tls": "=0.5.3",\n        "r2d2": "=0.8.10",\n        "r2d2_postgres": "=0.18.2",\n        "trnm-contracts": {"path": "../trnm-contracts"},\n    },\n""",\n    """    "crates/trnm-persistence-pg": {\n        "native-tls": "=0.2.18",\n        "postgres": "=0.19.14",\n        "postgres-native-tls": "=0.5.3",\n        "r2d2": "=0.8.10",\n        "r2d2_postgres": "=0.18.2",\n        "trnm-contracts": {"path": "../trnm-contracts"},\n        "trnm-session-core": {"path": "../trnm-session-core"},\n        "trnm-token-jwt-adapter": {"path": "../trnm-token-jwt-adapter"},\n    },\n""",\n)\n\nreplace_exact(\n    "scripts/check-trnm-server.py",\n    """        "trnm-contracts": {"path": "../trnm-contracts"},\n""",\n    """        "trnm-contracts": {"path": "../trnm-contracts"},\n        "trnm-session-core": {"path": "../trnm-session-core"},\n        "trnm-token-jwt-adapter": {"path": "../trnm-token-jwt-adapter"},\n""",\n)\n'
patch_path.write_text(
    patch_source[:start] + replacement + patch_source[end:],
    encoding="utf-8",
)
runpy.run_path(str(patch_path), run_name="__main__")

session = Path("crates/trnm-persistence-pg/src/session.rs")
session_source = session.read_text(encoding="utf-8")
replace_map = {
    "use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};\n"
    "use trnm_session_core::{RefreshTokenId, RevocationReason, SessionFamilyId};\n":
    "use trnm_contracts::{\n"
    "    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,\n"
    "};\n"
    "use trnm_session_core::RevocationReason;\n",
    "RevocationReason::Compromised": "RevocationReason::CredentialReset",
    "RevocationReason::ReplayDetected": "RevocationReason::RefreshReplay",
    "RevocationReason::Administrative": "RevocationReason::Administrator",
}
for old, new in replace_map.items():
    count = session_source.count(old)
    if count == 0:
        raise SystemExit(f"{session}: missing normalization anchor {old!r}")
    session_source = session_source.replace(old, new)

expired_arm = (
    '        RevocationReason::Expired => '
    'Err(invalid("expired_revocation_reason_not_persisted")),\n'
)
if session_source.count(expired_arm) != 1:
    raise SystemExit("expired revocation arm anchor mismatch")
session_source = session_source.replace(expired_arm, "", 1)
needle = "revocation_reason_code(RevocationReason::Expired)"
if session_source.count(needle) != 1:
    raise SystemExit("expired revocation test call mismatch")
call = session_source.index(needle)
assertion_start = session_source.rfind("        assert_eq!(", 0, call)
assertion_end = session_source.find("        );\n", call)
if assertion_start < 0 or assertion_end < 0:
    raise SystemExit("expired revocation assertion boundaries missing")
assertion_end += len("        );\n")
session.write_text(
    session_source[:assertion_start] + session_source[assertion_end:],
    encoding="utf-8",
)

binary_auth = Path("crates/trnm-persistence-pg/src/bin/trnm_server/auth.rs")
library_auth = Path("crates/trnm-persistence-pg/src/auth.rs")
if not binary_auth.is_file() or library_auth.exists():
    raise SystemExit("auth library move precondition failed")
auth_source = binary_auth.read_text(encoding="utf-8")
old_auth_import = (
    "use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};\n"
    "use trnm_session_core::{RefreshTokenId, SessionFamilyId};\n"
)
new_auth_import = (
    "use trnm_contracts::{\n"
    "    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,\n"
    "};\n"
)
if auth_source.count(old_auth_import) != 1:
    raise SystemExit("auth source import anchor mismatch")
library_auth.write_text(
    auth_source.replace(old_auth_import, new_auth_import, 1),
    encoding="utf-8",
)
binary_auth.unlink()

replace_exact(
    "crates/trnm-persistence-pg/src/lib.rs",
    "mod pool;\nmod session;\n",
    "mod auth;\nmod pool;\nmod session;\n",
)
replace_exact(
    "crates/trnm-persistence-pg/src/lib.rs",
    "pub use pool::{PgPool, PgPoolConfig, PgPoolSnapshot, PgTlsConfig};\n",
    "pub use auth::{\n"
    "    parse_refresh_credential, AccessTokenVerifier, ParsedRefreshCredential,\n"
    "    SessionPrincipal,\n"
    "};\n"
    "pub use pool::{PgPool, PgPoolConfig, PgPoolSnapshot, PgTlsConfig};\n",
)
replace_exact(
    "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs",
    "pub(crate) mod auth;\n",
    "",
)
replace_exact(
    "scripts/check-trnm-server.py",
    '    MODULE_ROOT / "auth.rs",\n',
    '    PERSISTENCE_ROOT / "auth.rs",\n',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '        "crates/trnm-persistence-pg/src/bin/trnm_server/auth.rs": [\n',
    '        "crates/trnm-persistence-pg/src/auth.rs": [\n',
)
replace_exact(
    "scripts/check-trnm-server.py",
    "RevocationReason::ReplayDetected",
    "RevocationReason::RefreshReplay",
)

status_path = Path("docs/status/TRNM_SERVER_STATUS.json")
status = json.loads(status_path.read_text(encoding="utf-8"))
binary_path = "crates/trnm-persistence-pg/src/bin/trnm_server/auth.rs"
library_path = "crates/trnm-persistence-pg/src/auth.rs"
paths = status.get("source_paths")
if not isinstance(paths, list) or paths.count(binary_path) != 1:
    raise SystemExit("server status binary auth path mismatch")
paths[paths.index(binary_path)] = library_path
status_path.write_text(
    json.dumps(status, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

for path_text in (
    ".github/workflows/temporary-session-foundation.yml",
    ".github/workflows/temporary-session-foundation-library.yml",
    ".github/tmp/apply-session-foundation.py",
    ".github/tmp/finalize-session-foundation.py",
):
    path = Path(path_text)
    if path.exists():
        path.unlink()
