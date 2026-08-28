#!/usr/bin/env bash
set -Eeuo pipefail
cat >&2 <<'MSG'
This project already lives in the existing Trillionnium-Nakama repository.
Creating a second TrillionniumGame repository would break repository identity,
history, pull-request and evidence continuity.

Read docs/development/REPOSITORY_TRANSITION_RUNBOOK.md, then run:

  TRNM_REPOSITORY_RENAME_CONFIRM=rename-Trillionnium-Nakama-to-TrillionniumGame \
    bash scripts/rename-existing-repository.sh
MSG
exit 64
