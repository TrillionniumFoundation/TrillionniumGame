#[test]
fn authority_takeover_fences_stale_generation() {
    let Some((database_url, profile)) = live_database_environment("authority lease contract")
    else {
        return;
    };
    let entity = EntityId::new([0x71; 16]);
    let owner_a = NodeId::new([0x72; 16]);
    let owner_b = NodeId::new([0x73; 16]);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    repository
        .bootstrap_entity(entity, 1, digest(0x74), 1)
        .unwrap();
    let lease_a = repository
        .acquire_authority_lease(entity, owner_a, 1, 10, 20)
        .unwrap();
    assert_eq!(lease_a.lease_generation, 1);
    assert_eq!(lease_a.authority_generation, 1);
    let held = repository
        .acquire_authority_lease(entity, owner_b, 1, 15, 30)
        .unwrap_err();
    assert_eq!(held.code(), StableCode::Aborted);
    assert_eq!(held.reason(), "authority_lease_held");
    let renewed = repository.renew_authority_lease(lease_a, 15, 25).unwrap();
    assert_eq!(renewed.lease_generation, 1);
    let lease_b = repository
        .acquire_authority_lease(entity, owner_b, 1, 25, 40)
        .unwrap();
    assert_eq!(lease_b.lease_generation, 2);
    assert_eq!(lease_b.authority_generation, 2);
    let head = repository.load_head(entity).unwrap().unwrap();
    assert_eq!(head.authority_generation, 2);

    let stale = CommitRequest {
        entity,
        command: CommandId::new([0x75; 16]),
        fingerprint: digest(0x76),
        expected_revision: 0,
        authority_generation: 1,
        next_state: digest(0x77),
        committed_at_ms: 30,
        events: vec![],
        outbox: vec![],
    };
    assert_eq!(
        repository.commit_command(&stale).unwrap_err().reason(),
        "authority_generation_mismatch"
    );
}
