# ADR Roadmap

| ADR | 最晚阶段门 | 决策 |
| --- | --- | --- |
| ADR-0002 Compatibility Profiles | SG1 | C0–C5、compatibility/native profile、claim wording |
| ADR-0003 Protocol Generation and Gateway | SG3 | tonic/axum/transcoding、JSON/protobuf exactness |
| ADR-0004 Database and Migration Architecture | SG3 | PostgreSQL/CockroachDB、schema modes、CDC、rollback |
| ADR-0005 Realtime Ownership and Fencing | SG3 | socket/presence/stream/party/match owner generations |
| ADR-0006 Runtime Engine Selection | SG3 | JavaScript、Lua、native Rust、WASM、sandbox/capabilities |
| ADR-0007 Search and Query Architecture | SG3 | Nakama query grammar、PostgreSQL/Tantivy、rebuild |
| ADR-0008 IAP Effect Model | SG5 | provider call、receipt、outbox、refund/renewal、reconciliation |
| ADR-0009 Console and Administrative Security | SG5 | Rust/WASM UI、RBAC、MFA、approval、audit |
| ADR-0010 Upstream Release Train | SG2 | frozen baseline、delta lane、1.0 final baseline |
| ADR-0011 Cutover and Rollback Authority | SG7 | entity cohorts、fencing、key revoke、retirement |
| ADR-0012 Unsafe and Native Dependency Budget | SG3 | FFI、native VM、cryptography、review policy |

ADR 必须包含 alternatives、decision drivers、compatibility impact、migration、security、performance、operations、rollback 和 evidence requirements。
