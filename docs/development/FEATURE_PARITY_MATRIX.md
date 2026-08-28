# TrillionniumGame Feature Parity Matrix

Status: planning denominator for Nakama OSS v3.40.0.

No row may be removed merely because one Trillionnium product does not currently use it. A row may be split into finer rows as implementation knowledge improves.

| ID | Capability | Domain | Workstream | Window | Parity target | Status | Required evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TG-PAR-001 | Bootstrap and server start | CLI/config | W1 | M1-M4 | exact | planned | startup/config differential |
| TG-PAR-002 | Database migrate up/down/status | CLI/data | W1 | M1-M5 | exact | planned | schema checksum and round-trip |
| TG-PAR-003 | Healthcheck/readiness/shutdown | operations | W1 | M2-M5 | behavioral | planned | process and fault black-box |
| TG-PAR-004 | HTTP/JSON API gateway | protocol | W2 | M3-M8 | wire-exact | planned | OpenAPI and SDK differential |
| TG-PAR-005 | gRPC API | protocol | W2 | M3-M8 | wire-exact | planned | protobuf/status differential |
| TG-PAR-006 | WebSocket JSON protocol | realtime protocol | W2 | M4-M9 | wire-exact | planned | RT envelope black-box |
| TG-PAR-007 | WebSocket protobuf protocol | realtime protocol | W2 | M4-M9 | wire-exact | planned | RT envelope black-box |
| TG-PAR-008 | Device authentication | identity | W3 | M4-M8 | behavioral | planned | oracle + concurrency |
| TG-PAR-009 | Custom authentication | identity | W3 | M4-M8 | behavioral | planned | oracle + concurrency |
| TG-PAR-010 | Email/password authentication | identity | W3 | M5-M10 | behavioral | planned | oracle + security |
| TG-PAR-011 | Apple authentication/linking | identity provider | W3 | M6-M11 | behavioral | planned | provider sandbox + fixtures |
| TG-PAR-012 | Facebook authentication/linking/import | identity provider | W3 | M6-M11 | behavioral | planned | provider mock + fixtures |
| TG-PAR-013 | Facebook Instant Game authentication | identity provider | W3 | M6-M11 | behavioral | planned | signed fixture differential |
| TG-PAR-014 | Game Center authentication/linking | identity provider | W3 | M6-M11 | behavioral | planned | signed fixture differential |
| TG-PAR-015 | Google authentication/linking | identity provider | W3 | M6-M11 | behavioral | planned | provider sandbox + fixtures |
| TG-PAR-016 | Steam authentication/linking/import | identity provider | W3 | M6-M11 | behavioral | planned | provider sandbox + fixtures |
| TG-PAR-017 | Session JWT issue/refresh/logout | session | W3 | M5-M10 | wire-and-behavior | planned | token and revocation differential |
| TG-PAR-018 | Account get/update/delete/export/import | account | W3 | M6-M12 | behavioral | planned | API + DB differential |
| TG-PAR-019 | Identity link/unlink matrix | identity | W3 | M7-M12 | behavioral | planned | state-machine differential |
| TG-PAR-020 | Wallet and metadata operations | account | W3 | M7-M12 | behavioral | planned | transaction/idempotency tests |
| TG-PAR-021 | Storage read/write/delete | storage | W4 | M6-M11 | behavioral | planned | API + DB differential |
| TG-PAR-022 | Storage ACL and server-owned objects | storage | W4 | M7-M12 | behavioral | planned | permission matrix |
| TG-PAR-023 | Storage optimistic concurrency/version | storage | W4 | M7-M12 | behavioral | planned | concurrent writer differential |
| TG-PAR-024 | Storage list cursors | storage | W4 | M8-M13 | wire-and-behavior | planned | cursor corpus |
| TG-PAR-025 | Storage index/search | search | W4 | M9-M14 | query-compatible | planned | query and rebuild differential |
| TG-PAR-026 | Friends lifecycle | social | W5 | M8-M14 | behavioral | planned | model/oracle tests |
| TG-PAR-027 | Blocking and friend status | social | W5 | M9-M15 | behavioral | planned | edge/presence tests |
| TG-PAR-028 | Groups CRUD/search/list | social | W5 | M8-M14 | behavioral | planned | API + DB differential |
| TG-PAR-029 | Group membership roles and requests | social | W5 | M9-M16 | behavioral | planned | state-machine differential |
| TG-PAR-030 | Presence registry | realtime | W6 | M9-M15 | behavioral | planned | disconnect/node-failure tests |
| TG-PAR-031 | Streams and status follows | realtime | W6 | M10-M16 | behavioral | planned | fanout differential |
| TG-PAR-032 | Direct/group/room chat | chat | W6 | M10-M17 | behavioral | planned | RT + persistence differential |
| TG-PAR-033 | Chat history/update/delete | chat | W6 | M11-M18 | behavioral | planned | cursor and permission tests |
| TG-PAR-034 | Notifications persistent/realtime | notifications | W6 | M10-M17 | behavioral | planned | delivery and retry differential |
| TG-PAR-035 | Leaderboard definitions | competitive | W7 | M10-M15 | behavioral | planned | config differential |
| TG-PAR-036 | Leaderboard records/ranks/cursors | competitive | W7 | M11-M17 | behavioral | planned | rank/tie differential |
| TG-PAR-037 | Leaderboard reset scheduler/hooks | scheduler | W7 | M12-M18 | behavioral | planned | clock/race differential |
| TG-PAR-038 | Tournaments lifecycle and records | competitive | W7 | M11-M18 | behavioral | planned | API/scheduler differential |
| TG-PAR-039 | Matchmaker query grammar | matchmaking | W8 | M12-M18 | query-exact | planned | golden and malicious corpus |
| TG-PAR-040 | Matchmaker ticket pool and matching | matchmaking | W8 | M13-M21 | behavioral | planned | pool replay differential |
| TG-PAR-041 | Matchmaker processor/matched hooks | runtime/matchmaking | W8 | M15-M22 | behavioral | planned | hook ordering differential |
| TG-PAR-042 | Realtime party lifecycle | party | W8 | M13-M20 | behavioral | planned | model/oracle tests |
| TG-PAR-043 | Party matchmaking | party/matchmaking | W8 | M15-M22 | behavioral | planned | atomic group tests |
| TG-PAR-044 | Relayed match lifecycle | multiplayer | W9 | M14-M19 | behavioral | planned | RT differential |
| TG-PAR-045 | Relayed match data routing | multiplayer | W9 | M15-M21 | behavioral/performance | planned | recipient/latency tests |
| TG-PAR-046 | Authoritative match lifecycle | multiplayer | W10 | M15-M23 | behavioral | planned | callback differential |
| TG-PAR-047 | Authoritative tick scheduler | multiplayer | W10 | M17-M25 | behavioral/performance | planned | drift/overrun/load tests |
| TG-PAR-048 | Match signal/list/get/placement | multiplayer | W10 | M18-M27 | behavioral | planned | API/fencing differential |
| TG-PAR-049 | Rust native runtime SDK | runtime | W11 | M12-M22 | functional | planned | SDK conformance |
| TG-PAR-050 | WASM runtime component ABI | runtime | W11 | M14-M25 | functional/security | planned | capability/fuel tests |
| TG-PAR-051 | Lua runtime compatibility | runtime | W11 | M16-M29 | behavioral | planned | module corpus differential |
| TG-PAR-052 | JavaScript/TypeScript runtime compatibility | runtime | W11 | M16-M30 | behavioral | planned | module corpus differential |
| TG-PAR-053 | Runtime hooks and RPCs | runtime | W11 | M14-M28 | behavioral | planned | registration/order differential |
| TG-PAR-054 | Runtime module APIs | runtime | W11 | M15-M30 | behavioral | planned | function denominator differential |
| TG-PAR-055 | Go module source migration toolkit | runtime migration | W11 | M18-M30 | migration | planned | real module port evidence |
| TG-PAR-056 | Apple purchase/subscription validation | IAP | W12 | M18-M25 | behavioral/security | planned | sandbox + signed fixtures |
| TG-PAR-057 | Google purchase/subscription validation | IAP | W12 | M18-M25 | behavioral/security | planned | sandbox + notification tests |
| TG-PAR-058 | Huawei/Facebook/Steam/Samsung validation | IAP | W12 | M20-M28 | behavioral/security | planned | provider fixture matrix |
| TG-PAR-059 | IAP persistence/refund/void semantics | IAP | W12 | M20-M28 | behavioral | planned | ambiguous outcome tests |
| TG-PAR-060 | Console API | console | W13 | M20-M29 | behavioral | planned | endpoint/RBAC differential |
| TG-PAR-061 | Rust/WASM Console UI | console | W13 | M22-M32 | functional/accessibility | planned | workflow/screenshot tests |
| TG-PAR-062 | Console RBAC/ACL/audit | console/security | W13 | M21-M31 | behavioral/security | planned | permission matrix |
| TG-PAR-063 | Nakama schema reader/importer | migration | W14 | M18-M27 | data-exact | planned | round-trip fixtures |
| TG-PAR-064 | Online backfill/change capture | migration | W14 | M22-M32 | data-exact | planned | restart/idempotency tests |
| TG-PAR-065 | Dual-read comparator and receipts | migration | W14 | M22-M34 | semantic | planned | full dataset validation |
| TG-PAR-066 | Prometheus metrics compatibility | observability | W15 | M8-M24 | behavioral | planned | metric name/label contract |
| TG-PAR-067 | Structured logs/tracing/redaction | observability | W15 | M6-M24 | functional/security | planned | fault/redaction tests |
| TG-PAR-068 | Backup/PITR/restore | operations | W15 | M20-M36 | operational | planned | independent restore drill |
| TG-PAR-069 | Key/certificate/provider secret rotation | security | W15 | M18-M34 | operational/security | planned | live rotation drill |
| TG-PAR-070 | Single-node performance parity | performance | W16 | M24-M36 | performance | planned | oracle benchmark |
| TG-PAR-071 | Multi-node HA and fencing | HA | W16 | M26-M40 | operational | planned | chaos/failover evidence |
| TG-PAR-072 | Official SDK compatibility matrix | compatibility | W2/W16 | M6-M42 | wire-and-behavior | planned | cross-SDK black-box |
| TG-PAR-073 | Nakama shadow/canary/cutover | migration | W16 | M30-M46 | operational | planned | cohort and rollback evidence |
| TG-PAR-074 | Nakama retirement and archival verification | retirement | W16 | M40-M48 | operational | planned | zero-owner/key-revocation evidence |

## Status rules

- `planned` means no accepted implementation claim.
- A row may move to `implemented` only with Rust code and focused tests.
- A row may move to `verified-differential` only with an exact Nakama oracle baseline and zero unexplained P0/P1 divergence.
- A row may move to `production` only after migration, security, load, operations and rollback gates pass.
- Go plugin binary ABI is not represented as a parity row; real Go modules are migrated under `TG-PAR-055`.
