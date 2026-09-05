//! Credential-free, bounded TLS witness for the isolated PostgreSQL fault lab.
//! This does not replace the native-tls pool: each accepted negative also needs
//! pool refusal and fresh healthy pool controls on the same numeric endpoint.
use std::io::{self, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::str::FromStr;
use std::time::{Duration, Instant};

use openssl::ssl::{HandshakeError, SslConnector, SslMethod, SslVerifyMode, SslVersion};
use openssl::x509::X509;
use postgres::config::Host;
use postgres::Config;

const SSL_REQUEST: [u8; 8] = [0, 0, 0, 8, 4, 210, 22, 47];
pub const WITNESS_BUDGET: Duration = Duration::from_secs(2);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Observation {
    Verified,
    TrustChainRejected,
    OtherCertificateRejected,
    InvalidRoot,
    TransportFailure,
    NegotiationFailure,
    SetupFailure,
    DeadlineExceeded,
}

// OpenSSL include/openssl/x509_vfy.h stable X509_V_ERR_* values:
// 2 issuer missing; 18 self-signed leaf; 19 self-signed chain;
// 20 local issuer missing; 21 unverifiable leaf. Expiry/hostname/purpose and
// generic TLS errors are deliberately NOT accepted as cross-root rejection.
fn certificate_failure(code: i32) -> Observation {
    match code {
        2 | 18 | 19 | 20 | 21 => Observation::TrustChainRejected,
        0 => Observation::TransportFailure,
        _ => Observation::OtherCertificateRejected,
    }
}

pub fn endpoint(url: &str) -> Result<SocketAddr, &'static str> {
    let config = Config::from_str(url).map_err(|_| "tls_witness_url_invalid")?;
    let [Host::Tcp(host)] = config.get_hosts() else {
        return Err("tls_witness_requires_one_numeric_loopback_host");
    };
    if !config.get_hostaddrs().is_empty() {
        return Err("tls_witness_hostaddr_override_forbidden");
    }
    let ip =
        IpAddr::from_str(host).map_err(|_| "tls_witness_requires_one_numeric_loopback_host")?;
    if !ip.is_loopback() {
        return Err("tls_witness_requires_one_numeric_loopback_host");
    }
    let port = match config.get_ports() {
        [] => 5432,
        [port] if *port != 0 => *port,
        _ => return Err("tls_witness_requires_one_nonzero_port"),
    };
    Ok(SocketAddr::new(ip, port))
}

pub fn observe(address: SocketAddr, pem: &[u8], budget: Duration) -> Observation {
    if !address.ip().is_loopback() || budget.is_zero() || budget > WITNESS_BUDGET {
        return Observation::SetupFailure;
    }
    let started = Instant::now();
    let deadline = started + budget;
    let result = observe_until(address, pem, deadline);
    if Instant::now() >= deadline {
        Observation::DeadlineExceeded
    } else {
        result
    }
}

fn observe_until(address: SocketAddr, pem: &[u8], deadline: Instant) -> Observation {
    let certificate = match X509::from_pem(pem) {
        Ok(certificate) => certificate,
        Err(_) => return Observation::InvalidRoot,
    };
    let mut builder = match SslConnector::builder(SslMethod::tls()) {
        Ok(builder) => builder,
        Err(_) => return Observation::SetupFailure,
    };
    builder.set_verify(SslVerifyMode::PEER);
    if builder
        .set_min_proto_version(Some(SslVersion::TLS1_2))
        .is_err()
        || builder.cert_store_mut().add_cert(certificate).is_err()
    {
        return Observation::SetupFailure;
    }
    let connector = builder.build();
    let remaining = match remaining(deadline) {
        Ok(remaining) => remaining,
        Err(_) => return Observation::DeadlineExceeded,
    };
    let socket = match TcpStream::connect_timeout(&address, remaining) {
        Ok(socket) => socket,
        Err(_) => return Observation::TransportFailure,
    };
    let mut stream = DeadlineStream { socket, deadline };
    if stream.write_all(&SSL_REQUEST).is_err() {
        return Observation::TransportFailure;
    }
    let mut reply = [0u8; 1];
    if stream.read_exact(&mut reply).is_err() {
        return Observation::TransportFailure;
    }
    if reply != [b'S'] {
        return Observation::NegotiationFailure;
    }
    let configured = match connector.configure() {
        Ok(configured) => configured.verify_hostname(true),
        Err(_) => return Observation::SetupFailure,
    };
    match configured.connect(&address.ip().to_string(), stream) {
        Ok(_) => Observation::Verified,
        Err(HandshakeError::Failure(stream)) => {
            certificate_failure(stream.ssl().verify_result().as_raw())
        }
        Err(HandshakeError::WouldBlock(_)) => Observation::DeadlineExceeded,
        Err(HandshakeError::SetupFailure(_)) => Observation::SetupFailure,
    }
}

fn remaining(deadline: Instant) -> io::Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "tls_witness_deadline"))
}

struct DeadlineStream {
    socket: TcpStream,
    deadline: Instant,
}

impl Read for DeadlineStream {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        self.socket
            .set_read_timeout(Some(remaining(self.deadline)?))?;
        let count = self.socket.read(bytes)?;
        remaining(self.deadline)?;
        Ok(count)
    }
}

impl Write for DeadlineStream {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.socket
            .set_write_timeout(Some(remaining(self.deadline)?))?;
        let count = self.socket.write(bytes)?;
        remaining(self.deadline)?;
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.socket
            .set_write_timeout(Some(remaining(self.deadline)?))?;
        self.socket.flush()?;
        remaining(self.deadline)?;
        Ok(())
    }
}

/// Every negative is sandwiched between freshly executed healthy controls.
/// A failed control or an unrelated failure is a failing lab, not a passed test.
pub fn bracket(
    expected: Observation,
    mut control: impl FnMut() -> Result<(), String>,
    negative: impl FnOnce() -> Observation,
    pool_refusal: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    if !matches!(
        expected,
        Observation::TrustChainRejected | Observation::InvalidRoot
    ) {
        return Err("tls_witness_invalid_expectation".to_owned());
    }
    control()?;
    let observed = negative();
    let refusal = pool_refusal();
    control()?;
    if observed != expected {
        return Err(format!("tls_negative_wrong_class:{observed:?}"));
    }
    refusal
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn cross_root_classification_is_narrow() {
        for code in [2, 18, 19, 20, 21] {
            assert_eq!(certificate_failure(code), Observation::TrustChainRejected);
        }
        for code in [-1, 1, 3, 9, 10, 24, 26, 62, 64, i32::MAX] {
            assert_eq!(
                certificate_failure(code),
                Observation::OtherCertificateRejected
            );
        }
        assert_eq!(certificate_failure(0), Observation::TransportFailure);
    }

    #[test]
    fn generic_failures_never_satisfy_negative_assertion() {
        for observed in [
            Observation::Verified,
            Observation::TransportFailure,
            Observation::NegotiationFailure,
            Observation::SetupFailure,
            Observation::DeadlineExceeded,
            Observation::OtherCertificateRejected,
            Observation::InvalidRoot,
        ] {
            assert!(bracket(
                Observation::TrustChainRejected,
                || Ok(()),
                || observed,
                || Ok(())
            )
            .is_err());
        }
    }

    #[test]
    fn invalid_root_is_local_parse_rejection_not_remote_trust_rejection() {
        assert!(bracket(
            Observation::InvalidRoot,
            || Ok(()),
            || Observation::InvalidRoot,
            || Ok(())
        )
        .is_ok());
        assert!(bracket(
            Observation::InvalidRoot,
            || Ok(()),
            || Observation::TrustChainRejected,
            || Ok(())
        )
        .is_err());
        let address = SocketAddr::from(([127, 0, 0, 1], 1));
        assert_eq!(
            observe(address, b"invalid PEM", WITNESS_BUDGET),
            Observation::InvalidRoot
        );
    }

    #[test]
    fn controls_run_before_and_after_each_negative() {
        let sequence = RefCell::new(Vec::new());
        bracket(
            Observation::TrustChainRejected,
            || {
                sequence.borrow_mut().push("healthy");
                Ok(())
            },
            || {
                sequence.borrow_mut().push("witness");
                Observation::TrustChainRejected
            },
            || {
                sequence.borrow_mut().push("pool-refusal");
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(
            *sequence.borrow(),
            ["healthy", "witness", "pool-refusal", "healthy"]
        );
    }

    #[test]
    fn failed_pre_control_prevents_probe_execution() {
        let called = RefCell::new(false);
        assert!(bracket(
            Observation::TrustChainRejected,
            || Err("authentication_or_query_failure".to_owned()),
            || {
                *called.borrow_mut() = true;
                Observation::TrustChainRejected
            },
            || Ok(())
        )
        .is_err());
        assert!(!*called.borrow());
    }

    #[test]
    fn failed_post_control_rejects_apparent_certificate_failure() {
        let mut calls = 0;
        assert!(bracket(
            Observation::TrustChainRejected,
            || {
                calls += 1;
                if calls == 1 {
                    Ok(())
                } else {
                    Err("endpoint_down".to_owned())
                }
            },
            || Observation::TrustChainRejected,
            || Ok(())
        )
        .is_err());
        assert_eq!(calls, 2);
    }

    #[test]
    fn successful_pool_cannot_hide_behind_witness_rejection() {
        assert!(bracket(
            Observation::TrustChainRejected,
            || Ok(()),
            || Observation::TrustChainRejected,
            || Err("unexpected_pool_acceptance".to_owned())
        )
        .is_err());
    }

    #[test]
    fn endpoint_parser_rejects_dns_remote_hosts_and_multi_host_substitution() {
        assert_eq!(
            endpoint("postgresql://user:secret@127.0.0.1:55432/db").unwrap(),
            SocketAddr::from(([127, 0, 0, 1], 55432))
        );
        for url in [
            "postgresql://localhost/db",
            "postgresql://192.0.2.1/db",
            "host=/tmp user=postgres",
            "host=127.0.0.1,127.0.0.2",
            "host=127.0.0.1 hostaddr=127.0.0.2",
            "postgresql://127.0.0.1:0/db",
        ] {
            assert!(
                endpoint(url).is_err(),
                "endpoint must reject an ambiguous or remote target"
            );
        }
    }

    #[test]
    fn expired_deadline_never_performs_socket_io() {
        assert_eq!(
            remaining(Instant::now()).unwrap_err().kind(),
            io::ErrorKind::TimedOut
        );
        let address = SocketAddr::from(([127, 0, 0, 1], 1));
        assert_eq!(
            observe(address, b"unused", Duration::ZERO),
            Observation::SetupFailure
        );
        assert_eq!(
            observe(address, b"unused", WITNESS_BUDGET + Duration::from_secs(1)),
            Observation::SetupFailure
        );
    }
    #[test]
    fn real_non_tls_and_stalled_endpoints_never_earn_trust_rejection() {
        use openssl::asn1::Asn1Time;
        use openssl::hash::MessageDigest;
        use openssl::pkey::PKey;
        use openssl::rsa::Rsa;
        use openssl::x509::X509NameBuilder;
        use std::net::TcpListener;
        use std::sync::mpsc;
        use std::thread;

        let key = PKey::from_rsa(Rsa::generate(2048).unwrap()).unwrap();
        let mut name = X509NameBuilder::new().unwrap();
        name.append_entry_by_text("CN", "isolated-witness-test")
            .unwrap();
        let name = name.build();
        let mut cert = X509::builder().unwrap();
        cert.set_version(2).unwrap();
        cert.set_subject_name(&name).unwrap();
        cert.set_issuer_name(&name).unwrap();
        cert.set_pubkey(&key).unwrap();
        cert.set_not_before(&Asn1Time::days_from_now(0).unwrap())
            .unwrap();
        cert.set_not_after(&Asn1Time::days_from_now(1).unwrap())
            .unwrap();
        cert.sign(&key, MessageDigest::sha256()).unwrap();
        let pem = cert.build().to_pem().unwrap();
        for response in [Some(b'N'), Some(b'?'), None] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let (release, released) = mpsc::channel();
            let server = thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(3);
                while Instant::now() < deadline {
                    match listener.accept() {
                        Ok((mut socket, _)) => {
                            socket
                                .set_read_timeout(Some(Duration::from_secs(1)))
                                .unwrap();
                            socket
                                .set_write_timeout(Some(Duration::from_secs(1)))
                                .unwrap();
                            let mut request = [0u8; 8];
                            if socket.read_exact(&mut request).is_ok() {
                                assert_eq!(request, SSL_REQUEST);
                                if let Some(byte) = response {
                                    let _ = socket.write_all(&[byte]);
                                }
                            }
                            let _ = released.recv_timeout(Duration::from_secs(1));
                            return;
                        }
                        Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                            thread::park_timeout(Duration::from_millis(2));
                        }
                        Err(_) => return,
                    }
                }
            });
            let result = observe(address, &pem, Duration::from_millis(200));
            let _ = release.send(());
            server.join().unwrap();
            assert_ne!(result, Observation::TrustChainRejected);
            if response.is_some() {
                assert_eq!(result, Observation::NegotiationFailure);
            } else {
                assert!(matches!(
                    result,
                    Observation::DeadlineExceeded | Observation::TransportFailure
                ));
            }
        }
    }
}
