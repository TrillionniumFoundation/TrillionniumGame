from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-partition-compat-v4.py <world-root>")

root = Path(sys.argv[1]).resolve()
base = Path(__file__).with_name("apply-partition-compat-v3.py")
subprocess.run([sys.executable, str(base), str(root)], check=True)

boundary = root / "trillionnium/crates/trnm-game-server/tests/settlement_game_server_boundary.rs"
text = boundary.read_text(encoding="utf-8")
marker = "#[test]\nfn direct_migration_includes_use_an_unambiguous_environment_macro() {"
if text.count(marker) != 1:
    raise SystemExit(f"migration safety test source drifted: {text.count(marker)}")
start = text.index(marker)
body_start = text.index("{", start)
depth = 0
end = None
for index in range(body_start, len(text)):
    char = text[index]
    if char == "{":
        depth += 1
    elif char == "}":
        depth -= 1
        if depth == 0:
            end = index + 1
            break
if end is None:
    raise SystemExit("migration safety test has no closing brace")
if end < len(text) and text[end] == "\n":
    end += 1
replacement = '''#[test]
fn direct_sources_never_restore_generated_authority() {
    for source in [
        read_crate_source_bundle("src/lib.rs"),
        read_settlement_worker_bundle(),
    ] {
        assert!(!source.contains("OUT_DIR"));
        assert!(!source.contains("trnm_game_server_lib_generated.rs"));
        assert!(!source.contains("trnm_settlement_worker_generated.rs"));
        assert!(!source.contains("src/lib.rs.in"));
        assert!(!source.contains("settlement_worker.rs.in"));
    }
}
'''
boundary.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
print("partition compatibility v4: PASS")
