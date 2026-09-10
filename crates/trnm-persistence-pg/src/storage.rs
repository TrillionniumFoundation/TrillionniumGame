include!("storage_parts/00_prelude.rs");
include!("storage_parts/01_repository.rs");
include!("storage_parts/02_list_helpers.rs");
include!("storage_parts/03_write.rs");
include!("storage_parts/04_delete_authorize.rs");
include!("storage_parts/05_decode_errors.rs");

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_contracts::Digest32;

    include!("storage_parts/90_test_helpers.rs");
    include!("storage_parts/91_test_batch.rs");
    include!("storage_parts/92_test_list.rs");
    include!("storage_parts/93_test_page.rs");
    include!("storage_parts/94_test_integrity.rs");
}
