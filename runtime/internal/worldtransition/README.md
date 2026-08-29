# `worldtransition`

Pure Go verifier for `trnm_world_transition_v1`.

This package prepares and reconstructs exact deterministic World requests,
verifies accepted/rejected results, and emits shadow observations. It owns no
network, database, session, signer, clock, random source or mutable global
state. Production orchestration belongs in the separate authority-safe
reservation/store layer.
