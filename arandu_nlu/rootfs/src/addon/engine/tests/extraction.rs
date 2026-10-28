use std::{fs, path::PathBuf};

use local_nlu::{MentionExtraction, ResolutionCatalog, extract};
use serde::Deserialize;

#[derive(Deserialize)]
struct Row {
    id: String,
    text: String,
    expected: Expected,
}

#[derive(Deserialize)]
struct Expected {
    outcome: String,
    #[serde(default)]
    segments: Vec<local_nlu::OperationSegment>,
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn catalog() -> ResolutionCatalog {
    serde_json::from_slice(
        &fs::read(root().join("evaluation/ptbr-independent/mention-extraction/snapshot-v1.json"))
            .expect("extraction snapshot"),
    )
    .expect("valid extraction snapshot")
}

fn rows() -> Vec<Row> {
    fs::read_to_string(
        root().join("evaluation/ptbr-independent/mention-extraction/corpus-v1.jsonl"),
    )
    .expect("extraction corpus")
    .lines()
    .map(|line| serde_json::from_str(line).expect("valid extraction row"))
    .collect()
}

#[test]
fn frozen_mention_extraction_corpus_matches() {
    let catalog = catalog();
    let rows = rows();
    assert_eq!(rows.len(), 30);
    for row in &rows {
        let actual = extract(&row.text, &catalog);
        match row.expected.outcome.as_str() {
            "no_extraction" => assert_eq!(actual, None, "case {}", row.id),
            "extracted" => {
                let actual = actual.unwrap_or_else(|| panic!("case {} extracted", row.id));
                assert_eq!(actual.text, row.text, "case {}", row.id);
                assert_eq!(actual.catalog_id, catalog.catalog_id, "case {}", row.id);
                assert_eq!(actual.generation, catalog.generation, "case {}", row.id);
                assert_eq!(
                    actual.operation_segments, row.expected.segments,
                    "case {}",
                    row.id
                );
            }
            _ => panic!("unknown outcome {}", row.expected.outcome),
        }
    }
}

#[test]
fn extraction_survives_snapshot_permutation() {
    let mut catalog = catalog();
    let rows = rows();
    let baseline: Vec<Option<MentionExtraction>> = rows
        .iter()
        .map(|row| extract(&row.text, &catalog))
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
            extract(&row.text, &catalog),
            baseline[index],
            "case {}",
            row.id
        );
    }
}
