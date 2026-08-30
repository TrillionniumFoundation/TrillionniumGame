from pathlib import Path

root = Path('.')
lib = root / 'crates/trnm-persistence-pg/src/lib.rs'
app = root / 'crates/trnm-persistence-pg/src/bin/trnm_server/app.rs'
pool = root / 'crates/trnm-persistence-pg/src/bin/trnm_server/pool.rs'
retry = root / 'crates/trnm-persistence-pg/src/bin/trnm_server/retry.rs'
for path in (lib, app, pool, retry):
    if not path.is_file():
        raise SystemExit(f'missing required file: {path}')

source = lib.read_text(encoding='utf-8')
if 'pub fn readiness_probe(&mut self)' not in source:
    anchor = '    pub fn table_exists(&mut self, table: &str) -> Result<bool, DomainError> {\n'
    index = source.find(anchor)
    if index < 0:
        anchor = '    pub fn bind_schema_metadata(\n'
        index = source.find(anchor)
    if index < 0:
        raise SystemExit('lib readiness insertion anchor missing')
    method = '''    pub fn readiness_probe(&mut self) -> Result<(), DomainError> {
        let row = self
            .client
            .query_one("SELECT 1", &[])
            .map_err(map_postgres_error)?;
        let value: i32 = row.get(0);
        if value != 1 {
            return Err(DomainError::new(
                StableCode::DataLoss,
                "database_readiness_probe_invalid",
                RetryClass::SafeBackoff,
            ));
        }
        Ok(())
    }

'''
    source = source[:index] + method + source[index:]
lib.write_text(source, encoding='utf-8')

source = app.read_text(encoding='utf-8')
trait_start = source.find('pub trait Repository')
trait_end = source.find('\n}', trait_start)
if trait_start < 0 or trait_end < 0:
    raise SystemExit('repository trait missing')
if 'fn readiness_probe' not in source[trait_start:trait_end]:
    anchor = '    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError>;\n'
    if anchor not in source:
        raise SystemExit('trait commit anchor missing')
    source = source.replace(
        anchor,
        anchor + '\n    fn readiness_probe(&mut self) -> Result<(), DomainError> {\n        Ok(())\n    }\n',
        1,
    )
implementation = source.find('impl Repository for PgRepository')
implementation_end = source.find('\n}', implementation)
if implementation < 0 or implementation_end < 0:
    raise SystemExit('PgRepository implementation missing')
if 'fn readiness_probe' not in source[implementation:implementation_end]:
    source = source[:implementation_end] + '''

    fn readiness_probe(&mut self) -> Result<(), DomainError> {
        PgRepository::readiness_probe(self)
    }
''' + source[implementation_end:]
if 'readiness_failures:' not in source:
    source = source.replace(
        '    drain_requests: u64,\n',
        '    drain_requests: u64,\n    readiness_failures: u64,\n',
        1,
    )
old = '''    fn readiness(&self) -> Response {
        if self.draining {
            error_response(503, "unavailable", "Service is draining.", "backoff")
        } else {
            Response::json(200, br#"{\\"status\\":\\"ready\\"}"#.to_vec())
        }
    }
'''
if old in source:
    source = source.replace(
        old,
        '''    fn readiness(&mut self) -> Response {
        if self.draining {
            return error_response(503, "unavailable", "Service is draining.", "backoff");
        }
        match self.repository.readiness_probe() {
            Ok(()) => Response::json(200, br#"{\\"status\\":\\"ready\\"}"#.to_vec()),
            Err(_) => {
                Metrics::increment(&mut self.metrics.readiness_failures);
                error_response(503, "unavailable", "Service is unavailable.", "backoff")
            }
        }
    }
''',
        1,
    )
elif 'fn readiness(&mut self)' not in source:
    raise SystemExit('readiness method anchor missing')
if 'trnm_server_readiness_failures_total' not in source:
    marker = '# TYPE trnm_server_ready gauge\\n\\\ntrnm_server_ready {}\\n'
    if marker not in source:
        raise SystemExit('metrics string marker missing')
    source = source.replace(
        marker,
        '# TYPE trnm_server_readiness_failures_total counter\\n\\\ntrnm_server_readiness_failures_total {}\\n\\\n' + marker,
        1,
    )
    arguments = '                self.metrics.command_replays,\n                ready,\n'
    if arguments not in source:
        raise SystemExit('metrics argument marker missing')
    source = source.replace(
        arguments,
        '                self.metrics.command_replays,\n                self.metrics.readiness_failures,\n                ready,\n',
        1,
    )
fake_start = source.find('impl Repository for FakeRepository')
fake_end = source.find('\n    fn token()', fake_start)
if fake_start >= 0 and fake_end >= 0 and 'fn readiness_probe' not in source[fake_start:fake_end]:
    insert_at = source.rfind('\n    }', fake_start, fake_end)
    if insert_at < 0:
        raise SystemExit('fake repository insertion point missing')
    source = source[:insert_at] + '''

        fn readiness_probe(&mut self) -> Result<(), DomainError> {
            match self.failure {
                Some(error) => Err(error),
                None => Ok(()),
            }
        }
''' + source[insert_at:]
if 'readiness_fails_closed_when_database_probe_fails' not in source:
    anchor = '    #[test]\n    fn authenticated_drain_stops_new_mutations() {\n'
    if anchor not in source:
        raise SystemExit('readiness test anchor missing')
    source = source.replace(
        anchor,
        '''    #[test]
    fn readiness_fails_closed_when_database_probe_fails() {
        let repository = FakeRepository {
            failure: Some(DomainError::new(
                StableCode::Unavailable,
                "private_database_probe_reason",
                RetryClass::SafeBackoff,
            )),
        };
        let mut app = App::new(repository, token());
        let response = app.handle(&Request::new(
            "GET",
            "/readyz",
            BTreeMap::new(),
            Vec::new(),
        ));
        assert_eq!(response.status, 503);
        let body = String::from_utf8(response.body).unwrap();
        assert!(!body.contains("private_database_probe_reason"));
        assert!(body.contains("Service is unavailable"));
    }

''' + anchor,
        1,
    )
app.write_text(source, encoding='utf-8')

source = pool.read_text(encoding='utf-8')
implementation = source.find('impl Repository for PooledRepository')
implementation_end = source.find('\n}', implementation)
if implementation < 0 or implementation_end < 0:
    raise SystemExit('pooled repository implementation missing')
if 'fn readiness_probe' not in source[implementation:implementation_end]:
    source = source[:implementation_end] + '''

    fn readiness_probe(&mut self) -> Result<(), DomainError> {
        let mut repository = self.pool.acquire()?;
        let _cancellation = repository.cancellation_watchdog()?;
        repository.readiness_probe()
    }
''' + source[implementation_end:]
pool.write_text(source, encoding='utf-8')

source = retry.read_text(encoding='utf-8')
implementation = source.find('impl<R: Repository> Repository for RetryingRepository<R>')
implementation_end = source.find('\n}', implementation)
if implementation < 0 or implementation_end < 0:
    raise SystemExit('retry repository implementation missing')
if 'fn readiness_probe' not in source[implementation:implementation_end]:
    source = source[:implementation_end] + '''

    fn readiness_probe(&mut self) -> Result<(), DomainError> {
        execute_with_metrics(self.policy, &self.metrics, || self.inner.readiness_probe())
    }
''' + source[implementation_end:]
retry.write_text(source, encoding='utf-8')
