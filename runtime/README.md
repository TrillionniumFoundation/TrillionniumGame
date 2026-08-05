# Runtime modules

This module registers two independent authoritative surfaces:

- `trnm_authoritative_match_v1` preserves the fixed two-participant match v1.
- `trnm_research_session_v1` owns Paper Raid admission, presence, ordered
  external-Agent actions, durable archive/cursor recovery, authorization-epoch
  key rotation, and signed cooperative completion for 3–5 participants.

Paper/project bytes never enter Nakama state. Actions carry typed payload bytes
and content-addressed references; Hepta owns long-lived research facts and
human authorship consent. Completion and authorization consumption are local
durable facts first, then exact-body outboxes retry until a signed ACK verifies
against the pinned Hepta issuer map. A 2xx response alone never marks delivery.

Run the focused short gates with `make research-contract research-core
research-restart`; `make research-compose-smoke` adds the pinned real
PostgreSQL/Nakama/Hepta-fixture/SIGKILL black box. `make paper-raid-check` runs
both layers.
