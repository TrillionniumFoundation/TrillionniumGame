#!/usr/bin/env bash
set -euo pipefail
cat >&2 <<'MSG'
TrillionniumGame already uses the canonical repository
TrillionniumFoundation/TrillionniumGame (repository ID 1323087470).

Creating, publishing or renaming to another repository would break source,
pull-request, issue, workflow and evidence identity. Repository migration is
complete; this guard intentionally performs no mutation.

Read docs/GOVERNANCE.md and PROJECT_BOUNDARY.md for the current authority.
MSG
exit 64
