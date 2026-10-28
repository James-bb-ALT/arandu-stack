#![forbid(unsafe_code)]

mod binding;
mod error;
mod id;
mod store;

pub use binding::{
    CallerBinding, ContextBinding, PairingEpoch, SESSION_BINDING_BYTES, SessionBinding,
};
pub use error::{Result, SessionError, SessionErrorCode};
pub use id::{SESSION_ID_BYTES, SessionId};
pub use store::{
    CancellationOutcome, ContinuationEndpoint, ContinuationOutcome, ContinuationResult,
    MAX_ACTIVE_SESSIONS, MAX_PENDING_REFERENTS, MAX_TTL_TICKS, ReferentResolution, SessionConfig,
    SessionDiagnostics, SessionStore,
};
