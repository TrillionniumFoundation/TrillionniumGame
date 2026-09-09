//! Authoritative RPG -> RTS -> RPG campaign contracts.
//!
//! This crate is deliberately independent from Bevy. Presentation clients may
//! request a [`BattleSeedV1`] and submit a [`BattleResultV1`], but only this
//! aggregate is allowed to mutate persistent RPG progression.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt, fs,
    io::Write,
    path::{Path, PathBuf},
};
pub use trnm_economy_protocol::{
    ActorRef as EconomyActorRef, AssetRef as EconomyAssetRef, EconomicIntent, EconomicIntentKind,
    EconomicReceipt, EconomyAccountBinding, EconomyAssetClass, EconomyAssetSemantic,
    EconomyCurrencyClass, EconomyMode, EconomyTransferability,
    IdempotencyKey as EconomyIdempotencyKey, ReceiptProgressionClass, ReceiptStatus,
    SettlementBackendKind, WalletSnapshot, BATTLE_WALLET_REWARD_DAILY_CAP,
    BATTLE_WALLET_REWARD_PER_EVENT_CAP, CEX_SETTLEMENT_BACKEND_ID, OFFLINE_LOCAL_BACKEND_ID,
    SELLER_REVERSIBLE_WINDOW_SECONDS, SERVER_SIGNED_VALUE_ENTITLEMENT_METADATA_KEY,
    TERM_EXCHANGE_PROTOCOL_VERSION,
};
use trnm_rpg_core::{
    inventory_item_for as trillionnium_inventory_item_for, market_price_with_state,
    mirror_city_world_graph, npc_choice_dialogue, npc_dialogue, npc_room_at, npc_schedule,
    npc_social_event, original_combat_log, quest_condition_graph, quest_narrative,
    quest_resolution_text, quest_runtime_rule, resolve_mentor_sparring, skill_unlockable,
    BuildPath, BuildTitle, Character as WorldTrillionniumCharacter, CharacterOrigin, CombatLogBeat,
    DialogueChoice, EncounterOutcome, EquipmentAffixCondition, FactionRank, GrowthStat,
    ItemCondition, NpcRelationship, QuestApproach, RelationshipAction, RelationshipStage,
    RpgEncounterState, SparringAction, SparringOutcome, SparringReport, TechniqueStyle,
    TrillionniumAttributes, WorldRoutePlan, ARCHIVE_STEPS_ROOM, ASH_BEACON_FIELD_ROOM,
    BASIN_OBSERVATORY_ROOM, CARAVAN_YARD_ROOM, CINDER_REFUGE_ROOM, CISTERN_WARD_ROOM,
    CRAFTING_RECIPES, DEEP_RELAY_ROOM, ECONOMY_ITEM_CATALOG, EMBER_ORCHARD_EDGE_ROOM,
    ENCOUNTER_CATALOG, EXPEDITION_GATE_ROOM, GLASS_BASIN_WAYHOUSE_ROOM, GLASS_REED_MARSH_ROOM,
    LANTERN_INFIRMARY_ROOM, MARKET_WIND_PAVILION_ROOM, MENTOR_HALL_ROOM, MIRROR_SQUARE_ROOM,
    MOON_BRIDGE_ROOM, NIGHT_WATCH_POST_ROOM, NPC_CATALOG, OUTER_SIGNAL_ROAD_ROOM,
    REGIONAL_QUEST_CATALOG, RELAY_QUARTER_ROOM, SECT_CATALOG, SKILL_CATALOG, WORKSHOP_GATE_ROOM,
};
pub use trnm_rpg_core::{EncounterAction, MasteryChallenge, SectId};

// Correctness-critical implementation is partitioned by ownership. Every
// included file is ordinary reviewed source; no build script rewrites runtime
// semantics and no generated Rust source participates in compilation.

// Ownership section: contracts_and_domain. Ordinary Git-tracked source.
include!("lib_parts/contracts_and_domain/part_01.rs");

// Ownership section: authored_content. Ordinary Git-tracked source.
include!("lib_parts/authored_content/part_01.rs");

// Ownership section: campaign_state. Ordinary Git-tracked source.
include!("lib_parts/campaign_state/part_01.rs");

// Ownership section: campaign_commands. Ordinary Git-tracked source.
include!("lib_parts/campaign_commands/part_01.rs");

// Ownership section: campaign_commands. Ordinary Git-tracked source.
include!("lib_parts/campaign_commands/part_02.rs");

// Ownership section: campaign_commands. Ordinary Git-tracked source.
include!("lib_parts/campaign_commands/part_03.rs");

// Ownership section: campaign_commands. Ordinary Git-tracked source.
include!("lib_parts/campaign_commands/part_04.rs");

// Ownership section: campaign_commands. Ordinary Git-tracked source.
include!("lib_parts/campaign_commands/part_05.rs");

// Ownership section: campaign_commands. Ordinary Git-tracked source.
include!("lib_parts/campaign_commands/part_06.rs");

// Ownership section: rts_mapping. Ordinary Git-tracked source.
include!("lib_parts/rts_mapping/part_01.rs");

// Ownership section: save_slots. Ordinary Git-tracked source.
include!("lib_parts/save_slots/part_01.rs");

// Player settings are the first ownership section promoted from crate-root text
// inclusion into a real Rust module boundary. The temporary `use super::*`
// keeps the semantic dependency surface unchanged while the public API remains
// available at the crate root; later tranches narrow these imports explicitly.
mod player_settings {
    use super::*;
    include!("lib_parts/player_settings/part_01.rs");
}
use player_settings::atomic_write_json;
pub use player_settings::*;

// Ownership section: campaign_storage. Ordinary Git-tracked source.
include!("lib_parts/campaign_storage/part_01.rs");

// Ownership section: economy_commands. Ordinary Git-tracked source.
include!("lib_parts/economy_commands/part_01.rs");

// Ownership section: tests. Ordinary Git-tracked source.
include!("lib_parts/tests/part_01.rs");
