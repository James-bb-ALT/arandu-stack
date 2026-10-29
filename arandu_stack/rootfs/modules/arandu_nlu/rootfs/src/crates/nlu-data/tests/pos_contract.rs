use nlu_data::{
    DataErrorCode, canonical_json,
    pos::{
        MAX_POS_LABEL_BYTES, MAX_POS_LABELS, MAX_POS_MANIFEST_BYTES, MAX_POS_PACKAGE_BYTES,
        MAX_POS_TRANSITIONS, POS_PACKAGE_MAGIC, POS_PACKAGE_VERSION, PosCompileInput, PosEndpoint,
        PosModelIdentity, PosSourceIdentity, PosTrainingIdentity, PosTransition,
        compile_pos_transition_package, decode_pos_transition_package,
    },
    sha256_hex,
};
use serde_json::Value;

#[test]
fn compiler_and_decoder_bind_the_complete_contract() {
    let identity = identity();
    let compiled = compile_pos_transition_package(&input(
        vec!["B", "A"],
        vec![
            transition(label("B"), PosEndpoint::Eos),
            transition(label("A"), label("B")),
            transition(PosEndpoint::Bos, label("A")),
        ],
    ))
    .expect("compile");

    assert_eq!(
        &compiled.package_bytes()[..POS_PACKAGE_MAGIC.len()],
        POS_PACKAGE_MAGIC
    );
    assert_eq!(
        compiled.package_bytes()[POS_PACKAGE_MAGIC.len()],
        POS_PACKAGE_VERSION
    );
    assert!(compiled.package_bytes().len() <= MAX_POS_PACKAGE_BYTES);
    assert!(compiled.manifest_bytes().len() <= MAX_POS_MANIFEST_BYTES);
    assert!(compiled.manifest_bytes().ends_with(b"\n"));

    let decoded = decode_pos_transition_package(
        compiled.package_bytes(),
        compiled.manifest_bytes(),
        &identity,
    )
    .expect("decode");
    assert_eq!(decoded.labels(), ["A", "B"]);
    assert_eq!(
        decoded.transitions(),
        [
            transition(PosEndpoint::Bos, label("A")),
            transition(label("A"), label("B")),
            transition(label("B"), PosEndpoint::Eos),
        ]
    );
    assert!(decoded.contains_transition(&transition(label("A"), label("B"))));

    let manifest = decoded.manifest();
    assert_eq!(manifest.schema_version(), 1);
    assert_eq!(manifest.format(), "nlu-pos-transition-package-v1");
    assert_eq!(manifest.identity(), &identity);
    assert_eq!(manifest.random_seed(), 0);
    assert!(!manifest.randomness_used());
    assert_eq!(manifest.source().source_id(), "source-v1");
    assert_eq!(manifest.source().corpus_version(), "1.0.0");
    assert_eq!(manifest.source().generator_id(), "generator-v1");
    assert_eq!(manifest.source().source_license(), "Apache-2.0");
    assert_eq!(manifest.source().locale(), "pt-BR");
    assert_eq!(manifest.training().train_sentence_count(), 3);
    assert_eq!(manifest.training().train_document_count(), 1);
    assert_eq!(manifest.training().train_token_count(), 6);
    assert_eq!(manifest.label_count(), 2);
    assert_eq!(manifest.transition_count(), 3);
    assert_eq!(
        manifest.package_bytes(),
        u64::try_from(compiled.package_bytes().len()).expect("package size")
    );
    assert_eq!(
        manifest.package_sha256(),
        sha256_hex(compiled.package_bytes()).expect("package hash")
    );
    assert_eq!(manifest.labels_sha256().len(), 64);
    assert_eq!(manifest.transitions_sha256().len(), 64);

    let value: Value =
        serde_json::from_slice(compiled.manifest_bytes()).expect("parse canonical manifest");
    let mut expected = canonical_json(&value, "manifest").expect("canonical manifest");
    expected.push(b'\n');
    assert_eq!(compiled.manifest_bytes(), expected);
}

#[test]
fn label_and_transition_permutations_emit_identical_bytes() {
    let first = compile_pos_transition_package(&input(
        vec!["A", "B"],
        vec![
            transition(PosEndpoint::Bos, label("A")),
            transition(label("A"), label("B")),
            transition(label("B"), PosEndpoint::Eos),
        ],
    ))
    .expect("first compile");
    let second = compile_pos_transition_package(&input(
        vec!["B", "A"],
        vec![
            transition(label("B"), PosEndpoint::Eos),
            transition(label("A"), label("B")),
            transition(PosEndpoint::Bos, label("A")),
        ],
    ))
    .expect("second compile");

    assert_eq!(first.package_bytes(), second.package_bytes());
    assert_eq!(first.manifest_bytes(), second.manifest_bytes());
}

#[test]
fn compiler_rejects_duplicates_unknown_labels_and_invalid_boundaries() {
    let duplicate_label = input(
        vec!["A", "A"],
        vec![
            transition(PosEndpoint::Bos, label("A")),
            transition(label("A"), PosEndpoint::Eos),
        ],
    );
    assert_eq!(
        compile_pos_transition_package(&duplicate_label)
            .expect_err("duplicate label")
            .code(),
        DataErrorCode::InvalidRecord
    );

    let repeated = transition(PosEndpoint::Bos, label("A"));
    let duplicate_transition = input(
        vec!["A"],
        vec![
            repeated.clone(),
            repeated,
            transition(label("A"), PosEndpoint::Eos),
        ],
    );
    assert_eq!(
        compile_pos_transition_package(&duplicate_transition)
            .expect_err("duplicate transition")
            .code(),
        DataErrorCode::InvalidRecord
    );

    let unknown = input(
        vec!["A"],
        vec![
            transition(PosEndpoint::Bos, label("B")),
            transition(label("B"), PosEndpoint::Eos),
        ],
    );
    assert_eq!(
        compile_pos_transition_package(&unknown)
            .expect_err("unknown label")
            .code(),
        DataErrorCode::InvalidRecord
    );

    for transitions in [
        vec![transition(PosEndpoint::Bos, PosEndpoint::Eos)],
        vec![
            transition(PosEndpoint::Bos, label("A")),
            transition(PosEndpoint::Eos, label("A")),
        ],
        vec![
            transition(label("A"), PosEndpoint::Bos),
            transition(label("A"), PosEndpoint::Eos),
        ],
    ] {
        assert_eq!(
            compile_pos_transition_package(&input(vec!["A"], transitions))
                .expect_err("invalid boundary")
                .code(),
            DataErrorCode::InvalidRecord
        );
    }
}

#[test]
fn compiler_accepts_exact_label_limit_and_rejects_one_over_limits() {
    let labels = (0..MAX_POS_LABELS)
        .map(|index| format!("A{index}"))
        .collect::<Vec<_>>();
    let transitions = labels
        .iter()
        .flat_map(|value| {
            [
                transition(PosEndpoint::Bos, label(value)),
                transition(label(value), PosEndpoint::Eos),
            ]
        })
        .collect::<Vec<_>>();
    let exact = PosCompileInput::new(identity(), source(), training(), labels, transitions);
    compile_pos_transition_package(&exact).expect("exact label limit");

    let one_over_labels = (0..=MAX_POS_LABELS)
        .map(|index| format!("A{index}"))
        .collect::<Vec<_>>();
    let excessive_labels = PosCompileInput::new(
        identity(),
        source(),
        training(),
        one_over_labels,
        vec![
            transition(PosEndpoint::Bos, label("A0")),
            transition(label("A0"), PosEndpoint::Eos),
        ],
    );
    assert_eq!(
        compile_pos_transition_package(&excessive_labels)
            .expect_err("one-over labels")
            .code(),
        DataErrorCode::ResourceLimit
    );

    let excessive_transitions = PosCompileInput::new(
        identity(),
        source(),
        training(),
        vec!["A".to_owned()],
        vec![transition(PosEndpoint::Bos, label("A")); MAX_POS_TRANSITIONS + 1],
    );
    assert_eq!(
        compile_pos_transition_package(&excessive_transitions)
            .expect_err("one-over transitions")
            .code(),
        DataErrorCode::ResourceLimit
    );

    let long_label = "A".repeat(MAX_POS_LABEL_BYTES + 1);
    let excessive_label_bytes = PosCompileInput::new(
        identity(),
        source(),
        training(),
        vec![long_label.clone()],
        vec![
            transition(PosEndpoint::Bos, label(&long_label)),
            transition(label(&long_label), PosEndpoint::Eos),
        ],
    );
    assert_eq!(
        compile_pos_transition_package(&excessive_label_bytes)
            .expect_err("one-over label bytes")
            .code(),
        DataErrorCode::ResourceLimit
    );
}

#[test]
fn decoder_enforces_package_and_manifest_byte_limits_first() {
    let identity = identity();
    let package = vec![0; MAX_POS_PACKAGE_BYTES + 1];
    assert_eq!(
        decode_pos_transition_package(&package, b"not-json", &identity)
            .expect_err("one-over package")
            .code(),
        DataErrorCode::ResourceLimit
    );

    let compiled = compiled();
    let manifest = vec![b' '; MAX_POS_MANIFEST_BYTES + 1];
    assert_eq!(
        decode_pos_transition_package(compiled.package_bytes(), &manifest, &identity)
            .expect_err("one-over manifest")
            .code(),
        DataErrorCode::ResourceLimit
    );

    let one_over_labels = raw_package(
        &(0..=MAX_POS_LABELS)
            .map(|index| format!("A{index}"))
            .collect::<Vec<_>>(),
        &[],
    );
    let manifest = manifest_for_package(compiled.manifest_bytes(), &one_over_labels);
    assert_eq!(
        decode_pos_transition_package(&one_over_labels, &manifest, &identity)
            .expect_err("one-over encoded labels")
            .code(),
        DataErrorCode::ResourceLimit
    );

    let mut one_over_transitions = Vec::new();
    one_over_transitions.extend_from_slice(POS_PACKAGE_MAGIC);
    one_over_transitions.push(POS_PACKAGE_VERSION);
    one_over_transitions.push(1);
    one_over_transitions.extend_from_slice(
        &u16::try_from(MAX_POS_TRANSITIONS + 1)
            .expect("transition count")
            .to_be_bytes(),
    );
    one_over_transitions.extend_from_slice(&[1, b'A']);
    let manifest = manifest_for_package(compiled.manifest_bytes(), &one_over_transitions);
    assert_eq!(
        decode_pos_transition_package(&one_over_transitions, &manifest, &identity)
            .expect_err("one-over encoded transitions")
            .code(),
        DataErrorCode::ResourceLimit
    );
}

#[test]
fn decoder_rejects_bad_framing_truncation_and_trailing_bytes() {
    let identity = identity();
    let compiled = compiled();
    let package = compiled.package_bytes();

    for end in [0, 6, 7, 8, 10, package.len() - 1] {
        let changed = &package[..end];
        let manifest = manifest_for_package(compiled.manifest_bytes(), changed);
        assert!(
            decode_pos_transition_package(changed, &manifest, &identity).is_err(),
            "accepted truncation at {end}"
        );
    }

    let mut bad_magic = package.to_vec();
    bad_magic[0] ^= 1;
    assert_raw_rejected(&compiled, &bad_magic, DataErrorCode::InvalidRecord);

    let mut bad_version = package.to_vec();
    bad_version[POS_PACKAGE_MAGIC.len()] = POS_PACKAGE_VERSION + 1;
    assert_raw_rejected(&compiled, &bad_version, DataErrorCode::InvalidRecord);

    let mut trailing = package.to_vec();
    trailing.push(0);
    assert_raw_rejected(&compiled, &trailing, DataErrorCode::InvalidRecord);
}

#[test]
fn decoder_rejects_noncanonical_labels_and_transitions() {
    let compiled = compiled();

    for package in [
        raw_package(&["B".to_owned(), "A".to_owned()], &[(0, 1), (1, 3)]),
        raw_package(&["A".to_owned(), "A".to_owned()], &[(0, 1), (1, 3)]),
        raw_package(&["A".to_owned(), "B".to_owned()], &[(1, 3), (0, 1)]),
        raw_package(&["A".to_owned(), "B".to_owned()], &[(0, 1), (0, 1), (1, 3)]),
    ] {
        assert_raw_rejected(&compiled, &package, DataErrorCode::InvalidRecord);
    }
}

#[test]
fn decoder_rejects_invalid_endpoint_codes_and_boundary_directions() {
    let compiled = compiled();
    for transitions in [
        vec![(0, 4), (1, 3)],
        vec![(0, 1), (3, 1)],
        vec![(0, 1), (1, 0)],
        vec![(0, 3)],
        vec![(1, 3)],
    ] {
        let package = raw_package(&["A".to_owned(), "B".to_owned()], &transitions);
        assert_raw_rejected(&compiled, &package, DataErrorCode::InvalidRecord);
    }
}

#[test]
fn decoder_rejects_hash_size_configuration_and_inventory_mismatches() {
    let identity = identity();
    let compiled = compiled();

    let wrong_identity =
        PosModelIdentity::new("model-v1", "algorithm-v1", "compiler-v1", "config-v2");
    assert_eq!(
        decode_pos_transition_package(
            compiled.package_bytes(),
            compiled.manifest_bytes(),
            &wrong_identity,
        )
        .expect_err("configuration mismatch")
        .code(),
        DataErrorCode::IntegrityMismatch
    );

    for field in ["package_sha256", "labels_sha256", "transitions_sha256"] {
        let manifest = mutate_manifest(compiled.manifest_bytes(), |value| {
            value[field] = Value::String("0".repeat(64));
        });
        assert_eq!(
            decode_pos_transition_package(compiled.package_bytes(), &manifest, &identity)
                .expect_err("hash mismatch")
                .code(),
            DataErrorCode::IntegrityMismatch,
            "field {field}"
        );
    }

    let manifest = mutate_manifest(compiled.manifest_bytes(), |value| {
        value["package_bytes"] =
            Value::from(u64::try_from(compiled.package_bytes().len() + 1).expect("package size"));
    });
    assert_eq!(
        decode_pos_transition_package(compiled.package_bytes(), &manifest, &identity)
            .expect_err("size mismatch")
            .code(),
        DataErrorCode::IntegrityMismatch
    );
}

#[test]
fn decoder_rejects_noncanonical_duplicate_or_open_manifests() {
    let identity = identity();
    let compiled = compiled();

    let value: Value =
        serde_json::from_slice(compiled.manifest_bytes()).expect("parse manifest value");
    let mut pretty = serde_json::to_vec_pretty(&value).expect("pretty manifest");
    pretty.push(b'\n');
    assert_eq!(
        decode_pos_transition_package(compiled.package_bytes(), &pretty, &identity)
            .expect_err("noncanonical manifest")
            .code(),
        DataErrorCode::InvalidManifest
    );

    let open = mutate_manifest(compiled.manifest_bytes(), |value| {
        value
            .as_object_mut()
            .expect("manifest object")
            .insert("unknown".to_owned(), Value::Bool(true));
    });
    assert_eq!(
        decode_pos_transition_package(compiled.package_bytes(), &open, &identity)
            .expect_err("unknown manifest field")
            .code(),
        DataErrorCode::InvalidManifest
    );

    let mut duplicate = br#"{"schema_version":1,"#.to_vec();
    duplicate.extend_from_slice(&compiled.manifest_bytes()[1..]);
    assert_eq!(
        decode_pos_transition_package(compiled.package_bytes(), &duplicate, &identity)
            .expect_err("duplicate manifest key")
            .code(),
        DataErrorCode::DuplicateJsonKey
    );

    let invalid_seed = mutate_manifest(compiled.manifest_bytes(), |value| {
        value["random_seed"] = Value::from(1);
    });
    assert_eq!(
        decode_pos_transition_package(compiled.package_bytes(), &invalid_seed, &identity)
            .expect_err("changed seed")
            .code(),
        DataErrorCode::InvalidManifest
    );
}

fn identity() -> PosModelIdentity {
    PosModelIdentity::new("model-v1", "algorithm-v1", "compiler-v1", "config-v1")
}

fn source() -> PosSourceIdentity {
    PosSourceIdentity::new(
        "source-v1",
        "1.0.0",
        "generator-v1",
        "Apache-2.0",
        "pt-BR",
        digest('a'),
        digest('b'),
        digest('c'),
        digest('d'),
    )
}

fn training() -> PosTrainingIdentity {
    PosTrainingIdentity::new(digest('e'), digest('f'), digest('1'), digest('2'), 3, 1, 6)
}

fn input(labels: Vec<&str>, transitions: Vec<PosTransition>) -> PosCompileInput {
    PosCompileInput::new(
        identity(),
        source(),
        training(),
        labels.into_iter().map(str::to_owned).collect(),
        transitions,
    )
}

fn compiled() -> nlu_data::pos::CompiledPosPackage {
    compile_pos_transition_package(&input(
        vec!["A", "B"],
        vec![
            transition(PosEndpoint::Bos, label("A")),
            transition(label("A"), label("B")),
            transition(label("B"), PosEndpoint::Eos),
        ],
    ))
    .expect("compile fixture")
}

fn transition(from: PosEndpoint, to: PosEndpoint) -> PosTransition {
    PosTransition::new(from, to)
}

fn label(value: impl Into<String>) -> PosEndpoint {
    PosEndpoint::label(value)
}

fn digest(digit: char) -> String {
    digit.to_string().repeat(64)
}

fn raw_package(labels: &[String], transitions: &[(u8, u8)]) -> Vec<u8> {
    let mut package = Vec::new();
    package.extend_from_slice(POS_PACKAGE_MAGIC);
    package.push(POS_PACKAGE_VERSION);
    package.push(u8::try_from(labels.len()).expect("label count"));
    package.extend_from_slice(
        &u16::try_from(transitions.len())
            .expect("transition count")
            .to_be_bytes(),
    );
    for label in labels {
        package.push(u8::try_from(label.len()).expect("label bytes"));
        package.extend_from_slice(label.as_bytes());
    }
    for (from, to) in transitions {
        package.extend_from_slice(&[*from, *to]);
    }
    package
}

fn manifest_for_package(manifest: &[u8], package: &[u8]) -> Vec<u8> {
    mutate_manifest(manifest, |value| {
        value["package_bytes"] =
            Value::from(u64::try_from(package.len()).expect("changed package size"));
        value["package_sha256"] = Value::String(sha256_hex(package).expect("changed package hash"));
    })
}

fn mutate_manifest(manifest: &[u8], mutate: impl FnOnce(&mut Value)) -> Vec<u8> {
    let mut value: Value = serde_json::from_slice(manifest).expect("parse manifest");
    mutate(&mut value);
    let mut bytes = canonical_json(&value, "changed manifest").expect("canonical changed manifest");
    bytes.push(b'\n');
    bytes
}

fn assert_raw_rejected(
    compiled: &nlu_data::pos::CompiledPosPackage,
    package: &[u8],
    expected_code: DataErrorCode,
) {
    let manifest = manifest_for_package(compiled.manifest_bytes(), package);
    let error = decode_pos_transition_package(package, &manifest, &identity())
        .expect_err("invalid package");
    assert_eq!(error.code(), expected_code);
}
