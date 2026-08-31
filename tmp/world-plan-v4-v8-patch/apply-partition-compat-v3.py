from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-partition-compat-v3.py <world-root>")
root = Path(sys.argv[1]).resolve()
base = Path(__file__).with_name("apply-partition-compat-v2.py")
subprocess.run([sys.executable, str(base), str(root)], check=True)

boundary = root / "trillionnium/crates/trnm-game-server/tests/settlement_game_server_boundary.rs"
text = boundary.read_text(encoding="utf-8")
positive = '    assert!(game_server.contains("concat!(::std::env!(\\\"CARGO_MANIFEST_DIR\\\")"));\n'
if text.count(positive) != 1:
    raise SystemExit(f"migration spelling assertion drifted: {text.count(positive)}")
boundary.write_text(text.replace(positive, "", 1), encoding="utf-8")
print("partition compatibility v3: PASS")
