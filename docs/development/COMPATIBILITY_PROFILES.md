# 兼容等级与声明规范

## 1. 兼容等级

| 等级 | 名称 | 含义 |
| --- | --- | --- |
| C0 | Build/Schema | Protobuf/OpenAPI/配置可以生成和解析，不代表行为兼容 |
| C1 | Wire | HTTP/gRPC/WebSocket 字段、状态、错误、cursor 和 framing 兼容 |
| C2 | Behavioral | 状态转换、权限、顺序、hook、重试和可见副作用兼容 |
| C3 | Data Migration | 可从 pinned Nakama schema 无损迁移、验证并按 runbook 回滚 |
| C4 | Operational | 配置、CLI、health、metrics、shutdown、HA 和运维工作流达到约定兼容 |
| C5 | Full Replacement | C1–C4 全部通过，实际 Go modules 已迁移，Nakama 可安全退役 |

`drop-in replacement` 只允许用于 C5，并必须附带：不支持加载已编译 Go plugin；需要先完成源码迁移。任何更低等级必须写明具体等级。

## 2. 两种运行 profile

### Nakama compatibility profile

默认 single-project 语义；优先保持公开协议和可观察行为；不新增字段、不改变错误码、不改变默认权限；内部可更安全，但不能在外部产生未经版本化的差异。

### Trillionnium native hardened profile

可启用更强 idempotency、WASM capabilities、多租户、额外审计和扩展 API；扩展使用独立 namespace/version/capability negotiation；不计入 Nakama parity denominator；不允许 compatibility profile 静默继承破坏性默认值。

## 3. 稳定性等级

| 等级 | 例子 | 差异策略 |
| --- | --- | --- |
| wire-contract | method/path/status/gRPC code/RT error/field encoding | 任一未批准差异阻塞 |
| behavior-contract | ACL、排序、状态机、hook 顺序、DB 可见效果 | 任一 P0/P1 差异阻塞 |
| observability-contract | health、documented metric、documented log field | release profile 定义后阻塞 |
| best-effort | 未文档化日志文本、内部 query shape | 记录但不自动阻塞，除非有依赖证据 |

Error message 由 extractor 标记：`exact`、`stable-prefix`、`semantic-only` 或 `internal-only`。

## 4. Runtime 兼容边界

- Go runtime：提供源码迁移，不提供 `.so` ABI。
- Lua/JS：按公共 Runtime API 和代表性 module corpus 达到 C2；底层引擎可不同。
- Rust native：Trillionnium 原生迁移目标，不属于 Nakama parity。
- WASM component：安全扩展能力，使用 `TG-EXT-*`；不允许掩盖 Lua/JS/Go migration 缺口。

## 5. 数据兼容边界

`schema-reader` -> `offline-import` -> `online-backfill` -> `cdc-catchup` -> `semantic-verify` -> `native-primary`。除明确 outbox/CDC 事务外，禁止两套业务逻辑同时写同一实体。

## 6. 对外声明模板

允许：

> TrillionniumGame build X 对 Nakama v3.40.0 的 Device Auth 达到 C2，证据绑定 commit、artifact、oracle 和 test manifest。

禁止：

> 已经兼容 Nakama。

除非 C5 gate 完整关闭。
