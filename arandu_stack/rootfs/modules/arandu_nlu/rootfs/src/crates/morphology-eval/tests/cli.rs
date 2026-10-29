use std::{path::PathBuf, process::Command};

const MANIFEST_PATH: &str = "data/evaluation/p07/morphology-v1/manifest.json";

#[test]
fn successful_cli_emits_one_canonical_json_line() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let output = Command::new(env!("CARGO_BIN_EXE_morphology-eval"))
        .arg("--root")
        .arg(root)
        .arg("--manifest")
        .arg(MANIFEST_PATH)
        .output()
        .expect("run evaluator");
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    assert_eq!(output.stdout.last(), Some(&b'\n'));
    assert_ne!(
        output.stdout.get(output.stdout.len().saturating_sub(2)),
        Some(&b'\n')
    );
    serde_json::from_slice::<serde_json::Value>(&output.stdout).expect("report JSON");
}

#[test]
fn argument_failure_emits_no_stdout() {
    let output = Command::new(env!("CARGO_BIN_EXE_morphology-eval"))
        .arg("--root")
        .output()
        .expect("run evaluator");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(!output.stderr.is_empty());
}
