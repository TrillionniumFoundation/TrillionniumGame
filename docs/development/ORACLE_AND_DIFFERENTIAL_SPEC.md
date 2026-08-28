# Oracle 与差分验证规范

## 1. Oracle lock

每个 oracle run 必须绑定：Nakama tag/commit/tree；server artifact/image digest；matching `nakama-common`；DB exact version/image；runtime modules；config canonical bytes；locale/timezone/clock/random；provider mocks/TLS/network policy；harness commit/artifact；CPU/kernel/container runtime。

缺少任一字段的结果只能用于开发诊断，不能关闭 parity。

## 2. 两条 oracle lane

- immutable oracle：未经修改的官方 artifact；
- instrumented oracle：只为时间、随机数、provider、DB/trace 捕获加入最小补丁。

插桩 oracle 必须在注入字段之外与 immutable oracle 通过 equivalence suite；补丁、source blob、reviewer 和 artifact digest 进入 lock。

## 3. 执行模型

1. 从同一 logical fixture 构造隔离的 Nakama 与 Rust 初态；
2. 使用同一 request trace 驱动 HTTP/gRPC/JSON socket/protobuf socket；
3. 捕获 response、events、hook sequence、DB logical changes、provider requests、metrics；
4. 仅通过 registry normalization 处理不可控字段；
5. 输出 typed divergence 与不可变 artifact manifest；
6. 重放至少两次确认不是 nondeterministic test；
7. P0/P1 divergence 自动失败并进入 issue registry。

## 4. 禁止 normalization

user/session/device/provider identity；ACL/role/owner；command/event order、sequence、version；leaderboard rank/score；purchase product/transaction/amount/currency/status；storage collection/key/user/version/value hash；HTTP/gRPC/RT error code；JWT audience/issuer/subject/expiry/revocation；match/party/ticket membership；durable row/constraint outcomes；signing key ID 与 signature result。

## 5. 可注册 normalization

server-generated UUID/nonce 仅在无外部语义依赖时映射；timestamps 使用注入时钟而非随意截断；unordered map 仅在 schema 定义无序时排序；metrics scrape timestamp；保持引用关系的 ephemeral node/socket IDs。

每条 normalizer 必须有 ID、owner、test、reason、expiry 和 affected denominator items。

## 6. Divergence 等级

| 等级 | 例子 | 结果 |
| --- | --- | --- |
| P0 Integrity/Security | 身份错绑、ACL 越权、丢写、重复价值、stale owner | 立即阻止 merge/canary |
| P1 Compatibility | wire code、cursor、排序、hook、状态机不同 | 阻止对应 gate |
| P2 Performance/Observability | latency、metric、log contract 超预算 | 阻止 release profile |
| P3 Cosmetic/Internal | 未承诺文本、内部 query | 记录并审查 |

## 7. Fixture 隐私

Production trace 进入 corpus 前必须删除 token、password、receipt、provider assertion、IP、email、device ID 和自由文本；使用一致但不可逆 pseudonym；通过 schema allowlist；记录来源、合法依据、retention 和 reviewer；禁止公共 artifact 上传真实用户内容。

## 8. Evidence manifest

可计分 run 产出 canonical JSON：commit/tree、artifact digests、oracle lock、test IDs、input corpus hash、normalizer hash、result、divergences、limitations、reviewers、expiry。Gate 只引用 evidence manifest，不引用截图或 PR 文字。
