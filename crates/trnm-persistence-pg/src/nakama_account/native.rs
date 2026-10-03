use super::{
    authenticate_device, authenticate_device_with_id_source, checked_stored_user, database_uuid,
    decode_database_uuid, read_user, AccountSqlFailure, AuthenticateDevice,
    AuthenticateDeviceOutcome, DeviceDatabase, DeviceTransaction, NakamaAccountError,
    NakamaAccountIdGenerationError, NakamaLegacyUser,
};
use crate::{AuthoritativeSchemaTarget, PgRepository};
use postgres::Transaction;
use trnm_contracts::{DomainError, UserId};

const FIND_DEVICE: &str = "SELECT user_id::TEXT FROM public.user_device WHERE id=$1";
const READ_USER: &str = "SELECT username, FLOOR(EXTRACT(EPOCH FROM disable_time))::BIGINT FROM public.users WHERE id=$1::TEXT::UUID";
const INSERT_USER: &str = "INSERT INTO public.users (id,username,create_time,update_time) \
    SELECT $1::TEXT::UUID AS id,$2 AS username,now(),now() \
    WHERE NOT EXISTS (SELECT id FROM public.user_device WHERE id=$3::VARCHAR)";
const INSERT_DEVICE: &str =
    "INSERT INTO public.user_device (id,user_id) VALUES ($1,$2::TEXT::UUID)";

impl PgRepository {
    /// Full AccountsV5 ready/catalog proof precedes any account business query.
    /// The current capture gate deliberately rejects without account SQL.
    pub fn read_nakama_user(
        &mut self,
        id: UserId,
    ) -> Result<Option<NakamaLegacyUser>, NakamaAccountError> {
        read_user(&mut NativeDatabase { repository: self }, id)
    }

    /// Purely native Device identity; no provider or caller-supplied principal.
    /// Caller supplies one trusted, fresh v4 UUID outside TX for the missing /
    /// create path. All five attempts reuse it. Use a deadline-bound pool lease
    /// for service calls; this method does no network/provider I/O beyond SQL.
    pub fn authenticate_nakama_device(
        &mut self,
        request: AuthenticateDevice<'_>,
        new_user_id: Option<UserId>,
    ) -> Result<AuthenticateDeviceOutcome, NakamaAccountError> {
        let profile = self.profile;
        let result = authenticate_device(
            &mut NativeDatabase { repository: self },
            profile,
            request,
            new_user_id,
        );
        if result.as_ref().is_err_and(|error| error.unknown_commit())
            || result
                .as_ref()
                .is_ok_and(|outcome| outcome.committed_cleanup_failure.is_some())
        {
            self.client.retire();
        }
        result
    }
    /// Trusted local OS ID source, deferred to the source missing/create phase.
    /// No remote I/O or principal creation is delegated to this callback. The
    /// repository alone owns its at-most-five attempts and commit diagnostics.
    pub fn authenticate_nakama_device_with_id_source(
        &mut self,
        request: AuthenticateDevice<'_>,
        new_user_id: impl FnOnce() -> Result<UserId, NakamaAccountIdGenerationError>,
    ) -> Result<AuthenticateDeviceOutcome, NakamaAccountError> {
        let profile = self.profile;
        let result = authenticate_device_with_id_source(
            &mut NativeDatabase { repository: self },
            profile,
            request,
            new_user_id,
        );
        if result.as_ref().is_err_and(|error| error.unknown_commit())
            || result
                .as_ref()
                .is_ok_and(|outcome| outcome.committed_cleanup_failure.is_some())
        {
            self.client.retire();
        }
        result
    }
}

struct NativeDatabase<'a> {
    repository: &'a mut PgRepository,
}
impl DeviceDatabase for NativeDatabase<'_> {
    type Transaction<'a>
        = NativeTransaction<'a>
    where
        Self: 'a;
    fn require_ready(&mut self) -> Result<(), DomainError> {
        self.repository
            .verify_authoritative_schema_target(AuthoritativeSchemaTarget::NakamaAccountsV5)
            .map(|_| ())
    }
    fn find_device(&mut self, device_id: &str) -> Result<Option<UserId>, AccountSqlFailure> {
        self.repository
            .client
            .query_opt(FIND_DEVICE, &[&device_id])
            .map_err(sql_failure)?
            .map(|row| {
                let value: String = row.try_get(0).map_err(sql_failure)?;
                decode_database_uuid(&value)
            })
            .transpose()
    }
    fn read_user(&mut self, id: UserId) -> Result<Option<NakamaLegacyUser>, AccountSqlFailure> {
        let id_text = database_uuid(id);
        self.repository
            .client
            .query_opt(READ_USER, &[&id_text])
            .map_err(sql_failure)?
            .map(|row| {
                checked_stored_user(
                    id,
                    row.try_get(0).map_err(sql_failure)?,
                    row.try_get(1).map_err(sql_failure)?,
                )
            })
            .transpose()
    }
    fn begin(&mut self) -> Result<Self::Transaction<'_>, AccountSqlFailure> {
        // No isolation override: matches fixed Go BeginTx(ctx,nil).
        self.repository
            .client
            .transaction()
            .map(|transaction| NativeTransaction {
                transaction: Some(transaction),
            })
            .map_err(sql_failure)
    }
}

struct NativeTransaction<'a> {
    transaction: Option<Transaction<'a>>,
}
impl<'a> NativeTransaction<'a> {
    fn sql(&mut self) -> Result<&mut Transaction<'a>, AccountSqlFailure> {
        self.transaction
            .as_mut()
            .ok_or_else(AccountSqlFailure::closed)
    }
}
impl DeviceTransaction for NativeTransaction<'_> {
    fn insert_user(
        &mut self,
        request: AuthenticateDevice<'_>,
        id: UserId,
    ) -> Result<u64, AccountSqlFailure> {
        let id_text = database_uuid(id);
        self.sql()?
            .execute(
                INSERT_USER,
                &[&id_text, &request.username, &request.device_id],
            )
            .map_err(sql_failure)
    }
    fn insert_device(&mut self, device_id: &str, id: UserId) -> Result<u64, AccountSqlFailure> {
        let id_text = database_uuid(id);
        self.sql()?
            .execute(INSERT_DEVICE, &[&device_id, &id_text])
            .map_err(sql_failure)
    }
    fn savepoint(&mut self) -> Result<(), AccountSqlFailure> {
        self.sql()?
            .batch_execute("SAVEPOINT cockroach_restart")
            .map_err(sql_failure)
    }
    fn release(&mut self) -> Result<(), AccountSqlFailure> {
        self.sql()?
            .batch_execute("RELEASE SAVEPOINT cockroach_restart")
            .map_err(sql_failure)
    }
    fn restart(&mut self) -> Result<(), AccountSqlFailure> {
        self.sql()?
            .batch_execute("ROLLBACK TO SAVEPOINT cockroach_restart")
            .map_err(sql_failure)
    }
    fn commit(&mut self) -> Result<(), AccountSqlFailure> {
        self.transaction
            .take()
            .ok_or_else(AccountSqlFailure::closed)?
            .commit()
            .map_err(sql_failure)
    }
    fn rollback(&mut self) -> Result<(), AccountSqlFailure> {
        self.transaction
            .take()
            .ok_or_else(AccountSqlFailure::closed)?
            .rollback()
            .map_err(sql_failure)
    }
}

fn sql_failure(error: postgres::Error) -> AccountSqlFailure {
    let sqlstate = error
        .code()
        .and_then(|code| code.code().as_bytes().try_into().ok());
    let username_collision = error.as_db_error().is_some_and(|db| {
        db.code().code() == "23505"
            && !db.message().contains("user_device_pkey")
            && db.message().contains("users_username_key")
    });
    AccountSqlFailure {
        sqlstate,
        username_collision,
        transaction_closed: false,
        reason: "nakama_native_database_failure",
    }
}

#[cfg(test)]
mod sql_tests {
    use super::*;
    #[test]
    fn native_sql_preserves_source_order_and_disable_floor_and_plain_device_insert() {
        assert!(FIND_DEVICE.starts_with("SELECT user_id::TEXT FROM public.user_device"));
        assert!(READ_USER.contains("FLOOR(EXTRACT(EPOCH FROM disable_time))::BIGINT"));
        assert!(INSERT_USER.contains("WHERE NOT EXISTS (SELECT id FROM public.user_device"));
        assert!(INSERT_USER.contains("now(),now()"));
        assert!(!INSERT_USER.contains("ON CONFLICT"));
        assert!(!INSERT_DEVICE.contains("ON CONFLICT"));
        assert!(!READ_USER.contains("UPDATE"));
    }
}
