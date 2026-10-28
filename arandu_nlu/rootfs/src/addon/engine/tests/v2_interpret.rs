use std::{fs, path::PathBuf};

use local_nlu::{InterpretRequestV2, InterpretResponseV2, ResolutionCatalog, interpret_v2};
use serde::Deserialize;

#[derive(Deserialize)]
struct Row {
    id: String,
    text: String,
    generation: String,
    expected: Expected,
}

#[derive(Deserialize)]
struct Expected {
    status: String,
    #[serde(default)]
    operations: Vec<local_nlu::Operation>,
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn snapshot() -> ResolutionCatalog {
    serde_json::from_slice(
        &fs::read(root().join("evaluation/ptbr-independent/v2-interpret/snapshot-v1.json"))
            .expect("v2 snapshot"),
    )
    .expect("valid v2 snapshot")
}

fn rows() -> Vec<Row> {
    fs::read_to_string(root().join("evaluation/ptbr-independent/v2-interpret/corpus-v1.jsonl"))
        .expect("v2 corpus")
        .lines()
        .map(|line| serde_json::from_str(line).expect("valid v2 row"))
        .collect()
}

fn expected_outcome(row: &Row) -> InterpretResponseV2 {
    match row.expected.status.as_str() {
        "plan" => InterpretResponseV2::Plan {
            operations: row.expected.operations.clone(),
            version: 2,
        },
        "ambiguous" => InterpretResponseV2::Ambiguous { version: 2 },
        "no_match" => InterpretResponseV2::NoMatch { version: 2 },
        _ => panic!("unknown status {}", row.expected.status),
    }
}

#[test]
fn frozen_v2_interpret_corpus_matches() {
    let snapshot = snapshot();
    let rows = rows();
    assert_eq!(rows.len(), 24);
    for row in &rows {
        let actual = interpret_v2(&InterpretRequestV2 {
            text: row.text.clone(),
            snapshot: snapshot.clone(),
            generation: row.generation.clone(),
        });
        assert_eq!(actual, expected_outcome(row), "case {}", row.id);
    }
}

#[test]
fn v2_interpret_survives_snapshot_permutation() {
    let mut snapshot = snapshot();
    let rows = rows();
    let baseline: Vec<_> = rows
        .iter()
        .map(|row| {
            interpret_v2(&InterpretRequestV2 {
                text: row.text.clone(),
                snapshot: snapshot.clone(),
                generation: row.generation.clone(),
            })
        })
        .collect();
    snapshot.areas.reverse();
    snapshot.entities.reverse();
    for area in &mut snapshot.areas {
        area.names.reverse();
    }
    for entity in &mut snapshot.entities {
        entity.aliases.reverse();
        entity.capabilities.reverse();
    }
    for (index, row) in rows.iter().enumerate() {
        assert_eq!(
            interpret_v2(&InterpretRequestV2 {
                text: row.text.clone(),
                snapshot: snapshot.clone(),
                generation: row.generation.clone(),
            }),
            baseline[index],
            "case {}",
            row.id
        );
    }
}
