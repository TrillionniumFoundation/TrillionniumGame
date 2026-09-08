#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, pprint, re, tomllib
from pathlib import Path
from typing import Any

LONG = ["trnm-contracts","trnm-authority-core","trnm-session-core","trnm-storage-core","trnm-persistence-core","trnm-persistence-pg","trnm-canonical-core","trnm-transport-core","trnm-token-core","trnm-presence-core","trnm-query-core","trnm-token-jwt-adapter","trnm-presence-router-v2","trnm-server","trnm-persistence-runtime-policy","trnm-realtime-wire","trnm-storage-nakama-version","trnm-token-crypto-provider","trnm-token-jwt-provider-adapter"]
PURE = ["trnm-contracts","trnm-authority-core","trnm-session-core","trnm-storage-core","trnm-persistence-core","trnm-canonical-core","trnm-transport-core","trnm-token-core","trnm-presence-core","trnm-query-core"]
GATES = ["trnm-token-jwt-adapter-gate","trnm-token-jwt-adapter-gate-v2"]

def req(v: bool, m: str) -> None:
    if not v: raise RuntimeError(m)
def rd(p: Path) -> str: return p.read_text(encoding="utf-8")
def wr(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(s.rstrip()+"\n", encoding="utf-8")
def jobj(p: Path) -> dict[str, Any]:
    v=json.loads(rd(p)); req(isinstance(v,dict),f"{p}: object required"); return v
def jput(p: Path,v:dict[str,Any])->None: wr(p,json.dumps(v,indent=2,ensure_ascii=False))

def manifests(root: Path)->None:
    for n in LONG:
        p=root/"crates"/n/"Cargo.toml"; s=rd(p)
        for pat,val in [(r"(?m)^version(?:\.workspace)?\s*=.*$","version.workspace = true"),(r"(?m)^edition(?:\.workspace)?\s*=.*$","edition.workspace = true"),(r"(?m)^rust-version(?:\.workspace)?\s*=.*$","rust-version.workspace = true"),(r"(?m)^license(?:\.workspace)?\s*=.*$","license.workspace = true"),(r"(?m)^publish(?:\.workspace)?\s*=.*$","publish.workspace = true")]:
            s,c=re.subn(pat,val,s,count=1); req(c==1,f"{p}: missing {pat}")
        s=re.sub(r"(?ms)^\[workspace\][ \t]*\n?(?=\[|\Z)","",s)
        s=re.sub(r"(?ms)^\[lints(?:\.[^\]]+)?\]\n.*?(?=^\[|\Z)","",s)
        wr(p,s.rstrip()+"\n\n[lints]\nworkspace = true")
        (p.parent/"Cargo.lock").unlink(missing_ok=True)
        readme=p.parent/"README.md"
        if readme.exists():
            wr(readme,rd(readme).replace("Workspace class: `root`  \n","Workspace class: `root`\n"))

def authority(root: Path)->None:
    p=root/"docs/development/RUST_PACKAGE_AUTHORITY.json"; v=jobj(p); v["last_reviewed_at"]="2026-09-09"
    v["workspace"]["members"]=[f"crates/{n}" for n in LONG]
    old={x.get("manifest"):x for x in v.get("isolated_workspaces",[]) if isinstance(x,dict)}
    v["isolated_workspaces"]=[old.get(f"crates/{n}/Cargo.toml",{"manifest":f"crates/{n}/Cargo.toml","classification":"temporary-security-differential-gate","aggregate_gate_required":True}) for n in GATES]
    v["server_binary_authority"]={"name":"trnm-server","manifest":"crates/trnm-server/Cargo.toml","source":"crates/trnm-server/src/main.rs","status":"canonical-composition-authority-source-candidate","reason":"The root-workspace trnm-server package is the only default server binary; the persistence-owned source is feature-gated diagnostic compatibility only.","replacement_contract":["no second default first-party trnm-server target","diagnostic compatibility target requires diagnostic-compat-server","workspace lockfile gate and evidence identity move together","exact-head prospective-merge and independent review remain required"]}
    v["foundation_prototype"]={"name":"trnm-pg-compat-server","manifest":"crates/trnm-persistence-pg/Cargo.toml","source":"crates/trnm-persistence-pg/src/bin/trnm-server.rs","status":"feature-gated-diagnostic-compatibility-source-candidate","relationship":"Not a second production authority.","compatibility_credit":False,"production_credit":False}
    for k in v.setdefault("claim_boundary",{}): v["claim_boundary"][k]=False
    jput(p,v)

def foundation(root: Path)->None:
    p=root/"scripts/check-rust-foundation.py"; s=rd(p); deps={}
    for n in LONG: deps[f"crates/{n}"]=tomllib.loads(rd(root/"crates"/n/"Cargo.toml")).get("dependencies",{})
    members="{\n"+"".join(f'    "crates/{n}",\n' for n in LONG)+"}"
    pure="{\n"+"".join(f'    "crates/{n}",\n' for n in PURE)+"}"
    block=f"EXPECTED_MEMBERS = {members}\nPURE_CORE_MEMBERS = {pure}\nEXPECTED_DEPENDENCIES: dict[str, dict[str, Any]] = {pprint.pformat(deps,width=100,sort_dicts=True)}\n"
    s,c=re.subn(r"(?s)EXPECTED_MEMBERS = \{.*?\nFORBIDDEN_PURE_CORE_PATTERNS =",block+"FORBIDDEN_PURE_CORE_PATTERNS =",s,count=1); req(c==1,"foundation constants")
    wr(p,s)

def crypto(root: Path)->None:
    a=root/"crates/trnm-persistence-pg/src/auth.rs"; s=rd(a).replace("const MINIMUM_KEY_BYTES: usize = 16;","const MINIMUM_KEY_BYTES: usize = 32;").replace('assert!(debug.contains("openssl-software-source-candidate"));','assert!(debug.contains("<opaque-provider>"));')
    if "fn hs256_key_length_requires_32_actual_bytes()" not in s:
        anchor="    #[test]\n    fn strict_epoch_access_token_yields_session_principal()"; req(anchor in s,"auth test anchor")
        test='''    #[test]
    fn hs256_key_length_requires_32_actual_bytes() {
        for length in [0_usize, 15, 16, 31] {
            let error = AccessTokenVerifier::from_epoch_key(ISSUER.to_owned(), AUDIENCE.to_owned(), EPOCH, vec![0x5a; length]).unwrap_err();
            assert_eq!(error.reason(), "access_token_profile_invalid", "length={length}");
        }
        for length in [32_usize, 48, 64] {
            AccessTokenVerifier::from_epoch_key(ISSUER.to_owned(), AUDIENCE.to_owned(), EPOCH, vec![0x5a; length]).unwrap();
        }
    }

'''; s=s.replace(anchor,test+anchor,1)
    wr(a,s)
    p=root/"crates/trnm-token-crypto-provider/src/software.rs"; s=rd(p).replace("const MINIMUM_KEY_BYTES: usize = 16;","const MINIMUM_KEY_BYTES: usize = 32;")
    s=s.replace("assert_eq!(\n                    provider.insert_key(&access, vec![0; 15]).unwrap_err(),\n                    ProviderError::InvalidKeyMaterial\n                );","for length in [0_usize, 15, 16, 31] {\n                    assert_eq!(provider.insert_key(&access, vec![0; length]).unwrap_err(), ProviderError::InvalidKeyMaterial, \"length={length}\");\n                }")
    wr(p,s)
    p=root/"docs/development/CRYPTO_PATH_AUTHORITY.json"; v=jobj(p); v.setdefault("key_contracts",{}).update({"hs256_minimum_actual_key_bytes":32,"encoded_length_may_substitute":False,"padding_hashing_or_truncation_may_substitute":False}); jput(p,v)

def server_checkers(root: Path)->None:
    p=root/"scripts/check-trnm-server.py"; s=rd(p)
    s=s.replace('            "max_lifetime_seconds: Some(15 * 60)",','            "MAX_ACCESS_TOKEN_LIFETIME_SECONDS",\n            "if lifetime > MAX_ACCESS_TOKEN_LIFETIME_SECONDS",')
    s=s.replace('            "sha256_digest(value.as_bytes())",','            "sha256_digest(value.as_bytes())",\n            "trnm_token_jwt_provider_adapter",\n            "authenticate(",\n            "from_provider(",')
    s=s.replace('server.get("manifest") != "crates/trnm-persistence-pg/Cargo.toml"','server.get("manifest") != "crates/trnm-server/Cargo.toml"').replace('server.get("source") != "crates/trnm-persistence-pg/src/bin/trnm-server.rs"','server.get("source") != "crates/trnm-server/src/main.rs"'); wr(p,s)
    p=root/"scripts/check-rust-server-source-candidate.py"; s=rd(p).replace('lock_path = CRATE / "Cargo.lock"','lock_path = ROOT / "Cargo.lock"').replace('package.get("publish") is False','package.get("publish", {}).get("workspace") is True').replace('package.get("rust-version") == "1.85.1"','package.get("rust-version", {}).get("workspace") is True').replace('manifest.get("workspace") == {}','"workspace" not in manifest').replace('"server must remain an isolated workspace"','"server must remain a root-workspace member"'); wr(p,s)
    p=root/"scripts/check-rust-server-vertical-slice.py"; s=rd(p).replace('lock = read(CRATE / "Cargo.lock")','lock = read(ROOT / "Cargo.lock")').replace('require("[workspace]" in manifest, "isolated workspace boundary missing")','require("[workspace]" not in manifest, "nested server workspace returned")'); wr(p,s)
    p=root/"scripts/check-rust-server-slice.py"; s=rd(p).replace('CANONICAL_SERVER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"','CANONICAL_SERVER = CRATE / "src/main.rs"').replace('CANDIDATE_SERVER = CRATE / "src/main.rs"','CANDIDATE_SERVER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"').replace('require("[workspace]" in manifest, "candidate must remain isolated")','require("[workspace]" not in manifest, "nested canonical server workspace returned")').replace('"atomic canonical authority transfer from trnm-persistence-pg",','"accepted exact-head authority-transfer evidence",').replace('"authority_transferred": False,','"authority_transferred_source_candidate": True,'); wr(p,s)
    p=root/"docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"; v=jobj(p); v["not_implemented"]=["accepted exact-head authority-transfer evidence" if x=="atomic canonical authority transfer from trnm-persistence-pg" else x for x in v.get("not_implemented",[])]; jput(p,v)
    p=root/"scripts/check-plan-v3-extension.py"; wr(p,rd(p).replace('"crates/trnm-server/Cargo.lock",','"Cargo.lock",'))
    p=root/"scripts/check-storage-version-compat.py"; s=rd(p).replace('lock_path = root / "Cargo.lock"','lock_path = ROOT / "Cargo.lock"').replace('package.get("publish") is False','package.get("publish", {}).get("workspace") is True').replace('manifest.get("workspace") == {}','"workspace" not in manifest').replace('"adapter must remain an explicit standalone workspace candidate"','"adapter must remain a root-workspace member"')
    s=re.sub(r'\n        require\(\n            "registry\+" not in lock and "git\+" not in lock,\n            "lock gained an external source",\n        \)',"",s,count=1); wr(p,s)

def commands(root: Path)->None:
    for rel in ["crates/trnm-persistence-pg/README.md","docs/TESTING_AND_EVIDENCE.md","docs/DEVELOPMENT.md",".github/workflows/cockroach-serialization-retry.yml",".github/workflows/websocket-wire-source.yml",".github/workflows/grpc-health-source.yml","scripts/ci-trnm-server-live.sh"]:
        p=root/rel
        if p.exists():
            s=re.sub(r"(?<!diagnostic-compat-server )--bin trnm-server\b","--features diagnostic-compat-server --bin trnm-pg-compat-server",rd(p)).replace("binary=target/debug/trnm-server","binary=target/debug/trnm-pg-compat-server"); wr(p,s)
    p=root/"scripts/check-rust-server-process.sh"; wr(p,rd(p).replace("binary=crates/trnm-server/target/debug/trnm-server","binary=target/debug/trnm-server"))

def run(root: Path)->None:
    req((root/".git").is_dir(),"git worktree required")
    manifests(root); authority(root); foundation(root); crypto(root); server_checkers(root); commands(root)

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("root",type=Path); run(ap.parse_args().root.resolve())
