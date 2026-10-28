#![forbid(unsafe_code)]

mod adapter;
mod catalog;
mod channel;
mod error;
mod helper;
mod ipc;
mod pairing;
mod pairing_wire;
mod process;
mod relay;
mod supervisor;
mod wire;
mod wyoming_tcp;

pub use adapter::{CatalogBindings, interpret_submission, recognize_wyoming};
pub use catalog::{
    CatalogEntity, CatalogSeed, MAX_CATALOG_FRAME_BYTES, read_catalog_seed, write_catalog_seed,
};
pub use channel::{NoiseStream, accept_noise_stream, initiate_noise_stream};
pub use error::{Result, RuntimeError};
pub use helper::relay_companion_submission;
pub use ipc::{
    CompanionSubmission, CompanionSubmissionKind, HelperReply, MAX_SUBMISSION_TEXT_BYTES,
    read_helper_reply, read_submission, write_helper_reply, write_submission,
};
pub use pairing::PairingBindings;
pub use pairing_wire::{
    DEFAULT_ADAPTER_RELAY_PORT, DEFAULT_HELPER_PAIRING_PORT, MAX_PAIRING_WIRE_BYTES, PairingOffer,
    accept_pairing, initiate_pairing,
};
pub use process::{
    DEFAULT_ADAPTER_GID, DEFAULT_ADAPTER_UID, DEFAULT_GENERATION_ROOT, DEFAULT_IPC_GID,
    DEFAULT_SERVER_GID, DEFAULT_SERVER_UID, GenerationDirectory, RuntimeIdentities,
    configure_server_command, prepare_product_generation_root,
};
pub use relay::serve_companion_once;
pub use supervisor::{
    MAX_HANDSHAKE_BYTES, MAX_SERVER_FRAME_BYTES, MAX_SYNCHRONIZATION_BYTES,
    READ_ONLY_COMMAND_ALLOWLIST, SupervisorClient, SupervisorCommand, SupervisorEntry,
    SupervisorError, SupervisorResult, SupervisorSnapshot,
};
pub use wire::{
    CompanionExecutionClass, CompanionGraph, CompanionNode, MAX_COMPANION_NODES,
    MAX_COMPANION_TIMEOUT_MILLIS, MAX_COMPANION_WIRE_BYTES, projected_plan_digest,
};
pub use wyoming_tcp::{
    DEFAULT_TCP_READ_TIMEOUT, DEFAULT_TCP_WRITE_TIMEOUT, MAX_TCP_IO_TIMEOUT, MonotonicClock,
    RecognitionProvider, TCP_READ_BUFFER_BYTES, WyomingTcpConfig, WyomingTcpError,
    WyomingTcpReport, WyomingTcpResult, drive_wyoming_tcp,
};
