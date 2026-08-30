# Denominator review request package

Status: exact-source review input; no reviewed-lock, SG1, compatibility or production claim.

The package materializes all fourteen candidate denominator files and a one-to-one
review request for every extracted leaf. To avoid denominator shrinkage, every leaf
is conservatively proposed as `mandatory`. A proposal is not a classification
decision and contains no reviewer identity.

Each request includes:

- the exact candidate head and SHA-256;
- every stable leaf ID and signature hash;
- proposed owner, task, differential test, gate and evidence path;
- two required independent reviewer roles;
- exact remote workflow run/job identity and deterministic archive digest;
- every unresolved manual contract as an `owned-blocker` linked to an issue;
- a review-bundle template whose reviewer arrays are intentionally empty.

Independent reviewers may retain `mandatory`, select an approved optional profile,
or create a time-bounded versioned exclusion with an ADR and the required reviewer
count. They may not remove a leaf silently. Restricted Console ACL implementation
material remains a legal/manual blocker and must not be copied into this repository.

The package cannot close SG1. The reviewed-lock tool still requires exact leaf
coverage, author/reviewer separation, two real reviewers, accepted remote evidence,
manual-contract disposition and a separate global SG1 gate review.
