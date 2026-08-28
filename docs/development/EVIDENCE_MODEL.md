# TrillionniumGame Evidence Model

状态：**binding v1**

## 1. 原则

兼容性、性能、安全、迁移和运维 claim 只能由 evidence artifact 提升。CI 日志链接、截图或人工描述本身不是持久证据，除非被 hash、归档并写入 manifest。

## 2. Evidence 必须绑定

claim/gate/parity/task；upstream tag/commit/tree/blob/image；candidate commit/tree/artifact；environment/OS/arch/DB/toolchain/SDK/locale/timezone；fixtures/input digest；commands/runner；timestamps；result/metrics/divergences/normalization；limitations/expiry/reviewer；附件 SHA-256。

Schema：`docs/evidence/schemas/trillionnium-evidence-v1.schema.json`。

## 3. Evidence 类型

manifest、unit、property、fuzz、wire-differential、database-differential、runtime-differential、sdk-blackbox、migration-rehearsal、fault-injection、performance、endurance、security-review、penetration-test、backup-restore、canary、cutover、retirement。

## 4. Divergence

每个 divergence 有 id、severity、category、expected、observed、normalization rule、owner、status、waiver/expiry。P0/P1 未解释 divergence 阻止对应 profile/gate/canary。Security、identity、ACL、durability、IAP value、single-owner 或 completion integrity 差异不得通过 waiver 获得 production credit。

## 5. Evidence 生命周期

Evidence 与 commit/artifact 不可分离；环境或依赖变化可使其失效；security/performance/restore/key rotation/canary 必须有 expiry；deterministic manifests/vectors 在 source identity 不变时可长期有效；artifact 生成者不能单独批准自己的证据。
