# SDK consumer denominator candidate

Status: **candidate default-branch snapshots; no release or compatibility claim**

This slice creates the finite `DEN-SDK` consumer matrix for ten official Heroic Labs SDK repositories: JavaScript, .NET, Unity, Java, Unreal, C++, Godot, Swift, Defold and Dart.

Each repository is bound to an exact commit and root tree captured on 2026-08-28. These are discovery snapshots, not approved release lines or support windows. The workflow independently fetches and recomputes every Git tree using the pinned-source contract.

The generator derives the pinned Nakama HTTP/gRPC RPC names and realtime message names, scans each SDK source tree, and emits one candidate leaf for every SDK × server operation/message combination. Identifier matches are heuristic evidence only; missing or present matches do not grant coverage. Every leaf remains unclassified and records `transport_profile = null` and `support_window = null`.

SG1 remains open until reviewers select release lines, classify mandatory platforms/transports, resolve generated aliases and overloads, run official black-box clients, and verify serialization, errors, reconnect, refresh and version support.
