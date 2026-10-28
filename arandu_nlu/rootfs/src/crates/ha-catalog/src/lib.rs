#![forbid(unsafe_code)]

pub mod coverage;
pub mod error;
pub mod model;
pub mod resolve;
pub mod snapshot;
pub mod store;

pub use error::{AssociationKind, CatalogError, DuplicateKind, LimitKind};
pub use model::{
    AliasProvenance, AreaId, AreaInput, AreaRecord, CapabilityDescriptor,
    CapabilityDescriptorInput, DeviceId, DeviceInput, DeviceRecord, Domain, EntityInput,
    EntityInputParts, EntityRecord, EntityVisibility, ExplicitAlias, ExternalEntityId, FloorId,
    FloorInput, FloorRecord, RegistryEntryId, SensitiveText, StateQueryDisposition,
};
pub use resolve::{
    EntityClarification, EntityConstraint, EntityMatch, EntityQuery, EntityResolution,
    RankingFactor, RankingFactorKind, ResolutionAbstention, ResolutionExplanation, ResolutionRank,
    resolve_entity,
};
pub use snapshot::{CatalogSnapshot, CatalogSnapshotInput};
pub use store::CatalogStore;
