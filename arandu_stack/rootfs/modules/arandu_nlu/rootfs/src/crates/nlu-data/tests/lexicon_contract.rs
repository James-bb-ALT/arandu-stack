use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicUsize, Ordering},
};

use nlu_data::{
    DataErrorCode, compile_lexicon,
    lexicon::{LEXICON_MANIFEST_FILE, LEXICON_PACKAGE_FILE, decode_lexicon},
    remove_lexicon_source,
};
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
        let path = std::env::temp_dir().join(format!(
            "nlu-lexicon-{name}-{}-{sequence}",
            std::process::id()
        ));
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
fn fresh_compilers_match_the_tracked_artifact_byte_for_byte() {
    let root = repository_root();
    let manifest = source_manifest(&root);
    let temporary = TestRoot::new("reproduction");
    let first = temporary.child("first");
    let second = temporary.child("second");
    let third = temporary.child("third");
    let copied_root = temporary.child("copied-root");
    copy_admitted_root(&root, &copied_root);
    let copied_manifest = source_manifest(&copied_root);
    let source_before = admitted_source_snapshot(&root);

    assert_eq!(
        compile_lexicon(&manifest, &root, &first).expect("first compile"),
        33
    );
    assert_eq!(
        compile_lexicon(&copied_manifest, &copied_root, &second).expect("copied-root compile"),
        33
    );
    run_data_command(
        &[
            "compile-lexicon",
            "--manifest",
            manifest.to_str().expect("manifest path"),
            "--root",
            root.to_str().expect("root path"),
            "--output",
            third.to_str().expect("third path"),
        ],
        "NLU_DATA_COMPILE_LEXICON_PASS\n",
    );
    assert_eq!(admitted_source_snapshot(&root), source_before);

    for name in [LEXICON_PACKAGE_FILE, LEXICON_MANIFEST_FILE] {
        let expected =
            fs::read(root.join("data/lexicon/p06").join(name)).expect("tracked artifact");
        assert_eq!(
            fs::read(first.join(name)).expect("first artifact"),
            expected
        );
        assert_eq!(
            fs::read(second.join(name)).expect("second artifact"),
            expected
        );
        assert_eq!(
            fs::read(third.join(name)).expect("third artifact"),
            expected
        );
    }
}

#[test]
fn every_entry_retains_complete_lineage_and_conflicts_remain_observable() {
    let root = repository_root();
    let package =
        fs::read(root.join("data/lexicon/p06").join(LEXICON_PACKAGE_FILE)).expect("package");
    let manifest =
        fs::read(root.join("data/lexicon/p06").join(LEXICON_MANIFEST_FILE)).expect("manifest");
    let decoded = decode_lexicon(&package, &manifest).expect("decode");
    assert_eq!(decoded.entries().len(), 33);
    assert_eq!(decoded.manifest().source_ids(), [SOURCE_ID]);
    assert_eq!(decoded.manifest().derivative_licenses(), ["Apache-2.0"]);

    for entry in decoded.entries() {
        assert_eq!(entry.source().source_id(), SOURCE_ID);
        assert_eq!(entry.source().record_id(), entry.analysis_id());
        assert_eq!(entry.source().source_license(), "Apache-2.0");
        assert_eq!(entry.source().partition(), "shared");
        assert_eq!(entry.transformations().len(), 2);
        assert_eq!(entry.derivative_license(), "Apache-2.0");
        assert_eq!(entry.generation().parameters().len(), 4);
    }

    let duplicate_groups = decoded
        .entries()
        .windows(2)
        .filter(|pair| pair[0].surface() == pair[1].surface())
        .count();
    assert_eq!(duplicate_groups, 1);
}

#[test]
fn the_external_manifest_pin_rejects_before_following_a_forged_path() {
    let root = repository_root();
    let original: Value =
        serde_json::from_slice(&fs::read(source_manifest(&root)).expect("source manifest"))
            .expect("parse manifest");
    let mut changed = original;
    changed["artifacts"][0]["path"] =
        Value::String("FIXTURE_TECNICA/missing-before-pin.json".to_owned());
    let temporary = TestRoot::new("manifest-pin");
    let changed_path = temporary.child("source-manifest.json");
    fs::write(
        &changed_path,
        serde_json::to_vec(&changed).expect("encode changed manifest"),
    )
    .expect("write changed manifest");
    let output = temporary.child("output");
    let error = compile_lexicon(&changed_path, &root, &output)
        .expect_err("coordinated manifest change must fail");
    assert_eq!(error.code(), DataErrorCode::IntegrityMismatch);
    assert_eq!(error.context(), "pinned source manifest SHA-256");
    assert!(!output.exists());
}

#[test]
fn removing_the_source_rebuilds_a_byte_clean_empty_artifact() {
    let root = repository_root();
    let temporary = TestRoot::new("removal");
    let compiled = temporary.child("compiled");
    compile_lexicon(&source_manifest(&root), &root, &compiled).expect("compile");
    let filtered = temporary.child("filtered");
    assert_eq!(
        remove_lexicon_source(&compiled, SOURCE_ID, &filtered).expect("remove source"),
        0
    );

    let package = fs::read(filtered.join(LEXICON_PACKAGE_FILE)).expect("filtered package");
    let manifest = fs::read(filtered.join(LEXICON_MANIFEST_FILE)).expect("filtered manifest");
    let decoded = decode_lexicon(&package, &manifest).expect("decode filtered");
    assert!(decoded.entries().is_empty());
    assert!(decoded.manifest().source_ids().is_empty());
    assert!(decoded.manifest().derivative_licenses().is_empty());
    assert!(!contains(&package, SOURCE_ID.as_bytes()));
    assert!(!contains(&manifest, SOURCE_ID.as_bytes()));

    let absent = temporary.child("absent");
    let error =
        remove_lexicon_source(&filtered, SOURCE_ID, &absent).expect_err("absent source removal");
    assert_eq!(error.code(), DataErrorCode::InvalidArguments);
    assert!(!absent.exists());
}

#[test]
fn public_schemas_are_closed_and_match_the_emitted_objects() {
    let root = repository_root();
    let entry_schema: Value = serde_json::from_slice(
        &fs::read(root.join("schemas/lexicon-entry-v1.schema.json")).expect("entry schema"),
    )
    .expect("parse entry schema");
    let manifest_schema: Value = serde_json::from_slice(
        &fs::read(root.join("schemas/lexicon-package-manifest-v1.schema.json"))
            .expect("manifest schema"),
    )
    .expect("parse manifest schema");

    for schema in [&entry_schema, &manifest_schema] {
        assert_eq!(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema"
        );
        assert_eq!(schema["type"], "object");
        assert_eq!(schema["additionalProperties"], false);
        assert_eq!(schema["properties"]["schema_version"]["const"], 1);
    }

    let package =
        fs::read(root.join("data/lexicon/p06").join(LEXICON_PACKAGE_FILE)).expect("package");
    let first_entry = first_entry_value(&package);
    let emitted_manifest: Value = serde_json::from_slice(
        &fs::read(root.join("data/lexicon/p06").join(LEXICON_MANIFEST_FILE)).expect("manifest"),
    )
    .expect("parse manifest");
    assert_eq!(
        object_keys(&first_entry),
        required_fields(&entry_schema),
        "entry schema fields differ"
    );
    assert_eq!(
        object_keys(&emitted_manifest),
        required_fields(&manifest_schema),
        "manifest schema fields differ"
    );
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

fn copy_admitted_root(source: &Path, destination: &Path) {
    let manifest: Value =
        serde_json::from_slice(&fs::read(source_manifest(source)).expect("source manifest"))
            .expect("parse source manifest");
    let mut relative_paths = manifest["artifacts"]
        .as_array()
        .expect("artifact array")
        .iter()
        .map(|artifact| artifact["path"].as_str().expect("artifact path").to_owned())
        .collect::<Vec<_>>();
    relative_paths.push(
        manifest["source"]["license_path"]
            .as_str()
            .expect("license path")
            .to_owned(),
    );
    relative_paths.push(
        manifest["source_decision"]["adr"]
            .as_str()
            .expect("decision path")
            .to_owned(),
    );
    relative_paths.push("data/manifests/project-authored-synthetic-ptbr-v1.json".to_owned());
    relative_paths.sort();
    relative_paths.dedup();
    for relative in relative_paths {
        let target = destination.join(&relative);
        fs::create_dir_all(target.parent().expect("target parent")).expect("create target parent");
        fs::copy(source.join(&relative), target).expect("copy admitted source file");
    }
}

fn admitted_source_snapshot(root: &Path) -> Vec<(String, Vec<u8>)> {
    let manifest: Value =
        serde_json::from_slice(&fs::read(source_manifest(root)).expect("source manifest"))
            .expect("parse source manifest");
    let mut relative_paths = manifest["artifacts"]
        .as_array()
        .expect("artifact array")
        .iter()
        .map(|artifact| artifact["path"].as_str().expect("artifact path").to_owned())
        .collect::<Vec<_>>();
    relative_paths.push("data/manifests/project-authored-synthetic-ptbr-v1.json".to_owned());
    relative_paths.sort();
    relative_paths
        .into_iter()
        .map(|relative| {
            let bytes = fs::read(root.join(&relative)).expect("snapshot source file");
            (relative, bytes)
        })
        .collect()
}

fn first_entry_value(package: &[u8]) -> Value {
    let length = u32::from_be_bytes(package[16..20].try_into().expect("entry length")) as usize;
    serde_json::from_slice(&package[20..20 + length]).expect("parse first entry")
}

fn object_keys(value: &Value) -> BTreeSet<String> {
    value.as_object().expect("object").keys().cloned().collect()
}

fn required_fields(schema: &Value) -> BTreeSet<String> {
    schema["required"]
        .as_array()
        .expect("required fields")
        .iter()
        .map(|field| field.as_str().expect("required field").to_owned())
        .collect()
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
