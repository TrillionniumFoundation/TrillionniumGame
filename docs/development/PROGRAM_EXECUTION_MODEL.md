# TrillionniumGame Program Execution Model

状态：**binding planning model v1**

## 1. 规划单位

项目采用四层规划：

1. **Parity leaf**：上游一个 API method、RT message、runtime function、config key、migration、Console method 或 metric；
2. **Task**：可由一个 accountable owner 在 2–12 周内关闭的交付单位；
3. **Workstream**：跨多个 task 的领域执行线；
4. **Stage gate**：允许项目进入下一类风险的证据门。

Capability-level parity matrix 只做人工导航，不计算完成百分比。完成率来自 extractor 生成的 leaf manifests。

## 2. 阶段与门禁

| Stage | 时间窗 | 准入 | 退出 | 禁止的 claim |
| --- | --- | --- | --- | --- |
| S0 Repository & Governance | W0–W2 | 管理权限、旧仓库快照 | history/refs 保留、main 落地、rename evidence、W0 owner | compatibility |
| S1 Denominator & Oracle | M0–M3 | S0 | leaf manifests、oracle reproducible、baseline digest | implementation completeness |
| S2 Risk Feasibility | W2–W12 | S1 | P0 spikes 关闭，关键 ADR accepted | schedule certainty |
| S3 Foundation & Protocol | M1–M9 | S1/S2 | R1、transport/SDK skeleton、DB framework | durable parity |
| S4 Core Domains | M4–M18 | S3 | identity/storage/social/competitive differential | realtime parity |
| S5 Realtime & Runtime | M9–M30 | S3/S4 | presence/party/matches/runtime isolation | full feature beta |
| S6 Full Feature & Migration | M18–M36 | S4/S5 | IAP/Console/data migration rehearsal | drop-in replacement |
| S7 Scale/Security/HA | M24–M42 | S5/S6 | SLO、HA、security、restore、endurance | production |
| S8 Cutover & Retirement | M36–M60 | S7 | canary、rollback、Nakama drain/key revoke | retired-upstream before evidence |

## 3. 关键路径

```text
W0 denominator/oracle
 -> W1 config/DB/migration
 -> W2 protocol/SDK
 -> W3 identity/session + W4 storage/OCC
 -> W6 realtime ownership/fanout/fencing
 -> W8 matchmaker/party + W10 authoritative scheduler
 -> W11 runtime engines/module APIs/migration
 -> W14 data migration/write fence/rollback
 -> W16 shadow/canary/HA/retirement
```

JS/Lua、CockroachDB、migration 和 protocol spikes 在时间上早于其完整 workstream，以避免关键路径后期返工。

## 4. 资源包络

初始计划包络：

- 950–1,350 person-months；
- 约 79–113 person-years；
- 另加 25% schedule/cost contingency；
- 峰值 28–36 FTE；
- Runtime/VM、realtime distributed systems、migration/DB 是稀缺技能瓶颈。

此包络不是预算批准。S1 和 S2 完成后必须重新估算。若估算超出 1,350 person-months，Program Board 必须选择：增加资源、延后时间或通过 ADR 缩减兼容 profile；不得保持原期限并降低证据标准。

## 5. 季度 staffing 模型

| 阶段 | 主要角色 | FTE 范围 |
| --- | --- | ---: |
| M0–M3 | architecture、compatibility、platform、legal | 8–12 |
| M4–M9 | protocol、identity、DB、SRE、QA | 16–22 |
| M10–M18 | storage/social/realtime/competitive/runtime | 24–32 |
| M19–M30 | runtime、multiplayer、migration、IAP、Console | 28–36 |
| M31–M42 | HA、performance、security、migration、operations | 24–32 |
| M43–M60 | cutover、support、reliability、retirement | 14–24 |

## 6. Task 执行合同

每个 task 必须包含：owner role、reviewer roles、dependencies、effort range、linked parity IDs、linked product gates、deliverables、acceptance tests、risk IDs、rollback、evidence paths、Definition of Ready 和 Definition of Done。

Task 关闭时必须产生 exact commit-bound evidence。不能仅通过修改 backlog status 关闭。

## 7. 变更控制

以下变化必须 ADR + Program Board：删除或排除 parity leaf；更改数据库或 runtime engine；引入 native dependency；改变 session/match/party/IAP/scheduler authority；改变 1.0 deployment profile；接受长期 divergence；越过 stage gate；升级公开 claim。

## 8. 进度度量

禁止使用代码行数、已启动 crate 数、PR 数或未加权 task count 作为完成百分比。报告至少包括：leaf denominator coverage、verified-differential leaf count、unresolved P0/P1 divergences、critical-path forecast、defect escape、performance budget trend、migration validation、security finding age、evidence freshness、staffing/cost variance 和 upstream delta backlog。
