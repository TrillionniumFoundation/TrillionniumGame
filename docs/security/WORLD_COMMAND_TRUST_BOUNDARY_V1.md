# World command trust boundary v1

Nakama/TrillionniumGame owns player authentication, authorization consumption, participant roles, command idempotency, canonical global sequence, match version, storage recovery, canonical roots and completion signing.

World receives only an opaque deterministic transition request containing the selected ruleset/content revisions, previous deterministic state, expected tick, opaque transition/command identifiers and canonical gameplay command. It receives no session, participant roster, global cursor, private authority key, completion signature, finality or wallet authority.

World responses are unsigned deterministic material. TrillionniumGame independently verifies request, state, replay, outcome and transition hashes before attempting a stale-fenced commit.

External HTTPS work occurs after reservation persistence and outside the core mutex and storage/database transaction. Final accepted state is committed with the original signed command event and the World journal through one version-fenced Nakama storage batch.

Trillionnium Chain is excluded from this trust boundary. Nothing here provides Chain finality or inclusion evidence.
