# World command deployed runtime test matrix v1

| ID | Scenario | Required result |
|---|---|---|
| WCDR-001 | normal accepted transition | one canonical command event and one receipt |
| WCDR-002 | response dropped after upstream success | identical request identity; exactly-once convergence |
| WCDR-003 | delayed World response | no long PostgreSQL transaction or business lock |
| WCDR-004 | process exit after reservation persistence | original pending reservation recovered |
| WCDR-005 | process exit after result verification | restart commits or replays exactly once |
| WCDR-006 | tampered result | no canonical state advance |
| WCDR-007 | stale core/state/generation fence | no event/state/receipt advance |
| WCDR-008 | duplicate committed command | original receipt/event; no World call |

The exact-head workflow currently executes WCDR-001 through WCDR-005. WCDR-006 through WCDR-008 remain covered at source/unit level and must be added to the deployed matrix before cutover review.

Chain behavior is not part of this matrix.
