#![forbid(unsafe_code)]

//! Bounded social, group, chat, notification, and receipt state transitions.
//!
//! This crate is transport- and persistence-independent. Durable adapters must
//! provide one writer, serializable persistence, outbox delivery, and recovery.

include!("parts/00_foundation.rs");
include!("parts/01_payloads.rs");
include!("parts/02_config.rs");
include!("parts/03_relationships.rs");
include!("parts/04_groups.rs");
include!("parts/05_messages.rs");
include!("parts/06_notifications.rs");
include!("parts/07_receipts.rs");
include!("parts/08_state.rs");

include!("parts/10_registry_friend_request.rs");
include!("parts/11_registry_friend_graph.rs");
include!("parts/12_registry_group_join.rs");
include!("parts/13_registry_group_admin.rs");
include!("parts/14_registry_messages.rs");
include!("parts/15_registry_notifications.rs");
include!("parts/16_registry_receipts.rs");
include!("parts/17_registry_invariants.rs");
include!("parts/18_helpers.rs");

#[cfg(test)]
mod tests {
    include!("tests/00_common.rs");
    include!("tests/01_friendships.rs");
    include!("tests/02_groups.rs");
    include!("tests/03_messages.rs");
    include!("tests/04_notifications_limits.rs");
    include!("tests/05_config.rs");
}
