from pathlib import Path
import json

def replace_exact(path_text: str, old: str, new: str, expected: int = 1) -> None:
    path = Path(path_text)
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != expected:
        raise SystemExit(
            f"{path_text}: expected {expected} anchors, found {count} for {old!r}"
        )
    path.write_text(source.replace(old, new, expected), encoding="utf-8")

replace_exact(
    "crates/trnm-persistence-pg/Cargo.toml",
    '''r2d2_postgres = "=0.18.2"
trnm-contracts = { path = "../trnm-contracts" }
''',
    '''r2d2_postgres = "=0.18.2"
trnm-contracts = { path = "../trnm-contracts" }
trnm-session-core = { path = "../trnm-session-core" }
trnm-token-jwt-adapter = { path = "../trnm-token-jwt-adapter" }
''',
)

replace_exact(
    "crates/trnm-persistence-pg/src/lib.rs",
    '''mod pool;

pub use pool::{PgPool, PgPoolConfig, PgPoolSnapshot, PgTlsConfig};
''',
    '''mod pool;
mod session;

pub use pool::{PgPool, PgPoolConfig, PgPoolSnapshot, PgTlsConfig};
pub use session::{
    CreateSessionFamily, RefreshRotationOutcome, RefreshTokenCredential, RotateRefreshToken,
    SessionFamilyRecord,
};
''',
)

replace_exact(
    "crates/trnm-token-jwt-adapter/src/lib.rs",
    '''pub use jwt::{
    issue_epoch, issue_legacy, verify, ClaimMapping, JwtError, KeyRing, SecretKey, TokenRoute,
    VerificationProfile, VerifiedPrincipal, VerifiedToken, EPOCH_KEY_ID_PREFIX,
};
''',
    '''pub use jwt::{
    issue_epoch, issue_legacy, verify, ClaimMapping, JwtError, KeyRing, SecretKey, TokenRoute,
    VerificationProfile, VerifiedPrincipal, VerifiedToken, EPOCH_KEY_ID_PREFIX,
};

#[must_use]
pub fn sha256_digest(input: &[u8]) -> [u8; 32] {
    sha256::digest(input)
}
''',
)

replace_exact(
    "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs",
    '''pub(crate) mod app;
pub(crate) mod codec;
''',
    '''pub(crate) mod app;
pub(crate) mod auth;
pub(crate) mod codec;
''',
)

for checker in (
    "scripts/check-rust-foundation.py",
    "scripts/check-trnm-server.py",
):
    replace_exact(
        checker,
        '''        "trnm-contracts": {"path": "../trnm-contracts"},
''',
        '''        "trnm-contracts": {"path": "../trnm-contracts"},
        "trnm-session-core": {"path": "../trnm-session-core"},
        "trnm-token-jwt-adapter": {"path": "../trnm-token-jwt-adapter"},
''',
    )

replace_exact(
    "scripts/check-trnm-server.py",
    '''REQUIRED_FILES = {
    PERSISTENCE_ROOT / "pool.rs",
''',
    '''REQUIRED_FILES = {
    PERSISTENCE_ROOT / "pool.rs",
    PERSISTENCE_ROOT / "session.rs",
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''    MODULE_ROOT / "app.rs",
    MODULE_ROOT / "codec.rs",
''',
    '''    MODULE_ROOT / "app.rs",
    MODULE_ROOT / "auth.rs",
    MODULE_ROOT / "codec.rs",
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''    "jitter_remains_inside_half_to_full_backoff",
    "default_pool_policy_is_bounded_and_valid",
''',
    '''    "jitter_remains_inside_half_to_full_backoff",
    "create_and_rotation_validation_fail_closed",
    "persisted_revocation_reason_mapping_is_exact",
    "generic_session_failure_does_not_disclose_identity_state",
    "strict_epoch_access_token_yields_session_principal",
    "malformed_tampered_and_incomplete_access_tokens_fail_closed",
    "refresh_credential_is_bounded_id_prefixed_and_hashed",
    "verifier_debug_redacts_key_material",
    "default_pool_policy_is_bounded_and_valid",
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''        "crates/trnm-persistence-pg/src/bin/trnm-server.rs": [
''',
    '''        "crates/trnm-persistence-pg/src/session.rs": [
            "pub struct CreateSessionFamily",
            "pub enum RefreshRotationOutcome",
            "IsolationLevel::Serializable",
            "FOR UPDATE",
            "refresh_compare_and_swap_failed",
            "revoked_reason = 2",
            "RevocationReason::ReplayDetected",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs": [
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''        "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs": [
''',
    '''        "crates/trnm-persistence-pg/src/bin/trnm_server/auth.rs": [
            "pub struct AccessTokenVerifier",
            "allow_legacy_without_key_id: false",
            "max_lifetime_seconds: Some(15 * 60)",
            "claim_string(claims, \\"sid\\")",
            "claim_unsigned(claims, \\"sgn\\")",
            "sha256_digest(value.as_bytes())",
            "\\"session_authentication_failed\\"",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs": [
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''            "pub(crate) mod pool;",
            "pub(crate) mod websocket;",
''',
    '''            "pub(crate) mod auth;",
            "pub(crate) mod pool;",
            "pub(crate) mod websocket;",
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''    if test_count < 39:
        fail(f"expected at least 39 server/pool source tests, got {test_count}")
''',
    '''    if test_count < 46:
        fail(f"expected at least 46 server/session/pool source tests, got {test_count}")
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''        "retry_jitter_source_candidate",
    ]
''',
    '''        "retry_jitter_source_candidate",
        "access_token_verifier_source_candidate",
        "refresh_family_repository_source_candidate",
    ]
''',
)
replace_exact(
    "scripts/check-trnm-server.py",
    '''                "retry_jitter_source_candidate": True,
                "cargo_executed_here": False,
''',
    '''                "retry_jitter_source_candidate": True,
                "access_token_verifier_source_candidate": True,
                "refresh_family_repository_source_candidate": True,
                "cargo_executed_here": False,
''',
)

status_path = Path("docs/status/TRNM_SERVER_STATUS.json")
status = json.loads(status_path.read_text(encoding="utf-8"))
for path in (
    "crates/trnm-persistence-pg/src/session.rs",
    "crates/trnm-persistence-pg/src/bin/trnm_server/auth.rs",
):
    if path not in status["source_paths"]:
        status["source_paths"].append(path)
for item in (
    "strict epoch-routed access-token verifier with issuer, audience, lifetime, subject, token-id, family and generation checks",
    "opaque ID-prefixed refresh credentials hashed before persistence",
    "serializable refresh-family creation, rotation, replay revocation and logout repository operations",
    "generic unauthenticated identity-state failures that do not disclose family, token or user existence",
):
    if item not in status["implemented_source"]:
        status["implemented_source"].append(item)
old_missing = "session JWT verification and refresh-family socket revocation"
new_missing = (
    "HTTP middleware/endpoints and persistent socket revocation integration for the "
    "new session verifier and refresh-family repository"
)
if old_missing in status["not_implemented_or_verified"]:
    index = status["not_implemented_or_verified"].index(old_missing)
    status["not_implemented_or_verified"][index] = new_missing
status["claims"]["access_token_verifier_source_candidate"] = True
status["claims"]["refresh_family_repository_source_candidate"] = True
status["claims"]["session_integrated"] = False
status_path.write_text(
    json.dumps(status, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

milestone_path = Path("docs/roadmap/NEXT_MILESTONE.json")
milestone = json.loads(milestone_path.read_text(encoding="utf-8"))
found = set()
def walk(value):
    if isinstance(value, dict):
        identifier = value.get("id")
        if identifier in {"TG-V3-021", "TG-V3-022"}:
            value["status"] = "source-candidate"
            found.add(identifier)
        for child in value.values():
            walk(child)
    elif isinstance(value, list):
        for child in value:
            walk(child)
walk(milestone)
if found != {"TG-V3-021", "TG-V3-022"}:
    raise SystemExit(f"NEXT_MILESTONE task IDs not found: {sorted(found)}")
milestone_path.write_text(
    json.dumps(milestone, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

temporary = Path(".github/workflows/temporary-session-foundation.yml")
if not temporary.is_file():
    raise SystemExit("temporary session workflow missing")
temporary.unlink()
