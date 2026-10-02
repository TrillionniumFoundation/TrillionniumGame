#!/usr/bin/env python3
"""Require the intended SQLSTATE/constraint; an unrelated failure is no proof."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def verify_error(text: str, state: str, constraint: str | None = None) -> None:
    observed = set(re.findall(r"(?:SQLSTATE\s*:\s*|ERROR:\s*)([0-9A-Z]{5})\b", text))
    if observed != {state}:
        raise ValueError("SQLSTATE did not match the intended rejection")
    if constraint is not None:
        # Do not accept a longer constraint name or a permission/syntax error
        # that happens to mention an identifier from the submitted SQL.
        pattern = r"constraint\s+(?:[\"'(])?" + re.escape(constraint) + r"(?:[\"')]|\b)"
        cockroach_field = r'^CONSTRAINT:\s*"?' + re.escape(constraint) + r'"?\s*$'
        if (re.search(pattern, text, re.IGNORECASE) is None
                and re.search(cockroach_field, text, re.IGNORECASE | re.MULTILINE) is None):
            raise ValueError("the intended constraint was not rejected")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--sqlstate", required=True)
    parser.add_argument("--constraint")
    args = parser.parse_args()
    if re.fullmatch(r"[0-9A-Z]{5}", args.sqlstate) is None:
        parser.error("SQLSTATE must contain exactly five uppercase alphanumeric characters")
    try:
        verify_error(args.stderr.read_text(encoding="utf-8"), args.sqlstate, args.constraint)
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
