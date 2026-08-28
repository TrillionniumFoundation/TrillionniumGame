# Pinned upstream source acquisition

Status: **implemented-local candidate; remote exact-source fetch blocked until Actions runs**  
Plan position: W0 upstream truth source, prerequisite to all machine denominators.

## Problem

A tag name, branch name, tarball URL or collection of individually downloaded files is not a sufficient compatibility denominator. A download can redirect, truncate, contain an unexpected tree, exploit archive extraction, or be modified after extraction while still retaining a misleading marker.

## Contract

The source fetcher accepts only:

```text
owner/name repository
non-zero exact 40-character commit SHA
non-zero exact 40-character root tree SHA
```

It then:

1. downloads the GitHub archive over HTTPS with a byte limit;
2. records the archive SHA-256 and resolved URL;
3. rejects traversal, links, special files, duplicate output paths, excess files and excess extracted bytes;
4. strips exactly one GitHub archive root prefix;
5. restores regular/executable file modes;
6. independently recomputes every Git blob/tree SHA-1 and the root Git tree;
7. refuses publication unless the recomputed root exactly equals the reviewed tree;
8. writes a canonical source-lock marker;
9. atomically publishes the verified checkout;
10. re-verifies the tree whenever the marker is consumed, detecting post-fetch tamper.

The lock marker itself and `.git` are excluded from source-tree hashing. Symlinks and special files are deliberately rejected in this compatibility input profile.

## Evidence boundary

A successful fetch proves only that one local checkout is byte/mode-equivalent to the exact reviewed Git tree. It does not classify parity leaves, close SG1, prove Oracle reproducibility, or earn compatibility/production credit.

## Exact initial use

The workflow is prepared to fetch and verify:

- `heroiclabs/nakama` commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`, tree `f3c9cfc2726d5543da1564629170f35b98e3797d`;
- `heroiclabs/nakama-common` commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`, tree `c6a7b9796b9c2a6b5118c74e5f213963a5001f14`.

The resulting checkouts are temporary workflow inputs. Exact candidate artifacts may contain evidence manifests and checksums, but the full third-party source archive is not uploaded as a Trillionnium release artifact.
