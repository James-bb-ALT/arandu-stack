#![forbid(unsafe_code)]

mod engine;
mod error;
mod table;

pub use engine::{
    CancellationOutcome, ConfirmationConfig, ConfirmationId, ConfirmationRejection,
    ConfirmationRejectionReason, ConfirmationRequirement, MAX_PENDING_CONFIRMATIONS, MAX_TTL_TICKS,
    PolicyAcceptance, PolicyContext, PolicyDecision, PolicyDenial, PolicyDenialReason,
    PolicyDiagnostics, PolicyEngine, StoredConfirmationAcceptance, StoredConfirmationOutcome,
};
pub use error::{PolicyError, PolicyErrorCode, Result};
pub use table::{
    ExpectedSlot, PLAN_OPERATION_COUNT, PermittedGraphClasses, PolicyDescriptor, PolicyDisposition,
    PolicyGeneration, PolicyTable, RiskClass, STANDARD_DESCRIPTOR_COUNT, SlotKind,
};
