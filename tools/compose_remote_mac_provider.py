#!/usr/bin/env python3
"""Compose an opaque-key remote MAC provider boundary and hostile controls."""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def remote_source() -> str:
    return textwrap.dedent(
        '''\
        use std::fmt;
        use std::time::Duration;

        pub const REMOTE_HS256_TAG_BYTES: usize = 32;
        pub const MAX_REMOTE_MAC_MESSAGE_BYTES: usize = 1024 * 1024;
        pub const MAX_REMOTE_MAC_TIMEOUT: Duration = Duration::from_secs(5);

        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub enum RemoteMacPurpose {
            AccessToken,
            RefreshCredential,
        }

        #[derive(Clone, Debug, Eq, PartialEq)]
        pub enum RemoteMacRequestKind {
            Sign,
            Verify { tag: [u8; REMOTE_HS256_TAG_BYTES] },
        }

        #[derive(Clone, Debug, Eq, PartialEq)]
        pub struct RemoteMacRequest {
            pub request_id: [u8; 16],
            pub key_reference: String,
            pub purpose: RemoteMacPurpose,
            pub message: Vec<u8>,
            pub kind: RemoteMacRequestKind,
        }

        #[derive(Clone, Debug, Eq, PartialEq)]
        pub enum RemoteMacResponse {
            Signed {
                request_id: [u8; 16],
                tag: [u8; REMOTE_HS256_TAG_BYTES],
            },
            Verified {
                request_id: [u8; 16],
                valid: bool,
            },
        }

        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub enum RemoteMacError {
            InvalidConfiguration,
            InvalidRequest,
            Timeout,
            TransportUnavailable,
            ProtocolViolation,
            VerificationRejected,
        }

        pub trait RemoteMacTransport: Send + Sync {
            fn exchange(
                &self,
                request: &RemoteMacRequest,
                timeout: Duration,
            ) -> Result<RemoteMacResponse, RemoteMacError>;
        }

        pub struct RemoteHs256Provider<T> {
            transport: T,
            key_reference: String,
            timeout: Duration,
        }

        impl<T> fmt::Debug for RemoteHs256Provider<T> {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct("RemoteHs256Provider")
                    .field("transport", &"<opaque-transport>")
                    .field("key_reference", &"<opaque-key-reference>")
                    .field("timeout", &self.timeout)
                    .finish()
            }
        }

        impl<T: RemoteMacTransport> RemoteHs256Provider<T> {
            pub fn new(
                transport: T,
                key_reference: impl Into<String>,
                timeout: Duration,
            ) -> Result<Self, RemoteMacError> {
                let key_reference = key_reference.into();
                if !valid_key_reference(&key_reference)
                    || timeout.is_zero()
                    || timeout > MAX_REMOTE_MAC_TIMEOUT
                {
                    return Err(RemoteMacError::InvalidConfiguration);
                }
                Ok(Self {
                    transport,
                    key_reference,
                    timeout,
                })
            }

            pub fn sign(
                &self,
                request_id: [u8; 16],
                purpose: RemoteMacPurpose,
                message: &[u8],
            ) -> Result<[u8; REMOTE_HS256_TAG_BYTES], RemoteMacError> {
                validate_request(request_id, message)?;
                let request = RemoteMacRequest {
                    request_id,
                    key_reference: self.key_reference.clone(),
                    purpose,
                    message: message.to_vec(),
                    kind: RemoteMacRequestKind::Sign,
                };
                match self.transport.exchange(&request, self.timeout)? {
                    RemoteMacResponse::Signed {
                        request_id: response_id,
                        tag,
                    } if response_id == request_id => Ok(tag),
                    _ => Err(RemoteMacError::ProtocolViolation),
                }
            }

            pub fn verify(
                &self,
                request_id: [u8; 16],
                purpose: RemoteMacPurpose,
                message: &[u8],
                tag: &[u8],
            ) -> Result<(), RemoteMacError> {
                validate_request(request_id, message)?;
                let tag: [u8; REMOTE_HS256_TAG_BYTES] = tag
                    .try_into()
                    .map_err(|_| RemoteMacError::InvalidRequest)?;
                let request = RemoteMacRequest {
                    request_id,
                    key_reference: self.key_reference.clone(),
                    purpose,
                    message: message.to_vec(),
                    kind: RemoteMacRequestKind::Verify { tag },
                };
                match self.transport.exchange(&request, self.timeout)? {
                    RemoteMacResponse::Verified {
                        request_id: response_id,
                        valid: true,
                    } if response_id == request_id => Ok(()),
                    RemoteMacResponse::Verified {
                        request_id: response_id,
                        valid: false,
                    } if response_id == request_id => {
                        Err(RemoteMacError::VerificationRejected)
                    }
                    _ => Err(RemoteMacError::ProtocolViolation),
                }
            }

            #[must_use]
            pub const fn timeout(&self) -> Duration {
                self.timeout
            }
        }

        fn valid_key_reference(value: &str) -> bool {
            !value.is_empty()
                && value.len() <= 256
                && value.bytes().all(|byte| {
                    matches!(
                        byte,
                        b'a'..=b'z'
                            | b'A'..=b'Z'
                            | b'0'..=b'9'
                            | b'/'
                            | b'_'
                            | b'-'
                            | b'.'
                            | b':'
                            | b'@'
                    )
                })
        }

        fn validate_request(request_id: [u8; 16], message: &[u8]) -> Result<(), RemoteMacError> {
            if request_id.iter().all(|byte| *byte == 0)
                || message.is_empty()
                || message.len() > MAX_REMOTE_MAC_MESSAGE_BYTES
            {
                return Err(RemoteMacError::InvalidRequest);
            }
            Ok(())
        }

        #[cfg(test)]
        mod tests {
            use super::*;
            use std::sync::Mutex;

            #[derive(Debug)]
            struct MockTransport {
                response: Result<RemoteMacResponse, RemoteMacError>,
                requests: Mutex<Vec<RemoteMacRequest>>,
                timeouts: Mutex<Vec<Duration>>,
            }

            impl MockTransport {
                fn new(response: Result<RemoteMacResponse, RemoteMacError>) -> Self {
                    Self {
                        response,
                        requests: Mutex::new(Vec::new()),
                        timeouts: Mutex::new(Vec::new()),
                    }
                }
            }

            impl RemoteMacTransport for MockTransport {
                fn exchange(
                    &self,
                    request: &RemoteMacRequest,
                    timeout: Duration,
                ) -> Result<RemoteMacResponse, RemoteMacError> {
                    self.requests.lock().unwrap().push(request.clone());
                    self.timeouts.lock().unwrap().push(timeout);
                    self.response.clone()
                }
            }

            fn request_id(value: u8) -> [u8; 16] {
                [value; 16]
            }

            #[test]
            fn opaque_reference_signing_never_accepts_key_bytes() {
                let id = request_id(1);
                let transport = MockTransport::new(Ok(RemoteMacResponse::Signed {
                    request_id: id,
                    tag: [7; REMOTE_HS256_TAG_BYTES],
                }));
                let provider = RemoteHs256Provider::new(
                    transport,
                    "kms://tenant/access/epoch-7",
                    Duration::from_millis(250),
                )
                .unwrap();
                assert_eq!(
                    provider
                        .sign(id, RemoteMacPurpose::AccessToken, b"header.payload")
                        .unwrap(),
                    [7; REMOTE_HS256_TAG_BYTES]
                );
                let requests = provider.transport.requests.lock().unwrap();
                assert_eq!(requests.len(), 1);
                assert_eq!(requests[0].key_reference, "kms://tenant/access/epoch-7");
                assert_eq!(requests[0].message, b"header.payload");
                assert_eq!(
                    provider.transport.timeouts.lock().unwrap().as_slice(),
                    &[Duration::from_millis(250)]
                );
            }

            #[test]
            fn verification_is_exact_width_correlated_and_fail_closed() {
                let id = request_id(2);
                let provider = RemoteHs256Provider::new(
                    MockTransport::new(Ok(RemoteMacResponse::Verified {
                        request_id: id,
                        valid: false,
                    })),
                    "hsm:partition/token-key",
                    Duration::from_millis(100),
                )
                .unwrap();
                assert_eq!(
                    provider
                        .verify(id, RemoteMacPurpose::AccessToken, b"message", &[0; 31])
                        .unwrap_err(),
                    RemoteMacError::InvalidRequest
                );
                assert_eq!(
                    provider
                        .verify(id, RemoteMacPurpose::AccessToken, b"message", &[0; 32])
                        .unwrap_err(),
                    RemoteMacError::VerificationRejected
                );
            }

            #[test]
            fn mismatched_response_and_transport_failure_never_fall_back() {
                let id = request_id(3);
                let mismatch = RemoteHs256Provider::new(
                    MockTransport::new(Ok(RemoteMacResponse::Signed {
                        request_id: request_id(4),
                        tag: [1; 32],
                    })),
                    "kms:key",
                    Duration::from_millis(100),
                )
                .unwrap();
                assert_eq!(
                    mismatch
                        .sign(id, RemoteMacPurpose::RefreshCredential, b"refresh")
                        .unwrap_err(),
                    RemoteMacError::ProtocolViolation
                );
                let unavailable = RemoteHs256Provider::new(
                    MockTransport::new(Err(RemoteMacError::TransportUnavailable)),
                    "kms:key",
                    Duration::from_millis(100),
                )
                .unwrap();
                assert_eq!(
                    unavailable
                        .sign(id, RemoteMacPurpose::AccessToken, b"message")
                        .unwrap_err(),
                    RemoteMacError::TransportUnavailable
                );
            }

            #[test]
            fn configuration_request_and_debug_boundaries_are_redacted() {
                assert_eq!(
                    RemoteHs256Provider::new(
                        MockTransport::new(Err(RemoteMacError::Timeout)),
                        "bad key",
                        Duration::from_millis(100),
                    )
                    .unwrap_err(),
                    RemoteMacError::InvalidConfiguration
                );
                assert_eq!(
                    RemoteHs256Provider::new(
                        MockTransport::new(Err(RemoteMacError::Timeout)),
                        "kms:key",
                        Duration::ZERO,
                    )
                    .unwrap_err(),
                    RemoteMacError::InvalidConfiguration
                );
                let provider = RemoteHs256Provider::new(
                    MockTransport::new(Err(RemoteMacError::Timeout)),
                    "kms:secret-reference",
                    Duration::from_millis(100),
                )
                .unwrap();
                let debug = format!("{provider:?}");
                assert!(!debug.contains("secret-reference"));
                assert!(debug.contains("<opaque-key-reference>"));
                assert_eq!(
                    provider
                        .sign([0; 16], RemoteMacPurpose::AccessToken, b"message")
                        .unwrap_err(),
                    RemoteMacError::InvalidRequest
                );
                assert_eq!(
                    provider
                        .sign(request_id(1), RemoteMacPurpose::AccessToken, &[])
                        .unwrap_err(),
                    RemoteMacError::InvalidRequest
                );
            }
        }
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
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
          "<opaque-key-reference>",
          "mismatched_response_and_transport_failure_never_fall_back",
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
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,json,subprocess,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]; CHECKER=ROOT/"scripts/check-remote-mac-provider.py"
        def load_checker():
          spec=importlib.util.spec_from_file_location("remote_mac_provider_contract",CHECKER)
          if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
          module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
        class RemoteMacProviderContractTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls):
            cls.checker=load_checker(); cls.source=cls.checker.SOURCE.read_text(); cls.contract=json.loads(cls.checker.CONTRACT.read_text())
          def test_real_contract_passes(self): cls.checker.validate(self.source,self.contract)
          def test_raw_key_and_fallback_rejected(self):
            for marker in ("raw_key","SoftwareHs256Provider"):
              with self.subTest(marker=marker):
                with self.assertRaisesRegex(self.checker.ValidationError,"forbidden"): cls.checker.validate(self.source+marker,self.contract)
          def test_positive_claim_rejected(self):
            value=json.loads(json.dumps(self.contract)); value["claim_boundary"]["production_ready"]=True
            with self.assertRaisesRegex(self.checker.ValidationError,"positive"): cls.checker.validate(self.source,value)
          def test_command_line_checker_passes(self):
            result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn("remote MAC provider boundary: OK",result.stdout)
        if __name__=="__main__": unittest.main()
        '''
    )


def update_library(root: Path) -> None:
    path=root/"crates/trnm-token-crypto-provider/src/lib.rs"; text=read(path)
    if "mod remote;" not in text:
        anchor="mod software;\n"
        require(anchor in text,"software module anchor missing")
        text=text.replace(anchor,"mod remote;\n"+anchor,1)
    export=textwrap.dedent(
      '''\
      pub use remote::{
          RemoteHs256Provider, RemoteMacError, RemoteMacPurpose, RemoteMacRequest,
          RemoteMacRequestKind, RemoteMacResponse, RemoteMacTransport,
          MAX_REMOTE_MAC_MESSAGE_BYTES, MAX_REMOTE_MAC_TIMEOUT, REMOTE_HS256_TAG_BYTES,
      };
      '''
    )
    if "RemoteHs256Provider" not in text:
        anchor="pub use software::"
        index=text.find(anchor)
        require(index>=0,"software export anchor missing")
        text=text[:index]+export+text[index:]
    write(path,text)


def update_contract_and_docs(root:Path)->None:
    contract={
      "schema":"trillionnium.remote-mac-provider.v1",
      "operation_family":"HS256 sign and verify",
      "raw_key_bytes_enter_application_process":False,
      "local_software_fallback_allowed":False,
      "request_correlation_required":True,
      "key_reference_maximum_bytes":256,
      "message_maximum_bytes":1048576,
      "tag_bytes":32,
      "maximum_timeout_ms":5000,
      "required_production_adapters":[
        "approved cloud KMS HMAC service",
        "approved HSM or secret-manager MAC service",
      ],
      "required_live_evidence":[
        "credential and IAM denial",
        "timeout and transport loss",
        "wrong key version and disabled key",
        "rotation and revoke",
        "audit log correlation",
        "throughput and tail latency",
        "independent cryptography and security acceptance",
      ],
      "claim_boundary":{
        "production_adapter_implemented":False,
        "live_kms_hsm_verified":False,
        "independently_accepted":False,
        "production_ready":False,
      },
    }
    write(root/"contracts/security/remote-mac-provider.v1.json",json.dumps(contract,indent=2,ensure_ascii=False))
    security=root/"docs/SECURITY_AND_PRIVACY.md"; text=read(security)
    section=textwrap.dedent(
      '''\

      ## Opaque remote MAC provider boundary

      `RemoteHs256Provider` sends only a bounded request identifier, opaque key reference, purpose, message bytes and—when verifying—an exact 32-byte tag through a caller-supplied transport. The application process has no constructor or field for raw key bytes. Responses must match the request identifier, transport failures are returned directly, and no local software fallback exists. Debug output redacts both transport and key reference.

      This establishes a production-provider interface, not a production KMS/HSM implementation. Promotion still requires an approved vendor adapter, IAM and audit evidence, timeout/rotation/revocation fault packets, performance evidence and independent cryptographic review.
      '''
    )
    if "## Opaque remote MAC provider boundary" not in text: text+=section
    write(security,text)


def run(root:Path)->None:
    require((root/".git").is_dir(),"Git working tree required")
    update_library(root)
    write(root/"crates/trnm-token-crypto-provider/src/remote.rs",remote_source())
    write(root/"scripts/check-remote-mac-provider.py",checker_source())
    write(root/"tests/control_plane/test_remote_mac_provider_contract.py",test_source())
    update_contract_and_docs(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
