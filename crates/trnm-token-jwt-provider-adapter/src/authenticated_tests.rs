use super::*;
use trnm_token_crypto_provider::KeyHandle;

const REDACTED: &str = "AuthenticatedJwt { [REDACTED] }";

fn fixture(payload_bytes: Vec<u8>, epoch: Option<u32>) -> AuthenticatedJwt {
    AuthenticatedJwt {
        route: epoch.map_or(TokenRoute::Legacy, TokenRoute::Epoch),
        key: KeyReference::new(
            KeyDomain::AccessToken,
            KeyHandle::new("kms://sensitive-provider-location").unwrap(),
            epoch,
        )
        .unwrap(),
        payload_bytes,
    }
}

#[test]
fn debug_redacts_compact_pretty_and_hex_formats() {
    let jwt = fixture(
        br#"{"sub":"sensitive-user","secret":"do-not-log"}"#.to_vec(),
        Some(7),
    );
    for rendered in [
        format!("{jwt:?}"),
        format!("{jwt:#?}"),
        format!("{jwt:x?}"),
        format!("{jwt:X?}"),
    ] {
        assert_eq!(rendered, REDACTED);
    }
}

#[test]
fn debug_is_independent_of_payload_bytes_length_and_route() {
    let payloads = [
        Vec::new(),
        vec![0],
        (0..=255).collect(),
        vec![b'x'; AuthenticationProfile::default().max_payload_bytes],
    ];
    for payload in payloads {
        for epoch in [None, Some(1), Some(u32::MAX)] {
            let jwt = fixture(payload.clone(), epoch);
            assert_eq!(format!("{jwt:?}"), REDACTED);
            assert_eq!(format!("{jwt:#?}"), REDACTED);
        }
    }
}

#[test]
fn debug_redacts_nested_result_option_and_collection() {
    let left = fixture(br#"{"sub":"left-secret"}"#.to_vec(), None);
    let right = fixture(br#"{"sub":"other-longer-secret"}"#.to_vec(), Some(9));
    let left_result: Result<_, AuthenticationError> = Ok(left.clone());
    let right_result: Result<_, AuthenticationError> = Ok(right.clone());
    assert_eq!(format!("{left_result:?}"), format!("{right_result:?}"));
    assert_eq!(format!("{left_result:#?}"), format!("{right_result:#?}"));
    assert_eq!(format!("{:?}", Some(&left)), format!("{:?}", Some(&right)));
    assert_eq!(format!("{:#?}", vec![left]), format!("{:#?}", vec![right]));
}

#[test]
fn debug_redacts_derived_diagnostic_wrapper_and_format_args() {
    #[derive(Debug)]
    struct Diagnostic<'a> {
        authenticated: &'a AuthenticatedJwt,
    }
    let left = fixture(br#"{"sub":"left-secret"}"#.to_vec(), None);
    let right = fixture(vec![0xff; 4096], Some(31));
    let left_diagnostic = Diagnostic {
        authenticated: &left,
    };
    let right_diagnostic = Diagnostic {
        authenticated: &right,
    };
    assert_eq!(left_diagnostic.authenticated.route(), TokenRoute::Legacy);
    assert_eq!(right_diagnostic.authenticated.route(), TokenRoute::Epoch(31));
    assert_eq!(
        format!("{left_diagnostic:?}"),
        format!("{right_diagnostic:?}")
    );
    assert_eq!(
        format!("{}", format_args!("authenticated={left:#?}")),
        format!("{}", format_args!("authenticated={right:#?}"))
    );
}

#[test]
fn read_only_accessors_preserve_exact_authenticated_data() {
    let payload = br#"{"sub":"user-1","exp":2000000000}"#;
    let jwt = fixture(payload.to_vec(), Some(7));
    assert_eq!(jwt.route(), TokenRoute::Epoch(7));
    assert_eq!(jwt.key().domain, KeyDomain::AccessToken);
    assert_eq!(jwt.key().epoch, Some(7));
    assert_eq!(jwt.payload_bytes(), payload);
    assert_eq!(
        jwt.parse_claims(JsonLimits::default())
            .unwrap()
            .as_object()
            .unwrap()
            .get("sub")
            .and_then(JsonValue::as_str),
        Some("user-1")
    );
    assert_eq!(jwt.clone(), jwt);
}

#[test]
fn parse_failure_has_no_payload_in_error_formatting() {
    let jwt = fixture(b"not-json-do-not-log".to_vec(), None);
    let error = jwt.parse_claims(JsonLimits::default()).unwrap_err();
    assert_eq!(error, AuthenticationError::PayloadJson);
    assert_eq!(format!("{error:?}"), "PayloadJson");
    assert_eq!(
        format!("{error}"),
        "authenticated JWT payload JSON parse failed"
    );
}
