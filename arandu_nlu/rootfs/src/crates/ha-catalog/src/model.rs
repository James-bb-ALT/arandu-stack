use std::collections::BTreeSet;

use core::fmt;
use lang_ptbr::NormalizedText;
use nlu_core::{CapabilityId, CatalogGeneration, EntityId, EntityRef};

use crate::{CatalogError, LimitKind};

pub const MAX_SENSITIVE_TEXT_BYTES: usize = 1_024;
pub const MAX_DOMAIN_BYTES: usize = 64;
pub const MAX_LOCATION_ID_BYTES: usize = 128;

fn valid_registry_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

macro_rules! hex_registry_identifier {
    ($name:ident, $error:ident) => {
        #[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name(Box<str>);

        impl $name {
            pub fn new(value: &str) -> Result<Self, CatalogError> {
                if !valid_registry_id(value) {
                    return Err(CatalogError::$error);
                }
                Ok(Self(value.into()))
            }

            #[must_use]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<&str> for $name {
            type Error = CatalogError;

            fn try_from(value: &str) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct(stringify!($name))
                    .field("bytes", &self.0.len())
                    .finish()
            }
        }
    };
}

hex_registry_identifier!(RegistryEntryId, InvalidRegistryEntryId);
hex_registry_identifier!(DeviceId, InvalidDeviceId);

fn valid_location_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() <= MAX_LOCATION_ID_BYTES
        && bytes
            .first()
            .is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        && bytes
            .last()
            .is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        && bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_' || *byte == b'-'
        })
}

macro_rules! location_identifier {
    ($name:ident, $error:ident) => {
        #[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name(Box<str>);

        impl $name {
            pub fn new(value: &str) -> Result<Self, CatalogError> {
                if !valid_location_id(value) {
                    return Err(CatalogError::$error);
                }
                Ok(Self(value.into()))
            }

            #[must_use]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<&str> for $name {
            type Error = CatalogError;

            fn try_from(value: &str) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct(stringify!($name))
                    .field("bytes", &self.0.len())
                    .finish()
            }
        }
    };
}

location_identifier!(AreaId, InvalidAreaId);
location_identifier!(FloorId, InvalidFloorId);

impl RegistryEntryId {
    pub fn to_core_entity_id(&self) -> Result<EntityId, CatalogError> {
        EntityId::new(&format!("ha_entity:id_{}", self.as_str()))
            .map_err(|_| CatalogError::InvalidCoreEntityId)
    }
}

#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Domain(Box<str>);

impl Domain {
    pub fn new(value: &str) -> Result<Self, CatalogError> {
        let bytes = value.as_bytes();
        let last = bytes.last().copied();
        if bytes.is_empty()
            || bytes.len() > MAX_DOMAIN_BYTES
            || !bytes[0].is_ascii_lowercase()
            || !last.is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
            || !bytes
                .iter()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
        {
            return Err(CatalogError::InvalidDomain);
        }
        Ok(Self(value.into()))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<&str> for Domain {
    type Error = CatalogError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl fmt::Debug for Domain {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Domain")
            .field("bytes", &self.0.len())
            .finish()
    }
}

#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ExternalEntityId {
    value: Box<str>,
    domain: Domain,
}

impl ExternalEntityId {
    pub fn new(value: &str) -> Result<Self, CatalogError> {
        if value.len() > MAX_SENSITIVE_TEXT_BYTES {
            return Err(CatalogError::LimitExceeded {
                kind: LimitKind::SensitiveTextBytes,
                limit: MAX_SENSITIVE_TEXT_BYTES as u32,
            });
        }
        let (domain, object_id) = value
            .split_once('.')
            .ok_or(CatalogError::InvalidExternalEntityId)?;
        if object_id.is_empty()
            || object_id.contains('.')
            || !object_id
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
            || !object_id
                .as_bytes()
                .first()
                .is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
            || !object_id
                .as_bytes()
                .last()
                .is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        {
            return Err(CatalogError::InvalidExternalEntityId);
        }
        let domain = Domain::new(domain).map_err(|_| CatalogError::InvalidExternalEntityId)?;
        Ok(Self {
            value: value.into(),
            domain,
        })
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.value
    }

    #[must_use]
    pub const fn domain(&self) -> &Domain {
        &self.domain
    }
}

impl TryFrom<&str> for ExternalEntityId {
    type Error = CatalogError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl fmt::Debug for ExternalEntityId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExternalEntityId")
            .field("bytes", &self.value.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct SensitiveText {
    original: Box<str>,
    nfc: Box<str>,
}

impl SensitiveText {
    pub fn new(value: impl Into<String>) -> Result<Self, CatalogError> {
        let value = value.into();
        if value.is_empty() || value.chars().all(char::is_whitespace) {
            return Err(CatalogError::InvalidSensitiveText);
        }
        if value.len() > MAX_SENSITIVE_TEXT_BYTES {
            return Err(CatalogError::LimitExceeded {
                kind: LimitKind::SensitiveTextBytes,
                limit: MAX_SENSITIVE_TEXT_BYTES as u32,
            });
        }
        let normalized =
            NormalizedText::new(value.clone()).map_err(|_| CatalogError::InvalidSensitiveText)?;
        if normalized.is_empty() || normalized.len() > MAX_SENSITIVE_TEXT_BYTES {
            return Err(CatalogError::InvalidSensitiveText);
        }
        Ok(Self {
            original: value.into(),
            nfc: normalized.as_str().into(),
        })
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.original
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.original.as_bytes()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.original.len()
    }

    #[must_use]
    pub const fn is_empty(&self) -> bool {
        false
    }

    #[must_use]
    pub fn exact_nfc_eq(&self, other: &SensitiveText) -> bool {
        self.nfc == other.nfc
    }

    pub(crate) fn nfc_key(&self) -> &str {
        &self.nfc
    }
}

impl fmt::Debug for SensitiveText {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SensitiveText")
            .field("original_bytes", &self.original.len())
            .field("nfc_bytes", &self.nfc.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AliasProvenance {
    EntityRegistry,
    AreaRegistry,
    FloorRegistry,
}

#[derive(Clone, Eq, PartialEq)]
pub struct ExplicitAlias {
    text: SensitiveText,
    provenance: AliasProvenance,
}

impl ExplicitAlias {
    #[must_use]
    pub const fn new(text: SensitiveText, provenance: AliasProvenance) -> Self {
        Self { text, provenance }
    }

    #[must_use]
    pub const fn text(&self) -> &SensitiveText {
        &self.text
    }

    #[must_use]
    pub const fn provenance(&self) -> AliasProvenance {
        self.provenance
    }
}

impl fmt::Debug for ExplicitAlias {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExplicitAlias")
            .field("text", &self.text)
            .field("provenance", &self.provenance)
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EntityVisibility {
    conversation_exposed: bool,
    enabled: bool,
    hidden: bool,
}

impl EntityVisibility {
    #[must_use]
    pub const fn new(conversation_exposed: bool, enabled: bool, hidden: bool) -> Self {
        Self {
            conversation_exposed,
            enabled,
            hidden,
        }
    }

    #[must_use]
    pub const fn exposed() -> Self {
        Self::new(true, true, false)
    }

    #[must_use]
    pub const fn conversation_exposed(self) -> bool {
        self.conversation_exposed
    }

    #[must_use]
    pub const fn enabled(self) -> bool {
        self.enabled
    }

    #[must_use]
    pub const fn hidden(self) -> bool {
        self.hidden
    }

    #[must_use]
    pub const fn is_catalog_visible(self) -> bool {
        self.conversation_exposed && self.enabled
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct FloorInput {
    pub id: FloorId,
    pub name: SensitiveText,
    pub aliases: Vec<ExplicitAlias>,
}

impl fmt::Debug for FloorInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FloorInput")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("alias_count", &self.aliases.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct AreaInput {
    pub id: AreaId,
    pub name: SensitiveText,
    pub aliases: Vec<ExplicitAlias>,
    pub floor_id: Option<FloorId>,
}

impl fmt::Debug for AreaInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AreaInput")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("alias_count", &self.aliases.len())
            .field("has_floor", &self.floor_id.is_some())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct DeviceInput {
    pub id: DeviceId,
    pub name: SensitiveText,
    pub area_id: Option<AreaId>,
}

impl fmt::Debug for DeviceInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DeviceInput")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("has_area", &self.area_id.is_some())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct CapabilityDescriptorInput {
    pub id: CapabilityId,
    pub enabled: bool,
    pub state_query_domains: Vec<Domain>,
}

impl fmt::Debug for CapabilityDescriptorInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CapabilityDescriptorInput")
            .field("id", &self.id)
            .field("enabled", &self.enabled)
            .field("state_query_domain_count", &self.state_query_domains.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct EntityInputParts {
    pub generation: CatalogGeneration,
    pub registry_id: RegistryEntryId,
    pub external_id: ExternalEntityId,
    pub domain: Domain,
    pub display_name: SensitiveText,
    pub aliases: Vec<ExplicitAlias>,
    pub capabilities: Vec<CapabilityId>,
    pub area_id: Option<AreaId>,
    pub floor_id: Option<FloorId>,
    pub device_id: Option<DeviceId>,
    pub visibility: EntityVisibility,
}

impl fmt::Debug for EntityInputParts {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EntityInputParts")
            .field("generation", &self.generation)
            .field("registry_id", &self.registry_id)
            .field("external_id", &self.external_id)
            .field("domain", &self.domain)
            .field("display_name", &self.display_name)
            .field("alias_count", &self.aliases.len())
            .field("capability_count", &self.capabilities.len())
            .field("has_area", &self.area_id.is_some())
            .field("has_floor", &self.floor_id.is_some())
            .field("has_device", &self.device_id.is_some())
            .field("visibility", &self.visibility)
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct EntityInput(EntityInputParts);

impl EntityInput {
    #[must_use]
    pub const fn new(parts: EntityInputParts) -> Self {
        Self(parts)
    }

    #[must_use]
    pub const fn parts(&self) -> &EntityInputParts {
        &self.0
    }

    pub(crate) fn into_parts(self) -> EntityInputParts {
        self.0
    }
}

impl fmt::Debug for EntityInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct FloorRecord {
    pub(crate) id: FloorId,
    pub(crate) name: SensitiveText,
    pub(crate) aliases: Box<[ExplicitAlias]>,
}

impl FloorRecord {
    #[must_use]
    pub const fn id(&self) -> &FloorId {
        &self.id
    }

    #[must_use]
    pub const fn name(&self) -> &SensitiveText {
        &self.name
    }

    #[must_use]
    pub fn aliases(&self) -> &[ExplicitAlias] {
        &self.aliases
    }
}

impl fmt::Debug for FloorRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FloorRecord")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("alias_count", &self.aliases.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct AreaRecord {
    pub(crate) id: AreaId,
    pub(crate) name: SensitiveText,
    pub(crate) aliases: Box<[ExplicitAlias]>,
    pub(crate) floor_id: Option<FloorId>,
}

impl AreaRecord {
    #[must_use]
    pub const fn id(&self) -> &AreaId {
        &self.id
    }

    #[must_use]
    pub const fn name(&self) -> &SensitiveText {
        &self.name
    }

    #[must_use]
    pub fn aliases(&self) -> &[ExplicitAlias] {
        &self.aliases
    }

    #[must_use]
    pub const fn floor_id(&self) -> Option<&FloorId> {
        self.floor_id.as_ref()
    }
}

impl fmt::Debug for AreaRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AreaRecord")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("alias_count", &self.aliases.len())
            .field("has_floor", &self.floor_id.is_some())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct DeviceRecord {
    pub(crate) id: DeviceId,
    pub(crate) name: SensitiveText,
    pub(crate) area_id: Option<AreaId>,
}

impl DeviceRecord {
    #[must_use]
    pub const fn id(&self) -> &DeviceId {
        &self.id
    }

    #[must_use]
    pub const fn name(&self) -> &SensitiveText {
        &self.name
    }

    #[must_use]
    pub const fn area_id(&self) -> Option<&AreaId> {
        self.area_id.as_ref()
    }
}

impl fmt::Debug for DeviceRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DeviceRecord")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("has_area", &self.area_id.is_some())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct CapabilityDescriptor {
    pub(crate) id: CapabilityId,
    pub(crate) enabled: bool,
    pub(crate) state_query_domains: BTreeSet<Domain>,
}

impl CapabilityDescriptor {
    #[must_use]
    pub const fn id(&self) -> &CapabilityId {
        &self.id
    }

    #[must_use]
    pub const fn enabled(&self) -> bool {
        self.enabled
    }

    #[must_use]
    pub fn state_query_domains(&self) -> &BTreeSet<Domain> {
        &self.state_query_domains
    }

    #[must_use]
    pub fn state_query_disposition(&self, domain: &Domain) -> StateQueryDisposition {
        if !self.enabled {
            StateQueryDisposition::AbstainDescriptorDisabled
        } else if !self.state_query_domains.contains(domain) {
            StateQueryDisposition::AbstainDomainNotReviewed
        } else {
            StateQueryDisposition::Supported
        }
    }
}

impl fmt::Debug for CapabilityDescriptor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CapabilityDescriptor")
            .field("id", &self.id)
            .field("enabled", &self.enabled)
            .field("state_query_domain_count", &self.state_query_domains.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StateQueryDisposition {
    Supported,
    AbstainCapabilityNotAssociated,
    AbstainDescriptorDisabled,
    AbstainDomainNotReviewed,
}

#[derive(Clone, Eq, PartialEq)]
pub struct EntityRecord {
    pub(crate) generation: CatalogGeneration,
    pub(crate) registry_id: RegistryEntryId,
    pub(crate) core_id: EntityId,
    pub(crate) external_id: ExternalEntityId,
    pub(crate) domain: Domain,
    pub(crate) display_name: SensitiveText,
    pub(crate) aliases: Box<[ExplicitAlias]>,
    pub(crate) capabilities: BTreeSet<CapabilityId>,
    pub(crate) area_id: Option<AreaId>,
    pub(crate) floor_id: Option<FloorId>,
    pub(crate) device_id: Option<DeviceId>,
}

impl EntityRecord {
    #[must_use]
    pub const fn generation(&self) -> CatalogGeneration {
        self.generation
    }

    #[must_use]
    pub const fn registry_id(&self) -> &RegistryEntryId {
        &self.registry_id
    }

    #[must_use]
    pub const fn core_id(&self) -> &EntityId {
        &self.core_id
    }

    #[must_use]
    pub fn entity_ref(&self) -> EntityRef {
        EntityRef::new(self.core_id.clone(), self.generation)
    }

    #[must_use]
    pub const fn external_id(&self) -> &ExternalEntityId {
        &self.external_id
    }

    #[must_use]
    pub const fn domain(&self) -> &Domain {
        &self.domain
    }

    #[must_use]
    pub const fn display_name(&self) -> &SensitiveText {
        &self.display_name
    }

    #[must_use]
    pub fn aliases(&self) -> &[ExplicitAlias] {
        &self.aliases
    }

    #[must_use]
    pub fn capabilities(&self) -> &BTreeSet<CapabilityId> {
        &self.capabilities
    }

    #[must_use]
    pub const fn area_id(&self) -> Option<&AreaId> {
        self.area_id.as_ref()
    }

    #[must_use]
    pub const fn floor_id(&self) -> Option<&FloorId> {
        self.floor_id.as_ref()
    }

    #[must_use]
    pub const fn device_id(&self) -> Option<&DeviceId> {
        self.device_id.as_ref()
    }
}

impl fmt::Debug for EntityRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EntityRecord")
            .field("generation", &self.generation)
            .field("registry_id", &self.registry_id)
            .field("core_id", &self.core_id)
            .field("external_id", &self.external_id)
            .field("domain", &self.domain)
            .field("display_name", &self.display_name)
            .field("alias_count", &self.aliases.len())
            .field("capability_count", &self.capabilities.len())
            .field("has_area", &self.area_id.is_some())
            .field("has_floor", &self.floor_id.is_some())
            .field("has_device", &self.device_id.is_some())
            .finish()
    }
}
