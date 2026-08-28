# TrillionniumGame 全量 Rust 重写开发计划 v2

状态：**审计后可执行规划基线**  
生效日期：2026-08-28  
项目 ID：`trillionnium-game`  
当前远端：`TrillionniumFoundation/Trillionnium-Nakama`  
目标名称：`TrillionniumFoundation/TrillionniumGame`  
首个兼容基线：Nakama OSS `v3.40.0` / `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`  
协议/Runtime 基线：nakama-common `v1.47.0` / `449b77ecc8789aa466c36b67f6e498033dfcd9c5`  
目标版本：`TrillionniumGame 1.0`  
计划置信区间：**P50 48 个月，P80 60 个月**；峰值 **28–36 FTE**

## 0. v2 修订目的

v1 正确地锁定了“完整 Nakama OSS server parity”和“最终无第一方 Go server”两个核心目标，但仍是战略计划：74 条 parity 行只是领域汇总，119 项 task 缺乏依赖、负责人、估算、证据和回滚字段，发布门缺少机器可判定条件。

v2 将计划升级为项目控制系统：

- 人类可读 matrix 与机器 leaf denominator 分离；
- task 具备依赖、角色、估算、deliverables、tests、risks、gates 和 evidence paths；
- 引入 C0–C5 compatibility claim taxonomy；
- 建立未修改 oracle、插桩 oracle、normalizer registry 和差分严重度；
- 为 session、party、match、IAP、scheduler 等定义迁移唯一权威；
- 建立 P50/P80、关键路径、stage gates、SLO、容量 profile 和成本指标；
- 在前 12 周完成 Runtime、数据库、查询、Console 和在线迁移技术尖峰；
- 以不可变 evidence contract 关闭产品门禁。

完整审计见 `docs/development/PLAN_AUDIT_2026-08-28.md`。

## 1. 不变使命与范围

使用 Rust 全量重写 Nakama OSS 游戏后端服务器，而不是只覆盖 Trillionnium 当前调用到的功能。1.0 分母包含：

- server bootstrap、config、CLI、migrate、health/readiness、shutdown；
- HTTP/JSON API v2、gRPC、WebSocket JSON/protobuf；
- authentication providers、sessions、accounts、link/unlink、wallet/metadata；
- storage、ACL、OCC、cursor、search/index；
- friends、groups、chat、notifications、presence、streams；
- leaderboards、tournaments、schedulers；
- matchmaker、parties、relayed matches、authoritative matches；
- Runtime RPC/hooks/module APIs、Rust SDK、Lua、JavaScript/TypeScript；
- IAP/subscriptions/provider callbacks；
- Console API、RBAC、audit 和 Rust/WASM UI；
- PostgreSQL/CockroachDB profiles；
- metrics/logs/traces、security、backup、HA、upgrade；
- existing Nakama data/module migration、shadow、canary、cutover 和 retirement。

明确不属于 1.0：Heroic Cloud 托管控制面、非公开 Enterprise 实现、Satori/Hiro 产品本体、官方客户端 SDK 源码重写、World gameplay、Chain finality、CEX ledger、已编译 Go plugin ABI。

最终生产拓扑不包含第一方 Go server、Go sidecar 或 compiled Go plugin loader。现有 Go module 必须迁移到 Rust/WASM。该限制意味着 C5 声明是“支持范围内的协议、数据、功能、Runtime source migration 和运维替换”，而非 Go 二进制 ABI 替换。

## 2. 兼容声明等级

| 等级 | 声明 | 最低证据 |
| --- | --- | --- |
| C0 | planning/spec only | pinned baseline、denominator plan |
| C1 | wire-compatible subset | exact HTTP/gRPC/RT differential |
| C2 | behavior-compatible domain | wire + DB effects + hooks + concurrency |
| C3 | data-migration compatible | repeatable migration、semantic validation、rollback |
| C4 | operationally replaceable | HA、security、capacity、backup、upgrade、runbooks |
| C5 | supported full replacement | all mandatory leaves production、Go sources migrated、cutover/retirement complete |

任何文档、README、release、Console 或 API 不得越级使用 `drop-in`、`production-ready` 或 `replacement`。

## 3. Parity denominator

`FEATURE_PARITY_MATRIX.md` 的 74 行是人工导航 roll-up，不是完成率分母。SG1 必须从 pinned source 生成以下 leaf manifests：

- D0 upstream identities；
- D1 HTTP/gRPC methods/routes/messages/enums/JSON mapping；
- D2 realtime messages/envelopes/errors/lifecycle；
- D3 Console methods/ACL/workflows；
- D4 Runtime initializers/hooks/functions/context/module APIs；
- D5 config keys/defaults/validation/CLI flags/exit codes；
- D6 migrations/tables/columns/constraints/indexes/sequences/invariants；
- D7 metrics/health/logging/packaging/shutdown/operations；
- D8 provider/IAP states/callbacks/retry semantics。

每个 leaf 必须具有 source blob、signature hash、owner、task、test、compatibility profile、status 和 evidence refs。分母项不得因“未使用”被删除；任何 remove/merge 必须有 upstream delta 或 ADR。

## 4. Oracle 与差分

保留两条 oracle lane：

1. **immutable oracle**：未经修改的官方 Nakama artifact；
2. **instrumented oracle**：仅用于时钟、随机数、provider、DB 和 trace 捕获的最小审计补丁。

插桩 oracle 必须证明在注入字段之外与 immutable oracle 一致。两端使用由同一 seed 克隆出的隔离数据库，禁止共享可写数据库。

差分捕获：wire bytes、HTTP/gRPC/RT status、headers、disconnect reason、DB row/invariant changes、events、hooks、logs/metrics，以及外部 effect intent/receipt。

Normalizer 只允许处理登记的 non-contract fields。user ID、authorization、ACL、sequence、amount、version、cursor、error code、durable effect 等字段永远禁止 normalize。

## 5. 迁移唯一权威

迁移状态：

```text
nakama_primary
 -> rust_shadow_no_effect
 -> rust_canary_new_entities
 -> rust_primary_new_entities
 -> nakama_read_only
 -> nakama_retired
```

- session refresh family、party、ticket、match、scheduler definition、IAP transaction 和 durable command 在任一时刻只有一个 writer；
- shadow 不签发 token、不入真实 pool、不广播、不结算、不写权威状态；
- API handler 不得同步双写两套业务系统；允许 source transaction 写 immutable outbox/CDC，由 target 幂等 apply；
- active party/ticket/match 默认 drain，不进行未经版本化证明的热迁移；
- rollback 只重新路由新实体；已在 Rust 中创建的 active entity 不跨权威来回切换。

## 6. 执行模型

四层规划：

1. parity leaf；
2. 2–12 周可关闭 task；
3. W0–W16 workstream；
4. SG0–SG9 stage gate。

禁止以 LOC、crate 数、PR 数或未加权 task 数计算完成率。进度报告必须包含：specified/implemented/verified/production leaf coverage、open P0/P1 divergences、critical-path forecast、defect escape、performance budget、migration coverage、security finding age、evidence freshness、FTE/cost variance 和 upstream delta。

## 7. Stage gates

| Gate | 目标 | 退出条件 |
| --- | --- | --- |
| SG0 Repository Adoption | W0–W2 | history/refs retained、plan landed、rename evidence、governance owner |
| SG1 Denominator Lock | M0–M3 | D0–D8 manifests、100% owner/task/test classification |
| SG2 Oracle Reproducibility | M1–M4 | immutable/instrumented oracle、10 次 normalized hash 一致 |
| SG3 Architecture Feasibility | M2–M6 | JS/Lua/runtime/query/DB/Console/migration spikes 与 ADR |
| SG4 Foundation Alpha | M4–M9 | config/CLI/API/socket/DB skeleton 与 R1 evidence |
| SG5 Core Services Differential | M9–M18 | identity/storage/social/competitive C2 mandatory leaves |
| SG6 Realtime and Runtime Alpha | M16–M30 | presence/chat/party/matches/runtime C2 + isolation |
| SG7 Full Feature Beta / Final Upstream Freeze | M26–M36 | IAP/Console/migration complete，冻结 1.0 final upstream |
| SG8 Migration RC | M32–M48 | C3/C4、shadow zero unexplained P0/P1、restore/rollback |
| SG9 Production Cutover | M42–M60 | security/perf/HA/endurance/canary 全通过，完成 retirement |

任何 gate 失败都会保持当前 authority 和 claim，不得通过 waiver 绕开身份、金额、权限、持久化、双权威或数据损坏问题。

## 8. 关键路径

```text
repository adoption
 -> machine denominator
 -> reproducible oracle
 -> protocol/config/data primitives
 -> identity + storage
 -> realtime ownership
 -> runtime engine compatibility
 -> authoritative multiplayer
 -> full data migration
 -> shadow/canary
 -> retirement
```

Runtime engine、realtime ownership、data migration 和 cutover 是不可通过普通 API 人力线性压缩的瓶颈。

## 9. 工作流 W0–W16

### W0 — Governance and upstream truth source
锁定 source/protocol/database/config/runtime manifests；实现 extractors、oracle、license registry、branch governance 和 upstream delta lane。

### W1 — Foundation, config, CLI and migrations
Rust workspace、typed config、CLI、PostgreSQL/CockroachDB primitives、expand/contract migration、observability、shutdown/readiness。

### W2 — HTTP, gRPC and realtime protocol core
Pinned protobuf/OpenAPI、transcoding、gRPC、WebSocket JSON/protobuf、limits、heartbeat、compression、error/disconnect、SDK matrix。

### W3 — Authentication, sessions and accounts
全部公开 provider、link/unlink、session issue/refresh/logout/revoke、accounts、wallet/metadata、ban 和 socket revocation。

### W4 — Storage engine and search
Batch storage、ACL、OCC、server-owned objects、cursor、query grammar、index、rebuild/lag。

### W5 — Friends, groups and social graph
Friend/group state machines、roles/requests/bans、provider imports、notifications、deletion cleanup。

### W6 — Presence, streams, chat and notifications
Socket ownership、streams/status、chat/history、notification delivery、bounded fanout、routing/fencing。

### W7 — Leaderboards and tournaments
Definitions、records、ranks、cursors、reset/end schedulers、hooks、rewards。

### W8 — Matchmaker and parties
Query parser、ticket pool、matching、processor hooks、party lifecycle、leadership、atomic party matchmaking。

### W9 — Client-relayed multiplayer
Create/join/leave/list、tokens、presence、match data routing、recipient filtering、node route、cleanup。

### W10 — Server-authoritative multiplayer
Lifecycle callbacks、fixed tick、dispatcher、signals、termination、generation fencing、placement、resource limits、optional snapshots。

### W11 — Runtime framework and module migration
Runtime denominator、Rust SDK、WASM extension、Lua/JS compatibility、RPC/hooks/module APIs/jobs、module ordering、Go source migration。

### W12 — IAP and subscriptions
Provider validation、persistence、refund/void/renewal、notifications、idempotent effect model、timeouts/circuit breakers/key rotation。

### W13 — Console API and Rust/WASM UI
Console methods、auth/RBAC、domain APIs、audit、dangerous-action approval、large-data workflows、accessibility。

### W14 — Data migration and compatibility
Schema introspection、snapshot/backfill/CDC、receipts、semantic comparator、write fence、rollback exporter、active entity disposition。

### W15 — Observability, security and operations
Telemetry、redaction/privacy、rate/abuse、TLS/mTLS、key rotation、backup/PITR、supply chain、fuzz/pentest、incident runbooks。

### W16 — Performance, HA, release and retirement
Oracle benchmark、capacity profiles、multi-node failover/fencing、rolling upgrades、shadow/canary、24h/72h/7d endurance、cutover/retirement。

## 10. Risk-first technical spikes（前 12 周）

必须在扩大实现前完成：

- HTTP/gRPC JSON transcoding exactness；
- JWT/session/refresh/logout/socket revoke；
- JavaScript Goja corpus 对候选引擎；
- Lua GopherLua corpus 对候选引擎；
- WASM capability/fuel/memory model；
- 组织现有 Go module inventory；
- PostgreSQL/CockroachDB transaction/OCC/scheduler semantics；
- storage/group/matchmaker query architecture；
- WebSocket/presence/fanout at 100k synthetic sockets；
- authoritative tick scheduler isolation；
- online migration/write fence/rollback；
- Rust/WASM Console 大数据/RBAC/accessibility。

Spike 只能关闭 feasibility risk，不能获得 parity 或 production credit。No-Go 必须触发 ADR 和 P80 重估。

## 11. 容量、SLO 与成本

不使用孤立的“百万连接”作为默认承诺。环境选择 DEV、COMPAT、PROD-S、PROD-M 或 STRETCH profile，并绑定硬件、地域、DB、用户模型和预算。

必须量化：API availability/error budget、auth/storage/social p50/p95/p99、RT ingress-to-delivery、reconnect、ghost cleanup、tick jitter/overrun、matchmaker time-to-match/fairness、DB pool/txn retry/replication lag、index lag、RPO/RTO、cost per CCU/request/match-hour/GB-month。

性能对比以同硬件 pinned Nakama oracle 为基线；correctness、security 和 durability 不得为性能让步。

## 12. 计划与资源

- P50：48 个月；
- P80：60 个月；
- 峰值：28–36 FTE；
- 初始资源包络：950–1,350 person-months，加 25% contingency；
- M0–M3：8–12 FTE；M4–M9：16–22；M10–M18：24–32；M19–M30：28–36；M31–M42：24–32；M43–M60：14–24。

SG1/SG3 后必须以真实 leaf count、spike、velocity 和 defect data 重估。若超过包络，只能增加资源、延长时间或用 ADR 明确改变 profile；不得降低证据标准。

## 13. Product gates

所有门禁初始为 open：repository、scope、oracle、protocol、data、runtime、realtime、IAP、Console、privacy、security、performance、operations、upstream delta、cutover。

关闭 gate 的 evidence 必须包含：evidence ID、gate、source commit/tree、artifact digests、environment lock、commands、timestamps、result、limitations、reviewers、expiry。

## 14. 首 90 天

1. 保留 repository ID、history、branches、PR 和 Actions evidence；完成仓库重命名与治理快照；
2. 建 D0–D8 extractors 和 `denominator.lock.json`；
3. 建 immutable/instrumented Nakama oracle；
4. 完成 12 个技术尖峰及 ADR；
5. 建 Rust workspace、CI、DB/config/protocol skeleton；
6. 完成 device/custom auth、session refresh/logout、storage OCC、WebSocket handshake/presence 四个 vertical slices；
7. 建 differential harness v1 和 evidence schema；
8. 发布 SG1/SG2 评审，重新给出 P50/P80、预算和关键路径。

在 SG1–SG3 未通过前，不进行大规模 endpoint translation。

## 15. Definition of Ready / Done

Task Ready：owner、reviewers、dependencies、effort、parity IDs、gates、risks、deliverables、acceptance、rollback 和 evidence paths 完整。

Task Done：Rust implementation；unit/property/fuzz；oracle differential；concurrency/retry/failure；metrics/logs/traces；security/privacy；migration/rollback；current docs；exact commit CI evidence；residual limitations。仅有代码、happy path、本机成功或人工截图不能关闭 task。

## 16. 规范入口

- `docs/development/PLAN_AUDIT_2026-08-28.md`
- `docs/development/PARITY_DENOMINATOR_SPEC.md`
- `docs/development/ORACLE_AND_DIFFERENTIAL_SPEC.md`
- `docs/development/COMPATIBILITY_PROFILES.md`
- `docs/development/PROGRAM_EXECUTION_MODEL.md`
- `docs/development/CRITICAL_PATH_AND_STAGE_GATES.md`
- `docs/development/MIGRATION_AUTHORITY_MATRIX.md`
- `docs/development/DATA_MIGRATION_STATE_MACHINE.md`
- `docs/development/CAPACITY_AND_SLO_SPEC.md`
- `docs/development/TECHNICAL_SPIKES.md`
- `docs/development/EVIDENCE_MODEL.md`
- `docs/status/PRODUCT_GATES.json`
- `docs/status/RISK_REGISTER.json`
- `docs/status/SERVICE_LEVEL_OBJECTIVES.json`
- `docs/development/backlog/`
