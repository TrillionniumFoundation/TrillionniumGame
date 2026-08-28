# 关键路径与阶段门

## 1. 计划置信区间

- P50：48 个月；
- P80：60 个月；
- 峰值：28–36 FTE；
- 假设：完整 OSS parity、Lua/JS runtime、Console、IAP、PostgreSQL/CockroachDB、数据迁移和 C5 replacement 均不删减。

缩短日历时间只能通过增加可并行的独立团队或减少正式 denominator；不能通过删除差分、故障、安全和迁移证据实现。

## 2. 阶段门

| Gate | 目标时间 | 进入条件 | 退出条件 | 失败动作 |
| --- | --- | --- | --- | --- |
| SG0 Repository Adoption | M0 | admin、备份、计划 tree | history/refs 保留、rename evidence、governance snapshot | rename 回滚，不删历史 |
| SG1 Denominator Lock | M0–M3 | pinned source | D0–D8 manifest、hash、owner/task/test 100% | 禁止开始广泛 port |
| SG2 Oracle Reproducibility | M1–M4 | SG1 protocol subset | oracle image/DB/module/env 可重复，10 次 hash 一致 | 修复 harness，不接受手工 fixture |
| SG3 Architecture Feasibility | M2–M6 | SG1/SG2 | JS/Lua/runtime/query/DB/Console spikes 和 ADR | 调整实现方案或计划周期 |
| SG4 Foundation Alpha | M4–M9 | SG3 | config/CLI/API/socket/DB skeleton，R1 evidence | 不进入领域并行扩张 |
| SG5 Core Services Differential | M9–M18 | SG4 | auth/session/storage/social/competitive mandatory items C2 | 阻止 realtime migration |
| SG6 Realtime and Runtime Alpha | M16–M30 | SG5 | presence/chat/party/matchmaker/matches/runtime C2 + isolation | 禁止生产 shadow |
| SG7 Full Feature Beta / Final Upstream Freeze | M26–M36 | SG6 | IAP/Console/data migration complete；选择 1.0 final upstream baseline | 延期，不移动 canary |
| SG8 Migration RC | M32–M48 | SG7 | C3/C4、shadow zero unexplained P0/P1、restore/rollback | 保持 Nakama primary |
| SG9 Production Cutover | M42–M60 | SG8 | security/perf/HA/endurance/canary 全部通过 | 自动停止扩大并回滚新流量 |

## 3. 关键路径

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

Console UI、部分 provider、performance tooling 可并行；Runtime engine、realtime ownership、data migration 和 cutover 不可通过增加普通 API 工程师线性压缩。

## 4. 早期 go/no-go 技术尖峰

M6 前必须完成：JavaScript engine；Lua engine；Runtime sandbox；match tick isolation；storage/search/query；CockroachDB；Console Rust/WASM；online migration/CDC 或 bounded freeze。

每个 spike 产出 ADR；没有通过则更新架构和 P80，不得把风险留到实现后期。

## 5. 人员爬坡

| 阶段 | FTE | 重点 |
| --- | ---: | --- |
| M0–M3 | 8–12 | program、protocol、oracle、DB、runtime spikes |
| M4–M12 | 18–24 | foundation、identity、storage、protocol、SRE |
| M12–M30 | 28–36 | realtime、runtime、social、competitive、IAP、Console |
| M30–M48 | 24–32 | migration、HA、security、performance、provider certification |
| M48–M60 | 14–24 | canary、operations、support、upstream delta、retirement |

## 6. 计划绩效

每四周发布 denominator coverage、gate burn-up、critical-path slip、P50/P80 更新、open divergences、flaky/waiver/evidence expiry、FTE/cost variance 和 upstream delta backlog。禁止用提交数、代码行或 crate 数代替进度。
