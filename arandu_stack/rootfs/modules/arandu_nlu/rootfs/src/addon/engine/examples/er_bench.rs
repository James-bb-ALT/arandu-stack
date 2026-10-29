//! Post-implementation benchmark and conformance probe for entity resolution.
//!
//! Reads the frozen preparation inputs (`catalog-v1.json`, `corpus-v1.jsonl`),
//! translates each row into a [`ResolutionRequest`], verifies the outcome
//! against the project-authored gold label, then times repeated full passes.
//! Any gold mismatch fails closed with a nonzero exit before timing starts.
//! Prints one JSON document to stdout; memory is measured externally.

use std::fs;
use std::time::Instant;

use local_nlu::{
    Action, ResolutionCatalog, ResolutionConstraints, ResolutionEvidence, ResolutionOutcome,
    ResolutionRequest, resolve_entity,
};

fn action(name: &str) -> Action {
    match name {
        "turn_on" => Action::TurnOn,
        "turn_off" => Action::TurnOff,
        "set_fan_percentage" => Action::SetFanPercentage,
        "get_state" => Action::GetState,
        _ => panic!("unknown capability {name}"),
    }
}

fn evidence(name: &str) -> ResolutionEvidence {
    match name {
        "external_entity_id" => ResolutionEvidence::ExternalEntityId,
        "explicit_registry_alias" => ResolutionEvidence::ExplicitRegistryAlias,
        "display_name_with_constraint" => ResolutionEvidence::DisplayNameWithConstraint,
        _ => panic!("unknown evidence {name}"),
    }
}

fn base() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../evaluation/ptbr-independent/entity-resolution")
}

fn load_catalog() -> ResolutionCatalog {
    let raw: serde_json::Value =
        serde_json::from_slice(&fs::read(base().join("catalog-v1.json")).expect("read catalog"))
            .expect("parse catalog");
    ResolutionCatalog {
        catalog_id: raw["catalog_id"].as_str().expect("catalog id").to_owned(),
        generation: raw["generation"].as_str().expect("generation").to_owned(),
        areas: raw["areas"]
            .as_array()
            .expect("areas")
            .iter()
            .map(|area| local_nlu::ResolutionArea {
                area_id: area["area_id"].as_str().expect("area id").to_owned(),
                names: area["names"]
                    .as_array()
                    .expect("area names")
                    .iter()
                    .map(|name| name.as_str().expect("area name").to_owned())
                    .collect(),
            })
            .collect(),
        entities: raw["entities"]
            .as_array()
            .expect("entities")
            .iter()
            .map(|entity| local_nlu::ResolutionEntity {
                registry_id: entity["registry_id"]
                    .as_str()
                    .expect("registry id")
                    .to_owned(),
                entity_id: entity["entity_id"].as_str().expect("entity id").to_owned(),
                domain: entity["domain"].as_str().expect("domain").to_owned(),
                area_id: Some(entity["area_id"].as_str().expect("area ref").to_owned()),
                display_name: entity["display_name"].as_str().expect("display").to_owned(),
                aliases: entity["aliases"]
                    .as_array()
                    .expect("aliases")
                    .iter()
                    .map(|alias| alias.as_str().expect("alias").to_owned())
                    .collect(),
                capabilities: entity["capabilities"]
                    .as_array()
                    .expect("capabilities")
                    .iter()
                    .map(|name| action(name.as_str().expect("capability")))
                    .collect(),
            })
            .collect(),
    }
}

fn load_rows() -> Vec<serde_json::Value> {
    fs::read_to_string(base().join("corpus-v1.jsonl"))
        .expect("read corpus")
        .lines()
        .map(|line| serde_json::from_str(line).expect("parse row"))
        .collect()
}

fn span_bound(value: &serde_json::Value, what: &str) -> usize {
    let raw = value.as_u64().unwrap_or_else(|| panic!("span {what}"));
    usize::try_from(raw).unwrap_or_else(|_| panic!("span {what} out of range"))
}

fn build_request(catalog: &ResolutionCatalog, row: &serde_json::Value) -> ResolutionRequest {
    let mut catalog = catalog.clone();
    if row
        .get("catalog_variant")
        .and_then(serde_json::Value::as_str)
        == Some("duplicate_registry_id")
    {
        catalog.entities.push(catalog.entities[0].clone());
    }
    ResolutionRequest {
        text: row["text"].as_str().expect("text").to_owned(),
        generation: row["generation"].as_str().expect("generation").to_owned(),
        mention: row["mention"].as_str().expect("mention").to_owned(),
        span: row.get("span").and_then(|span| {
            if span.is_null() {
                return None;
            }
            let bounds = span.as_array().expect("span bounds");
            Some([
                span_bound(&bounds[0], "start"),
                span_bound(&bounds[1], "end"),
            ])
        }),
        constraints: ResolutionConstraints {
            area_id: row["constraints"]
                .get("area_id")
                .and_then(|value| value.as_str().map(std::string::ToString::to_string)),
            domain: row["constraints"]
                .get("domain")
                .and_then(|value| value.as_str().map(std::string::ToString::to_string)),
            capability: row["constraints"]
                .get("capability")
                .and_then(|value| value.as_str().map(action)),
        },
        catalog,
    }
}

fn expected(row: &serde_json::Value) -> ResolutionOutcome {
    match row["expected"]["outcome"].as_str().expect("outcome") {
        "resolved" => ResolutionOutcome::Resolved {
            registry_id: row["expected"]["candidates"][0]
                .as_str()
                .expect("candidate")
                .to_owned(),
            evidence: evidence(row["expected"]["evidence"].as_str().expect("evidence")),
        },
        "ambiguous" => ResolutionOutcome::Ambiguous {
            candidates: row["expected"]["candidates"]
                .as_array()
                .expect("candidates")
                .iter()
                .map(|candidate| candidate.as_str().expect("candidate").to_owned())
                .collect(),
        },
        "no_match" => ResolutionOutcome::NoMatch,
        other => panic!("unknown outcome {other}"),
    }
}

fn iterations() -> u64 {
    std::env::args()
        .skip_while(|argument| argument != "--iterations")
        .nth(1)
        .and_then(|value| value.parse().ok())
        .unwrap_or(20_000)
}

fn main() {
    let catalog = load_catalog();
    let rows = load_rows();
    let requests: Vec<ResolutionRequest> = rows
        .iter()
        .map(|row| build_request(&catalog, row))
        .collect();
    let gold: Vec<ResolutionOutcome> = rows.iter().map(expected).collect();
    for (index, request) in requests.iter().enumerate() {
        let actual = resolve_entity(request);
        if actual != gold[index] {
            eprintln!(
                "gold mismatch at row {}: got {actual:?}, want {:?}",
                rows[index]["id"], gold[index]
            );
            std::process::exit(1);
        }
    }
    let rounds = iterations();
    let started = Instant::now();
    for _ in 0..rounds {
        for request in &requests {
            std::hint::black_box(resolve_entity(request));
        }
    }
    let total_ns = started.elapsed().as_nanos();
    let cases = requests.len() as u128;
    let total_cases = cases * u128::from(rounds.max(1));
    // Bounded inputs: total_ns stays far below 2^53 for any realistic run and
    // total_cases is bounded by cases times iterations, so both conversions
    // below are exact and the mean keeps sub-nanosecond fractional precision.
    #[allow(clippy::cast_precision_loss)]
    let mean_ns_per_case = total_ns as f64 / total_cases as f64;
    println!(
        "{}",
        serde_json::json!({
            "schema_version": 1,
            "cases": requests.len(),
            "gold_mismatches": 0,
            "iterations": rounds,
            "total_ns": total_ns,
            "mean_ns_per_case": mean_ns_per_case,
            "throughput_cases_per_second": 1_000_000_000.0 / mean_ns_per_case,
        })
    );
}
