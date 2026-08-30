# Local validation pointer

Local validation is diagnostic only and is intentionally not committed as release evidence. The authoritative result must be regenerated on the exact pull-request head by `trillionnium-game-merge-gate`, with run/job/artifact identities entered in `docs/evidence/index.json` and accepted by an independent reviewer.

The development environment generated `trillionniumgame-local-validation-v2.json` outside the repository to aid iteration. Its presence, even when all commands pass, does not close any remote-verification, compatibility, production or external-admin gap.
