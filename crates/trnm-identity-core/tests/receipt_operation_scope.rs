use trnm_identity_core::{
    AccountId, AccountStatus, CommandId, CommandReceipt, DisplayName, Fingerprint, IdentityConfig,
    IdentityError, IdentityErrorCode, IdentityProvider, IdentityRegistry, ProviderIdentity,
    Username,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Operation {
    Create,
    Authenticate,
    Link,
    Unlink,
    Update,
    SetStatus,
    Delete,
}

impl Operation {
    const ALL: [Self; 7] = [
        Self::Create,
        Self::Authenticate,
        Self::Link,
        Self::Unlink,
        Self::Update,
        Self::SetStatus,
        Self::Delete,
    ];
}

fn bytes16(value: u8) -> [u8; 16] {
    let mut bytes = [0; 16];
    bytes[15] = value;
    bytes
}

fn bytes32(value: u8) -> [u8; 32] {
    let mut bytes = [0; 32];
    bytes[31] = value;
    bytes
}

fn account(value: u8) -> AccountId {
    AccountId::new(bytes16(value)).expect("nonzero account")
}

fn command(value: u8) -> CommandId {
    CommandId::new(bytes16(value)).expect("nonzero command")
}

fn fingerprint(value: u8) -> Fingerprint {
    Fingerprint::new(bytes32(value)).expect("nonzero fingerprint")
}

fn provider(kind: IdentityProvider, value: &str) -> ProviderIdentity {
    ProviderIdentity::new(kind, value).expect("valid provider")
}

fn registry() -> IdentityRegistry {
    IdentityRegistry::new(IdentityConfig::new(16, 8, 128).expect("valid limits"))
}

fn create_base(registry: &mut IdentityRegistry, primary: ProviderIdentity) -> CommandReceipt {
    registry
        .create_account(
            command(1),
            fingerprint(1),
            account(1),
            Username::new("identity-scope-user").expect("valid username"),
            DisplayName::new("Identity Scope User").expect("valid display name"),
            primary,
        )
        .expect("base account")
}

fn seed_receipt(
    operation: Operation,
) -> (
    IdentityRegistry,
    CommandReceipt,
    ProviderIdentity,
    ProviderIdentity,
) {
    let mut registry = registry();
    let primary = provider(IdentityProvider::Device, "scope-primary");
    let secondary = provider(IdentityProvider::Email, "scope-secondary@example.invalid");
    let receipt = match operation {
        Operation::Create => registry
            .create_account(
                command(200),
                fingerprint(200),
                account(1),
                Username::new("identity-scope-user").expect("valid username"),
                DisplayName::new("Identity Scope User").expect("valid display name"),
                primary.clone(),
            )
            .expect("create receipt"),
        Operation::Authenticate => {
            create_base(&mut registry, primary.clone());
            registry
                .authenticate(command(200), fingerprint(200), &primary)
                .expect("authenticate receipt")
        }
        Operation::Link => {
            create_base(&mut registry, primary.clone());
            registry
                .link_provider(
                    command(200),
                    fingerprint(200),
                    account(1),
                    1,
                    secondary.clone(),
                )
                .expect("link receipt")
        }
        Operation::Unlink => {
            create_base(&mut registry, primary.clone());
            registry
                .link_provider(command(2), fingerprint(2), account(1), 1, secondary.clone())
                .expect("setup link");
            registry
                .unlink_provider(command(200), fingerprint(200), account(1), 2, &secondary)
                .expect("unlink receipt")
        }
        Operation::Update => {
            create_base(&mut registry, primary.clone());
            registry
                .update_profile(
                    command(200),
                    fingerprint(200),
                    account(1),
                    1,
                    None,
                    Some(DisplayName::new("Updated").expect("valid display name")),
                )
                .expect("update receipt")
        }
        Operation::SetStatus => {
            create_base(&mut registry, primary.clone());
            registry
                .set_status(
                    command(200),
                    fingerprint(200),
                    account(1),
                    1,
                    AccountStatus::Disabled,
                )
                .expect("status receipt")
        }
        Operation::Delete => {
            create_base(&mut registry, primary.clone());
            registry
                .delete_account(command(200), fingerprint(200), account(1), 1)
                .expect("delete receipt")
        }
    };
    (registry, receipt, primary, secondary)
}

fn attempt(
    registry: &mut IdentityRegistry,
    operation: Operation,
    primary: &ProviderIdentity,
    secondary: &ProviderIdentity,
) -> Result<CommandReceipt, IdentityError> {
    match operation {
        Operation::Create => registry.create_account(
            command(200),
            fingerprint(200),
            account(9),
            Username::new("attempted-user").expect("valid username"),
            DisplayName::new("Attempted User").expect("valid display name"),
            provider(IdentityProvider::Steam, "attempted-steam"),
        ),
        Operation::Authenticate => registry.authenticate(command(200), fingerprint(200), primary),
        Operation::Link => registry.link_provider(
            command(200),
            fingerprint(200),
            account(1),
            1,
            secondary.clone(),
        ),
        Operation::Unlink => {
            registry.unlink_provider(command(200), fingerprint(200), account(1), 1, secondary)
        }
        Operation::Update => registry.update_profile(
            command(200),
            fingerprint(200),
            account(1),
            1,
            None,
            Some(DisplayName::new("Attempted Update").expect("valid display name")),
        ),
        Operation::SetStatus => registry.set_status(
            command(200),
            fingerprint(200),
            account(1),
            1,
            AccountStatus::Banned,
        ),
        Operation::Delete => registry.delete_account(command(200), fingerprint(200), account(1), 1),
    }
}

#[test]
fn receipt_replay_is_scoped_to_the_exact_public_operation() {
    for stored in Operation::ALL {
        for attempted in Operation::ALL {
            let (mut registry, receipt, primary, secondary) = seed_receipt(stored);
            let snapshot = registry.clone();
            let result = attempt(&mut registry, attempted, &primary, &secondary);
            if stored == attempted {
                assert_eq!(
                    result.expect("same-operation replay must succeed"),
                    receipt,
                    "stored={stored:?}, attempted={attempted:?}"
                );
            } else {
                let error = result.expect_err("cross-operation replay must fail");
                assert_eq!(
                    error.code(),
                    IdentityErrorCode::Conflict,
                    "stored={stored:?}, attempted={attempted:?}"
                );
                assert_eq!(
                    error.reason(),
                    "command_operation_conflict",
                    "stored={stored:?}, attempted={attempted:?}"
                );
            }
            assert_eq!(
                registry, snapshot,
                "replay must not mutate: stored={stored:?}, attempted={attempted:?}"
            );
        }
    }
}
