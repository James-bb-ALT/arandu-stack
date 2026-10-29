use core::fmt;

use crate::{
    AdapterError, CatalogGeneration, OperationKind, RegistryEntryId, Result, TargetSet,
    TypedOperation,
};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum MeasurementUnit {
    MilliCelsius,
    BasisPoints,
    MilliWatts,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum EntityState {
    On,
    Off,
    Open,
    Closed,
    Locked,
    Unlocked,
    Unavailable,
    Unknown,
    Measurement { value: i64, unit: MeasurementUnit },
}

#[derive(Clone, Eq, PartialEq)]
pub struct EntitySnapshot {
    generation: CatalogGeneration,
    target: RegistryEntryId,
    state: EntityState,
}

impl EntitySnapshot {
    #[must_use]
    pub const fn new(
        generation: CatalogGeneration,
        target: RegistryEntryId,
        state: EntityState,
    ) -> Self {
        Self {
            generation,
            target,
            state,
        }
    }

    #[must_use]
    pub const fn generation(&self) -> CatalogGeneration {
        self.generation
    }

    #[must_use]
    pub const fn target(&self) -> &RegistryEntryId {
        &self.target
    }

    #[must_use]
    pub const fn state(&self) -> EntityState {
        self.state
    }
}

impl fmt::Debug for EntitySnapshot {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EntitySnapshot")
            .field("generation", &self.generation)
            .field("target", &self.target)
            .field("state", &self.state)
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct BundledSnapshot {
    generation: CatalogGeneration,
    entries: Vec<EntitySnapshot>,
}

impl BundledSnapshot {
    pub fn new(
        generation: CatalogGeneration,
        expected_targets: &TargetSet,
        mut entries: Vec<EntitySnapshot>,
    ) -> Result<Self> {
        if entries.iter().any(|entry| entry.generation != generation) {
            return Err(AdapterError::SnapshotGenerationMismatch);
        }
        entries.sort_by(|left, right| left.target.cmp(&right.target));
        if entries
            .windows(2)
            .any(|pair| pair[0].target == pair[1].target)
        {
            return Err(AdapterError::DuplicateTarget);
        }
        if entries.len() != expected_targets.len()
            || entries
                .iter()
                .zip(expected_targets.as_slice())
                .any(|(entry, target)| entry.target != *target)
        {
            return Err(AdapterError::SnapshotTargetMismatch);
        }
        Ok(Self {
            generation,
            entries,
        })
    }

    #[must_use]
    pub const fn generation(&self) -> CatalogGeneration {
        self.generation
    }

    #[must_use]
    pub fn entries(&self) -> &[EntitySnapshot] {
        &self.entries
    }
}

impl fmt::Debug for BundledSnapshot {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("BundledSnapshot")
            .field("generation", &self.generation)
            .field("entry_count", &self.entries.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RevalidationFailure {
    StaleCatalog,
    CallerMissing,
    CallerInactive,
    PermissionDenied,
    AdminRequired,
    CapabilityMismatch,
    TargetChanged,
    PolicyDenied,
    ContextMismatch,
    PriorResultMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionFailure {
    Revalidation(RevalidationFailure),
    RejectedBeforeEffect,
    Expired,
    ContractViolation,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IndeterminateReason {
    DuplicateReserved,
    DuplicateInFlight,
    Timeout,
    CancellationRace,
    LostResponse,
    InvalidExecutorResult,
    PreviouslyIndeterminate,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionSuccess {
    StateRead(EntitySnapshot),
    BundledSnapshot(BundledSnapshot),
    EffectApplied(OperationKind),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NodeExecutionResult {
    Succeeded(ExecutionSuccess),
    Failed(ExecutionFailure),
    Indeterminate(IndeterminateReason),
}

impl NodeExecutionResult {
    #[must_use]
    pub const fn is_terminal_stop(&self) -> bool {
        matches!(self, Self::Failed(_) | Self::Indeterminate(_))
    }

    #[must_use]
    pub const fn is_definitive(&self) -> bool {
        !matches!(self, Self::Indeterminate(_))
    }

    pub(crate) fn is_compatible_with(
        &self,
        operation: &TypedOperation,
        generation: CatalogGeneration,
    ) -> bool {
        match self {
            Self::Succeeded(ExecutionSuccess::StateRead(snapshot)) => {
                operation.kind() == OperationKind::ReadEntityState
                    && snapshot.generation() == generation
                    && operation.targets().len() == 1
                    && &operation.targets().as_slice()[0] == snapshot.target()
            }
            Self::Succeeded(ExecutionSuccess::BundledSnapshot(snapshot)) => {
                operation.kind() == OperationKind::ReadBundledSnapshot
                    && snapshot.generation() == generation
                    && snapshot.entries().len() == operation.targets().len()
                    && snapshot
                        .entries()
                        .iter()
                        .zip(operation.targets().as_slice())
                        .all(|(entry, target)| entry.target() == target)
            }
            Self::Succeeded(ExecutionSuccess::EffectApplied(kind)) => {
                operation.kind() == *kind && kind.is_effecting()
            }
            Self::Failed(_) | Self::Indeterminate(_) => true,
        }
    }
}
