use super::*;

fn limits() -> LegacyHttpLimits {
    LegacyHttpLimits::default()
}
fn key() -> LegacyHttpServerKey {
    LegacyHttpServerKey::new(b"server-key".to_vec()).unwrap()
}
fn authorization(password: &[u8]) -> String {
    let mut plain = b"server-key:".to_vec();
    plain.extend_from_slice(password);
    format!(
        "Basic {}",
        base64::engine::general_purpose::STANDARD.encode(plain)
    )
}
fn device(body: &[u8], query: &str) -> Result<LegacyDeviceHttpRequest, LegacyGatewayError> {
    decode_device_http_request(&key(), Some(&authorization(b"")), body, query, limits())
}
fn refresh(body: &[u8]) -> Result<LegacyRefreshHttpRequest, LegacyGatewayError> {
    decode_refresh_http_request(&key(), Some(&authorization(b"")), body, limits())
}

#[test]
fn basic_password_is_ignored_as_bytes_and_first_colon_only() {
    for password in [b"".as_slice(), b"ignored:also", &[0xff, 0xfe, 0, b':']] {
        require_basic_server_key(&key(), Some(&authorization(password)), limits()).unwrap();
    }
    let encoded = authorization(b"binary");
    let with_crlf = format!("{}\r\n{}", &encoded[..9], &encoded[9..]);
    require_basic_server_key(&key(), Some(&with_crlf), limits()).unwrap();
    assert!(!format!("{:?}", key()).contains("server-key"));
}

#[test]
fn basic_exact_prefix_padding_alphabet_key_and_colon_failures() {
    for header in [
        "",
        "basic c2VydmVyLWtleTo=",
        "Bearer c2VydmVyLWtleTo=",
        "Basic c2VydmVyLWtleQ==",
        "Basic c2VydmVyLWtleTo",
        "Basic :",
        "Basic c2VydmVy LWtleTo=",
        "Basic c2VydmVyLWtleTo_",
        "Basic d3Jvbmc6",
        "Basic c2VydmVyLWtleTo===",
    ] {
        let error = require_basic_server_key(&key(), Some(header), limits()).unwrap_err();
        assert_eq!(
            (error.status(), error.message()),
            (401, "Server key invalid")
        );
    }
    let error = require_basic_server_key(&key(), None, limits()).unwrap_err();
    assert_eq!(
        (error.status(), error.message()),
        (401, "Server key required")
    );
}

#[test]
fn basic_accepts_all_unused_terminal_bits_like_non_strict_std_encoding() {
    let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let header = authorization(b"");
    // server-key: is11 bytes, so the penultimate symbol has two unused bits.
    assert!(header.ends_with("To="));
    for symbol in &alphabet[40..44] {
        let mut variant = header.as_bytes().to_vec();
        let index = variant.len() - 2;
        variant[index] = *symbol;
        require_basic_server_key(
            &key(),
            Some(std::str::from_utf8(&variant).unwrap()),
            limits(),
        )
        .unwrap();
    }
}

#[test]
fn authentication_wins_before_invalid_body_query_and_body_budget() {
    for body in [
        b"[bad json".as_slice(),
        &[0xff],
        &vec![b'x'; limits().body_bytes + 1],
    ] {
        let error =
            decode_device_http_request(&key(), Some("Basic invalid"), body, "create=%GG", limits())
                .unwrap_err();
        assert_eq!(
            (error.status(), error.message()),
            (401, "Server key invalid")
        );
        let error = decode_refresh_http_request(&key(), None, body, limits()).unwrap_err();
        assert_eq!(
            (error.status(), error.message()),
            (401, "Server key required")
        );
    }
}

#[test]
fn device_body_account_and_query_are_separate() {
    let request = device(br#"{"id":"1234567890","vars":{"role":"player"},"create":false,"username":"body","account":{"id":"override"}}"#, "create=0&username=query").unwrap();
    let input = request.auth_input();
    assert_eq!(input.account_id, Some("1234567890"));
    assert_eq!(input.username, "query");
    assert_eq!(input.create, Some(false));
    assert_eq!(
        input.variables.unwrap().get("role").map(String::as_str),
        Some("player")
    );
    let input = device(br#"{"account":{"id":"1234567890"}}"#, "").unwrap();
    assert_eq!(input.auth_input().account_id, Some(""));
    assert_eq!(input.auth_input().create, None);
}

#[test]
fn query_wrapper_presence_all_parsebool_spellings_and_value_path() {
    for value in ["1", "t", "T", "TRUE", "true", "True"] {
        assert_eq!(
            device(b"{}", &format!("create={value}"))
                .unwrap()
                .auth_input()
                .create,
            Some(true)
        );
    }
    for value in ["0", "f", "F", "FALSE", "false", "False"] {
        assert_eq!(
            device(b"{}", &format!("create.value={value}"))
                .unwrap()
                .auth_input()
                .create,
            Some(false)
        );
    }
    for value in ["", "yes", "no", "TrUe", "null", " true", "2"] {
        assert_eq!(
            device(b"{}", &format!("create={value}"))
                .unwrap_err()
                .code(),
            StableCode::InvalidArgument
        );
    }
    assert_eq!(
        device(b"{}", "create.unknown=anything")
            .unwrap()
            .auth_input()
            .create,
        Some(false)
    );
    assert_eq!(
        device(b"{}", "create=true&create.value=false")
            .unwrap_err()
            .code(),
        StableCode::Unimplemented
    );
}

#[test]
fn percent_plus_unknown_filtered_and_duplicate_query_cases() {
    let request = device(
        b"{}",
        "username=A%2BB+C%2f%25&create=%54rue&account.id=ignored&unknown=x&unknown=y",
    )
    .unwrap();
    assert_eq!(request.auth_input().username, "A+B C/%");
    assert_eq!(request.auth_input().create, Some(true));
    for query in [
        "create=true&%63reate=false",
        "username=a&username=a",
        "create[true]=false",
        "username.x=a",
        "create.value.x=true",
        "unknown=%",
        "unknown=%GG",
        "unknown=x;y",
    ] {
        assert_eq!(
            device(b"{}", query).unwrap_err().code(),
            StableCode::InvalidArgument,
            "{query}"
        );
    }
    assert_eq!(
        device(b"{}", "username=%FF").unwrap_err().code(),
        StableCode::Unimplemented
    );
    assert_eq!(
        device(b"{}", "unknown=%FF&account.vars[x]=y")
            .unwrap()
            .auth_input()
            .create,
        None
    );
    // Go's regexp '.' does not span LF; this key is unknown, not a bracket group.
    assert_eq!(
        device(b"{}", "create[%0A]=false")
            .unwrap()
            .auth_input()
            .create,
        None
    );
}

#[test]
fn empty_body_eof_first_json_value_and_message_root_type() {
    for body in [
        b"".as_slice(),
        b" \t\r\n",
        b"{}",
        b"{} {}",
        b"{} trailing unread bytes [",
    ] {
        assert_eq!(refresh(body).unwrap().token(), b"");
    }
    for body in [
        b"null".as_slice(),
        b"[]",
        b"true",
        b"123",
        br#""token""#,
        b"{",
        b"{]",
        b"{\"token\":}",
    ] {
        assert_eq!(
            refresh(body).unwrap_err().code(),
            StableCode::InvalidArgument
        );
    }
}

#[test]
fn refresh_absent_null_empty_vars_inherit_and_filled_vars_replace() {
    for body in [br#"{"token":null}"#.as_slice(), br#"{"vars":null}"#, b"{}"] {
        let request = refresh(body).unwrap();
        assert_eq!(request.token(), b"");
        assert!(request.variables().is_none());
    }
    let request = refresh(br#"{"token":"credential","vars":{}}"#).unwrap();
    assert_eq!(request.token(), b"credential");
    assert!(request.variables().is_none());
    let request = refresh(br#"{"vars":{"":"","name":"value"}}"#).unwrap();
    assert_eq!(request.variables().unwrap().len(), 2);
    assert!(!format!("{request:?}").contains("value"));
}

#[test]
fn refresh_empty_map_inheritance_does_not_change_device_empty_map_input() {
    let device = device(br#"{"id":"validdevice01234","vars":{}}"#, "").unwrap();
    assert!(device.auth_input().variables.unwrap().is_empty());
    let refresh = refresh(br#"{"token":"credential","vars":{}}"#).unwrap();
    assert!(refresh.variables().is_none());
}

#[test]
fn refresh_empty_map_inheritance_still_rejects_invalid_and_duplicate_maps() {
    for body in [
        br#"{"vars":[]}"#.as_slice(),
        br#"{"vars":false}"#,
        br#"{"vars":1}"#,
        br#"{"vars":{"x":null}}"#,
        br#"{"vars":{"x":1}}"#,
        br#"{"vars":{"x":"a","x":"b"}}"#,
        br#"{"vars":{},"vars":{}}"#,
    ] {
        assert_eq!(
            refresh(body).unwrap_err().code(),
            StableCode::InvalidArgument
        );
    }
}

#[test]
fn duplicate_known_null_aliases_and_map_keys_are_rejected() {
    for body in [
        br#"{"token":null,"token":"x"}"#.as_slice(),
        br#"{"vars":null,"vars":{}}"#,
        br#"{"token":"a","to\u006ben":"b"}"#,
        br#"{"vars":{"x":"a","\u0078":"b"}}"#,
        br#"{"vars":{"x":null}}"#,
        br#"{"vars":[]}"#,
        br#"{"token":1}"#,
    ] {
        assert_eq!(
            refresh(body).unwrap_err().code(),
            StableCode::InvalidArgument
        );
    }
    for body in [
        br#"{"refresh_token":"a","refreshToken":"b"}"#.as_slice(),
        br#"{"refreshToken":null,"refresh_token":null}"#,
    ] {
        assert_eq!(
            logout_body(body, limits()).unwrap_err().code(),
            StableCode::InvalidArgument
        );
    }
}

#[test]
fn unknown_values_and_duplicates_discard_without_number_conversion() {
    let request = refresh(br#"{"token":"actual","unknown":{"x":[1e999,{"token":false}]},"unknown":null,"Token":"ignored","refresh_token":"ignored"}"#).unwrap();
    assert_eq!(request.token(), b"actual");
    assert!(request.variables().is_none());
    let request = logout_body(
        br#"{"token":null,"refreshToken":"refresh","unknown":{"token":"ignored"}}"#,
        limits(),
    )
    .unwrap();
    assert_eq!(request.token(), b"");
    assert_eq!(request.refresh_token(), b"refresh");
    assert!(!format!("{request:?}").contains("refresh\""));
}

#[test]
fn discarded_strings_still_require_valid_utf8_utf16_and_json_grammar() {
    for body in [
        b"{\"x\":\"\xff\"}".as_slice(),
        br#"{"x":"\ud800"}"#,
        br#"{"x":"\q"}"#,
        br#"{"x":[1,]}"#,
        br#"{"x":00}"#,
    ] {
        assert_eq!(
            refresh(body).unwrap_err().code(),
            StableCode::InvalidArgument
        );
    }
    refresh(br#"{"x":"\ud83d\ude00","token":"\u0061"}"#).unwrap();
}

#[test]
fn json_caps_cover_unknown_containers_nodes_members_and_decoded_strings() {
    let auth = authorization(b"");
    let decode = |body: &[u8], resource_limits| {
        decode_refresh_http_request(&key(), Some(&auth), body, resource_limits)
            .unwrap_err()
            .code()
    };
    assert_eq!(
        decode(
            b"{\"x\":{}}",
            LegacyHttpLimits {
                json_depth: 1,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode(
            b"{\"x\":[1,2]}",
            LegacyHttpLimits {
                json_nodes: 4,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode(
            b"{\"x\":1,\"y\":2}",
            LegacyHttpLimits {
                json_members: 1,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode(
            b"{\"token\":\"large\"}",
            LegacyHttpLimits {
                scalar_bytes: 4,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode(
            br#"{"x":"\u0061\u0062","y":"cd"}"#,
            LegacyHttpLimits {
                decoded_string_bytes: 5,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode(
            br#"{"vars":{"a":"1","b":"2"}}"#,
            LegacyHttpLimits {
                vars_entries: 1,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode(
            b"{}",
            LegacyHttpLimits {
                body_bytes: 1,
                ..limits()
            }
        ),
        StableCode::ResourceExhausted
    );
    decode_refresh_http_request(
        &key(),
        Some(&auth),
        br#"{"x":"[[[[:]]]]"}"#,
        LegacyHttpLimits {
            json_depth: 1,
            ..limits()
        },
    )
    .unwrap();
}

#[test]
fn authorization_query_and_configuration_caps_fail_closed() {
    let header = authorization(b"");
    assert_eq!(
        require_basic_server_key(
            &key(),
            Some(&header),
            LegacyHttpLimits {
                authorization_bytes: header.len() - 1,
                ..limits()
            }
        )
        .unwrap_err()
        .code(),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode_device_http_request(
            &key(),
            Some(&header),
            b"{}",
            "a=x&b=y",
            LegacyHttpLimits {
                query_pairs: 1,
                ..limits()
            }
        )
        .unwrap_err()
        .code(),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        decode_device_http_request(
            &key(),
            Some(&header),
            b"{}",
            "unknown=long",
            LegacyHttpLimits {
                query_bytes: 3,
                ..limits()
            }
        )
        .unwrap_err()
        .code(),
        StableCode::ResourceExhausted
    );
    assert_eq!(
        require_basic_server_key(
            &key(),
            Some(&header),
            LegacyHttpLimits {
                json_depth: 65,
                ..limits()
            }
        )
        .unwrap_err()
        .code(),
        StableCode::Internal
    );
    assert!(LegacyHttpServerKey::new(Vec::new()).is_err());
    assert!(LegacyHttpServerKey::new(vec![b'x'; 4097]).is_err());
}

#[test]
fn session_false_and_default_fields_omit_and_names_are_proto_names() {
    let false_body = encode_session_parts(false, "access", "refresh", limits()).unwrap();
    assert_eq!(
        false_body,
        br#"{"token":"access","refresh_token":"refresh"}"#
    );
    assert_eq!(
        encode_session_parts(true, "access", "refresh", limits()).unwrap(),
        br#"{"created":true,"token":"access","refresh_token":"refresh"}"#
    );
    assert_eq!(
        encode_session_parts(false, "", "", limits()).unwrap(),
        b"{}"
    );
    assert_eq!(encode_legacy_logout(), b"{}");
    let escaped = encode_session_parts(false, "a\"\n", "r\\", limits()).unwrap();
    let object: serde_json::Value = serde_json::from_slice(&escaped).unwrap();
    assert_eq!(object["token"], "a\"\n");
    assert_eq!(object["refresh_token"], "r\\");
    assert_eq!(
        encode_session_parts(
            false,
            "token",
            "refresh",
            LegacyHttpLimits {
                response_bytes: 1,
                ..limits()
            }
        )
        .unwrap_err()
        .code(),
        StableCode::ResourceExhausted
    );
}

#[test]
fn static_public_business_errors_have_source_status_and_no_diagnostics() {
    for (error, code, status, message) in [
        (
            LegacyAuthError::RefreshTokenRequired,
            StableCode::InvalidArgument,
            400,
            "Refresh token is required.",
        ),
        (
            LegacyAuthError::RefreshTokenInvalidOrExpired,
            StableCode::Unauthenticated,
            401,
            "Refresh token invalid or expired.",
        ),
        (
            LegacyAuthError::UserAccountNotFound,
            StableCode::NotFound,
            404,
            "User account not found.",
        ),
        (
            LegacyAuthError::UserAccountBanned,
            StableCode::PermissionDenied,
            403,
            "User account banned.",
        ),
        (
            LegacyAuthError::UsernameAlreadyInUse,
            StableCode::AlreadyExists,
            409,
            "Username is already in use.",
        ),
        (
            LegacyAuthError::SessionLogoutTokenInvalid,
            StableCode::InvalidArgument,
            400,
            "Session token invalid.",
        ),
        (
            LegacyAuthError::RefreshLogoutTokenInvalid,
            StableCode::InvalidArgument,
            400,
            "Refresh token invalid.",
        ),
    ] {
        let public = legacy_gateway_error(&error);
        assert_eq!(
            (public.code(), public.status(), public.message()),
            (code, status, message)
        );
        let json: serde_json::Value = serde_json::from_slice(&public.json_body()).unwrap();
        assert_eq!(json["code"], code as u16);
        assert_eq!(json["message"], message);
        assert_eq!(json.as_object().unwrap().len(), 2);
        assert_eq!(
            public.www_authenticate(),
            if code == StableCode::Unauthenticated {
                Some(message)
            } else {
                None
            }
        );
    }
    let private = LegacyDeviceAuthError::Unconfirmed(LegacyAuthError::DeviceRepository(
        LegacyRepositoryError::Internal,
    ));
    let public = legacy_device_gateway_error(&private);
    assert_eq!(
        (public.code(), public.message()),
        (
            StableCode::Internal,
            "Error finding or creating user account."
        )
    );
    assert!(!String::from_utf8(public.json_body())
        .unwrap()
        .contains("SQLSTATE"));
}

#[test]
fn path_recognition_is_exact_post_and_remains_unqualified() {
    assert_eq!(
        legacy_auth_http_route("POST", "/v2/account/authenticate/device?create=false"),
        Some(LegacyAuthHttpRoute::AuthenticateDevice)
    );
    assert_eq!(
        legacy_auth_http_route("POST", "/v2/account/session/refresh?ignored=%GG"),
        Some(LegacyAuthHttpRoute::Refresh)
    );
    assert_eq!(
        legacy_auth_http_route("POST", "/v2/session/logout"),
        Some(LegacyAuthHttpRoute::Logout)
    );
    for (method, target) in [
        ("GET", "/v2/session/logout"),
        ("POST", "/v2/session/logout/"),
        ("POST", "/v2/session/logoutx"),
        ("POST", "/v2/account/authenticate/device/"),
        ("POST", "/v1/session/refresh"),
    ] {
        assert_eq!(legacy_auth_http_route(method, target), None);
    }
}

#[test]
fn device_native_and_lease_errors_keep_private_message_and_specific_code() {
    use super::super::legacy_auth::{
        LegacyLeaseCancellation, LegacyLeaseCompletion, LegacyLeaseFailure,
        LegacyNativeAccountFailure,
    };
    let native = LegacyNativeAccountFailure {
        code: StableCode::Internal,
        phase: Some("private-commit-phase"),
        last_failure: None,
        cleanup_failure: None,
        attempts: Some(5),
        exhausted: Some(true),
        unknown_commit: Some(true),
    };
    for repository in [
        LegacyRepositoryError::NativeFailure(native),
        LegacyRepositoryError::Lease(Box::new(LegacyLeaseFailure {
            boundary_error: trnm_contracts::DomainError::new(
                StableCode::Unavailable,
                "private-native-reason",
                trnm_contracts::RetryClass::Never,
            ),
            setup_error: None,
            completion: LegacyLeaseCompletion::NativeFailure(native),
            cancellation: LegacyLeaseCancellation::Shutdown,
            lease_retired: true,
        })),
    ] {
        let code = repository.code();
        let device = LegacyDeviceAuthError::Unconfirmed(LegacyAuthError::DeviceRepository(
            repository.clone(),
        ));
        let public = legacy_device_gateway_error(&device);
        assert_eq!(public.code(), code);
        assert_eq!(public.message(), "Error finding or creating user account.");
        let json: serde_json::Value = serde_json::from_slice(&public.json_body()).unwrap();
        assert_eq!(json["code"], code as u16);
        assert_eq!(json["message"], "Error finding or creating user account.");
        assert_eq!(json.as_object().unwrap().len(), 2);
        let rendered = format!(
            "{public:?} {public} {}",
            String::from_utf8(public.json_body()).unwrap()
        );
        for secret in [
            "private-commit-phase",
            "private-native-reason",
            "SQLSTATE",
            "unknown_commit",
            "attempts",
        ] {
            assert!(!rendered.contains(secret));
        }
        let refresh = legacy_gateway_error(&LegacyAuthError::Repository(repository));
        assert_eq!(refresh.code(), code);
        assert_eq!(refresh.message(), "Error finding user account.");
    }
}

#[test]
fn device_error_mapping_preserves_semantics_and_old_private_branches() {
    for (repository, code, message) in [
        (
            LegacyRepositoryError::UserNotFound,
            StableCode::NotFound,
            "User account not found.",
        ),
        (
            LegacyRepositoryError::UserBanned,
            StableCode::PermissionDenied,
            "User account banned.",
        ),
        (
            LegacyRepositoryError::UsernameAlreadyInUse,
            StableCode::AlreadyExists,
            "Username is already in use.",
        ),
        (
            LegacyRepositoryError::Unimplemented,
            StableCode::Unimplemented,
            "Legacy authentication unavailable.",
        ),
        (
            LegacyRepositoryError::Internal,
            StableCode::Internal,
            "Error finding or creating user account.",
        ),
        (
            LegacyRepositoryError::Unavailable,
            StableCode::Unavailable,
            "Error finding or creating user account.",
        ),
        (
            LegacyRepositoryError::DataLoss,
            StableCode::DataLoss,
            "Error finding or creating user account.",
        ),
        (
            LegacyRepositoryError::ResourceExhausted,
            StableCode::ResourceExhausted,
            "Error finding or creating user account.",
        ),
    ] {
        let device =
            LegacyDeviceAuthError::Unconfirmed(LegacyAuthError::DeviceRepository(repository));
        let public = legacy_device_gateway_error(&device);
        assert_eq!((public.code(), public.message()), (code, message));
    }
}

#[test]
fn registered_options_extensions_reject_before_unknown_and_null_handling() {
    const NAMES: [&str; 7] = [
        "google.api.http",
        "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_swagger",
        "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_operation",
        "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_schema",
        "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_enum",
        "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_tag",
        "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_field",
    ];
    for name in NAMES {
        for value in ["null", "{}", "false"] {
            let raw = format!(r#"{{"[{}]":{}}}"#, name, value);
            for result in [
                device(raw.as_bytes(), "").map(|_| ()),
                refresh(raw.as_bytes()).map(|_| ()),
                logout_body(raw.as_bytes(), limits()).map(|_| ()),
            ] {
                assert_eq!(result.unwrap_err().code(), StableCode::InvalidArgument);
            }
        }
        let escaped = format!(r#"{{"\u005b{}\u005d":null}}"#, name);
        assert!(device(escaped.as_bytes(), "").is_err());
        assert!(refresh(escaped.as_bytes()).is_err());
        assert!(logout_body(escaped.as_bytes(), limits()).is_err());
        let ordinary = format!(r#"{{"{}":null}}"#, name);
        device(ordinary.as_bytes(), "").unwrap();
        refresh(ordinary.as_bytes()).unwrap();
        logout_body(ordinary.as_bytes(), limits()).unwrap();
        // A protobuf string map is not a message extension namespace.
        let map = format!(r#"{{"vars":{{"[{}]":"data"}}}}"#, name);
        assert_eq!(
            device(map.as_bytes(), "")
                .unwrap()
                .variables
                .unwrap()
                .get(&format!("[{name}]")),
            Some(&"data".to_string())
        );
    }
    for name in [
        "[unregistered.extension]",
        "[google.api.http]suffix",
        "[[google.api.http]]",
        "[GOOGLE.API.HTTP]",
        "[]",
    ] {
        let raw = format!(r#"{{"{}":null}}"#, name);
        device(raw.as_bytes(), "").unwrap();
        refresh(raw.as_bytes()).unwrap();
        logout_body(raw.as_bytes(), limits()).unwrap();
    }
}

#[test]
fn custom_account_body_query_authentication_and_debug_do_not_cross_boundaries() {
    let r=decode_custom_http_request(&key(),Some(&authorization(b"")),br#"{"id":"secret-custom","vars":{"k":"v"},"username":"body","create":true,"account":{"id":"override"}}"#,"create=false&username=query",limits()).unwrap();
    let i = r.auth_input();
    assert_eq!(i.account_id, Some("secret-custom"));
    assert_eq!(i.username, "query");
    assert_eq!(i.create, Some(false));
    assert_eq!(i.variables.unwrap().get("k").map(String::as_str), Some("v"));
    assert!(!format!("{r:?}").contains("secret-custom"));
    let e = decode_custom_http_request(&key(), None, b"invalidJSON", "create=%GG", limits())
        .unwrap_err();
    assert_eq!((e.status(), e.message()), (401, "Server key required"));
    let r = decode_custom_http_request(&key(), Some(&authorization(b"")), b" \n", "", limits())
        .unwrap();
    assert_eq!(r.auth_input().account_id, Some(""));
    assert_eq!(r.auth_input().create, None);
}
#[test]
fn custom_closed_route_and_gateway_messages_keep_exact_source_classes() {
    assert_eq!(
        legacy_auth_http_route("POST", "/v2/account/authenticate/custom?create=false"),
        Some(LegacyAuthHttpRoute::AuthenticateCustom)
    );
    for (method, target) in [
        ("GET", "/v2/account/authenticate/custom"),
        ("POST", "/v2/account/authenticate/custom/"),
        ("POST", "/v2/account/authenticate/customx"),
        ("POST", "/v2/account/authenticate/email"),
    ] {
        assert_eq!(legacy_auth_http_route(method, target), None);
    }
    let input = LegacyCustomAuthError::Unconfirmed(LegacyAuthError::CustomInput(
        super::super::legacy_auth::CustomInputError::IdRequired,
    ));
    let e = legacy_custom_gateway_error(&input);
    assert_eq!((e.status(), e.message()), (400, "Custom ID is required."));
    let creation = LegacyCustomAuthError::Unconfirmed(LegacyAuthError::CustomRepository(
        LegacyRepositoryError::Internal,
    ));
    let e = legacy_custom_gateway_error(&creation);
    assert_eq!(
        (e.status(), e.message()),
        (500, "Error finding or creating user account.")
    );
}
