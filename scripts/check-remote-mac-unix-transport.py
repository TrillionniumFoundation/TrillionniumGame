#!/usr/bin/env python3
"""Validate the Unix remote MAC transport boundary."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"crates/trnm-token-crypto-provider/src/remote_unix.rs"
LIB=ROOT/"crates/trnm-token-crypto-provider/src/lib.rs"
REQUIRED=("UnixSocketRemoteMacTransport","UnixStream::connect","set_read_timeout","set_write_timeout","MAX_REQUEST_FRAME_BYTES","MAX_RESPONSE_FRAME_BYTES","REQUEST_MAGIC","RESPONSE_MAGIC","ProtocolViolation","TransportUnavailable","sign_round_trip_uses_bounded_opaque_frame","oversized_truncated_and_relative_paths_are_rejected")
FORBIDDEN=("raw_key","key_material","private_key","SoftwareHs256Provider","new_from_slice","Hmac<","sha256_digest")
class ValidationError(RuntimeError): pass
def require(value:bool,message:str)->None:
  if not value: raise ValidationError(message)
def validate(source:str,lib:str)->None:
  for marker in REQUIRED: require(marker in source,f"Unix MAC transport missing {marker}")
  for marker in FORBIDDEN: require(marker not in source,f"Unix MAC transport contains forbidden marker {marker}")
  require("#[cfg(unix)]\nmod remote_unix;" in lib,"Unix module not gated")
  require("#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;" in lib,"Unix export not gated")
  require("fallback" not in source.lower(),"software fallback marker")
def main()->int:
  try: validate(SOURCE.read_text(),LIB.read_text())
  except (OSError,ValidationError) as error:
    print(f"remote MAC Unix transport validation failed: {error}",file=sys.stderr); return 1
  print("remote MAC Unix transport boundary: OK"); return 0
if __name__=="__main__": raise SystemExit(main())
