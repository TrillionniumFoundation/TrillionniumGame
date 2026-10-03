use super::*;
use trnm_session_core::NakamaLegacyBlacklistPolicy;
use trnm_token_jwt_adapter::NakamaLegacyDecodeLimits;

const ACCESS_KEY: &[u8] = b"defaultencryptionkey";
const REFRESH_KEY: &[u8] = b"defaultrefreshencryptionkey";
const UID: &str = "01234567-89ab-cdef-0123-456789abcdef";

fn cache_policy() -> NakamaLegacyBlacklistPolicy {
    NakamaLegacyBlacklistPolicy {
        max_users: 16,
        max_tokens_per_user: 32,
        max_total_tokens: 64,
        max_token_id_bytes: 1024,
        max_total_token_id_bytes: 8192,
        max_mutation_items: 32,
        max_prepared_token_id_bytes: 4096,
    }
}
fn policy(cache: NakamaLegacyBlacklistPolicy) -> LegacyAuthPolicy {
    let decode = NakamaLegacyDecodeLimits::new(4096, 32, 256, 8192).unwrap();
    LegacyAuthPolicy::new(LegacyAuthPolicyConfig {
        session_ttl_seconds: 60,
        refresh_ttl_seconds: 3600,
        single_session: false,
        issuer_limits: NakamaLegacyIssueLimits::new(4096, 8192).unwrap(),
        verifier_limits: NakamaLegacyVerifyLimits::new(8192, decode, decode).unwrap(),
        blacklist_limits: NakamaLegacyBlacklistLimits::new(cache).unwrap(),
    })
    .unwrap()
}
fn service_with(cache: NakamaLegacyBlacklistPolicy) -> LegacyAuthService {
    LegacyAuthService::from_config(
        LegacyAuthConfig::new(
            ACCESS_KEY,
            REFRESH_KEY,
            NakamaLegacyKeyLimits::new(4096).unwrap(),
            policy(cache),
        )
        .unwrap(),
    )
    .unwrap()
}
fn service() -> LegacyAuthService {
    service_with(cache_policy())
}
fn user() -> UserId {
    parse_uuid(UID).unwrap()
}
fn token(
    s: &LegacyAuthService,
    kind: NakamaLegacyKeyKind,
    uid: &str,
    tid: &str,
    exp: i64,
    iat: i64,
    vars: Option<&BTreeMap<String, String>>,
) -> String {
    NakamaLegacyIssuer::new(&s.state().provider, s.state().policy.0.issuer_limits)
        .issue(
            kind,
            &NakamaLegacyClaims {
                token_id: tid,
                user_id: uid,
                username: "old-name",
                variables: vars,
                expires_at: exp,
                issued_at: iat,
            },
        )
        .unwrap()
        .into_string()
}
fn read_claims(
    s: &LegacyAuthService,
    t: &str,
    kind: NakamaLegacyKeyKind,
    now: i64,
) -> trnm_token_jwt_adapter::NakamaLegacyRawClaims {
    let verified = s.parse_at(t.as_bytes(), kind, now).unwrap().0;
    let c = verified.claims();
    trnm_token_jwt_adapter::NakamaLegacyRawClaims {
        token_id: c.token_id.clone(),
        user_id: c.user_id.clone(),
        username: c.username.clone(),
        variables: c.variables.clone(),
        expires_at: c.expires_at,
        issued_at: c.issued_at,
    }
}
struct Users {
    calls: usize,
    expected: UserId,
    result: Option<LegacyStoredUser>,
    error: Option<LegacyRepositoryError>,
}
impl LegacyUserRepository for Users {
    fn read_legacy_user(
        &mut self,
        id: UserId,
    ) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError> {
        self.calls += 1;
        assert_eq!(id, self.expected);
        if let Some(error) = self.error {
            return Err(error);
        }
        Ok(self.result.take())
    }
}
fn users(username: &str, disabled: Option<i64>) -> Users {
    Users {
        calls: 0,
        expected: user(),
        result: Some(LegacyStoredUser {
            user_id: user(),
            username: username.into(),
            disable_time_unix_seconds: disabled,
        }),
        error: None,
    }
}

#[test]
fn owned_configuration_accepts_actual_short_defaults_without_key_fallback() {
    assert_eq!((ACCESS_KEY.len(), REFRESH_KEY.len()), (20, 27));
    let limits = NakamaLegacyKeyLimits::new(4096).unwrap();
    for (access, refresh) in [
        (b"".as_slice(), REFRESH_KEY),
        (ACCESS_KEY, b"".as_slice()),
        (ACCESS_KEY, ACCESS_KEY),
    ] {
        assert!(matches!(
            LegacyAuthConfig::new(access, refresh, limits, policy(cache_policy())),
            Err(LegacyAuthError::InvalidConfiguration)
        ));
    }
    let config =
        LegacyAuthConfig::new(ACCESS_KEY, REFRESH_KEY, limits, policy(cache_policy())).unwrap();
    assert_eq!(config.policy.0.session_ttl_seconds, 60);
    assert_eq!(config.policy.0.refresh_ttl_seconds, 3600);
    assert!(!format!("{config:?}").contains("defaultencryptionkey"));
    let s = LegacyAuthService::from_config(config).unwrap();
    let access = token(&s, NakamaLegacyKeyKind::Access, UID, "tid", 160, 100, None);
    assert!(s.verify_access_at(access.as_bytes(), 100).is_ok());
    let refresh = token(
        &s,
        NakamaLegacyKeyKind::Refresh,
        UID,
        "tid",
        3700,
        100,
        None,
    );
    assert!(matches!(
        s.verify_access_at(refresh.as_bytes(), 100),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    let other = LegacyAuthService::from_config(
        LegacyAuthConfig::new(
            b"different-actual-access",
            REFRESH_KEY,
            limits,
            policy(cache_policy()),
        )
        .unwrap(),
    )
    .unwrap();
    assert!(matches!(
        other.verify_access_at(access.as_bytes(), 100),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    for ttl in [0, -1, i64::MAX] {
        let mut p = policy(cache_policy()).0;
        p.session_ttl_seconds = ttl;
        assert!(matches!(
            LegacyAuthPolicy::new(p),
            Err(LegacyAuthError::InvalidConfiguration)
        ));
    }
}

#[test]
fn sealed_access_context_requires_mac_expiry_uuid_and_shared_cache_in_order() {
    let s = service();
    let formats = [
        UID.to_owned(),
        UID.to_uppercase(),
        UID.replace('-', ""),
        format!("{{{UID}}}"),
        format!("urn:uuid:{UID}"),
        format!("urn:uuid:{}", UID.replace('-', "")),
        format!("{{{}}}", UID.replace('-', "")),
    ];
    for uid in formats {
        let t = token(&s, NakamaLegacyKeyKind::Access, &uid, "tid", 160, 100, None);
        assert_eq!(
            s.verify_access_at(t.as_bytes(), 100).unwrap().user(),
            user()
        );
    }
    let nil = token(
        &s,
        NakamaLegacyKeyKind::Access,
        "00000000-0000-0000-0000-000000000000",
        "raw\0tid",
        160,
        100,
        None,
    );
    assert!(s
        .verify_access_at(nil.as_bytes(), 100)
        .unwrap()
        .user()
        .is_zero());
    let invalid = token(
        &s,
        NakamaLegacyKeyKind::Access,
        "not-a-uuid",
        "tid",
        160,
        100,
        None,
    );
    assert!(matches!(
        s.verify_access_at(invalid.as_bytes(), 100),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    let valid = token(&s, NakamaLegacyKeyKind::Access, UID, "tid", 160, 100, None);
    assert!(matches!(
        s.verify_access_at(valid.as_bytes(), 160),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    let mut altered = valid.into_bytes();
    altered[0] ^= 1;
    assert!(matches!(
        s.verify_access_at(&altered, 100),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    s.cache()
        .unwrap()
        .remove_all(cache_user(user()), 101)
        .unwrap();
    let t = token(&s, NakamaLegacyKeyKind::Access, UID, "tid", 160, 100, None);
    assert!(matches!(
        s.clone().verify_access_at(t.as_bytes(), 101),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    for bearer in ["bearer invalid", "Bearer\tinvalid", " Bearer invalid"] {
        assert!(matches!(
            s.verify_access_bearer(bearer),
            Err(LegacyAuthError::AuthTokenInvalid)
        ));
    }
}

#[test]
fn refresh_rejects_untrusted_or_revoked_token_before_durable_user_read() {
    let s = service();
    let mut repo = users("stored", None);
    assert!(matches!(
        s.refresh_at(&mut repo, b"", None, 100, || Ok(100)),
        Err(LegacyAuthError::RefreshTokenRequired)
    ));
    let access = token(&s, NakamaLegacyKeyKind::Access, UID, "tid", 160, 100, None);
    assert!(matches!(
        s.refresh_at(&mut repo, access.as_bytes(), None, 100, || Ok(100)),
        Err(LegacyAuthError::RefreshTokenInvalidOrExpired)
    ));
    let refresh = token(
        &s,
        NakamaLegacyKeyKind::Refresh,
        UID,
        "tid",
        3700,
        100,
        None,
    );
    s.cache()
        .unwrap()
        .remove_all(cache_user(user()), 101)
        .unwrap();
    assert!(matches!(
        s.refresh_at(&mut repo, refresh.as_bytes(), None, 101, || Ok(101)),
        Err(LegacyAuthError::RefreshTokenInvalidOrExpired)
    ));
    assert_eq!(repo.calls, 0);
}

#[test]
fn refresh_uses_stored_username_preserves_tid_iat_and_distinguishes_nil_empty_vars() {
    let s = service();
    let vars = BTreeMap::from([("key".into(), "original".into())]);
    let t = token(
        &s,
        NakamaLegacyKeyKind::Refresh,
        &UID.to_uppercase(),
        "same-raw\0tid",
        4000,
        -7,
        Some(&vars),
    );
    let renamed = "x".repeat(512); // Historical VARCHAR character-domain values are not input revalidated.
    let mut repo = users(&renamed, Some(0));
    let mut samples = [100, 101].into_iter();
    let pair = s
        .refresh_at(&mut repo, t.as_bytes(), None, 100, || {
            Ok(samples.next().unwrap())
        })
        .unwrap();
    assert!(!pair.created);
    assert_eq!(
        (pair.access_expires_at, pair.refresh_expires_at),
        (160, 3701)
    );
    let claims = read_claims(
        &s,
        pair.tokens().access.as_str(),
        NakamaLegacyKeyKind::Access,
        100,
    );
    assert_eq!(claims.user_id, UID);
    assert_eq!(claims.username, renamed);
    assert_eq!(claims.token_id, "same-raw\0tid");
    assert_eq!(claims.issued_at, -7);
    assert_eq!(claims.variables, Some(vars));
    let empty = BTreeMap::new();
    let mut repo = users("new", None);
    let pair = s
        .refresh_at(&mut repo, t.as_bytes(), Some(&empty), 100, || Ok(100))
        .unwrap();
    // Explicit empty request clears prior variables; source omitempty then omits vrs.
    assert_eq!(
        read_claims(
            &s,
            pair.tokens().refresh.as_str(),
            NakamaLegacyKeyKind::Refresh,
            100
        )
        .variables,
        None
    );
    let replacement = BTreeMap::from([("other".into(), "replacement".into())]);
    let mut repo = users("new", None);
    let pair = s
        .refresh_at(&mut repo, t.as_bytes(), Some(&replacement), 100, || Ok(100))
        .unwrap();
    assert_eq!(
        read_claims(
            &s,
            pair.tokens().access.as_str(),
            NakamaLegacyKeyKind::Access,
            100
        )
        .variables,
        Some(replacement)
    );
}

#[test]
fn refresh_missing_banned_wrong_row_and_native_error_never_issue_tokens() {
    let s = service();
    let t = token(
        &s,
        NakamaLegacyKeyKind::Refresh,
        UID,
        "tid",
        3700,
        100,
        None,
    );
    let mut absent = users("stored", None);
    absent.result = None;
    assert!(matches!(
        s.refresh_at(&mut absent, t.as_bytes(), None, 100, || panic!(
            "must not issue"
        )),
        Err(LegacyAuthError::UserAccountNotFound)
    ));
    for disabled in [-1, 1] {
        let mut repo = users("stored", Some(disabled));
        assert!(matches!(
            s.refresh_at(&mut repo, t.as_bytes(), None, 100, || panic!(
                "must not issue"
            )),
            Err(LegacyAuthError::UserAccountBanned)
        ));
    }
    let mut wrong = users("stored", None);
    wrong.result.as_mut().unwrap().user_id = UserId::new([2; 16]);
    assert!(matches!(
        s.refresh_at(&mut wrong, t.as_bytes(), None, 100, || panic!(
            "must not issue"
        )),
        Err(LegacyAuthError::Repository(LegacyRepositoryError::DataLoss))
    ));
    let mut unavailable = users("stored", None);
    unavailable.error = Some(LegacyRepositoryError::Unavailable);
    assert!(matches!(
        s.refresh_at(&mut unavailable, t.as_bytes(), None, 100, || panic!(
            "must not issue"
        )),
        Err(LegacyAuthError::Repository(
            LegacyRepositoryError::Unavailable
        ))
    ));
}

#[test]
fn whole_pair_issue_same_second_is_deterministic_and_time_overflow_returns_no_pair() {
    let s = service();
    let t = token(
        &s,
        NakamaLegacyKeyKind::Refresh,
        UID,
        "tid",
        3700,
        100,
        None,
    );
    let a = s
        .refresh_at(&mut users("stored", None), t.as_bytes(), None, 100, || {
            Ok(100)
        })
        .unwrap();
    let b = s
        .refresh_at(&mut users("stored", None), t.as_bytes(), None, 100, || {
            Ok(100)
        })
        .unwrap();
    let c = s
        .refresh_at(&mut users("stored", None), t.as_bytes(), None, 100, || {
            Ok(101)
        })
        .unwrap();
    assert_eq!(a.tokens().access.as_str(), b.tokens().access.as_str());
    assert_eq!(a.tokens().refresh.as_str(), b.tokens().refresh.as_str());
    assert_ne!(a.tokens().access.as_str(), c.tokens().access.as_str());
    let before = s.blacklist_stats().unwrap();
    assert!(matches!(
        s.refresh_at(&mut users("stored", None), t.as_bytes(), None, 100, || Ok(
            i64::MAX
        )),
        Err(LegacyAuthError::ClockRange)
    ));
    assert_eq!(s.blacklist_stats().unwrap(), before);
}

#[test]
fn logout_validates_access_then_refresh_owner_and_purpose_before_mutation() {
    let s = service();
    let auth = token(&s, NakamaLegacyKeyKind::Access, UID, "auth", 160, 100, None);
    let p = s.verify_access_at(auth.as_bytes(), 100).unwrap();
    let access = token(&s, NakamaLegacyKeyKind::Access, UID, "a", 160, 100, None);
    let refresh = token(&s, NakamaLegacyKeyKind::Refresh, UID, "r", 3700, 100, None);
    let other = token(
        &s,
        NakamaLegacyKeyKind::Refresh,
        "00000000-0000-0000-0000-000000000000",
        "r",
        3700,
        100,
        None,
    );
    let before = s.blacklist_stats().unwrap();
    assert!(matches!(
        s.logout_at(&p, b"invalid", b"also-invalid", || Ok(100)),
        Err(LegacyAuthError::SessionLogoutTokenInvalid)
    ));
    assert!(matches!(
        s.logout_at(&p, access.as_bytes(), other.as_bytes(), || Ok(100)),
        Err(LegacyAuthError::RefreshLogoutTokenInvalid)
    ));
    assert!(matches!(
        s.logout_at(&p, refresh.as_bytes(), access.as_bytes(), || Ok(100)),
        Err(LegacyAuthError::SessionLogoutTokenInvalid)
    ));
    assert_eq!(s.blacklist_stats().unwrap(), before);
    let other_service = service();
    assert!(matches!(
        other_service.logout_at(&p, b"", b"", || Ok(100)),
        Err(LegacyAuthError::PrincipalServiceMismatch)
    ));
    s.logout_at(&p, access.as_bytes(), refresh.as_bytes(), || Ok(100))
        .unwrap();
    assert!(matches!(
        s.verify_access_at(access.as_bytes(), 100),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    assert_eq!(s.blacklist_stats().unwrap().tokens, 2);
    // Body tokens that are already revoked still parse; the authenticated header is separate.
    s.logout_at(&p, access.as_bytes(), refresh.as_bytes(), || Ok(100))
        .unwrap();
    assert_eq!(s.blacklist_stats().unwrap().tokens, 2);
    assert!(s.verify_access_at(auth.as_bytes(), 100).is_ok());
}

#[test]
fn nonempty_body_with_empty_tids_removes_all_with_source_strict_same_second_rule() {
    let s = service();
    let auth = token(&s, NakamaLegacyKeyKind::Access, UID, "auth", 160, 100, None);
    let p = s.verify_access_at(auth.as_bytes(), 100).unwrap();
    let a = token(&s, NakamaLegacyKeyKind::Access, UID, "", 160, 100, None);
    let r = token(&s, NakamaLegacyKeyKind::Refresh, UID, "", 3700, 100, None);
    s.logout_at(&p, a.as_bytes(), r.as_bytes(), || Ok(100))
        .unwrap();
    assert!(s.verify_access_at(auth.as_bytes(), 100).is_ok()); // exp-TTL == invalidation survives.
    s.logout_at(&p, b"", b"", || Ok(101)).unwrap();
    assert!(matches!(
        s.verify_access_at(auth.as_bytes(), 101),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
}

#[test]
fn logout_quota_is_atomic_and_does_not_evict_existing_revocations() {
    let mut limits = cache_policy();
    limits.max_tokens_per_user = 1;
    let s = service_with(limits);
    let auth = token(&s, NakamaLegacyKeyKind::Access, UID, "auth", 160, 100, None);
    let p = s.verify_access_at(auth.as_bytes(), 100).unwrap();
    let a = token(&s, NakamaLegacyKeyKind::Access, UID, "a", 160, 100, None);
    let r = token(&s, NakamaLegacyKeyKind::Refresh, UID, "r", 3700, 100, None);
    let before = s.blacklist_stats().unwrap();
    assert!(matches!(
        s.logout_at(&p, a.as_bytes(), r.as_bytes(), || Ok(100)),
        Err(LegacyAuthError::Cache(
            NakamaLegacyBlacklistError::TokensPerUserLimit
        ))
    ));
    assert_eq!(s.blacklist_stats().unwrap(), before);
    assert!(s.verify_access_at(a.as_bytes(), 100).is_ok());
    s.logout_at(&p, a.as_bytes(), b"", || Ok(100)).unwrap();
    let retained = s.blacklist_stats().unwrap();
    let other = token(
        &s,
        NakamaLegacyKeyKind::Access,
        UID,
        "other",
        160,
        100,
        None,
    );
    assert!(s.logout_at(&p, other.as_bytes(), b"", || Ok(100)).is_err());
    assert_eq!(s.blacklist_stats().unwrap(), retained);
    assert!(matches!(
        s.verify_access_at(a.as_bytes(), 100),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
}

#[test]
fn ban_whole_preflight_and_poisoned_cache_fail_closed() {
    let mut limits = cache_policy();
    limits.max_users = 1;
    limits.max_mutation_items = 1;
    let s = service_with(limits);
    let a = UserId::new([1; 16]);
    let b = UserId::new([2; 16]);
    let before = s.blacklist_stats().unwrap();
    assert!(matches!(
        s.ban(&[a, b]),
        Err(LegacyAuthError::Cache(
            NakamaLegacyBlacklistError::MutationItemsLimit
        ))
    ));
    assert_eq!(s.blacklist_stats().unwrap(), before);
    s.ban(&[a]).unwrap();
    let retained = s.blacklist_stats().unwrap();
    assert!(s.ban(&[b]).is_err());
    assert_eq!(s.blacklist_stats().unwrap(), retained);
    s.unban(&[a]);
    assert_eq!(s.blacklist_stats().unwrap(), retained);
    let poisoned = service();
    let state = Arc::clone(poisoned.state());
    let _ = thread::spawn(move || {
        let _guard = state.cache.lock().unwrap();
        panic!("synthetic mutex poison");
    })
    .join();
    let t = token(
        &poisoned,
        NakamaLegacyKeyKind::Access,
        UID,
        "tid",
        160,
        100,
        None,
    );
    assert!(matches!(
        poisoned.verify_access_at(t.as_bytes(), 100),
        Err(LegacyAuthError::CachePoisoned)
    ));
}

struct Devices {
    calls: usize,
    error: Option<LegacyRepositoryError>,
    created: bool,
    generated: bool,
}
impl LegacyDeviceRepository for Devices {
    fn authenticate_legacy_device(
        &mut self,
        input: LegacyDeviceRepositoryInput<'_>,
    ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
        self.calls += 1;
        assert_eq!(input.device_id, "0123456789");
        if self.generated {
            assert_eq!(input.requested_username.len(), 10);
            assert!(input
                .requested_username
                .bytes()
                .all(|b| b.is_ascii_alphabetic()));
        } else {
            assert_eq!(input.requested_username, "new-input");
        }
        assert!(input.create);
        if let Some(e) = self.error {
            return Err(e);
        }
        Ok(LegacyDeviceAccount {
            user_id: user(),
            stored_username: "actual-renamed-stored".into(),
            created: self.created,
            committed_cleanup_failure: None,
        })
    }
}
#[test]
fn device_requires_real_effect_boundary_and_issues_actual_stored_name_without_family() {
    let s = service();
    let mut repo = Devices {
        calls: 0,
        error: None,
        created: false,
        generated: false,
    };
    assert!(matches!(
        s.authenticate_device(
            &mut repo,
            LegacyDeviceAuthInput {
                account_id: None,
                username: "bad[]",
                create: None,
                variables: None
            }
        ),
        Err(LegacyDeviceAuthError::Unconfirmed(
            LegacyAuthError::DeviceInput(_)
        ))
    ));
    assert_eq!(repo.calls, 0);
    for error in [
        LegacyRepositoryError::Unimplemented,
        LegacyRepositoryError::UserNotFound,
        LegacyRepositoryError::UserBanned,
        LegacyRepositoryError::UsernameAlreadyInUse,
    ] {
        repo.error = Some(error);
        assert!(s
            .authenticate_device(
                &mut repo,
                LegacyDeviceAuthInput {
                    account_id: Some("0123456789"),
                    username: "new-input",
                    create: None,
                    variables: None
                }
            )
            .is_err());
    }
    repo.error = None;
    repo.created = true;
    let pair = s
        .authenticate_device(
            &mut repo,
            LegacyDeviceAuthInput {
                account_id: Some("0123456789"),
                username: "new-input",
                create: None,
                variables: None,
            },
        )
        .unwrap();
    assert!(pair.created);
    let p = s
        .verify_access(pair.tokens().access.as_str().as_bytes())
        .unwrap();
    assert_eq!(p.username(), "actual-renamed-stored");
    assert_eq!(p.user(), user());
    assert_eq!(parse_uuid(p.token_id()).unwrap().as_bytes()[6] >> 4, 4);
    assert!(!p.token_id().contains("family"));
    repo.generated = true;
    repo.created = false;
    let generated = s
        .authenticate_device(
            &mut repo,
            LegacyDeviceAuthInput {
                account_id: Some("0123456789"),
                username: "",
                create: None,
                variables: None,
            },
        )
        .unwrap();
    assert!(!generated.created);
}

#[test]
fn clock_floor_and_credential_debug_keep_trust_boundaries() {
    assert_eq!(system_time_seconds(UNIX_EPOCH).unwrap(), 0);
    assert_eq!(
        system_time_seconds(UNIX_EPOCH - Duration::from_nanos(1)).unwrap(),
        -1
    );
    assert_eq!(
        system_time_seconds(UNIX_EPOCH - Duration::from_millis(1500)).unwrap(),
        -2
    );
    let s = service();
    let t = token(
        &s,
        NakamaLegacyKeyKind::Access,
        UID,
        "SECRET-TID",
        160,
        100,
        None,
    );
    let p = s.verify_access_at(t.as_bytes(), 100).unwrap();
    let debug = format!(
        "{s:?} {p:?} {:?} {:?}",
        LegacyStoredUser {
            user_id: user(),
            username: "SECRET-NAME".into(),
            disable_time_unix_seconds: None
        },
        LegacyAuthError::AuthTokenInvalid
    );
    for secret in ["SECRET-TID", "SECRET-NAME", UID, "defaultencryptionkey", &t] {
        assert!(!debug.contains(secret));
    }
    assert_eq!(
        LegacyAuthError::AuthTokenInvalid.to_string(),
        "Auth token invalid"
    );
}

#[test]
fn single_session_device_uses_shared_source_invalidation_and_fresh_pair() {
    let mut p = policy(cache_policy()).0;
    p.single_session = true;
    let s = LegacyAuthService::from_config(
        LegacyAuthConfig::new(
            ACCESS_KEY,
            REFRESH_KEY,
            NakamaLegacyKeyLimits::new(4096).unwrap(),
            LegacyAuthPolicy::new(p).unwrap(),
        )
        .unwrap(),
    )
    .unwrap();
    let now = utc_seconds().unwrap();
    let old = token(
        &s,
        NakamaLegacyKeyKind::Access,
        UID,
        "old-tid",
        now + 59,
        now - 1,
        None,
    );
    assert!(s.verify_access_at(old.as_bytes(), now).is_ok());
    let mut repo = Devices {
        calls: 0,
        error: None,
        created: false,
        generated: false,
    };
    let pair = s
        .authenticate_device(
            &mut repo,
            LegacyDeviceAuthInput {
                account_id: Some("0123456789"),
                username: "new-input",
                create: None,
                variables: None,
            },
        )
        .unwrap();
    assert!(matches!(
        s.verify_access_at(old.as_bytes(), now),
        Err(LegacyAuthError::AuthTokenInvalid)
    ));
    let fresh = s
        .verify_access(pair.tokens().access.as_str().as_bytes())
        .unwrap();
    assert_eq!(fresh.user(), user());
    assert_ne!(fresh.token_id(), "old-tid");
    assert_eq!(s.blacklist_stats().unwrap().tokens, 0); // Source Add remains a no-op.
}

#[test]
fn remove_retains_exp_plus_one_and_pair_failure_never_returns_partial_credentials() {
    let s = service();
    let t = token(&s, NakamaLegacyKeyKind::Access, UID, "tid", 160, 100, None);
    let p = s.verify_access_at(t.as_bytes(), 100).unwrap();
    s.logout_at(&p, t.as_bytes(), b"", || Ok(100)).unwrap();
    assert_eq!(s.cache().unwrap().sweep(160).session_tokens_removed, 0);
    assert_eq!(s.cache().unwrap().sweep(161).session_tokens_removed, 1);
    let identity = NakamaLegacyClaims {
        token_id: "tid",
        user_id: UID,
        username: "stored",
        variables: None,
        expires_at: 0,
        issued_at: 100,
    };
    let mut count = 0;
    assert!(matches!(
        s.issue_session(user(), &identity, false, &mut || {
            count += 1;
            if count == 1 {
                Ok(100)
            } else {
                Err(LegacyAuthError::ClockUnavailable)
            }
        }),
        Err(LegacyAuthError::ClockUnavailable)
    ));
    assert_eq!(count, 2);
    assert_eq!(s.blacklist_stats().unwrap().tokens, 0);
}

#[test]
fn runtime_clones_share_one_worker_and_last_service_drop_joins_without_state_cycle() {
    let s = service();
    let weak = Arc::downgrade(s.state());
    let clone = s.clone();
    assert!(Arc::ptr_eq(s.state(), clone.state()));
    drop(s);
    assert!(weak.upgrade().is_some());
    drop(clone);
    assert!(weak.upgrade().is_none());
}

#[test]
fn logout_remove_all_samples_cutoff_before_lock_and_preserves_same_second() {
    let s = service();
    let t = token(&s, NakamaLegacyKeyKind::Access, UID, "tid", 160, 100, None);
    let principal = s.verify_access_at(t.as_bytes(), 100).unwrap();
    let state = Arc::clone(s.state());
    s.logout_at(&principal, b"", b"", || {
        // Previously WouldBlock: source samples before its cache write lock.
        assert!(state.cache.try_lock().is_ok());
        Ok(100)
    })
    .unwrap();
    assert!(s.verify_access_at(t.as_bytes(), 100).is_ok());
}

#[test]
fn device_committed_cleanup_failure_is_retained_through_whole_token_pair() {
    struct Committed;
    impl LegacyDeviceRepository for Committed {
        fn authenticate_legacy_device(
            &mut self,
            _: LegacyDeviceRepositoryInput<'_>,
        ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
            Ok(LegacyDeviceAccount {
                user_id: user(),
                stored_username: "committed".into(),
                created: true,
                committed_cleanup_failure: Some(LegacyCommittedCleanupFailure {
                    sqlstate: Some(*b"08006"),
                    username_collision: false,
                    transaction_closed: false,
                    reason: "committed-cleanup",
                }),
            })
        }
    }
    let s = service();
    let pair = s
        .authenticate_device(
            &mut Committed,
            LegacyDeviceAuthInput {
                account_id: Some("0123456789"),
                username: "name",
                create: Some(true),
                variables: None,
            },
        )
        .unwrap();
    assert!(pair.created);
    assert_eq!(
        pair.committed_cleanup_failure.unwrap().reason,
        "committed-cleanup"
    );
    assert!(s
        .verify_access(pair.tokens().access.as_str().as_bytes())
        .is_ok());
}

fn committed_account(created: bool) -> LegacyDeviceAccount {
    LegacyDeviceAccount {
        user_id: user(),
        stored_username: "PRIVATE-COMMITTED-NAME".into(),
        created,
        committed_cleanup_failure: created.then_some(LegacyCommittedCleanupFailure {
            sqlstate: Some(*b"08006"),
            username_collision: false,
            transaction_closed: false,
            reason: "PRIVATE-CLEANUP-REASON",
        }),
    }
}
fn assert_committed_failure(
    error: LegacyDeviceAuthError,
    expected: impl FnOnce(&LegacyAuthError) -> bool,
    code: trnm_contracts::StableCode,
) {
    assert!(error.creation_commit_confirmed());
    assert!(!error.retry_permitted());
    assert!(!error.compensation_permitted());
    assert_eq!(error.code(), code);
    let LegacyDeviceAuthError::CommittedCreation(failure) = error else {
        panic!("expected committed creation")
    };
    assert!(expected(failure.cause()), "{:?}", failure.cause());
    assert_eq!(
        failure.committed_cleanup_failure(),
        committed_account(true).committed_cleanup_failure
    );
    assert!(failure.creation_commit_confirmed());
    assert!(!failure.retry_permitted());
    assert!(!failure.compensation_permitted());
    let rendered = format!("{failure:?} {failure}");
    for secret in [
        "08006",
        "PRIVATE-CLEANUP-REASON",
        "PRIVATE-COMMITTED-NAME",
        UID,
        "PRIVATE-TOKEN-ID",
    ] {
        assert!(!rendered.contains(secret));
    }
    assert!(core::mem::size_of::<LegacyDevicePostCommitFailure>() <= 256);
}

#[test]
fn ban_samples_clock_before_bounds_preparation_and_preserves_atomic_quota() {
    let mut limits = cache_policy();
    limits.max_users = 1;
    limits.max_mutation_items = 1;
    let s = service_with(limits);
    let before = s.blacklist_stats().unwrap();
    let mut clock_calls = 0;
    let invalid = [user(), UserId::new([1; 16])];
    assert!(matches!(
        s.ban_at(&invalid, || {
            clock_calls += 1;
            Err(LegacyAuthError::ClockUnavailable)
        }),
        Err(LegacyAuthError::ClockUnavailable)
    ));
    assert_eq!(clock_calls, 1);
    assert_eq!(s.blacklist_stats().unwrap(), before);
    // Clock still runs before the rejected whole-input bound; no key preparation
    // or cache mutation is allowed to obscure its failure.
    assert!(matches!(
        s.ban_at(&invalid, || {
            assert!(s.state().cache.try_lock().is_ok());
            Ok(100)
        }),
        Err(LegacyAuthError::Cache(
            NakamaLegacyBlacklistError::MutationItemsLimit
        ))
    ));
    s.ban_at(&[user()], || Ok(100)).unwrap();
    let retained = s.blacklist_stats().unwrap();
    assert!(matches!(
        s.ban_at(&[UserId::new([1; 16])], || Ok(101)),
        Err(LegacyAuthError::Cache(
            NakamaLegacyBlacklistError::UserLimit
        ))
    ));
    assert_eq!(s.blacklist_stats().unwrap(), retained);
    assert!(s
        .cache()
        .unwrap()
        .is_valid_session(cache_user(user()), 160, cache_token("issued-at-100"))
        .unwrap());
    s.ban_at(&[user()], || Ok(101)).unwrap();
    assert!(!s
        .cache()
        .unwrap()
        .is_valid_session(cache_user(user()), 160, cache_token("issued-at-100"))
        .unwrap());
}

#[test]
fn committed_device_random_and_each_clock_stage_keep_cause_and_cleanup() {
    use trnm_contracts::StableCode;
    let s = service();
    assert_committed_failure(
        s.finish_device_account(
            committed_account(true),
            None,
            || panic!("random precedes iat"),
            || Err(LegacyAuthError::RandomUnavailable),
        )
        .unwrap_err(),
        |e| matches!(e, LegacyAuthError::RandomUnavailable),
        StableCode::Internal,
    );
    for failure_stage in 0..3 {
        let mut samples = 0;
        let error = s
            .finish_device_account(
                committed_account(true),
                None,
                || {
                    let current = samples;
                    samples += 1;
                    if current == failure_stage {
                        Err(LegacyAuthError::ClockUnavailable)
                    } else {
                        Ok(100)
                    }
                },
                || Ok("PRIVATE-TOKEN-ID".into()),
            )
            .unwrap_err();
        assert_eq!(samples, failure_stage + 1);
        assert_committed_failure(
            error,
            |e| matches!(e, LegacyAuthError::ClockUnavailable),
            StableCode::Internal,
        );
    }
    let mut samples = [100, i64::MAX].into_iter();
    assert_committed_failure(
        s.finish_device_account(
            committed_account(true),
            None,
            || Ok(samples.next().unwrap()),
            || Ok("PRIVATE-TOKEN-ID".into()),
        )
        .unwrap_err(),
        |e| matches!(e, LegacyAuthError::ClockRange),
        StableCode::Internal,
    );
    let mut samples = [100, 100, i64::MAX].into_iter();
    assert_committed_failure(
        s.finish_device_account(
            committed_account(true),
            None,
            || Ok(samples.next().unwrap()),
            || Ok("PRIVATE-TOKEN-ID".into()),
        )
        .unwrap_err(),
        |e| matches!(e, LegacyAuthError::ClockRange),
        StableCode::Internal,
    );
}

#[test]
fn committed_device_single_session_clock_quota_and_poison_keep_diagnostic() {
    use trnm_contracts::StableCode;
    let mut cfg = policy(cache_policy()).0;
    cfg.single_session = true;
    let s = LegacyAuthService::from_config(
        LegacyAuthConfig::new(
            ACCESS_KEY,
            REFRESH_KEY,
            NakamaLegacyKeyLimits::new(4096).unwrap(),
            LegacyAuthPolicy::new(cfg).unwrap(),
        )
        .unwrap(),
    )
    .unwrap();
    assert_committed_failure(
        s.finish_device_account(
            committed_account(true),
            None,
            || {
                assert!(s.state().cache.try_lock().is_ok());
                Err(LegacyAuthError::ClockUnavailable)
            },
            || panic!("no tid after cutoff failure"),
        )
        .unwrap_err(),
        |e| matches!(e, LegacyAuthError::ClockUnavailable),
        StableCode::Internal,
    );
    let mut limits = cache_policy();
    limits.max_users = 1;
    cfg.blacklist_limits = NakamaLegacyBlacklistLimits::new(limits).unwrap();
    let limited = LegacyAuthService::from_config(
        LegacyAuthConfig::new(
            ACCESS_KEY,
            REFRESH_KEY,
            NakamaLegacyKeyLimits::new(4096).unwrap(),
            LegacyAuthPolicy::new(cfg).unwrap(),
        )
        .unwrap(),
    )
    .unwrap();
    limited
        .cache()
        .unwrap()
        .remove_all(cache_user(UserId::new([1; 16])), 100)
        .unwrap();
    let before = limited.blacklist_stats().unwrap();
    assert_committed_failure(
        limited
            .finish_device_account(
                committed_account(true),
                None,
                || Ok(100),
                || panic!("quota failure precedes tid"),
            )
            .unwrap_err(),
        |e| {
            matches!(
                e,
                LegacyAuthError::Cache(NakamaLegacyBlacklistError::UserLimit)
            )
        },
        StableCode::ResourceExhausted,
    );
    assert_eq!(limited.blacklist_stats().unwrap(), before);
    // This poison happens after the mocked durable outcome; the source Add is
    // a no-op but acquiring its shared guard can still fail after whole issuance.
    let poisoned = service();
    let state = Arc::clone(poisoned.state());
    let _ = thread::spawn(move || {
        let _guard = state.cache.lock().unwrap();
        panic!("synthetic poison")
    })
    .join();
    assert_committed_failure(
        poisoned
            .finish_device_account(
                committed_account(true),
                None,
                || Ok(100),
                || Ok("PRIVATE-TOKEN-ID".into()),
            )
            .unwrap_err(),
        |e| matches!(e, LegacyAuthError::CachePoisoned),
        StableCode::Internal,
    );
}

#[test]
fn committed_device_issuer_failure_has_no_pair_and_keeps_native_cleanup() {
    let mut cfg = policy(cache_policy()).0;
    cfg.issuer_limits = NakamaLegacyIssueLimits::new(2, 8192).unwrap();
    let s = LegacyAuthService::from_config(
        LegacyAuthConfig::new(
            ACCESS_KEY,
            REFRESH_KEY,
            NakamaLegacyKeyLimits::new(4096).unwrap(),
            LegacyAuthPolicy::new(cfg).unwrap(),
        )
        .unwrap(),
    )
    .unwrap();
    let before = s.blacklist_stats().unwrap();
    assert_committed_failure(
        s.finish_device_account(
            committed_account(true),
            None,
            || Ok(100),
            || Ok("PRIVATE-TOKEN-ID".into()),
        )
        .unwrap_err(),
        |e| {
            matches!(
                e,
                LegacyAuthError::Issue(NakamaLegacyIssueError::Payload(_))
            )
        },
        trnm_contracts::StableCode::Internal,
    );
    assert_eq!(s.blacklist_stats().unwrap(), before);
}

#[test]
fn existing_device_failure_does_not_claim_current_commit_or_allow_retry() {
    let s = service();
    let failure = s
        .finish_device_account(
            committed_account(false),
            None,
            || panic!("random precedes clock"),
            || Err(LegacyAuthError::RandomUnavailable),
        )
        .unwrap_err();
    assert!(matches!(
        failure,
        LegacyDeviceAuthError::Unconfirmed(LegacyAuthError::RandomUnavailable)
    ));
    assert!(!failure.creation_commit_confirmed());
    assert!(!failure.retry_permitted());
    assert!(!failure.compensation_permitted());
    let native_unknown = LegacyDeviceAuthError::from(LegacyAuthError::DeviceRepository(
        LegacyRepositoryError::Internal,
    ));
    assert!(!native_unknown.creation_commit_confirmed());
    assert!(!native_unknown.retry_permitted());
    assert_eq!(native_unknown.code(), trnm_contracts::StableCode::Internal);
    let mut invalid = committed_account(true);
    invalid.created = false;
    let failure = s
        .finish_device_account(
            invalid,
            None,
            || panic!("invalid outcome must not issue"),
            || panic!("invalid outcome must not issue"),
        )
        .unwrap_err();
    assert!(!failure.creation_commit_confirmed());
    assert_eq!(failure.code(), trnm_contracts::StableCode::DataLoss);
    let LegacyDeviceAuthError::UnconfirmedCleanup {
        cause,
        committed_cleanup_failure,
    } = failure
    else {
        panic!("must retain forged cleanup")
    };
    assert!(matches!(
        cause,
        LegacyAuthError::DeviceRepository(LegacyRepositoryError::DataLoss)
    ));
    assert_eq!(
        committed_cleanup_failure,
        committed_account(true).committed_cleanup_failure.unwrap()
    );
}

#[test]
fn device_success_preserves_exact_normal_token_pair_and_clock_order() {
    let s = service();
    let mut stages = Vec::new();
    let tid_called = std::cell::Cell::new(false);
    let pair = s
        .finish_device_account(
            committed_account(true),
            None,
            || {
                assert!(tid_called.get());
                stages.push("clock");
                Ok(100)
            },
            || {
                tid_called.set(true);
                Ok("PRIVATE-TOKEN-ID".into())
            },
        )
        .unwrap();
    assert_eq!(stages, ["clock", "clock", "clock"]);
    let identity = NakamaLegacyClaims {
        token_id: "PRIVATE-TOKEN-ID",
        user_id: UID,
        username: "PRIVATE-COMMITTED-NAME",
        variables: None,
        expires_at: 0,
        issued_at: 100,
    };
    // Actual unchanged issuer path, exact tokens: no timing/claim normalization.
    let baseline = s
        .issue_session(user(), &identity, true, &mut || Ok(100))
        .unwrap();
    assert_eq!(pair.tokens(), baseline.tokens());
    assert_eq!(
        pair.committed_cleanup_failure,
        committed_account(true).committed_cleanup_failure
    );
}

#[test]
fn public_device_call_keeps_one_repository_outcome_and_commit_truth_on_issue_failure() {
    struct DurableOutcome {
        calls: usize,
        created: bool,
    }
    impl LegacyDeviceRepository for DurableOutcome {
        fn authenticate_legacy_device(
            &mut self,
            _: LegacyDeviceRepositoryInput<'_>,
        ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
            self.calls += 1;
            Ok(committed_account(self.created))
        }
    }
    for created in [false, true] {
        let s = service();
        let state = Arc::clone(s.state());
        let _ = thread::spawn(move || {
            let _guard = state.cache.lock().unwrap();
            panic!("synthetic poison")
        })
        .join();
        let mut repository = DurableOutcome { calls: 0, created };
        let failure = s
            .authenticate_device(
                &mut repository,
                LegacyDeviceAuthInput {
                    account_id: Some("0123456789"),
                    username: "normal",
                    create: Some(true),
                    variables: None,
                },
            )
            .unwrap_err();
        assert_eq!(repository.calls, 1);
        assert_eq!(failure.creation_commit_confirmed(), created);
        assert!(matches!(failure.cause(), LegacyAuthError::CachePoisoned));
        assert_eq!(
            failure.committed_cleanup_failure(),
            committed_account(created).committed_cleanup_failure
        );
        assert!(!failure.retry_permitted());
        assert!(!failure.compensation_permitted());
        assert_eq!(failure.code(), trnm_contracts::StableCode::Internal);
    }
    let s = service();
    let mut repository = DurableOutcome {
        calls: 0,
        created: true,
    };
    let failure = s
        .authenticate_device(
            &mut repository,
            LegacyDeviceAuthInput {
                account_id: None,
                username: "normal",
                create: Some(true),
                variables: None,
            },
        )
        .unwrap_err();
    assert_eq!(repository.calls, 0);
    assert!(!failure.creation_commit_confirmed());
    assert_eq!(failure.code(), trnm_contracts::StableCode::InvalidArgument);
    // A confirmed creation needs no failed cleanup to be durable: keep the
    // post-commit failure even when native cleanup succeeded without diagnostic.
    let mut account = committed_account(true);
    account.committed_cleanup_failure = None;
    let failure = s
        .finish_device_account(
            account,
            None,
            || panic!("random first"),
            || Err(LegacyAuthError::RandomUnavailable),
        )
        .unwrap_err();
    assert!(failure.creation_commit_confirmed());
    assert_eq!(failure.committed_cleanup_failure(), None);
}
