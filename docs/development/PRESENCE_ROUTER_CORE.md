# Rust connection and presence router core

Status: **source-level implementation candidate**.

This crate models authenticated connections, route ownership, presence membership, revocation fanout, drain, idle expiry and outbound queue budgets without implementing sockets, networking, clocks, databases or cross-node transport.

## Ownership

Every route is bound to `(connection, node, route_generation)`. Rebind increments the generation; stale nodes and stale generations cannot route, mutate presence, queue data or close the connection.

## Presence

Presence identity binds stream, user and session. Join requires an active non-draining connection. The join boundary accepts a typed `JoinPresenceRequest` that binds connection identity, route owner, route generation, stream, username, status and visibility/persistence flags into one adapter-ready validation object. Close, session revoke, user revoke and idle expiry remove all associated presence indexes atomically in the pure model.

## Revocation and drain

A user revocation epoch closes every current connection and rejects new connections presenting an older epoch. Node drain prevents new presence joins but allows heartbeat and bounded existing work until the adapter migrates or closes the connection.

## Backpressure

Outbound message and byte budgets reject before mutation. Queue accounting underflow is data loss. The actual socket writer and slow-consumer close policy remain adapter work.

## Remaining work

- WebSocket JSON and protobuf connections;
- distributed route registry and node-to-node fanout;
- durable or reconstructible presence decisions;
- heartbeat scheduler and reconnect cursor;
- socket revocation from session-family changes;
- slow-consumer integration;
- 100k connection/load/reconnect storm tests;
- immutable Nakama presence and lifecycle differential.

No socket, presence compatibility, C2, HA, production or public-online claim is made.
