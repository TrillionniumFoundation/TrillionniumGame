#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Canonical TrillionniumGame server composition root.
//!
//! The crate owns the process boundary and composes the already reviewed
//! persistence, session, protocol and realtime components. Ingress reads use
//! absolute request/frame deadlines, and drain actively closes registered
//! sockets before joining workers; loopback regressions exercise both paths.
//! Source availability is not production acceptance; exact-head execution and
//! independent review remain mandatory.

mod runtime;

pub use runtime::ServerError;

pub fn run(arguments: &[String]) -> Result<(), ServerError> {
    runtime::run(arguments)
}

pub fn run_from_environment() -> Result<(), ServerError> {
    runtime::run_from_environment()
}
