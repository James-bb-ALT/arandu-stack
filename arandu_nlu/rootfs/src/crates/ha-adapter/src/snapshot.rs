use core::fmt;

use crate::{
    AdapterError, AuthenticatedRequest, BundledSnapshot, CatalogGeneration, EntitySnapshot,
    EntityState, OperationKind, Result, TargetSet,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SnapshotSourceError {
    MissingTarget,
    Unauthorized,
    MalformedState,
    Unavailable,
}

pub trait ReadOnlySnapshotSource {
    fn catalog_generation(&self) -> CatalogGeneration;

    fn read_state(
        &mut self,
        target: &crate::RegistryEntryId,
    ) -> core::result::Result<EntityState, SnapshotSourceError>;
}

#[derive(Clone, Eq, PartialEq)]
pub struct BundledSnapshotContract {
    generation: CatalogGeneration,
    targets: TargetSet,
}

impl BundledSnapshotContract {
    pub fn from_request(request: &AuthenticatedRequest) -> Result<Self> {
        if request.operation().kind() != OperationKind::ReadBundledSnapshot {
            return Err(AdapterError::SnapshotOperationRequired);
        }
        Ok(Self {
            generation: request.catalog_generation(),
            targets: request.operation().targets().clone(),
        })
    }

    #[must_use]
    pub const fn generation(&self) -> CatalogGeneration {
        self.generation
    }

    #[must_use]
    pub const fn targets(&self) -> &TargetSet {
        &self.targets
    }

    pub fn capture<S: ReadOnlySnapshotSource>(&self, source: &mut S) -> Result<BundledSnapshot> {
        self.require_generation(source)?;
        let mut entries = Vec::with_capacity(self.targets.len());
        for target in self.targets.as_slice() {
            self.require_generation(source)?;
            let state = source
                .read_state(target)
                .map_err(|_| AdapterError::SnapshotUnavailable)?;
            self.require_generation(source)?;
            entries.push(EntitySnapshot::new(self.generation, target.clone(), state));
        }
        BundledSnapshot::new(self.generation, &self.targets, entries)
    }

    fn require_generation<S: ReadOnlySnapshotSource>(&self, source: &S) -> Result<()> {
        if source.catalog_generation() != self.generation {
            return Err(AdapterError::SnapshotGenerationMismatch);
        }
        Ok(())
    }
}

impl fmt::Debug for BundledSnapshotContract {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("BundledSnapshotContract")
            .field("generation", &self.generation)
            .field("target_count", &self.targets.len())
            .finish()
    }
}
