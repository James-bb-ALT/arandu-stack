#![cfg(feature = "evaluator")]

use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

const MANIFEST: &str = "data/evaluation/p08/pos-v1/manifest.json";

#[test]
fn successful_cli_matches_the_tracked_canonical_report() {
    let root = repository_root();
    let output = Command::new(env!("CARGO_BIN_EXE_pos-eval"))
        .arg("--root")
        .arg(&root)
        .arg("--manifest")
        .arg(MANIFEST)
        .output()
        .expect("run evaluator");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    assert_eq!(
        output.stdout,
        fs::read(root.join("data/evaluation/p08/pos-v1/report.json")).expect("tracked report")
    );
}

#[test]
fn argument_and_manifest_integrity_failures_emit_no_stdout() {
    let argument_failure = Command::new(env!("CARGO_BIN_EXE_pos-eval"))
        .arg("--root")
        .output()
        .expect("run invalid evaluator");
    assert!(!argument_failure.status.success());
    assert!(argument_failure.stdout.is_empty());

    let source = repository_root();
    let isolated = unique_temporary("manifest-mutation");
    let manifest_path = isolated.join(MANIFEST);
    fs::create_dir_all(manifest_path.parent().expect("manifest parent"))
        .expect("isolated manifest directory");
    let mut manifest = fs::read(source.join(MANIFEST)).expect("source manifest");
    manifest.push(b' ');
    fs::write(&manifest_path, manifest).expect("mutated manifest");
    let integrity_failure = Command::new(env!("CARGO_BIN_EXE_pos-eval"))
        .arg("--root")
        .arg(&isolated)
        .arg("--manifest")
        .arg(MANIFEST)
        .output()
        .expect("run evaluator with mutated manifest");
    assert!(!integrity_failure.status.success());
    assert!(integrity_failure.stdout.is_empty());
    fs::remove_dir_all(isolated).expect("remove isolated root");
}

#[test]
fn evaluator_reads_no_train_or_development_slice() {
    let source = repository_root();
    let isolated = unique_temporary("heldout-only");
    for relative in [
        MANIFEST,
        "data/evaluation/p08/pos-v1/splits/split-manifest.json",
        "data/evaluation/p08/pos-v1/splits/heldout.jsonl",
        "data/pos/p08/package.bin",
        "data/pos/p08/package-manifest.json",
    ] {
        let destination = isolated.join(relative);
        fs::create_dir_all(destination.parent().expect("artifact parent"))
            .expect("isolated artifact directory");
        fs::copy(source.join(relative), destination).expect("copy evaluator input");
    }

    let output = Command::new(env!("CARGO_BIN_EXE_pos-eval"))
        .arg("--root")
        .arg(&isolated)
        .arg("--manifest")
        .arg(MANIFEST)
        .output()
        .expect("run heldout-only evaluator");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !isolated
            .join("data/evaluation/p08/pos-v1/splits/train.jsonl")
            .exists()
    );
    assert!(
        !isolated
            .join("data/evaluation/p08/pos-v1/splits/development.jsonl")
            .exists()
    );
    assert_eq!(
        output.stdout,
        fs::read(source.join("data/evaluation/p08/pos-v1/report.json")).expect("tracked report")
    );
    fs::remove_dir_all(isolated).expect("remove isolated root");
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn unique_temporary(label: &str) -> PathBuf {
    let process = std::process::id();
    let path = std::env::temp_dir().join(format!("nlu-p08-pos-eval-{label}-{process}"));
    remove_if_present(&path);
    path
}

fn remove_if_present(path: &Path) {
    match fs::remove_dir_all(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("remove stale temporary directory: {error}"),
    }
}
