include!("authority_storage_parts/00_helpers.rs");
include!("authority_storage_parts/01_authority.rs");
include!("authority_storage_parts/02_storage_batch.rs");
include!("authority_storage_parts/03_storage_list.rs");

// Keep the source-contract index compile-bound to the actual split test items.
// Removing or renaming either required test now fails Rust compilation, while
// check-trnm-server.py can still discover the canonical function signatures
// from this include authority without depending on textual include expansion.
#[allow(dead_code)]
const REQUIRED_SERVER_CONTRACT_TESTS: [fn(); 2] = [
    authority_takeover_fences_stale_generation,
    storage_occ_acl_and_batch_rollback_are_transactional,
];

// Canonical source-contract signatures (compile-bound above):
// fn authority_takeover_fences_stale_generation() {
// fn storage_occ_acl_and_batch_rollback_are_transactional() {
