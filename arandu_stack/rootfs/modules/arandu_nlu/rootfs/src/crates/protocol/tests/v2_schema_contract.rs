use protocol::v2;
use serde::{Deserialize, de::IgnoredAny};

// FIXTURE_TECNICA: schema-only structural assertions.
const REQUEST_SCHEMA: &str = include_str!("../../../schemas/protocol-v2-request.schema.json");
const RESPONSE_SCHEMA: &str = include_str!("../../../schemas/protocol-v2-response.schema.json");

#[derive(Deserialize)]
struct SchemaIdentity {
    #[serde(rename = "$schema")]
    dialect: String,
    #[serde(rename = "$id")]
    id: String,
    #[serde(rename = "x-protocolVersion")]
    protocol_version: u16,
    #[serde(rename = "x-maxWireBytes")]
    max_wire_bytes: usize,
    #[serde(rename = "x-maxDecodedStringBytes")]
    max_decoded_string_bytes: usize,
    #[serde(rename = "x-maxNestingDepth")]
    max_nesting_depth: usize,
    #[serde(rename = "x-maxStructuralItems")]
    max_structural_items: usize,
    #[serde(rename = "x-maxIntegerTokenBytes")]
    max_integer_token_bytes: usize,
}

#[derive(Deserialize)]
struct ResponseSchemaDiagnostics {
    #[serde(rename = "$defs")]
    definitions: DiagnosticDefinitions,
}

#[derive(Deserialize)]
struct DiagnosticDefinitions {
    #[serde(rename = "diagnosticNoLimit")]
    no_limit: NoLimitDefinition,
    #[serde(rename = "wireByteDiagnostic")]
    wire_bytes: LimitDefinition,
    #[serde(rename = "stringByteDiagnostic")]
    string_bytes: LimitDefinition,
    #[serde(rename = "structuralItemDiagnostic")]
    structural_items: LimitDefinition,
    #[serde(rename = "diagnosticLimitDiagnostic")]
    diagnostics: LimitDefinition,
}

#[derive(Deserialize)]
struct NoLimitDefinition {
    properties: NoLimitProperties,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NoLimitProperties {
    #[serde(rename = "code")]
    _code: IgnoredAny,
    #[serde(rename = "limit")]
    _limit: IgnoredAny,
    #[serde(rename = "node")]
    _node: IgnoredAny,
}

#[derive(Deserialize)]
struct LimitDefinition {
    properties: LimitProperties,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LimitProperties {
    #[serde(rename = "code")]
    _code: IgnoredAny,
    limit: ConstantLimit,
}

#[derive(Deserialize)]
struct ConstantLimit {
    #[serde(rename = "const")]
    value: usize,
}

#[test]
fn schemas_are_versioned_strict_and_bound_to_v2_preflight_limits() {
    for (schema, expected_id) in [
        (REQUEST_SCHEMA, "urn:nlu-ptbr:protocol:v2:request"),
        (RESPONSE_SCHEMA, "urn:nlu-ptbr:protocol:v2:response"),
    ] {
        let identity: SchemaIdentity = serde_json::from_str(schema).expect("valid schema JSON");
        assert_eq!(
            identity.dialect,
            "https://json-schema.org/draft/2020-12/schema"
        );
        assert_eq!(identity.id, expected_id);
        assert_eq!(identity.protocol_version, v2::VERSION);
        assert_eq!(identity.max_wire_bytes, v2::MAX_WIRE_BYTES);
        assert_eq!(
            identity.max_decoded_string_bytes,
            v2::MAX_DECODED_STRING_BYTES
        );
        assert_eq!(identity.max_nesting_depth, v2::MAX_NESTING_DEPTH);
        assert_eq!(identity.max_structural_items, v2::MAX_STRUCTURAL_ITEMS);
        assert_eq!(
            identity.max_integer_token_bytes,
            v2::MAX_NUMERIC_TOKEN_BYTES
        );
        assert!(schema.contains(r#""additionalProperties": false"#));
        assert!(schema.contains(r#""const": 2"#));
    }
}

#[test]
fn request_schema_is_closed_to_the_five_authority_free_variants() {
    for tag in ["interpret", "continue", "confirm", "cancel", "health"] {
        assert!(
            REQUEST_SCHEMA.contains(&format!(r#""const": "{tag}""#)),
            "missing request tag {tag}"
        );
    }
    for prohibited in [
        "caller",
        "permission",
        "risk",
        "service",
        "payload",
        concat!("creden", "tial"),
        "authorization",
    ] {
        assert!(
            !REQUEST_SCHEMA.contains(prohibited),
            "prohibited request field {prohibited}"
        );
    }
    for fragment in [
        r#""pattern": "^[0-9a-f]{64}$""#,
        r#""x-decodedBytes": 32"#,
        r#""x-maxUtf8Bytes": 16384"#,
        r#""maximum": 18446744073709551615"#,
    ] {
        assert!(REQUEST_SCHEMA.contains(fragment), "missing {fragment}");
    }
}

#[test]
fn response_schema_covers_complete_semantics_and_every_closed_outcome() {
    for tag in [
        "complete_plan",
        "entity_clarification",
        "abstention",
        "policy_denial",
        "confirmation_required",
        "policy_accepted",
        "cancellation",
        "health",
        "protocol_error",
    ] {
        assert!(
            RESPONSE_SCHEMA.contains(&format!(r#""const": "{tag}""#)),
            "missing response tag {tag}"
        );
    }
    for field in [
        "catalog_generation",
        "execution_class",
        "nodes",
        "relations",
        "clauses",
        "relation_evidence",
        "independent_pairs",
        "argument_shares",
        "capability",
        "operation",
        "slots",
        "evidence",
        "intent",
        "polarity",
    ] {
        assert!(
            RESPONSE_SCHEMA.contains(&format!(r#""{field}""#)),
            "missing composed-plan field {field}"
        );
    }
    for fragment in [
        r#""continuation": {"#,
        r#""const": true"#,
        r#""authorizes_execution": {"#,
        r#""const": false"#,
        r#""const": ["#,
        r#""maxItems": 16"#,
        r#""maxItems": 64"#,
        r#""maxItems": 256"#,
        r#""maxItems": 32"#,
        r#""x-maxAggregateItems": 4096"#,
        r#""x-maxDiagnostics": 8"#,
        r#""x-maxEncodedBytes": 256"#,
    ] {
        assert!(RESPONSE_SCHEMA.contains(fragment), "missing {fragment}");
    }
}

#[test]
fn diagnostic_schema_allows_only_closed_codes_optional_node_and_fixed_limit() {
    let schema: ResponseSchemaDiagnostics =
        serde_json::from_str(RESPONSE_SCHEMA).expect("response schema");
    let definitions = schema.definitions;
    let _closed_no_limit_shape = definitions.no_limit.properties;
    assert_eq!(
        definitions.wire_bytes.properties.limit.value,
        v2::MAX_WIRE_BYTES
    );
    assert_eq!(
        definitions.string_bytes.properties.limit.value,
        v2::MAX_DECODED_STRING_BYTES
    );
    assert_eq!(
        definitions.structural_items.properties.limit.value,
        v2::MAX_STRUCTURAL_ITEMS
    );
    assert_eq!(
        definitions.diagnostics.properties.limit.value,
        v2::MAX_DIAGNOSTICS
    );
    for prohibited in [
        "\"message\"",
        "\"text\"",
        "\"path\"",
        "\"timestamp\"",
        "\"value\"",
        "\"caller\"",
        concat!("\"creden", "tial\""),
    ] {
        let diagnostic_region = RESPONSE_SCHEMA
            .split(r#""diagnosticNoLimit""#)
            .nth(1)
            .expect("diagnostic region")
            .split(r#""completePlanOutcome""#)
            .next()
            .expect("bounded diagnostic region");
        assert!(
            !diagnostic_region.contains(prohibited),
            "diagnostics contain {prohibited}"
        );
    }
}
