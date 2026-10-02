fn projected_object(
    key: &str,
    value: &[u8],
    version: &str,
    original_request: Option<&[u8]>,
) -> StorageObject {
    StorageObject {
        key: StorageObjectKey::new("profile", key, user()).unwrap(),
        value: value.to_vec(),
        version: PublicVersion::new(version).unwrap(),
        integrity_digest: IntegrityDigest::from_value(value),
        collision_witness: original_request
            .map(|request| CollisionWitness::from_request(request, value).unwrap()),
        read_permission: ReadPermission::OWNER,
        write_permission: WritePermission::OWNER,
    }
}

#[test]
fn storage_read_and_delete_accept_stored_identifiers_without_relaxing_new_writes() {
    for (collection, key) in [
        ("雪".repeat(128), "🔑".repeat(128)),
        (
            ".historic.collection".to_owned(),
            ".historic.key".to_owned(),
        ),
        (
            "historic\ncollection".to_owned(),
            "historic\tkey".to_owned(),
        ),
    ] {
        let fields: JsonObject = serde_json::from_str(
            &serde_json::json!({
                "collection": collection, "key": key, "user_id": uuid_string(user()),
            })
            .to_string(),
        )
        .unwrap();
        let read = decode_read_key(&fields).unwrap();
        assert_eq!(read.collection(), collection);
        assert_eq!(read.key(), key);
        assert_eq!(read.user_id(), user());
        let BatchOperation::Delete(delete) = decode_delete(&fields, user()).unwrap() else {
            panic!("expected delete operation")
        };
        assert_eq!(delete.key, read);
        let write: JsonObject = serde_json::from_str(
            &serde_json::json!({
                "collection": collection, "key": key, "value": "{}",
            })
            .to_string(),
        )
        .unwrap();
        assert_eq!(decode_write(&write, user()).err().unwrap().0, INVALID_KEYS);
    }
    for (collection, key) in [
        ("".to_owned(), "k".to_owned()),
        ("c".to_owned(), "".to_owned()),
        ("雪".repeat(129), "k".to_owned()),
        ("c".to_owned(), "🔑".repeat(129)),
    ] {
        let fields: JsonObject = serde_json::from_str(
            &serde_json::json!({
                "collection": collection, "key": key,
            })
            .to_string(),
        )
        .unwrap();
        assert_eq!(decode_read_key(&fields).unwrap_err().0, INVALID_KEYS);
        assert_eq!(decode_delete(&fields, user()).unwrap_err().0, INVALID_KEYS);
    }
    let supplied_owner: JsonObject = serde_json::from_str(
        r#"{"collection":".history","key":".k","user_id":"34343434-3434-3434-3434-343434343434","version":"*"}"#,
    ).unwrap();
    let BatchOperation::Delete(delete) = decode_delete(&supplied_owner, user()).unwrap() else {
        panic!("expected delete operation")
    };
    assert_eq!(delete.key.user_id(), user());
    assert_eq!(delete.expected_version.unwrap().as_str(), "*");
}

#[test]
fn storage_shared_object_and_ack_encoders_omit_empty_keys_and_preserve_raw_acl() {
    let owner = UserId::new([0; 16]);
    for (collection, key) in [("", ""), ("", "key"), ("collection", "")] {
        let stored_key = StorageObjectKey::new_nakama(collection, key, owner).unwrap();
        let mut object = projected_object("history", b"null", "", None);
        object.key = stored_key.clone();
        object.read_permission = ReadPermission::from_stored(32767).unwrap();
        object.write_permission = WritePermission::from_stored(32767).unwrap();
        // The shared encoder also serves list responses; batch-read ACL is
        // checked before encoding and does not grant read access to read=32767.
        let encoded: serde_json::Value = serde_json::from_str(
            &encode_storage_object(&object, &StorageTimes::default()).unwrap(),
        )
        .unwrap();
        if collection.is_empty() {
            assert!(encoded.get("collection").is_none());
        } else {
            assert_eq!(encoded["collection"], collection);
        }
        if key.is_empty() {
            assert!(encoded.get("key").is_none());
        } else {
            assert_eq!(encoded["key"], key);
        }
        assert_eq!(encoded["user_id"], "00000000-0000-0000-0000-000000000000");
        assert_eq!(encoded["permission_read"], 32767);
        assert_eq!(encoded["permission_write"], 32767);
        assert_eq!(encoded["value"], "null");
        assert!(encoded.get("version").is_none());
        assert!(!object.read_permission.allows_batch_read(true));

        let value = b"{}".to_vec();
        let version = ContentVersion::from_value(&value);
        let operation = BatchOperation::Write(WriteOperation {
            key: stored_key.clone(),
            value,
            expected: VersionCheck::Any,
            read_permission: ReadPermission::OWNER,
            write_permission: WritePermission::OWNER,
        });
        let receipt = stored_receipt(CoreReceipt {
            key: stored_key,
            previous_version: None,
            current_version: Some(version),
        });
        let response = write_response(&[operation], &[receipt]);
        assert_eq!(response.status, 200);
        let ack = &body(&response)["acks"][0];
        if collection.is_empty() {
            assert!(ack.get("collection").is_none());
        } else {
            assert_eq!(ack["collection"], collection);
        }
        if key.is_empty() {
            assert!(ack.get("key").is_none());
        } else {
            assert_eq!(ack["key"], key);
        }
        assert_eq!(ack["user_id"], "00000000-0000-0000-0000-000000000000");
        assert_eq!(ack["version"], version.as_str());
    }
}

#[test]
fn storage_read_preserves_native_projection_and_independent_public_tokens() {
    let request = b" { \"z\":2, \"a\":1, \"a\":3 }\n";
    let native = b"{\"a\": 3, \"z\": 2}";
    let generated = ContentVersion::from_value(request);
    assert_ne!(generated, ContentVersion::from_value(native));
    let known = projected_object("known", native, generated.as_str(), Some(request));
    let known_json: serde_json::Value =
        serde_json::from_str(&encode_storage_object(&known, &StorageTimes::default()).unwrap())
            .unwrap();
    assert_eq!(known_json["value"].as_str().unwrap().as_bytes(), native);
    assert_eq!(known_json["version"], generated.as_str());
    assert!(known_json.get("create_time").is_none());
    assert!(known_json.get("update_time").is_none());

    for (native, version) in [
        ("null", "".to_owned()),
        ("[1, 1.0, false, null]", "*".to_owned()),
        ("\"native string\"", "UPPERCASE-NONHEX".to_owned()),
        ("1.000", "é".repeat(32)),
        ("true", "trailing ".to_owned()),
    ] {
        let object = projected_object("history", native.as_bytes(), &version, None);
        let encoded: serde_json::Value = serde_json::from_str(
            &encode_storage_object(&object, &StorageTimes::default()).unwrap(),
        )
        .unwrap();
        assert_eq!(
            encoded["value"].as_str().unwrap().as_bytes(),
            native.as_bytes()
        );
        if version.is_empty() {
            assert!(encoded.get("version").is_none());
        } else {
            assert_eq!(encoded["version"], version);
        }
        assert!(object.collision_witness.is_none());
    }
}

#[test]
fn storage_native_projection_validates_known_witness_without_inventing_history() {
    let request = b" { \"n\": 1 }\n";
    let native = b"{\"n\": 1}";
    let generated = ContentVersion::from_value(request);
    let known = projected_object("k", native, generated.as_str(), Some(request));
    assert!(encode_storage_object(&known, &StorageTimes::default()).is_ok());
    let mut wrong_binding = known.clone();
    wrong_binding.collision_witness =
        Some(CollisionWitness::from_request(request, b"{\"n\": 2}").unwrap());
    let mut wrong_version = known.clone();
    wrong_version.version = PublicVersion::new("historical-token").unwrap();
    let mut wrong_digest = known.clone();
    wrong_digest.integrity_digest = IntegrityDigest::from_value(b"other");
    for wrong in [wrong_binding, wrong_version, wrong_digest] {
        assert_eq!(
            encode_storage_object(&wrong, &StorageTimes::default()),
            Err(StorageEncodingError::DataLoss),
        );
    }
    let mut unknown = known;
    unknown.collision_witness = None;
    unknown.version = PublicVersion::new("historical-token").unwrap();
    assert!(encode_storage_object(&unknown, &StorageTimes::default()).is_ok());
    assert!(unknown.collision_witness.is_none());
}

#[test]
fn storage_ack_uses_request_digest_and_keeps_empty_previous_version_present() {
    let raw = " { \"n\": 1 }\n";
    let native = b"{\"n\": 1}";
    let generated = ContentVersion::from_value(raw.as_bytes());
    assert_ne!(generated, ContentVersion::from_value(native));
    let operations = decode_operations(
        &request("/v2/storage", &write("k", raw, "")),
        user(),
        OperationKind::Write,
    )
    .unwrap();
    let mut receipt = stored_receipt(CoreReceipt {
        key: operations[0].key().clone(),
        previous_version: Some(PublicVersion::new("").unwrap()),
        current_version: Some(generated),
    });
    receipt.times.create = None;
    let response = write_response(&operations, std::slice::from_ref(&receipt));
    assert_eq!(response.status, 200);
    assert_eq!(body(&response)["acks"][0]["version"], generated.as_str());
    assert!(body(&response)["acks"][0].get("create_time").is_none());
    let insert = decode_operations(
        &request("/v2/storage", &write("k", raw, "*")),
        user(),
        OperationKind::Write,
    )
    .unwrap();
    assert_eq!(
        write_response(&insert, std::slice::from_ref(&receipt)).status,
        500
    );
    receipt.receipt.current_version = Some(ContentVersion::from_value(native));
    let rejected = write_response(&operations, std::slice::from_ref(&receipt));
    assert_eq!(rejected.status, 500);
    assert!(body(&rejected).get("acks").is_none());
    let operation = BatchOperation::Delete(DeleteOperation {
        key: operations[0].key().clone(),
        expected_version: None,
    });
    receipt.receipt.current_version = None;
    assert_eq!(
        delete_response(
            std::slice::from_ref(&operation),
            std::slice::from_ref(&receipt)
        )
        .status,
        200
    );
    receipt.receipt.previous_version = None;
    assert_eq!(delete_response(&[operation], &[receipt]).status, 500);
}

#[test]
fn storage_native_projection_budget_is_separate_from_request_value_budget() {
    let native = format!("\"{}\"", "x".repeat(MAX_REQUEST_VALUE_BYTES + 1));
    let object = projected_object("k", native.as_bytes(), "native-history", None);
    let response = read_response(
        std::slice::from_ref(&object.key),
        &[stored_object(object.clone())],
        user(),
    );
    assert_eq!(response.status, 200);
    assert_eq!(body(&response)["objects"][0]["value"], native);
    let over_budget = format!("\"{}\"", "x".repeat(16 * 1024 * 1024));
    let object = projected_object("k", over_budget.as_bytes(), "native-history", None);
    assert_eq!(
        encode_storage_object(&object, &StorageTimes::default()),
        Err(StorageEncodingError::ResourceExhausted)
    );
}

#[test]
fn storage_response_budget_counts_escaped_bytes_and_rejects_the_whole_batch() {
    // This legal native JSON string fits the 16 MiB projection budget. Its
    // quotes/backslashes double inside the API's JSON string envelope.
    let native = format!("\"{}\"", "\\\"".repeat(4 * 1024 * 1024));
    let first = projected_object("a", native.as_bytes(), "history-a", None);
    let second = projected_object("b", native.as_bytes(), "history-b", None);
    let single = read_response(
        std::slice::from_ref(&first.key),
        &[stored_object(first.clone())],
        user(),
    );
    assert_eq!(single.status, 200);
    assert!(single.body.len() > native.len());
    assert!(single.body.len() <= MAX_ENCODED_STORAGE_RESPONSE_BYTES);
    let keys = [first.key.clone(), second.key.clone()];
    let response = read_response(
        &keys,
        &[stored_object(first), stored_object(second)],
        user(),
    );
    assert_eq!(response.status, 429);
    assert_eq!(
        body(&response),
        serde_json::json!({"code":8,"message":"Error reading storage objects."})
    );
    assert!(body(&response).get("objects").is_none());
}

#[test]
fn storage_repository_resource_errors_preserve_code_eight_and_redact_reasons() {
    let mut repository = TestRepository {
        failure: Some(DomainError::new(
            StableCode::ResourceExhausted,
            "private native row size",
            RetryClass::Never,
        )),
        ..TestRepository::default()
    };
    for request in [
        request("/v2/storage", &write("k", "{}", "")),
        request(
            "/v2/storage/delete",
            r#"{"object_ids":[{"collection":"profile","key":"k"}]}"#,
        ),
        read_request(r#"{"object_ids":[{"collection":"profile","key":"k"}]}"#),
    ] {
        let response = handle(&mut repository, &request, user());
        assert_eq!(response.status, 429);
        assert_eq!(body(&response)["code"], 8);
        assert!(!String::from_utf8(response.body)
            .unwrap()
            .contains("private"));
    }
}
