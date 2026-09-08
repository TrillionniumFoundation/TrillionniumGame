#!/usr/bin/env python3
"""Idempotently converge repository-controlled Plan v3.2 architecture blockers.

This program deliberately does not alter gap status, evidence acceptance,
review decisions, production claims, cutover state, or Nakama retirement.
Those facts are derived from exact evidence and independent actors.
"""
from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path
from typing import Any, Iterable

LONG_LIVED = [
    "trnm-contracts",
    "trnm-authority-core",
    "trnm-session-core",
    "trnm-storage-core",
    "trnm-persistence-core",
    "trnm-persistence-pg",
    "trnm-canonical-core",
    "trnm-transport-core",
    "trnm-token-core",
    "trnm-presence-core",
    "trnm-query-core",
    "trnm-token-jwt-adapter",
    "trnm-presence-router-v2",
    "trnm-server",
    "trnm-persistence-runtime-policy",
    "trnm-realtime-wire",
    "trnm-storage-nakama-version",
    "trnm-token-crypto-provider",
    "trnm-token-jwt-provider-adapter",
]
TEMPORARY_GATES = [
    "trnm-token-jwt-adapter-gate",
    "trnm-token-jwt-adapter-gate-v2",
]
TEXT_SUFFIXES = {
    ".md",
    ".json",
    ".py",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
    ".rs",
    ".txt",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(read(path))
    require(isinstance(value, dict), f"{path}: top-level JSON must be an object")
    return value


def dump_json(path: Path, value: dict[str, Any]) -> None:
    write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def remove_temporary_workflows(root: Path) -> None:
    for relative in (
        ".github/workflows/architecture-source-export-one-shot.yml",
        ".github/workflows/architecture-test-name-one-shot.yml",
    ):
        (root / relative).unlink(missing_ok=True)


def ensure_dependency_block(text: str, dependencies: Iterable[str]) -> str:
    lines = list(dependencies)
    if "[dependencies]" not in text:
        anchor = "\n[lib]\n"
        require(anchor in text, "manifest has no [lib] anchor")
        block = "\n[dependencies]\n" + "\n".join(lines) + "\n"
        return text.replace(anchor, block + anchor, 1)
    for line in reversed(lines):
        if line not in text:
            text = text.replace("[dependencies]\n", f"[dependencies]\n{line}\n", 1)
    return text


def converge_jwt_backend(root: Path) -> None:
    dependency_lines = (
        'hmac = "=0.13.0"',
        'sha2 = "=0.11.0"',
        'subtle = "=2.6.1"',
    )
    for name in ["trnm-token-jwt-adapter", *TEMPORARY_GATES]:
        manifest_path = root / "crates" / name / "Cargo.toml"
        manifest = read(manifest_path)
        manifest = ensure_dependency_block(manifest, dependency_lines)
        manifest = manifest.replace(
            'description = "Strict dependency-free HS256 legacy and key-epoch JWT adapter"',
            'description = "Strict RustCrypto-backed HS256 legacy and key-epoch JWT adapter"',
        )
        write(manifest_path, manifest)

    backend = textwrap.dedent(
        '''\
        use hmac::{Hmac, Mac};
        use sha2::{Digest, Sha256};
        use subtle::ConstantTimeEq;

        type HmacSha256 = Hmac<Sha256>;

        #[must_use]
        pub fn digest(input: &[u8]) -> [u8; 32] {
            let output = Sha256::digest(input);
            let mut result = [0_u8; 32];
            result.copy_from_slice(&output);
            result
        }

        #[must_use]
        pub fn hmac_sha256(key: &[u8], chunks: &[&[u8]]) -> [u8; 32] {
            let mut mac = HmacSha256::new_from_slice(key)
                .expect("HMAC-SHA256 accepts every key length");
            for chunk in chunks {
                mac.update(chunk);
            }
            let output = mac.finalize().into_bytes();
            let mut result = [0_u8; 32];
            result.copy_from_slice(&output);
            result
        }

        #[must_use]
        pub fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
            left.len() == right.len() && bool::from(left.ct_eq(right))
        }

        #[cfg(test)]
        mod tests {
            use super::*;

            fn decode_hex(value: &str) -> Vec<u8> {
                value
                    .as_bytes()
                    .chunks_exact(2)
                    .map(|pair| {
                        let high = (pair[0] as char).to_digit(16).expect("hex") as u8;
                        let low = (pair[1] as char).to_digit(16).expect("hex") as u8;
                        (high << 4) | low
                    })
                    .collect()
            }

            #[test]
            fn sha256_known_answer() {
                assert_eq!(
                    digest(b"abc").as_slice(),
                    decode_hex("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
                );
            }

            #[test]
            fn hmac_sha256_rfc_4231_case_one() {
                assert_eq!(
                    hmac_sha256(&[0x0b; 20], &[b"Hi There"]).as_slice(),
                    decode_hex("b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7")
                );
            }

            #[test]
            fn comparison_rejects_full_width_length_differences() {
                for delta in [1_usize, 255, 256, 257, 512] {
                    let left = vec![0_u8; 32];
                    let right = vec![0_u8; 32 + delta];
                    assert!(!constant_time_eq(&left, &right));
                    assert!(!constant_time_eq(&right, &left));
                }
                assert!(constant_time_eq(&[7; 32], &[7; 32]));
                assert!(!constant_time_eq(&[7; 32], &[8; 32]));
            }
        }
        '''
    )
    write(root / "crates/trnm-token-jwt-adapter/src/sha256.rs", backend)


def remove_function(text: str, signature: str, next_anchor: str) -> str:
    pattern = rf"\nfn {re.escape(signature)}\s*\{{.*?\n\}}\n\n{re.escape(next_anchor)}"
    replaced, count = re.subn(
        pattern,
        f"\n{next_anchor}",
        text,
        count=1,
        flags=re.S,
    )
    return replaced if count else text


def converge_active_process_crypto(root: Path) -> None:
    app_path = root / "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs"
    app = read(app_path)
    app = remove_function(
        app,
        "constant_time_eq(left: &[u8], right: &[u8]) -> bool",
        "const fn http_status",
    )
    if "constant_time_eq(" in app:
        app = app.replace("constant_time_eq(", "memcmp::eq(")
    if "memcmp::eq(" in app and "use openssl::memcmp;" not in app:
        anchor = "use std::sync::{Arc, Mutex};\n"
        require(anchor in app, "app import anchor missing")
        app = app.replace(anchor, "use openssl::memcmp;\n" + anchor, 1)
    require("fn constant_time_eq" not in app, "custom app comparison helper remains")
    write(app_path, app)

    worker_path = root / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"
    worker = read(worker_path)
    worker = worker.replace("use trnm_token_jwt_adapter::sha256_digest;\n", "")
    if "use openssl::sha::sha256;" not in worker:
        anchor = "use trnm_contracts::{Digest32, DomainError, RetryClass};\n"
        require(anchor in worker, "outbox import anchor missing")
        worker = worker.replace(anchor, "use openssl::sha::sha256;\n\n" + anchor, 1)
    worker = worker.replace("sha256_digest(", "sha256(")
    worker = worker.replace(
        "if constant_time_eq(&actual, expected) {",
        "if actual.as_slice() == expected {",
    )
    worker = re.sub(
        r"\nfn constant_time_eq\(left: &\[u8\], right: &\[u8\]\) -> bool \{.*?\n\}\n\n#\[cfg\(test\)\]",
        "\n#[cfg(test)]",
        worker,
        count=1,
        flags=re.S,
    )
    require(
        "trnm_token_jwt_adapter::sha256_digest" not in worker,
        "outbox still imports private JWT SHA",
    )
    require("fn constant_time_eq" not in worker, "custom outbox comparison remains")
    write(worker_path, worker)


def workspace_block() -> str:
    members = "".join(f'  "crates/{name}",\n' for name in LONG_LIVED)
    excludes = "".join(f'  "crates/{name}",\n' for name in TEMPORARY_GATES)
    return (
        "[workspace]\n"
        "members = [\n"
        f"{members}"
        "]\n"
        "exclude = [\n"
        f"{excludes}"
        "]\n"
        'resolver = "2"\n\n'
    )


def converge_workspace(root: Path) -> None:
    cargo_path = root / "Cargo.toml"
    cargo = read(cargo_path)
    cargo, count = re.subn(
        r"(?s)\[workspace\]\n.*?(?=\[workspace\.package\])",
        workspace_block(),
        cargo,
        count=1,
    )
    require(count == 1, f"root workspace replacement count={count}")
    write(cargo_path, cargo)

    for name in LONG_LIVED:
        crate = root / "crates" / name
        manifest = crate / "Cargo.toml"
        text = re.sub(r"(?m)^\[workspace\][ \t]*\n?", "", read(manifest))
        write(manifest, text)
        (crate / "Cargo.lock").unlink(missing_ok=True)
        readme = crate / "README.md"
        if readme.exists():
            write(readme, read(readme).replace("Workspace class: `isolated`", "Workspace class: `root`"))

    registry_path = root / "docs/status/MODULE_DOCUMENTATION.json"
    registry = load_json(registry_path)
    rows = registry.get("modules")
    require(isinstance(rows, list), "module registry rows missing")
    for row in rows:
        if not isinstance(row, dict):
            continue
        module_id = row.get("id")
        if module_id in LONG_LIVED:
            row["workspace"] = "root"
        elif module_id in TEMPORARY_GATES:
            row["workspace"] = "isolated"
    summary = registry.setdefault("summary", {})
    summary.update(
        {
            "module_count": len(rows),
            "documented_count": len(rows),
            "root_workspace_count": len(LONG_LIVED),
            "isolated_workspace_count": len(TEMPORARY_GATES),
            "undocumented_count": 0,
        }
    )
    dump_json(registry_path, registry)

    for relative in (
        "docs/development/RUST_PACKAGE_AUTHORITY.json",
        "docs/status/COMPONENT_DOCUMENTATION.json",
    ):
        path = root / relative
        if path.exists():
            dump_json(path, rewrite_workspace_metadata(load_json(path)))


def row_identity(value: dict[str, Any]) -> str:
    return " ".join(
        str(value.get(key, ""))
        for key in ("id", "name", "path", "manifest", "cargo_manifest")
    )


def rewrite_workspace_metadata(value: Any) -> Any:
    if isinstance(value, list):
        return [rewrite_workspace_metadata(item) for item in value]
    if not isinstance(value, dict):
        return value
    identity = row_identity(value)
    if "crates/trnm-" in identity or identity.startswith("trnm-"):
        isolated = any(name in identity for name in TEMPORARY_GATES)
        for key in ("workspace", "workspace_class", "workspace_kind"):
            if key in value:
                value[key] = "isolated" if isolated else "root"
        for key in ("lockfile", "cargo_lock", "lock_path"):
            if key in value:
                value[key] = (
                    f"crates/{next(name for name in TEMPORARY_GATES if name in identity)}/Cargo.lock"
                    if isolated
                    else "Cargo.lock"
                )
    for key, item in list(value.items()):
        value[key] = rewrite_workspace_metadata(item)
    for key in ("root_workspace_count", "root_package_count", "workspace_member_count"):
        if key in value and isinstance(value[key], int):
            value[key] = len(LONG_LIVED)
    for key in ("isolated_workspace_count", "isolated_package_count"):
        if key in value and isinstance(value[key], int):
            value[key] = len(TEMPORARY_GATES)
    return value


def discover_bins(root: Path) -> list[tuple[str, str]]:
    bin_root = root / "crates/trnm-persistence-pg/src/bin"
    result: list[tuple[str, str]] = []
    for path in sorted(bin_root.glob("*.rs")):
        result.append((path.stem, path.relative_to(root / "crates/trnm-persistence-pg").as_posix()))
    for path in sorted(bin_root.glob("*/main.rs")):
        result.append((path.parent.name, path.relative_to(root / "crates/trnm-persistence-pg").as_posix()))
    names = [name for name, _ in result]
    require(len(names) == len(set(names)), f"duplicate persistence bin names: {names}")
    return result


def remove_manifest_bin_sections(text: str) -> str:
    return re.sub(r"(?ms)^\[\[bin\]\]\n.*?(?=^\[|\Z)", "", text).rstrip() + "\n"


def converge_single_server(root: Path) -> None:
    canonical_path = root / "crates/trnm-server/Cargo.toml"
    canonical = read(canonical_path).replace(
        'name = "trnm-server-composition-candidate"',
        'name = "trnm-server"',
    )
    write(canonical_path, canonical)

    pg_path = root / "crates/trnm-persistence-pg/Cargo.toml"
    pg = remove_manifest_bin_sections(read(pg_path))
    if "autobins = false" not in pg:
        pg = pg.replace('name = "trnm-persistence-pg"\n', 'name = "trnm-persistence-pg"\nautobins = false\n', 1)
    if "diagnostic-compat-server = []" not in pg:
        require("[features]\n" in pg, "persistence [features] anchor missing")
        pg = pg.replace("[features]\n", "[features]\ndiagnostic-compat-server = []\n", 1)
    blocks: list[str] = []
    for name, path in discover_bins(root):
        if name == "trnm-server":
            blocks.append(
                "\n[[bin]]\n"
                'name = "trnm-pg-compat-server"\n'
                f'path = "{path}"\n'
                'required-features = ["diagnostic-compat-server"]\n'
            )
        else:
            blocks.append("\n[[bin]]\n" f'name = "{name}"\n' f'path = "{path}"\n')
    require(any("trnm-pg-compat-server" in block for block in blocks), "compat server source not found")
    write(pg_path, pg.rstrip() + "\n" + "".join(blocks))

    exact_replacements = {
        "trnm-server-composition-candidate": "trnm-server",
        "crates/trnm-persistence-pg/Cargo.toml::trnm-server": "crates/trnm-server/Cargo.toml::trnm-server",
        "cargo test --package trnm-persistence-pg --bin trnm-server": "cargo test --package trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server",
        "cargo test -p trnm-persistence-pg --locked --bin trnm-server": "cargo test -p trnm-persistence-pg --features diagnostic-compat-server --locked --bin trnm-pg-compat-server",
        "cargo test -p trnm-persistence-pg --bin trnm-server": "cargo test -p trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server",
        "cargo build --package trnm-persistence-pg --bin trnm-server": "cargo build --package trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server",
        "cargo run --package trnm-persistence-pg --bin trnm-server": "cargo run --package trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server",
    }
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES or ".git" in path.parts:
            continue
        try:
            text = read(path)
        except UnicodeDecodeError:
            continue
        original = text
        for old, new in exact_replacements.items():
            text = text.replace(old, new)
        if path.as_posix().startswith("crates/trnm-persistence-pg/"):
            text = text.replace("CARGO_BIN_EXE_trnm-server", "CARGO_BIN_EXE_trnm-pg-compat-server")
        if text != original:
            write(path, text)


def update_crypto_authority(root: Path) -> None:
    path = root / "docs/development/CRYPTO_PATH_AUTHORITY.json"
    if not path.exists():
        return
    authority = load_json(path)
    providers = authority.setdefault("approved_primitive_providers", [])
    if isinstance(providers, list):
        if not any(isinstance(row, dict) and row.get("id") == "PROVIDER-RUSTCRYPTO-JWT" for row in providers):
            providers.append(
                {
                    "id": "PROVIDER-RUSTCRYPTO-JWT",
                    "packages": [
                        {"name": "hmac", "version": "0.13.0"},
                        {"name": "sha2", "version": "0.11.0"},
                        {"name": "subtle", "version": "2.6.1"},
                    ],
                    "operations": [
                        "SHA-256 digest",
                        "HMAC-SHA256 issue and verification",
                        "constant-time equal-length byte comparison",
                    ],
                    "source_paths": ["crates/trnm-token-jwt-adapter/src/sha256.rs"],
                    "profile": "reviewed-library-source-candidate",
                    "production_approval": False,
                    "independent_security_acceptance": False,
                }
            )
        openssl_row = next(
            (
                row
                for row in providers
                if isinstance(row, dict) and row.get("id") == "PROVIDER-OPENSSL-HMAC-SHA256"
            ),
            None,
        )
        if openssl_row is not None:
            operation = "SHA-256 for durable spool receipts, retry jitter and failure-reason digests"
            operations = openssl_row.setdefault("operations", [])
            if operation not in operations:
                operations.append(operation)

    rows = {
        row.get("id"): row
        for row in authority.get("path_classifications", [])
        if isinstance(row, dict)
    }
    outbox = rows.get("CRYPTO-PATH-ACTIVE-OUTBOX-DIGEST")
    if outbox is not None:
        outbox.update(
            {
                "classification": "active-durable-spool-reviewed-primitive-source-candidate",
                "implementation": "openssl::sha::sha256 and ordinary equality for public deterministic spool bytes",
                "primitive_provider": "PROVIDER-OPENSSL-HMAC-SHA256",
                "private_primitive_reachable": False,
                "required_acceptance": "Byte-stability, fault, data-integrity and security evidence remain independently accepted facts.",
                "claim_credit": False,
            }
        )
        outbox.pop("required_remediation", None)
    reference = rows.get("CRYPTO-PATH-COMPATIBILITY-REFERENCE")
    if reference is not None:
        reference.update(
            {
                "classification": "non-production-compatibility-adapter-reviewed-library-source-candidate",
                "implementation": "strict adapter backed by RustCrypto hmac/sha2 and subtle primitives",
                "primitive_provider": "PROVIDER-RUSTCRYPTO-JWT",
                "private_primitive_reachable": False,
                "required_acceptance": "Three-way Nakama differential, malformed-token/fuzz corpus, provenance and independent cryptographic review remain required.",
                "claim_credit": False,
            }
        )
        reference.pop("required_remediation", None)
    summary = authority.setdefault("summary", {})
    summary["all_active_crypto_paths_reviewed_provider_backed"] = True
    summary["private_compatibility_implementation_removed"] = True
    summary["production_key_provider_accepted"] = False
    summary["gap_closed"] = False
    summary["production_ready"] = False
    dump_json(path, authority)


def checker_sources() -> dict[str, str]:
    return {
        "scripts/check-production-crypto-paths.py": textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            from __future__ import annotations
            import json
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[1]
            APP = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs"
            WORKER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"
            AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
            def require(value: bool, message: str) -> None:
                if not value:
                    raise SystemExit(message)
            app = APP.read_text(encoding="utf-8")
            worker = WORKER.read_text(encoding="utf-8")
            require("fn constant_time_eq" not in app, "custom app comparison helper is forbidden")
            require("memcmp::eq(" in app, "app must use OpenSSL constant-time comparison")
            require("fn constant_time_eq" not in worker, "custom outbox comparison helper is forbidden")
            require("trnm_token_jwt_adapter::sha256_digest" not in worker, "outbox must not reach private JWT SHA")
            require("openssl::sha::sha256" in worker, "outbox must use a reviewed SHA-256 provider")
            authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
            rows = {row["id"]: row for row in authority["path_classifications"]}
            require(rows["CRYPTO-PATH-ACTIVE-OUTBOX-DIGEST"]["private_primitive_reachable"] is False, "outbox authority is stale")
            require(authority["summary"]["gap_closed"] is False, "source cannot self-close security review")
            print("production crypto path validation passed")
            '''
        ),
        "scripts/check-jwt-rustcrypto-backend.py": textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            from __future__ import annotations
            import json
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[1]
            MANIFESTS = [
                ROOT / "crates/trnm-token-jwt-adapter/Cargo.toml",
                ROOT / "crates/trnm-token-jwt-adapter-gate/Cargo.toml",
                ROOT / "crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml",
            ]
            SOURCE = ROOT / "crates/trnm-token-jwt-adapter/src/sha256.rs"
            AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
            def require(value: bool, message: str) -> None:
                if not value:
                    raise SystemExit(message)
            for manifest in MANIFESTS:
                text = manifest.read_text(encoding="utf-8")
                for dependency in ("hmac", "sha2", "subtle"):
                    require(f"{dependency} =" in text, f"{manifest}: missing {dependency}")
            source = SOURCE.read_text(encoding="utf-8")
            for marker in ("Hmac<Sha256>", "Sha256::digest", "ConstantTimeEq", ".ct_eq("):
                require(marker in source, f"JWT backend missing {marker}")
            for marker in ("ROUND_CONSTANTS", "wrapping_add", "rotate_right"):
                require(marker not in source, f"private SHA marker remains: {marker}")
            authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
            rows = {row["id"]: row for row in authority["path_classifications"]}
            row = rows["CRYPTO-PATH-COMPATIBILITY-REFERENCE"]
            require(row["private_primitive_reachable"] is False, "crypto authority is stale")
            require(authority["summary"]["gap_closed"] is False, "library migration cannot self-close review")
            print("JWT RustCrypto backend validation passed")
            '''
        ),
        "scripts/check-workspace-convergence.py": textwrap.dedent(
            f'''\
            #!/usr/bin/env python3
            from __future__ import annotations
            import json
            import tomllib
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[1]
            LONG_LIVED = {LONG_LIVED!r}
            GATES = {TEMPORARY_GATES!r}
            def require(value: bool, message: str) -> None:
                if not value:
                    raise SystemExit(message)
            manifest = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
            workspace = manifest["workspace"]
            require(set(workspace["members"]) == {{f"crates/{{name}}" for name in LONG_LIVED}}, "root members are stale")
            require(set(workspace["exclude"]) == {{f"crates/{{name}}" for name in GATES}}, "root exclusions are stale")
            for name in LONG_LIVED:
                crate = ROOT / "crates" / name
                require("[workspace]" not in (crate / "Cargo.toml").read_text(encoding="utf-8"), f"{{name}} remains nested")
                require(not (crate / "Cargo.lock").exists(), f"{{name}} retains a nested lockfile")
            registry = json.loads((ROOT / "docs/status/MODULE_DOCUMENTATION.json").read_text(encoding="utf-8"))
            rows = {{row["id"]: row for row in registry["modules"]}}
            require(all(rows[name]["workspace"] == "root" for name in LONG_LIVED), "registry root metadata is stale")
            require(all(rows[name]["workspace"] == "isolated" for name in GATES), "registry gate metadata is stale")
            print("workspace convergence validation passed")
            '''
        ),
        "scripts/check-single-server-authority.py": textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            from __future__ import annotations
            import json
            import subprocess
            import tomllib
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[1]
            def require(value: bool, message: str) -> None:
                if not value:
                    raise SystemExit(message)
            metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=ROOT, text=True))
            owners = []
            compat = []
            for package in metadata["packages"]:
                for target in package["targets"]:
                    if "bin" not in target["kind"]:
                        continue
                    if target["name"] == "trnm-server":
                        owners.append(package["name"])
                    if target["name"] == "trnm-pg-compat-server":
                        compat.append((package["name"], target.get("required-features", [])))
            require(owners == ["trnm-server"], f"default server owners must be exactly one: {owners}")
            require(compat == [("trnm-persistence-pg", ["diagnostic-compat-server"])], f"compat target is invalid: {compat}")
            persistence = tomllib.loads((ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8"))
            require(persistence["package"].get("autobins") is False, "persistence autobins must be false")
            print("single server authority validation passed")
            '''
        ),
    }


def test_sources() -> dict[str, str]:
    modules = {
        "test_production_crypto_paths.py": "scripts/check-production-crypto-paths.py",
        "test_jwt_rustcrypto_backend.py": "scripts/check-jwt-rustcrypto-backend.py",
        "test_workspace_convergence.py": "scripts/check-workspace-convergence.py",
        "test_single_server_authority.py": "scripts/check-single-server-authority.py",
    }
    result: dict[str, str] = {}
    for filename, checker in modules.items():
        result[f"tests/control_plane/{filename}"] = textwrap.dedent(
            f'''\
            from __future__ import annotations
            import subprocess
            import unittest
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[2]
            class RepositoryCheckerTest(unittest.TestCase):
                def test_repository_checker(self) -> None:
                    subprocess.run(["python3", "{checker}"], cwd=ROOT, check=True)
            if __name__ == "__main__":
                unittest.main()
            '''
        )
    return result


def install_checkers(root: Path) -> None:
    for relative, source in {**checker_sources(), **test_sources()}.items():
        path = root / relative
        write(path, source)
        if relative.startswith("scripts/"):
            path.chmod(0o755)


def update_merge_gate(root: Path) -> None:
    path = root / ".github/workflows/trillionnium-game-merge-gate.yml"
    text = read(path)
    anchor = (
        "      - name: Validate Rust server source contract\n"
        "        run: python3 scripts/check-trnm-server.py\n"
    )
    require(anchor in text, "merge-gate server anchor missing")
    steps = "".join(
        (
            f"      - name: {name}\n"
            f"        run: python3 {script}\n"
        )
        for name, script in (
            ("Validate production cryptographic paths", "scripts/check-production-crypto-paths.py"),
            ("Validate JWT RustCrypto backend", "scripts/check-jwt-rustcrypto-backend.py"),
            ("Validate converged Rust workspace topology", "scripts/check-workspace-convergence.py"),
            ("Validate unique server composition authority", "scripts/check-single-server-authority.py"),
        )
        if f"run: python3 {script}" not in text
    )
    if steps:
        text = text.replace(anchor, anchor + steps, 1)

    for script in (
        "scripts/check-production-crypto-paths.py",
        "scripts/check-jwt-rustcrypto-backend.py",
        "scripts/check-workspace-convergence.py",
        "scripts/check-single-server-authority.py",
    ):
        if script not in text:
            compile_anchor = "            scripts/check-trnm-server.py\n"
            require(compile_anchor in text, "merge-gate compile anchor missing")
            text = text.replace(compile_anchor, compile_anchor + f"            {script}\n", 1)

    matrix_pattern = re.compile(
        r"(?ms)(  isolated-rust:.*?      matrix:\n        include:\n).*?(^    steps:\n)",
    )
    match = matrix_pattern.search(text)
    if match:
        include = (
            "          - component: token-jwt-adapter-gate\n"
            "            manifest: crates/trnm-token-jwt-adapter-gate/Cargo.toml\n"
            "          - component: token-jwt-adapter-gate-v2\n"
            "            manifest: crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml\n"
        )
        text = text[: match.start(1)] + match.group(1) + include + match.group(2) + text[match.end(2) :]
    write(path, text)


def update_docs(root: Path) -> None:
    development_path = root / "docs/DEVELOPMENT.md"
    development = read(development_path)
    old = (
        "The repository also contains intentionally isolated Cargo workspaces. "
        "The aggregate merge gate discovers the package authority and runs format, "
        "all-target tests and strict Clippy for every isolated workspace. A root-workspace "
        "pass alone is not whole-repository coverage."
    )
    new = (
        "All long-lived Rust products and adapters are members of the root Cargo workspace. "
        "Only the two temporary JWT differential gate packages remain isolated until an "
        "accepted retirement packet exists. The aggregate merge gate runs the root workspace "
        "and both temporary gates; a root-workspace pass alone does not retire a gate."
    )
    development = development.replace(old, new)
    development = development.replace(
        "The database-backed server candidate is `crates/trnm-persistence-pg/src/bin/trnm-server.rs`. The standalone `crates/trnm-server` executable is a foundation candidate, not a second production authority.",
        "The only default server binary is `crates/trnm-server`::`trnm-server`. The database-backed source at `crates/trnm-persistence-pg/src/bin/trnm-server.rs` builds only as the explicit `diagnostic-compat-server` target `trnm-pg-compat-server`.",
    )
    write(development_path, development)

    architecture_path = root / "docs/ARCHITECTURE.md"
    architecture = read(architecture_path)
    architecture = architecture.replace(
        "`crates/trnm-persistence-pg/src/bin/trnm-server.rs` is the current canonical database-backed integration slice and the server binary named by the machine package authority;",
        "`crates/trnm-server` is the only package allowed to publish the default `trnm-server` process and owns the canonical composition root;",
    )
    architecture = architecture.replace(
        "`crates/trnm-server` is a dependency-bounded foundation executable used to keep process/ingress/core contracts independently buildable.",
        "`crates/trnm-persistence-pg/src/bin/trnm-server.rs` is retained only as the feature-gated `trnm-pg-compat-server` diagnostic and compatibility harness.",
    )
    write(architecture_path, architecture)

    for path in (
        root / "crates/trnm-token-jwt-adapter/README.md",
        root / "docs/SECURITY_AND_PRIVACY.md",
        root / "SECURITY.md",
    ):
        if path.exists():
            text = read(path)
            text = text.replace("dependency-free HS256", "RustCrypto-backed HS256")
            text = text.replace(
                "private SHA-256, HMAC-SHA256 and equality code",
                "reviewed RustCrypto SHA-256, HMAC-SHA256 and subtle comparison primitives",
            )
            write(path, text)


def add_status_authority(root: Path) -> None:
    status_path = root / "docs/status/ARCHITECTURE_CONVERGENCE_STATUS.json"
    status = {
        "schema": "trillionnium.architecture-convergence-status.v1",
        "project_id": "trillionnium-game",
        "plan_version": 3,
        "identity_rule": "Exact commit, tree, workflow, artifact and prospective-merge identity are supplied by retained execution evidence; this source file does not guess its own final commit.",
        "repository_controlled_source": {
            "one_default_server_authority": True,
            "long_lived_rust_workspace_converged": True,
            "private_crypto_primitives_absent_from_active_and_compatibility_implementations": True,
            "temporary_one_shot_workflows_absent": True,
            "temporary_isolated_jwt_gate_count": len(TEMPORARY_GATES),
        },
        "external_and_product_facts": {
            "exact_head_workflow_collection_accepted": False,
            "prospective_merge_packet_accepted": False,
            "conflict_free_specialist_review_accepted": False,
            "administrative_no_bypass_packet_accepted": False,
            "all_denominator_family_locks_accepted": False,
            "global_sg1_acceptance": False,
            "complete_nakama_surface_implemented": False,
            "production_kms_hsm_accepted": False,
            "multi_node_ha_pitr_endurance_accepted": False,
            "shadow_canary_cutover_retirement_accepted": False,
        },
        "claims": {
            "source_convergence_candidate": True,
            "accepted_evidence": False,
            "all_gaps_closed": False,
            "production_ready": False,
            "public_online": False,
            "cutover_authorized": False,
            "nakama_retired": False,
        },
    }
    dump_json(status_path, status)

    authority_path = root / "docs/DOCUMENTATION_AUTHORITY.json"
    authority = load_json(authority_path)
    documents = authority.get("machine_control_documents")
    require(isinstance(documents, list), "documentation machine controls missing")
    relative = "docs/status/ARCHITECTURE_CONVERGENCE_STATUS.json"
    if relative not in documents:
        anchor = "docs/status/CURRENT_STATE.json"
        index = documents.index(anchor) + 1 if anchor in documents else len(documents)
        documents.insert(index, relative)
    dump_json(authority_path, authority)


def run(root: Path) -> None:
    require((root / ".git").exists(), f"{root}: not a Git working tree")
    remove_temporary_workflows(root)
    converge_jwt_backend(root)
    converge_active_process_crypto(root)
    converge_workspace(root)
    converge_single_server(root)
    update_crypto_authority(root)
    install_checkers(root)
    update_merge_gate(root)
    update_docs(root)
    add_status_authority(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    run(args.root.resolve())
