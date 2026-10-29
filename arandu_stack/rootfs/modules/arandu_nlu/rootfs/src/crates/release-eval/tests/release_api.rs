use std::{
    fs,
    path::{Path, PathBuf},
};

use release_eval::canonical_report;
use serde::Serialize;

const MAX_SOURCE_FILES: usize = 64;
const MAX_SOURCE_BYTES: u64 = 2 * 1024 * 1024;

#[derive(Serialize)]
struct CanonicalFixture<'a> {
    fixture_label: &'a str,
    fixture_value: u64,
}

#[test]
fn public_canonical_report_is_stable_for_mechanical_fixture() {
    let fixture = CanonicalFixture {
        fixture_label: "FIXTURE_TECNICA_CANONICAL",
        fixture_value: 7,
    };
    let first = canonical_report(&fixture).expect("FIXTURE_TECNICA first report");
    let second = canonical_report(&fixture).expect("FIXTURE_TECNICA second report");
    assert_eq!(first, second);
    assert_eq!(
        first,
        br#"{"fixture_label":"FIXTURE_TECNICA_CANONICAL","fixture_value":7}"#
    );
}

#[test]
fn test_sources_cannot_invoke_sealed_corpus_entry_points() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut source_files = Vec::new();
    collect_rust_sources(&manifest.join("src"), 0, &mut source_files);
    let unit_source_count = source_files.len();
    collect_rust_sources(&manifest.join("tests"), 0, &mut source_files);
    assert!(source_files.len() <= MAX_SOURCE_FILES);

    let forbidden = [
        concat!("frozen::", "load("),
        concat!("validate_", "inputs("),
        concat!("evaluate", "("),
        concat!("semantic_", "preflight("),
        concat!("benchmark", "("),
        concat!("benchmark_", "with_identity("),
        concat!("HELDOUT_", "PATH"),
        concat!("PERFORMANCE_", "PATH"),
        concat!("heldout", ".jsonl"),
        concat!("performance", ".jsonl"),
    ];
    for (index, path) in source_files.iter().enumerate() {
        let metadata = fs::metadata(path).expect("FIXTURE_TECNICA source metadata");
        assert!(metadata.len() <= MAX_SOURCE_BYTES);
        let source = fs::read_to_string(path).expect("FIXTURE_TECNICA source read");
        let relevant = if index < unit_source_count {
            source
                .find("#[cfg(test)]")
                .map_or("", |offset| &source[offset..])
        } else {
            source.as_str()
        };
        let compact = relevant
            .chars()
            .filter(|character| !character.is_ascii_whitespace())
            .collect::<String>();
        for pattern in forbidden {
            assert!(
                !compact.contains(pattern),
                "FIXTURE_TECNICA forbidden sealed-corpus call in {}",
                path.display()
            );
        }
    }
}

fn collect_rust_sources(directory: &Path, depth: usize, output: &mut Vec<PathBuf>) {
    assert!(depth <= 4);
    let mut entries = fs::read_dir(directory)
        .expect("FIXTURE_TECNICA source directory")
        .map(|entry| entry.expect("FIXTURE_TECNICA source entry").path())
        .collect::<Vec<_>>();
    entries.sort();
    for path in entries {
        let file_type = fs::symlink_metadata(&path)
            .expect("FIXTURE_TECNICA source file type")
            .file_type();
        assert!(!file_type.is_symlink());
        if file_type.is_dir() {
            collect_rust_sources(&path, depth + 1, output);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            output.push(path);
            assert!(output.len() <= MAX_SOURCE_FILES);
        }
    }
}
