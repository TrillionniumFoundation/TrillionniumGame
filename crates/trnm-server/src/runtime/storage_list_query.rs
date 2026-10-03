//! Independent bounded projection of the pinned grpc-gateway list query.
//! Gateway malformed-input wording, alias collision order and raw non-UTF8
//! strings remain unqualified; known fields are parsed before repository I/O.

use trnm_contracts::UserId;

use super::storage_api::parse_uuid;

const MAX_TARGET_BYTES: usize = 32 * 1024;
const MAX_COLLECTION_BYTES: usize = 4096;
const INVALID_QUERY: &str = "Invalid storage list query.";
const INVALID_LIMIT: &str = "Invalid limit - limit must be between 1 and 100.";
const INVALID_USER: &str = "Invalid user ID - make sure user ID is a valid UUID.";

#[derive(Debug)]
pub(super) struct ListQuery {
    pub collection: String,
    pub owner: Option<UserId>,
    pub limit: usize,
    pub cursor: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct QueryError(pub u16, pub u8, pub &'static str);

/// Match only the URL's bounded routing shape. The legacy gateway dispatches
/// against net/http URL.Path, which has already been percent-unescaped once.
pub(super) fn routing_path(target: &str) -> Option<String> {
    if target.len() > MAX_TARGET_BYTES {
        return None;
    }
    unescape(target.split('?').next().unwrap_or(""), false).ok()
}

pub(super) fn parse(target: &str) -> Result<ListQuery, QueryError> {
    if target.len() > MAX_TARGET_BYTES {
        return Err(invalid(INVALID_QUERY));
    }
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    // The pinned gateway uses legacy unescaping: net/http URL.Path is decoded
    // before path components are matched. '+' is literal in a path segment.
    let path = unescape(path, false)?;
    let suffix = path.strip_prefix("/v2/storage/").ok_or(not_found())?;
    let parts = suffix.split('/').collect::<Vec<_>>();
    if !(1..=2).contains(&parts.len()) {
        return Err(not_found());
    }
    let collection = parts[0].to_owned();
    if collection.len() > MAX_COLLECTION_BYTES {
        return Err(invalid(INVALID_QUERY));
    }
    let owner_bound = parts.len() == 2;
    let mut owner = owner_bound.then(|| parts[1].to_owned());
    let mut limit: Option<String> = None;
    let mut limit_present = false;
    let mut cursor: Option<String> = None;
    for pair in query.split('&').filter(|pair| !pair.is_empty()) {
        // Go ParseQuery rejects an unescaped semicolon anywhere in a pair.
        if pair.contains(';') {
            return Err(invalid(INVALID_QUERY));
        }
        let (name, value) = pair.split_once('=').unwrap_or((pair, ""));
        let mut name = unescape(name, true)?;
        let value = unescape(value, true)?;
        // The gateway map notation adds its bracket value to the value list.
        // The upstream anchored regexp uses '.', which excludes LF even in
        // percent-decoded query names. Such names remain ordinary unknowns.
        let bracketed = if !name.contains('\n') && name.ends_with(']') {
            if let Some(index) = name.rfind('[') {
                name.truncate(index);
                true
            } else {
                false
            }
        } else {
            false
        };
        let first = name.split('.').next().unwrap_or("");
        if first == "collection" || (owner_bound && matches!(first, "user_id" | "userId")) {
            continue;
        }
        let slot = match name.as_str() {
            "limit" | "limit.value" => &mut limit,
            "cursor" => &mut cursor,
            "user_id" | "userId" => &mut owner,
            _ if matches!(first, "user_id" | "userId" | "cursor") => {
                return Err(invalid(INVALID_QUERY));
            }
            _ if first == "limit" => {
                // An unknown nested field still allocates the Int32Value in
                // the gateway. Its zero value fails the API's limit range.
                if name.starts_with("limit.value.") {
                    return Err(invalid(INVALID_QUERY));
                }
                limit_present = true;
                continue;
            }
            _ => continue,
        };
        // Distinct aliases collide deterministically here. Go iterates a map;
        // its last assignment order for distinct aliases is a registered gap.
        if bracketed || slot.replace(value).is_some() {
            return Err(invalid(INVALID_QUERY));
        }
    }
    let limit = match limit {
        None if limit_present => return Err(invalid(INVALID_LIMIT)),
        None => 1,
        Some(value) => {
            // Rust and Go accept +/-decimal, but Go base10 rejects separators,
            // whitespace, decimal fractions, exponents and non-ASCII digits.
            let digits = value.strip_prefix(['+', '-']).unwrap_or(&value);
            if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err(invalid(INVALID_LIMIT));
            }
            match value.parse::<i32>() {
                Ok(value @ 1..=100) => value as usize,
                _ => return Err(invalid(INVALID_LIMIT)),
            }
        }
    };
    let owner = match owner.filter(|owner| !owner.is_empty()) {
        None => None,
        Some(owner) => Some(parse_uuid(&owner).ok_or(invalid(INVALID_USER))?),
    };
    Ok(ListQuery {
        collection,
        owner,
        limit,
        cursor: cursor.unwrap_or_default(),
    })
}

fn invalid(message: &'static str) -> QueryError {
    QueryError(400, 3, message)
}

fn not_found() -> QueryError {
    QueryError(404, 5, "Requested resource was not found.")
}

fn unescape(input: &str, query: bool) -> Result<String, QueryError> {
    let bytes = input.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'%' => {
                let high = bytes.get(index + 1).and_then(|byte| hex(*byte));
                let low = bytes.get(index + 2).and_then(|byte| hex(*byte));
                result.push(
                    (high.ok_or(invalid(INVALID_QUERY))? << 4)
                        | low.ok_or(invalid(INVALID_QUERY))?,
                );
                index += 3;
            }
            b'+' if query => {
                result.push(b' ');
                index += 1;
            }
            byte => {
                result.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8(result).map_err(|_| invalid(INVALID_QUERY))
}

fn hex(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{parse, routing_path, INVALID_LIMIT, INVALID_QUERY, INVALID_USER};
    use trnm_contracts::UserId;

    // Source-extracted Go query oracle; not the full Nakama HTTP server.
    // grpc-gateway commit ba9b55c1c15c84633be18c45463e123f31a5e999
    // https://github.com/grpc-ecosystem/grpc-gateway/blob/ba9b55c1c15c84633be18c45463e123f31a5e999/runtime/query.go
    // Source SHA256 cdb3c973f81862d47dacf2056cf0ac58c66c98f9c0b609efc9bc6f10f69510c6.
    // Go 1.26.5, common 1.47.0 and protobuf 1.36.11; logging suppressed,
    // never-used Bytes linker stub. Routing shape is a mux/pattern projection.
    const QUERY_ORACLE: &str = r###"[{"target":"/v2/storage/a?","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit=001","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit=%2B1","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit=+1","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"parsing field \"limit\": strconv.ParseInt: parsing \" 1\": invalid syntax","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit=1.0","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"parsing field \"limit\": strconv.ParseInt: parsing \"1.0\": invalid syntax","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit=1e0","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"parsing field \"limit\": strconv.ParseInt: parsing \"1e0\": invalid syntax","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit.value=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.unknown=9","observed":{"accepted":true,"api_limit_valid":false,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":0,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.unknown=9&limit.value=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.value=2&limit.unknown=9","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.unknown=9&limit=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit=2&limit.unknown=9","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.unknown.nested=9&limit.value=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.unknown[x]=9&limit.value=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.unknown=9&limit.unknown=8&limit.value=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit.value.foo=9","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"invalid path: \"value\" is not a message","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit=1&limit=2","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"too many values for field \"limit\": 1, 2","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit=1&limit.value=2","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":2,"owner":"","raw_path":""},"residual":"distinct_known_aliases"},{"target":"/v2/storage/a?.limit.unknown=9","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?limit[%0A]=1","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?cursor[%0A]=a","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?user_id[%0A]=bad","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?collection.foo=x","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?user_id.foo=x","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"invalid path: \"user_id\" is not a message","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?cursor[x]=a","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"too many values for field \"cursor\": x, a","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit[x]=2","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"too many values for field \"limit\": x, 2","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?limit..value=9","observed":{"accepted":true,"api_limit_valid":false,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":0,"owner":"","raw_path":""}},{"target":"/v2/storage/a?cursor.x=y","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"invalid path: \"cursor\" is not a message","raw_path":"","stage":"query"}},{"target":"/v2/storage/a?cursor=ok%0D%0A","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"ok\r\n","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a?unknown=x;y","observed":{"accepted":false,"decoded_path":"/v2/storage/a","error":"invalid semicolon separator in query","raw_path":"","stage":"form"}},{"target":"/v2/storage/a?unknown=x%3By","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""}},{"target":"/%76%32/%73torage/a","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":"/%76%32/%73torage/a"}},{"target":"/v2/storage/a+b","observed":{"accepted":true,"api_limit_valid":true,"collection":"a+b","cursor":"","decoded_path":"/v2/storage/a+b","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a%2Fb","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a/b","limit":1,"owner":"b","raw_path":"/v2/storage/a%2Fb"}},{"target":"/v2/storage/a%252Fb","observed":{"accepted":true,"api_limit_valid":true,"collection":"a%2Fb","cursor":"","decoded_path":"/v2/storage/a%2Fb","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a/","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a/","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/","observed":{"accepted":true,"api_limit_valid":true,"collection":"","cursor":"","decoded_path":"/v2/storage/","limit":1,"owner":"","raw_path":""}},{"target":"/v2/storage/a/00000000-0000-0000-0000-000000000000?userId=bad&collection=bad","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a/00000000-0000-0000-0000-000000000000","limit":1,"owner":"00000000-0000-0000-0000-000000000000","raw_path":""}},{"target":"/v2/storage/a?user_id=&userId=","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""},"residual":"distinct_known_aliases"},{"target":"/v2/storage/a?unknown=%FF","observed":{"accepted":true,"api_limit_valid":true,"collection":"a","cursor":"","decoded_path":"/v2/storage/a","limit":1,"owner":"","raw_path":""},"residual":"raw_non_utf8_query_string"}]"###;

    #[test]
    fn list_query_defaults_wrappers_aliases_and_path_binding() {
        let query = parse("/v2/storage/inventory?ignored=x&collection=wrong").unwrap();
        assert_eq!(query.collection, "inventory");
        assert_eq!(query.owner, None);
        assert_eq!(query.limit, 1);
        assert!(query.cursor.is_empty());
        let owner = "00000000-0000-0000-0000-000000000000";
        for limit in ["001", "%2B1", "100"] {
            let query = parse(&format!(
                "/v2/storage/a/{owner}?userId=bad&limit.value={limit}"
            ))
            .unwrap();
            assert_eq!(query.owner, Some(UserId::new([0; 16])));
        }
        let query = parse(&format!("/v2/storage/a?userId={owner}")).unwrap();
        assert_eq!(query.owner, Some(UserId::new([0; 16])));
        assert_eq!(parse("/v2/storage/a/").unwrap().owner, None);
    }

    #[test]
    fn list_query_path_and_query_escaping_follow_legacy_gateway() {
        let query = parse("/v2/storage/a+%E4%B8%AD?cursor=x%2By_z%0D%0A&unknown=a%3Bb").unwrap();
        assert_eq!(query.collection, "a+中");
        assert_eq!(query.cursor, "x+y_z\r\n");
        assert_eq!(parse("/v2/storage/a%2Fb").unwrap_err().2, INVALID_USER);
        for target in [
            "/v2/storage/a?x=%",
            "/v2/storage/a?x=a;b",
            "/v2/storage/%GG",
            "/v2/storage/a?x=%FF",
        ] {
            assert_eq!(parse(target).unwrap_err().0, 400, "{target}");
        }
        assert!(parse("/v2/storage/.a%01").is_ok());
        assert!(parse("/v2/storage/").is_ok());
        assert!(parse(&format!("/v2/storage/{}", "中".repeat(129))).is_ok());
    }

    #[test]
    fn list_query_rejects_known_duplicates_and_invalid_decimal_limits() {
        for query in [
            "limit=1&limit=2",
            "limit=1&limit.value=2",
            "cursor=a&cursor=b",
            "user_id=&userId=",
            "limit[2]=1",
            "cursor.x=1",
            "user_id.x=1",
        ] {
            assert_eq!(
                parse(&format!("/v2/storage/a?{query}")).unwrap_err().2,
                INVALID_QUERY
            );
        }
        for limit in [
            "",
            "0",
            "101",
            "-1",
            "1.0",
            "1e0",
            "1_0",
            "2147483648",
            "+1",
            "%201",
            "１",
        ] {
            assert_eq!(
                parse(&format!("/v2/storage/a?limit={limit}"))
                    .unwrap_err()
                    .2,
                INVALID_LIMIT,
                "{limit}"
            );
        }
        assert_eq!(
            parse("/v2/storage/a?limit.unknown=1").unwrap_err().2,
            INVALID_LIMIT
        );
    }

    #[test]
    fn unknown_wrapper_fields_only_allocate_and_preserve_a_known_limit() {
        for query in [
            "limit.unknown=9&limit=2",
            "limit=2&limit.unknown=9",
            "limit.unknown=9&limit.value=2",
            "limit.value=2&limit.unknown=9",
            "limit.unknown.nested=9&limit.value=2",
            "limit.unknown[x]=9&limit.value=2",
            "limit.unknown=9&limit.unknown=8&limit.value=2",
        ] {
            assert_eq!(
                parse(&format!("/v2/storage/a?{query}")).unwrap().limit,
                2,
                "{query}"
            );
        }
        for query in [
            "limit.unknown=9",
            "limit.unknown=9&limit.unknown=8",
            "limit.unknown.nested=9",
        ] {
            assert_eq!(
                parse(&format!("/v2/storage/a?{query}")).unwrap_err().2,
                INVALID_LIMIT
            );
        }
        assert_eq!(parse("/v2/storage/a?.limit.unknown=1").unwrap().limit, 1);
    }

    #[test]
    fn linebreak_query_names_do_not_match_the_gateway_bracket_regexp() {
        for query in ["limit[%0A]=1", "cursor[%0A]=a", "user_id[%0A]=bad"] {
            let query = parse(&format!("/v2/storage/a?{query}")).unwrap();
            assert_eq!(query.limit, 1);
            assert_eq!(query.owner, None);
            assert!(query.cursor.is_empty());
        }
    }

    #[test]
    fn routing_shape_unescapes_static_segments_and_slashes_once() {
        assert_eq!(
            routing_path("/%76%32/%73torage/a?cursor=%GG").as_deref(),
            Some("/v2/storage/a")
        );
        assert_eq!(
            routing_path("/v2/storage/a%2Fb").as_deref(),
            Some("/v2/storage/a/b")
        );
        assert_eq!(
            routing_path("/v2/storage/a%252Fb").as_deref(),
            Some("/v2/storage/a%2Fb")
        );
        assert_eq!(
            routing_path("/v2/storage/a+b").as_deref(),
            Some("/v2/storage/a+b")
        );
        assert!(routing_path("/v2/storage/%GG").is_none());
        assert!(routing_path(&format!("/v2/storage/{}", "a".repeat(32 * 1024))).is_none());
    }

    #[test]
    fn frozen_go_query_observations_and_named_candidate_residuals() {
        let vectors: serde_json::Value = serde_json::from_str(QUERY_ORACLE).unwrap();
        assert_eq!(vectors.as_array().unwrap().len(), 40); // original 39 + UTF-8 residual
        for vector in vectors.as_array().unwrap() {
            let target = vector["target"].as_str().unwrap();
            let observed = &vector["observed"];
            let result = parse(target);
            if let Some(residual) = vector["residual"].as_str() {
                // Go accepts these queries. This candidate deliberately rejects
                // distinct known aliases and raw non-UTF8 query strings; these
                // assertions record differences rather than redefine the oracle.
                assert!(matches!(
                    residual,
                    "distinct_known_aliases" | "raw_non_utf8_query_string"
                ));
                assert_eq!(observed["accepted"], true, "{residual}: {target}");
                assert_eq!(observed["api_limit_valid"], true, "{residual}: {target}");
                let error = result.unwrap_err();
                assert_eq!((error.0, error.1), (400, 3), "{residual}: {target}");
            } else if observed["accepted"] != true || observed["api_limit_valid"] != true {
                // Error wording remains unqualified. Compare only the status
                // and gRPC code for query failures or API-invalid limit=0.
                let error = result.unwrap_err();
                assert_eq!((error.0, error.1), (400, 3), "{target}");
            } else if observed["owner"] == "b" {
                // The scratch oracle stops before Nakama's API UUID check.
                // Legacy percent-unescaping makes a%2Fb select owner 'b'.
                let error = result.unwrap_err();
                assert_eq!((error.0, error.1, error.2), (400, 3, INVALID_USER));
            } else {
                let query = result
                    .unwrap_or_else(|error| panic!("Go projection accepted {target}: {error:?}"));
                assert_eq!(
                    query.collection,
                    observed["collection"].as_str().unwrap(),
                    "{target}"
                );
                assert_eq!(
                    query.limit,
                    usize::try_from(observed["limit"].as_u64().unwrap()).unwrap(),
                    "{target}"
                );
                assert_eq!(
                    query.cursor,
                    observed["cursor"].as_str().unwrap(),
                    "{target}"
                );
                let owner = observed["owner"].as_str().unwrap();
                let expected_owner = if owner.is_empty() {
                    None
                } else {
                    assert_eq!(owner, "00000000-0000-0000-0000-000000000000");
                    Some(UserId::new([0; 16]))
                };
                assert_eq!(query.owner, expected_owner, "{target}");
            }
        }
    }
}
