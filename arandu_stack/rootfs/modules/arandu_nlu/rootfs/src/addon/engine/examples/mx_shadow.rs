//! Offline utterance shadow: extraction plus resolution plus v1 reference.
//!
//! Reads a mention-extraction corpus with its ER snapshot and a mirrored
//! MLP catalog, then emits per-row JSON: extraction outcome, per-mention
//! resolution outcomes, and the v1 interpretation outcome with its flattened
//! target set. Semantics stay in Rust; aggregation and comparison live in
//! the Python runner. Strictly observational: no execution, no residential
//! data.

use std::collections::BTreeSet;
use std::fs;

use local_nlu::{
    Catalog, InterpretRequest, InterpretResponse, ResolutionCatalog, ResolutionConstraints,
    ResolutionOutcome, extract, interpret, resolve_entity,
};

fn arg_value(name: &str) -> String {
    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        if argument == name {
            return arguments.next().unwrap_or_else(|| {
                eprintln!("missing value for {name}");
                std::process::exit(2);
            });
        }
    }
    eprintln!("missing argument {name}");
    std::process::exit(2);
}

fn resolution_request(
    snapshot: &ResolutionCatalog,
    text: &str,
    mention: &local_nlu::Mention,
) -> local_nlu::ResolutionRequest {
    let mut constraints = ResolutionConstraints::default();
    for constraint in &mention.constraints {
        match constraint.kind {
            local_nlu::ConstraintKind::Area => {
                constraints.area_id = Some(constraint.value.clone());
            }
            local_nlu::ConstraintKind::Domain => {
                constraints.domain = Some(constraint.value.clone());
            }
            local_nlu::ConstraintKind::Capability => {
                constraints.capability = Some(match constraint.value.as_str() {
                    "turn_on" => local_nlu::Action::TurnOn,
                    "turn_off" => local_nlu::Action::TurnOff,
                    "set_fan_percentage" => local_nlu::Action::SetFanPercentage,
                    "get_state" => local_nlu::Action::GetState,
                    other => panic!("unknown capability {other}"),
                });
            }
        }
    }
    local_nlu::ResolutionRequest {
        text: text.to_owned(),
        catalog: snapshot.clone(),
        generation: snapshot.generation.clone(),
        mention: mention.text.clone(),
        span: Some(mention.span),
        constraints,
    }
}

fn main() {
    let snapshot: ResolutionCatalog =
        serde_json::from_slice(&fs::read(arg_value("--snapshot")).expect("read snapshot"))
            .expect("parse snapshot");
    let catalog: Catalog =
        serde_json::from_slice(&fs::read(arg_value("--mlp-catalog")).expect("read catalog"))
            .expect("parse catalog");
    let corpus = fs::read_to_string(arg_value("--corpus")).expect("read corpus");
    let mut rows_out = Vec::new();
    for line in corpus.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let row: serde_json::Value = serde_json::from_str(line).expect("parse row");
        let text = row["text"].as_str().expect("text").to_owned();
        let extraction = extract(&text, &snapshot);
        let mut resolutions = Vec::new();
        if let Some(ref extraction) = extraction {
            for segment in &extraction.operation_segments {
                for mention in &segment.mentions {
                    let outcome = resolve_entity(&resolution_request(&snapshot, &text, mention));
                    let (status, registry_id, candidates) = match &outcome {
                        ResolutionOutcome::Resolved {
                            registry_id,
                            evidence: _,
                        } => ("resolved", Some(registry_id.clone()), Vec::new()),
                        ResolutionOutcome::Ambiguous { candidates } => {
                            ("ambiguous", None, candidates.clone())
                        }
                        ResolutionOutcome::NoMatch => ("no_match", None, Vec::new()),
                    };
                    resolutions.push(serde_json::json!({
                        "mention": mention.id,
                        "outcome": status,
                        "registry_id": registry_id,
                        "candidates": candidates,
                    }));
                }
            }
        }
        let v1 = interpret(&InterpretRequest {
            catalog: catalog.clone(),
            text: text.clone(),
            version: 1,
        });
        let (v1_status, v1_targets) = match &v1 {
            InterpretResponse::Plan { operations, .. } => {
                let mut targets = BTreeSet::new();
                for operation in operations {
                    targets.extend(operation.targets.iter().cloned());
                }
                ("plan", targets.into_iter().collect::<Vec<_>>())
            }
            InterpretResponse::Ambiguous { .. } => ("ambiguous", Vec::new()),
            InterpretResponse::NoMatch { .. } => ("no_match", Vec::new()),
            InterpretResponse::InvalidRequest { .. } => ("invalid_request", Vec::new()),
        };
        rows_out.push(serde_json::json!({
            "id": row["id"],
            "extracted": extraction.is_some(),
            "v1": {"status": v1_status, "targets": v1_targets},
            "resolutions": resolutions,
        }));
    }
    println!("{}", serde_json::Value::Array(rows_out));
}
