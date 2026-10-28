use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicUsize, Ordering},
};

use nlu_data::{DataErrorCode, compile, fetch, import, normalize, split, validate_stage, verify};
use serde_json::Value;

const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("repository root")
        .to_path_buf()
}

fn source_manifest(root: &Path) -> PathBuf {
    root.join("data/manifests/project-authored-synthetic-ptbr-v1.json")
}

struct TestRoot {
    path: PathBuf,
}

impl TestRoot {
    fn new(name: &str) -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let sequence = NEXT.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("nlu-data-{name}-{}-{sequence}", std::process::id()));
        fs::create_dir(&path).expect("create test root");
        Self { path }
    }

    fn child(&self, name: &str) -> PathBuf {
        self.path.join(name)
    }
}

impl Drop for TestRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[test]
fn verifies_and_fetches_only_with_explicit_authorization() {
    let root = repository_root();
    let manifest = source_manifest(&root);
    assert_eq!(verify(&manifest, &root).expect("verify source"), 14);

    let temporary = TestRoot::new("fetch");
    let denied = temporary.child("denied");
    let error = fetch(&manifest, &root, &denied, false).expect_err("fetch without consent");
    assert_eq!(error.code(), DataErrorCode::InvalidArguments);
    assert!(!denied.exists());

    let fetched = temporary.child("fetched");
    fetch(&manifest, &root, &fetched, true).expect("authorized fetch");
    assert_eq!(
        verify(&fetched.join("source-manifest.json"), &fetched).expect("verify fetched source"),
        14
    );
    assert_eq!(
        fs::read(root.join("data/project-authored/p02-v1/heldout.jsonl"))
            .expect("original heldout"),
        fs::read(fetched.join("data/project-authored/p02-v1/heldout.jsonl"))
            .expect("fetched heldout")
    );
}

#[test]
fn rejects_corrupt_bytes_and_symlink_substitution() {
    let root = repository_root();
    let manifest = source_manifest(&root);
    let temporary = TestRoot::new("corruption");
    let fetched = temporary.child("fetched");
    fetch(&manifest, &root, &fetched, true).expect("authorized fetch");
    let fetched_manifest = fetched.join("source-manifest.json");
    let artifact = fetched.join("data/project-authored/p02-v1/development.jsonl");

    let original = fs::read(&artifact).expect("read artifact");
    let mut corrupt = original.clone();
    corrupt[0] ^= 1;
    fs::write(&artifact, corrupt).expect("write corruption");
    let error = verify(&fetched_manifest, &fetched).expect_err("hash mismatch");
    assert_eq!(error.code(), DataErrorCode::IntegrityMismatch);

    fs::remove_file(&artifact).expect("remove corrupt artifact");
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(
            root.join("data/project-authored/p02-v1/development.jsonl"),
            &artifact,
        )
        .expect("create substitution symlink");
        let error = verify(&fetched_manifest, &fetched).expect_err("symlink substitution");
        assert_eq!(error.code(), DataErrorCode::InvalidPath);
    }
}

#[test]
fn complete_pipeline_is_reproducible_and_selectively_removable() {
    let root = repository_root();
    let manifest = source_manifest(&root);
    let temporary = TestRoot::new("reproduction");

    let first = build_pipeline(&temporary, "first", &manifest, &root);
    let second = build_pipeline_cli(&temporary, "second", &manifest, &root);
    for relative in [
        "imported/records.jsonl",
        "normalized/records.jsonl",
        "split/records.jsonl",
        "compiled/package.bin",
        "compiled/package-manifest.json",
    ] {
        assert_eq!(
            fs::read(first.join(relative)).expect("first output"),
            fs::read(second.join(relative)).expect("second output"),
            "reproduction differs for {relative}"
        );
    }

    let filtered = temporary.child("filtered");
    run_data_command(
        &[
            "remove-source",
            "--input",
            first.join("split").to_str().expect("split path"),
            "--source-id",
            SOURCE_ID,
            "--output",
            filtered.to_str().expect("filtered path"),
        ],
        "NLU_DATA_REMOVE_SOURCE_PASS\n",
    );
    assert_eq!(
        validate_stage(&filtered).expect("validate filtered stage"),
        0
    );
    let filtered_package = temporary.child("filtered-package");
    assert_eq!(
        compile(&filtered, &filtered_package).expect("compile filtered stage"),
        0
    );
    let package = fs::read(filtered_package.join("package.bin")).expect("filtered package");
    assert!(
        !package
            .windows(SOURCE_ID.len())
            .any(|window| window == SOURCE_ID.as_bytes())
    );
    let package_manifest: Value = serde_json::from_slice(
        &fs::read(filtered_package.join("package-manifest.json")).expect("package manifest"),
    )
    .expect("parse package manifest");
    assert_eq!(package_manifest["record_count"], 0);
    assert_eq!(package_manifest["source_ids"], serde_json::json!([]));
}

#[test]
fn rejects_manifest_field_status_license_review_path_and_hash_mutations() {
    let root = repository_root();
    let manifest_path = source_manifest(&root);
    let original: Value =
        serde_json::from_slice(&fs::read(&manifest_path).expect("manifest")).expect("parse");
    let temporary = TestRoot::new("manifest-mutations");

    let mut unknown = original.clone();
    unknown
        .as_object_mut()
        .expect("root object")
        .insert("unexpected".to_owned(), Value::Bool(true));
    assert_manifest_fails(&temporary, "unknown", &unknown, &root);

    let mut missing = original.clone();
    missing["source"]
        .as_object_mut()
        .expect("source object")
        .remove("id");
    assert_manifest_fails(&temporary, "missing", &missing, &root);

    let mut status = original.clone();
    status["source"]["admission_status"] = Value::String("ADMITTED_AUTONOMOUS".to_owned());
    assert_manifest_fails(&temporary, "status", &status, &root);

    for (name, expression) in [
        ("license-nc", "CC-BY-NC-4.0"),
        ("license-nd", "CC-BY-ND-4.0"),
        ("license-research", "LicenseRef-Research-Only"),
        ("license-missing", "NOASSERTION"),
    ] {
        let mut license = original.clone();
        license["source"]["license"] = Value::String(expression.to_owned());
        assert_manifest_fails(&temporary, name, &license, &root);
    }

    let mut mutable_version = original.clone();
    mutable_version["source"]["immutable_version"] = Value::String("main".to_owned());
    assert_manifest_fails(&temporary, "mutable-version", &mutable_version, &root);

    let mut owner = original.clone();
    owner["source"]["upstream_owner"] = Value::String(String::new());
    assert_manifest_fails(&temporary, "owner", &owner, &root);

    let mut reviews = original.clone();
    reviews["source_reviews"]
        .as_array_mut()
        .expect("review array")
        .pop();
    assert_manifest_fails(&temporary, "reviews", &reviews, &root);

    let mut traversal = original.clone();
    traversal["artifacts"][0]["path"] = Value::String("../LICENSE".to_owned());
    assert_manifest_fails(&temporary, "traversal", &traversal, &root);

    let mut hash = original;
    hash["artifacts"][0]["sha256"] = Value::String("0".repeat(64));
    let path = write_manifest(&temporary, "hash", &hash);
    let error = verify(&path, &root).expect_err("artifact hash mutation");
    assert_eq!(error.code(), DataErrorCode::IntegrityMismatch);
}

#[test]
fn binary_exposes_verify_and_denies_implicit_fetch() {
    let root = repository_root();
    let manifest = source_manifest(&root);
    let verify_output = Command::new(env!("CARGO_BIN_EXE_nlu-data"))
        .args(["verify", "--manifest"])
        .arg(&manifest)
        .arg("--root")
        .arg(&root)
        .output()
        .expect("run verify command");
    assert!(verify_output.status.success());
    assert_eq!(verify_output.stdout, b"NLU_DATA_VERIFY_PASS\n");

    let temporary = TestRoot::new("cli");
    let denied = temporary.child("denied");
    let fetch_output = Command::new(env!("CARGO_BIN_EXE_nlu-data"))
        .args(["fetch", "--manifest"])
        .arg(&manifest)
        .arg("--root")
        .arg(&root)
        .arg("--output")
        .arg(&denied)
        .output()
        .expect("run fetch command");
    assert_eq!(fetch_output.status.code(), Some(2));
    assert!(!denied.exists());
    assert!(
        !fetch_output
            .stderr
            .windows(b"quero".len())
            .any(|window| window == b"quero")
    );

    let fetched = temporary.child("fetched");
    let authorized_output = Command::new(env!("CARGO_BIN_EXE_nlu-data"))
        .args(["fetch", "--manifest"])
        .arg(&manifest)
        .arg("--root")
        .arg(&root)
        .arg("--output")
        .arg(&fetched)
        .arg("--allow-fetch")
        .output()
        .expect("run authorized fetch command");
    assert!(authorized_output.status.success());
    assert_eq!(authorized_output.stdout, b"NLU_DATA_FETCH_PASS\n");
    assert_eq!(
        verify(&fetched.join("source-manifest.json"), &fetched).expect("verify CLI fetch"),
        14
    );
}

#[test]
fn published_manifest_schemas_are_versioned_closed_objects() {
    let root = repository_root();
    for name in [
        "data-source-manifest-v1.schema.json",
        "data-stage-manifest-v1.schema.json",
        "data-package-manifest-v1.schema.json",
    ] {
        let schema: Value = serde_json::from_slice(
            &fs::read(root.join("schemas").join(name)).expect("schema bytes"),
        )
        .expect("schema JSON");
        assert_eq!(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema"
        );
        assert_eq!(schema["type"], "object");
        assert_eq!(schema["additionalProperties"], false);
        assert_eq!(schema["properties"]["schema_version"]["const"], 1);
    }
}

fn build_pipeline(temporary: &TestRoot, name: &str, manifest: &Path, root: &Path) -> PathBuf {
    let output = temporary.child(name);
    fs::create_dir(&output).expect("create pipeline root");
    let imported = output.join("imported");
    let normalized = output.join("normalized");
    let split_stage = output.join("split");
    let compiled = output.join("compiled");
    assert_eq!(import(manifest, root, &imported).expect("import"), 11_850);
    assert_eq!(
        normalize(&imported, &normalized).expect("normalize"),
        11_850
    );
    assert_eq!(validate_stage(&normalized).expect("validate"), 11_850);
    assert_eq!(split(&normalized, &split_stage).expect("split"), 11_850);
    assert_eq!(compile(&split_stage, &compiled).expect("compile"), 11_850);
    output
}

fn build_pipeline_cli(temporary: &TestRoot, name: &str, manifest: &Path, root: &Path) -> PathBuf {
    let output = temporary.child(name);
    fs::create_dir(&output).expect("create pipeline root");
    let imported = output.join("imported");
    let normalized = output.join("normalized");
    let split_stage = output.join("split");
    let compiled = output.join("compiled");
    run_data_command(
        &[
            "import",
            "--manifest",
            manifest.to_str().expect("manifest path"),
            "--root",
            root.to_str().expect("root path"),
            "--output",
            imported.to_str().expect("import path"),
        ],
        "NLU_DATA_IMPORT_PASS\n",
    );
    run_data_command(
        &[
            "normalize",
            "--input",
            imported.to_str().expect("import path"),
            "--output",
            normalized.to_str().expect("normalize path"),
        ],
        "NLU_DATA_NORMALIZE_PASS\n",
    );
    run_data_command(
        &[
            "validate",
            "--input",
            normalized.to_str().expect("normalize path"),
        ],
        "NLU_DATA_VALIDATE_PASS\n",
    );
    run_data_command(
        &[
            "split",
            "--input",
            normalized.to_str().expect("normalize path"),
            "--output",
            split_stage.to_str().expect("split path"),
        ],
        "NLU_DATA_SPLIT_PASS\n",
    );
    run_data_command(
        &[
            "compile",
            "--input",
            split_stage.to_str().expect("split path"),
            "--output",
            compiled.to_str().expect("compile path"),
        ],
        "NLU_DATA_COMPILE_PASS\n",
    );
    output
}

fn run_data_command(arguments: &[&str], expected_stdout: &str) {
    let output = Command::new(env!("CARGO_BIN_EXE_nlu-data"))
        .args(arguments)
        .output()
        .expect("run nlu-data command");
    assert!(
        output.status.success(),
        "command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(output.stdout, expected_stdout.as_bytes());
    assert!(output.stderr.is_empty());
}

fn assert_manifest_fails(temporary: &TestRoot, name: &str, manifest: &Value, root: &Path) {
    let path = write_manifest(temporary, name, manifest);
    assert!(
        verify(&path, root).is_err(),
        "manifest mutation passed: {name}"
    );
}

fn write_manifest(temporary: &TestRoot, name: &str, manifest: &Value) -> PathBuf {
    let path = temporary.child(&format!("{name}.json"));
    fs::write(
        &path,
        serde_json::to_vec(manifest).expect("serialize manifest"),
    )
    .expect("write manifest");
    path
}
