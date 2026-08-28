# TrillionniumGame 全量 Rust 重写开发计划 v1

状态：**规划基线**  
生效日期：2026-08-28  
项目 ID：`trillionnium-game`  
目标仓库：`TrillionniumFoundation/TrillionniumGame`  
首个兼容基线：Nakama OSS `v3.40.0`  
目标版本：`TrillionniumGame 1.0`  
规划周期：36–48 个月；峰值团队：28–36 FTE

## 1. 项目使命

使用 Rust 全量重写 Nakama OSS 游戏后端服务器，而不是只重写当前 Trillionnium World 使用到的子集。最终产品必须独立拥有公开协议、数据语义、实时协调、Runtime、Console、IAP、迁移、运维、安全和发布证据。

任何“已兼容”“可替换”“生产就绪”声明必须绑定精确上游基线、精确 TrillionniumGame 提交和产物、差分命令、环境、证据、审阅者、限制和有效期。

## 2. 固定上游基线

```text
heroiclabs/nakama
  tag: v3.40.0
  commit: d4d92f93f78bbbe62c7fc50a3f85c772ec121a09
  tree: f3c9cfc2726d5543da1564629170f35b98e3797d

heroiclabs/nakama-common
  tag: v1.47.0
  commit: 449b77ecc8789aa466c36b67f6e498033dfcd9c5
  tree: c6a7b9796b9c2a6b5118c74e5f213963a5001f14
```

不允许以浮动 `master` 作为验收分母。复制、翻译、生成或派生的上游材料必须记录文件 blob SHA 并遵守 Apache-2.0 与商标边界。

## 3. 1.0 完整范围

### 3.1 平台与协议

- server bootstrap、配置、CLI、迁移、健康检查、优雅退出；
- HTTP/JSON API v2；
- gRPC API；
- WebSocket JSON 与 protobuf 实时协议；
- Basic server key、Bearer session、runtime HTTP key；
- cursor、分页、错误码、连接关闭和限流语义；
- 官方客户端 SDK 黑盒兼容矩阵。

### 3.2 身份、账号与会话

- device、custom、email/password；
- Apple、Facebook、Facebook Instant Game、Game Center、Google、Steam 等基线 provider；
- link/unlink；
- session JWT、refresh、logout、revocation；
- account read/update/delete/export/import；
- profile、wallet、metadata、device、language、timezone、ban/restriction。

### 3.3 持久系统与社交

- Storage read/write/delete、ACL、OCC version、list cursor、search/index；
- friends、requests、block、provider friend imports；
- groups CRUD/search、members、roles、requests、bans、promotion/demotion；
- leaderboards、records、ranks、resets；
- tournaments、joins、records、rewards、scheduling。

### 3.4 实时系统

- session/socket registry；
- presence、streams、status follow；
- direct/group/room chat、history、update/delete；
- persistent/realtime notifications；
- parties、party leader、join requests、party data；
- matchmaker query grammar、tickets、matching、hooks；
- client-relayed matches；
- server-authoritative matches、fixed ticks、callbacks、signals、listing、placement、drain、fencing。

### 3.5 Runtime Framework

- Rust native runtime SDK；
- WASM component host；
- Rust 托管的 Lua compatibility runtime；
- Rust 托管的 JavaScript/TypeScript compatibility runtime；
- RPC、HTTP、Console handlers；
- before/after hooks、events、sessions、notifications、schedulers；
- 全领域 runtime module APIs；
- 现有 Go runtime module 的源码分析、迁移、Rust/WASM 重写和差分证明。

最终生产产品不加载已编译 Go plugin，也不保留 Go server 或 Go sidecar。这意味着 1.0 的完整替换声明是“协议、数据、功能和迁移等价”，而不是 Go 二进制插件 ABI 等价。

### 3.6 IAP、Console 与运维

- Apple、Google、Huawei、Facebook、Steam、Samsung 等公开基线交易适配；
- purchase/subscription validation、persist、refund/void/renewal notifications；
- Console API、authentication、RBAC、audit、全领域管理工作流；
- Rust/WASM Console UI；
- metrics、logs、traces、profiles；
- PostgreSQL 与 CockroachDB 独立兼容；
- backup、PITR、restore、rolling upgrade、HA、security、capacity、endurance；
- Nakama data migration、shadow、canary、cutover、rollback 和 retirement。

## 4. 明确排除

- Heroic Cloud 托管控制面和非公开 Enterprise 实现；
- Satori/Hiro 产品本体；
- 官方客户端 SDK 仓库源码重写；
- World gameplay、Chain finality、CEX ledger/custody；
- 已编译 Go `.so` plugin 的 ABI 兼容。

这些排除项不得被扩大为对 Nakama OSS 公开能力的删减。

## 5. 不可违反的系统不变量

1. 一个 session、party、match、scheduler job、purchase 或 durable command 在任一时刻只有一个写入权威。
2. 外部网络工作不得跨越持有 mutable database transaction 的边界。
3. 已确认响应对应的 durable state 不得在 crash、retry 或 failover 后丢失。
4. 所有 retry 使用稳定 identity；同 identity 不同内容必须冲突。
5. 所有异步边界必须有有界队列、deadline、cancellation 和 backpressure policy。
6. stale owner/generation/fencing token 不得写入。
7. Redis/cache/index 可重建；PostgreSQL/CockroachDB 是 durable truth。
8. 权限、身份、顺序、金额、版本和错误码差异不得通过 normalizer 隐藏。
9. migration、canary 和 rollback 不得形成未受控双写。
10. 任何 P0/P1 未解释差异阻止兼容或发布升级。

## 6. 目标架构

```text
Official/Existing Nakama Client SDKs
            |
  HTTP/JSON | gRPC | WebSocket JSON/protobuf
            |
+-----------v--------------------------------------+
| Edge and Protocol Gateway                       |
| auth, limits, transcoding, CID, socket lifecycle|
+-----------+--------------------------------------+
            |
+-----------v--------------------------------------+
| Domain Services                                 |
| identity | storage | social | competition | IAP |
+-----------+------------------------+-------------+
            |                        |
+-----------v----------+  +----------v-------------+
| Realtime Fabric      |  | Runtime Host           |
| presence/chat/party  |  | Rust/WASM/Lua/JS       |
| relay/match routing  |  | RPC/hooks/jobs/matches |
+-----------+----------+  +----------+-------------+
            |                        |
+-----------v------------------------v-------------+
| Ownership and Coordination                      |
| matchmaker | actors | schedulers | leases/fences|
+-----------+--------------------------------------+
            |
+-----------v--------------------------------------+
| Data Plane                                       |
| PostgreSQL/CockroachDB | index | outbox | cache  |
+-----------+--------------------------------------+
            |
+-----------v--------------------------------------+
| Console / Admin / Observability / Migration      |
+--------------------------------------------------+
```

首轮可采用模块化单体部署，但 crate 边界和所有权必须支持后续拆分。禁止业务 crate 直接依赖 binary crate。

## 7. 差分 Oracle

每个兼容能力必须通过 Nakama oracle 与 Rust candidate 的差分测试：

1. 从相同种子构造隔离数据库；
2. 固定或记录时间、随机数、provider replies 与 module fixtures；
3. 发送相同 HTTP/gRPC/WebSocket 序列；
4. 捕获 wire bytes、status、headers、database effects、events、hooks、metrics；
5. 仅按登记过的 non-contract fields 归一化；
6. 输出 typed divergence；
7. 未解释 P0/P1 divergence 阻止 PR、canary 或 release。

Oracle 必须同时保留“未经修改的官方镜像”与“最小可审计插桩镜像”，后者不能自行成为唯一事实来源。

## 8. 工作流

### W0 — Governance and upstream truth source

锁定上游 commits/trees/blobs；提取 API、RTAPI、Console、Runtime、Config/CLI、SQL 与 operations denominator；构建 oracle；建立许可证、CODEOWNERS、required checks 和 upstream delta bot。

### W1 — Foundation, config, CLI and migrations

Rust workspace、typed config、CLI、DB primitives、migration runner、logging/metrics/tracing、readiness、shutdown、local deployment。

### W2 — HTTP, gRPC and realtime protocol core

导入 pinned schemas，实现 HTTP transcoding、gRPC registration、WebSocket JSON/protobuf、limits、heartbeat、compression、disconnect 和 SDK runner。

### W3 — Authentication, sessions and accounts

实现全部公开 provider、identity link/unlink、sessions、accounts、wallet/metadata、ban/restriction 和 socket revocation。

### W4 — Storage engine and search

实现 object model、batch operations、ACL、OCC、cursors、query grammar、index、rebuild 与 lag operations。

### W5 — Friends, groups and social graph

实现 friend edge state machine、block/import/notifications、groups、roles、requests、bans 与 deletion cleanup。

### W6 — Presence, streams, chat and notifications

实现 socket ownership、streams、status、chat、history、notifications、bounded fanout、route lookup 和 fencing。

### W7 — Leaderboards and tournaments

实现 definitions、records、ranks、cursors、reset scheduler、tournaments、rewards 与 hooks。

### W8 — Matchmaker and parties

实现 query parser、tickets、pool、matching、processor hooks、parties、leadership 和 party matchmaking。

### W9 — Client-relayed multiplayer

实现 match create/join/leave/list、tokens、presence、data routing、recipient filtering、node routing 与 cleanup。

### W10 — Server-authoritative multiplayer

实现 callback lifecycle、tick scheduler、dispatcher、signals、termination、limits、generation fencing、placement 和 optional durable snapshots。

### W11 — Runtime framework and module migration

提取完整 runtime denominator；实现 Rust SDK、WASM、Lua、JS/TS、RPC/HTTP/Console handlers、hooks、module APIs、jobs、module ordering 和 Go source migration。

### W12 — IAP and subscriptions

实现 provider validation、persistence、refund/void/renewal、notifications、retry、circuit breaker 和 key rotation。

### W13 — Console API and Rust/WASM UI

实现 Console denominator、auth、roles、domain APIs、data views、dangerous actions、audit、accessibility 和 responsive UI。

### W14 — Data migration and compatibility

实现 schema introspection、planner、backfills、receipts、change capture/freeze modes、dual-read comparator、rollback exporter 和 active entity disposition。

### W15 — Observability, security and operations

实现 telemetry、redaction、rate/abuse controls、TLS/mTLS、key rotation、backup/PITR、SBOM/provenance、threat models、fuzz、penetration test 和 incident runbooks。

### W16 — Performance, HA, release and retirement

建立 benchmark oracle、单节点与多节点预算、placement/failover/fencing、rolling upgrade、shadow、canary、endurance、cutover、rollback 和 Nakama retirement。

## 9. 里程碑

| 时间窗口 | 里程碑 | 必须交付 |
| --- | --- | --- |
| M0–M3 | R0 Plan/Spec Freeze | denominator、oracle、ADR、license、CI |
| M1–M6 | R1 Foundation | workspace、config、CLI、DB、observability |
| M3–M9 | Protocol Alpha | HTTP/gRPC/WebSocket 与 SDK skeleton |
| M4–M12 | R2 Stateless API Alpha | auth/session/account differential |
| M6–M18 | R3 Persistent Systems Alpha | storage/social/competitive |
| M9–M22 | R4 Realtime Alpha | presence/chat/party/relay |
| M12–M30 | R5 Authoritative + Runtime Alpha | matchmaker/matches/runtime engines |
| M18–M32 | R6 Full Feature Beta | IAP/Console/full denominator |
| M18–M36 | Migration RC | schema/data migration and dual read |
| M24–M42 | Scale/Security RC | HA/capacity/endurance/security |
| M36–M48 | R8 Production | canary/cutover/retirement |

该时间线是范围完整时的规划，不是上线承诺。任何缩短只能通过明确修改 denominator 和版本声明，不能删除测试、迁移或安全证据。

## 10. 团队

峰值建议：

- 1 Program Director；
- 2 Principal/Staff Architects；
- 6 API/identity/persistence Rust engineers；
- 6 realtime/distributed-systems Rust engineers；
- 4 runtime/VM/compiler engineers；
- 3 database/data-migration engineers；
- 3 Console Rust/WASM engineers；
- 3 SRE/platform/security engineers；
- 4 QA/performance/compatibility engineers；
- technical writing、legal/privacy/payment specialists 按阶段加入。

安全、数据、Runtime、IAP、Console 和 authority changes 至少需要双人 review。

## 11. 初始验收与发布门禁

`TrillionniumGame 1.0` 只有在以下全部完成后才可称为生产替换：

- 完整机器 denominator 已生成且无静默缺口；
- HTTP/gRPC/WebSocket/SDK 差分通过；
- schema/data migration count/hash/semantic validation 通过；
- Runtime APIs 与代表性 Lua/JS/Go-source migrations 通过；
- realtime ownership、fencing 和 failure safety 通过；
- IAP provider safety 通过；
- Console/RBAC/audit 通过；
- security/fuzz/key rotation 通过；
- capacity/latency/endurance 通过；
- backup/HA/upgrade/rollback 通过；
- shadow/canary/cutover 通过；
- Nakama 已无新流量、无 active owner、私钥已撤销、历史证据仍可验证。

## 12. 首 90 天

1. 安全接管现有仓库，不强推、不丢历史；
2. 锁定上游 source/protocol/database/config/runtime manifests；
3. 建立未修改 oracle 与插桩 oracle；
4. 生成 API、RTAPI、Console、Runtime、Config/CLI、SQL denominator；
5. 建 Rust workspace 与 CI；
6. 做 Runtime JS/Lua engine bake-off；
7. 做 PostgreSQL/CockroachDB transaction semantics spike；
8. 做 provider sandbox availability spike；
9. 完成 device/custom authentication vertical slice；
10. 完成 session refresh/logout；
11. 完成 storage OCC vertical slice；
12. 完成 WebSocket handshake/presence vertical slice；
13. 完成 differential harness v1；
14. 发布第一份 exact evidence manifest；
15. 在 90 天评审中重新给出 P50/P80 预算和关键路径。

## 13. Definition of Done

每项工作必须同时具备：owner、reviewer、scope、Rust implementation、protocol/data impact、unit/property/fuzz、oracle differential、concurrency/retry/failure tests、metrics/logs/traces、security/privacy review、migration/rollback、current docs、exact commit CI evidence、residual limitations。

代码存在、happy path 通过、本机运行、单一 SDK 成功或人工截图均不能独立关闭 parity 项。
