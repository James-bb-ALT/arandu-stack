use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

const SPLIT_MANIFEST: &str = "data/evaluation/p08/pos-v1/splits/split-manifest.json";

#[test]
fn successful_cli_writes_only_the_two_frozen_artifacts() {
    let root = repository_root();
    let temporary = unique_temporary("success");
    fs::create_dir(&temporary).expect("temporary parent");
    let output_directory = temporary.join("model");

    let output = Command::new(env!("CARGO_BIN_EXE_pos-train"))
        .arg("--root")
        .arg(&root)
        .arg("--split-manifest")
        .arg(SPLIT_MANIFEST)
        .arg("--output")
        .arg(&output_directory)
        .output()
        .expect("run trainer");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    assert_eq!(output.stdout.last(), Some(&b'\n'));
    let summary: serde_json::Value = serde_json::from_slice(&output.stdout).expect("summary JSON");
    assert_eq!(summary["operation"], "train");

    let mut names = fs::read_dir(&output_directory)
        .expect("model directory")
        .map(|entry| {
            entry
                .expect("directory entry")
                .file_name()
                .into_string()
                .expect("UTF-8 filename")
        })
        .collect::<Vec<_>>();
    names.sort();
    assert_eq!(names, ["package-manifest.json", "package.bin"]);
    for filename in ["package.bin", "package-manifest.json"] {
        assert_eq!(
            fs::read(output_directory.join(filename)).expect("generated artifact"),
            fs::read(root.join("data/pos/p08").join(filename)).expect("tracked artifact")
        );
    }
    fs::remove_dir_all(&temporary).expect("remove temporary directory");
}

#[test]
fn argument_or_nonempty_output_failure_emits_no_stdout() {
    let missing = Command::new(env!("CARGO_BIN_EXE_pos-train"))
        .arg("--root")
        .output()
        .expect("run invalid trainer");
    assert!(!missing.status.success());
    assert!(missing.stdout.is_empty());

    let root = repository_root();
    let temporary = unique_temporary("nonempty");
    fs::create_dir(&temporary).expect("temporary directory");
    fs::write(temporary.join("occupied"), b"sentinel").expect("sentinel");
    let occupied = Command::new(env!("CARGO_BIN_EXE_pos-train"))
        .arg("--root")
        .arg(&root)
        .arg("--split-manifest")
        .arg(SPLIT_MANIFEST)
        .arg("--output")
        .arg(&temporary)
        .output()
        .expect("run trainer into occupied directory");
    assert!(!occupied.status.success());
    assert!(occupied.stdout.is_empty());
    assert_eq!(
        fs::read(temporary.join("occupied")).expect("sentinel bytes"),
        b"sentinel"
    );
    fs::remove_dir_all(&temporary).expect("remove temporary directory");
}

#[test]
fn trainer_never_requires_development_or_heldout_slice_files() {
    let source_root = repository_root();
    let isolated = unique_temporary("train-only");
    populate_train_root(&source_root, &isolated);
    let split_directory = isolated.join("data/evaluation/p08/pos-v1/splits");

    let output = Command::new(env!("CARGO_BIN_EXE_pos-train"))
        .arg("--root")
        .arg(&isolated)
        .arg("--split-manifest")
        .arg(SPLIT_MANIFEST)
        .arg("--output")
        .arg(isolated.join("model"))
        .output()
        .expect("run isolated trainer");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!split_directory.join("development.jsonl").exists());
    assert!(!split_directory.join("heldout.jsonl").exists());
    fs::remove_dir_all(&isolated).expect("remove isolated root");
}

#[test]
fn independent_roots_emit_byte_identical_artifacts() {
    let source = repository_root();
    let first = unique_temporary("root-a");
    let second = unique_temporary("root-b");
    populate_train_root(&source, &first);
    populate_train_root(&source, &second);

    for root in [&first, &second] {
        let output = Command::new(env!("CARGO_BIN_EXE_pos-train"))
            .arg("--root")
            .arg(root)
            .arg("--split-manifest")
            .arg(SPLIT_MANIFEST)
            .arg("--output")
            .arg(root.join("model"))
            .output()
            .expect("run independent trainer");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    for filename in ["package.bin", "package-manifest.json"] {
        assert_eq!(
            fs::read(first.join("model").join(filename)).expect("first artifact"),
            fs::read(second.join("model").join(filename)).expect("second artifact")
        );
    }
    fs::remove_dir_all(first).expect("remove first root");
    fs::remove_dir_all(second).expect("remove second root");
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn populate_train_root(source: &Path, destination: &Path) {
    let split_directory = destination.join("data/evaluation/p08/pos-v1/splits");
    fs::create_dir_all(&split_directory).expect("isolated split directory");
    fs::copy(
        source.join(SPLIT_MANIFEST),
        split_directory.join("split-manifest.json"),
    )
    .expect("copy split manifest");
    fs::copy(
        source.join("data/evaluation/p08/pos-v1/splits/train.jsonl"),
        split_directory.join("train.jsonl"),
    )
    .expect("copy train");
}

fn unique_temporary(label: &str) -> PathBuf {
    let process = std::process::id();
    let path = std::env::temp_dir().join(format!("nlu-p08-pos-train-{label}-{process}"));
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
