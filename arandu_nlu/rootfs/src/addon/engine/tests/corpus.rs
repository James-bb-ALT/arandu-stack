use std::{fs, path::PathBuf};

use local_nlu::{Catalog, InterpretRequest, InterpretResponse, interpret};
use serde::Deserialize;

#[derive(Deserialize)]
struct CorpusCase {
    expected: InterpretResponse,
    id: String,
    text: String,
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixtures() -> (Catalog, Vec<CorpusCase>) {
    let catalog = serde_json::from_slice(
        &fs::read(root().join("data/mlp/project-authored-synthetic-catalog-v1.json"))
            .expect("frozen catalog"),
    )
    .expect("valid frozen catalog");
    let cases = fs::read_to_string(root().join("data/mlp/project-authored-synthetic-v1.jsonl"))
        .expect("frozen corpus")
        .lines()
        .map(|line| serde_json::from_str(line).expect("valid corpus row"))
        .collect();
    (catalog, cases)
}

#[test]
fn frozen_internal_conformance_corpus_passes() {
    let (catalog, cases) = fixtures();
    assert_eq!(cases.len(), 24);
    for case in cases {
        let actual = interpret(&InterpretRequest {
            catalog: catalog.clone(),
            text: case.text,
            version: 1,
        });
        assert_eq!(actual, case.expected, "case {}", case.id);
    }
}

#[test]
fn catalog_order_cannot_change_semantics() {
    let (mut catalog, cases) = fixtures();
    let baseline: Vec<_> = cases
        .iter()
        .map(|case| {
            interpret(&InterpretRequest {
                catalog: catalog.clone(),
                text: case.text.clone(),
                version: 1,
            })
        })
        .collect();
    catalog.areas.reverse();
    catalog.entities.reverse();
    for area in &mut catalog.areas {
        area.names.reverse();
    }
    for entity in &mut catalog.entities {
        entity.names.reverse();
        entity.actions.reverse();
    }
    for (index, case) in cases.iter().enumerate() {
        assert_eq!(
            interpret(&InterpretRequest {
                catalog: catalog.clone(),
                text: case.text.clone(),
                version: 1,
            }),
            baseline[index],
            "case {}",
            case.id
        );
    }
}

#[test]
fn protocol_round_trip_preserves_original_unicode() {
    let (catalog, _) = fixtures();
    let request = InterpretRequest {
        catalog,
        text: "LÂMPADA “São João” — 50%".to_owned(),
        version: 1,
    };
    let bytes = serde_json::to_vec(&request).expect("serialize request");
    let decoded: InterpretRequest = serde_json::from_slice(&bytes).expect("deserialize request");
    assert_eq!(decoded.text, request.text);
}

#[test]
fn documented_exact_switch_alias_is_not_shadowed_by_area_grammar() {
    let (catalog, _) = fixtures();
    assert_eq!(
        interpret(&InterpretRequest {
            catalog,
            text: "Ligue o interruptor da cafeteira.".to_owned(),
            version: 1,
        }),
        InterpretResponse::Plan {
            operations: vec![local_nlu::Operation {
                action: local_nlu::Action::TurnOn,
                percentage: None,
                targets: vec!["reg_switch_cafeteira".to_owned()],
            }],
            version: 1,
        }
    );
}
