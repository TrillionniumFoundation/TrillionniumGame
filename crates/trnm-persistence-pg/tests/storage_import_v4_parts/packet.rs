fn source_storage_ddl(directory: &Path) -> String {
    let bytes = fs::read_to_string(directory.join("initial-schema.sql")).unwrap();
    let start = bytes.find("CREATE TABLE IF NOT EXISTS storage (").unwrap();
    let remaining = &bytes[start..];
    let mut end = 0;
    // The actual pinned storage table plus its the first two original indexes from the pinned initial SQL, without
    // copying/reconstructing any source domain/default/check expression.
    for _ in 0..3 {
        end += remaining[end..].find(';').unwrap() + 1;
    }
    remaining[..end].to_owned()
}

fn seed_source(source: &Database, upstream: &Path) {
    let mut client = source.client();
    client
        .batch_execute("CREATE TABLE public.users(id UUID PRIMARY KEY)")
        .unwrap();
    client.batch_execute(&source_storage_ddl(upstream)).unwrap();
    for uuid in [ZERO_UUID, OWNER_UUID, OTHER_UUID] {
        client
            .execute(
                "INSERT INTO public.users(id) VALUES($1::TEXT::UUID)",
                &[&uuid],
            )
            .unwrap();
    }
    let long_key = "界".repeat(128);
    let long_version = "界".repeat(32);
    let rows = [
        ("", "", ZERO_UUID, "null", "", 0_i16, 0_i16),
        ("", "a", OWNER_UUID, "\"source string\"", "UPPERCASE", 1, 1),
        ("all", "", ZERO_UUID, "true", "*", 2, 2),
        (
            "all",
            long_key.as_str(),
            OWNER_UUID,
            "[null,true,1,{\"x\":2}]",
            long_version.as_str(),
            3,
            3,
        ),
        (
            "all", "number", OTHER_UUID, "1.20e3", "not-hex", 32767, 32767,
        ),
        (
            "objects",
            "dups",
            OWNER_UUID,
            " {\"b\":1e2, \"a\":1, \"a\":2} ",
            "legacytoken",
            2,
            1,
        ),
        (
            "objects",
            "escape\nkey",
            OTHER_UUID,
            "\"escape \\u754c \\n\"",
            "opaque\tversion",
            0,
            32767,
        ),
        ("集合", "tail", ZERO_UUID, "{}", "opaque", 32767, 2),
    ];
    for (index, (collection, key, owner, value, version, read, write)) in
        rows.into_iter().enumerate()
    {
        // A reversed historical chronology is valid source data; no synthesized
        // now(), chronology check, or millisecond truncation is permissible.
        let (create, update) = if index == 7 {
            (
                StorageTimestamp::new(951_827_696, 654_321_000).unwrap(),
                StorageTimestamp::new(-1, 123_456_000).unwrap(),
            )
        } else {
            (
                StorageTimestamp::new(-1, 123_456_000).unwrap(),
                StorageTimestamp::new(951_827_696, 654_321_000).unwrap(),
            )
        };
        client.execute("INSERT INTO public.storage(collection,key,user_id,value,version,read,write,create_time,update_time) VALUES($1,$2,$3::TEXT::UUID,$4::TEXT::JSONB,$5,$6,$7,$8,$9)", &[&collection,&key,&owner,&value,&version,&read,&write,&create,&update]).unwrap();
    }
}

const ZERO_UUID: &str = "00000000-0000-0000-0000-000000000000";
const OWNER_UUID: &str = "11111111-1111-1111-1111-111111111111";
const OTHER_UUID: &str = "22222222-2222-2222-2222-222222222222";

fn timestamp_json(value: StorageTimestamp) -> Value {
    json!({"seconds":value.seconds,"nanos":value.nanos})
}

fn source_rows(client: &mut Client) -> Vec<Value> {
    client.query("SELECT collection,key,user_id::TEXT,value::TEXT,version,read,write,create_time,update_time FROM public.storage ORDER BY collection,key,user_id", &[]).unwrap().iter().map(|row| json!({
        "collection":row.get::<_,String>(0),"key":row.get::<_,String>(1),"user_id":row.get::<_,String>(2),
        "native_text":row.get::<_,String>(3),"public_version":row.get::<_,String>(4),
        "read":row.get::<_,i16>(5),"write":row.get::<_,i16>(6),
        "create_time":timestamp_json(row.get(7)),"update_time":timestamp_json(row.get(8))
    })).collect()
}

fn target_rows(client: &mut Client) -> Vec<Value> {
    client.query("SELECT collection,object_key,user_id,value_jsonb::TEXT,public_version::TEXT,read_permission,write_permission,create_time,update_time,value_projection_digest,value_origin,source_manifest_digest,value_bytes,version_digest,updated_at_ms FROM public.trnm_storage_objects ORDER BY collection,object_key,user_id", &[]).unwrap().iter().map(|row| {
        let owner: Vec<u8> = row.get(2);
        let user_id = uuid_string(&owner);
        let projection: Vec<u8> = row.get(9);
        let manifest: Option<Vec<u8>> = row.get(11);
        json!({
            "collection":row.get::<_,String>(0),"key":row.get::<_,String>(1),"user_id":user_id,
            "native_text":row.get::<_,String>(3),"public_version":row.get::<_,String>(4),
            "read":row.get::<_,i16>(5),"write":row.get::<_,i16>(6),
            "create_time":timestamp_json(row.get(7)),"update_time":timestamp_json(row.get(8)),
            "projection_sha256":hex_bytes(&projection),"value_origin":row.get::<_,String>(10),
            "source_manifest_sha256":manifest.as_ref().map(|bytes|hex_bytes(bytes)),
            "raw_value_is_null":row.get::<_,Option<Vec<u8>>>(12).is_none(),
            "raw_digest_is_null":row.get::<_,Option<Vec<u8>>>(13).is_none(),
            "updated_at_ms":row.get::<_,i64>(14)
        })
    }).collect()
}

fn hex_bytes(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut result, "{byte:02x}").expect("String write");
    }
    result
}

fn uuid_string(bytes: &[u8]) -> String {
    assert_eq!(bytes.len(), 16);
    let hex = hex_bytes(bytes);
    format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..]
    )
}

fn key_from_row(row: &Value) -> StorageObjectKey {
    let owner = match row["user_id"].as_str().unwrap() {
        ZERO_UUID => UserId::new([0; 16]),
        OWNER_UUID => UserId::new([0x11; 16]),
        OTHER_UUID => UserId::new([0x22; 16]),
        _ => panic!("unexpected synthetic source owner"),
    };
    StorageObjectKey::new_nakama(
        row["collection"].as_str().unwrap(),
        row["key"].as_str().unwrap(),
        owner,
    )
    .unwrap()
}

fn single_row_export_preserves_all_zero_continuation(
    environment: &LiveEnvironment,
    source: &Database,
    files: &PacketDirectory,
) {
    let before = source_rows(&mut source.client());
    assert_eq!(before[0]["collection"], "");
    assert_eq!(before[0]["key"], "");
    assert_eq!(before[0]["user_id"], ZERO_UUID);
    // A page of one makes the literal minimum tuple the first continuation.
    // This extra actual export is outside the retained main evidence root.
    let path = export_with_page_rows(environment, source, files.path.join("one-row-pages"), 1);
    let packet = verify_storage_export(&path, &independent_custody(environment, &path)).unwrap();
    assert_eq!(packet.summary().rows, SOURCE_ROWS);
    assert_eq!(packet.summary().pages, SOURCE_ROWS);
    let manifest: Value =
        serde_json::from_slice(&fs::read(path.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["page_rows"], 1);
    let records: Vec<Value> = fs::read_to_string(path.join("rows.ndjson"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(records.len(), SOURCE_ROWS);
    for (ordinal, (record, native)) in records.iter().zip(&before).enumerate() {
        assert_eq!(record["ordinal"], ordinal);
        for field in [
            "collection",
            "key",
            "user_id",
            "public_version",
            "read",
            "write",
            "create_time",
            "update_time",
        ] {
            assert_eq!(
                record[field], native[field],
                "one-row native export field {field} changed"
            );
        }
        let raw = fs::read(path.join(record["value_path"].as_str().unwrap())).unwrap();
        assert_eq!(raw, native["native_text"].as_str().unwrap().as_bytes());
        assert_eq!(record["value_sha256"], digest_hex(&raw));
        assert_eq!(record["value_bytes"], raw.len());
    }
    assert_eq!(
        source_rows(&mut source.client()),
        before,
        "read-only exporter changed source rows"
    );
}

fn copy_packet(source: &Path, destination: &Path) {
    fs::create_dir(destination).unwrap();
    for entry in fs::read_dir(source).unwrap() {
        let entry = entry.unwrap();
        let target = destination.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_packet(&entry.path(), &target);
        } else {
            assert!(entry.file_type().unwrap().is_file());
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn reanchor_packet(environment: &LiveEnvironment, path: &Path) -> VerifiedStorageExport {
    // Deliberately hostile synthetic packet: update every affected hash, then
    // provide a new independent custody anchor. It receives no export credit.
    let mut manifest: Value =
        serde_json::from_slice(&fs::read(path.join("manifest.json")).unwrap()).unwrap();
    for member in manifest["members"].as_array_mut().unwrap() {
        let bytes = fs::read(path.join(member["path"].as_str().unwrap())).unwrap();
        member["bytes"] = json!(bytes.len());
        member["sha256"] = json!(digest_hex(&bytes));
    }
    write_json(path.join("manifest.json"), &manifest);
    let mut receipt: Value =
        serde_json::from_slice(&fs::read(path.join("source-receipt.json")).unwrap()).unwrap();
    receipt["manifest_sha256"] = json!(digest_hex(&fs::read(path.join("manifest.json")).unwrap()));
    write_json(path.join("source-receipt.json"), &receipt);
    verify_storage_export(path, &independent_custody(environment, path)).unwrap()
}

fn change_packet_value(path: &Path, ordinal: usize, text: &str) {
    let mut rows: Vec<Value> = fs::read_to_string(path.join("rows.ndjson"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    let row = &mut rows[ordinal];
    fs::write(path.join(row["value_path"].as_str().unwrap()), text).unwrap();
    row["value_sha256"] = json!(digest_hex(text.as_bytes()));
    row["value_bytes"] = json!(text.len());
    let mut bytes = Vec::new();
    for row in rows {
        bytes.extend_from_slice(&serde_json::to_vec(&row).unwrap());
        bytes.push(b'\n');
    }
    fs::write(path.join("rows.ndjson"), bytes).unwrap();
}

fn packet_admission_negatives(
    environment: &LiveEnvironment,
    files: &PacketDirectory,
    original: &Path,
) {
    let wrong = StorageImportCustody::new(
        &digest_hex(b"wrong independent manifest"),
        &digest_hex(&fs::read(original.join("source-receipt.json")).unwrap()),
        environment.producer_commit.clone(),
        environment.producer_tree.clone(),
        &digest_hex(
            &fs::read(
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("src/storage_import_parts/exporter.rs"),
            )
            .unwrap(),
        ),
        &digest_hex(&fs::read("/proc/self/exe").unwrap()),
        fixture_execution_id(environment),
    )
    .unwrap();
    assert!(verify_storage_export(original, &wrong).is_err());
    let extra = files.path.join("extra");
    copy_packet(original, &extra);
    let custody = independent_custody(environment, &extra);
    fs::write(extra.join("unlisted.bin"), b"closed-world packet intrusion").unwrap();
    assert!(verify_storage_export(&extra, &custody).is_err());
    #[cfg(unix)]
    {
        let linked = files.path.join("linked");
        copy_packet(original, &linked);
        let custody = independent_custody(environment, &linked);
        let value = linked.join("values/00000000.json");
        fs::remove_file(&value).unwrap();
        std::os::unix::fs::symlink(original.join("values/00000000.json"), &value).unwrap();
        assert!(verify_storage_export(&linked, &custody).is_err());
    }
}

fn native_source_collation_is_not_silently_normalized(environment: &LiveEnvironment) {
    let source = Database::new(environment, "collated");
    let mut client = source.client();
    client
        .batch_execute("CREATE TABLE public.users(id UUID PRIMARY KEY)")
        .unwrap();
    let collation = if environment.profile == DatabaseProfile::PostgreSql {
        "C"
    } else {
        "en"
    };
    let original = source_storage_ddl(&environment.upstream_directory);
    assert_eq!(
        original.matches("collection  VARCHAR(128)").count(),
        1,
        "collation fixture must change one actual pinned source column"
    );
    let ddl = original.replace(
        "collection  VARCHAR(128)",
        &format!("collection VARCHAR(128) COLLATE \"{collation}\""),
    );
    client
        .batch_execute(&ddl)
        .expect("native source collation fixture DDL");
    let files = PacketDirectory::new();
    let source_file =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/storage_import_parts/exporter.rs");
    let options = StorageExportOptions {
        output_directory: files.path.join("collated"),
        page_rows: PAGE_ROWS,
        execution_class: "native-source-ddl-fixture".to_owned(),
        producer_commit: environment.producer_commit.clone(),
        producer_tree: environment.producer_tree.clone(),
        producer_source_sha256: digest_hex(&fs::read(&source_file).unwrap()),
        producer_source_file: source_file,
        producer_binary_sha256: digest_hex(&fs::read("/proc/self/exe").unwrap()),
        execution_id: fixture_execution_id(environment),
        upstream_directory: environment.upstream_directory.clone(),
    };
    let error = source
        .repository()
        .export_storage_snapshot(&options)
        .unwrap_err();
    assert_eq!(error.code(), StableCode::InvalidArgument);
    assert!(!options
        .output_directory
        .join("source-receipt.json")
        .exists());
}
