# Contracts

Versioned schemas shared with World, Hepta, Chain adapters and Integration
belong here until a dedicated generated-contract package is established.
Contracts must be usable without a sibling working-tree path.

## World transition v1

- `world-transition-v1.schema.json` is a byte-exact vendored World schema.
- `world-transition-v1-consumer-lock.json` pins the exact World commit, tree and
  source blob identities.
- `world-transition-v1-adapter-status.json` records the Nakama delivery state
  without granting cross-repository or release credit.

Integration, not this repository, owns the final exact multi-repository
component lock.
