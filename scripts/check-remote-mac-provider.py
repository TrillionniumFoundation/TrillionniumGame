#!/usr/bin/env python3
"""Fail-closed controls for the opaque remote MAC provider boundary."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"crates/trnm-token-crypto-provider/src/remote.rs"
CONTRACT=ROOT/"contracts/security/remote-mac-provider.v1.json"
REQUIRED=(
  "pub trait RemoteMacTransport: Send + Sync",
  "key_reference: String",
  "MAX_REMOTE_MAC_TIMEOUT",
  "REMOTE_HS256_TAG_BYTES: usize = 32",
  "request_id: [u8; 16]",
  "RemoteMacError::ProtocolViolation",
  "RemoteMacError::TransportUnavailable",
  "RemoteMacError::VerificationRejected",
  "impl fmt::Debug for RemoteMacRequestKind",
  "impl fmt::Debug for RemoteMacRequest",
  "impl fmt::Debug for RemoteMacResponse",
  "<opaque-key-reference>",
  "<redacted-tag>",
  "mismatched_response_and_transport_failure_never_fall_back",
  "request_kind_and_response_debug_never_expose_sensitive_bytes",
)
FORBIDDEN=(
  "raw_key",
  "key_material",
  "private_key",
  "SoftwareHs256Provider",
  "new_from_slice",
  "Hmac<",
  "sha256_digest",
  "fallback",
)
class ValidationError(RuntimeError): pass
def require(value:bool,message:str)->None:
  if not value: raise ValidationError(message)
def validate(source:str,contract:dict)->None:
  for marker in REQUIRED: require(marker in source,f"remote provider missing {marker}")
  for marker in FORBIDDEN: require(marker not in source,f"remote provider contains forbidden marker {marker}")
  require(contract.get("schema")=="trillionnium.remote-mac-provider.v1","contract schema")
  require(contract.get("raw_key_bytes_enter_application_process") is False,"raw key boundary")
  require(contract.get("local_software_fallback_allowed") is False,"fallback boundary")
  require(contract.get("tag_bytes")==32,"tag width")
  require(contract.get("maximum_timeout_ms")==5000,"timeout bound")
  claims=contract.get("claim_boundary",{})
  require(claims and not any(claims.values()),"positive production or acceptance claim")
def main()->int:
  try: validate(SOURCE.read_text(encoding="utf-8"),json.loads(CONTRACT.read_text(encoding="utf-8")))
  except (OSError,json.JSONDecodeError,ValidationError) as error:
    print(f"remote MAC provider validation failed: {error}",file=sys.stderr); return 1
  print("remote MAC provider boundary: OK"); return 0
if __name__=="__main__": raise SystemExit(main())
