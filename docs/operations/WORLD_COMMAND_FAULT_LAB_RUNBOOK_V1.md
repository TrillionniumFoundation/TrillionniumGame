# World command isolated fault-lab runbook v1

## Preconditions

- Docker Engine and Compose are available.
- The checkout is clean and points to the exact reviewed branch head.
- No production credentials are present; the runner generates disposable fixture credentials.
- Trillionnium Chain is not required and must not be connected.

## Execute

```bash
bash scripts/check-world-command-deployed-runtime-v1-local.sh
bash scripts/run-world-command-deployed-fault-lab-v2.sh
```

The second command builds PostgreSQL, TrillionniumGame/Nakama, a TLS World transition fixture and a response-drop proxy. It creates a fresh Compose project and removes its volumes unless `TRNM_KEEP_WORLD_COMMAND_FAULT_LAB=1` is explicitly set.

## Evidence

The default evidence directory is:

```text
run/world-command-deployed-runtime-v1/
```

Required artifacts include the exact commit/tree, rendered Compose model, per-scenario client transcripts, core and World storage objects, atomicity decisions, PostgreSQL activity/lock captures, proxy/World statistics, process exit codes and `SHA256SUMS`.

## Failure posture

- A failed or missing scenario keeps the overall report failed.
- Empty/missing exact-head Actions runs are not success.
- A response loss preserves the original reservation/request identity.
- An ambiguous storage acknowledgement terminates the runtime generation.
- No fault-lab result authorizes public online, player markets, authority cutover, Chain finality or value settlement.
