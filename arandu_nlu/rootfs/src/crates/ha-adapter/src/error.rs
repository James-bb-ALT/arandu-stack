use core::fmt;

pub type Result<T> = core::result::Result<T, AdapterError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AdapterError {
    InvalidOpaqueIdentifier,
    InvalidIdentifier,
    InvalidCapability,
    InvalidRegistryEntryId,
    InvalidProtocolVersion,
    WrongDirection,
    InvalidDeliveryKind,
    InvalidSequence,
    InvalidOperationId,
    InvalidNodeAttemptId,
    InvalidNodeOrdinal,
    InvalidCatalogGeneration,
    InvalidPosition,
    EmptyTargets,
    TooManyTargets,
    DuplicateTarget,
    BundledSnapshotRequiresMultipleTargets,
    CapabilityOperationMismatch,
    WrongEpoch,
    WrongConnection,
    UnexpectedSequence,
    ChannelRevoked,
    SequenceExhausted,
    InvalidCapacity,
    InvalidTtl,
    ClockRollback,
    DeadlineOverflow,
    LedgerCapacityExceeded,
    NoActiveEpoch,
    EpochReuse,
    EpochHistoryExhausted,
    UnknownDuplicate,
    BindingMismatch,
    InvalidLedgerTransition,
    ResultOperationMismatch,
    EmptySchedule,
    TooManyScheduleNodes,
    ScheduleBindingMismatch,
    DuplicateNodeAttempt,
    MixedScheduleReplay,
    ScheduleNotPrepared,
    ScheduleFinished,
    InvalidRoute,
    InvalidSafetyProof,
    SnapshotOperationRequired,
    SnapshotGenerationMismatch,
    SnapshotTargetMismatch,
    SnapshotUnavailable,
    LockPoisoned,
}

impl AdapterError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidOpaqueIdentifier => "invalid_opaque_identifier",
            Self::InvalidIdentifier => "invalid_identifier",
            Self::InvalidCapability => "invalid_capability",
            Self::InvalidRegistryEntryId => "invalid_registry_entry_id",
            Self::InvalidProtocolVersion => "invalid_protocol_version",
            Self::WrongDirection => "wrong_direction",
            Self::InvalidDeliveryKind => "invalid_delivery_kind",
            Self::InvalidSequence => "invalid_sequence",
            Self::InvalidOperationId => "invalid_operation_id",
            Self::InvalidNodeAttemptId => "invalid_node_attempt_id",
            Self::InvalidNodeOrdinal => "invalid_node_ordinal",
            Self::InvalidCatalogGeneration => "invalid_catalog_generation",
            Self::InvalidPosition => "invalid_position",
            Self::EmptyTargets => "empty_targets",
            Self::TooManyTargets => "too_many_targets",
            Self::DuplicateTarget => "duplicate_target",
            Self::BundledSnapshotRequiresMultipleTargets => {
                "bundled_snapshot_requires_multiple_targets"
            }
            Self::CapabilityOperationMismatch => "capability_operation_mismatch",
            Self::WrongEpoch => "wrong_epoch",
            Self::WrongConnection => "wrong_connection",
            Self::UnexpectedSequence => "unexpected_sequence",
            Self::ChannelRevoked => "channel_revoked",
            Self::SequenceExhausted => "sequence_exhausted",
            Self::InvalidCapacity => "invalid_capacity",
            Self::InvalidTtl => "invalid_ttl",
            Self::ClockRollback => "clock_rollback",
            Self::DeadlineOverflow => "deadline_overflow",
            Self::LedgerCapacityExceeded => "ledger_capacity_exceeded",
            Self::NoActiveEpoch => "no_active_epoch",
            Self::EpochReuse => "epoch_reuse",
            Self::EpochHistoryExhausted => "epoch_history_exhausted",
            Self::UnknownDuplicate => "unknown_duplicate",
            Self::BindingMismatch => "binding_mismatch",
            Self::InvalidLedgerTransition => "invalid_ledger_transition",
            Self::ResultOperationMismatch => "result_operation_mismatch",
            Self::EmptySchedule => "empty_schedule",
            Self::TooManyScheduleNodes => "too_many_schedule_nodes",
            Self::ScheduleBindingMismatch => "schedule_binding_mismatch",
            Self::DuplicateNodeAttempt => "duplicate_node_attempt",
            Self::MixedScheduleReplay => "mixed_schedule_replay",
            Self::ScheduleNotPrepared => "schedule_not_prepared",
            Self::ScheduleFinished => "schedule_finished",
            Self::InvalidRoute => "invalid_route",
            Self::InvalidSafetyProof => "invalid_safety_proof",
            Self::SnapshotOperationRequired => "snapshot_operation_required",
            Self::SnapshotGenerationMismatch => "snapshot_generation_mismatch",
            Self::SnapshotTargetMismatch => "snapshot_target_mismatch",
            Self::SnapshotUnavailable => "snapshot_unavailable",
            Self::LockPoisoned => "lock_poisoned",
        }
    }
}

impl fmt::Display for AdapterError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for AdapterError {}
