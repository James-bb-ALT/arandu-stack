use std::collections::{BTreeMap, BTreeSet};

use core::fmt;
use nlu_core::{CapabilityId, CatalogGeneration};

use crate::{
    AliasProvenance, AreaId, AreaInput, AreaRecord, AssociationKind, CapabilityDescriptor,
    CapabilityDescriptorInput, CatalogError, DeviceId, DeviceInput, DeviceRecord, DuplicateKind,
    EntityInput, EntityRecord, ExplicitAlias, ExternalEntityId, FloorId, FloorInput, FloorRecord,
    LimitKind, RegistryEntryId, StateQueryDisposition,
};

pub const MAX_FLOORS: usize = 256;
pub const MAX_AREAS: usize = 1_024;
pub const MAX_DEVICES: usize = 4_096;
pub const MAX_ENTITIES: usize = 4_096;
pub const MAX_CAPABILITY_DESCRIPTORS: usize = 256;
pub const MAX_ALIASES_PER_RECORD: usize = 64;
pub const MAX_CAPABILITIES_PER_ENTITY: usize = 64;
pub const MAX_DESCRIPTOR_DOMAINS: usize = 128;
pub const MAX_CATALOG_AGGREGATE_ITEMS: usize = 32_768;
pub const MAX_CATALOG_TEXT_BYTES: usize = 4_194_304;
pub const MAX_RESOLUTION_CANDIDATES: usize = nlu_core::MAX_CLARIFICATION_OPTIONS;

#[derive(Clone, Eq, PartialEq)]
pub struct CatalogSnapshotInput {
    pub generation: CatalogGeneration,
    pub floors: Vec<FloorInput>,
    pub areas: Vec<AreaInput>,
    pub devices: Vec<DeviceInput>,
    pub capability_descriptors: Vec<CapabilityDescriptorInput>,
    pub entities: Vec<EntityInput>,
}

impl CatalogSnapshotInput {
    #[must_use]
    pub const fn empty(generation: CatalogGeneration) -> Self {
        Self {
            generation,
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: Vec::new(),
            entities: Vec::new(),
        }
    }
}

impl fmt::Debug for CatalogSnapshotInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CatalogSnapshotInput")
            .field("generation", &self.generation)
            .field("floor_count", &self.floors.len())
            .field("area_count", &self.areas.len())
            .field("device_count", &self.devices.len())
            .field(
                "capability_descriptor_count",
                &self.capability_descriptors.len(),
            )
            .field("entity_count", &self.entities.len())
            .finish()
    }
}

pub struct CatalogSnapshot {
    generation: CatalogGeneration,
    floors: BTreeMap<FloorId, FloorRecord>,
    areas: BTreeMap<AreaId, AreaRecord>,
    devices: BTreeMap<DeviceId, DeviceRecord>,
    capability_descriptors: BTreeMap<CapabilityId, CapabilityDescriptor>,
    entities: BTreeMap<RegistryEntryId, EntityRecord>,
    external_index: BTreeMap<Box<str>, RegistryEntryId>,
    alias_index: BTreeMap<Box<str>, BTreeSet<RegistryEntryId>>,
    display_index: BTreeMap<Box<str>, BTreeSet<RegistryEntryId>>,
}

impl CatalogSnapshot {
    pub fn build(input: CatalogSnapshotInput) -> Result<Self, CatalogError> {
        validate_input_limits(&input)?;
        let generation = input.generation;
        let floors = build_floors(input.floors)?;
        let areas = build_areas(input.areas, &floors)?;
        let devices = build_devices(input.devices, &areas)?;
        let capability_descriptors = build_capability_descriptors(input.capability_descriptors)?;
        let (entities, external_index, alias_index, display_index) = build_entities(
            generation,
            input.entities,
            &floors,
            &areas,
            &devices,
            &capability_descriptors,
        )?;

        validate_candidate_buckets(&alias_index)?;
        validate_candidate_buckets(&display_index)?;

        Ok(Self {
            generation,
            floors,
            areas,
            devices,
            capability_descriptors,
            entities,
            external_index,
            alias_index,
            display_index,
        })
    }

    #[must_use]
    pub const fn generation(&self) -> CatalogGeneration {
        self.generation
    }

    #[must_use]
    pub const fn floors(&self) -> &BTreeMap<FloorId, FloorRecord> {
        &self.floors
    }

    #[must_use]
    pub const fn areas(&self) -> &BTreeMap<AreaId, AreaRecord> {
        &self.areas
    }

    #[must_use]
    pub const fn devices(&self) -> &BTreeMap<DeviceId, DeviceRecord> {
        &self.devices
    }

    #[must_use]
    pub const fn capability_descriptors(&self) -> &BTreeMap<CapabilityId, CapabilityDescriptor> {
        &self.capability_descriptors
    }

    #[must_use]
    pub const fn entities(&self) -> &BTreeMap<RegistryEntryId, EntityRecord> {
        &self.entities
    }

    #[must_use]
    pub fn floor(&self, id: &FloorId) -> Option<&FloorRecord> {
        self.floors.get(id)
    }

    #[must_use]
    pub fn area(&self, id: &AreaId) -> Option<&AreaRecord> {
        self.areas.get(id)
    }

    #[must_use]
    pub fn device(&self, id: &DeviceId) -> Option<&DeviceRecord> {
        self.devices.get(id)
    }

    #[must_use]
    pub fn capability_descriptor(&self, id: &CapabilityId) -> Option<&CapabilityDescriptor> {
        self.capability_descriptors.get(id)
    }

    #[must_use]
    pub fn entity(&self, id: &RegistryEntryId) -> Option<&EntityRecord> {
        self.entities.get(id)
    }

    #[must_use]
    pub fn entity_by_external_id(&self, id: &ExternalEntityId) -> Option<&EntityRecord> {
        self.external_index
            .get(id.as_str())
            .and_then(|registry_id| self.entities.get(registry_id))
    }

    #[must_use]
    pub fn state_query_disposition(
        &self,
        entity_id: &RegistryEntryId,
        capability_id: &CapabilityId,
    ) -> Option<StateQueryDisposition> {
        let entity = self.entities.get(entity_id)?;
        if !entity.capabilities().contains(capability_id) {
            return Some(StateQueryDisposition::AbstainCapabilityNotAssociated);
        }
        self.capability_descriptors
            .get(capability_id)
            .map(|descriptor| descriptor.state_query_disposition(entity.domain()))
    }

    pub(crate) fn external_candidate(&self, key: &str) -> Option<&RegistryEntryId> {
        self.external_index.get(key)
    }

    pub(crate) fn alias_candidates(&self, key: &str) -> Option<&BTreeSet<RegistryEntryId>> {
        self.alias_index.get(key)
    }

    pub(crate) fn display_candidates(&self, key: &str) -> Option<&BTreeSet<RegistryEntryId>> {
        self.display_index.get(key)
    }
}

impl fmt::Debug for CatalogSnapshot {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CatalogSnapshot")
            .field("generation", &self.generation)
            .field("floor_count", &self.floors.len())
            .field("area_count", &self.areas.len())
            .field("device_count", &self.devices.len())
            .field(
                "capability_descriptor_count",
                &self.capability_descriptors.len(),
            )
            .field("entity_count", &self.entities.len())
            .finish()
    }
}

fn build_floors(inputs: Vec<FloorInput>) -> Result<BTreeMap<FloorId, FloorRecord>, CatalogError> {
    let mut records = BTreeMap::new();
    for input in inputs {
        let aliases = canonical_aliases(input.aliases, AliasProvenance::FloorRegistry)?;
        let id = input.id;
        let record = FloorRecord {
            id: id.clone(),
            name: input.name,
            aliases,
        };
        if records.insert(id, record).is_some() {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::Floor,
            });
        }
    }
    Ok(records)
}

fn build_areas(
    inputs: Vec<AreaInput>,
    floors: &BTreeMap<FloorId, FloorRecord>,
) -> Result<BTreeMap<AreaId, AreaRecord>, CatalogError> {
    let mut records = BTreeMap::new();
    for input in inputs {
        if input
            .floor_id
            .as_ref()
            .is_some_and(|floor_id| !floors.contains_key(floor_id))
        {
            return Err(CatalogError::Dangling {
                kind: AssociationKind::AreaFloor,
            });
        }
        let aliases = canonical_aliases(input.aliases, AliasProvenance::AreaRegistry)?;
        let id = input.id;
        let record = AreaRecord {
            id: id.clone(),
            name: input.name,
            aliases,
            floor_id: input.floor_id,
        };
        if records.insert(id, record).is_some() {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::Area,
            });
        }
    }
    Ok(records)
}

fn build_devices(
    inputs: Vec<DeviceInput>,
    areas: &BTreeMap<AreaId, AreaRecord>,
) -> Result<BTreeMap<DeviceId, DeviceRecord>, CatalogError> {
    let mut records = BTreeMap::new();
    for input in inputs {
        if input
            .area_id
            .as_ref()
            .is_some_and(|area_id| !areas.contains_key(area_id))
        {
            return Err(CatalogError::Dangling {
                kind: AssociationKind::DeviceArea,
            });
        }
        let id = input.id;
        let record = DeviceRecord {
            id: id.clone(),
            name: input.name,
            area_id: input.area_id,
        };
        if records.insert(id, record).is_some() {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::Device,
            });
        }
    }
    Ok(records)
}

fn build_capability_descriptors(
    inputs: Vec<CapabilityDescriptorInput>,
) -> Result<BTreeMap<CapabilityId, CapabilityDescriptor>, CatalogError> {
    let mut records = BTreeMap::new();
    for mut input in inputs {
        if input.state_query_domains.len() > MAX_DESCRIPTOR_DOMAINS {
            return Err(CatalogError::LimitExceeded {
                kind: LimitKind::DescriptorDomains,
                limit: MAX_DESCRIPTOR_DOMAINS as u32,
            });
        }
        input.state_query_domains.sort();
        if input
            .state_query_domains
            .windows(2)
            .any(|pair| pair[0] == pair[1])
        {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::DescriptorDomain,
            });
        }
        let id = input.id;
        let record = CapabilityDescriptor {
            id: id.clone(),
            enabled: input.enabled,
            state_query_domains: input.state_query_domains.into_iter().collect(),
        };
        if records.insert(id, record).is_some() {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::CapabilityDescriptor,
            });
        }
    }
    Ok(records)
}

type EntityBuildOutput = (
    BTreeMap<RegistryEntryId, EntityRecord>,
    BTreeMap<Box<str>, RegistryEntryId>,
    BTreeMap<Box<str>, BTreeSet<RegistryEntryId>>,
    BTreeMap<Box<str>, BTreeSet<RegistryEntryId>>,
);

fn build_entities(
    generation: CatalogGeneration,
    inputs: Vec<EntityInput>,
    floors: &BTreeMap<FloorId, FloorRecord>,
    areas: &BTreeMap<AreaId, AreaRecord>,
    devices: &BTreeMap<DeviceId, DeviceRecord>,
    descriptors: &BTreeMap<CapabilityId, CapabilityDescriptor>,
) -> Result<EntityBuildOutput, CatalogError> {
    let mut seen_registry_ids = BTreeSet::new();
    let mut seen_external_ids = BTreeSet::new();
    let mut entities = BTreeMap::new();
    let mut external_index = BTreeMap::new();
    let mut alias_index = BTreeMap::<Box<str>, BTreeSet<RegistryEntryId>>::new();
    let mut display_index = BTreeMap::<Box<str>, BTreeSet<RegistryEntryId>>::new();

    for input in inputs {
        let mut parts = input.into_parts();
        if parts.generation != generation {
            return Err(CatalogError::StaleEntityGeneration);
        }
        if parts.external_id.domain() != &parts.domain {
            return Err(CatalogError::InconsistentEntityDomain);
        }
        if !seen_registry_ids.insert(parts.registry_id.clone()) {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::RegistryEntry,
            });
        }
        if !seen_external_ids.insert(parts.external_id.clone()) {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::ExternalEntityId,
            });
        }

        let aliases = canonical_aliases(parts.aliases, AliasProvenance::EntityRegistry)?;
        if parts.capabilities.len() > MAX_CAPABILITIES_PER_ENTITY {
            return Err(CatalogError::LimitExceeded {
                kind: LimitKind::CapabilitiesPerEntity,
                limit: MAX_CAPABILITIES_PER_ENTITY as u32,
            });
        }
        parts.capabilities.sort();
        if parts.capabilities.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CatalogError::Duplicate {
                kind: DuplicateKind::Capability,
            });
        }
        if parts
            .capabilities
            .iter()
            .any(|capability| !descriptors.contains_key(capability))
        {
            return Err(CatalogError::Dangling {
                kind: AssociationKind::EntityCapability,
            });
        }

        let device = match parts.device_id.as_ref() {
            Some(device_id) => Some(devices.get(device_id).ok_or(CatalogError::Dangling {
                kind: AssociationKind::EntityDevice,
            })?),
            None => None,
        };
        let effective_area_id = parts
            .area_id
            .clone()
            .or_else(|| device.and_then(DeviceRecord::area_id).cloned());
        let effective_area = match effective_area_id.as_ref() {
            Some(area_id) => Some(areas.get(area_id).ok_or(CatalogError::Dangling {
                kind: AssociationKind::EntityArea,
            })?),
            None => None,
        };
        let derived_floor_id = effective_area.and_then(AreaRecord::floor_id).cloned();
        if let Some(floor_id) = parts.floor_id.as_ref() {
            if !floors.contains_key(floor_id) {
                return Err(CatalogError::Dangling {
                    kind: AssociationKind::EntityFloor,
                });
            }
            if derived_floor_id.as_ref() != Some(floor_id) {
                return Err(CatalogError::InconsistentAreaFloor);
            }
        }
        let effective_floor_id = parts.floor_id.or(derived_floor_id);

        if !parts.visibility.is_catalog_visible() {
            continue;
        }

        let core_id = parts.registry_id.to_core_entity_id()?;
        let registry_id = parts.registry_id;
        let record = EntityRecord {
            generation,
            registry_id: registry_id.clone(),
            core_id,
            external_id: parts.external_id,
            domain: parts.domain,
            display_name: parts.display_name,
            aliases,
            capabilities: parts.capabilities.into_iter().collect(),
            area_id: effective_area_id,
            floor_id: effective_floor_id,
            device_id: parts.device_id,
        };

        external_index.insert(record.external_id().as_str().into(), registry_id.clone());
        for alias in record.aliases() {
            alias_index
                .entry(alias.text().nfc_key().into())
                .or_default()
                .insert(registry_id.clone());
        }
        display_index
            .entry(record.display_name().nfc_key().into())
            .or_default()
            .insert(registry_id.clone());
        entities.insert(registry_id, record);
    }

    Ok((entities, external_index, alias_index, display_index))
}

fn canonical_aliases(
    mut aliases: Vec<ExplicitAlias>,
    expected_provenance: AliasProvenance,
) -> Result<Box<[ExplicitAlias]>, CatalogError> {
    if aliases.len() > MAX_ALIASES_PER_RECORD {
        return Err(CatalogError::LimitExceeded {
            kind: LimitKind::AliasesPerRecord,
            limit: MAX_ALIASES_PER_RECORD as u32,
        });
    }
    if aliases
        .iter()
        .any(|alias| alias.provenance() != expected_provenance)
    {
        return Err(CatalogError::InvalidAliasProvenance);
    }
    aliases.sort_by(|left, right| {
        left.text()
            .nfc_key()
            .cmp(right.text().nfc_key())
            .then_with(|| left.text().as_bytes().cmp(right.text().as_bytes()))
    });
    if aliases
        .windows(2)
        .any(|pair| pair[0].text().nfc_key() == pair[1].text().nfc_key())
    {
        return Err(CatalogError::Duplicate {
            kind: DuplicateKind::Alias,
        });
    }
    Ok(aliases.into_boxed_slice())
}

fn validate_candidate_buckets(
    index: &BTreeMap<Box<str>, BTreeSet<RegistryEntryId>>,
) -> Result<(), CatalogError> {
    if index
        .values()
        .any(|candidates| candidates.len() > MAX_RESOLUTION_CANDIDATES)
    {
        return Err(CatalogError::LimitExceeded {
            kind: LimitKind::ResolutionCandidates,
            limit: MAX_RESOLUTION_CANDIDATES as u32,
        });
    }
    Ok(())
}

fn validate_input_limits(input: &CatalogSnapshotInput) -> Result<(), CatalogError> {
    check_count(input.floors.len(), MAX_FLOORS, LimitKind::Floors)?;
    check_count(input.areas.len(), MAX_AREAS, LimitKind::Areas)?;
    check_count(input.devices.len(), MAX_DEVICES, LimitKind::Devices)?;
    check_count(input.entities.len(), MAX_ENTITIES, LimitKind::Entities)?;
    check_count(
        input.capability_descriptors.len(),
        MAX_CAPABILITY_DESCRIPTORS,
        LimitKind::CapabilityDescriptors,
    )?;

    let mut items = 0_usize;
    let mut text_bytes = 0_usize;

    add_count(&mut items, input.floors.len())?;
    for floor in &input.floors {
        add_text(&mut text_bytes, floor.id.as_str())?;
        add_text(&mut text_bytes, floor.name.as_str())?;
        add_aliases(&mut items, &mut text_bytes, &floor.aliases)?;
    }

    add_count(&mut items, input.areas.len())?;
    for area in &input.areas {
        add_text(&mut text_bytes, area.id.as_str())?;
        add_text(&mut text_bytes, area.name.as_str())?;
        if let Some(floor_id) = &area.floor_id {
            add_text(&mut text_bytes, floor_id.as_str())?;
        }
        add_aliases(&mut items, &mut text_bytes, &area.aliases)?;
    }

    add_count(&mut items, input.devices.len())?;
    for device in &input.devices {
        add_text(&mut text_bytes, device.id.as_str())?;
        add_text(&mut text_bytes, device.name.as_str())?;
        if let Some(area_id) = &device.area_id {
            add_text(&mut text_bytes, area_id.as_str())?;
        }
    }

    add_count(&mut items, input.capability_descriptors.len())?;
    for descriptor in &input.capability_descriptors {
        add_text(&mut text_bytes, descriptor.id.as_str())?;
        add_count(&mut items, descriptor.state_query_domains.len())?;
        for domain in &descriptor.state_query_domains {
            add_text(&mut text_bytes, domain.as_str())?;
        }
    }

    add_count(&mut items, input.entities.len())?;
    for entity in &input.entities {
        let parts = entity.parts();
        add_text(&mut text_bytes, parts.registry_id.as_str())?;
        add_text(&mut text_bytes, parts.external_id.as_str())?;
        add_text(&mut text_bytes, parts.domain.as_str())?;
        add_text(&mut text_bytes, parts.display_name.as_str())?;
        if let Some(area_id) = &parts.area_id {
            add_text(&mut text_bytes, area_id.as_str())?;
        }
        if let Some(floor_id) = &parts.floor_id {
            add_text(&mut text_bytes, floor_id.as_str())?;
        }
        if let Some(device_id) = &parts.device_id {
            add_text(&mut text_bytes, device_id.as_str())?;
        }
        add_aliases(&mut items, &mut text_bytes, &parts.aliases)?;
        add_count(&mut items, parts.capabilities.len())?;
        for capability in &parts.capabilities {
            add_text(&mut text_bytes, capability.as_str())?;
        }
    }

    check_count(
        items,
        MAX_CATALOG_AGGREGATE_ITEMS,
        LimitKind::AggregateItems,
    )?;
    check_count(
        text_bytes,
        MAX_CATALOG_TEXT_BYTES,
        LimitKind::AggregateTextBytes,
    )
}

fn add_aliases(
    items: &mut usize,
    text_bytes: &mut usize,
    aliases: &[ExplicitAlias],
) -> Result<(), CatalogError> {
    add_count(items, aliases.len())?;
    for alias in aliases {
        add_text(text_bytes, alias.text().as_str())?;
    }
    Ok(())
}

fn add_count(total: &mut usize, value: usize) -> Result<(), CatalogError> {
    *total = total
        .checked_add(value)
        .ok_or(CatalogError::AggregateOverflow)?;
    Ok(())
}

fn add_text(total: &mut usize, value: &str) -> Result<(), CatalogError> {
    add_count(total, value.len())
}

fn check_count(value: usize, limit: usize, kind: LimitKind) -> Result<(), CatalogError> {
    if value > limit {
        return Err(CatalogError::LimitExceeded {
            kind,
            limit: limit as u32,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use nlu_core::{CapabilityId, CatalogGeneration};

    use super::*;
    use crate::{
        AreaInput, Domain, EntityInputParts, EntityVisibility, ExternalEntityId, SensitiveText,
    };

    const FIXTURE_TECNICA_REGISTRY_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const FIXTURE_TECNICA_AREA_A: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    fn generation() -> CatalogGeneration {
        CatalogGeneration::new(1).expect("FIXTURE_TECNICA generation")
    }

    fn entity() -> EntityInput {
        EntityInput::new(EntityInputParts {
            generation: generation(),
            registry_id: RegistryEntryId::new(FIXTURE_TECNICA_REGISTRY_A)
                .expect("FIXTURE_TECNICA registry"),
            external_id: ExternalEntityId::new("fixture_tecnica.entity_a")
                .expect("FIXTURE_TECNICA external ID"),
            domain: Domain::new("fixture_tecnica").expect("FIXTURE_TECNICA domain"),
            display_name: SensitiveText::new("FIXTURE_TECNICA_DISPLAY_A")
                .expect("FIXTURE_TECNICA display"),
            aliases: Vec::new(),
            capabilities: Vec::new(),
            area_id: None,
            floor_id: None,
            device_id: None,
            visibility: EntityVisibility::exposed(),
        })
    }

    #[test]
    fn stable_identity_is_registry_based_and_unexposed_entities_are_filtered() {
        let exposed = entity();
        let mut hidden_parts = entity().into_parts();
        hidden_parts.registry_id =
            RegistryEntryId::new("cccccccccccccccccccccccccccccccc").expect("fixture ID");
        hidden_parts.external_id =
            ExternalEntityId::new("fixture_tecnica.entity_b").expect("fixture external");
        hidden_parts.visibility = EntityVisibility::new(false, true, false);

        let snapshot = CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: Vec::new(),
            entities: vec![EntityInput::new(hidden_parts), exposed],
        })
        .expect("FIXTURE_TECNICA snapshot");

        assert_eq!(snapshot.entities().len(), 1);
        let record = snapshot
            .entities()
            .values()
            .next()
            .expect("one exposed record");
        assert_eq!(
            record.core_id().as_str(),
            "ha_entity:id_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        );
        assert_ne!(record.core_id().as_str(), record.external_id().as_str());
    }

    #[test]
    fn dangling_and_inconsistent_associations_are_rejected() {
        let mut dangling = entity().into_parts();
        dangling.area_id = Some(AreaId::new(FIXTURE_TECNICA_AREA_A).expect("FIXTURE_TECNICA area"));
        let error = CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: Vec::new(),
            entities: vec![EntityInput::new(dangling)],
        })
        .expect_err("dangling area");
        assert_eq!(
            error,
            CatalogError::Dangling {
                kind: AssociationKind::EntityArea
            }
        );

        let area = AreaInput {
            id: AreaId::new(FIXTURE_TECNICA_AREA_A).expect("area"),
            name: SensitiveText::new("FIXTURE_TECNICA_AREA_A").expect("area name"),
            aliases: Vec::new(),
            floor_id: None,
        };
        let mut inconsistent = entity().into_parts();
        inconsistent.area_id = Some(area.id.clone());
        inconsistent.floor_id =
            Some(FloorId::new("dddddddddddddddddddddddddddddddd").expect("floor"));
        let error = CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: vec![FloorInput {
                id: inconsistent.floor_id.clone().expect("floor"),
                name: SensitiveText::new("FIXTURE_TECNICA_FLOOR_A").expect("floor name"),
                aliases: Vec::new(),
            }],
            areas: vec![area],
            devices: Vec::new(),
            capability_descriptors: Vec::new(),
            entities: vec![EntityInput::new(inconsistent)],
        })
        .expect_err("inconsistent floor");
        assert_eq!(error, CatalogError::InconsistentAreaFloor);
    }

    #[test]
    fn descriptors_disable_and_narrow_without_closing_domains() {
        let capability =
            CapabilityId::new("fixture_tecnica:state_a").expect("FIXTURE_TECNICA capability");
        let mut parts = entity().into_parts();
        parts.capabilities.push(capability.clone());
        let descriptor = CapabilityDescriptorInput {
            id: capability.clone(),
            enabled: true,
            state_query_domains: vec![Domain::new("fixture_tecnica").expect("domain")],
        };
        let snapshot = CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: vec![descriptor],
            entities: vec![EntityInput::new(parts)],
        })
        .expect("snapshot");
        assert_eq!(
            snapshot.state_query_disposition(
                &RegistryEntryId::new(FIXTURE_TECNICA_REGISTRY_A).expect("ID"),
                &capability
            ),
            Some(StateQueryDisposition::Supported)
        );

        let unknown = Domain::new("fixture_tecnica_future").expect("open FIXTURE_TECNICA domain");
        assert!(!crate::coverage::is_pinned_domain(unknown.as_str()));
        assert_eq!(
            snapshot
                .capability_descriptor(&capability)
                .expect("descriptor")
                .state_query_disposition(&unknown),
            StateQueryDisposition::AbstainDomainNotReviewed
        );
    }

    #[test]
    fn snapshot_debug_redacts_residential_canaries() {
        let canary = "FIXTURE_TECNICA_PRIVATE_CANARY";
        let mut parts = entity().into_parts();
        parts.display_name = SensitiveText::new(canary).expect("canary");
        let snapshot = CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: Vec::new(),
            entities: vec![EntityInput::new(parts)],
        })
        .expect("snapshot");
        let output = format!("{snapshot:?} {:?}", snapshot.entities());
        assert!(!output.contains(canary));
        assert!(
            !CatalogError::InvalidSensitiveText
                .to_string()
                .contains(canary)
        );
    }
}
