"""Command-line entrypoint for the Nakama World transition adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .adapter import prepared_from_canonical_request, verify_world_result
from .canonical import canonical_dumps
from .contracts import NakamaAuthorityContext, TransitionContractError
from .shadow import compare_jsonl


def _context(value: Any) -> NakamaAuthorityContext:
    if not isinstance(value, dict):
        raise TransitionContractError("authority context must be an object")
    expected = {
        "authorization_id",
        "command_idempotency_key",
        "content_revision",
        "expected_tick",
        "global_event_sequence",
        "match_id",
        "match_version",
        "participant_roster_hash",
        "ruleset_revision",
    }
    if set(value) != expected:
        raise TransitionContractError(
            "authority context has unknown or missing fields"
        )
    context = NakamaAuthorityContext(**value)
    context.validate()
    return context


def _verify(args: argparse.Namespace) -> int:
    context_value = json.loads(args.context.read_text(encoding="utf-8"))
    context = _context(context_value)
    request_raw = args.request.read_text(encoding="utf-8")
    result_raw = args.result.read_text(encoding="utf-8")
    prepared = prepared_from_canonical_request(context, request_raw)
    verified = verify_world_result(prepared, result_raw)
    print(canonical_dumps(verified.to_record()))
    return 0


def _compare(args: argparse.Namespace) -> int:
    summary = compare_jsonl(args.world, args.candidate)
    encoded = canonical_dumps(summary)
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if summary["status"] == "matched" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify unsigned World transition v1 material inside a Nakama-owned "
            "authority context. This tool does not sign completion evidence."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-result")
    verify.add_argument("--context", required=True, type=Path)
    verify.add_argument("--request", required=True, type=Path)
    verify.add_argument("--result", required=True, type=Path)
    verify.set_defaults(operation=_verify)

    compare = subparsers.add_parser("compare-shadow")
    compare.add_argument("--world", required=True, type=Path)
    compare.add_argument("--candidate", required=True, type=Path)
    compare.add_argument("--summary", type=Path)
    compare.set_defaults(operation=_compare)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.operation(args))
    except (OSError, ValueError, TransitionContractError) as error:
        print(
            canonical_dumps(
                {
                    "contract_version": "trnm_nakama_world_transition_cli_error_v1",
                    "error": str(error),
                    "status": "blocked",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
