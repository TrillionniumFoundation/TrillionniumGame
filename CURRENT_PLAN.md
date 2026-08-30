# TrillionniumGame 全量 Rust 重写开发计划 v3

状态：**执行控制基线；所有兼容性与生产声明继续 fail closed**  
生效日期：2026-08-29  
项目 ID：`trillionnium-game`  
权威仓库：`TrillionniumFoundation/TrillionniumGame`  
仓库 ID：`1323087470`  
本版审计起点：`main@326e670cb008a990247e31a63c0c4b0e338df62f`  
首个兼容基线：Nakama OSS `v3.40.0` / `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`  
协议与 Runtime 基线：nakama-common `v1.47.0` / `449b77ecc8789aa466c36b67f6e498033dfcd9c5`  
目标版本：`TrillionniumGame 1.0`  
长期置信区间：**P50 48 个月，P80 60 个月**；峰值 **28–36 FTE**

## 0. v3 的核心变化

v2 建立了完整范围、D0–D8 分母、C0–C5 声明、W0–W16 工作流、SG0–SG9 阶段门、120 项长期 backlog 和 evidence schema。v3 不缩减这些内容，而是把它们从静态规划包升级为可持续执行系统：

1. **规划与状态分离**：压缩 backlog 继续作为不可变范围基线；任务实际状态写入 `docs/status/EXECUTION_STATUS.json`，不再通过修改范围文件表示进度。
2. **门禁从证据派生**：`docs/evidence/index.json` 是证据登记入口；`scripts/derive-gates.py` 只根据有效、未过期、目标 commit/tree 精确匹配且已独立审核的证据计算 gate。
3. **Gap 可关闭**：`docs/status/GAP_REGISTER.json` 为所有 P0/P1/P2 缺口提供 owner、blocking claims、close criteria、evidence 和外部依赖；“已写代码”不等于 gap closed。
4. **当前状态可审计**：`docs/status/CURRENT_STATE.json` 记录权威 commit、仓库治理、当前运行拓扑、声明边界和已知 blocker。
5. **数据库只有一个权威**：`migrations/` 是 production-authoritative migration chain；`database/schema/v2/` 只能作为非运行设计参考，禁止被服务、CI、备份或发布工具消费。
6. **优先纵向集成**：在继续横向扩展领域前，先交付一个可启动、可迁移、可接受请求、可提交事务、可投递 outbox、可重启和可差分的 Rust server 纵向切片。
7. **合并门统一**：所有代码、计划、状态和证据变更由 `trillionnium-game-merge-gate` 聚合；空、缺失、跳过、取消或非当前 head 的 check 一律不是通过。

## 1. 不变使命与范围

使用 Rust 全量重写 Nakama OSS 游戏后端服务器，而不是只覆盖 Trillionnium 当前调用到的功能。1.0 分母包括：

- server bootstrap、typed config、CLI、migrate、health、readiness、graceful shutdown；
- HTTP/JSON API v2、gRPC、WebSocket JSON/protobuf；
- authentication providers、sessions、accounts、link/unlink、wallet/metadata；
- storage、ACL、OCC、cursor、search/index；
- friends、groups、chat、notifications、presence、streams；
- leaderboards、tournaments、schedulers；
- matchmaker、parties、relayed matches、authoritative matches；
- Runtime RPC/hooks/module APIs、Rust SDK、Lua、JavaScript/TypeScript；
- IAP/subscriptions/provider callbacks；
- Console API、RBAC、audit 和 Rust/WASM UI；
- PostgreSQL/CockroachDB 独立 profiles；
- metrics/logs/traces、security、backup、PITR、HA、upgrade；
- existing Nakama data/module migration、shadow、canary、cutover 和 retirement。

明确不属于 1.0：Heroic Cloud 托管控制面、非公开 Enterprise 实现、Satori/Hiro 产品本体、官方客户端 SDK 源码重写、World gameplay、Chain finality、CEX ledger、已编译 Go plugin ABI。

最终生产拓扑不得包含第一方 Go server、Go sidecar 或 compiled Go plugin loader。当前 `runtime/` 中的 Go plugin 是迁移输入、兼容 oracle 和现行业务夹具，不是目标 Rust server 的完成证据。

## 2. 当前事实与声明边界

截至本版审计起点：

- Rust authority、session、storage、persistence/outbox、canonical framing、transport error、token policy、query 和 presence 等核心状态机已有 source-level candidates；
- PostgreSQL/CockroachDB 单节点 schema、事务失败回滚、部分响应丢失重放和备份后空库语义恢复已有局部证据；
- 根工作区仍没有可作为 Nakama 替代品运行的 Rust server binary；
- D0–D8 最终 leaf manifests 尚未全部分类、审核和锁定；
- 本仓库当前 head 的原生 GitHub Actions、required checks 和 main ruleset 尚未形成可信闭环；
- C1–C5、SG1–SG9、production-ready、public-online、drop-in replacement 和 Nakama retired 均为 false。

任何文档、PR、release、Console 或 API 必须与 `docs/status/CURRENT_STATE.json` 和派生 gate 结果保持一致。

## 3. 控制面数据模型

### 3.1 不可变范围层

- `docs/development/EXECUTION_BACKLOG.json`
- `docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz`
- `docs/development/FEATURE_PARITY_MATRIX.md`
- `docs/development/PARITY_DENOMINATORS.json`
- `docs/development/UPSTREAM_BASELINE.json`

这些文件定义长期范围与基线，不用于手工维护日常进度。

### 3.2 可变执行层

- `docs/status/EXECUTION_STATUS.json`：任务、workstream、stage gate 的当前状态与阻塞；
- `docs/status/GAP_REGISTER.json`：所有已知缺口及关闭合同；
- `docs/status/CURRENT_STATE.json`：当前权威状态快照；
- `docs/status/IMPLEMENTATION_INVENTORY.json`：代码路径到 capability、test、evidence、claim 的映射；
- `docs/roadmap/NEXT_MILESTONE.json`：近期唯一执行队列。

允许的任务状态：

```text
planned -> ready -> in-progress -> source-candidate -> locally-verified
        -> remote-verified -> independently-reviewed -> accepted
```

还允许 `blocked`、`rejected` 和 `superseded`。状态晋级必须由 `scripts/check-status-transitions.py` 验证；禁止从代码量、commit 数或 PR 数推断进度。

### 3.3 证据与派生层

- `docs/evidence/index.json` 登记证据；
- `docs/evidence/schemas/trillionnium-evidence-v1.schema.json` 定义证据合同；
- `scripts/derive-gates.py` 派生 product/stage gate；
- `docs/status/PRODUCT_GATES.json` 是派生输出或 fail-closed 快照，不得手工越级修改。

证据必须绑定 exact repository、commit、tree、artifact digest、environment、fixtures、commands、result、limitations、expiry 和 independent review。Relay 可以执行重型测试，但目标仓库必须验证 relay manifest 确实指向当前 candidate。

## 4. 兼容声明等级

| 等级 | 声明 | 最低证据 |
| --- | --- | --- |
| C0 | planning/build/schema candidate | pinned baseline、可复现 build、范围和状态控制面 |
| C1 | wire-compatible subset | exact HTTP/gRPC/RT bytes、status、headers、disconnect differential |
| C2 | behavior-compatible domain | C1 + DB effects、hooks、concurrency、faults、restart |
| C3 | data-migration compatible | repeatable migration、semantic validation、rollback barrier |
| C4 | operationally replaceable | HA、security、capacity、backup/PITR、upgrade、runbooks |
| C5 | supported full replacement | all mandatory leaves production、Go source migration、cutover/retirement complete |

声明是 capability/profile scoped；某个 API 的 C1 不能提升全仓库到 C1。任何 promotion 必须列出 denominator leaf IDs 和 evidence IDs。

## 5. Parity denominator D0–D8

`FEATURE_PARITY_MATRIX.md` 的 74 行是人工 roll-up，不是完成率分母。SG1 必须从 pinned source 生成并审核：

- D0：repository、release、commit、tree、blob、image、toolchain identity；
- D1：HTTP/gRPC methods、routes、messages、enums、JSON mapping；
- D2：realtime messages、envelopes、CID、errors、socket lifecycle；
- D3：Console methods、ACL actions、operator workflows；
- D4：Runtime initializers、hooks、contexts、match interface、module APIs；
- D5：config keys、defaults、validation、precedence、env mapping、CLI flags、exit codes；
- D6：migrations、tables、columns、constraints、indexes、sequences、data invariants；
- D7：metrics、health、readiness、logs、packaging、startup/shutdown、diagnostics；
- D8：auth/social/IAP providers、states、callbacks、retry and value effects。

每个 leaf 必须具有 source blob、signature hash、owner、task、test、compatibility profile、status 和 evidence refs。分母 decrease、merge 或 ignore 必须有 upstream delta 或 ADR；平台扩展使用 `TG-EXT-*`，不能提高 parity coverage。

## 6. Oracle 与差分

保留两条 oracle lane：

1. **immutable oracle**：未经修改的官方 Nakama artifact；
2. **instrumented oracle**：仅为时钟、随机数、provider、DB 和 trace 捕获做最小审计补丁。

插桩 oracle 必须证明注入字段之外与 immutable oracle 等价。两端使用同一 seed 克隆出的隔离数据库，禁止共享可写数据库。

差分至少捕获：wire bytes、HTTP/gRPC/RT status、headers、disconnect reason、DB rows/invariants、events、hooks、logs/metrics、external effect intent/receipt。Normalizer 只允许登记的 non-contract fields；identity、authorization、ACL、sequence、money、version、cursor、error code 和 durable effect 永远禁止 normalize。

## 7. 唯一数据库与数据权威

### 7.1 Schema authority

- `migrations/postgresql/` 与 `migrations/cockroachdb/` 是唯一 production-authoritative DDL chain；
- `crates/trnm-persistence-pg` 的 SQL ABI 必须与该 chain 同版本；
- backup/restore、catalog introspection、fault matrix 和 migration evidence 必须引用同一个 migration digest；
- `database/schema/v2/` 标记为 non-authoritative design history，不得被 runtime、CI、release 或 backup tooling 消费；
- 任何字段、ID 类型、tenant boundary、time representation、outbox lease 或 receipt 语义的改变必须走 expand/contract ADR。

### 7.2 Migration authority

迁移状态：

```text
nakama_primary
 -> rust_shadow_no_effect
 -> rust_canary_new_entities
 -> rust_primary_new_entities
 -> nakama_read_only
 -> nakama_retired
```

session refresh family、party、ticket、match、scheduler、IAP transaction 和 durable command 在任一时刻只有一个 writer。Shadow 不签发 token、不入真实 pool、不广播、不结算、不写权威状态。API handler 禁止同步双写；允许 source transaction 写 immutable outbox/CDC，由 target 幂等 apply。

## 8. Rust server 纵向切片

在继续大规模横向领域开发前，必须交付 `trnm-server` 最小纵向切片：

```text
config + CLI + migrate
 -> process bootstrap
 -> health/readiness/shutdown
 -> one HTTP/gRPC endpoint
 -> one WebSocket JSON/protobuf request
 -> session verification
 -> authority transition
 -> SERIALIZABLE DB transaction
 -> transactional outbox
 -> acknowledgement after commit
 -> restart/retry/reconnect
 -> metrics/traces
 -> immutable Nakama differential
```

最小切片必须：

- 使用 bounded queues、deadlines、cancellation 和 ownership generations；
- 没有外部 I/O 处于可变数据库事务内；
- commit 成功但响应丢失时重放 exact receipt；
- 对 PostgreSQL 与 CockroachDB 分别运行；
- 没有数据库环境时，required live lane 必须失败，不得静默 skip；
- 对 current PR head 生成 evidence artifact。

## 9. 安全与密码边界

- JWT、HMAC、hash、base64url 和 constant-time primitive 必须由经过审查的库或独立安全审核的 compatibility adapter 提供；
- access、refresh、console、runtime、socket 和 authority keys 必须分域；
- legacy 无 `kid` token 与 epoch token 分路，未知 epoch 不得 fallback；
- key material 不写日志、不进入 core dump evidence、不作为普通 Debug 输出；
- rotation、emergency revoke、refresh-family replay、socket disconnect 和 KMS/HSM adapter 必须有 runbook；
- security-critical crates 即使暂时独立 workspace，也必须进入统一 merge gate、dependency、license、fuzz 和 review 边界。

具体合同见 `docs/security/CRYPTOGRAPHY_AND_KEYS.md` 和 `SECURITY.md`。

## 10. 仓库治理与合并门

目标规则：

- 禁止直接 push `main`；
- required check：`trillionnium-game-merge-gate`；
- 当前 PR head 必须有 non-empty、terminal、successful run；
- 至少一名独立 reviewer；安全、数据库、协议、realtime 路径由 CODEOWNERS 指定额外审核；
- approval 在 head 变化后失效；
- merge queue 或 update-before-merge；
- signed commit/release provenance；
- empty/skipped/cancelled/absent checks 视为 failure to prove。

GitHub 组织/仓库设置若不能由代码完成，必须保留为 `external-admin` gap，附所需权限、API observation 和精确 acceptance；不得因为“已经写 workflow”就关闭。

## 11. Stage gates

| Gate | 目标 | v3 退出条件 |
| --- | --- | --- |
| SG0 Repository Adoption | governance ready | repository identity/history/refs verified；current-head CI non-empty；ruleset/review policy active |
| SG1 Denominator Lock | exact scope | D0–D8 manifests non-empty；100% owner/task/test/profile classification；zero unclassified |
| SG2 Oracle Reproducibility | trusted oracle | immutable/instrumented lanes；10 次 normalized hash 一致；normalizer independently reviewed |
| SG3 Architecture Feasibility | risk retired | JS/Lua/runtime/query/DB/Console/migration spikes 与 ADR complete |
| SG4 Foundation Alpha | vertical slice | Rust server skeleton、config/CLI/API/socket/DB/outbox/restart R1 evidence |
| SG5 Core Services Differential | core C2 | identity/storage/social/competitive mandatory leaves verified |
| SG6 Realtime and Runtime Alpha | distributed C2 | presence/chat/party/matches/runtime isolation/failover evidence |
| SG7 Full Feature Beta | feature complete | IAP/Console/migration complete；1.0 final upstream baseline frozen |
| SG8 Migration RC | replaceable candidate | C3/C4、shadow zero unexplained P0/P1、restore/rollback/HA/security |
| SG9 Production Cutover | supported release | canary/endurance/SLO/incident/retirement 全通过 |

任何 gate 失败保持当前 authority 和 claim。Identity、money、ACL、durability、single-owner 或 data corruption 问题不得 waiver 获得 production credit。

## 12. W0–W16 工作流

### W0 — Governance and upstream truth source
完成 repository settings、D0–D8 extractors、evidence index、branch governance、license/source registry、upstream delta lane。

### W1 — Foundation, config, CLI and migrations
交付 workspace、typed config、CLI、唯一 migration authority、PostgreSQL/CockroachDB repositories、observability、shutdown/readiness。

### W2 — HTTP, gRPC and realtime protocol core
交付 pinned protobuf/OpenAPI、transcoding、gRPC、WebSocket JSON/protobuf、limits、heartbeat、compression、error/disconnect、SDK matrix。

### W3 — Authentication, sessions and accounts
交付 providers、link/unlink、session issue/refresh/logout/revoke、accounts、wallet/metadata、ban 和 socket revocation。

### W4 — Storage engine and search
交付 Nakama-exact public content version、internal integrity digest、ACL/OCC/cursor、query/index、rebuild/lag。

### W5 — Friends, groups and social graph
交付 friend/group state machines、roles/requests/bans、imports、notifications、deletion cleanup。

### W6 — Presence, streams, chat and notifications
交付 socket ownership、streams/status、chat/history、notification delivery、bounded fanout、routing/fencing/reconnect。

### W7 — Leaderboards and tournaments
交付 definitions、records、ranks、cursors、reset/end schedulers、hooks、rewards。

### W8 — Matchmaker and parties
交付 query parser、ticket pool、matching、processor hooks、party lifecycle/leadership、atomic party matchmaking。

### W9 — Client-relayed multiplayer
交付 create/join/leave/list、tokens、presence、data routing、recipient filtering、node route、cleanup。

### W10 — Server-authoritative multiplayer
交付 lifecycle callbacks、fixed tick、dispatcher、signals、termination、generation fencing、placement、resource limits、snapshots。

### W11 — Runtime framework and module migration
交付 Runtime denominator、Rust SDK、WASM、Lua/JS profiles、RPC/hooks/module APIs/jobs、module ordering、Go source migration。

### W12 — IAP and subscriptions
交付 provider validation、persistence、refund/void/renewal、notifications、idempotent effects、timeouts/circuit breakers/key rotation。

### W13 — Console API and Rust/WASM UI
交付 Console methods、auth/RBAC/MFA、audit、dangerous-action approval、大数据工作流和 accessibility。

### W14 — Data migration and compatibility
交付 schema introspection、snapshot/backfill/CDC、receipts、semantic comparator、write fence、rollback exporter、active entity disposition。

### W15 — Observability, security and operations
交付 telemetry、redaction/privacy、rate/abuse、TLS/mTLS、key rotation、backup/PITR、supply chain、fuzz/pentest、incident runbooks。

### W16 — Performance, HA, release and retirement
交付 oracle benchmark、capacity profiles、multi-node failover/fencing、rolling upgrades、shadow/canary、24h/72h/7d endurance、cutover/retirement。

## 13. 当前 blocker 闭合顺序

### Wave 0 — Control-plane integrity

1. current-head Actions 与 ruleset；
2. v3 status/gap/evidence control files；
3. dynamic plan/status/gate validators；
4. CODEOWNERS、SECURITY 和 merge policy；
5. 修复 stale repository/module identity。

### Wave 1 — Data and security authority

1. 选择唯一 migration chain并阻止第二权威被消费；
2. 修复 outbox retry terminal transition；
3. 修复 constant-time comparison 长度处理；
4. 将 security-critical adapter 纳入统一 merge gate；
5. 独立 database/security review。

### Wave 2 — Rust vertical slice

1. `trnm-server` binary bootstrap；
2. config/CLI/migrate/health/readiness/shutdown；
3. one protocol + one realtime path；
4. session + transaction + outbox + restart；
5. exact oracle differential and evidence。

### Wave 3 — SG1/SG2 completion

完成所有 denominator 分类、source lock、oracle reproducibility、normalizer review 和 SDK consumer matrix。

只有 Wave 0–3 完成后，才允许大规模并行扩展 W3–W13。

## 14. Definition of Ready / Done

### Definition of Ready

一个任务只有在以下内容齐全后才可从 `planned` 进入 `ready`：

- exact scope、owner、independent review roles；
- denominator/parity/gate/gap IDs；
- upstream source identity；
- API/data/error/concurrency/security contracts；
- test matrix、environment、fixtures；
- migration、rollback、observability 和 resource budget；
- dependencies、estimate、risk 和 evidence output path。

### Definition of Done

一个任务只有在以下内容全部成立后才可进入 `accepted`：

- source code、schema、tests、docs 和 status 同步；
- format/test/lint/fuzz/static checks 对 exact head 成功且 non-empty；
- live/differential/fault lanes按任务要求完成；
- evidence manifest schema-valid、artifact digest verified；
- 无未解释 P0/P1 divergence；
- limitations 和 residual risks 明确；
- independent reviewer accepted；
- gate/claim 由脚本派生，而非手工声明。

## 15. Gap closure definition

一个 gap 只能在以下条件下关闭：

1. close criteria 每项均有 assertion；
2. implementation commit/tree 精确记录；
3. required tests non-empty、terminal、successful；
4. artifact digest 和 evidence ID 已登记；
5. 所有 blocking dependencies closed；
6. 无未解释 P0/P1 子 gap；
7. independent reviewer 决策为 accepted；
8. 若需外部管理员操作，GitHub/API 状态已实际改变并重新读取验证。

`implemented`、`documented`、`local-pass`、`workflow-added` 或 `issue-closed` 单独都不能代表 gap closed。

## 16. 进度、容量与资源

每四周发布：

- denominator specified/implemented/verified/production coverage；
- task 状态 burn-up 与 accepted lead time；
- open/ageing P0/P1 gaps 与 divergences；
- evidence freshness、expiry 和 rerun backlog；
- critical-path P50/P80、FTE/cost variance；
- defect escape、flaky/skip/waiver；
- SLO、capacity、cost per CCU/request/match-hour/GB-month；
- upstream delta backlog。

禁止用 LOC、crate 数、commit 数、PR 数或未加权 task 数表示完成率。SG1 与 SG3 后必须基于真实 leaf count、spike、velocity 和 defect data 重估 P50/P80。

## 17. 近期唯一执行队列

`docs/roadmap/NEXT_MILESTONE.json` 是当前唯一近期队列。其目标不是宣称全项目完成，而是闭合以下可验证前置条件：

- v3 控制面一致；
- P0 CI/governance blocker 有真实状态或精确 external-admin 证据；
- schema authority 单一；
-已确认的 crypto/outbox 缺陷修复；
-安全关键适配器进入统一检查；
- Rust vertical slice 的 architecture、interfaces、test/evidence contract ready。

任何新增工作必须先证明不会绕过这个队列的 critical path。

## 18. 最终验收

TrillionniumGame 1.0 只有在以下全部成立后才完成：

1. 最终冻结 upstream 的全部 mandatory D0–D8 leaves 达到生产要求；
2. 官方 SDK、HTTP/gRPC/WS JSON/protobuf、Runtime、Console、providers 和 IAP 在声明 profile 内通过；
3. Nakama 数据和 Go module source 已迁移，migration/rollback/restore 证据有效；
4. PostgreSQL 与 CockroachDB 各自满足支持声明；
5. security、privacy、performance、HA、upgrade、backup/PITR 和 endurance gates 通过；
6. shadow/canary 无未解释 P0/P1，SLO 满足，rollback rehearsal 完成；
7. Nakama 已无新流量、active authority、private keys 或未处置数据；
8. C5 与 release claim 由证据自动派生并经独立发布委员会批准。

在此之前，项目始终保持诚实边界：**candidate 可以存在，局部证据可以成立，但完整兼容、生产就绪和替代声明必须保持 false。**
