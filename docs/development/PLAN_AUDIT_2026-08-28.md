# TrillionniumGame 开发计划审计与优化报告

- 审计日期：2026-08-28
- 审计对象：`CURRENT_PLAN.md` v1、parity matrix、execution backlog、product gates、project boundary、upstream baseline、CI plan contract
- 审计结论：**方向正确，但 v1 仍是高质量战略计划，不是足以驱动 36 个月以上全量兼容工程的可执行项目控制系统。**
- 优化版本：`CURRENT_PLAN.md` v2

## 1. 总体评价

v1 的优点是范围没有被缩减为 Trillionnium 当前使用到的 Nakama 子集，明确排除了最终 Go 服务依赖，并覆盖协议、数据、Runtime、Console、IAP、迁移、HA 和退役。它还建立了 pinned upstream、差分 oracle、one-owner、bounded queue、fencing、fail-closed 和证据驱动发布等正确原则。

主要缺陷在于：范围虽然“写全了”，但 denominator、依赖、阶段门、验收阈值、迁移权威、资源预算和仓库接管操作还没有做到机器可闭环。若直接按 v1 开发，项目会在第二年出现“实现很多，但无法计算完成率、无法证明兼容、无法安全切换”的风险。

## 2. 发现与处置

| ID | 严重度 | 问题 | 后果 | v2 处置 |
| --- | --- | --- | --- | --- |
| AUD-P0-001 | P0 | 发布脚本只会创建新仓库，不能接管并重命名现有 `Trillionnium-Nakama` | 与实际仓库迁移目标冲突 | 新增现有仓库接管/重命名 runbook 和状态证据 |
| AUD-P0-002 | P0 | 未处理旧仓库分支、开放 PR、标签、Actions、Secrets、Environments 和历史证据 | 强推会破坏审计链和在途工作 | 禁止 force-push；保留 history/branches/PR，建立 archive 与 disposition 规则 |
| AUD-P0-003 | P0 | 74 条 parity 行只是领域汇总，却被 checker 当成完整 denominator | 无法证明“全量” | 引入多层机器 denominator：API、RTAPI、Console、Runtime、Config/CLI、SQL/Data、Ops/IAP |
| AUD-P0-004 | P0 | checker 要求“恰好 74 行”，与矩阵允许继续拆分相矛盾 | 细化反而导致 CI 失败 | 改为 roll-up 不少于 baseline；最终以生成 manifest hash 为真值 |
| AUD-P0-005 | P0 | Upstream baseline 未锁 Console proto/swagger、runtime interface、flags、server config、migration tree | oracle 与范围可能漂移 | 补齐关键 source roots，并要求 W0 生成全源 manifest |
| AUD-P0-006 | P0 | Backlog 只有标题和 acceptance，无依赖、owner、估算、risk、parity/test/gate 映射 | 无法排期、无法形成关键路径 | backlog 升级 v2，119 个任务全部增加执行与证据字段 |
| AUD-P0-007 | P0 | Product gates 没有定量 pass criteria 和 exact evidence contract | gate 可被人工主观关闭 | gate v2 要求 commit、artifact、environment、commands、result、reviewer、expiry |
| AUD-P0-008 | P0 | “drop-in replacement”没有说明 Go plugin ABI 被排除后的适用范围 | 形成误导性兼容声明 | 定义 C0–C5 兼容等级，只有完成 Go 源码迁移后才可称支持范围内的完整替换 |
| AUD-P0-009 | P0 | 迁移阶段没有逐实体权威矩阵 | session/party/match/IAP 可能双权威 | 新增 migration authority matrix，明确每个阶段唯一 owner 与回滚规则 |
| AUD-P0-010 | P0 | 36–48 个月内上游持续变化，但只有固定 v3.40 基线 | 交付时版本过旧或范围无限漂移 | 双轨策略：冻结基线 + 独立 upstream delta lane；SG7 冻结 1.0 最终兼容版本 |
| AUD-P1-001 | P1 | 一些系统不变量比 Nakama 更强，可能改变兼容行为 | “更安全”却不兼容 | 区分 compatibility profile 与 native hardened profile；任何外部差异必须版本化 |
| AUD-P1-002 | P1 | 预选 Boa/纯 Rust Lua，未先证明 Goja/GopherLua 语义等价 | Runtime 工作流极易返工 | 在 SG2–SG3 设置 engine bake-off；通过语义、隔离、性能、安全评分后 ADR 决策 |
| AUD-P1-003 | P1 | WASM component 是新增能力，却混入 Nakama parity denominator | 无端扩大关键路径 | 将 WASM 标记为平台扩展；不用于抬高 parity 完成率，也不替代 parity 缺口 |
| AUD-P1-004 | P1 | 所有 migration 都假设可逆 `down` | 大表/破坏性迁移不可安全回退 | 使用 expand/contract、forward-fix、PITR；只对确实可逆步骤提供 down |
| AUD-P1-005 | P1 | PostgreSQL 与 CockroachDB 被视为同一实现路径 | isolation、SQL、索引、序列与锁语义不同 | 独立 conformance profile、query plan 和故障矩阵 |
| AUD-P1-006 | P1 | 百万连接、百万 ticket、十亿对象是孤立数字 | 可能浪费资源或仍不满足真实需求 | 引入 DEV/COMPAT/PROD-S/PROD-M/STRETCH 五类容量 profile 与成本预算 |
| AUD-P1-007 | P1 | 差分允许 normalize，但未限定哪些字段绝不能 normalize | 可把真实不兼容“归一化掉” | 新增 normalizer registry；身份、权限、顺序、金额、版本、错误码等禁止 normalize |
| AUD-P1-008 | P1 | Production trace 语料缺少隐私处理合同 | 泄露 PII、token、receipt | 新增 fixture privacy pipeline、字段分类、不可逆脱敏和审批 |
| AUD-P1-009 | P1 | 多租户/project boundary 进入数据模型，但 Nakama 兼容默认是单项目 | 额外复杂度和越权风险 | 1.0 compatibility mode 保持 single-project；多租户作为版本化扩展 |
| AUD-P1-010 | P1 | error message、metrics、logs 的稳定等级未落成 | 不知道哪些差异阻塞发布 | 定义 wire-contract、behavior-contract、observability-contract、best-effort 四层 |
| AUD-P1-011 | P1 | Runtime/Console/IAP 三条超高风险线没有早期原型退出门 | 风险到后期才暴露 | 前 12 周安排 runtime engine、provider、Console UI 等尖峰和 go/no-go ADR |
| AUD-P1-012 | P1 | 缺少项目预算、人员爬坡和 P50/P80 工期 | 资源承诺无法审查 | 增加 P50 48 月、P80 60 月，按阶段给出 FTE 和关键岗位 |
| AUD-P2-001 | P2 | ADR 只有总决策，没有后续决策队列 | 实现细节会在代码中隐式决定 | 新增 ADR roadmap |
| AUD-P2-002 | P2 | 计划没有 earned-value/coverage 公式 | “完成百分比”容易失真 | 只按 denominator 权重、证据状态和 gate 计算，不按代码量计算 |

## 3. v2 的结构性变化

1. `FEATURE_PARITY_MATRIX.md` 只保留人类可读 roll-up；机器 denominator 由提取器生成。
2. `EXECUTION_BACKLOG.json` 升级为 v2 root manifest，详细 task 分散到 `docs/development/backlog/`。
3. `PRODUCT_GATES.json` 升级为 v2，任何关闭动作必须绑定不可变证据。
4. 增加仓库迁移、兼容等级、关键路径、迁移权威、oracle、容量/SLO、ADR、风险和证据文档。
5. 将计划周期改为 P50 48 个月、P80 60 个月；任何更短承诺都必须明确删减 denominator，而不是压缩测试。
6. 1.0 仍以完整 Nakama OSS server parity 为目标，但新增能力不得掩盖或替代 parity 缺口。

## 4. 仍需在 W0 实测确认的未知项

- API、RTAPI、Console RPC 的精确数量及 generated HTTP route 数；
- Go/Lua/JavaScript Runtime initializer、hook 和 module method 的精确 denominator；
- v3.40.0 全部配置键、默认值、环境变量、CLI flags 和 exit codes；
- PostgreSQL/CockroachDB migration 的表、列、约束、索引和兼容差异；
- Console UI 真实工作流、ACL 组合和高成本查询；
- 官方 SDK 版本矩阵及其 JSON/protobuf 差异；
- 各 IAP provider sandbox 在项目所在法域和账号下的可用性；
- Trillionnium 现有 Go runtime modules 的完整清单和迁移复杂度。

这些未知项不允许用估算数字冒充完成；SG1 关闭前必须由提取器和 oracle 生成证据。

## 5. 审计结论

v2 之后，该计划可以作为大型工程的正式规划基线，但仍不能声称开发已开始或全量范围已冻结。首要工作不是编写 endpoint，而是完成仓库安全接管、机器 denominator、可复现 oracle 和 Runtime engine 技术尖峰。只有 SG0–SG3 通过，后续 48–60 个月计划才具有可信的成本和交付含义。
