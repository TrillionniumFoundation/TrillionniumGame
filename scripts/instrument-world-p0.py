#!/usr/bin/env python3
"""Instrument one exact World P0 test in a temporary clone."""

from pathlib import Path
import sys

OLD = '''        let (reconciliations, captured, file_violations) =
            transaction_reconciliation_counts(&source);
        reconcile_count += reconciliations;
        captured_transaction_reconcile_count += captured;
'''
NEW = '''        let (reconciliations, captured, file_violations) =
            transaction_reconciliation_counts(&source);
        if reconciliations > 0 {
            eprintln!(
                "P0_DIAG path={} reconciliations={} captured={} violations={:?}",
                path.display(), reconciliations, captured, file_violations
            );
        }
        reconcile_count += reconciliations;
        captured_transaction_reconcile_count += captured;
'''


def main() -> int:
    if len(sys.argv) != 2:
        return 64
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        print("instrumentation anchor did not match exactly once", file=sys.stderr)
        return 1
    path.write_text(text.replace(OLD, NEW), encoding="utf-8", newline="\n")
    print("WORLD_P0_INSTRUMENTATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
