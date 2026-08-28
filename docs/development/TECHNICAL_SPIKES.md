# TrillionniumGame Risk-First Technical Spikes

状态：**前 12 周强制执行**

Spike 只能证明可行性，不能获得 production 或 parity credit。每个 spike 绑定 exact source、命令、结果、限制和 ADR。

| ID | 截止 | 问题 | 必须交付 | Go/No-Go 标准 | 失败后的允许选择 |
| --- | --- | --- | --- | --- | --- |
| SPIKE-001 | W4 | HTTP/gRPC JSON transcoding exactness | 20 个复杂 API raw wire comparator | 无不可解释 P0 wire divergence | 自建 transcoder |
| SPIKE-002 | W4 | JWT/session/refresh/logout/socket revoke | token corpus、clock/key rotation、revocation model | 官方 SDK 可无修改完成流程 | 明确 token exchange profile |
| SPIKE-003 | W6 | JavaScript engine | Goja corpus 对 Boa/QuickJS 等候选 | 代表性 modules 与核心 APIs 达阈值 | ADR 选择 audited native engine 或保持 profile open |
| SPIKE-004 | W6 | Lua engine | number/coroutine/module/error/library corpus | 核心 modules 与 hook/context 一致 | 选择受审 native Lua 或明确 unsupported profile |
| SPIKE-005 | W5 | WASM ABI/capability/performance | host prototype、fuel/memory/deadline | isolation 与 overhead 合格 | 调整 ABI，禁止无约束 native plugin |
| SPIKE-006 | W6 | Go module migration scale | 全组织 inventory、API use graph、migration report | 100% module 有 owner/route | 延长 migration，不保留 Go loader |
| SPIKE-007 | W6 | PostgreSQL/CockroachDB semantics | OCC、serializable retry、scheduler lease、JSON/order corpus | CRDB profile 无 P0 gap | CRDB 延后，PostgreSQL 继续 |
| SPIKE-008 | W8 | Storage/group/matchmaker query | parser、100k/1m corpus、index rebuild | semantic parity + operational performance | 调整 index technology |
| SPIKE-009 | W8 | WebSocket/presence/fanout | 100k sockets、slow consumer、node loss | bounded memory、无 ghost、可恢复 routing | 重做 actor/routing，停止扩功能 |
| SPIKE-010 | W9 | Authoritative tick isolation | 多频率、overrun/panic/timeout corpus | jitter/fairness/isolation 达预算 | 分离 worker pool/process |
| SPIKE-011 | W10 | Online migration/rollback | snapshot/backfill/final delta/write fence | 重跑幂等、rollback barrier、zero orphan | bounded freeze/drain |
| SPIKE-012 | W10 | Rust/WASM Console | auth/RBAC + 两个大数据页面 | accessibility/pagination/bundle/maintenance 合格 | API 优先，UI 分阶段 |

结果写入 `evidence/spikes/<spike-id>/<candidate-commit>/evidence.json`。No-Go 必须触发 ADR；相关 compatibility profile 保持 open。
