#!/usr/bin/env python3
"""Execute the exact Surface 2 helper with corrected nested-source escaping."""
from __future__ import annotations

from pathlib import Path

SOURCE_PATH = Path(__file__).with_name("tmp_social_core_rebind_repair.py")
source = SOURCE_PATH.read_text(encoding="utf-8")
start = source.index("def write_registration_test(")
end = source.index("\ndef main()", start)
segment = source[start:end]
old = '+ "\\n", encoding="utf-8"'
new = '+ "\\\\n", encoding="utf-8"'
count = segment.count(old)
if count != 3:
    raise SystemExit(f"unexpected nested newline literal count: {count}")
segment = segment.replace(old, new)
source = source[:start] + segment + source[end:]
namespace = {
    "__name__": "__main__",
    "__file__": str(SOURCE_PATH),
}
exec(compile(source, str(SOURCE_PATH), "exec"), namespace)
