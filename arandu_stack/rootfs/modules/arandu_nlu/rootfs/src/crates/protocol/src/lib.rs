#![forbid(unsafe_code)]

mod error;
mod preflight;
pub mod v1;
pub mod v2;

pub use error::{ProtocolError, ProtocolErrorCode};
pub use v1::Outcome;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RequestVersion {
    V1,
    V2,
}

pub fn detect_request_version(bytes: &[u8]) -> Result<RequestVersion, ProtocolError> {
    v2::detect_request_version(bytes)
}

pub const MAX_WIRE_BYTES: usize = 65_536;
pub const MAX_DECODED_STRING_BYTES: usize = 16_384;
pub const MAX_NESTING_DEPTH: usize = 32;
pub const MAX_NUMERIC_TOKEN_BYTES: usize = 20;
pub const MAX_STRUCTURAL_ITEMS: usize = 4_096;
pub const MAX_PROTOCOL_ERROR_BYTES: usize = 256;
