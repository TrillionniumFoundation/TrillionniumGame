fn native_storage_render(control: &mut postgres::Client, raw: &str) -> String {
    control
        .query_one("SELECT (($1::TEXT)::JSONB)::TEXT", &[&raw])
        .unwrap_or_else(|_| panic!("canonical storage fixture: native JSONB projection failed"))
        .get(0)
}

fn seed_storage_known_native(
    control: &mut postgres::Client,
    name: &str,
    owner: UserId,
    permission: ReadPermission,
    raw: &str,
) -> String {
    let native = native_storage_render(control, raw);
    let original = raw.as_bytes();
    let request_digest = IntegrityDigest::from_value(original).get();
    let projection_digest = IntegrityDigest::from_value(native.as_bytes()).get();
    let version = ContentVersion::from_value(original);
    assert_eq!(control.execute(
        "INSERT INTO public.trnm_storage_objects \
         (collection, object_key, user_id, value_bytes, version_digest, \
          read_permission, write_permission, updated_at_ms, create_time, update_time, \
          value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest) \
         VALUES ($1, $2, $3, $4, $5, $6, 1, 0, pg_catalog.now(), pg_catalog.now(), \
                 ($7::TEXT)::JSONB, $8, $9, 'write-request-bytes', NULL)",
        &[&LIVE_COLLECTION, &name, &owner.as_bytes().as_slice(), &original,
          &request_digest.as_bytes().as_slice(), &permission.get(), &raw,
          &version.as_str(), &projection_digest.as_bytes().as_slice()],
    ).unwrap_or_else(|_| panic!("canonical storage fixture: known native object seed failed")), 1);
    native
}

fn seed_storage_unknown_native(
    control: &mut postgres::Client,
    name: &str,
    raw_native_input: &str,
    public_version: &str,
) -> String {
    let native = native_storage_render(control, raw_native_input);
    let projection = IntegrityDigest::from_value(native.as_bytes()).get();
    // A synthetic source-bound manifest witnesses this fixture only. This is
    // not an implemented Nakama export importer or original-request proof.
    let manifest =
        IntegrityDigest::from_value(b"canonical storage v3 synthetic source manifest").get();
    assert_eq!(control.execute(
        "INSERT INTO public.trnm_storage_objects \
         (collection, object_key, user_id, value_bytes, version_digest, \
          read_permission, write_permission, updated_at_ms, create_time, update_time, \
          value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest) \
         VALUES ($1, $2, $3, NULL, NULL, 1, 1, 0, NULL, NULL, \
                 ($4::TEXT)::JSONB, $5, $6, 'nakama-export-unknown-request', $7)",
        &[&LIVE_COLLECTION, &name, &LIVE_USER.as_bytes().as_slice(), &raw_native_input,
          &public_version, &projection.as_bytes().as_slice(), &manifest.as_bytes().as_slice()],
    ).unwrap_or_else(|_| panic!("canonical storage fixture: unknown native object seed failed")), 1);
    native
}

fn native_input_gateway_failure(
    error: &postgres::Error,
    deleting: bool,
) -> (u16, serde_json::Value) {
    let classified = error
        .code()
        .map(|code| trnm_persistence_pg::classify_sqlstate(code.code()))
        .unwrap_or_else(|| {
            DomainError::new(
                StableCode::Unavailable,
                "native_input_sqlstate_missing",
                RetryClass::Never,
            )
        });
    let message = if deleting {
        "Error deleting storage objects."
    } else {
        "Error writing storage objects."
    };
    match classified.code() {
        StableCode::InvalidArgument
            if !deleting && classified.reason() == "database_constraint_violation" =>
        {
            (500, serde_json::json!({"code":13,"message":message}))
        }
        StableCode::InvalidArgument => (
            400,
            serde_json::json!({"code":3,"message":"Invalid storage request."}),
        ),
        StableCode::ResourceExhausted => (429, serde_json::json!({"code":8,"message":message})),
        _ => (500, serde_json::json!({"code":13,"message":message})),
    }
}

fn prove_storage_jsonb_v3_app(
    control: &mut postgres::Client,
    app: &mut App<PgRepository>,
    credential: &str,
    profile: DatabaseProfile,
) {
    let history = [
        ("v3-history-null", "null", String::new()),
        ("v3-history-array", "[1,1.0,false,null]", "*".to_owned()),
        (
            "v3-history-string",
            "\"native \\u263a string\"",
            "UPPERCASE-NONHEX".to_owned(),
        ),
        ("v3-history-number", "1.000", "雪".repeat(32)),
        ("v3-history-boolean", "true", "trailing ".to_owned()),
        (
            "v3-history-object",
            "{\"duplicate\":1,\"duplicate\":2}",
            "not-a-md5".to_owned(),
        ),
    ];
    let native_history = history
        .iter()
        .map(|(name, raw, version)| {
            let native = seed_storage_unknown_native(control, name, raw, version);
            ((*name).to_owned(), (native, version.clone()))
        })
        .collect::<BTreeMap<_, _>>();
    let read_body = serde_json::json!({"object_ids":history.iter().map(|(name, _, _)| {
        serde_json::json!({"collection":LIVE_COLLECTION,"key":name,"user_id":LIVE_USER_UUID})
    }).collect::<Vec<_>>()})
    .to_string();
    let read = app.handle(&read_request("/v2/storage", Some(credential), &read_body));
    assert_eq!(read.status, 200);
    let encoded = json(&read);
    assert_eq!(encoded["objects"].as_array().unwrap().len(), history.len());
    let listed = app.handle(&list_request(
        &format!("/v2/storage/{LIVE_COLLECTION}/{LIVE_USER_UUID}?limit=100"),
        Some(credential),
    ));
    assert_eq!(listed.status, 200);
    let listed = json(&listed);
    let mut list_history_count = 0;
    for objects in [&encoded["objects"], &listed["objects"]] {
        for object in objects.as_array().unwrap() {
            let name = object["key"].as_str().unwrap();
            let Some((native, version)) = native_history.get(name) else {
                continue;
            };
            assert_eq!(
                object["value"].as_str().unwrap().as_bytes(),
                native.as_bytes()
            );
            if version.is_empty() {
                assert!(object.get("version").is_none());
            } else {
                assert_eq!(object["version"].as_str().unwrap(), version);
            }
            assert!(object.get("create_time").is_none());
            assert!(object.get("update_time").is_none());
            assert_eq!(object["permission_read"], 1);
            assert_eq!(object["permission_write"], 1);
            list_history_count += 1;
        }
    }
    assert_eq!(list_history_count, 2 * history.len());

    let exact_raw = " { \"z\":2, \"a\":1, \"a\":3 }\n";
    let exact_native = native_storage_render(control, exact_raw);
    let exact_version = ContentVersion::from_value(exact_raw.as_bytes());
    assert_ne!(
        exact_version,
        ContentVersion::from_value(exact_native.as_bytes())
    );
    let write = serde_json::json!({"objects":[{
        "collection":LIVE_COLLECTION,"key":"v3-history-object","value":exact_raw,"version":"not-a-md5"
    }]}).to_string();
    let written = app.handle(&request("/v2/storage", Some(credential), &write));
    assert_eq!(written.status, 200);
    let ack = json(&written);
    assert_eq!(ack["acks"][0]["version"], exact_version.as_str());
    assert!(ack["acks"][0].get("create_time").is_none());
    assert!(ack["acks"][0]["update_time"].as_str().is_some());
    assert_eq!(
        persisted_storage_value(control, "v3-history-object", LIVE_USER),
        Some(exact_native.as_bytes().to_vec())
    );
    let read_body = serde_json::json!({"object_ids":[{
        "collection":LIVE_COLLECTION,"key":"v3-history-object","user_id":LIVE_USER_UUID
    }]})
    .to_string();
    let known_read = app.handle(&read_request("/v2/storage", Some(credential), &read_body));
    assert_eq!(known_read.status, 200);
    let known_read = json(&known_read);
    let known_list = app.handle(&list_request(
        &format!("/v2/storage/{LIVE_COLLECTION}/{LIVE_USER_UUID}?limit=100"),
        Some(credential),
    ));
    assert_eq!(known_list.status, 200);
    let known_list = json(&known_list);
    for objects in [&known_read["objects"], &known_list["objects"]] {
        let object = objects
            .as_array()
            .unwrap()
            .iter()
            .find(|object| object["key"].as_str() == Some("v3-history-object"))
            .unwrap();
        assert_eq!(
            object["value"].as_str().unwrap().as_bytes(),
            exact_native.as_bytes()
        );
        assert_eq!(object["version"], exact_version.as_str());
        assert!(object.get("create_time").is_none());
        let projected: prost_types::Timestamp =
            object["update_time"].as_str().unwrap().parse().unwrap();
        assert_eq!(projected.nanos, 0);
    }
    for (name, _, version) in [&history[0], &history[1], &history[3]] {
        let input = serde_json::json!({"object_ids":[{"collection":LIVE_COLLECTION,"key":name,"version":version}]}).to_string();
        let deleted = app.handle(&request("/v2/storage/delete", Some(credential), &input));
        assert_eq!(deleted.status, 200);
        assert_eq!(deleted.body, b"{}");
        assert!(persisted_storage_value(control, name, LIVE_USER).is_none());
    }

    // Unknown original request provenance is not promoted by a blind no-op,
    // even when incoming request content differs from the old native payload.
    let raw = " { \"same\": 1 }\n";
    let version = ContentVersion::from_value(raw.as_bytes());
    seed_storage_unknown_native(control, "v3-unknown-noop", "null", version.as_str());
    let before = storage_live_snapshot(control);
    let mut input = serde_json::json!({"objects":[{
        "collection":LIVE_COLLECTION,"key":"v3-unknown-noop","value":raw,
        "permission_read":1,"permission_write":1
    }]});
    let blind = app.handle(&request(
        "/v2/storage",
        Some(credential),
        &input.to_string(),
    ));
    assert_eq!(blind.status, 200);
    let ack = json(&blind);
    assert_eq!(ack["acks"][0]["version"], version.as_str());
    assert!(ack["acks"][0].get("create_time").is_none());
    assert!(ack["acks"][0].get("update_time").is_none());
    assert!(
        storage_live_snapshot(control) == before,
        "unknown no-op changed native data, provenance or times"
    );
    input["objects"][0]["version"] = serde_json::json!(version.as_str());
    let exact = app.handle(&request(
        "/v2/storage",
        Some(credential),
        &input.to_string(),
    ));
    assert_eq!(exact.status, 200);
    let ack = json(&exact);
    assert_eq!(ack["acks"][0]["version"], version.as_str());
    assert!(ack["acks"][0].get("create_time").is_none());
    assert!(ack["acks"][0]["update_time"].as_str().is_some());
    let changed = storage_live_snapshot(control)
        .into_iter()
        .find(|row| row.key == "v3-unknown-noop")
        .unwrap();
    assert_eq!(changed.value, native_storage_render(control, raw));
    assert_eq!(changed.public_version, version.as_str());
    assert_eq!(changed.value_origin, "write-request-bytes");
    assert_eq!(changed.raw_value.as_deref(), Some(raw.as_bytes()));
    assert_eq!(
        changed.raw_integrity.as_deref(),
        Some(
            IntegrityDigest::from_value(raw.as_bytes())
                .get()
                .as_bytes()
                .as_slice()
        )
    );
    assert!(changed.source_manifest.is_none());
    assert!(changed.create_micros.is_none());
    assert!(changed.update_micros.is_some());

    // A legal historical JSONB render may exceed the new request's 1 MiB
    // limit. It remains a valid read under the separate 16/32 MiB budgets.
    let large_input = format!("\"{}\"", "x".repeat(1024 * 1024 + 32));
    let native_large = seed_storage_unknown_native(
        control,
        "v3-large-native",
        &large_input,
        "large-native-history",
    );
    assert!(native_large.len() > 1024 * 1024);
    let input = serde_json::json!({"object_ids":[{
        "collection":LIVE_COLLECTION,"key":"v3-large-native","user_id":LIVE_USER_UUID
    }]})
    .to_string();
    let read = app.handle(&read_request("/v2/storage", Some(credential), &input));
    assert_eq!(read.status, 200);
    assert!(read.body.len() <= super::storage_api::MAX_ENCODED_STORAGE_RESPONSE_BYTES);
    assert!(
        json(&read)["objects"][0]["value"]
            .as_str()
            .unwrap()
            .as_bytes()
            == native_large.as_bytes(),
        "large native JSONB value changed"
    );
    assert_eq!(json(&read)["objects"][0]["version"], "large-native-history");

    // SQL profile acceptance is observed directly, without guessing SQLSTATE
    // from a domain reason or claiming the result is a Nakama differential.
    let token = "native\0condition";
    let native_condition = control.query_one("SELECT $1::TEXT", &[&token]).map(|row| {
        let observed: String = row.get(0);
        assert_eq!(observed.as_bytes(), token.as_bytes());
    });
    for deleting in [false, true] {
        let input = if deleting {
            serde_json::json!({"object_ids":[{"collection":LIVE_COLLECTION,"key":"v3-history-boolean","version":token}]})
        } else {
            serde_json::json!({"objects":[{"collection":LIVE_COLLECTION,"key":"v3-history-boolean","version":token,"value":"{}"}]})
        }.to_string();
        let path = if deleting {
            "/v2/storage/delete"
        } else {
            "/v2/storage"
        };
        let before = storage_live_snapshot(control);
        let response = app.handle(&request(path, Some(credential), &input));
        match &native_condition {
            Ok(()) => {
                assert_eq!(response.status, 400);
                let message = if deleting {
                    "Storage delete rejected - not found, version check failed, or permission denied."
                } else {
                    "Storage write rejected - version check failed."
                };
                assert_eq!(
                    json(&response),
                    serde_json::json!({"code":3,"message":message})
                );
            }
            Err(error) => {
                let (status, expected) = native_input_gateway_failure(error, deleting);
                assert_eq!(response.status, status);
                assert_eq!(json(&response), expected);
            }
        }
        assert!(
            storage_live_snapshot(control) == before,
            "native NUL condition changed durable storage"
        );
    }
    let nul_payload = r#"{"bad":"\u0000"}"#;
    let native_payload = control
        .query_one("SELECT (($1::TEXT)::JSONB)::TEXT", &[&nul_payload])
        .map(|row| row.get::<_, String>(0));
    let before = storage_live_snapshot(control);
    let input = serde_json::json!({"objects":[{"collection":LIVE_COLLECTION,"key":"v3-native-nul","value":nul_payload}]}).to_string();
    let response = app.handle(&request("/v2/storage", Some(credential), &input));
    match &native_payload {
        Ok(native) => {
            assert_eq!(response.status, 200);
            assert_eq!(
                json(&response)["acks"][0]["version"],
                ContentVersion::from_value(nul_payload.as_bytes()).as_str()
            );
            assert!(
                persisted_storage_value(control, "v3-native-nul", LIVE_USER).as_deref()
                    == Some(native.as_bytes())
            );
        }
        Err(error) => {
            let (status, expected) = native_input_gateway_failure(error, false);
            assert_eq!(response.status, status);
            assert_eq!(json(&response), expected);
            assert!(
                storage_live_snapshot(control) == before,
                "rejected native JSONB input changed durable storage"
            );
        }
    }
    // This syntactically valid JSON object passes the outer protobuf string
    // and RawValue host parser. A separate native TEXT::JSONB expression
    // observes SQLSTATE 22P02; it is not itself a native Bind-format proof.
    // The authenticated App below exercises the production repository path.
    let surrogate_payload = r#"{"bad":"\ud800"}"#;
    assert_eq!(surrogate_payload.len(), 16);
    let native_surrogate = control
        .query_one("SELECT (($1::TEXT)::JSONB)::TEXT", &[&surrogate_payload])
        .expect_err("canonical storage fixture: native lone surrogate unexpectedly accepted");
    assert_eq!(
        native_surrogate.code().map(|code| code.code()),
        Some("22P02")
    );
    let before_surrogate = storage_live_snapshot(control);
    let input = serde_json::json!({"objects":[{
        "collection":LIVE_COLLECTION,"key":"v3-native-unpaired-surrogate","value":surrogate_payload
    }]})
    .to_string();
    let rejected = app.handle(&request("/v2/storage", Some(credential), &input));
    assert_eq!(rejected.status, 500);
    assert_eq!(
        json(&rejected),
        serde_json::json!({"code":13,"message":"Error writing storage objects."})
    );
    assert_eq!(
        storage_live_snapshot(control),
        before_surrogate,
        "native lone surrogate rejection changed a complete storage tuple"
    );
    // This App owns the original directly connected PgRepository. A following
    // authenticated repository read uses that same connection after rollback,
    // rather than acquiring or replacing a pooled lease for the health check.
    let health_input = serde_json::json!({"object_ids":[{
        "collection":LIVE_COLLECTION,"key":"v3-history-boolean","user_id":LIVE_USER_UUID
    }]})
    .to_string();
    let healthy = app.handle(&read_request(
        "/v2/storage",
        Some(credential),
        &health_input,
    ));
    assert_eq!(healthy.status, 200);
    let healthy = json(&healthy);
    let healthy_objects = healthy["objects"].as_array().unwrap();
    assert_eq!(healthy_objects.len(), 1);
    let (native_boolean, boolean_version) = native_history.get("v3-history-boolean").unwrap();
    assert_eq!(healthy_objects[0]["key"], "v3-history-boolean");
    assert_eq!(
        healthy_objects[0]["value"].as_str().unwrap().as_bytes(),
        native_boolean.as_bytes()
    );
    assert_eq!(
        healthy_objects[0]["version"].as_str().unwrap(),
        boolean_version.as_str()
    );
    assert_eq!(storage_live_snapshot(control), before_surrogate);
    println!(
        "\nstorage_jsonb_native_write_failure_executed profile={} cases=1 fields=15 sqlstate=22P02",
        profile.metadata_value()
    );
    println!("\nstorage_jsonb_v3_native_inputs profile={} condition_sql={} payload_sql={} compatibility_credit=false",
        profile.metadata_value(), if native_condition.is_ok() { "accepted" } else { "rejected" }, if native_payload.is_ok() { "accepted" } else { "rejected" });
    println!("\nstorage_jsonb_v3_live_executed profile={} history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3", profile.metadata_value());
}
