use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u8 = 1;
pub const MAX_TEXT_BYTES: usize = 2_048;
pub const MAX_TEXT_CHARS: usize = 512;
pub const MAX_AREAS: usize = 256;
pub const MAX_ENTITIES: usize = 1_024;
pub const MAX_NAMES: usize = 8;
pub const MAX_NAME_BYTES: usize = 128;
pub const MAX_IDENTIFIER_BYTES: usize = 128;
pub const MAX_ENTITY_ID_BYTES: usize = 255;
pub const MAX_ACTIONS_PER_ENTITY: usize = 4;
pub const MAX_OPERATIONS: usize = 4;
pub const MAX_TARGET_CLAUSES: usize = 4;
pub const MAX_MENTIONS_PER_SEGMENT: usize = 4;
pub const MAX_TOTAL_MENTIONS: usize = 16;
pub const MAX_TARGETS_PER_OPERATION: usize = 32;
pub const MAX_TOTAL_NAME_BYTES: usize = 65_536;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    GetState,
    SetFanPercentage,
    TurnOff,
    TurnOn,
}

impl Action {
    pub(crate) fn supports_domain(self, domain: &str) -> bool {
        match self {
            Self::GetState => matches!(
                domain,
                "binary_sensor" | "fan" | "light" | "sensor" | "switch"
            ),
            Self::SetFanPercentage => domain == "fan",
            Self::TurnOff | Self::TurnOn => {
                matches!(domain, "fan" | "light" | "switch")
            }
        }
    }

    pub(crate) fn is_effect(self) -> bool {
        self != Self::GetState
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Area {
    pub area_id: String,
    pub names: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogEntity {
    pub actions: Vec<Action>,
    pub area_id: Option<String>,
    pub domain: String,
    pub entity_id: String,
    pub names: Vec<String>,
    pub registry_id: String,
    pub is_primary: Option<bool>,
}

impl CatalogEntity {
    pub(crate) fn supports(&self, action: Action) -> bool {
        self.actions.contains(&action)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub areas: Vec<Area>,
    pub entities: Vec<CatalogEntity>,
    pub version: u8,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct InterpretRequest {
    pub catalog: Catalog,
    pub text: String,
    pub version: u8,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub action: Action,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub percentage: Option<u8>,
    pub targets: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum InterpretResponse {
    Ambiguous {
        version: u8,
    },
    InvalidRequest {
        version: u8,
    },
    NoMatch {
        version: u8,
    },
    Plan {
        operations: Vec<Operation>,
        version: u8,
    },
}

impl InterpretResponse {
    pub(crate) const fn ambiguous() -> Self {
        Self::Ambiguous {
            version: PROTOCOL_VERSION,
        }
    }

    pub(crate) const fn invalid_request() -> Self {
        Self::InvalidRequest {
            version: PROTOCOL_VERSION,
        }
    }

    pub(crate) const fn no_match() -> Self {
        Self::NoMatch {
            version: PROTOCOL_VERSION,
        }
    }

    pub(crate) fn plan(operations: Vec<Operation>) -> Self {
        Self::Plan {
            operations,
            version: PROTOCOL_VERSION,
        }
    }
}

pub(crate) fn validate_request(request: &mut InterpretRequest) -> bool {
    if request.version != PROTOCOL_VERSION
        || request.catalog.version != PROTOCOL_VERSION
        || request.text.is_empty()
        || request.text.len() > MAX_TEXT_BYTES
        || request.text.chars().count() > MAX_TEXT_CHARS
        || request.text.chars().any(char::is_control)
        || request.catalog.areas.len() > MAX_AREAS
        || request.catalog.entities.len() > MAX_ENTITIES
    {
        return false;
    }

    let mut total_name_bytes = 0_usize;
    let mut area_ids = BTreeSet::new();
    for area in &request.catalog.areas {
        if !valid_identifier(&area.area_id)
            || !area_ids.insert(area.area_id.as_str())
            || !valid_names(&area.names, &mut total_name_bytes)
        {
            return false;
        }
    }

    let mut registry_ids = BTreeSet::new();
    let mut entity_ids = BTreeSet::new();
    for entity in &mut request.catalog.entities {
        if !valid_identifier(&entity.registry_id)
            || entity.entity_id.is_empty()
            || entity.entity_id.len() > MAX_ENTITY_ID_BYTES
            || entity.entity_id.chars().any(char::is_control)
            || !entity.entity_id.starts_with(&format!("{}.", entity.domain))
            || !matches!(
                entity.domain.as_str(),
                "binary_sensor" | "fan" | "light" | "sensor" | "switch"
            )
            || !registry_ids.insert(entity.registry_id.as_str())
            || !entity_ids.insert(entity.entity_id.as_str())
            || !valid_names(&entity.names, &mut total_name_bytes)
            || entity.actions.is_empty()
            || entity.actions.len() > MAX_ACTIONS_PER_ENTITY
        {
            return false;
        }
        entity.actions.sort_unstable();
        entity.actions.dedup();
        if entity.actions.is_empty()
            || entity
                .actions
                .iter()
                .any(|action| !action.supports_domain(&entity.domain))
            || entity
                .area_id
                .as_deref()
                .is_some_and(|area_id| !area_ids.contains(area_id))
        {
            return false;
        }
    }

    total_name_bytes <= MAX_TOTAL_NAME_BYTES
}

pub(crate) fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_IDENTIFIER_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn valid_names(names: &[String], total_name_bytes: &mut usize) -> bool {
    if names.is_empty() || names.len() > MAX_NAMES {
        return false;
    }
    let mut unique = BTreeSet::new();
    for name in names {
        if name.is_empty()
            || name.len() > MAX_NAME_BYTES
            || name.chars().any(char::is_control)
            || !unique.insert(name)
        {
            return false;
        }
        *total_name_bytes = match total_name_bytes.checked_add(name.len()) {
            Some(total) => total,
            None => return false,
        };
    }
    true
}
