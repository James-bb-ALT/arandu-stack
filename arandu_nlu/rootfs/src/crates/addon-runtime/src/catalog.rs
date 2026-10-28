use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};

use ha_catalog::{
    AliasProvenance, AreaId, AreaInput, CapabilityDescriptorInput, CatalogSnapshot,
    CatalogSnapshotInput, DeviceId, DeviceInput, Domain, EntityInput, EntityInputParts,
    EntityVisibility, ExplicitAlias, ExternalEntityId, FloorId, FloorInput, RegistryEntryId,
    SensitiveText,
};
use nlu_core::{CapabilityId, CatalogGeneration};
use serde_json::{Map, Value, json};

use crate::{Result, RuntimeError, SupervisorCommand, SupervisorSnapshot};

pub const MAX_CATALOG_FRAME_BYTES: usize = 4_194_304;
const CATALOG_VERSION: u16 = 2;
const MAX_CATALOG_ENTITIES: usize = ha_catalog::snapshot::MAX_ENTITIES;
const MAX_ENTITY_ALIASES: usize = ha_catalog::snapshot::MAX_ALIASES_PER_RECORD;
const ALLOWED_CAPABILITIES: [&str; 3] = ["ha:light_control", "ha:state_query", "ha:switch_control"];

#[derive(Clone, Eq, PartialEq)]
pub struct CatalogFloor {
    id: Box<str>,
    name: Box<str>,
    aliases: Vec<Box<str>>,
}

impl CatalogFloor {
    fn new(id: &str, name: String, aliases: Vec<String>) -> Result<Self> {
        FloorId::new(id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        SensitiveText::new(name.clone()).map_err(|_| RuntimeError::CatalogSnapshot)?;
        Ok(Self {
            id: id.into(),
            name: name.into(),
            aliases: canonical_strings(aliases, MAX_ENTITY_ALIASES, false)?,
        })
    }

    fn projection(&self) -> Value {
        json!({
            "aliases": self.aliases,
            "floor_id": self.id,
            "name": self.name,
        })
    }

    fn into_input(self) -> Result<FloorInput> {
        Ok(FloorInput {
            id: FloorId::new(&self.id).map_err(|_| RuntimeError::CatalogSnapshot)?,
            name: SensitiveText::new(String::from(self.name))
                .map_err(|_| RuntimeError::CatalogSnapshot)?,
            aliases: explicit_aliases(self.aliases, AliasProvenance::FloorRegistry)?,
        })
    }
}

impl core::fmt::Debug for CatalogFloor {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CatalogFloor")
            .field("alias_count", &self.aliases.len())
            .field("residential_data", &"redacted")
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct CatalogArea {
    id: Box<str>,
    name: Box<str>,
    aliases: Vec<Box<str>>,
    floor_id: Option<Box<str>>,
}

impl CatalogArea {
    fn new(id: &str, name: String, aliases: Vec<String>, floor_id: Option<&str>) -> Result<Self> {
        AreaId::new(id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        SensitiveText::new(name.clone()).map_err(|_| RuntimeError::CatalogSnapshot)?;
        if let Some(value) = floor_id {
            FloorId::new(value).map_err(|_| RuntimeError::CatalogSnapshot)?;
        }
        Ok(Self {
            id: id.into(),
            name: name.into(),
            aliases: canonical_strings(aliases, MAX_ENTITY_ALIASES, false)?,
            floor_id: floor_id.map(Into::into),
        })
    }

    fn projection(&self) -> Value {
        json!({
            "aliases": self.aliases,
            "area_id": self.id,
            "floor_id": self.floor_id,
            "name": self.name,
        })
    }

    fn into_input(self) -> Result<AreaInput> {
        Ok(AreaInput {
            id: AreaId::new(&self.id).map_err(|_| RuntimeError::CatalogSnapshot)?,
            name: SensitiveText::new(String::from(self.name))
                .map_err(|_| RuntimeError::CatalogSnapshot)?,
            aliases: explicit_aliases(self.aliases, AliasProvenance::AreaRegistry)?,
            floor_id: self
                .floor_id
                .map(|value| FloorId::new(&value).map_err(|_| RuntimeError::CatalogSnapshot))
                .transpose()?,
        })
    }
}

impl core::fmt::Debug for CatalogArea {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CatalogArea")
            .field("alias_count", &self.aliases.len())
            .field("has_floor", &self.floor_id.is_some())
            .field("residential_data", &"redacted")
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct CatalogDevice {
    id: Box<str>,
    name: Box<str>,
    area_id: Option<Box<str>>,
}

impl CatalogDevice {
    fn new(id: &str, name: String, area_id: Option<&str>) -> Result<Self> {
        DeviceId::new(id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        SensitiveText::new(name.clone()).map_err(|_| RuntimeError::CatalogSnapshot)?;
        if let Some(value) = area_id {
            AreaId::new(value).map_err(|_| RuntimeError::CatalogSnapshot)?;
        }
        Ok(Self {
            id: id.into(),
            name: name.into(),
            area_id: area_id.map(Into::into),
        })
    }

    fn projection(&self) -> Value {
        json!({
            "area_id": self.area_id,
            "device_id": self.id,
            "name": self.name,
        })
    }

    fn into_input(self) -> Result<DeviceInput> {
        Ok(DeviceInput {
            id: DeviceId::new(&self.id).map_err(|_| RuntimeError::CatalogSnapshot)?,
            name: SensitiveText::new(String::from(self.name))
                .map_err(|_| RuntimeError::CatalogSnapshot)?,
            area_id: self
                .area_id
                .map(|value| AreaId::new(&value).map_err(|_| RuntimeError::CatalogSnapshot))
                .transpose()?,
        })
    }
}

impl core::fmt::Debug for CatalogDevice {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CatalogDevice")
            .field("has_area", &self.area_id.is_some())
            .field("residential_data", &"redacted")
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct CatalogEntity {
    registry_id: Box<str>,
    external_id: Box<str>,
    display_name: Box<str>,
    aliases: Vec<Box<str>>,
    capabilities: Vec<Box<str>>,
    area_id: Option<Box<str>>,
    floor_id: Option<Box<str>>,
    device_id: Option<Box<str>>,
}

impl CatalogEntity {
    pub fn new(
        registry_id: &str,
        external_id: &str,
        display_name: String,
        aliases: Vec<String>,
        capabilities: Vec<String>,
    ) -> Result<Self> {
        Self::with_associations(
            registry_id,
            external_id,
            display_name,
            aliases,
            capabilities,
            None,
            None,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn with_associations(
        registry_id: &str,
        external_id: &str,
        display_name: String,
        aliases: Vec<String>,
        capabilities: Vec<String>,
        area_id: Option<&str>,
        floor_id: Option<&str>,
        device_id: Option<&str>,
    ) -> Result<Self> {
        RegistryEntryId::new(registry_id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        ExternalEntityId::new(external_id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        SensitiveText::new(display_name.clone()).map_err(|_| RuntimeError::CatalogSnapshot)?;
        if let Some(value) = area_id {
            AreaId::new(value).map_err(|_| RuntimeError::CatalogSnapshot)?;
        }
        if let Some(value) = floor_id {
            FloorId::new(value).map_err(|_| RuntimeError::CatalogSnapshot)?;
        }
        if let Some(value) = device_id {
            DeviceId::new(value).map_err(|_| RuntimeError::CatalogSnapshot)?;
        }
        let aliases = canonical_strings(aliases, MAX_ENTITY_ALIASES, false)?;
        let capabilities = canonical_strings(capabilities, ALLOWED_CAPABILITIES.len(), true)?;
        if capabilities.is_empty()
            || capabilities
                .iter()
                .any(|capability| !ALLOWED_CAPABILITIES.contains(&capability.as_ref()))
        {
            return Err(RuntimeError::CatalogSnapshot);
        }
        Ok(Self {
            registry_id: registry_id.into(),
            external_id: external_id.into(),
            display_name: display_name.into(),
            aliases,
            capabilities,
            area_id: area_id.map(Into::into),
            floor_id: floor_id.map(Into::into),
            device_id: device_id.map(Into::into),
        })
    }

    #[must_use]
    pub fn registry_id(&self) -> &str {
        &self.registry_id
    }

    #[must_use]
    pub fn external_id(&self) -> &str {
        &self.external_id
    }

    fn projection(&self) -> Value {
        json!({
            "aliases": self.aliases,
            "area_id": self.area_id,
            "capabilities": self.capabilities,
            "device_id": self.device_id,
            "display_name": self.display_name,
            "external_id": self.external_id,
            "floor_id": self.floor_id,
            "registry_id": self.registry_id,
        })
    }

    fn legacy_projection(&self) -> Value {
        json!({
            "aliases": self.aliases,
            "capabilities": self.capabilities,
            "display_name": self.display_name,
            "external_id": self.external_id,
            "registry_id": self.registry_id,
        })
    }

    fn has_associations(&self) -> bool {
        self.area_id.is_some() || self.floor_id.is_some() || self.device_id.is_some()
    }

    fn into_input(self, generation: CatalogGeneration) -> Result<EntityInput> {
        let external_id =
            ExternalEntityId::new(&self.external_id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        let domain = external_id.domain().clone();
        let aliases = self
            .aliases
            .into_iter()
            .map(|alias| {
                SensitiveText::new(String::from(alias))
                    .map(|text| ExplicitAlias::new(text, AliasProvenance::EntityRegistry))
                    .map_err(|_| RuntimeError::CatalogSnapshot)
            })
            .collect::<Result<Vec<_>>>()?;
        let capabilities = self
            .capabilities
            .into_iter()
            .map(|capability| {
                CapabilityId::new(&capability).map_err(|_| RuntimeError::CatalogSnapshot)
            })
            .collect::<Result<Vec<_>>>()?;
        Ok(EntityInput::new(EntityInputParts {
            generation,
            registry_id: RegistryEntryId::new(&self.registry_id)
                .map_err(|_| RuntimeError::CatalogSnapshot)?,
            external_id,
            domain,
            display_name: SensitiveText::new(String::from(self.display_name))
                .map_err(|_| RuntimeError::CatalogSnapshot)?,
            aliases,
            capabilities,
            area_id: self
                .area_id
                .map(|value| AreaId::new(&value).map_err(|_| RuntimeError::CatalogSnapshot))
                .transpose()?,
            floor_id: self
                .floor_id
                .map(|value| FloorId::new(&value).map_err(|_| RuntimeError::CatalogSnapshot))
                .transpose()?,
            device_id: self
                .device_id
                .map(|value| DeviceId::new(&value).map_err(|_| RuntimeError::CatalogSnapshot))
                .transpose()?,
            visibility: EntityVisibility::exposed(),
        }))
    }
}

impl core::fmt::Debug for CatalogEntity {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CatalogEntity")
            .field("alias_count", &self.aliases.len())
            .field("capability_count", &self.capabilities.len())
            .field("residential_data", &"redacted")
            .finish()
    }
}

pub struct CatalogSeed {
    generation: u64,
    floors: Vec<CatalogFloor>,
    areas: Vec<CatalogArea>,
    devices: Vec<CatalogDevice>,
    entities: Vec<CatalogEntity>,
}

impl CatalogSeed {
    pub fn new(generation: u64, entities: Vec<CatalogEntity>) -> Result<Self> {
        Self::with_topology(generation, Vec::new(), Vec::new(), Vec::new(), entities)
    }

    fn with_topology(
        generation: u64,
        mut floors: Vec<CatalogFloor>,
        mut areas: Vec<CatalogArea>,
        mut devices: Vec<CatalogDevice>,
        mut entities: Vec<CatalogEntity>,
    ) -> Result<Self> {
        CatalogGeneration::new(generation).map_err(|_| RuntimeError::CatalogSnapshot)?;
        if entities.len() > MAX_CATALOG_ENTITIES {
            return Err(RuntimeError::CatalogSnapshot);
        }
        floors.sort_by(|left, right| left.id.cmp(&right.id));
        areas.sort_by(|left, right| left.id.cmp(&right.id));
        devices.sort_by(|left, right| left.id.cmp(&right.id));
        entities.sort_by(|left, right| left.registry_id.cmp(&right.registry_id));
        if duplicate_by(&floors, |value| value.id.as_ref())
            || duplicate_by(&areas, |value| value.id.as_ref())
            || duplicate_by(&devices, |value| value.id.as_ref())
            || duplicate_by(&entities, |value| value.registry_id.as_ref())
        {
            return Err(RuntimeError::CatalogSnapshot);
        }
        let seed = Self {
            generation,
            floors,
            areas,
            devices,
            entities,
        };
        seed.validate_external_ids()?;
        Ok(seed)
    }

    pub fn from_supervisor_snapshot(snapshot: &SupervisorSnapshot) -> Result<Self> {
        let area_values = registry_array(snapshot, SupervisorCommand::AreaRegistryList)?;
        let device_values = registry_array(snapshot, SupervisorCommand::DeviceRegistryList)?;
        let floor_values = registry_array(snapshot, SupervisorCommand::FloorRegistryList)?;
        let area_objects = index_registry_values(area_values, "area_id")?;
        let device_objects = index_registry_values(device_values, "id")?;
        let floor_objects = index_registry_values(floor_values, "floor_id")?;

        let registry = snapshot
            .get(SupervisorCommand::EntityRegistryList)
            .and_then(Value::as_array)
            .ok_or(RuntimeError::CatalogSnapshot)?;
        if registry.len() > MAX_CATALOG_ENTITIES {
            return Err(RuntimeError::CatalogSnapshot);
        }
        let states = state_names(
            snapshot
                .get(SupervisorCommand::GetStates)
                .and_then(Value::as_array)
                .ok_or(RuntimeError::CatalogSnapshot)?,
        )?;
        let exposed = exposed_entities(
            snapshot
                .get(SupervisorCommand::ExposeEntityList)
                .ok_or(RuntimeError::CatalogSnapshot)?,
        )?;
        let services = snapshot
            .get(SupervisorCommand::GetServices)
            .and_then(Value::as_object)
            .ok_or(RuntimeError::CatalogSnapshot)?;

        let mut selected_floors = BTreeMap::<Box<str>, CatalogFloor>::new();
        let mut selected_areas = BTreeMap::<Box<str>, CatalogArea>::new();
        let mut selected_devices = BTreeMap::<Box<str>, CatalogDevice>::new();
        let mut entities = Vec::new();
        for value in registry {
            let object = value.as_object().ok_or(RuntimeError::CatalogSnapshot)?;
            if !matches!(object.get("disabled_by"), Some(Value::Null)) {
                continue;
            }
            let registry_id = required_catalog_string(object, "id")?;
            let external_id = required_catalog_string(object, "entity_id")?;
            if !exposed.contains(external_id) {
                continue;
            }
            let state_name = states
                .get(external_id)
                .ok_or(RuntimeError::CatalogSnapshot)?;
            let display_name = optional_catalog_string(object, "name")?
                .or(state_name.as_deref())
                .or(optional_catalog_string(object, "original_name")?)
                .ok_or(RuntimeError::CatalogSnapshot)?
                .to_owned();
            let domain = external_id
                .split_once('.')
                .map(|(domain, _)| domain)
                .ok_or(RuntimeError::CatalogSnapshot)?;
            let mut capabilities = vec!["ha:state_query".to_owned()];
            if domain == "light" && has_service(services, "light", "turn_on") {
                capabilities.push("ha:light_control".to_owned());
            }
            if domain == "switch" && has_service(services, "switch", "turn_off") {
                capabilities.push("ha:switch_control".to_owned());
            }
            let device_id = optional_catalog_string(object, "device_id")?;
            let explicit_area_id = optional_catalog_string(object, "area_id")?;
            let device_area_id = if let Some(id) = device_id {
                let device_object = device_objects
                    .get(id)
                    .ok_or(RuntimeError::CatalogSnapshot)?;
                if !matches!(device_object.get("disabled_by"), None | Some(Value::Null)) {
                    return Err(RuntimeError::CatalogSnapshot);
                }
                let area_id = optional_catalog_string(device_object, "area_id")?;
                let name = optional_catalog_string(device_object, "name_by_user")?
                    .or(optional_catalog_string(device_object, "name")?)
                    .ok_or(RuntimeError::CatalogSnapshot)?
                    .to_owned();
                let device = CatalogDevice::new(id, name, area_id)?;
                selected_devices.entry(id.into()).or_insert(device);
                if let Some(device_area_id) = area_id {
                    select_area(
                        device_area_id,
                        &area_objects,
                        &floor_objects,
                        &mut selected_areas,
                        &mut selected_floors,
                    )?;
                }
                area_id
            } else {
                None
            };
            let area_id = explicit_area_id.or(device_area_id);
            let floor_id = area_id
                .map(|id| {
                    select_area(
                        id,
                        &area_objects,
                        &floor_objects,
                        &mut selected_areas,
                        &mut selected_floors,
                    )
                })
                .transpose()?
                .flatten();
            entities.push(CatalogEntity::with_associations(
                registry_id,
                external_id,
                display_name,
                required_catalog_strings(object, "aliases")?,
                capabilities,
                area_id,
                floor_id.as_deref(),
                device_id,
            )?);
        }
        entities.sort_by(|left, right| left.registry_id.cmp(&right.registry_id));
        let floors = selected_floors.into_values().collect::<Vec<_>>();
        let areas = selected_areas.into_values().collect::<Vec<_>>();
        let devices = selected_devices.into_values().collect::<Vec<_>>();
        let generation = content_generation(&floors, &areas, &devices, &entities)?;
        Self::with_topology(generation, floors, areas, devices, entities)
    }

    #[must_use]
    pub const fn generation(&self) -> u64 {
        self.generation
    }

    #[must_use]
    pub fn entities(&self) -> &[CatalogEntity] {
        &self.entities
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        let floors = self
            .floors
            .iter()
            .map(CatalogFloor::projection)
            .collect::<Vec<_>>();
        let areas = self
            .areas
            .iter()
            .map(CatalogArea::projection)
            .collect::<Vec<_>>();
        let devices = self
            .devices
            .iter()
            .map(CatalogDevice::projection)
            .collect::<Vec<_>>();
        let entities = self
            .entities
            .iter()
            .map(CatalogEntity::projection)
            .collect::<Vec<_>>();
        let encoded = nlu_data::canonical_json(
            &json!({
                "areas": areas,
                "devices": devices,
                "entities": entities,
                "floors": floors,
                "generation": self.generation,
                "version": CATALOG_VERSION,
            }),
            "runtime catalog",
        )
        .map_err(|_| RuntimeError::CatalogFrame)?;
        if encoded.is_empty() || encoded.len() > MAX_CATALOG_FRAME_BYTES {
            return Err(RuntimeError::CatalogFrame);
        }
        Ok(encoded)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > MAX_CATALOG_FRAME_BYTES {
            return Err(RuntimeError::CatalogFrame);
        }
        let value = nlu_data::parse_strict_json(bytes, "runtime catalog")
            .map_err(|_| RuntimeError::CatalogFrame)?;
        let object = exact_object(
            &value,
            &[
                "areas",
                "devices",
                "entities",
                "floors",
                "generation",
                "version",
            ],
        )?;
        if object.get("version").and_then(Value::as_u64) != Some(u64::from(CATALOG_VERSION)) {
            return Err(RuntimeError::CatalogFrame);
        }
        let generation = object
            .get("generation")
            .and_then(Value::as_u64)
            .ok_or(RuntimeError::CatalogFrame)?;
        let floors = catalog_array(object, "floors", ha_catalog::snapshot::MAX_FLOORS)?;
        let areas = catalog_array(object, "areas", ha_catalog::snapshot::MAX_AREAS)?;
        let devices = catalog_array(object, "devices", ha_catalog::snapshot::MAX_DEVICES)?;
        let entities = catalog_array(object, "entities", MAX_CATALOG_ENTITIES)?;
        if entities.len() > MAX_CATALOG_ENTITIES {
            return Err(RuntimeError::CatalogFrame);
        }
        let floors = floors
            .iter()
            .map(decode_floor)
            .collect::<Result<Vec<_>>>()?;
        let areas = areas.iter().map(decode_area).collect::<Result<Vec<_>>>()?;
        let devices = devices
            .iter()
            .map(decode_device)
            .collect::<Result<Vec<_>>>()?;
        let entities = entities
            .iter()
            .map(decode_entity)
            .collect::<Result<Vec<_>>>()?;
        Self::with_topology(generation, floors, areas, devices, entities)
    }

    pub fn into_snapshot(self) -> Result<CatalogSnapshot> {
        let Self {
            generation,
            floors,
            areas,
            devices,
            entities,
        } = self;
        let generation =
            CatalogGeneration::new(generation).map_err(|_| RuntimeError::CatalogSnapshot)?;
        let mut descriptor_domains = BTreeMap::<Box<str>, BTreeSet<Domain>>::new();
        for entity in &entities {
            let external = ExternalEntityId::new(&entity.external_id)
                .map_err(|_| RuntimeError::CatalogSnapshot)?;
            for capability in &entity.capabilities {
                let domains = descriptor_domains.entry(capability.clone()).or_default();
                if capability.as_ref() == "ha:state_query" {
                    domains.insert(external.domain().clone());
                }
            }
        }
        let descriptors = descriptor_domains
            .into_iter()
            .map(|(capability, domains)| {
                Ok(CapabilityDescriptorInput {
                    id: CapabilityId::new(&capability)
                        .map_err(|_| RuntimeError::CatalogSnapshot)?,
                    enabled: true,
                    state_query_domains: domains.into_iter().collect(),
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let entities = entities
            .into_iter()
            .map(|entity| entity.into_input(generation))
            .collect::<Result<Vec<_>>>()?;
        CatalogSnapshot::build(CatalogSnapshotInput {
            generation,
            floors: floors
                .into_iter()
                .map(CatalogFloor::into_input)
                .collect::<Result<Vec<_>>>()?,
            areas: areas
                .into_iter()
                .map(CatalogArea::into_input)
                .collect::<Result<Vec<_>>>()?,
            devices: devices
                .into_iter()
                .map(CatalogDevice::into_input)
                .collect::<Result<Vec<_>>>()?,
            capability_descriptors: descriptors,
            entities,
        })
        .map_err(|_| RuntimeError::CatalogSnapshot)
    }

    fn validate_external_ids(&self) -> Result<()> {
        let mut external_ids = BTreeSet::new();
        if self
            .entities
            .iter()
            .any(|entity| !external_ids.insert(entity.external_id.as_ref()))
        {
            return Err(RuntimeError::CatalogSnapshot);
        }
        Ok(())
    }
}

fn registry_array(snapshot: &SupervisorSnapshot, command: SupervisorCommand) -> Result<&[Value]> {
    snapshot
        .get(command)
        .and_then(Value::as_array)
        .ok_or(RuntimeError::CatalogSnapshot)
        .map(Vec::as_slice)
}

fn state_names(states: &[Value]) -> Result<BTreeMap<Box<str>, Option<Box<str>>>> {
    if states.len() > MAX_CATALOG_ENTITIES {
        return Err(RuntimeError::CatalogSnapshot);
    }
    let mut names = BTreeMap::new();
    for state in states {
        let object = state.as_object().ok_or(RuntimeError::CatalogSnapshot)?;
        let entity_id = required_catalog_string(object, "entity_id")?;
        ExternalEntityId::new(entity_id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        let friendly_name = match object.get("attributes") {
            Some(Value::Object(attributes)) => {
                optional_catalog_string(attributes, "friendly_name")?
                    .map(str::to_owned)
                    .map(String::into_boxed_str)
            }
            _ => return Err(RuntimeError::CatalogSnapshot),
        };
        if names.insert(entity_id.into(), friendly_name).is_some() {
            return Err(RuntimeError::CatalogSnapshot);
        }
    }
    Ok(names)
}

fn exposed_entities(value: &Value) -> Result<BTreeSet<Box<str>>> {
    let object = exact_object(value, &["exposed_entities"])?;
    let exposed = object
        .get("exposed_entities")
        .and_then(Value::as_object)
        .ok_or(RuntimeError::CatalogSnapshot)?;
    if exposed.len() > MAX_CATALOG_ENTITIES {
        return Err(RuntimeError::CatalogSnapshot);
    }
    let mut result = BTreeSet::new();
    for (entity_id, assistants) in exposed {
        ExternalEntityId::new(entity_id).map_err(|_| RuntimeError::CatalogSnapshot)?;
        let assistants = assistants
            .as_object()
            .ok_or(RuntimeError::CatalogSnapshot)?;
        if assistants.get("conversation").and_then(Value::as_bool) == Some(true) {
            result.insert(entity_id.clone().into_boxed_str());
        }
    }
    Ok(result)
}

fn index_registry_values<'a>(
    values: &'a [Value],
    id_field: &str,
) -> Result<BTreeMap<&'a str, &'a Map<String, Value>>> {
    let mut indexed = BTreeMap::new();
    for value in values {
        let object = value.as_object().ok_or(RuntimeError::CatalogSnapshot)?;
        let id = required_catalog_string(object, id_field)?;
        if indexed.insert(id, object).is_some() {
            return Err(RuntimeError::CatalogSnapshot);
        }
    }
    Ok(indexed)
}

fn select_area(
    area_id: &str,
    area_objects: &BTreeMap<&str, &Map<String, Value>>,
    floor_objects: &BTreeMap<&str, &Map<String, Value>>,
    selected_areas: &mut BTreeMap<Box<str>, CatalogArea>,
    selected_floors: &mut BTreeMap<Box<str>, CatalogFloor>,
) -> Result<Option<String>> {
    let area_object = area_objects
        .get(area_id)
        .ok_or(RuntimeError::CatalogSnapshot)?;
    let floor_id = optional_catalog_string(area_object, "floor_id")?;
    let area = CatalogArea::new(
        area_id,
        required_catalog_string(area_object, "name")?.to_owned(),
        optional_catalog_strings(area_object, "aliases")?,
        floor_id,
    )?;
    selected_areas.entry(area_id.into()).or_insert(area);
    if let Some(id) = floor_id {
        let floor_object = floor_objects.get(id).ok_or(RuntimeError::CatalogSnapshot)?;
        let floor = CatalogFloor::new(
            id,
            required_catalog_string(floor_object, "name")?.to_owned(),
            optional_catalog_strings(floor_object, "aliases")?,
        )?;
        selected_floors.entry(id.into()).or_insert(floor);
    }
    Ok(floor_id.map(str::to_owned))
}

fn has_service(services: &Map<String, Value>, domain: &str, service: &str) -> bool {
    services
        .get(domain)
        .and_then(Value::as_object)
        .is_some_and(|domain_services| domain_services.contains_key(service))
}

fn content_generation(
    floors: &[CatalogFloor],
    areas: &[CatalogArea],
    devices: &[CatalogDevice],
    entities: &[CatalogEntity],
) -> Result<u64> {
    let has_topology = !floors.is_empty()
        || !areas.is_empty()
        || !devices.is_empty()
        || entities.iter().any(CatalogEntity::has_associations);
    let value = if has_topology {
        json!({
            "areas": areas.iter().map(CatalogArea::projection).collect::<Vec<_>>(),
            "devices": devices.iter().map(CatalogDevice::projection).collect::<Vec<_>>(),
            "entities": entities.iter().map(CatalogEntity::projection).collect::<Vec<_>>(),
            "floors": floors.iter().map(CatalogFloor::projection).collect::<Vec<_>>(),
        })
    } else {
        json!({
            "entities": entities
                .iter()
                .map(CatalogEntity::legacy_projection)
                .collect::<Vec<_>>()
        })
    };
    let encoded = nlu_data::canonical_json(&value, "live catalog")
        .map_err(|_| RuntimeError::CatalogSnapshot)?;
    let digest = nlu_data::sha256_hex(&encoded).map_err(|_| RuntimeError::CatalogSnapshot)?;
    let prefix = digest.get(..16).ok_or(RuntimeError::CatalogSnapshot)?;
    let mut generation = u64::from_str_radix(prefix, 16)
        .map_err(|_| RuntimeError::CatalogSnapshot)?
        & i64::MAX as u64;
    if generation == 0 {
        generation = 1;
    }
    Ok(generation)
}

fn required_catalog_string<'a>(object: &'a Map<String, Value>, field: &str) -> Result<&'a str> {
    object
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or(RuntimeError::CatalogSnapshot)
}

fn optional_catalog_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a str>> {
    match object.get(field) {
        Some(Value::Null) | None => Ok(None),
        Some(Value::String(value)) if !value.is_empty() => Ok(Some(value)),
        _ => Err(RuntimeError::CatalogSnapshot),
    }
}

fn optional_catalog_strings(object: &Map<String, Value>, field: &str) -> Result<Vec<String>> {
    match object.get(field) {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::Array(values)) if values.len() <= MAX_ENTITY_ALIASES => values
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .filter(|text| !text.is_empty())
                    .map(str::to_owned)
                    .ok_or(RuntimeError::CatalogSnapshot)
            })
            .collect(),
        _ => Err(RuntimeError::CatalogSnapshot),
    }
}

fn required_catalog_strings(object: &Map<String, Value>, field: &str) -> Result<Vec<String>> {
    if !matches!(object.get(field), Some(Value::Array(_))) {
        return Err(RuntimeError::CatalogSnapshot);
    }
    optional_catalog_strings(object, field)
}

impl core::fmt::Debug for CatalogSeed {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CatalogSeed")
            .field("generation", &self.generation)
            .field("floor_count", &self.floors.len())
            .field("area_count", &self.areas.len())
            .field("device_count", &self.devices.len())
            .field("entity_count", &self.entities.len())
            .field("residential_data", &"redacted")
            .finish()
    }
}

pub fn read_catalog_seed(reader: &mut impl Read) -> Result<CatalogSeed> {
    let mut prefix = [0_u8; 4];
    reader
        .read_exact(&mut prefix)
        .map_err(|_| RuntimeError::CatalogFrame)?;
    let length =
        usize::try_from(u32::from_be_bytes(prefix)).map_err(|_| RuntimeError::CatalogFrame)?;
    if length == 0 || length > MAX_CATALOG_FRAME_BYTES {
        return Err(RuntimeError::CatalogFrame);
    }
    let mut bytes = vec![0_u8; length];
    if reader.read_exact(&mut bytes).is_err() {
        bytes.fill(0);
        return Err(RuntimeError::CatalogFrame);
    }
    let result = CatalogSeed::decode(&bytes);
    bytes.fill(0);
    result
}

pub fn write_catalog_seed(writer: &mut impl Write, seed: &CatalogSeed) -> Result<()> {
    let bytes = seed.encode()?;
    let length = u32::try_from(bytes.len()).map_err(|_| RuntimeError::CatalogFrame)?;
    writer
        .write_all(&length.to_be_bytes())
        .and_then(|()| writer.write_all(&bytes))
        .and_then(|()| writer.flush())
        .map_err(|_| RuntimeError::CatalogFrame)
}

fn decode_entity(value: &Value) -> Result<CatalogEntity> {
    let object = exact_object(
        value,
        &[
            "aliases",
            "area_id",
            "capabilities",
            "device_id",
            "display_name",
            "external_id",
            "floor_id",
            "registry_id",
        ],
    )?;
    let registry_id = string(object, "registry_id")?;
    let external_id = string(object, "external_id")?;
    let display_name = string(object, "display_name")?.to_owned();
    let aliases = string_array(object, "aliases", MAX_ENTITY_ALIASES)?;
    let capabilities = string_array(object, "capabilities", ALLOWED_CAPABILITIES.len())?;
    CatalogEntity::with_associations(
        registry_id,
        external_id,
        display_name,
        aliases,
        capabilities,
        nullable_string(object, "area_id")?,
        nullable_string(object, "floor_id")?,
        nullable_string(object, "device_id")?,
    )
}

fn decode_floor(value: &Value) -> Result<CatalogFloor> {
    let object = exact_object(value, &["aliases", "floor_id", "name"])?;
    CatalogFloor::new(
        string(object, "floor_id")?,
        string(object, "name")?.to_owned(),
        string_array(object, "aliases", MAX_ENTITY_ALIASES)?,
    )
}

fn decode_area(value: &Value) -> Result<CatalogArea> {
    let object = exact_object(value, &["aliases", "area_id", "floor_id", "name"])?;
    CatalogArea::new(
        string(object, "area_id")?,
        string(object, "name")?.to_owned(),
        string_array(object, "aliases", MAX_ENTITY_ALIASES)?,
        nullable_string(object, "floor_id")?,
    )
}

fn decode_device(value: &Value) -> Result<CatalogDevice> {
    let object = exact_object(value, &["area_id", "device_id", "name"])?;
    CatalogDevice::new(
        string(object, "device_id")?,
        string(object, "name")?.to_owned(),
        nullable_string(object, "area_id")?,
    )
}

fn exact_object<'a>(value: &'a Value, fields: &[&str]) -> Result<&'a Map<String, Value>> {
    let object = value.as_object().ok_or(RuntimeError::CatalogFrame)?;
    if object.len() != fields.len() || object.keys().any(|field| !fields.contains(&field.as_str()))
    {
        return Err(RuntimeError::CatalogFrame);
    }
    Ok(object)
}

fn string<'a>(object: &'a Map<String, Value>, field: &str) -> Result<&'a str> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or(RuntimeError::CatalogFrame)
}

fn nullable_string<'a>(object: &'a Map<String, Value>, field: &str) -> Result<Option<&'a str>> {
    match object.get(field) {
        Some(Value::Null) => Ok(None),
        Some(Value::String(value)) if !value.is_empty() => Ok(Some(value)),
        _ => Err(RuntimeError::CatalogFrame),
    }
}

fn catalog_array<'a>(
    object: &'a Map<String, Value>,
    field: &str,
    maximum: usize,
) -> Result<&'a [Value]> {
    object
        .get(field)
        .and_then(Value::as_array)
        .filter(|values| values.len() <= maximum)
        .map(Vec::as_slice)
        .ok_or(RuntimeError::CatalogFrame)
}

fn string_array(object: &Map<String, Value>, field: &str, maximum: usize) -> Result<Vec<String>> {
    let values = object
        .get(field)
        .and_then(Value::as_array)
        .ok_or(RuntimeError::CatalogFrame)?;
    if values.len() > maximum {
        return Err(RuntimeError::CatalogFrame);
    }
    values
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or(RuntimeError::CatalogFrame)
        })
        .collect()
}

fn canonical_strings(
    mut values: Vec<String>,
    maximum: usize,
    require_nonempty: bool,
) -> Result<Vec<Box<str>>> {
    if values.len() > maximum || (require_nonempty && values.is_empty()) {
        return Err(RuntimeError::CatalogSnapshot);
    }
    values.sort();
    if values.windows(2).any(|pair| pair[0] == pair[1]) {
        return Err(RuntimeError::CatalogSnapshot);
    }
    values
        .into_iter()
        .map(|value| {
            if value.is_empty() {
                Err(RuntimeError::CatalogSnapshot)
            } else {
                Ok(value.into_boxed_str())
            }
        })
        .collect()
}

fn explicit_aliases(
    aliases: Vec<Box<str>>,
    provenance: AliasProvenance,
) -> Result<Vec<ExplicitAlias>> {
    aliases
        .into_iter()
        .map(|alias| {
            SensitiveText::new(String::from(alias))
                .map(|text| ExplicitAlias::new(text, provenance))
                .map_err(|_| RuntimeError::CatalogSnapshot)
        })
        .collect()
}

fn duplicate_by<T>(values: &[T], key: impl Fn(&T) -> &str) -> bool {
    values.windows(2).any(|pair| key(&pair[0]) == key(&pair[1]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entity(id: u8) -> CatalogEntity {
        CatalogEntity::new(
            &format!("{id:032x}"),
            &format!("switch.fixture_tecnica_{id}"),
            format!("FIXTURE_TECNICA_DISPLAY_{id}"),
            vec![format!("FIXTURE_TECNICA_ALIAS_{id}")],
            vec!["ha:state_query".to_owned(), "ha:switch_control".to_owned()],
        )
        .expect("FIXTURE_TECNICA entity")
    }

    #[test]
    fn startup_catalog_round_trips_and_builds_one_generation() {
        let seed =
            CatalogSeed::new(7, vec![entity(2), entity(1)]).expect("FIXTURE_TECNICA catalog");
        let mut frame = Vec::new();
        write_catalog_seed(&mut frame, &seed).expect("FIXTURE_TECNICA write");
        let decoded = read_catalog_seed(&mut frame.as_slice()).expect("FIXTURE_TECNICA read");

        assert_eq!(decoded.generation(), 7);
        assert_eq!(decoded.entities()[0].registry_id(), format!("{:032x}", 1));
        let snapshot = decoded.into_snapshot().expect("FIXTURE_TECNICA snapshot");
        assert_eq!(snapshot.generation().get(), 7);
        assert_eq!(snapshot.entities().len(), 2);
        assert!(!format!("{seed:?}").contains("FIXTURE_TECNICA_DISPLAY"));
    }

    #[test]
    fn startup_catalog_is_closed_bounded_and_unique() {
        let duplicate = CatalogSeed::new(1, vec![entity(1), entity(1)])
            .expect_err("duplicate registry identity");
        assert_eq!(duplicate, RuntimeError::CatalogSnapshot);

        let open = br#"{"entities":[],"generation":1,"private":"FIXTURE_TECNICA","version":1}"#;
        assert_eq!(
            CatalogSeed::decode(open).expect_err("open field"),
            RuntimeError::CatalogFrame
        );
        let mut oversized = vec![0_u8; 4];
        oversized.copy_from_slice(&((MAX_CATALOG_FRAME_BYTES as u32) + 1).to_be_bytes());
        assert_eq!(
            read_catalog_seed(&mut oversized.as_slice()).expect_err("oversized"),
            RuntimeError::CatalogFrame
        );
    }
}
