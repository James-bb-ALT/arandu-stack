use core::fmt;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum DuplicateKind {
    RegistryEntry,
    ExternalEntityId,
    Device,
    Area,
    Floor,
    CapabilityDescriptor,
    Alias,
    Capability,
    DescriptorDomain,
    Constraint,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AssociationKind {
    DeviceArea,
    AreaFloor,
    EntityDevice,
    EntityArea,
    EntityFloor,
    EntityCapability,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LimitKind {
    SensitiveTextBytes,
    DomainBytes,
    Floors,
    Areas,
    Devices,
    Entities,
    CapabilityDescriptors,
    AliasesPerRecord,
    CapabilitiesPerEntity,
    DescriptorDomains,
    AggregateItems,
    AggregateTextBytes,
    ResolutionCandidates,
    QueryConstraints,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CatalogError {
    InvalidRegistryEntryId,
    InvalidDeviceId,
    InvalidAreaId,
    InvalidFloorId,
    InvalidDomain,
    InvalidExternalEntityId,
    InvalidSensitiveText,
    InvalidQueryText,
    InvalidAliasProvenance,
    InvalidCoreEntityId,
    InvalidCapabilityDescriptor,
    Duplicate { kind: DuplicateKind },
    Dangling { kind: AssociationKind },
    InconsistentEntityDomain,
    InconsistentAreaFloor,
    StaleEntityGeneration,
    StaleCatalogGeneration,
    NonSequentialCatalogGeneration,
    CatalogGenerationOverflow,
    ContradictoryConstraint,
    SpanSourceMismatch,
    LimitExceeded { kind: LimitKind, limit: u32 },
    AggregateOverflow,
    LockPoisoned,
}

impl CatalogError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidRegistryEntryId => "catalog_invalid_registry_entry_id",
            Self::InvalidDeviceId => "catalog_invalid_device_id",
            Self::InvalidAreaId => "catalog_invalid_area_id",
            Self::InvalidFloorId => "catalog_invalid_floor_id",
            Self::InvalidDomain => "catalog_invalid_domain",
            Self::InvalidExternalEntityId => "catalog_invalid_external_entity_id",
            Self::InvalidSensitiveText => "catalog_invalid_sensitive_text",
            Self::InvalidQueryText => "catalog_invalid_query_text",
            Self::InvalidAliasProvenance => "catalog_invalid_alias_provenance",
            Self::InvalidCoreEntityId => "catalog_invalid_core_entity_id",
            Self::InvalidCapabilityDescriptor => "catalog_invalid_capability_descriptor",
            Self::Duplicate { .. } => "catalog_duplicate",
            Self::Dangling { .. } => "catalog_dangling_association",
            Self::InconsistentEntityDomain => "catalog_inconsistent_entity_domain",
            Self::InconsistentAreaFloor => "catalog_inconsistent_area_floor",
            Self::StaleEntityGeneration => "catalog_stale_entity_generation",
            Self::StaleCatalogGeneration => "catalog_stale_generation",
            Self::NonSequentialCatalogGeneration => "catalog_nonsequential_generation",
            Self::CatalogGenerationOverflow => "catalog_generation_overflow",
            Self::ContradictoryConstraint => "catalog_contradictory_constraint",
            Self::SpanSourceMismatch => "catalog_span_source_mismatch",
            Self::LimitExceeded { .. } => "catalog_limit_exceeded",
            Self::AggregateOverflow => "catalog_aggregate_overflow",
            Self::LockPoisoned => "catalog_lock_poisoned",
        }
    }
}

impl fmt::Display for CatalogError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for CatalogError {}
