use nlu_data::{
    DataErrorCode, canonical_json, compile_intent_package, decode_intent_package,
    intent::{INTENT_COMPILER_ID, IntentPackageIdentity, MAX_INTENT_SCHEMA_BYTES},
    parse_strict_json, sha256_hex,
};
use serde_json::Value;

const SCHEMA_SOURCE: &[u8] = include_bytes!("../../../data/intents/p09/schema-source.json");

fn identity() -> IntentPackageIdentity {
    IntentPackageIdentity::new(
        "p09-intent-schema-v1",
        "p09-weighted-marker-slot-extraction-v1",
        INTENT_COMPILER_ID,
        "p09-intent-config-v1",
    )
}

fn rebind_manifest(package: &[u8], manifest: &[u8]) -> Vec<u8> {
    let mut value = parse_strict_json(manifest, "manifest").expect("manifest");
    let object = value.as_object_mut().expect("manifest object");
    object.insert(
        "package_bytes".into(),
        Value::from(u64::try_from(package.len()).expect("package size")),
    );
    object.insert(
        "package_sha256".into(),
        Value::from(sha256_hex(package).expect("package hash")),
    );
    let mut bytes = canonical_json(&value, "manifest").expect("canonical manifest");
    bytes.push(b'\n');
    bytes
}

#[test]
fn frozen_schema_compiles_and_decodes_strictly() {
    let compiled = compile_intent_package(SCHEMA_SOURCE).expect("compile frozen schema");
    let decoded = decode_intent_package(
        compiled.package_bytes(),
        compiled.manifest_bytes(),
        &identity(),
    )
    .expect("decode frozen schema");

    assert_eq!(decoded.schema().schema_version(), 1);
    assert_eq!(decoded.schema().intents().len(), 20);
    assert_eq!(decoded.manifest().intent_count(), 20);
    assert_eq!(decoded.manifest().source().linguistic_input(), "train_only");
}

#[test]
fn package_bytes_ignore_schema_inventory_order() {
    let original = compile_intent_package(SCHEMA_SOURCE).expect("original");
    let mut value = parse_strict_json(SCHEMA_SOURCE, "schema").expect("schema");
    let intents = value
        .get_mut("intents")
        .and_then(Value::as_array_mut)
        .expect("intents");
    intents.reverse();
    for intent in intents {
        intent
            .get_mut("markers")
            .and_then(Value::as_array_mut)
            .expect("markers")
            .reverse();
        intent
            .get_mut("slots")
            .and_then(Value::as_array_mut)
            .expect("slots")
            .reverse();
    }
    let reordered = serde_json::to_vec(&value).expect("reordered schema");
    let compiled = compile_intent_package(&reordered).expect("compile reordered");

    assert_eq!(original.package_bytes(), compiled.package_bytes());
    assert_ne!(original.manifest_bytes(), compiled.manifest_bytes());
}

#[test]
fn schema_rejects_duplicate_unknown_and_oversized_content() {
    let duplicate = br#"{"schema_version":1,"schema_version":1}"#;
    assert_eq!(
        compile_intent_package(duplicate)
            .expect_err("duplicate")
            .code(),
        DataErrorCode::DuplicateJsonKey
    );

    let mut value = parse_strict_json(SCHEMA_SOURCE, "schema").expect("schema");
    value
        .as_object_mut()
        .expect("schema object")
        .insert("unknown".into(), Value::Bool(true));
    let unknown = serde_json::to_vec(&value).expect("unknown schema");
    assert_eq!(
        compile_intent_package(&unknown)
            .expect_err("unknown field")
            .code(),
        DataErrorCode::InvalidRecord
    );

    assert_eq!(
        compile_intent_package(&vec![b' '; MAX_INTENT_SCHEMA_BYTES + 1])
            .expect_err("oversized schema")
            .code(),
        DataErrorCode::ResourceLimit
    );
}

#[test]
fn decoder_rejects_inner_framing_changes_with_recomputed_outer_hash() {
    let compiled = compile_intent_package(SCHEMA_SOURCE).expect("compile");
    let mut package = compiled.package_bytes().to_vec();
    package[7] = 2;
    let rebound = rebind_manifest(&package, compiled.manifest_bytes());
    assert_eq!(
        decode_intent_package(&package, &rebound, &identity())
            .expect_err("version mutation")
            .code(),
        DataErrorCode::InvalidRecord
    );

    let mut trailing = compiled.package_bytes().to_vec();
    trailing.push(0);
    let rebound = rebind_manifest(&trailing, compiled.manifest_bytes());
    assert_eq!(
        decode_intent_package(&trailing, &rebound, &identity())
            .expect_err("trailing mutation")
            .code(),
        DataErrorCode::InvalidRecord
    );
}

#[test]
fn decoder_rejects_identity_substitution_before_use() {
    let compiled = compile_intent_package(SCHEMA_SOURCE).expect("compile");
    let wrong = IntentPackageIdentity::new(
        "p09-intent-schema-v2",
        "p09-weighted-marker-slot-extraction-v1",
        INTENT_COMPILER_ID,
        "p09-intent-config-v1",
    );
    assert_eq!(
        decode_intent_package(compiled.package_bytes(), compiled.manifest_bytes(), &wrong,)
            .expect_err("identity substitution")
            .code(),
        DataErrorCode::IntegrityMismatch
    );
}

#[test]
fn schema_rejects_ambiguous_capture_identity() {
    let mut value = parse_strict_json(SCHEMA_SOURCE, "schema").expect("schema");
    let slots = value["intents"]
        .as_array_mut()
        .expect("intents")
        .iter_mut()
        .find(|intent| intent["external_intent"] == "HassTurnOn")
        .and_then(|intent| intent["slots"].as_array_mut())
        .expect("turn-on slots");
    slots[1]["occurrence"] = Value::from(0);

    let changed = serde_json::to_vec(&value).expect("changed schema");
    assert_eq!(
        compile_intent_package(&changed)
            .expect_err("ambiguous role/occurrence")
            .code(),
        DataErrorCode::InvalidRecord
    );
}
