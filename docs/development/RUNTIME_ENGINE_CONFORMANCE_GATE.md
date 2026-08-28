# JavaScript and Lua runtime engine conformance gate

Status: **comparison machinery and seed corpus only; no engine is selected**

The gate defines a finite JavaScript/Lua corpus, two execution lanes (`nakama-oracle` and `rust-candidate`), and ten contiguous attempts per case and lane. It compares return values, errors, stdout and host calls; validates lane determinism; and records resource maxima separately.

Divergence policy:

- P0: host-call/capability differences;
- P1: return/error/stdout or lane nondeterminism;
- P2: resource budget exceedance;
- P3: reserved diagnostics.

Network, filesystem, process, raw-socket and dynamic-library host capabilities invalidate evidence rather than becoming normalizable differences. A semantic candidate requires zero P0/P1. Resource exceedance remains visible as P2.

Even clean evidence plus two independent reviewers and an ADR produces only `independent-architecture-review-required`. It does not select a JavaScript or Lua engine, prove the complete Nakama Runtime API, close SG3 or grant production readiness.

The next implementation step is to build Goja/GopherLua oracle harnesses and candidate-engine harnesses that emit this observation schema for every seed case, then expand the corpus with all existing production modules and runtime hooks.
