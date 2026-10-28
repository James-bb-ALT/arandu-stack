//! Exact-evidence entity resolution for one mention against one catalog snapshot.
//!
//! ADR-0051 preparation contract, implemented as an additive module. Protocol v1
//! (`interpret`) and the Home Assistant integration are unchanged: this resolver
//! never executes services, never invents candidates, and never selects a winner
//! by catalog order.
//!
//! Precedence applies after the already contracted Unicode normalization
//! (`normalize`): exact external dotted `entity_id` first, then exact explicit
//! registry alias, then exact display name plus at least one independent
//! constraint. An exact higher-rank match suppresses lower-rank candidates.
//! Equal-rank candidates remain a tie and report `ambiguous`. A display-name tie
//! without any constraint also abstains as `ambiguous`; a single display-name
//! match without a constraint reports `no_match` because display names alone
//! never resolve. Every other failure (unknown, contradictory, stale,
//! malformed, invalid span, duplicate identity) reports `no_match` at the
//! resolution boundary.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{
    Action,
    model::{
        MAX_ACTIONS_PER_ENTITY, MAX_AREAS, MAX_ENTITIES, MAX_ENTITY_ID_BYTES, MAX_IDENTIFIER_BYTES,
        MAX_NAME_BYTES, MAX_NAMES, MAX_TEXT_BYTES, MAX_TEXT_CHARS, valid_identifier,
    },
    normalize::normalize,
};

pub const MAX_GENERATION_BYTES: usize = 64;
pub const MAX_CATALOG_ID_BYTES: usize = 128;
pub const MAX_ALIASES_PER_ENTITY: usize = MAX_NAMES;

const KNOWN_DOMAINS: &[&str] = &["binary_sensor", "fan", "light", "sensor", "switch"];

fn valid_domain(value: &str) -> bool {
    KNOWN_DOMAINS.contains(&value)
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResolutionArea {
    pub area_id: String,
    pub names: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResolutionEntity {
    pub registry_id: String,
    pub entity_id: String,
    pub domain: String,
    #[serde(default)]
    pub area_id: Option<String>,
    pub display_name: String,
    pub aliases: Vec<String>,
    pub capabilities: Vec<Action>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResolutionCatalog {
    pub catalog_id: String,
    pub generation: String,
    pub areas: Vec<ResolutionArea>,
    pub entities: Vec<ResolutionEntity>,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResolutionConstraints {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub area_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub domain: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capability: Option<Action>,
}

impl ResolutionConstraints {
    fn has_independent_constraint(&self) -> bool {
        self.area_id.is_some() || self.domain.is_some() || self.capability.is_some()
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResolutionRequest {
    pub text: String,
    pub catalog: ResolutionCatalog,
    pub generation: String,
    pub mention: String,
    #[serde(default)]
    pub span: Option<[usize; 2]>,
    #[serde(default)]
    pub constraints: ResolutionConstraints,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ResolutionEvidence {
    ExternalEntityId,
    ExplicitRegistryAlias,
    DisplayNameWithConstraint,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum ResolutionOutcome {
    Resolved {
        registry_id: String,
        evidence: ResolutionEvidence,
    },
    Ambiguous {
        candidates: Vec<String>,
    },
    NoMatch,
}

struct IndexedEntity<'a> {
    entity: &'a ResolutionEntity,
    normalized_id: String,
    normalized_aliases: Vec<String>,
    normalized_display: String,
}

pub(crate) struct IndexedArea {
    pub(crate) area_id: String,
    pub(crate) normalized_names: Vec<String>,
}

pub(crate) struct Snapshot<'a> {
    area_ids: BTreeSet<&'a str>,
    rows: Vec<IndexedEntity<'a>>,
    pub(crate) areas: Vec<IndexedArea>,
}
impl Snapshot<'_> {
    /// Word-boundary occurrences of dotted external IDs inside a mention.
    /// These ranges are rigid designator spans: area scanning skips them,
    /// while the remainder of the mention is scanned normally. Explicit
    /// aliases are natural language and keep area scanning.
    pub(crate) fn rigid_spans(&self, mention: &str) -> Vec<(usize, usize)> {
        let mut spans = Vec::new();
        for row in &self.rows {
            let mut search = 0_usize;
            while search + row.normalized_id.len() <= mention.len() {
                match mention[search..].find(row.normalized_id.as_str()) {
                    Some(found) => {
                        let absolute = search + found;
                        let end = absolute + row.normalized_id.len();
                        let before = absolute == 0 || mention.as_bytes()[absolute - 1] == b' ';
                        let after = end == mention.len() || mention.as_bytes()[end] == b' ';
                        if before && after {
                            spans.push((absolute, end));
                        }
                        search = absolute + 1;
                    }
                    None => break,
                }
            }
        }
        spans
    }
}
#[must_use]
pub fn resolve_entity(request: &ResolutionRequest) -> ResolutionOutcome {
    if !valid_request_shape(request) {
        return ResolutionOutcome::NoMatch;
    }
    if request.generation != request.catalog.generation {
        return ResolutionOutcome::NoMatch;
    }
    if let Some([start, end]) = request.span
        && !valid_span(&request.text, &request.mention, start, end)
    {
        return ResolutionOutcome::NoMatch;
    }
    let Some(snapshot) = index_snapshot(&request.catalog) else {
        return ResolutionOutcome::NoMatch;
    };
    if !valid_constraints(&request.constraints, &snapshot) {
        return ResolutionOutcome::NoMatch;
    }
    let normalized_mention = normalize(&request.mention);
    if normalized_mention.is_empty() {
        return ResolutionOutcome::NoMatch;
    }

    let mut external = BTreeSet::new();
    let mut aliases = BTreeSet::new();
    let mut displays = BTreeSet::new();
    for row in &snapshot.rows {
        if row.normalized_id == normalized_mention {
            external.insert(row.entity.registry_id.as_str());
        }
        if row
            .normalized_aliases
            .iter()
            .any(|alias| alias == &normalized_mention)
        {
            aliases.insert(row.entity.registry_id.as_str());
        }
        if row.normalized_display == normalized_mention {
            displays.insert(row.entity.registry_id.as_str());
        }
    }

    if !external.is_empty() {
        return decide(
            filter_candidates(&snapshot, &external, &request.constraints),
            ResolutionEvidence::ExternalEntityId,
        );
    }
    if !aliases.is_empty() {
        return decide(
            filter_candidates(&snapshot, &aliases, &request.constraints),
            ResolutionEvidence::ExplicitRegistryAlias,
        );
    }
    if displays.is_empty() {
        return ResolutionOutcome::NoMatch;
    }
    if !request.constraints.has_independent_constraint() {
        return match displays.len() {
            1 => ResolutionOutcome::NoMatch,
            _ => ResolutionOutcome::Ambiguous {
                candidates: displays.into_iter().map(str::to_owned).collect(),
            },
        };
    }
    decide(
        filter_candidates(&snapshot, &displays, &request.constraints),
        ResolutionEvidence::DisplayNameWithConstraint,
    )
}

fn decide(matches: BTreeSet<&str>, evidence: ResolutionEvidence) -> ResolutionOutcome {
    match matches.len() {
        0 => ResolutionOutcome::NoMatch,
        1 => ResolutionOutcome::Resolved {
            registry_id: matches.into_iter().next().unwrap_or_default().to_owned(),
            evidence,
        },
        _ => ResolutionOutcome::Ambiguous {
            candidates: matches.into_iter().map(str::to_owned).collect(),
        },
    }
}

fn filter_candidates<'a>(
    snapshot: &'a Snapshot<'a>,
    candidates: &BTreeSet<&str>,
    constraints: &ResolutionConstraints,
) -> BTreeSet<&'a str> {
    snapshot
        .rows
        .iter()
        .filter(|row| candidates.contains(row.entity.registry_id.as_str()))
        .filter(|row| {
            constraints
                .area_id
                .as_deref()
                .is_none_or(|area| row.entity.area_id.as_deref() == Some(area))
        })
        .filter(|row| {
            constraints
                .domain
                .as_deref()
                .is_none_or(|domain| row.entity.domain == domain)
        })
        .filter(|row| {
            constraints
                .capability
                .is_none_or(|capability| row.entity.capabilities.contains(&capability))
        })
        .map(|row| row.entity.registry_id.as_str())
        .collect()
}

fn valid_request_shape(request: &ResolutionRequest) -> bool {
    if request.text.is_empty()
        || request.text.len() > MAX_TEXT_BYTES
        || request.text.chars().count() > MAX_TEXT_CHARS
        || request.text.chars().any(char::is_control)
        || request.mention.is_empty()
        || request.mention.len() > MAX_ENTITY_ID_BYTES
        || request.mention.chars().any(char::is_control)
        || !valid_identifier(&request.generation)
        || request.generation.len() > MAX_GENERATION_BYTES
        || !valid_identifier(&request.catalog.catalog_id)
        || request.catalog.catalog_id.len() > MAX_CATALOG_ID_BYTES
        || !valid_identifier(&request.catalog.generation)
        || request.catalog.generation.len() > MAX_GENERATION_BYTES
    {
        return false;
    }
    if let Some(area_id) = request.constraints.area_id.as_deref()
        && (!valid_identifier(area_id) || area_id.len() > MAX_IDENTIFIER_BYTES)
    {
        return false;
    }
    if let Some(domain) = request.constraints.domain.as_deref()
        && (domain.len() > MAX_IDENTIFIER_BYTES || domain.chars().any(char::is_control))
    {
        return false;
    }
    true
}

fn valid_span(text: &str, mention: &str, start: usize, end: usize) -> bool {
    if start >= end || end > text.len() {
        return false;
    }
    match text.get(start..end) {
        Some(slice) => slice == mention,
        None => false,
    }
}

fn valid_constraints(constraints: &ResolutionConstraints, snapshot: &Snapshot<'_>) -> bool {
    if let Some(area_id) = constraints.area_id.as_deref()
        && !snapshot.area_ids.contains(area_id)
    {
        return false;
    }
    if let Some(domain) = constraints.domain.as_deref()
        && !valid_domain(domain)
    {
        return false;
    }
    true
}

pub(crate) fn index_snapshot(catalog: &ResolutionCatalog) -> Option<Snapshot<'_>> {
    if catalog.areas.len() > MAX_AREAS || catalog.entities.len() > MAX_ENTITIES {
        return None;
    }
    let mut area_ids = BTreeSet::new();
    let mut areas = Vec::with_capacity(catalog.areas.len());
    for area in &catalog.areas {
        if !valid_identifier(&area.area_id)
            || !area_ids.insert(area.area_id.as_str())
            || !valid_name_list(&area.names)
        {
            return None;
        }
        areas.push(IndexedArea {
            area_id: area.area_id.clone(),
            normalized_names: area.names.iter().map(|name| normalize(name)).collect(),
        });
    }
    let mut registry_ids = BTreeSet::new();
    let mut entity_ids = BTreeSet::new();
    let mut rows = Vec::with_capacity(catalog.entities.len());
    for entity in &catalog.entities {
        if !valid_identifier(&entity.registry_id)
            || !registry_ids.insert(entity.registry_id.as_str())
            || entity.entity_id.is_empty()
            || entity.entity_id.len() > MAX_ENTITY_ID_BYTES
            || entity.entity_id.chars().any(char::is_control)
            || !entity_ids.insert(entity.entity_id.as_str())
            || !valid_domain(&entity.domain)
            || entity
                .area_id
                .as_deref()
                .is_some_and(|area| !area_ids.contains(area))
            || entity.display_name.is_empty()
            || entity.display_name.len() > MAX_NAME_BYTES
            || entity.display_name.chars().any(char::is_control)
            || entity.aliases.len() > MAX_ALIASES_PER_ENTITY
            || entity.capabilities.is_empty()
            || entity.capabilities.len() > MAX_ACTIONS_PER_ENTITY
            || entity
                .capabilities
                .iter()
                .any(|capability| !capability.supports_domain(&entity.domain))
        {
            return None;
        }
        let mut seen_aliases = BTreeSet::new();
        for alias in &entity.aliases {
            if alias.is_empty()
                || alias.len() > MAX_NAME_BYTES
                || alias.chars().any(char::is_control)
                || !seen_aliases.insert(alias)
            {
                return None;
            }
        }
        let normalized_display = normalize(&entity.display_name);
        let normalized_aliases: Vec<String> = entity
            .aliases
            .iter()
            .map(|alias| normalize(alias))
            .collect();
        if normalized_display.is_empty()
            || normalized_aliases.iter().any(String::is_empty)
            || normalize(&entity.entity_id).is_empty()
        {
            return None;
        }
        rows.push(IndexedEntity {
            entity,
            normalized_id: normalize(&entity.entity_id),
            normalized_aliases,
            normalized_display,
        });
    }
    Some(Snapshot {
        area_ids,
        rows,
        areas,
    })
}

fn valid_name_list(names: &[String]) -> bool {
    if names.is_empty() || names.len() > MAX_NAMES {
        return false;
    }
    let mut unique = BTreeSet::new();
    for name in names {
        if name.is_empty()
            || name.len() > MAX_NAME_BYTES
            || name.chars().any(char::is_control)
            || normalize(name).is_empty()
            || !unique.insert(name)
        {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn area(area_id: &str, names: &[&str]) -> ResolutionArea {
        ResolutionArea {
            area_id: area_id.to_owned(),
            names: names.iter().map(|name| (*name).to_owned()).collect(),
        }
    }

    fn entity(
        registry_id: &str,
        entity_id: &str,
        domain: &str,
        area_id: Option<&str>,
        display_name: &str,
        aliases: &[&str],
        capabilities: &[Action],
    ) -> ResolutionEntity {
        ResolutionEntity {
            registry_id: registry_id.to_owned(),
            entity_id: entity_id.to_owned(),
            domain: domain.to_owned(),
            area_id: area_id.map(str::to_owned),
            display_name: display_name.to_owned(),
            aliases: aliases.iter().map(|alias| (*alias).to_owned()).collect(),
            capabilities: capabilities.to_vec(),
        }
    }

    fn lamp(
        registry_id: &str,
        area_id: &str,
        display_name: &str,
        aliases: &[&str],
    ) -> ResolutionEntity {
        entity(
            registry_id,
            &format!("light.{registry_id}"),
            "light",
            Some(area_id),
            display_name,
            aliases,
            &[Action::TurnOn, Action::TurnOff],
        )
    }

    fn catalog(areas: Vec<ResolutionArea>, entities: Vec<ResolutionEntity>) -> ResolutionCatalog {
        ResolutionCatalog {
            catalog_id: "test-catalog".to_owned(),
            generation: "gen-001".to_owned(),
            areas,
            entities,
        }
    }

    fn sala_catalog() -> ResolutionCatalog {
        catalog(
            vec![
                area("area_sala", &["sala"]),
                area("area_quarto", &["quarto"]),
            ],
            vec![
                lamp("a_main", "area_sala", "luz da sala", &["luz principal"]),
                lamp("a_lamp", "area_sala", "abajur", &["abajur da sala"]),
                lamp("b_lamp", "area_quarto", "abajur", &["abajur do quarto"]),
            ],
        )
    }

    fn request(
        catalog: ResolutionCatalog,
        mention: &str,
        constraints: ResolutionConstraints,
        span: Option<[usize; 2]>,
    ) -> ResolutionRequest {
        ResolutionRequest {
            text: format!("Acenda {mention}."),
            catalog,
            generation: "gen-001".to_owned(),
            mention: mention.to_owned(),
            span,
            constraints,
        }
    }

    fn plain(catalog: ResolutionCatalog, mention: &str) -> ResolutionRequest {
        request(catalog, mention, ResolutionConstraints::default(), None)
    }

    fn resolved(registry_id: &str, evidence: ResolutionEvidence) -> ResolutionOutcome {
        ResolutionOutcome::Resolved {
            registry_id: registry_id.to_owned(),
            evidence,
        }
    }

    fn area_constraint(area_id: &str) -> ResolutionConstraints {
        ResolutionConstraints {
            area_id: Some(area_id.to_owned()),
            ..ResolutionConstraints::default()
        }
    }

    #[test]
    fn external_id_beats_alias_and_display() {
        let catalog = catalog(
            vec![],
            vec![
                entity(
                    "winner",
                    "light.sala",
                    "light",
                    None,
                    "luz",
                    &["luz da sala"],
                    &[Action::TurnOn],
                ),
                entity(
                    "loser",
                    "light.other",
                    "light",
                    None,
                    "light sala",
                    &["light sala"],
                    &[Action::TurnOn],
                ),
            ],
        );
        assert_eq!(
            resolve_entity(&plain(catalog, "light.sala")),
            resolved("winner", ResolutionEvidence::ExternalEntityId)
        );
    }

    #[test]
    fn alias_beats_display() {
        let catalog = catalog(
            vec![],
            vec![
                entity(
                    "alias_owner",
                    "light.one",
                    "light",
                    None,
                    "luminaria",
                    &["abajur da sala"],
                    &[Action::TurnOn],
                ),
                entity(
                    "display_owner",
                    "light.two",
                    "light",
                    None,
                    "abajur da sala",
                    &["outro nome"],
                    &[Action::TurnOn],
                ),
            ],
        );
        assert_eq!(
            resolve_entity(&plain(catalog, "abajur da sala")),
            resolved("alias_owner", ResolutionEvidence::ExplicitRegistryAlias)
        );
    }

    #[test]
    fn display_with_area_domain_and_capability_constraints_resolve() {
        let constraints = area_constraint("area_sala");
        assert_eq!(
            resolve_entity(&request(sala_catalog(), "abajur", constraints, None)),
            resolved("a_lamp", ResolutionEvidence::DisplayNameWithConstraint)
        );
        let domain = ResolutionConstraints {
            domain: Some("fan".to_owned()),
            ..ResolutionConstraints::default()
        };
        let single = catalog(
            vec![],
            vec![entity(
                "only",
                "fan.only",
                "fan",
                None,
                "ventilador",
                &["vento"],
                &[Action::TurnOn],
            )],
        );
        assert_eq!(
            resolve_entity(&request(single, "ventilador", domain, None)),
            resolved("only", ResolutionEvidence::DisplayNameWithConstraint)
        );
        let capability = ResolutionConstraints {
            capability: Some(Action::SetFanPercentage),
            ..ResolutionConstraints::default()
        };
        let fan = catalog(
            vec![],
            vec![entity(
                "fan",
                "fan.sala",
                "fan",
                None,
                "ventilador",
                &["vento"],
                &[Action::TurnOn, Action::SetFanPercentage],
            )],
        );
        assert_eq!(
            resolve_entity(&request(fan, "ventilador", capability, None)),
            resolved("fan", ResolutionEvidence::DisplayNameWithConstraint)
        );
    }

    #[test]
    fn display_without_constraint_never_resolves_single_match() {
        let single = catalog(
            vec![],
            vec![entity(
                "only",
                "switch.only",
                "switch",
                None,
                "cafeteira",
                &["tomada"],
                &[Action::TurnOn],
            )],
        );
        assert_eq!(
            resolve_entity(&plain(single, "cafeteira")),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn display_tie_abstains_with_and_without_constraints() {
        assert_eq!(
            resolve_entity(&plain(sala_catalog(), "abajur")),
            ResolutionOutcome::Ambiguous {
                candidates: vec!["a_lamp".to_owned(), "b_lamp".to_owned()],
            }
        );
        let domain = ResolutionConstraints {
            domain: Some("light".to_owned()),
            ..ResolutionConstraints::default()
        };
        assert_eq!(
            resolve_entity(&request(sala_catalog(), "abajur", domain, None)),
            ResolutionOutcome::Ambiguous {
                candidates: vec!["a_lamp".to_owned(), "b_lamp".to_owned()],
            }
        );
    }

    #[test]
    fn unknown_mention_is_no_match() {
        assert_eq!(
            resolve_entity(&plain(sala_catalog(), "projetor")),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn contradictory_constraints_are_no_match() {
        let conflict = request(
            sala_catalog(),
            "abajur da sala",
            area_constraint("area_quarto"),
            None,
        );
        assert_eq!(resolve_entity(&conflict), ResolutionOutcome::NoMatch);
        let domain = ResolutionConstraints {
            area_id: Some("area_sala".to_owned()),
            domain: Some("switch".to_owned()),
            ..ResolutionConstraints::default()
        };
        assert_eq!(
            resolve_entity(&request(sala_catalog(), "luz da sala", domain, None)),
            ResolutionOutcome::NoMatch
        );
        let capability = ResolutionConstraints {
            capability: Some(Action::SetFanPercentage),
            ..ResolutionConstraints::default()
        };
        assert_eq!(
            resolve_entity(&request(sala_catalog(), "abajur", capability, None)),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn stale_or_empty_generation_is_no_match() {
        let mut stale = plain(sala_catalog(), "abajur da sala");
        stale.generation = "gen-000".to_owned();
        assert_eq!(resolve_entity(&stale), ResolutionOutcome::NoMatch);
        let mut empty = plain(sala_catalog(), "abajur da sala");
        empty.generation.clear();
        assert_eq!(resolve_entity(&empty), ResolutionOutcome::NoMatch);
    }

    #[test]
    fn empty_or_evidence_free_mention_is_no_match() {
        assert_eq!(
            resolve_entity(&plain(sala_catalog(), "")),
            ResolutionOutcome::NoMatch
        );
        assert_eq!(
            resolve_entity(&plain(sala_catalog(), "...")),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn duplicate_catalog_identities_are_no_match() {
        let mut registry_dup = sala_catalog();
        registry_dup.entities.push(entity(
            "a_lamp",
            "light.distinct",
            "light",
            Some("area_sala"),
            "outra",
            &["outro alias"],
            &[Action::TurnOn],
        ));
        assert_eq!(
            resolve_entity(&plain(registry_dup, "outra")),
            ResolutionOutcome::NoMatch
        );
        let mut dotted_dup = sala_catalog();
        dotted_dup.entities.push(entity(
            "clone",
            "light.a_lamp",
            "light",
            Some("area_sala"),
            "outra",
            &["outro alias"],
            &[Action::TurnOn],
        ));
        assert_eq!(
            resolve_entity(&plain(dotted_dup, "outra")),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn unknown_area_and_domain_references_are_no_match() {
        let mut bad_area = sala_catalog();
        bad_area.entities[0].area_id = Some("area_missing".to_owned());
        assert_eq!(
            resolve_entity(&plain(bad_area, "luz principal")),
            ResolutionOutcome::NoMatch
        );
        assert_eq!(
            resolve_entity(&request(
                sala_catalog(),
                "abajur",
                area_constraint("area_missing"),
                None
            )),
            ResolutionOutcome::NoMatch
        );
        let domain = ResolutionConstraints {
            domain: Some("fan".to_owned()),
            ..ResolutionConstraints::default()
        };
        assert_eq!(
            resolve_entity(&request(sala_catalog(), "abajur", domain, None)),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn invalid_spans_are_no_match_and_valid_spans_resolve() {
        let text = "Acenda o abajur da sala.";
        let build = |span| ResolutionRequest {
            text: text.to_owned(),
            catalog: sala_catalog(),
            generation: "gen-001".to_owned(),
            mention: "abajur da sala".to_owned(),
            span,
            constraints: ResolutionConstraints::default(),
        };
        assert_eq!(
            resolve_entity(&build(Some([15, 5]))),
            ResolutionOutcome::NoMatch
        );
        assert_eq!(
            resolve_entity(&build(Some([0, 999]))),
            ResolutionOutcome::NoMatch
        );
        assert_eq!(
            resolve_entity(&build(Some([8, 15]))),
            ResolutionOutcome::NoMatch
        );
        assert_eq!(
            resolve_entity(&build(Some([9, 23]))),
            resolved("a_lamp", ResolutionEvidence::ExplicitRegistryAlias)
        );

        let unicode_text = "Acenda a cafeteira ☕.";
        let split_inside_emoji = unicode_text.len() - 2;
        let unicode = ResolutionRequest {
            text: unicode_text.to_owned(),
            catalog: catalog(
                vec![area("area_cozinha", &["cozinha"])],
                vec![lamp("coffee", "area_cozinha", "cafeteira ☕", &["pass"])],
            ),
            generation: "gen-001".to_owned(),
            mention: "cafeteira ☕".to_owned(),
            span: Some([9, unicode_text.len() - 1]),
            constraints: area_constraint("area_cozinha"),
        };
        assert_eq!(
            resolve_entity(&unicode),
            resolved("coffee", ResolutionEvidence::DisplayNameWithConstraint)
        );
        let split = ResolutionRequest {
            span: Some([9, split_inside_emoji]),
            ..unicode.clone()
        };
        assert_eq!(resolve_entity(&split), ResolutionOutcome::NoMatch);
    }

    #[test]
    fn catalog_permutation_preserves_outcomes() {
        let forward_resolved = resolve_entity(&plain(sala_catalog(), "abajur da sala"));
        let forward_ambiguous = resolve_entity(&plain(sala_catalog(), "abajur"));
        let mut reversed = sala_catalog();
        reversed.areas.reverse();
        reversed.entities.reverse();
        for entity in &mut reversed.entities {
            entity.aliases.reverse();
        }
        assert_eq!(
            resolve_entity(&plain(reversed.clone(), "abajur da sala")),
            forward_resolved
        );
        assert_eq!(
            resolve_entity(&plain(reversed, "abajur")),
            forward_ambiguous
        );
    }

    #[test]
    fn snapshot_capability_domain_mismatch_is_no_match() {
        let bad = catalog(
            vec![],
            vec![entity(
                "sensor",
                "sensor.temp",
                "sensor",
                None,
                "temperatura",
                &["temp"],
                &[Action::TurnOn],
            )],
        );
        assert_eq!(
            resolve_entity(&plain(bad, "temp")),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn bounds_and_control_characters_are_no_match() {
        let mut oversized = plain(sala_catalog(), "abajur da sala");
        oversized.text = "a".repeat(MAX_TEXT_BYTES + 1);
        assert_eq!(resolve_entity(&oversized), ResolutionOutcome::NoMatch);
        let mut long_mention = plain(sala_catalog(), "abajur da sala");
        long_mention.text = format!("Acenda {}.", "a".repeat(MAX_ENTITY_ID_BYTES + 1));
        long_mention.mention = "a".repeat(MAX_ENTITY_ID_BYTES + 1);
        assert_eq!(resolve_entity(&long_mention), ResolutionOutcome::NoMatch);
        let mut control = plain(sala_catalog(), "abajur da sala");
        control.text = "Acenda\ta sala.".to_owned();
        assert_eq!(resolve_entity(&control), ResolutionOutcome::NoMatch);
        let mut many = sala_catalog();
        many.entities.extend((0..MAX_ENTITIES).map(|index| {
            lamp(
                &format!("extra_{index}"),
                "area_sala",
                &format!("luminaria {index}"),
                &[&format!("alias {index}")],
            )
        }));
        assert_eq!(
            resolve_entity(&plain(many, "abajur da sala")),
            ResolutionOutcome::NoMatch
        );
        let mut areas = sala_catalog();
        areas
            .areas
            .extend((0..MAX_AREAS).map(|index| area(&format!("area_{index}"), &["x"])));
        assert_eq!(
            resolve_entity(&plain(areas, "abajur da sala")),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn area_constraint_never_matches_arealess_entity() {
        let catalog = catalog(
            vec![area("area_sala", &["sala"])],
            vec![entity(
                "free",
                "light.free",
                "light",
                None,
                "abajur",
                &["livre"],
                &[Action::TurnOn],
            )],
        );
        assert_eq!(
            resolve_entity(&request(
                catalog,
                "livre",
                area_constraint("area_sala"),
                None
            )),
            ResolutionOutcome::NoMatch
        );
    }

    #[test]
    fn contracted_normalization_folds_case_and_diacritics_only() {
        let catalog = catalog(
            vec![area("area_sala", &["sala"])],
            vec![entity(
                "lamp",
                "light.lamp",
                "light",
                Some("area_sala"),
                "Abajur",
                &["LÂMPADA"],
                &[Action::TurnOn],
            )],
        );
        assert_eq!(
            resolve_entity(&request(
                catalog.clone(),
                "ABAJUR",
                area_constraint("area_sala"),
                None
            )),
            resolved("lamp", ResolutionEvidence::DisplayNameWithConstraint)
        );
        assert_eq!(
            resolve_entity(&plain(catalog, "lampada")),
            resolved("lamp", ResolutionEvidence::ExplicitRegistryAlias)
        );
    }

    #[test]
    fn normalization_collision_on_external_ids_is_ambiguous() {
        let catalog = catalog(
            vec![],
            vec![
                entity(
                    "one",
                    "light.a-b",
                    "light",
                    None,
                    "um",
                    &["primeiro"],
                    &[Action::TurnOn],
                ),
                entity(
                    "two",
                    "light.a b",
                    "light",
                    None,
                    "dois",
                    &["segundo"],
                    &[Action::TurnOn],
                ),
            ],
        );
        assert_eq!(
            resolve_entity(&plain(catalog, "light.a-b")),
            ResolutionOutcome::Ambiguous {
                candidates: vec!["one".to_owned(), "two".to_owned()],
            }
        );
    }
}
