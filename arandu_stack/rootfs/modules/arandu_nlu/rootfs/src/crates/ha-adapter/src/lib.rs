#![forbid(unsafe_code)]

mod binding;
mod error;
mod ledger;
mod operation;
mod outcome;
mod render;
mod routing;
mod schedule;
mod snapshot;
mod types;

pub use binding::{
    AUTHENTICATED_PROTOCOL_VERSION, AuthenticatedRequest, AuthenticatedRequestParts, ChannelGuard,
    DeliveryKind, Direction, ProtocolVersion, Sequence,
};
pub use error::{AdapterError, Result};
pub use ledger::{
    LedgerConfig, LedgerDiagnostics, LedgerStatus, MAX_RETIRED_EPOCHS, OperationLedger,
    ReservationOutcome,
};
pub use operation::{MAX_OPERATION_TARGETS, OperationKind, TargetSet, TypedOperation};
pub use outcome::{
    BundledSnapshot, EntitySnapshot, EntityState, ExecutionFailure, ExecutionSuccess,
    IndeterminateReason, MeasurementUnit, NodeExecutionResult, RevalidationFailure,
};
pub use render::{
    ExecutionRenderOutcome, InterpretationOutcome, InterpretationReason, RenderedResponse,
    ResponseRenderer,
};
pub use routing::{
    CompanionMode, GraphTopology, RouteAbstention, RouteNode, RoutePlan, TargetScope,
    TransportRoute, WyomingSafetyAssessment, WyomingSafetyProof, select_transport,
};
pub use schedule::{
    ExecutionReport, ExecutionSchedule, NodeExecutor, NodeRevalidator, RevalidationDecision,
    ScheduleProgress, ScheduleState,
};
pub use snapshot::{BundledSnapshotContract, ReadOnlySnapshotSource, SnapshotSourceError};
pub use types::{
    CallerId, CapabilityId, CatalogGeneration, ConnectionNonce, ContextId, LogicalTime,
    NodeAttemptId, NodeOrdinal, OperationId, PairingEpoch, PlanDigest, PositionPercent,
    RegistryEntryId, SessionId,
};
