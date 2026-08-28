# 迁移权威矩阵

## 1. 原则

任何时刻，一个 durable entity 只有一个业务写入 owner。Shadow 可以读取和执行无副作用逻辑，但不能签发 token、ACK durable write、发送价值、推进 scheduler 或发布 authoritative realtime event。

## 2. 实体矩阵

| 实体 | Nakama primary | Shadow | Rust canary | Rust primary | 热迁移 |
| --- | --- | --- | --- | --- | --- |
| Account/identity | Nakama write | Rust read/compare | cohort owner by user ID | Rust | 可在无并发登录窗口切换 |
| Session/refresh family | Nakama issue/revoke | Rust validate only | 新 session 由 cohort owner；旧 session 固定 owner | Rust | 不跨 owner 刷新；旧 token 过渡验证 |
| Storage object | Nakama write | dual-read compare | collection/user cohort owner | Rust | CDC/backfill 后 CAS cutover |
| Friend/group edge | Nakama write | compare | user/group shard owner | Rust | 需 edge closure/fencing |
| Chat message/channel | Nakama write/deliver | consume copy | 新 channel/session cohort | Rust | 活跃 channel 默认 drain |
| Notification | Nakama create/deliver | suppress delivery | owner by user | Rust | outbox receipt 防重复 |
| Leaderboard/tournament | Nakama scheduler/write | shadow calculate | whole definition owner，不按单记录拆分 | Rust | reset boundary 切换 |
| Matchmaker ticket | Nakama | 不入真实 pool | 新 ticket 由路由 owner | Rust | 禁止迁移 active ticket |
| Party | Nakama | shadow state only | 新 party 固定 owner | Rust | active party 默认 drain |
| Relayed match | Nakama | packet observe only | 新 match 固定 owner | Rust | 禁止热迁移 |
| Authoritative match | Nakama | deterministic no-effect | 新 match 固定 owner | Rust | 默认 drain；除非单独版本化 snapshot contract |
| IAP transaction | Nakama persist/reward | verify only | provider transaction ID 固定 owner | Rust | 全局 idempotency registry 后切换 |
| Scheduler job | Nakama lease | Rust calculate only | whole schedule definition owner | Rust | lease epoch/fencing 切换 |
| Runtime RPC | Nakama handler | Rust no-effect compare | route cohort；副作用 owner 唯一 | Rust | 按 RPC capability 分类 |

## 3. 数据复制阶段

```text
inventory
 -> immutable snapshot
 -> online backfill
 -> CDC catch-up
 -> semantic verification
 -> write fence
 -> owner flip
 -> read verification
 -> old writer revoke
```

每一步有 checkpoint、receipt、rate limit、pause/resume 和 rollback marker。

## 4. Dual-write 限制

只允许 source transaction 写不可变 outbox/CDC record；target 以 source event ID 幂等 apply；target 在 primary 切换前不对外可见；comparator 不回写 source；不确定结果进入 quarantine。禁止 API handler 同时调用 Nakama 和 Rust 两套业务写路径。

## 5. Session 迁移

保留 user UUID；旧 access token 只在明确时间窗由 bridge 验证；refresh family 不在两个系统同时续签；logout-all/ban/revocation epoch 双边一致；窗口结束后撤销旧 signing/refresh keys。

## 6. 回滚

回滚只影响尚未创建的新 entity 或已完成 owner-fence 的 cohort；已在 Rust 创建的 active party/match/ticket 不切回 Nakama；Rust primary 后必须安全回传 Rust-only 写入或停止服务，不能在旧系统缺写时直接恢复流量。
