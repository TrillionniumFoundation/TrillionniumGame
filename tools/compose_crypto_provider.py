#!/usr/bin/env python3
"""Move active access-token authentication behind the opaque provider contract."""
from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_dependency(text: str, line: str) -> str:
    if line in text:
        return text
    if "[dependencies]\n" not in text:
        anchor = "\n[lib]\n"
        require(anchor in text, "manifest has no dependency or lib anchor")
        return text.replace(anchor, f"\n[dependencies]\n{line}\n{anchor}", 1)
    return text.replace("[dependencies]\n", f"[dependencies]\n{line}\n", 1)


def update_provider_crate(root: Path) -> None:
    manifest_path = root / "crates/trnm-token-crypto-provider/Cargo.toml"
    manifest = read(manifest_path)
    for line in (
        'hmac = "=0.13.0"',
        'sha2 = "=0.11.0"',
        'subtle = "=2.6.1"',
    ):
        manifest = ensure_dependency(manifest, line)
    write(manifest_path, manifest)

    lib_path = root / "crates/trnm-token-crypto-provider/src/lib.rs"
    lib = read(lib_path)
    if "mod software;" not in lib:
        anchor = "mod lifecycle;\n"
        require(anchor in lib, "provider module anchor missing")
        lib = lib.replace(anchor, anchor + "mod software;\n", 1)
    export = "pub use software::{SecretKeyMaterial, SoftwareHs256Provider};\n"
    if export not in lib:
        anchor = "use core::fmt;\n"
        require(anchor in lib, "provider export anchor missing")
        lib = lib.replace(anchor, export + "\n" + anchor, 1)
    if "InvalidKeyMaterial," not in lib:
        anchor = "    InvalidKeyEpoch,\n"
        require(anchor in lib, "ProviderError enum anchor missing")
        lib = lib.replace(anchor, anchor + "    InvalidKeyMaterial,\n", 1)
    if "Self::InvalidKeyMaterial" not in lib:
        anchor = '            Self::InvalidKeyEpoch => formatter.write_str("key epoch must be greater than zero"),\n'
        require(anchor in lib, "ProviderError display anchor missing")
        lib = lib.replace(
            anchor,
            anchor
            + '            Self::InvalidKeyMaterial => formatter.write_str("invalid software key material"),\n',
            1,
        )
    write(lib_path, lib)

    software = textwrap.dedent(
        '''\
        use core::fmt;
        use std::collections::BTreeMap;
        use std::sync::RwLock;

        use hmac::{Hmac, Mac};
        use sha2::Sha256;
        use subtle::ConstantTimeEq;

        use super::{
            validate_signing_input, Hs256Provider, KeyDomain, KeyReference, ProviderError,
            Signature32, VerificationDecision, SIGNATURE_BYTES,
        };

        type HmacSha256 = Hmac<Sha256>;
        const MINIMUM_KEY_BYTES: usize = 16;
        const MAXIMUM_KEY_BYTES: usize = 4_096;

        #[derive(Eq, PartialEq)]
        pub struct SecretKeyMaterial(Vec<u8>);

        impl SecretKeyMaterial {
            pub fn new(value: impl Into<Vec<u8>>) -> Result<Self, ProviderError> {
                let value = value.into();
                if !(MINIMUM_KEY_BYTES..=MAXIMUM_KEY_BYTES).contains(&value.len()) {
                    return Err(ProviderError::InvalidKeyMaterial);
                }
                Ok(Self(value))
            }

            fn expose(&self) -> &[u8] {
                &self.0
            }
        }

        impl fmt::Debug for SecretKeyMaterial {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct("SecretKeyMaterial")
                    .field("bytes", &"<redacted>")
                    .field("length", &self.0.len())
                    .finish()
            }
        }

        impl Drop for SecretKeyMaterial {
            fn drop(&mut self) {
                self.0.fill(0);
            }
        }

        #[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
        struct SoftwareKeyId {
            domain: KeyDomain,
            handle: String,
            epoch: Option<u32>,
        }

        impl From<&KeyReference> for SoftwareKeyId {
            fn from(value: &KeyReference) -> Self {
                Self {
                    domain: value.domain,
                    handle: value.handle.as_str().to_owned(),
                    epoch: value.epoch,
                }
            }
        }

        #[derive(Default)]
        pub struct SoftwareHs256Provider {
            keys: RwLock<BTreeMap<SoftwareKeyId, SecretKeyMaterial>>,
        }

        impl SoftwareHs256Provider {
            #[must_use]
            pub fn new() -> Self {
                Self::default()
            }

            pub fn insert_key(
                &self,
                key: &KeyReference,
                material: impl Into<Vec<u8>>,
            ) -> Result<(), ProviderError> {
                let material = SecretKeyMaterial::new(material)?;
                self.keys
                    .write()
                    .map_err(|_| ProviderError::Internal)?
                    .insert(SoftwareKeyId::from(key), material);
                Ok(())
            }

            pub fn remove_key(&self, key: &KeyReference) -> Result<bool, ProviderError> {
                Ok(self
                    .keys
                    .write()
                    .map_err(|_| ProviderError::Internal)?
                    .remove(&SoftwareKeyId::from(key))
                    .is_some())
            }

            #[must_use]
            pub fn key_count(&self) -> usize {
                self.keys.read().map_or(0, |keys| keys.len())
            }

            fn tag(
                &self,
                key: &KeyReference,
                exact_signing_input: &[u8],
            ) -> Result<[u8; SIGNATURE_BYTES], ProviderError> {
                validate_signing_input(exact_signing_input)?;
                let keys = self.keys.read().map_err(|_| ProviderError::Internal)?;
                let material = keys
                    .get(&SoftwareKeyId::from(key))
                    .ok_or(ProviderError::KeyUnavailable)?;
                let mut mac = HmacSha256::new_from_slice(material.expose())
                    .map_err(|_| ProviderError::InvalidKeyMaterial)?;
                mac.update(exact_signing_input);
                let output = mac.finalize().into_bytes();
                let mut result = [0_u8; SIGNATURE_BYTES];
                result.copy_from_slice(&output);
                Ok(result)
            }
        }

        impl fmt::Debug for SoftwareHs256Provider {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct("SoftwareHs256Provider")
                    .field("key_count", &self.key_count())
                    .field("key_material", &"<redacted>")
                    .finish()
            }
        }

        impl Hs256Provider for SoftwareHs256Provider {
            fn sign(
                &self,
                key: &KeyReference,
                exact_signing_input: &[u8],
            ) -> Result<Signature32, ProviderError> {
                self.tag(key, exact_signing_input).map(Signature32::new)
            }

            fn verify(
                &self,
                key: &KeyReference,
                exact_signing_input: &[u8],
                signature: &Signature32,
            ) -> Result<VerificationDecision, ProviderError> {
                let expected = self.tag(key, exact_signing_input)?;
                Ok(if bool::from(expected.ct_eq(signature.as_bytes())) {
                    VerificationDecision::Accepted
                } else {
                    VerificationDecision::Rejected
                })
            }
        }

        #[cfg(test)]
        mod tests {
            use super::*;
            use crate::{KeyHandle, KeyReference};

            fn key(domain: KeyDomain, epoch: u32) -> KeyReference {
                KeyReference::new(
                    domain,
                    KeyHandle::new(format!("software://test/{epoch}")).unwrap(),
                    Some(epoch),
                )
                .unwrap()
            }

            #[test]
            fn sign_verify_and_domain_separation_are_explicit() {
                let provider = SoftwareHs256Provider::new();
                let access = key(KeyDomain::AccessToken, 7);
                let socket = key(KeyDomain::Socket, 7);
                provider.insert_key(&access, vec![0x11; 32]).unwrap();
                provider.insert_key(&socket, vec![0x22; 32]).unwrap();
                let input = b"header.payload";
                let access_tag = provider.sign(&access, input).unwrap();
                assert_eq!(
                    provider.verify(&access, input, &access_tag).unwrap(),
                    VerificationDecision::Accepted
                );
                assert_eq!(
                    provider.verify(&socket, input, &access_tag).unwrap(),
                    VerificationDecision::Rejected
                );
            }

            #[test]
            fn missing_removed_and_invalid_keys_fail_closed() {
                let provider = SoftwareHs256Provider::new();
                let access = key(KeyDomain::AccessToken, 9);
                assert_eq!(
                    provider.sign(&access, b"header.payload").unwrap_err(),
                    ProviderError::KeyUnavailable
                );
                assert_eq!(
                    provider.insert_key(&access, vec![0; 15]).unwrap_err(),
                    ProviderError::InvalidKeyMaterial
                );
                provider.insert_key(&access, vec![0x33; 32]).unwrap();
                assert!(provider.remove_key(&access).unwrap());
                assert_eq!(provider.key_count(), 0);
            }

            #[test]
            fn debug_output_never_contains_key_material() {
                let provider = SoftwareHs256Provider::new();
                let access = key(KeyDomain::AccessToken, 11);
                provider
                    .insert_key(&access, b"0123456789abcdef0123456789abcdef".to_vec())
                    .unwrap();
                let rendered = format!("{provider:?}");
                assert!(rendered.contains("<redacted>"));
                assert!(!rendered.contains("0123456789abcdef"));
            }
        }
        '''
    )
    write(root / "crates/trnm-token-crypto-provider/src/software.rs", software)


def update_persistence_manifest(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/Cargo.toml"
    text = read(path)
    for line in (
        'trnm-token-crypto-provider = { path = "../trnm-token-crypto-provider" }',
        'trnm-token-jwt-provider-adapter = { path = "../trnm-token-jwt-provider-adapter" }',
    ):
        text = ensure_dependency(text, line)
    write(path, text)


def provider_backed_prefix() -> str:
    return textwrap.dedent(
        '''\
        use std::collections::{BTreeMap, BTreeSet};
        use std::fmt;
        use std::sync::Arc;

        use openssl::hash::{hash, MessageDigest};
        use trnm_contracts::{
            Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
        };
        use trnm_token_crypto_provider::{
            Hs256Provider, KeyDomain, KeyHandle, KeyReference, SoftwareHs256Provider,
        };
        use trnm_token_jwt_adapter::json::{JsonLimits, JsonValue};
        use trnm_token_jwt_provider_adapter::{
            authenticate, AuthenticationError, AuthenticationProfile, KeyResolver, TokenRoute,
        };

        const MAX_TOKEN_BYTES: usize = 32 * 1_024;
        const MAX_HEADER_BYTES: usize = 1_024;
        const MAX_PAYLOAD_BYTES: usize = 16 * 1_024;
        const MINIMUM_KEY_BYTES: usize = 16;
        const MAXIMUM_KEY_BYTES: usize = 4_096;
        const CLOCK_SKEW_SECONDS: i64 = 30;
        const MAX_ACCESS_TOKEN_LIFETIME_SECONDS: u64 = 15 * 60;

        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub struct SessionPrincipal {
            pub user: UserId,
            pub family: SessionFamilyId,
            pub generation: u64,
            pub access_token_id: [u8; 16],
            pub expires_at_unix_seconds: i64,
        }

        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub struct ParsedRefreshCredential {
            pub id: RefreshTokenId,
            pub digest: Digest32,
        }

        #[derive(Clone, Debug)]
        struct FixedAccessKeyResolver {
            epoch: u32,
            key: KeyReference,
        }

        impl KeyResolver for FixedAccessKeyResolver {
            fn resolve(
                &self,
                domain: KeyDomain,
                route: TokenRoute,
            ) -> Result<KeyReference, AuthenticationError> {
                if domain == KeyDomain::AccessToken && route == TokenRoute::Epoch(self.epoch) {
                    Ok(self.key.clone())
                } else {
                    Err(AuthenticationError::UnknownKey)
                }
            }
        }

        pub struct AccessTokenVerifier {
            issuer: String,
            audience: String,
            epoch: u32,
            resolver: FixedAccessKeyResolver,
            provider: Arc<dyn Hs256Provider>,
        }

        impl AccessTokenVerifier {
            pub fn from_epoch_key(
                issuer: String,
                audience: String,
                epoch: u32,
                key: Vec<u8>,
            ) -> Result<Self, DomainError> {
                if !(MINIMUM_KEY_BYTES..=MAXIMUM_KEY_BYTES).contains(&key.len()) {
                    return Err(configuration_error("access_token_profile_invalid"));
                }
                let reference = KeyReference::new(
                    KeyDomain::AccessToken,
                    KeyHandle::new(format!("software://access-token/{epoch}"))
                        .map_err(|_| configuration_error("access_token_profile_invalid"))?,
                    Some(epoch),
                )
                .map_err(|_| configuration_error("access_token_profile_invalid"))?;
                let provider = Arc::new(SoftwareHs256Provider::new());
                provider
                    .insert_key(&reference, key)
                    .map_err(|_| configuration_error("access_token_profile_invalid"))?;
                Self::from_provider(issuer, audience, epoch, reference, provider)
            }

            pub fn from_provider(
                issuer: String,
                audience: String,
                epoch: u32,
                key: KeyReference,
                provider: Arc<dyn Hs256Provider>,
            ) -> Result<Self, DomainError> {
                if issuer.is_empty()
                    || audience.is_empty()
                    || issuer.len() > 512
                    || audience.len() > 512
                    || epoch == 0
                    || key.domain != KeyDomain::AccessToken
                    || key.epoch != Some(epoch)
                {
                    return Err(configuration_error("access_token_profile_invalid"));
                }
                Ok(Self {
                    issuer,
                    audience,
                    epoch,
                    resolver: FixedAccessKeyResolver { epoch, key },
                    provider,
                })
            }

            pub fn verify_bearer(
                &self,
                authorization: Option<&str>,
                now_unix_seconds: i64,
            ) -> Result<SessionPrincipal, DomainError> {
                let token = authorization
                    .and_then(|value| value.strip_prefix("Bearer "))
                    .filter(|value| {
                        !value.is_empty()
                            && !value
                                .bytes()
                                .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
                    })
                    .ok_or_else(unauthenticated)?;
                let profile = AuthenticationProfile {
                    domain: KeyDomain::AccessToken,
                    max_token_bytes: MAX_TOKEN_BYTES,
                    max_header_bytes: MAX_HEADER_BYTES,
                    max_payload_bytes: MAX_PAYLOAD_BYTES,
                    allow_legacy_without_key_id: false,
                    reject_unknown_header_fields: true,
                    json_limits: JsonLimits::default(),
                };
                let authenticated = authenticate(
                    token,
                    &profile,
                    &self.resolver,
                    self.provider.as_ref(),
                )
                .map_err(|_| unauthenticated())?;
                if authenticated.route != TokenRoute::Epoch(self.epoch)
                    || authenticated.key != self.resolver.key
                {
                    return Err(unauthenticated());
                }
                let claims = authenticated
                    .parse_claims(JsonLimits::default())
                    .map_err(|_| unauthenticated())?;
                let claims = claims.as_object().ok_or_else(unauthenticated)?;
                self.validate_claims(claims, now_unix_seconds)
            }

            fn validate_claims(
                &self,
                claims: &BTreeMap<String, JsonValue>,
                now_unix_seconds: i64,
            ) -> Result<SessionPrincipal, DomainError> {
                if claim_string(claims, "iss")? != self.issuer
                    || !audience_contains(claims.get("aud"), &self.audience)?
                    || claim_unsigned(claims, "trnm_kep")? != u64::from(self.epoch)
                {
                    return Err(unauthenticated());
                }

                let issued_at = claim_numeric_date(claims, "iat")?;
                let expires_at_unix_seconds = claim_numeric_date(claims, "exp")?;
                let not_before = optional_numeric_date(claims, "nbf")?;
                let now = i128::from(now_unix_seconds);
                let skew = i128::from(CLOCK_SKEW_SECONDS);
                if now >= i128::from(expires_at_unix_seconds) + skew
                    || now + skew < i128::from(issued_at)
                    || not_before.is_some_and(|value| now + skew < i128::from(value))
                    || expires_at_unix_seconds <= issued_at
                {
                    return Err(unauthenticated());
                }
                let lifetime = u64::try_from(expires_at_unix_seconds - issued_at)
                    .map_err(|_| unauthenticated())?;
                if lifetime > MAX_ACCESS_TOKEN_LIFETIME_SECONDS {
                    return Err(unauthenticated());
                }

                let user = UserId::new(parse_lower_hex::<16>(claim_string(claims, "sub")?)?);
                let access_token_id = parse_lower_hex::<16>(claim_string(claims, "jti")?)?;
                let family =
                    SessionFamilyId::new(parse_lower_hex::<16>(claim_string(claims, "sid")?)?);
                let generation = claim_unsigned(claims, "sgn")?;
                if user.is_zero() || family.is_zero() || access_token_id.iter().all(|byte| *byte == 0) {
                    return Err(unauthenticated());
                }

                Ok(SessionPrincipal {
                    user,
                    family,
                    generation,
                    access_token_id,
                    expires_at_unix_seconds,
                })
            }
        }

        impl fmt::Debug for AccessTokenVerifier {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct("AccessTokenVerifier")
                    .field("issuer", &self.issuer)
                    .field("audience", &self.audience)
                    .field("active_epoch", &self.epoch)
                    .field("key_reference", &self.resolver.key)
                    .field("provider", &"<opaque-provider>")
                    .finish()
            }
        }

        '''
    )


def rewrite_auth(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/src/auth.rs"
    source = read(path)
    if "pub fn from_provider(" in source and "trnm_token_jwt_provider_adapter" in source:
        return
    marker = "pub fn parse_refresh_credential"
    index = source.find(marker)
    require(index > 0, "refresh parser marker missing")
    tail = source[index:]
    tail = re.sub(
        r"\nfn compact_segments\(.*?\n\}\n\n(?=fn claim_string)",
        "\n",
        tail,
        count=1,
        flags=re.S,
    )
    write(path, provider_backed_prefix() + tail)


def update_authority(root: Path) -> None:
    path = root / "docs/development/CRYPTO_PATH_AUTHORITY.json"
    if not path.exists():
        return
    value = json.loads(read(path))
    rows = {
        row.get("id"): row
        for row in value.get("path_classifications", [])
        if isinstance(row, dict)
    }
    active = rows.get("CRYPTO-PATH-ACTIVE-ACCESS-AUTH")
    if active is not None:
        active.update(
            {
                "classification": "active-server-authentication-opaque-provider-source-candidate",
                "implementation": "trnm-token-jwt-provider-adapter plus Hs256Provider; raw software key construction is compatibility/development-only",
                "primitive_provider": "CONTRACT-HS256-OPAQUE-PROVIDER",
                "private_primitive_reachable": False,
                "required_acceptance": "Production KMS/HSM profile, rotation, revoke, deadlines, audit and independent cryptographic review remain required.",
                "claim_credit": False,
            }
        )
    summary = value.setdefault("summary", {})
    summary["all_active_crypto_paths_reviewed_provider_backed"] = True
    summary["production_key_provider_accepted"] = False
    summary["gap_closed"] = False
    summary["production_ready"] = False
    write(path, json.dumps(value, indent=2) + "\n")


def install_checker(root: Path) -> None:
    checker = textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        from __future__ import annotations
        import json
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[1]
        AUTH = ROOT / "crates/trnm-persistence-pg/src/auth.rs"
        PROVIDER = ROOT / "crates/trnm-token-crypto-provider/src/software.rs"
        MANIFEST = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
        AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
        def require(value: bool, message: str) -> None:
            if not value:
                raise SystemExit(message)
        auth = AUTH.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        provider = PROVIDER.read_text(encoding="utf-8")
        manifest = MANIFEST.read_text(encoding="utf-8")
        for marker in ("trnm_token_jwt_provider_adapter", "authenticate(", "Hs256Provider", "from_provider("):
            require(marker in auth, f"active auth provider composition missing {marker}")
        for forbidden in ("PKey::hmac", "Signer::new", "hmac_sha256", "constant_time_eq"):
            require(forbidden not in auth, f"active auth directly owns primitive {forbidden}")
        for dependency in ("trnm-token-crypto-provider", "trnm-token-jwt-provider-adapter"):
            require(dependency in manifest, f"persistence dependency missing {dependency}")
        for marker in ("impl Hs256Provider for SoftwareHs256Provider", "HmacSha256", "ct_eq"):
            require(marker in provider, f"software provider missing {marker}")
        authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
        rows = {row["id"]: row for row in authority["path_classifications"]}
        require(rows["CRYPTO-PATH-ACTIVE-ACCESS-AUTH"]["private_primitive_reachable"] is False, "active auth authority is stale")
        require(authority["summary"]["production_key_provider_accepted"] is False, "software source must not claim KMS/HSM acceptance")
        require(authority["summary"]["gap_closed"] is False, "source cannot self-close security review")
        print("active authentication provider composition validation passed")
        '''
    )
    path = root / "scripts/check-auth-provider-composition.py"
    write(path, checker)
    path.chmod(0o755)
    test = textwrap.dedent(
        '''\
        from __future__ import annotations
        import subprocess
        import unittest
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[2]
        class AuthProviderCompositionTest(unittest.TestCase):
            def test_repository_checker(self) -> None:
                subprocess.run(["python3", "scripts/check-auth-provider-composition.py"], cwd=ROOT, check=True)
        if __name__ == "__main__":
            unittest.main()
        '''
    )
    write(root / "tests/control_plane/test_auth_provider_composition.py", test)


def update_merge_gate(root: Path) -> None:
    path = root / ".github/workflows/trillionnium-game-merge-gate.yml"
    text = read(path)
    anchor = (
        "      - name: Validate Rust server source contract\n"
        "        run: python3 scripts/check-trnm-server.py\n"
    )
    step = (
        "      - name: Validate active authentication provider composition\n"
        "        run: python3 scripts/check-auth-provider-composition.py\n"
    )
    if step not in text:
        require(anchor in text, "merge-gate auth anchor missing")
        text = text.replace(anchor, anchor + step, 1)
    write(path, text)


def update_docs(root: Path) -> None:
    path = root / "docs/SECURITY_AND_PRIVACY.md"
    if path.exists():
        text = read(path)
        addition = textwrap.dedent(
            '''

            ### Active access-token provider boundary

            The active database-backed access-token verifier authenticates the exact encoded JWT signing input through `trnm-token-jwt-provider-adapter` and the opaque `Hs256Provider` contract. The compatibility constructor may instantiate `SoftwareHs256Provider` for development and migration tests, but production approval requires a separately accepted KMS/HSM or approved secret-manager implementation. Persistence, protocol and session handlers do not implement HMAC or compare authenticators directly.
            '''
        )
        if "### Active access-token provider boundary" not in text:
            text += addition
        write(path, text)


def run(root: Path) -> None:
    update_provider_crate(root)
    update_persistence_manifest(root)
    rewrite_auth(root)
    update_authority(root)
    install_checker(root)
    update_merge_gate(root)
    update_docs(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    run(args.root.resolve())
