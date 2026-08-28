# Capacity、SLO 与性能证据规范

## 1. 不使用孤立规模数字

百万连接、百万 ticket、十亿 storage object 只能作为 STRETCH profile，不能替代真实产品容量和成本约束。每个环境选择一个 profile，并绑定用户模型、硬件、DB、地域和预算。

## 2. 容量 profiles

| Profile | 用途 | 并发/数据范围 | 证据要求 |
| --- | --- | --- | --- |
| DEV | 本地/CI | 小规模确定性 | correctness only |
| COMPAT | Oracle parity | 足以覆盖边界和并发 | differential + repeatability |
| PROD-S | 首个闭环生产 | 由实测需求定义 | HA、SLO、24/72h |
| PROD-M | 中型多节点 | 3–10x PROD-S | failover、cost、7d |
| STRETCH | 架构上限 | 1M socket/ticket、1B object 等 | 独立 benchmark，不承诺默认生产 |

## 3. SLO 指标

每个 profile 必须设定 API availability/error budget；auth/storage/social p50/p95/p99；WebSocket ingress-to-delivery；reconnect；presence cleanup；tick jitter/overrun/mailbox；matchmaker time-to-match/fairness/cancel；leaderboard reset；runtime CPU/memory/fuel/timeouts；DB pool/txn retry/replication lag；index lag/rebuild；RPO/RTO/restore；cost per CCU/request/match-hour/GB-month。

## 4. 性能兼容规则

先记录 pinned Nakama oracle 的同硬件基线。Rust 不要求每个微基准都更快，但不能在批准场景超过预算。Correctness、security 和 durability 不得为性能让步。Benchmark 报告置信区间、热身、样本、CPU throttling、allocator 和 DB cache。默认 regression threshold 5%，高噪声场景另行统计定义。超预算必须有 issue、owner 和 expiry。

## 5. 故障负载组合

测试覆盖正常、峰值、burst、reconnect storm、provider slow、DB failover、index rebuild、rolling upgrade、slow consumer、runtime overrun 和磁盘压力。健康环境吞吐不能独立关闭性能门。
