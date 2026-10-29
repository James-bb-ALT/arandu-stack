use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{
    Action, Operation, ResolutionCatalog,
    model::{MAX_OPERATIONS, MAX_TEXT_BYTES, MAX_TEXT_CHARS},
    normalize::{normalize, strip_article, strip_preposition},
    resolution::{
        ResolutionConstraints, ResolutionOutcome, ResolutionRequest, index_snapshot, resolve_entity,
    },
};

pub const PROTOCOL_VERSION_V3: u8 = 3;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct InterpretRequestV3 {
    pub text: String,
    #[serde(alias = "catalog")]
    pub snapshot: ResolutionCatalog,
    pub generation: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MusicAction {
    Next,
    Pause,
    Play,
    Previous,
    Resume,
    SetVolume,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MusicPlan {
    pub action: MusicAction,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_query: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub player_area: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum InterpretResponseV3 {
    AmbiguousTarget {
        version: u8,
    },
    InvalidRequest {
        version: u8,
    },
    MissingSlot {
        missing_slot: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        action: Option<Action>,
        #[serde(skip_serializing_if = "Option::is_none")]
        domain: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        media_query: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        provider: Option<String>,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        candidates: Vec<String>,
        version: u8,
    },
    Plan {
        #[serde(skip_serializing_if = "Vec::is_empty")]
        operations: Vec<Operation>,
        #[serde(skip_serializing_if = "Option::is_none")]
        music: Option<MusicPlan>,
        version: u8,
    },
    UnresolvedTarget {
        version: u8,
    },
    UnsupportedIntent {
        version: u8,
    },
}

#[must_use]
pub fn interpret_v3(request: &InterpretRequestV3) -> InterpretResponseV3 {
    if request.text.is_empty()
        || request.text.len() > MAX_TEXT_BYTES
        || request.text.chars().count() > MAX_TEXT_CHARS
        || request.text.chars().any(char::is_control)
    {
        return invalid();
    }
    if request.generation != request.snapshot.generation
        || index_snapshot(&request.snapshot).is_none()
    {
        return invalid();
    }
    let text = normalize(&request.text);
    if text.is_empty() {
        return invalid();
    }
    if let Some(music) = interpret_music(&text, &request.snapshot) {
        return music;
    }
    if !starts_device(&text) {
        return InterpretResponseV3::UnsupportedIntent {
            version: PROTOCOL_VERSION_V3,
        };
    }
    interpret_devices(&text, request)
}

fn interpret_devices(text: &str, request: &InterpretRequestV3) -> InterpretResponseV3 {
    let clauses = split_device_clauses(text);
    if clauses.is_empty() || clauses.len() > MAX_OPERATIONS {
        return unsupported();
    }
    let mut operations = Vec::with_capacity(clauses.len());
    let mut affected = BTreeSet::new();
    let mut current_action = None;
    for clause in clauses {
        let Some((action, target)) = device_clause_parts(clause, current_action) else {
            return unsupported();
        };
        current_action = Some(action);
        let (domain, mention, area_name) = domain_mention(target);
        let resolved = resolve_device(
            &request.text,
            &request.snapshot,
            &request.generation,
            action,
            domain,
            mention,
            area_name,
        );
        match resolved {
            DeviceResolution::Resolved(targets) => {
                for target in &targets {
                    if !affected.insert(target.clone()) {
                        return unsupported();
                    }
                }
                operations.push(Operation {
                    action,
                    percentage: None,
                    targets,
                });
            }
            DeviceResolution::Ambiguous(candidates) => {
                return InterpretResponseV3::MissingSlot {
                    missing_slot: "target".to_owned(),
                    action: Some(action),
                    domain: Some(domain.to_owned()),
                    media_query: None,
                    provider: None,
                    candidates,
                    version: PROTOCOL_VERSION_V3,
                };
            }
            DeviceResolution::MissingTarget => {
                return InterpretResponseV3::MissingSlot {
                    missing_slot: "target".to_owned(),
                    action: Some(action),
                    domain: Some(domain.to_owned()),
                    media_query: None,
                    provider: None,
                    candidates: Vec::new(),
                    version: PROTOCOL_VERSION_V3,
                };
            }
        }
    }
    InterpretResponseV3::Plan {
        operations,
        music: None,
        version: PROTOCOL_VERSION_V3,
    }
}

fn interpret_music(text: &str, catalog: &ResolutionCatalog) -> Option<InterpretResponseV3> {
    let action = if text.starts_with("pausa ") {
        MusicAction::Pause
    } else if text.starts_with("continua ") {
        MusicAction::Resume
    } else if text.starts_with("proxima ") {
        MusicAction::Next
    } else if text.starts_with("volta ") {
        MusicAction::Previous
    } else if text.starts_with("toca ") || text.starts_with("toque ") {
        MusicAction::Play
    } else {
        return None;
    };
    if action != MusicAction::Play {
        return Some(InterpretResponseV3::MissingSlot {
            missing_slot: "player".to_owned(),
            action: None,
            domain: None,
            media_query: None,
            provider: None,
            candidates: Vec::new(),
            version: PROTOCOL_VERSION_V3,
        });
    }
    let mut rest = text
        .strip_prefix("toca ")
        .or_else(|| text.strip_prefix("toque "))
        .unwrap_or(text)
        .trim();
    rest = rest.strip_prefix("o ").unwrap_or(rest);
    let mut media_type = None;
    if let Some(after) = rest.strip_prefix("album ") {
        media_type = Some("album".to_owned());
        rest = after;
    }
    let (without_area, player_area) = peel_area(rest, catalog);
    let (query, provider) = peel_provider(without_area);
    if provider.is_some() && query.is_empty() {
        return Some(InterpretResponseV3::MissingSlot {
            missing_slot: "media_query".to_owned(),
            action: None,
            domain: None,
            media_query: None,
            provider,
            candidates: Vec::new(),
            version: PROTOCOL_VERSION_V3,
        });
    }
    if query.is_empty() || query == "musica" {
        return Some(InterpretResponseV3::MissingSlot {
            missing_slot: "media_query".to_owned(),
            action: None,
            domain: None,
            media_query: None,
            provider,
            candidates: Vec::new(),
            version: PROTOCOL_VERSION_V3,
        });
    }
    Some(InterpretResponseV3::Plan {
        operations: Vec::new(),
        music: Some(MusicPlan {
            action,
            media_query: Some(title_case_query(query)),
            media_type,
            provider,
            player_area,
        }),
        version: PROTOCOL_VERSION_V3,
    })
}

fn split_device_clauses(text: &str) -> Vec<&str> {
    let mut result = Vec::new();
    let mut start = 0;
    for (idx, _) in text.match_indices(" e ") {
        let next = idx + 3;
        let next_text = &text[next..];
        if starts_device(next_text) || starts_domain_noun(next_text) {
            result.push(text[start..idx].trim());
            start = next;
        }
    }
    result.push(text[start..].trim());
    result
}

fn starts_device(text: &str) -> bool {
    [
        "desliga ",
        "desligue ",
        "apaga ",
        "apague ",
        "liga ",
        "ligue ",
        "acenda ",
        "acende ",
    ]
    .iter()
    .any(|prefix| text.starts_with(prefix))
}

fn starts_domain_noun(text: &str) -> bool {
    let stripped = strip_article(text);
    ["luz ", "ventilador ", "abajur ", "cafeteira "]
        .iter()
        .any(|prefix| stripped.starts_with(prefix))
}

fn device_clause_parts(clause: &str, inherited: Option<Action>) -> Option<(Action, &str)> {
    if let Some((verb, rest)) = clause.split_once(' ') {
        let action = match verb {
            "desliga" | "desligue" | "apaga" | "apague" => Some(Action::TurnOff),
            "liga" | "ligue" | "acenda" | "acende" => Some(Action::TurnOn),
            _ => None,
        };
        if let Some(action) = action {
            return Some((action, rest));
        }
    }
    inherited.map(|action| (action, clause))
}

fn domain_mention(target: &str) -> (&'static str, &str, Option<&str>) {
    let target = strip_article(target);
    for (prefix, domain, mention) in [
        ("luz ", "light", "luz"),
        ("ventilador ", "fan", "ventilador"),
        ("abajur", "light", "abajur"),
        ("cafeteira", "switch", "cafeteira"),
    ] {
        if target == prefix.trim() {
            return (domain, mention, None);
        }
        if let Some(rest) = target.strip_prefix(prefix) {
            let area = strip_preposition(rest);
            return (domain, mention, area);
        }
    }
    ("switch", target, None)
}

enum DeviceResolution {
    Resolved(Vec<String>),
    Ambiguous(Vec<String>),
    MissingTarget,
}

fn resolve_device(
    text: &str,
    catalog: &ResolutionCatalog,
    generation: &str,
    action: Action,
    domain: &str,
    mention: &str,
    area_name: Option<&str>,
) -> DeviceResolution {
    let area_id = area_name.and_then(|name| area_id(catalog, name));
    if area_name.is_some() && area_id.is_none() {
        return DeviceResolution::MissingTarget;
    }
    let constraints = ResolutionConstraints {
        area_id,
        domain: Some(domain.to_owned()),
        capability: Some(action),
    };
    match resolve_entity(&ResolutionRequest {
        text: text.to_owned(),
        catalog: catalog.clone(),
        generation: generation.to_owned(),
        mention: mention.to_owned(),
        span: None,
        constraints,
    }) {
        ResolutionOutcome::Resolved { registry_id, .. } => {
            DeviceResolution::Resolved(vec![registry_id])
        }
        ResolutionOutcome::Ambiguous { candidates } => DeviceResolution::Ambiguous(candidates),
        ResolutionOutcome::NoMatch => DeviceResolution::MissingTarget,
    }
}

fn area_id(catalog: &ResolutionCatalog, name: &str) -> Option<String> {
    let normalized = normalize(name);
    let matches: BTreeSet<_> = catalog
        .areas
        .iter()
        .filter(|area| {
            area.names
                .iter()
                .any(|candidate| normalize(candidate) == normalized)
        })
        .map(|area| area.area_id.clone())
        .collect();
    (matches.len() == 1).then(|| matches.into_iter().next().unwrap_or_default())
}

fn peel_area<'a>(value: &'a str, catalog: &ResolutionCatalog) -> (&'a str, Option<String>) {
    for marker in [" na ", " no "] {
        if let Some(index) = value.rfind(marker) {
            let candidate = &value[index + marker.len()..];
            if let Some(area) = area_id(catalog, candidate) {
                return (value[..index].trim(), Some(area));
            }
        }
    }
    (value, None)
}

fn peel_provider(value: &str) -> (&str, Option<String>) {
    for (marker, provider) in [
        (" no spotify", "spotify"),
        (" na spotify", "spotify"),
        (" no deezer", "deezer"),
        (" na deezer", "deezer"),
    ] {
        if let Some(query) = value.strip_suffix(marker) {
            return (query.trim(), Some(provider.to_owned()));
        }
    }
    if value == "spotify" || value == "deezer" {
        return ("", Some(value.to_owned()));
    }
    (value.trim(), None)
}

fn title_case_query(value: &str) -> String {
    value
        .split_whitespace()
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().chain(chars).collect::<String>(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

const fn invalid() -> InterpretResponseV3 {
    InterpretResponseV3::InvalidRequest {
        version: PROTOCOL_VERSION_V3,
    }
}

const fn unsupported() -> InterpretResponseV3 {
    InterpretResponseV3::UnsupportedIntent {
        version: PROTOCOL_VERSION_V3,
    }
}
