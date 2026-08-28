# Parity Denominator 规范

状态：v2 绑定规范  
目标：把“完整重写 Nakama OSS”转换为不可口头缩减、可由机器验证的有限集合。

## 1. Denominator 层级

| 层级 | 内容 | 主来源 |
| --- | --- | --- |
| D0 | upstream repository/tag/commit/tree/blob 身份 | Git object database / release metadata |
| D1 | HTTP/gRPC API service、method、route、message、enum、JSON mapping | `apigrpc.proto`、Swagger、`nakama-common/api.proto` |
| D2 | Realtime client message、server event、envelope、CID、error | `nakama-common/rtapi/realtime.proto` |
| D3 | Console service、route、message、ACL action、UI workflow | Console proto/swagger、ACL source、UI route manifest |
| D4 | Runtime initializer、hook、context、match interface、module API、Lua/JS declaration | runtime definitions、`index.d.ts`、server runtime adapters |
| D5 | Config key、default、validation、CLI flag、precedence、exit code | server config、flags、main |
| D6 | Database migration、table、column、constraint、index、sequence、data invariant | migrate/data/query source |
| D7 | Metrics、health、logs、shutdown、packaging、ports、runtime diagnostics | metrics、CLI、build、operational behavior |
| D8 | Provider adapter、IAP state、external callback and retry semantics | `iap/*`、social/provider source、public protocol |

`FEATURE_PARITY_MATRIX.md` 是 D1–D8 的领域 roll-up，不是 denominator 本体。

## 2. 每个 denominator item 的字段

```json
{
  "id": "TG-D1-API-000001",
  "class": "http_api_operation",
  "symbol": "AuthenticateDevice",
  "source": {
    "repository": "heroiclabs/nakama",
    "commit": "...",
    "path": "apigrpc/apigrpc.proto",
    "blob": "...",
    "start_line": 1,
    "end_line": 1
  },
  "signature_hash": "sha256:...",
  "compatibility_profile": "C1",
  "stability_tier": "wire-contract",
  "owner_role": "protocol",
  "workstream": "W2",
  "task_ids": ["TG-W2-002"],
  "test_ids": ["TG-DIFF-D1-API-000001"],
  "status": "specified",
  "evidence_refs": [],
  "waiver": null
}
```

## 3. 提取规则

1. 提取器在无网络、仅使用 vendored/pinned source snapshot 的环境运行。
2. 输入 manifest 必须绑定 repository、tag、commit、tree 和每个读取文件的 blob。
3. 输出按稳定 ID 排序，使用 canonical JSON，发布 SHA-256。
4. 同一输入必须得到字节一致输出；生成器版本和 toolchain 也进入 manifest。
5. 无法自动提取的项进入 `manual_contracts`，必须有来源和 reviewer，不能被忽略。
6. 删除或合并 item 必须记录 upstream delta 或 ADR；不能通过重新编号隐藏范围缩减。
7. Upstream 升级产生 add/change/remove delta，并给出兼容影响和迁移计划。

## 4. 覆盖率计算

不得按代码行、PR 数或 crate 数计算完成率。

```text
specified_coverage = specified_weight / total_weight
implemented_coverage = implemented_weight / total_weight
verified_coverage = verified_weight / total_weight
production_coverage = production_weight / total_weight
```

每个 item 默认权重为 1；只有经 ADR 批准，才可按风险增加权重，不能降低到 0。

状态权重：planned 0；specified 0.10；implemented 0.35；verified-unit 0.50；verified-differential 0.70；verified-integration 0.82；verified-load/security 0.92；production 1.00。

对外兼容完成率只能引用 `verified-differential` 及以上；生产完成率只能引用 `production`。

## 5. 发布约束

- SG1 前 denominator hash 不得为空。
- 中间版本允许有未实现项，但每个缺口必须可枚举。
- SG7 要求所有 C0–C4 mandatory item 达到相应 gate 的验证状态。
- SG9 要求所有 mandatory item 为 `production`，所有 waiver 关闭或被正式版本化排除。
- 新增平台能力使用 `TG-EXT-*`，不得计入 Nakama parity 百分比。

## 6. 变更治理

`denominator.lock.json` 是唯一机器真值。CI 必须拒绝 denominator 数量无解释下降、source blob 不匹配、item 无 owner/task/test、production evidence 过期，以及 waiver 无 owner/reason/expiry/migration path。
