# Generated upstream manifests

This directory is reserved for **reviewed and locked** machine parity manifests.

The API/RTAPI workflow currently uploads exact-head candidate manifests as CI artifacts. Candidate files are not committed here because they remain fully unclassified and cannot receive SG1 or compatibility credit. After independent classification and review, a later change will place reviewed lock files here with their exact source and evidence digests.

Do not hand-edit generated leaf IDs, counts or hashes. Denominator decreases require an upstream delta or ADR, and platform extensions use the `TG-EXT-*` namespace rather than inflating Nakama parity coverage.
