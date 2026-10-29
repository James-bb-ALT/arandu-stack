#![deny(unsafe_code)]

mod config;
mod error;
mod framing;
mod health;
#[allow(unsafe_code)]
mod peer;
mod runtime;
mod server;
mod snapshot;

pub use config::{
    MAX_CONNECTION_QUEUE, MAX_FRAME_TIMEOUT_MILLIS, MAX_FRAMES_PER_CONNECTION,
    MAX_SOCKET_PATH_BYTES, MAX_WORKERS, MIN_FRAME_TIMEOUT_MILLIS, PeerIdentity, ServerConfig,
};
pub use error::{Result, ServerError, ServerErrorCode};
pub use framing::{MAX_FRAME_BYTES, read_frame, write_frame};
pub use health::{HealthSnapshot, Readiness, SUPPORTED_PROTOCOL_VERSIONS};
pub use runtime::{NluRequestHandler, NluRuntime};
pub use server::{RequestHandler, UnixServer};
pub use snapshot::{RuntimeSnapshot, SnapshotMetadata, SnapshotState, SnapshotStore};
