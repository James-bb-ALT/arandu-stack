use std::{fs, path::PathBuf};

use local_nlu::{
    Action, ResolutionCatalog, ResolutionConstraints, ResolutionEvidence, ResolutionOutcome,
    ResolutionRequest, resolve_entity,
};
use serde::Deserialize;

#[derive(Deserialize)]
struct SourceArea {
    area_id: String,
    names: Vec<String>,
}

#[derive(Deserialize)]
struct SourceEntity {
    registry_id: String,
    entity_id: String,
    domain: String,
    area_id: String,
    display_name: String,
    aliases: Vec<String>,
    capabilities: Vec<String>,
}

#[derive(Deserialize)]
struct SourceCatalog {
    catalog_id: String,
    generation: String,
    areas: Vec<SourceArea>,
    entities: Vec<SourceEntity>,
}

#[derive(Deserialize)]
struct Expected {
    outcome: String,
    #[serde(default)]
    evidence: Option<String>,
    #[serde(default)]
    candidates: Option<Vec<String>>,
}

#[derive(Deserialize)]
struct Row {
    id: String,
    text: String,
    mention: String,
    #[serde(default)]
    span: Option<[usize; 2]>,
    generation: String,
    #[serde(default)]
    constraints: RowConstraints,
    #[serde(default)]
    catalog_variant: Option<String>,
    expected: Expected,
}

#[derive(Default, Deserialize)]
struct RowConstraints {
    #[serde(default)]
    area_id: Option<String>,
    #[serde(default)]
    domain: Option<String>,
    #[serde(default)]
    capability: Option<String>,
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn capability(name: &str) -> Action {
    match name {
        "turn_on" => Action::TurnOn,
        "turn_off" => Action::TurnOff,
        "set_fan_percentage" => Action::SetFanPercentage,
        "get_state" => Action::GetState,
        _ => panic!("unknown capability {name}"),
    }
}

fn catalog() -> ResolutionCatalog {
    let source: SourceCatalog = serde_json::from_slice(
        &fs::read(root().join("evaluation/ptbr-independent/entity-resolution/catalog-v1.json"))
            .expect("entity-resolution catalog"),
    )
    .expect("valid entity-resolution catalog");
    ResolutionCatalog {
        catalog_id: source.catalog_id,
        generation: source.generation,
        areas: source
            .areas
            .into_iter()
            .map(|area| local_nlu::ResolutionArea {
                area_id: area.area_id,
                names: area.names,
            })
            .collect(),
        entities: source
            .entities
            .into_iter()
            .map(|entity| local_nlu::ResolutionEntity {
                registry_id: entity.registry_id,
                entity_id: entity.entity_id,
                domain: entity.domain,
                area_id: Some(entity.area_id),
                display_name: entity.display_name,
                aliases: entity.aliases,
                capabilities: entity
                    .capabilities
                    .iter()
                    .map(|name| capability(name))
                    .collect(),
            })
            .collect(),
    }
}

fn rows() -> Vec<Row> {
    fs::read_to_string(root().join("evaluation/ptbr-independent/entity-resolution/corpus-v1.jsonl"))
        .expect("entity-resolution corpus")
        .lines()
        .map(|line| serde_json::from_str(line).expect("valid entity-resolution row"))
        .collect()
}

fn evidence(name: &str) -> ResolutionEvidence {
    match name {
        "external_entity_id" => ResolutionEvidence::ExternalEntityId,
        "explicit_registry_alias" => ResolutionEvidence::ExplicitRegistryAlias,
        "display_name_with_constraint" => ResolutionEvidence::DisplayNameWithConstraint,
        _ => panic!("unknown evidence {name}"),
    }
}

fn expected_outcome(row: &Row) -> ResolutionOutcome {
    match row.expected.outcome.as_str() {
        "resolved" => ResolutionOutcome::Resolved {
            registry_id: row
                .expected
                .candidates
                .as_ref()
                .expect("resolved candidate")[0]
                .clone(),
            evidence: evidence(row.expected.evidence.as_deref().expect("resolved evidence")),
        },
        "ambiguous" => ResolutionOutcome::Ambiguous {
            candidates: row
                .expected
                .candidates
                .clone()
                .expect("ambiguous candidates"),
        },
        "no_match" => ResolutionOutcome::NoMatch,
        _ => panic!("unknown outcome {}", row.expected.outcome),
    }
}

fn request_for(catalog: ResolutionCatalog, row: &Row) -> ResolutionRequest {
    let mut catalog = catalog;
    if row.catalog_variant.as_deref() == Some("duplicate_registry_id") {
        catalog.entities.push(catalog.entities[0].clone());
    }
    ResolutionRequest {
        text: row.text.clone(),
        generation: row.generation.clone(),
        mention: row.mention.clone(),
        span: row.span,
        constraints: ResolutionConstraints {
            area_id: row.constraints.area_id.clone(),
            domain: row.constraints.domain.clone(),
            capability: row.constraints.capability.as_deref().map(capability),
        },
        catalog,
    }
}

#[test]
fn frozen_entity_resolution_corpus_matches() {
    let catalog = catalog();
    let rows = rows();
    assert_eq!(rows.len(), 21);
    for row in &rows {
        let actual = resolve_entity(&request_for(catalog.clone(), row));
        assert_eq!(actual, expected_outcome(row), "case {}", row.id);
    }
}

#[test]
fn entity_resolution_survives_catalog_permutation() {
    let mut catalog = catalog();
    let rows = rows();
    let baseline: Vec<_> = rows
        .iter()
        .map(|row| resolve_entity(&request_for(catalog.clone(), row)))
        .collect();
    catalog.areas.reverse();
    catalog.entities.reverse();
    for area in &mut catalog.areas {
        area.names.reverse();
    }
    for entity in &mut catalog.entities {
        entity.aliases.reverse();
        entity.capabilities.reverse();
    }
    for (index, row) in rows.iter().enumerate() {
        assert_eq!(
            resolve_entity(&request_for(catalog.clone(), row)),
            baseline[index],
            "case {}",
            row.id
        );
    }
}
