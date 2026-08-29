SHELL := /usr/bin/env bash

.PHONY: preflight contract core restart compose-smoke check

preflight:
	bash scripts/project-preflight.sh --dev

contract: preflight
	bash scripts/check-nakama-contract.sh

core: preflight
	bash scripts/check-nakama-core.sh

restart: preflight
	bash scripts/check-nakama-restart.sh

compose-smoke: preflight
	bash scripts/check-nakama-compose-smoke.sh

check:
	bash scripts/check-nakama-p0.sh
