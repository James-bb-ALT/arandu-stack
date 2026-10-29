use std::{collections::BTreeSet, fs, path::PathBuf};

use serde_json::Value;

#[test]
fn evaluation_schemas_are_closed_and_match_frozen_outputs() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let manifest = read_json(&root, "data/evaluation/p08/pos-v1/manifest.json");
    let report = read_json(&root, "data/evaluation/p08/pos-v1/report.json");
    let manifest_schema = read_json(&root, "schemas/pos-evaluation-manifest-v1.schema.json");
    let report_schema = read_json(&root, "schemas/pos-evaluation-report-v1.schema.json");

    assert_top_level_contract(
        &manifest_schema,
        "https://nlu.local/schemas/pos-evaluation-manifest-v1.schema.json",
        &manifest,
    );
    assert_top_level_contract(
        &report_schema,
        "https://nlu.local/schemas/pos-evaluation-report-v1.schema.json",
        &report,
    );
    assert_all_typed_objects_closed(&manifest_schema);
    assert_all_typed_objects_closed(&report_schema);

    for (field, schema) in manifest_schema["properties"]
        .as_object()
        .expect("manifest schema properties")
    {
        if let Some(expected) = schema.get("const") {
            assert_eq!(&manifest[field], expected, "manifest constant {field}");
        }
    }
    for field in ["heldout", "model"] {
        let definition =
            reference_definition(&manifest_schema, &manifest_schema["properties"][field]);
        assert_object_shape(definition, &manifest[field]);
        for (nested, schema) in definition["properties"]
            .as_object()
            .expect("nested manifest properties")
        {
            assert_eq!(
                &manifest[field][nested], &schema["const"],
                "manifest constant {field}.{nested}"
            );
        }
    }
    assert_eq!(
        manifest["limitations"],
        manifest_schema["$defs"]["limitations"]["const"]
    );

    for (field, schema) in report_schema["properties"]
        .as_object()
        .expect("report schema properties")
    {
        if let Some(expected) = schema.get("const") {
            assert_eq!(&report[field], expected, "report constant {field}");
        }
    }
    assert_eq!(
        report["limitations"],
        report_schema["$defs"]["limitations"]["const"]
    );
    assert_eq!(
        report["results"]["baseline"],
        report_schema["$defs"]["results"]["properties"]["baseline"]["allOf"][1]["const"]
    );
    assert_eq!(
        report["results"]["selected"],
        report_schema["$defs"]["results"]["properties"]["selected"]["allOf"][1]["const"]
    );
    for (field, schema) in report_schema["$defs"]["baselineDelta"]["properties"]
        .as_object()
        .expect("delta properties")
    {
        assert_eq!(
            &report["baseline_delta"][field], &schema["const"],
            "delta constant {field}"
        );
    }
}

#[test]
fn split_and_model_schemas_are_closed_and_match_frozen_manifests() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let split = read_json(
        &root,
        "data/evaluation/p08/pos-v1/splits/split-manifest.json",
    );
    let model = read_json(&root, "data/pos/p08/package-manifest.json");
    let split_schema = read_json(&root, "schemas/pos-split-manifest-v1.schema.json");
    let model_schema = read_json(&root, "schemas/pos-model-manifest-v1.schema.json");

    assert_top_level_contract(
        &split_schema,
        "https://nlu.local/schemas/pos-split-manifest-v1.schema.json",
        &split,
    );
    assert_top_level_contract(
        &model_schema,
        "https://nlu.local/schemas/pos-model-manifest-v1.schema.json",
        &model,
    );
    assert_all_typed_objects_closed(&split_schema);
    assert_all_typed_objects_closed(&model_schema);

    for field in ["digest_contract", "source", "partition"] {
        let schema = &split_schema["properties"][field];
        assert_object_shape(schema, &split[field]);
        assert_declared_constants_match(schema, &split[field]);
    }
    let split_item_schema = &split_schema["$defs"]["split"];
    for entry in split["splits"].as_array().expect("split entries") {
        assert_object_shape(split_item_schema, entry);
        assert_declared_constants_match(split_item_schema, entry);
    }

    for field in ["identity", "source", "training"] {
        let schema = &model_schema["properties"][field];
        assert_object_shape(schema, &model[field]);
        assert_declared_constants_match(schema, &model[field]);
    }
    assert_declared_constants_match(&model_schema, &model);
}

fn read_json(root: &std::path::Path, relative: &str) -> Value {
    serde_json::from_slice(&fs::read(root.join(relative)).expect("read JSON")).expect("parse JSON")
}

fn assert_top_level_contract(schema: &Value, id: &str, emitted: &Value) {
    assert_eq!(
        schema["$schema"],
        "https://json-schema.org/draft/2020-12/schema"
    );
    assert_eq!(schema["$id"], id);
    assert_eq!(schema["type"], "object");
    assert_eq!(schema["additionalProperties"], false);
    assert_eq!(required_fields(schema), object_keys(emitted));
    assert_eq!(property_fields(schema), object_keys(emitted));
}

fn assert_object_shape(schema: &Value, emitted: &Value) {
    assert_eq!(schema["type"], "object");
    assert_eq!(schema["additionalProperties"], false);
    assert_eq!(required_fields(schema), object_keys(emitted));
    assert_eq!(property_fields(schema), object_keys(emitted));
}

fn assert_declared_constants_match(schema: &Value, emitted: &Value) {
    for (field, property) in schema["properties"].as_object().expect("schema properties") {
        if let Some(expected) = property.get("const") {
            assert_eq!(&emitted[field], expected, "constant {field}");
        }
    }
}

fn assert_all_typed_objects_closed(value: &Value) {
    match value {
        Value::Object(object) => {
            if object.get("type") == Some(&Value::String("object".to_owned())) {
                assert_eq!(
                    object.get("additionalProperties"),
                    Some(&Value::Bool(false)),
                    "typed schema object is open"
                );
            }
            for child in object.values() {
                assert_all_typed_objects_closed(child);
            }
        }
        Value::Array(values) => {
            for child in values {
                assert_all_typed_objects_closed(child);
            }
        }
        _ => {}
    }
}

fn reference_definition<'a>(root: &'a Value, reference: &Value) -> &'a Value {
    let name = reference["$ref"]
        .as_str()
        .expect("definition reference")
        .strip_prefix("#/$defs/")
        .expect("local definition reference");
    &root["$defs"][name]
}

fn required_fields(schema: &Value) -> BTreeSet<String> {
    schema["required"]
        .as_array()
        .expect("required fields")
        .iter()
        .map(|field| field.as_str().expect("required field").to_owned())
        .collect()
}

fn property_fields(schema: &Value) -> BTreeSet<String> {
    schema["properties"]
        .as_object()
        .expect("schema properties")
        .keys()
        .cloned()
        .collect()
}

fn object_keys(value: &Value) -> BTreeSet<String> {
    value
        .as_object()
        .expect("emitted object")
        .keys()
        .cloned()
        .collect()
}
