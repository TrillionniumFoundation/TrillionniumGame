# TrillionniumGame 全量 Rust 重写开发计划 v3.1

状态：**当前执行计划；所有兼容性、生产与退役声明继续 fail closed**  
文档修订：2026-09-01  
机器计划版本：`3`  
项目 ID：`trillionnium-game`  
权威仓库：`TrillionniumFoundation/TrillionniumGame`  
目标版本：`TrillionniumGame 1.0`  
长期规模基线：**P50 48 个月，P80 60 个月，峰值 28–36 FTE**

本文件是唯一的人类可读执行计划。文档导航、架构、开发、兼容性、测试、安全、运维、治理和路线图分别由 [`docs/README.md`](docs/README.md) 下的当前主题文档承担；机器状态仍由 JSON 控制面维护。旧版、日期快照、`ALPHA`、`V1` 和重复主题文档不再属于活跃开发树。

## 0. 权威层级

发生冲突时按以下顺序处理：

1. 已批准的产品范围与本计划；
2. `docs/DOCUMENTATION_AUTHORITY.json` 与九份当前主题文档；
3. `docs/status/GAP_REGISTER.json`、`EXECUTION_STATUS.json`、`PRODUCT_GATES.json`、`CURRENT_STATE.json`；
4. `docs/roadmap/NEXT_MILESTONE.json`；
5. `docs/evidence/index.json` 与不可变 evidence manifest；
6. 源码、测试和工作流；
7. Issue、PR 正文、评论和历史 Git 对象。

较低层不得单独提升较高层声明。历史信息只保留在 Git 历史、PR/Issue 或不可变 evidence 中，不得以第二份“当前文档”重新进入活跃树。

## 1. 使命与 1.0 范围

目标是以 Rust 全量重写 Nakama OSS 游戏后端，而不是只覆盖当前 Trillionnium 调用到的功能。首个冻结兼容基线为：

- Nakama OSS `v3.40.0` / `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`；
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`；
- nakama-common `v1.47.0` / `449b77ecc8789aa466c36b67f6e498033dfcd9c5`。

1.0 必须覆盖 server bootstrap、typed config、CLI、migrate、health、readiness、graceful shutdown；HTTP/JSON API、gRPC、grpc-gateway、WebSocket JSON/protobuf；认证与 session；accounts/link/unlink；storage/OCC/ACL/search；friends/groups/chat/notifications/presence/streams；leaderboards/tournaments；matchmaker/party；relayed 与 authoritative multiplayer；Runtime hooks/RPC/jobs/modules；Lua、JavaScript/TypeScript、WASM 与 Rust SDK profile；providers、IAP、subscriptions；Console、RBAC、MFA、audit、UI；PostgreSQL 与 CockroachDB；迁移、备份、PITR、HA、升级、容量、耐久、shadow、canary、cutover 和 Nakama retirement。

不属于 1.0：Heroic Cloud 私有托管控制面、非公开 Enterprise 实现、Satori/Hiro 产品本体、官方客户端 SDK 源码重写、World gameplay、Chain finality、CEX ledger，以及已编译 Go plugin ABI 的长期兼容。

最终生产拓扑不得包含第一方 Go server、Go sidecar 或 compiled Go plugin loader。当前 `runtime/` 只作为迁移输入、行为 oracle 和业务夹具。

## 2. 当前事实边界

仓库已有 authority、session、storage、canonical framing、transport error、query、presence、JWT/token、PostgreSQL/CockroachDB persistence、transactional outbox、HTTP/WebSocket、有限 gRPC Healthcheck、response-loss 和 backup/restore 的局部 source/CI candidates。

当前 canonical database-backed server 仍位于 `crates/trnm-persistence-pg`；`crates/trnm-server` 是受限 foundation executable。最终必须收敛到一个生产 composition root，并把 persistence、protocol、service 和 process lifecycle 分层。现有局部成功不能推导完整 Nakama parity。

以下声明当前全部为 false：

```text
complete Nakama compatibility
C1–C5 repository-wide compatibility
SG1–SG9 completion
production-ready
public-online
drop-in replacement
Nakama retired
```

## 3. 文档与控制面模型

### 3.1 人类权威文档

- `CURRENT_PLAN.md`：范围、阶段、关闭规则；
- `docs/ARCHITECTURE.md`：当前/目标架构和依赖方向；
- `docs/DEVELOPMENT.md`：开发流程、工作区和编码约束；
- `docs/COMPATIBILITY.md`：baseline、denominator、oracle、C/SG 规则；
- `docs/TESTING_AND_EVIDENCE.md`：测试、证据与审核合同；
- `docs/SECURITY_AND_PRIVACY.md`：安全、密钥、隐私和供应链；
- `docs/OPERATIONS_AND_RELEASE.md`：运行、迁移、HA、发布与退役；
- `docs/GOVERNANCE.md`：分支、合并、CODEOWNERS 和管理员状态；
- `docs/ROADMAP.md`：当前 critical path 与并行化边界。

### 3.2 机器状态

- `docs/status/EXECUTION_STATUS.json`：任务、workstream 与 stage 状态；
- `docs/status/GAP_REGISTER.json`：所有 P0/P1/P2 gap 与关闭条件；
- `docs/status/CURRENT_STATE.json`：可审计状态快照；
- `docs/status/IMPLEMENTATION_INVENTORY.json`：源码到 capability/test/evidence 的映射；
- `docs/status/PRODUCT_GATES.json`：证据派生 gate；
- `docs/roadmap/NEXT_MILESTONE.json`：近期唯一机器队列；
- `docs/evidence/index.json`：证据登记入口。

合法状态链：

```text
planned -> ready -> in-progress -> source-candidate -> locally-verified
        -> remote-verified -> independently-reviewed -> accepted
```

`blocked`、`rejected`、`superseded` 只能按状态合同使用。代码存在、文档完成、Issue 关闭或一次 workflow 绿色都不等于 `accepted`。

## 4. 兼容声明 C0–C5

| 级别 | 含义 | 最低证明 |
| --- | --- | --- |
| C0 | planning/build/schema candidate | pinned baseline、可复现 build、范围与控制面 |
| C1 | wire-compatible subset | exact HTTP/gRPC/RT bytes、status、headers、close differential |
| C2 | behavior-compatible domain | C1 + DB effects、hooks、并发、fault、restart |
| C3 | data-migration compatible | repeatable migration、semantic validation、rollback barrier |
| C4 | operationally replaceable | HA、security、capacity、backup/PITR、upgrade、runbooks |
| C5 | supported full replacement | 全 mandatory leaves、迁移、cutover 与 retirement 完成 |

声明必须绑定 capability/profile/leaf，局部 C1 不会自动提升整个仓库。

## 5. Parity denominator D0–D8

SG1 的真实分母来自 pinned source，而不是文档行数或任务数量：

- D0：source、release、commit、tree、blob、image、toolchain identity；
- D1：HTTP/gRPC methods、routes、messages、enums、JSON mapping；
- D2：realtime messages、envelopes、CID、errors、socket lifecycle；
- D3：Console methods、ACL 与 operator workflows；
- D4：Runtime initializers、hooks、contexts、match interface、module APIs；
- D5：config、defaults、validation、precedence、env、CLI 与 exit codes；
- D6：migrations、tables、columns、constraints、indexes、sequences、invariants；
- D7：metrics、health、readiness、logs、packaging、startup/shutdown、diagnostics；
- D8：auth/social/IAP providers、states、callbacks、retry 与 value effects。

当前 14 个 denominator family 的 candidate manifests 已物化，但独立分类审核和 global lock 尚未完成。每个 leaf 必须有 source identity、signature hash、owner、task、test、profile、status 和 evidence refs。分母 decrease/merge/ignore 必须有 upstream delta 或批准决策。

## 6. Oracle 与差分

必须维护：

1. 未修改的 immutable Nakama oracle；
2. 只为 clock/random/provider/DB/trace 捕获做最小补丁的 instrumented oracle。

两端从同一 seed 克隆独立数据库，不共享可写状态。差分捕获 wire、status、headers、close、DB rows/invariants、events、hooks、logs/metrics、effect intent/receipt。Identity、ACL、money、sequence、version、cursor、error code 和 durable effect 永远不得 normalize。

## 7. 架构原则

`trnm-server` 最终是唯一生产 composition root。协议 adapter 只能调用 service command/query；service 通过 deadline-aware persistence API；PostgreSQL 与 CockroachDB 独立实现和验证；外部 effect 只能由 transaction outbox 驱动。

所有 queue、task、connection、batch、retry、runtime budget 与 shutdown 必须有硬边界。请求成功只能在 durable commit 或 exact receipt replay 后构造。任何可变数据库事务内禁止 provider、网络、文件或 telemetry export I/O。

## 8. 数据与迁移权威

`migrations/` 是唯一 production-authoritative DDL chain。`database/schema/v2/` 仅为 non-authoritative design history，runtime、CI、backup 和 release tooling 不得消费。

迁移阶段：

```text
nakama_primary
 -> rust_shadow_no_effect
 -> rust_canary_new_entities
 -> rust_primary_new_entities
 -> nakama_read_only
 -> nakama_retired
```

同一 session family、party、ticket、match、scheduler、IAP transaction 或 durable command 在任一时刻只能有一个 writer。禁止同步双写；允许 source transaction 写 immutable outbox/CDC，由 target 幂等 apply。

## 9. 安全原则

安全关键 primitive 使用经过审查的生态库，或经独立审核的极窄 compatibility adapter。Access、refresh、console、runtime、socket、authority、provider 和 evidence key 必须分域。未知 epoch 不得 fallback；raw token/key 不得记录；rotation、emergency revoke、refresh replay、socket disconnect 和 KMS/HSM adapter 必须有证据。

## 10. 测试与证据原则

必需 lane 的 empty、missing、skipped、cancelled、neutral、timed-out、startup-failure、older-head 或 zero-job 均不是通过。PostgreSQL 与 CockroachDB 分别证明。Local pass 只是开发反馈。

Evidence 必须绑定 exact repository、commit、tree、workflow/run/job/attempt、environment、fixture、command、assertion、artifact digest、limitations、expiry 和 independent review。Relay evidence 只有在目标仓库验证 exact target 后才可计分。

## 11. 合并与治理

稳定 required check 为 `trillionnium-game-merge-gate`。目标规则包括：禁止 direct/force push；要求最新 HEAD 的 non-empty aggregate；至少一名非作者 reviewer；CODEOWNER review；head 变化后 dismissal；latest-push approval；conversation resolution；merge queue/update-before-merge；管理员 bypass 有审计且不得用于兼容性提升。

仓库配置只有在 GitHub API/UI 回读和负向演练均成功后才算生效。文档与 desired JSON 只是准备，不是管理员状态证据。

## 12. Workstreams W0–W16

### W0 — Program, denominator and governance
锁定范围、upstream、D0–D8、状态、证据、审查和仓库规则。

### W1 — Foundation, persistence and outbox
交付 transaction repository、双数据库 profile、schema ABI、receipt、outbox、retry、reconcile。

### W2 — Protocol and server bootstrap
交付唯一 composition root、typed config、CLI、HTTP/gRPC/RTAPI、lifecycle 和错误映射。

### W3 — Identity, sessions and accounts
交付 providers、token、refresh family、revoke、accounts、link/unlink、wallet/metadata。

### W4 — Storage and data access
交付 storage、ACL、OCC、cursor、batch、index/search 与迁移语义。

### W5 — Social graph
交付 friends、groups、blocks、joins、limits、hooks 和 notifications。

### W6 — Realtime, chat and presence
交付 connection actor、streams、presence、chat、history、fanout、reconnect 和 revoke。

### W7 — Leaderboards and tournaments
交付 definitions、records、ranks、cursors、reset/end scheduler、hooks 和 rewards。

### W8 — Matchmaker and parties
交付 query parser、ticket pool、matching、processor hooks、party lifecycle 与 atomic matchmaking。

### W9 — Client-relayed multiplayer
交付 create/join/leave/list、tokens、presence、routing、filter、node route 与 cleanup。

### W10 — Server-authoritative multiplayer
交付 lifecycle callbacks、fixed tick、dispatcher、signals、termination、fencing、placement 与 snapshots。

### W11 — Runtime framework and module migration
交付 Runtime denominator、SDK、WASM、Lua/JS、RPC/hooks/jobs、module ordering 与 Go source migration。

### W12 — IAP and subscriptions
交付 provider validation、persistence、refund/void/renewal、notification、idempotency 与 reconciliation。

### W13 — Console API and Rust/WASM UI
交付 Console methods、auth/RBAC/MFA、audit、dangerous-action approval 和 accessibility。

### W14 — Data migration and compatibility
交付 introspection、snapshot/backfill/CDC、receipts、semantic comparator、write fence 与 rollback exporter。

### W15 — Observability, security and operations
交付 telemetry、privacy、rate/abuse、TLS/mTLS、keys、backup/PITR、supply chain、fuzz/pentest 和 incidents。

### W16 — Performance, HA, release and retirement
交付 oracle benchmark、capacity、multi-node failover、rolling upgrades、shadow/canary、endurance、cutover 与 retirement。

## 13. Stage gates SG0–SG9

- SG0：仓库身份、计划、CI、治理和单一 integration line；
- SG1：全部 denominator 分类、复现、独立审核并锁定；
- SG2：immutable/instrumented oracle、normalizer 和 differential engine；
- SG3：基础 core、protocol model、query/runtime feasibility；
- SG4：完整 foundation vertical slice、双数据库、receipt/outbox/fault；
- SG5：identity、session、storage 和安全边界；
- SG6：social/realtime/matchmaker/client-relayed domains；
- SG7：authoritative multiplayer、Runtime、IAP 和 Console；
- SG8：migration、security、HA、PITR、capacity 和 endurance；
- SG9：shadow/canary/cutover、支持模型、C5 与 Nakama retirement。

任何 SG 只由 accepted evidence 和 closed dependencies 派生。

## 14. 当前 blocker 闭合顺序

### Wave 0 — 文档、控制面与治理完整性

1. 保留唯一当前文档体系并删除旧入口；
2. exact-head Actions 和 closed-world aggregate；
3. 状态、gap、evidence、gate 一致性；
4. CODEOWNERS、review enforcement 与 ruleset read-back；
5. stale PR、branch 和 repository identity 清理。

### Wave 1 — 数据与安全权威

1. 单一 migration chain；
2. outbox terminal/reclaim/reconcile；
3. reviewed crypto provider 与 constant-time/malformed corpus；
4. pool/TLS/deadline/cancellation；
5. 独立 database/security review。

### Wave 2 — 唯一黄金纵向切片

```text
official SDK request
 -> HTTP/JSON + gRPC + persistent WebSocket
 -> session verification
 -> service command
 -> SERIALIZABLE transaction
 -> event + receipt + outbox
 -> acknowledgement after commit
 -> response-loss/restart/reconnect
 -> PostgreSQL + CockroachDB
 -> immutable Nakama differential
 -> independent protocol/database/security review
```

### Wave 3 — SG1/SG2 收口
完成 denominator lock、oracle reproducibility、normalizer review 和 SDK consumer matrix。

只有 Wave 0–3 达到各自 exit criteria，才允许大规模并行扩展 W3–W13。

## 15. Definition of Ready

任务进入 `ready` 前必须具备：exact scope、owner、independent reviewer role、gap/task/parity/gate IDs、upstream identity、API/data/error/concurrency/security contracts、test matrix、fixtures、resource budget、migration/rollback、observability、dependency、estimate、risk 与 evidence output。

## 16. Definition of Done

任务进入 `accepted` 前必须满足：源码、schema、tests、当前文档和状态同步；exact-head format/test/lint/fuzz/static 非空成功；所需 live/differential/fault/performance/endurance 完成；evidence schema/digest/identity 有效；无未解释 P0/P1；limitations 明确；独立 reviewer accepted；gate/claim 由脚本派生。

## 17. Gap closure definition

Gap 只能在以下全部成立时关闭：

1. 每项 close criterion 有 assertion；
2. implementation commit/tree 精确记录；
3. required tests non-empty、terminal、successful；
4. artifact digest 和 evidence ID 已登记；
5. blocking dependencies closed；
6. 无未解释 P0/P1 子 gap；
7. required independent review accepted；
8. 外部管理员状态已实际改变并重新读取验证。

`implemented`、`documented`、`local-pass`、`workflow-added`、`review-requested` 或 `issue-closed` 均不能单独关闭 gap。

## 18. 最终验收

TrillionniumGame 1.0 只有在全部 mandatory D0–D8 leaves 达到生产要求、官方 SDK/HTTP/gRPC/RT/Runtime/Console/provider/IAP 通过、Nakama 数据和 Go source 完成迁移、双数据库支持声明成立、安全/隐私/性能/HA/upgrade/backup/PITR/endurance 通过、shadow/canary 无未解释 P0/P1、rollback rehearsal 完成，并由独立发布委员会批准 C5 与 retirement 后才完成。

在此之前：局部 candidate 和局部证据可以成立，但完整兼容、生产就绪、公开上线、替代和退役声明必须保持 false。
