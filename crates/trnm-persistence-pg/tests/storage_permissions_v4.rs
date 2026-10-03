use std::collections::BTreeSet;
use std::env;
use std::panic::{catch_unwind, resume_unwind, AssertUnwindSafe};
use std::time::{SystemTime, UNIX_EPOCH};

use postgres::{Client, NoTls};
use trnm_contracts::{StableCode, UserId};
use trnm_persistence_pg::{
    ContentVersion, DatabaseProfile, IntegrityDigest, PgRepository, ReadPermission, StorageActor,
    StorageBatchOperation, StorageDeleteOperation, StorageObjectKey, StorageTimestamp,
    StorageWriteOperation, VersionCheck, WritePermission,
};

type Snapshot = (
    String,
    String,
    i16,
    i16,
    Vec<u8>,
    String,
    Option<Vec<u8>>,
    Option<Vec<u8>>,
    Option<Vec<u8>>,
    i64,
    Option<StorageTimestamp>,
    Option<StorageTimestamp>,
);

fn live_environment() -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE").as_deref() {
        Ok("1" | "true" | "TRUE" | "yes" | "YES") => true,
        Err(env::VarError::NotPresent) | Ok("0" | "false" | "FALSE" | "no" | "NO") => false,
        _ => panic!("v4 ACL fixture: invalid required-live configuration"),
    };
    let url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        _ if required => panic!("v4 ACL fixture: required database URL is absent"),
        _ => return None,
    };
    let profile = match env::var("TRNM_DATABASE_PROFILE").as_deref() {
        Ok("postgresql") => DatabaseProfile::PostgreSql,
        Ok("cockroachdb") => DatabaseProfile::CockroachDb,
        _ => panic!("v4 ACL fixture: database profile is required"),
    };
    Some((url, profile))
}

fn source_key(collection: &str, key: &str, owner: UserId) -> StorageObjectKey {
    StorageObjectKey::new_nakama(collection, key, owner).unwrap()
}

// These native rows qualify the stored ABI and its ACL behavior. Their synthetic
// nonzero manifest is deliberately not a verified Nakama export/custody receipt.
fn seed(
    control: &mut Client,
    key: &StorageObjectKey,
    permissions: (i16, i16),
    public_version: &str,
    native_input: &str,
) {
    let native: String = control
        .query_one("SELECT $1::TEXT::JSONB::TEXT", &[&native_input])
        .expect("v4 ACL fixture: native projection failed")
        .get(0);
    let digest = IntegrityDigest::from_value(native.as_bytes()).get();
    let manifest = [0x67_u8; 32];
    control
        .execute(
            "INSERT INTO public.trnm_storage_objects \
             (collection,object_key,user_id,value_jsonb,public_version,value_projection_digest, \
              value_origin,source_manifest_digest,value_bytes,version_digest,read_permission, \
              write_permission,updated_at_ms,create_time,update_time) \
             VALUES ($1,$2,$3,$4::TEXT::JSONB,$5,$6,'nakama-export-unknown-request',$7,NULL,NULL, \
                     $8,$9,10,TIMESTAMPTZ '1969-12-31 23:59:59.123456+00', \
                     TIMESTAMPTZ '2000-02-29 12:34:56.654321+00')",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
                &native_input,
                &public_version,
                &digest.as_bytes().as_slice(),
                &manifest.as_slice(),
                &permissions.0,
                &permissions.1,
            ],
        )
        .expect("v4 ACL fixture: native row insert failed");
}

fn snapshot(control: &mut Client, key: &StorageObjectKey) -> Snapshot {
    let row = control
        .query_one(
            "SELECT value_jsonb::TEXT,public_version::TEXT,read_permission,write_permission, \
             value_projection_digest,value_origin,source_manifest_digest,value_bytes,version_digest, \
             updated_at_ms,create_time,update_time FROM public.trnm_storage_objects \
             WHERE collection=$1 AND object_key=$2 AND user_id=$3",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .expect("v4 ACL fixture: snapshot failed");
    (
        row.get(0),
        row.get(1),
        row.get(2),
        row.get(3),
        row.get(4),
        row.get(5),
        row.get(6),
        row.get(7),
        row.get(8),
        row.get(9),
        row.get(10),
        row.get(11),
    )
}

fn write(
    key: &StorageObjectKey,
    value: &[u8],
    expected: VersionCheck,
    read: i16,
    acl: i16,
) -> StorageBatchOperation {
    StorageBatchOperation::Write(StorageWriteOperation {
        key: key.clone(),
        value: value.to_vec(),
        expected,
        read_permission: ReadPermission::from_stored(read).unwrap(),
        write_permission: WritePermission::from_stored(acl).unwrap(),
    })
}

#[test]
fn storage_v4_raw_acl_domains_keys_and_operation_predicates_are_native() {
    let Some((url, profile)) = live_environment() else {
        eprintln!("storage_v4_acl_live_skipped");
        return;
    };
    let mut repository = PgRepository::connect(&url, profile)
        .unwrap_or_else(|_| panic!("v4 ACL fixture: repository connection failed"));
    let identity = repository.verify_authoritative_schema().unwrap();
    assert_eq!(identity.schema_version, 4);
    assert_eq!(identity.storage_writer_epoch, 4);
    let mut control = Client::connect(&url, NoTls)
        .unwrap_or_else(|_| panic!("v4 ACL fixture: control connection failed"));
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let collection = format!("trnm-v4-acl-{nonce}");
    let cursor_collection = format!("{collection}-cursor");
    let mut owner_bytes = nonce.to_be_bytes();
    owner_bytes[0] = 0x75;
    let owner = UserId::new(owner_bytes);
    owner_bytes[0] = 0x76;
    let foreign = UserId::new(owner_bytes);
    let global = UserId::new([0; 16]);
    let actor = StorageActor::User(owner);
    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut keys = Vec::new();
        for read in [0_i16, 1, 2, 3, 32767] {
            for (label, user) in [("self", owner), ("other", foreign), ("global", global)] {
                let key = source_key(&collection, &format!("r{read}-{label}"), user);
                seed(
                    &mut control,
                    &key,
                    (read, 2),
                    "opaque-read",
                    "[3,true,null]",
                );
                keys.push((key, read));
            }
        }
        let expected = |predicate: &dyn Fn(&StorageObjectKey, i16) -> bool| {
            keys.iter()
                .filter(|(key, read)| predicate(key, *read))
                .map(|(key, _)| key.clone())
                .collect::<BTreeSet<_>>()
        };
        let batch = repository
            .read_storage_objects(
                actor,
                &keys.iter().map(|(key, _)| key.clone()).collect::<Vec<_>>(),
            )
            .unwrap();
        assert_eq!(
            batch
                .iter()
                .map(|object| object.key.clone())
                .collect::<BTreeSet<_>>(),
            expected(&|key, read| read == 2 || (read == 1 && key.user_id() == owner))
        );
        for scope in [None, Some(owner), Some(foreign), Some(global)] {
            let page = repository
                .list_storage_objects_nakama(actor, &collection, scope, None, 100)
                .unwrap();
            let expected_keys = expected(&|key, read| match scope {
                None => read >= 2,
                Some(user) if user == owner => key.user_id() == user && read >= 1,
                Some(user) => key.user_id() == user && read == 2,
            });
            assert_eq!(
                page.objects
                    .iter()
                    .map(|object| object.key.clone())
                    .collect::<BTreeSet<_>>(),
                expected_keys
            );
            assert!(page.next.is_none());
            for object in page.objects {
                let raw = keys.iter().find(|(key, _)| key == &object.key).unwrap().1;
                assert_eq!(object.read_permission.get(), raw);
                assert_eq!(object.write_permission.get(), 2);
                assert_eq!(object.version.as_str(), "opaque-read");
                assert!(object.collision_witness.is_none());
            }
        }
        let all = repository
            .read_storage_objects(
                StorageActor::Server,
                &keys.iter().map(|(key, _)| key.clone()).collect::<Vec<_>>(),
            )
            .unwrap();
        assert_eq!(all.len(), 15);
        assert!(all
            .iter()
            .any(|object| object.read_permission.get() == 32767));

        // The pinned public-all cursor comparison hard-codes read2 even though
        // source stored read3 rows are lawful. Repetition is observed, not fixed
        // by replacing the upstream offset with a stronger paginator.
        for key in ["a", "b"] {
            seed(
                &mut control,
                &source_key(&cursor_collection, key, owner),
                (3, 1),
                "cursor-opaque",
                "null",
            );
        }
        let first = repository
            .list_storage_objects_nakama(actor, &cursor_collection, None, None, 1)
            .unwrap();
        let position = first.next.as_ref().unwrap();
        assert_eq!(position.read, 3);
        let repeated = repository
            .list_storage_objects_nakama(actor, &cursor_collection, None, Some(position), 1)
            .unwrap();
        assert_eq!(repeated.objects[0].key, first.objects[0].key);
        assert_eq!(repeated.next, first.next);
        let own_first = repository
            .list_storage_objects_nakama(actor, &cursor_collection, Some(owner), None, 1)
            .unwrap();
        let own_next = repository
            .list_storage_objects_nakama(
                actor,
                &cursor_collection,
                Some(owner),
                own_first.next.as_ref(),
                1,
            )
            .unwrap();
        assert_eq!(own_next.objects[0].key.key(), "b");
        assert!(own_next.next.is_none());
        assert!(repository
            .read_storage_objects(actor, &[first.objects[0].key.clone()])
            .unwrap()
            .is_empty());

        let empty_global = source_key(&collection, "", global);
        let empty_source = source_key("", "", owner);
        let unicode_source = source_key("", &"界".repeat(128), owner);
        let control_source = source_key("", ".\n", owner);
        for key in [
            &empty_global,
            &empty_source,
            &unicode_source,
            &control_source,
        ] {
            seed(&mut control, key, (32767, 32767), "", "true");
        }
        let stored = repository
            .read_storage_objects(
                StorageActor::Server,
                &[
                    empty_global.clone(),
                    empty_source.clone(),
                    unicode_source.clone(),
                    control_source.clone(),
                ],
            )
            .unwrap();
        assert_eq!(stored.len(), 4);
        assert!(stored
            .iter()
            .all(|object| object.version.as_str().is_empty()
                && object.write_permission.get() == 32767));
        let typed = repository
            .list_storage_objects(StorageActor::Server, &collection, None, None, 100)
            .unwrap();
        assert!(typed.0.iter().any(|object| object.key == empty_global));
        let empty_page = repository
            .list_storage_objects_nakama(actor, "", Some(owner), None, 100)
            .unwrap();
        assert_eq!(empty_page.objects.len(), 3);
        assert!(empty_page
            .objects
            .iter()
            .any(|object| object.key.key().is_empty()));

        let incoming = b"{\"replacement\":true}";
        let generated = ContentVersion::from_value(incoming);
        for acl in [0_i16, 1, 2, 32767] {
            let key = source_key(&collection, &format!("write-{acl}"), owner);
            seed(
                &mut control,
                &key,
                (1, acl),
                generated.as_str(),
                "{\"preserved\":true}",
            );
            let before = snapshot(&mut control, &key);
            let outcome = repository.apply_storage_batch(
                actor,
                &[write(&key, incoming, VersionCheck::Any, 1, acl)],
                20,
            );
            if acl == 1 {
                assert_eq!(outcome.unwrap()[0].current_version, Some(generated));
            } else {
                assert_eq!(outcome.unwrap_err().code(), StableCode::PermissionDenied);
            }
            assert_eq!(snapshot(&mut control, &key), before);
            repository
                .apply_storage_batch(
                    StorageActor::Server,
                    &[write(&key, incoming, VersionCheck::Any, 1, acl)],
                    30,
                )
                .unwrap();
            assert_eq!(snapshot(&mut control, &key), before);
        }
        let changed_acl = source_key(&collection, "changed-raw-read", owner);
        seed(
            &mut control,
            &changed_acl,
            (3, 1),
            generated.as_str(),
            "{\"preserved\":true}",
        );
        let before = snapshot(&mut control, &changed_acl);
        repository
            .apply_storage_batch(
                actor,
                &[write(&changed_acl, incoming, VersionCheck::Any, 1, 1)],
                40,
            )
            .unwrap();
        let changed = snapshot(&mut control, &changed_acl);
        assert_eq!(changed.2, 1);
        assert_eq!(changed.5, "write-request-bytes");
        assert!(changed.6.is_none());
        assert_eq!(changed.7.as_deref(), Some(incoming.as_slice()));
        assert_eq!(changed.10, before.10);
        assert_eq!(changed.9, 40);
        repository
            .apply_storage_batch(
                actor,
                &[write(
                    &changed_acl,
                    incoming,
                    VersionCheck::Exact(generated.into()),
                    1,
                    1,
                )],
                50,
            )
            .unwrap();
        assert_eq!(snapshot(&mut control, &changed_acl).9, 50);
        let blocked = source_key(&collection, "write-2", owner);
        let changed_before = snapshot(&mut control, &changed_acl);
        let blocked_before = snapshot(&mut control, &blocked);
        let failure = repository
            .apply_storage_batch(
                actor,
                &[
                    write(&changed_acl, b"{\"changed\":2}", VersionCheck::Any, 1, 1),
                    write(&blocked, incoming, VersionCheck::Any, 1, 1),
                ],
                60,
            )
            .unwrap_err();
        assert_eq!(failure.code(), StableCode::PermissionDenied);
        assert_eq!(snapshot(&mut control, &changed_acl), changed_before);
        assert_eq!(snapshot(&mut control, &blocked), blocked_before);

        for acl in [0_i16, 1, 2, 32767] {
            for conditional in [false, true] {
                let key = source_key(&collection, &format!("delete-{acl}-{conditional}"), owner);
                seed(&mut control, &key, (3, acl), "*", "null");
                let before = snapshot(&mut control, &key);
                let operation = StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: key.clone(),
                    expected_version: conditional.then(|| "*".into()),
                });
                assert_eq!(
                    repository
                        .apply_storage_batch(
                            StorageActor::User(foreign),
                            std::slice::from_ref(&operation),
                            70
                        )
                        .unwrap_err()
                        .code(),
                    StableCode::PermissionDenied
                );
                assert_eq!(snapshot(&mut control, &key), before);
                let mismatch = StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: key.clone(),
                    expected_version: Some("different".into()),
                });
                assert_eq!(
                    repository
                        .apply_storage_batch(actor, &[mismatch], 70)
                        .unwrap_err()
                        .code(),
                    if acl == 0 {
                        StableCode::PermissionDenied
                    } else {
                        StableCode::FailedPrecondition
                    }
                );
                assert_eq!(snapshot(&mut control, &key), before);
                let outcome = repository.apply_storage_batch(actor, &[operation], 70);
                if acl == 0 {
                    assert_eq!(outcome.unwrap_err().code(), StableCode::PermissionDenied);
                    assert_eq!(snapshot(&mut control, &key), before);
                } else {
                    let receipt = outcome.unwrap();
                    assert_eq!(receipt[0].previous_version.as_ref().unwrap().as_str(), "*");
                    assert!(receipt[0].current_version.is_none());
                    assert!(control.query_opt("SELECT 1 FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3",
                        &[&key.collection(), &key.key(), &key.user_id().as_bytes().as_slice()]).unwrap().is_none());
                }
            }
        }
        assert_eq!(repository.verify_authoritative_schema().unwrap(), identity);
    }));
    control.execute(
        "DELETE FROM public.trnm_storage_objects WHERE collection=$1 OR collection=$2 OR (collection='' AND user_id=$3)",
        &[&collection, &cursor_collection, &owner.as_bytes().as_slice()],
    ).unwrap_or_else(|_| panic!("v4 ACL fixture: scoped cleanup failed"));
    if let Err(payload) = result {
        resume_unwind(payload);
    }
    let label = match profile {
        DatabaseProfile::PostgreSql => "postgresql",
        DatabaseProfile::CockroachDb => "cockroachdb",
    };
    println!("\nstorage_v4_acl_live_executed profile={label}");
}
