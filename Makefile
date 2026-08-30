SHELL := /usr/bin/env bash

ISOLATED_MANIFESTS := \
	crates/trnm-token-jwt-adapter/Cargo.toml \
	crates/trnm-token-jwt-adapter-gate/Cargo.toml \
	crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml \
	crates/trnm-presence-router-v2/Cargo.toml

.PHONY: \
	preflight plan server-source rust security-critical python legacy-go \
	contract core restart compose-smoke legacy-p0 check

preflight:
	bash scripts/project-preflight.sh --dev

plan:
	python3 scripts/check-plan.py

server-source:
	python3 scripts/check-trnm-server.py

rust:
	cargo fmt --all -- --check
	cargo test --workspace --all-targets --locked
	cargo clippy --workspace --all-targets --locked -- -D warnings

security-critical:
	@set -euo pipefail; \
	for manifest in $(ISOLATED_MANIFESTS); do \
		cargo fmt --manifest-path "$$manifest" -- --check; \
		cargo test --manifest-path "$$manifest" --all-targets --locked; \
		cargo clippy --manifest-path "$$manifest" --all-targets --locked -- -D warnings; \
	done

python:
	python3 -m compileall -q scripts tools tests
	python3 -m unittest discover -s tests -p 'test_*.py' -v
	bash scripts/check-api-rtapi-denominator.sh

legacy-go:
	@set -euo pipefail; \
	files=$$(find runtime -type f -name '*.go' -print | sort); \
	test -n "$$files"; \
	unformatted=$$(printf '%s\n' "$$files" | xargs gofmt -l); \
	test -z "$$unformatted" || { printf '%s\n' "$$unformatted" >&2; exit 1; }; \
	cd runtime; \
	go test ./... -count=1; \
	go test -race ./... -count=1; \
	go vet ./...

# Historical Nakama + Go-plugin migration-input checks. These remain useful,
# but they are not the default full-Rust server or compatibility gate.
contract: preflight
	bash scripts/check-nakama-contract.sh

core: preflight
	bash scripts/check-nakama-core.sh

restart: preflight
	bash scripts/check-nakama-restart.sh

compose-smoke: preflight
	bash scripts/check-nakama-compose-smoke.sh

legacy-p0:
	bash scripts/check-nakama-p0.sh

check: plan server-source rust security-critical python legacy-go
