SHELL := /usr/bin/env bash

.PHONY: preflight contract core restart compose-smoke research-contract research-core research-restart paper-raid-check check

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

research-contract: preflight
	bash scripts/check-nakama-research-contract.sh

research-core: preflight
	bash scripts/check-nakama-research-core.sh

research-restart: preflight
	bash scripts/check-nakama-research-restart.sh

paper-raid-check: research-contract research-core research-restart

check:
	bash scripts/check-nakama-p0.sh
