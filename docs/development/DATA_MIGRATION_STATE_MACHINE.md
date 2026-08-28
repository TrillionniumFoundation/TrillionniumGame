# Nakama → TrillionniumGame Data Migration State Machine

状态：**binding design baseline v1**

## 1. 状态

```text
UNINVENTORIED
 -> INVENTORIED
 -> SOURCE_FINGERPRINTED
 -> STRATEGY_SELECTED
 -> SNAPSHOT_CAPTURED
 -> BACKFILLING
 -> BACKFILL_VALIDATED
 -> CHANGE_CAPTURE_ACTIVE | BOUNDED_FREEZE_READY
 -> DUAL_READ_VALIDATING
 -> CUTOVER_READY
 -> SOURCE_WRITE_FENCED
 -> FINAL_DELTA_APPLIED
 -> RUST_PRIMARY
 -> ROLLBACK_WINDOW
 -> SOURCE_READ_ONLY
 -> RETIRED
```

任何状态转换都必须写 migration control record、operator identity、source/candidate schema digest、cutover epoch 和 evidence artifact hash。

## 2. 不变量

1. `SOURCE_WRITE_FENCED` 前 Nakama 是唯一写 authority，除非有独立 ADR 证明 idempotent dual-write；
2. `RUST_PRIMARY` 后旧系统不得接受会在回滚后丢失的写入；
3. backfill/change capture 每条记录都有稳定 source identity 和 receipt；
4. 重跑不得创建重复账号、edge、message、record、purchase 或 storage object；
5. 未知 schema、损坏 row、非法 enum、hash mismatch 进入 quarantine；
6. final delta 到 Rust write enable 由同一 cutover epoch 保护；
7. rollback 先 fence Rust write，再验证 reverse delta 或确认无新写入；
8. active authoritative match 默认不热切换。

## 3. Active entity disposition

| Entity | 首版策略 | 恢复/迁移规则 |
| --- | --- | --- |
| Access session | token exchange 或自然过期 | 保留 user UUID；revocation epoch 一致 |
| Refresh session | exchange once / revoke old family | 防止旧 refresh replay |
| WebSocket connection | reconnect to selected owner | 不迁移 TCP/WS connection |
| Presence | 重建 | durable truth 不依赖 presence cache |
| Chat channel | durable history backfill；presence 重建 | cursor 绑定 source sequence |
| Notification | durable backfill + idempotent delivery ID | 已读/删除状态校验 |
| Matchmaker ticket | expire/cancel/reissue | 同 ticket 不跨 authority 匹配 |
| Party | drain 或显式重建 | 所有成员同一 owner |
| Relayed match | drain | 新 match 才进入 Rust |
| Authoritative match | drain/quarantine | completion 由原 authority 完成 |
| Scheduler job | lease epoch handoff | 仅一个 owner generation |
| IAP transaction | receipt identity 迁移 | provider transaction ID 唯一 |

## 4. 验证层次

L0 schema fingerprint；L1 row count/PK coverage；L2 canonical row hash；L3 domain semantic reconstruction；L4 API read comparison；L5 write/retry/idempotency comparison；L6 production-sanitized trace replay。只有 L0–L5 全通过才进入 `CUTOVER_READY`。

## 5. 回滚屏障

Rollback window 内保存 source read-only snapshot、candidate write log/outbox、reverse-migration capability 或无 candidate-only write 保证、key/token authority map、route cohort、last validated sequence/receipt 和 runbook。无法证明旧系统可安全恢复时，不能把“路由切回”称为 rollback。
