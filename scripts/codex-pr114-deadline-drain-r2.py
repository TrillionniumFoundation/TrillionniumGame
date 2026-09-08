#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / "scripts/codex-pr114-deadline-drain.py"), run_name="__main__")

http = ROOT / "crates/trnm-server/src/runtime/http.rs"
text = http.read_text(encoding="utf-8")
old = "use std::time::{Duration, Instant};\n"
if text.count(old) != 1:
    raise SystemExit(f"http time import drift: {text.count(old)}")
text = text.replace(old, "use std::time::Instant;\n", 1)
old = "    use std::thread;\n\n    use super::*;\n"
new = "    use std::thread;\n    use std::time::Duration;\n\n    use super::*;\n"
if text.count(old) != 1:
    raise SystemExit(f"http test import drift: {text.count(old)}")
text = text.replace(old, new, 1)
old = '''        assert!(matches!(
            result,
            Err(ServerError::Input(_))
                | Err(ServerError::Io(ref error))
                    if matches!(error.kind(), std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock)
        ));
'''
new = '''        assert!(match result {
            Err(ServerError::Input(_)) => true,
            Err(ServerError::Io(error)) => matches!(
                error.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            ),
            _ => false,
        });
'''
if text.count(old) != 1:
    raise SystemExit(f"http timeout assertion drift: {text.count(old)}")
text = text.replace(old, new, 1)
http.write_text(text, encoding="utf-8")

websocket = ROOT / "crates/trnm-server/src/runtime/websocket.rs"
text = websocket.read_text(encoding="utf-8")
old = "use std::time::{Duration, Instant};\n"
if text.count(old) != 1:
    raise SystemExit(f"websocket time import drift: {text.count(old)}")
text = text.replace(old, "use std::time::Instant;\n", 1)
websocket.write_text(text, encoding="utf-8")
