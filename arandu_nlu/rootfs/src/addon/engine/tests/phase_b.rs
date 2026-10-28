use std::{fs, path::PathBuf};

use local_nlu::{Catalog, InterpretRequest, InterpretResponse, interpret};
use serde::Deserialize;

#[derive(Deserialize)]
struct Case {
    expected: InterpretResponse,
    id: String,
    text: String,
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixtures() -> (Catalog, Vec<Case>) {
    let catalog = serde_json::from_slice(
        &fs::read(root().join("data/phase-b/catalog-v1.json")).expect("Phase B catalog"),
    )
    .expect("valid Phase B catalog");
    let cases = fs::read_to_string(root().join("data/phase-b/corpus-v1.jsonl"))
        .expect("Phase B corpus")
        .lines()
        .map(|line| serde_json::from_str(line).expect("valid Phase B case"))
        .collect();
    (catalog, cases)
}

#[test]
fn frozen_phase_b_corpus_matches() {
    let (catalog, cases) = fixtures();
    assert_eq!(cases.len(), 23);
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
fn catalog_permutation_preserves_phase_b_results() {
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
                version: 1
            }),
            baseline[index],
            "case {}",
            case.id
        );
    }
}

#[test]
fn late_failure_never_returns_partial_plan() {
    let (catalog, _) = fixtures();
    let response = interpret(&InterpretRequest {
        catalog,
        text: "Ligue a luz da sala e ligue o projetor.".to_owned(),
        version: 1,
    });
    assert_eq!(response, InterpretResponse::NoMatch { version: 1 });
}

#[test]
fn four_operations_are_accepted_and_fifth_is_rejected() {
    let (catalog, _) = fixtures();
    let four = interpret(&InterpretRequest { catalog: catalog.clone(), text: "Ligue a luz da sala e desligue a luz do quarto e ligue a cafeteira e coloque o ventilador em 50 por cento.".to_owned(), version: 1 });
    assert!(matches!(four, InterpretResponse::Plan { operations, .. } if operations.len() == 4));
    let five = interpret(&InterpretRequest { catalog, text: "Ligue a luz da sala e desligue a luz do quarto e ligue a cafeteira e apague o ventilador e ligue a luz da sala.".to_owned(), version: 1 });
    assert_eq!(five, InterpretResponse::NoMatch { version: 1 });
}
