# Foundation schema rollback barrier

The initial schema is not automatically reversible by dropping tables. An approved rollback must:

1. stop new writes with a generation-bound write fence;
2. drain or quarantine outbox work;
3. export entity heads, commands, events, outbox, sessions, tokens, leases, and storage objects;
4. verify row counts, primary keys, digests, sequences, ACLs, and idempotency identities;
5. rebuild the destination from an empty database;
6. import idempotently and run semantic comparison;
7. preserve all acknowledged commands and applied external-effect receipts;
8. obtain independent data and operations approval.

`DROP TABLE` is not an accepted production rollback strategy.
