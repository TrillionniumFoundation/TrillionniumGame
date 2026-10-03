//! Root executes the tests that construct the real Legacy sweeper. Offline
//! author probes execute only the three explicitly pure functions below.
use super::super::auth::AccessTokenVerifier;
use super::super::auth_runtime::AuthAuthorityRuntime;
use super::super::config::AuthAuthorityConfig;
use super::super::http::{Request, Response};
use super::super::legacy_auth::{
    LegacyCustomAccount, LegacyCustomRepositoryInput, LegacyDeviceAccount,
    LegacyDeviceRepositoryInput, LegacyRepositoryError, LegacyStoredUser,
};
use super::super::legacy_config::LegacyServerAuthConfig;
use super::{App, Repository, SharedAppMetrics, SharedDrain};
use base64::Engine;
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use trnm_contracts::{Digest32, DomainError, SessionFamilyId, UserId};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, SessionFamilyRecord,
};
const ADMIN: &str = "test_operator_secret_not_a_player";
#[derive(Clone, Debug, Default)]
struct Counts {
    device: usize,
    custom: usize,
    user: usize,
    durable: usize,
    storage: usize,
}
#[derive(Clone, Debug, Default)]
struct Fake {
    counts: Arc<Mutex<Counts>>,
}
impl Repository for Fake {
    fn authenticate_legacy_custom(
        &mut self,
        input: LegacyCustomRepositoryInput<'_>,
    ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
        self.counts.lock().unwrap().custom += 1;
        assert_eq!(input.custom_id, "validcustom123");
        Ok(LegacyCustomAccount {
            user_id: UserId::new([7; 16]),
            stored_username: "StoredCustom".to_owned(),
            created: input.create,
        })
    }
    fn read_storage_objects(
        &mut self,
        actor: trnm_persistence_pg::StorageActor,
        _keys: &[trnm_persistence_pg::StorageObjectKey],
    ) -> Result<Vec<trnm_persistence_pg::StoredStorageObject>, DomainError> {
        assert_eq!(
            actor,
            trnm_persistence_pg::StorageActor::User(UserId::new([7; 16]))
        );
        self.counts.lock().unwrap().storage += 1;
        Ok(Vec::new())
    }

    fn bootstrap_entity(
        &mut self,
        _: EntityId,
        _: u64,
        _: Digest32,
        _: u64,
    ) -> Result<EntityHead, DomainError> {
        panic!("auth route reached authority bootstrap")
    }
    fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        panic!("auth route reached authority commit")
    }
    fn verify_access_session(
        &mut self,
        _: SessionFamilyId,
        _: UserId,
        _: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        self.counts.lock().unwrap().durable += 1;
        panic!("legacy used durable persisted verification")
    }
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError> {
        self.counts.lock().unwrap().user += 1;
        Ok(Some(LegacyStoredUser {
            user_id: user,
            username: "StoredName".to_owned(),
            disable_time_unix_seconds: None,
        }))
    }
    fn authenticate_legacy_device(
        &mut self,
        input: LegacyDeviceRepositoryInput<'_>,
    ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
        self.counts.lock().unwrap().device += 1;
        assert_eq!(input.device_id, "validdevice01234");
        assert!(!input.create);
        Ok(LegacyDeviceAccount {
            user_id: UserId::new([7; 16]),
            stored_username: "StoredName".to_owned(),
            created: false,
            committed_cleanup_failure: None,
        })
    }
}
fn app(fake: Fake, authority: AuthAuthorityRuntime, drain: SharedDrain) -> App<Fake> {
    App::with_shared_state(fake, ADMIN.to_owned(), SharedAppMetrics::default(), drain)
        .with_auth_authority(authority)
}
fn req(target: &str, auth: Option<&str>, body: &[u8]) -> Request {
    let mut headers = BTreeMap::from([("content-type".to_owned(), "application/json".to_owned())]);
    if let Some(auth) = auth {
        headers.insert("authorization".to_owned(), auth.to_owned());
    }
    Request::new("POST", target, headers, body)
}
fn basic() -> String {
    format!(
        "Basic {}",
        base64::engine::general_purpose::STANDARD.encode(b"server key:ignored\xff")
    )
}
fn legacy() -> AuthAuthorityRuntime {
    let config = LegacyServerAuthConfig::new(
        b"server key".to_vec(),
        b"access key".to_vec(),
        b"refresh key".to_vec(),
        60,
        3600,
        false,
    )
    .unwrap();
    // This constructs a real owned sweeper and is executed only by root Cargo.
    AuthAuthorityRuntime::install(&AuthAuthorityConfig::NakamaLegacy(config)).unwrap()
}
fn session(app: &mut App<Fake>) -> serde_json::Value {
    let r = app.handle(&req(
        "/v2/account/authenticate/device?create=false&username=PlayerName",
        Some(&basic()),
        br#"{"id":"validdevice01234","vars":{"secret":"privateVars"}}"#,
    ));
    assert_eq!(r.status, 200);
    let body: serde_json::Value = serde_json::from_slice(&r.body).unwrap();
    assert!(body.get("created").is_none());
    body
}

#[test]
fn wrong_authority_never_falls_back_or_calls_native_accounts() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    for authority in [
        AuthAuthorityRuntime::Disabled,
        AuthAuthorityRuntime::durable(
            AccessTokenVerifier::from_epoch_key(
                "peer".to_owned(),
                "peer".to_owned(),
                1,
                vec![1; 32],
            )
            .unwrap(),
        ),
    ] {
        let mut app = app(fake.clone(), authority, SharedDrain::default());
        for path in [
            "/v2/account/authenticate/device?create=false",
            "/v2/account/session/refresh?unknown=true",
            "/v2/session/logout?x=1",
        ] {
            assert_eq!(
                app.handle(&req(path, Some(&basic()), b"invalid")).status,
                501
            );
        }
    }
    let c = counts.lock().unwrap();
    assert_eq!((c.device, c.user, c.durable), (0, 0, 0));
}
#[test]
fn every_legacy_post_query_variant_is_rejected_by_drain_before_parsing() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let drain = SharedDrain::default();
    drain.begin();
    let mut app = app(fake, AuthAuthorityRuntime::Disabled, drain);
    for path in [
        "/v2/account/authenticate/device",
        "/v2/account/authenticate/device?create=false",
        "/v2/account/session/refresh?x=1",
        "/v2/session/logout?x=1",
    ] {
        assert_eq!(app.handle(&req(path, None, b"invalid-body")).status, 503);
    }
    let c = counts.lock().unwrap();
    assert_eq!((c.device, c.user, c.durable), (0, 0, 0));
}
#[test]
fn request_response_debug_and_static_challenge_cannot_disclose_secrets() {
    let request = req(
        "/v2/account/authenticate/device?username=privateDevice",
        Some("Bearer privateJWT"),
        b"privateVars",
    );
    let response = Response::json(401, b"privateJWT".to_vec())
        .with_www_authenticate(Some("Auth token invalid"));
    for text in [format!("{request:?}"), format!("{response:?}")] {
        for secret in ["privateDevice", "privateJWT", "privateVars"] {
            assert!(!text.contains(secret));
        }
    }
    let mut bytes = Vec::new();
    response.write_to(&mut bytes).unwrap();
    assert!(bytes
        .windows(b"WWW-Authenticate: Auth token invalid\r\n".len())
        .any(|w| w == b"WWW-Authenticate: Auth token invalid\r\n"));
    let mut bytes = Vec::new();
    Response::json(401, b"{}".to_vec())
        .with_www_authenticate(Some("invalid\r\nInjected: header"))
        .write_to(&mut bytes)
        .unwrap();
    assert!(!bytes.windows(b"Injected".len()).any(|w| w == b"Injected"));
}
#[test]
fn shared_legacy_runtime_revocation_crosses_app_and_never_uses_family_calls() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let authority = legacy();
    let mut first = app(fake.clone(), authority.clone(), SharedDrain::default());
    let mut second = app(fake, authority, SharedDrain::default());
    let tokens = session(&mut first);
    let token = tokens["token"].as_str().unwrap();
    let bearer = format!("Bearer {token}");
    let before = first.handle(&req(
        "/v2/storage",
        Some(&bearer),
        br#"{"object_ids":[{"collection":"c","key":"k"}]}"#,
    ));
    assert_eq!(before.status, 200);
    assert_eq!(counts.lock().unwrap().storage, 1);
    let body = serde_json::to_vec(&tokens).unwrap();
    assert_eq!(
        second
            .handle(&req("/v2/session/logout?unknown=1", Some(&bearer), &body))
            .status,
        200
    );
    assert_eq!(
        first
            .handle(&req("/v2/storage", Some(&bearer), b"invalid"))
            .status,
        401
    );
    assert_eq!(
        first
            .handle(&Request::new(
                "GET",
                "/v1/session/me",
                BTreeMap::from([("authorization".to_owned(), bearer)]),
                Vec::new()
            ))
            .status,
        501
    );
    let c = counts.lock().unwrap();
    assert_eq!((c.device, c.user, c.durable), (1, 0, 0));
}
#[test]
fn legacy_authentication_and_complete_decode_precede_native_and_cache_mutation() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let authority = legacy();
    let AuthAuthorityRuntime::NakamaLegacy { service, .. } = &authority else {
        unreachable!()
    };
    let service = service.clone();
    let mut app = app(fake, authority, SharedDrain::default());
    let initial = service.blacklist_stats().unwrap();
    for (path, auth, expected) in [
        ("/v2/account/authenticate/device?create=false", None, 401),
        (
            "/v2/account/authenticate/device?create=false",
            Some(basic()),
            400,
        ),
        ("/v2/account/session/refresh", Some(basic()), 400),
        ("/v2/session/logout", Some(format!("Bearer {ADMIN}")), 401),
    ] {
        assert_eq!(
            app.handle(&req(path, auth.as_deref(), b"invalid-body"))
                .status,
            expected
        );
    }
    let after = service.blacklist_stats().unwrap();
    assert_eq!(initial, after);
    let c = counts.lock().unwrap();
    assert_eq!((c.device, c.user, c.durable), (0, 0, 0));
}
#[test]
fn legacy_refresh_reads_once_and_invalid_logout_second_token_changes_no_cache() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let authority = legacy();
    let AuthAuthorityRuntime::NakamaLegacy { service, .. } = &authority else {
        unreachable!()
    };
    let service = service.clone();
    let mut app = app(fake, authority, SharedDrain::default());
    let tokens = session(&mut app);
    let bearer = format!("Bearer {}", tokens["token"].as_str().unwrap());
    let initial = service.blacklist_stats().unwrap();
    let invalid = serde_json::json!({"token":tokens["token"],"refresh_token":"invalid"});
    assert_eq!(
        app.handle(&req(
            "/v2/session/logout",
            Some(&bearer),
            &serde_json::to_vec(&invalid).unwrap()
        ))
        .status,
        400
    );
    assert_eq!(initial, service.blacklist_stats().unwrap());
    let refresh = serde_json::json!({"token":tokens["refresh_token"],"vars":{}});
    assert_eq!(
        app.handle(&req(
            "/v2/account/session/refresh?ignored=x",
            Some(&basic()),
            &serde_json::to_vec(&refresh).unwrap()
        ))
        .status,
        200
    );
    let c = counts.lock().unwrap();
    assert_eq!((c.device, c.user, c.durable), (1, 1, 0));
}

#[test]
fn selected_legacy_drain_rejects_before_native_and_blacklist_mutation() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let authority = legacy();
    let AuthAuthorityRuntime::NakamaLegacy { service, .. } = &authority else {
        unreachable!()
    };
    let service = service.clone();
    let initial = service.blacklist_stats().unwrap();
    let drain = SharedDrain::default();
    drain.begin();
    let mut app = app(fake, authority, drain);
    for path in [
        "/v2/account/authenticate/device?create=false&username=PlayerName",
        "/v2/account/session/refresh?x=1",
        "/v2/session/logout?x=1",
    ] {
        assert_eq!(
            app.handle(&req(path, Some(&basic()), b"invalid-body"))
                .status,
            503
        );
    }
    assert_eq!(initial, service.blacklist_stats().unwrap());
    let c = counts.lock().unwrap();
    assert_eq!((c.device, c.user, c.durable), (0, 0, 0));
}

#[test]
fn custom_selected_app_uses_stored_username_vars_and_created_after_typed_outcome() {
    // Root Cargo only: legacy() starts the real owned sweeper.
    for create in [false, true] {
        let fake = Fake::default();
        let counts = fake.counts.clone();
        let mut app = app(fake, legacy(), SharedDrain::default());
        let response = app.handle(&req(
            &format!("/v2/account/authenticate/custom?create={create}&username=Requested"),
            Some(&basic()),
            br#"{"id":"validcustom123","vars":{"realm":"one"}}"#,
        ));
        assert_eq!(response.status, 200);
        let body: serde_json::Value = serde_json::from_slice(&response.body).unwrap();
        assert_eq!(
            body.get("created")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            create
        );
        let token = body["token"].as_str().unwrap();
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(token.split('.').nth(1).unwrap())
            .unwrap();
        let claims: serde_json::Value = serde_json::from_slice(&payload).unwrap();
        assert_eq!(claims["usn"], "StoredCustom");
        assert_eq!(claims["vrs"]["realm"], "one");
        let c = counts.lock().unwrap();
        assert_eq!(c.custom, 1);
        assert_eq!(c.device, 0);
        assert_eq!(c.durable, 0);
    }
}
#[test]
fn custom_app_wrong_authority_and_drain_skip_all_account_effects() {
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let mut server = app(fake, AuthAuthorityRuntime::Disabled, SharedDrain::default());
    let response = server.handle(&req(
        "/v2/account/authenticate/custom",
        Some(&basic()),
        b"invalid",
    ));
    assert_eq!(response.status, 501);
    assert_eq!(counts.lock().unwrap().custom, 0);
    let fake = Fake::default();
    let counts = fake.counts.clone();
    let drain = SharedDrain::default();
    drain.begin();
    let mut server = app(fake, AuthAuthorityRuntime::Disabled, drain);
    let response = server.handle(&req(
        "/v2/account/authenticate/custom?create=false",
        None,
        b"invalid",
    ));
    assert_eq!(response.status, 503);
    let body: serde_json::Value = serde_json::from_slice(&response.body).unwrap();
    assert_eq!(body["code"], 14);
    assert_eq!(counts.lock().unwrap().custom, 0);
}
