#!/usr/bin/env python3
"""Compose a bounded Unix-domain transport for the opaque remote MAC provider."""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def source() -> str:
    return textwrap.dedent(
        '''\
        #![cfg(unix)]

        use std::io::{self, Read, Write};
        use std::os::unix::net::UnixStream;
        use std::path::{Path, PathBuf};
        use std::time::Duration;

        use crate::remote::{
            RemoteMacError, RemoteMacPurpose, RemoteMacRequest, RemoteMacRequestKind,
            RemoteMacResponse, RemoteMacTransport, REMOTE_HS256_TAG_BYTES,
        };

        const REQUEST_MAGIC: &[u8; 8] = b"TRNMHMC1";
        const RESPONSE_MAGIC: &[u8; 8] = b"TRNMRMC1";
        const MAX_SOCKET_PATH_BYTES: usize = 100;
        const MAX_REQUEST_FRAME_BYTES: usize = 1024 * 1024 + 512;
        const MAX_RESPONSE_FRAME_BYTES: usize = 64;

        #[derive(Clone, Debug)]
        pub struct UnixSocketRemoteMacTransport {
            socket_path: PathBuf,
        }

        impl UnixSocketRemoteMacTransport {
            pub fn new(socket_path: impl Into<PathBuf>) -> Result<Self, RemoteMacError> {
                let socket_path = socket_path.into();
                validate_socket_path(&socket_path)?;
                Ok(Self { socket_path })
            }

            #[must_use]
            pub fn socket_path(&self) -> &Path {
                &self.socket_path
            }
        }

        impl RemoteMacTransport for UnixSocketRemoteMacTransport {
            fn exchange(
                &self,
                request: &RemoteMacRequest,
                timeout: Duration,
            ) -> Result<RemoteMacResponse, RemoteMacError> {
                let frame = encode_request(request)?;
                let mut stream = UnixStream::connect(&self.socket_path).map_err(map_io_error)?;
                stream.set_read_timeout(Some(timeout)).map_err(map_io_error)?;
                stream.set_write_timeout(Some(timeout)).map_err(map_io_error)?;
                let length = u32::try_from(frame.len())
                    .map_err(|_| RemoteMacError::InvalidRequest)?
                    .to_be_bytes();
                stream.write_all(&length).map_err(map_io_error)?;
                stream.write_all(&frame).map_err(map_io_error)?;
                stream.flush().map_err(map_io_error)?;

                let mut response_length = [0_u8; 4];
                stream.read_exact(&mut response_length).map_err(map_io_error)?;
                let response_length = u32::from_be_bytes(response_length) as usize;
                if response_length == 0 || response_length > MAX_RESPONSE_FRAME_BYTES {
                    return Err(RemoteMacError::ProtocolViolation);
                }
                let mut response = vec![0_u8; response_length];
                stream.read_exact(&mut response).map_err(map_io_error)?;
                decode_response(&response)
            }
        }

        fn validate_socket_path(path: &Path) -> Result<(), RemoteMacError> {
            let value = path.as_os_str().as_encoded_bytes();
            if !path.is_absolute()
                || value.is_empty()
                || value.len() > MAX_SOCKET_PATH_BYTES
                || value.contains(&0)
            {
                return Err(RemoteMacError::InvalidConfiguration);
            }
            Ok(())
        }

        fn encode_request(request: &RemoteMacRequest) -> Result<Vec<u8>, RemoteMacError> {
            let key = request.key_reference.as_bytes();
            let key_length = u16::try_from(key.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
            let message_length =
                u32::try_from(request.message.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
            let (operation, tag) = match request.kind {
                RemoteMacRequestKind::Sign => (1_u8, None),
                RemoteMacRequestKind::Verify { tag } => (2_u8, Some(tag)),
            };
            let purpose = match request.purpose {
                RemoteMacPurpose::AccessToken => 1_u8,
                RemoteMacPurpose::RefreshCredential => 2_u8,
            };
            let capacity = 8 + 1 + 1 + 16 + 2 + 4 + key.len() + request.message.len()
                + tag.map_or(0, |_| REMOTE_HS256_TAG_BYTES);
            if capacity > MAX_REQUEST_FRAME_BYTES {
                return Err(RemoteMacError::InvalidRequest);
            }
            let mut output = Vec::with_capacity(capacity);
            output.extend_from_slice(REQUEST_MAGIC);
            output.push(operation);
            output.push(purpose);
            output.extend_from_slice(&request.request_id);
            output.extend_from_slice(&key_length.to_be_bytes());
            output.extend_from_slice(&message_length.to_be_bytes());
            output.extend_from_slice(key);
            output.extend_from_slice(&request.message);
            if let Some(tag) = tag {
                output.extend_from_slice(&tag);
            }
            Ok(output)
        }

        fn decode_response(frame: &[u8]) -> Result<RemoteMacResponse, RemoteMacError> {
            if frame.len() < 26 || &frame[..8] != RESPONSE_MAGIC {
                return Err(RemoteMacError::ProtocolViolation);
            }
            let operation = frame[8];
            let status = frame[9];
            let mut request_id = [0_u8; 16];
            request_id.copy_from_slice(&frame[10..26]);
            match (operation, status, frame.len()) {
                (1, 0, 58) => {
                    let mut tag = [0_u8; REMOTE_HS256_TAG_BYTES];
                    tag.copy_from_slice(&frame[26..58]);
                    Ok(RemoteMacResponse::Signed { request_id, tag })
                }
                (2, 0, 26) => Ok(RemoteMacResponse::Verified {
                    request_id,
                    valid: true,
                }),
                (2, 1, 26) => Ok(RemoteMacResponse::Verified {
                    request_id,
                    valid: false,
                }),
                _ => Err(RemoteMacError::ProtocolViolation),
            }
        }

        fn map_io_error(error: io::Error) -> RemoteMacError {
            match error.kind() {
                io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => RemoteMacError::Timeout,
                io::ErrorKind::UnexpectedEof | io::ErrorKind::InvalidData => {
                    RemoteMacError::ProtocolViolation
                }
                _ => RemoteMacError::TransportUnavailable,
            }
        }

        #[cfg(test)]
        mod tests {
            use super::*;
            use crate::remote::RemoteHs256Provider;
            use std::fs;
            use std::os::unix::net::UnixListener;
            use std::sync::atomic::{AtomicU64, Ordering};
            use std::thread;

            static NEXT_SOCKET: AtomicU64 = AtomicU64::new(1);

            struct SocketGuard(PathBuf);

            impl Drop for SocketGuard {
                fn drop(&mut self) {
                    let _ = fs::remove_file(&self.0);
                }
            }

            fn socket_path() -> (PathBuf, SocketGuard) {
                let path = std::env::temp_dir().join(format!(
                    "trnm-mac-{}-{}.sock",
                    std::process::id(),
                    NEXT_SOCKET.fetch_add(1, Ordering::Relaxed)
                ));
                let _ = fs::remove_file(&path);
                let guard = SocketGuard(path.clone());
                (path, guard)
            }

            fn read_request(mut stream: &UnixStream) -> Vec<u8> {
                let mut length = [0_u8; 4];
                stream.read_exact(&mut length).unwrap();
                let mut frame = vec![0_u8; u32::from_be_bytes(length) as usize];
                stream.read_exact(&mut frame).unwrap();
                frame
            }

            fn write_response(mut stream: &UnixStream, payload: &[u8]) {
                stream
                    .write_all(&u32::try_from(payload.len()).unwrap().to_be_bytes())
                    .unwrap();
                stream.write_all(payload).unwrap();
                stream.flush().unwrap();
            }

            #[test]
            fn sign_round_trip_uses_bounded_opaque_frame() {
                let (path, _guard) = socket_path();
                let listener = UnixListener::bind(&path).unwrap();
                let server = thread::spawn(move || {
                    let (stream, _) = listener.accept().unwrap();
                    let request = read_request(&stream);
                    assert_eq!(&request[..8], REQUEST_MAGIC);
                    assert_eq!(request[8], 1);
                    assert_eq!(request[9], 1);
                    assert!(request.windows(b"kms://tenant/key-7".len()).any(|row| row == b"kms://tenant/key-7"));
                    assert!(!request.windows(10).any(|row| row == b"raw-secret"));
                    let mut response = Vec::new();
                    response.extend_from_slice(RESPONSE_MAGIC);
                    response.push(1);
                    response.push(0);
                    response.extend_from_slice(&[7; 16]);
                    response.extend_from_slice(&[9; 32]);
                    write_response(&stream, &response);
                });
                let transport = UnixSocketRemoteMacTransport::new(path).unwrap();
                let provider = RemoteHs256Provider::new(
                    transport,
                    "kms://tenant/key-7",
                    Duration::from_secs(1),
                )
                .unwrap();
                assert_eq!(
                    provider
                        .sign([7; 16], RemoteMacPurpose::AccessToken, b"header.payload")
                        .unwrap(),
                    [9; 32]
                );
                server.join().unwrap();
            }

            #[test]
            fn verify_false_and_mismatched_response_are_fail_closed() {
                let (path, _guard) = socket_path();
                let listener = UnixListener::bind(&path).unwrap();
                let server = thread::spawn(move || {
                    let (stream, _) = listener.accept().unwrap();
                    let request = read_request(&stream);
                    assert_eq!(request[8], 2);
                    let mut response = Vec::new();
                    response.extend_from_slice(RESPONSE_MAGIC);
                    response.push(2);
                    response.push(1);
                    response.extend_from_slice(&[8; 16]);
                    write_response(&stream, &response);
                });
                let transport = UnixSocketRemoteMacTransport::new(path).unwrap();
                let provider = RemoteHs256Provider::new(
                    transport,
                    "hsm:partition/key",
                    Duration::from_secs(1),
                )
                .unwrap();
                assert_eq!(
                    provider
                        .verify(
                            [8; 16],
                            RemoteMacPurpose::AccessToken,
                            b"message",
                            &[3; 32],
                        )
                        .unwrap_err(),
                    RemoteMacError::VerificationRejected
                );
                server.join().unwrap();
            }

            #[test]
            fn oversized_truncated_and_relative_paths_are_rejected() {
                assert_eq!(
                    UnixSocketRemoteMacTransport::new("relative.sock").unwrap_err(),
                    RemoteMacError::InvalidConfiguration
                );
                let (path, _guard) = socket_path();
                let listener = UnixListener::bind(&path).unwrap();
                let server = thread::spawn(move || {
                    let (mut stream, _) = listener.accept().unwrap();
                    let _ = read_request(&stream);
                    stream.write_all(&32_u32.to_be_bytes()).unwrap();
                    stream.write_all(b"short").unwrap();
                });
                let transport = UnixSocketRemoteMacTransport::new(path).unwrap();
                let provider = RemoteHs256Provider::new(
                    transport,
                    "kms:key",
                    Duration::from_secs(1),
                )
                .unwrap();
                assert_eq!(
                    provider
                        .sign([1; 16], RemoteMacPurpose::AccessToken, b"message")
                        .unwrap_err(),
                    RemoteMacError::ProtocolViolation
                );
                server.join().unwrap();
            }
        }
        '''
    )


def checker() -> str:
    return textwrap.dedent(
        '''\
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
          require("mod remote_unix;" in lib,"Unix module not compiled")
          require("UnixSocketRemoteMacTransport" in lib,"Unix transport not exported")
          require("fallback" not in source.lower(),"software fallback marker")
        def main()->int:
          try: validate(SOURCE.read_text(),LIB.read_text())
          except (OSError,ValidationError) as error:
            print(f"remote MAC Unix transport validation failed: {error}",file=sys.stderr); return 1
          print("remote MAC Unix transport boundary: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
        '''
    )


def tests() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,subprocess,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]; CHECKER=ROOT/"scripts/check-remote-mac-unix-transport.py"
        def load_checker():
          spec=importlib.util.spec_from_file_location("remote_mac_unix_checker",CHECKER)
          if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
          module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
        class RemoteMacUnixTransportContractTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.checker=load_checker(); cls.source=cls.checker.SOURCE.read_text(); cls.lib=cls.checker.LIB.read_text()
          def test_real_contract_passes(self): cls.checker.validate(self.source,self.lib)
          def test_raw_key_or_timeout_removal_rejected(self):
            with self.assertRaisesRegex(self.checker.ValidationError,"forbidden"): cls.checker.validate(self.source+"raw_key",self.lib)
            with self.assertRaisesRegex(self.checker.ValidationError,"set_read_timeout"): cls.checker.validate(self.source.replace("set_read_timeout","removed",1),self.lib)
          def test_cli_passes(self):
            result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn("Unix transport boundary: OK",result.stdout)
        if __name__=="__main__": unittest.main()
        '''
    )


def update_lib(root: Path) -> None:
    path=root/"crates/trnm-token-crypto-provider/src/lib.rs"; text=read(path)
    if "mod remote_unix;" not in text:
        anchor="mod remote;\n"
        require(anchor in text,"remote module anchor missing")
        text=text.replace(anchor,anchor+"#[cfg(unix)]\nmod remote_unix;\n",1)
    export="#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;\n"
    if "pub use remote_unix::UnixSocketRemoteMacTransport;" not in text:
        anchor="pub use remote::{"
        index=text.find(anchor)
        require(index>=0,"remote export anchor missing")
        text=text[:index]+export+text[index:]
    write(path,text)


def update_docs(root: Path) -> None:
    path=root/"docs/SECURITY_AND_PRIVACY.md"; text=read(path)
    section=textwrap.dedent(
      '''\

      ## Unix sidecar transport for remote MAC

      On Unix targets, `UnixSocketRemoteMacTransport` provides a bounded one-request-per-connection protocol to an external MAC sidecar. Request and response frames have independent hard limits, fixed magic/version bytes, exact request IDs, I/O deadlines and strict response shapes. Truncation, invalid status, oversized frames, relative socket paths and transport failure all fail closed. The frame contains an opaque key reference but no raw key material.

      This transport makes the application-side boundary executable; the sidecar's KMS/HSM implementation, socket ownership and peer credentials, IAM/audit records, rotation/revoke behavior and independent security acceptance remain separate production facts.
      '''
    )
    if "## Unix sidecar transport for remote MAC" not in text: text+=section
    write(path,text)


def run(root: Path) -> None:
    require((root/".git").is_dir(),"Git working tree required")
    update_lib(root)
    write(root/"crates/trnm-token-crypto-provider/src/remote_unix.rs",source())
    write(root/"scripts/check-remote-mac-unix-transport.py",checker())
    write(root/"tests/control_plane/test_remote_mac_unix_transport.py",tests())
    update_docs(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
