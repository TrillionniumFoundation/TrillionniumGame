import gzip, hashlib, json
from pathlib import Path
payload = json.loads(gzip.decompress(Path("payload.json.gz").read_bytes()))
path = 'crates/trnm-persistence-pg/src/bin/trnm_server/retry_atomicity.rs'
old = 'impl Repository for InjectedRepository {'
new = 'impl std::fmt::Debug for InjectedRepository {\n    fn fmt(&self, formatter: &mut std::fmt::Formatter<\'_>) -> std::fmt::Result {\n        formatter.debug_struct("InjectedRepository")\n            .field("calls", &self.calls.load(Ordering::Relaxed))\n            .field("fail_every_attempt", &self.fail_every_attempt)\n            .finish_non_exhaustive()\n    }\n}\n\nimpl Repository for InjectedRepository {'
assert payload["new_files"][path].count(old) == 1
payload["new_files"][path] = payload["new_files"][path].replace(old, new, 1)
raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
data = gzip.compress(raw, mtime=0)
assert hashlib.sha256(raw).hexdigest() == "a86735e813d453fb88d12c3d57bc7bdf18750a0f22ccc03d233a285901c90df0"
assert gzip.decompress(data) == raw
Path("payload.active.json.gz").write_bytes(data)
print("refined_payload_source_sha256=" + hashlib.sha256(raw).hexdigest())
