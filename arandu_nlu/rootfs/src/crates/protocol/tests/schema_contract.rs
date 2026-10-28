use nlu_core::{
    AbstentionReason, CapabilityId, CatalogGeneration, Clarification, ClarificationOption,
    Confidence, Hypothesis, IntentId, NodeId, OperationId, OptionId, Plan, PlanNode, RequestText,
};
use protocol::{ProtocolError, v1};
use serde::Deserialize;

const REQUEST_SCHEMA: &str = include_str!("../../../schemas/protocol-v1-request.schema.json");
const RESPONSE_SCHEMA: &str = include_str!("../../../schemas/protocol-v1-response.schema.json");

const REQUEST_FIXTURE: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_request-v1.json");
const PLAN_FIXTURE: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_response-plan-v1.json");
const CLARIFICATION_FIXTURE: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-clarification-v1.json");
const ABSTENTION_FIXTURE: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-abstention-v1.json");
const ERROR_FIXTURE: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-protocol-error-v1.json");

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
}

fn fixture_request() -> RequestText {
    RequestText::new("FIXTURE_TECNICA_A".into()).expect("request")
}

fn fixture_payload(file: &'static [u8]) -> &'static [u8] {
    file.strip_suffix(b"\n")
        .expect("tracked JSON fixture has one text-file terminator")
}

fn fixture_plan(source: &RequestText) -> Plan {
    let node = PlanNode::new(
        NodeId::new("fixture_tecnica:node").expect("ID"),
        CapabilityId::new("fixture_tecnica:capability").expect("ID"),
        OperationId::new("fixture_tecnica:operation").expect("ID"),
        Vec::new(),
        vec![source.span(0, source.len() as u64).expect("span")],
    )
    .expect("node");
    Plan::new(
        source,
        CatalogGeneration::new(1).expect("generation"),
        vec![node],
        Vec::new(),
    )
    .expect("plan")
}

fn fixture_clarification(source: &RequestText) -> Clarification {
    let hypothesis = Hypothesis::new(
        source,
        IntentId::new("fixture_tecnica:intent").expect("ID"),
        Confidence::from_basis_points(5_000).expect("score"),
        vec![source.span(0, source.len() as u64).expect("span")],
    )
    .expect("hypothesis");
    Clarification::new(
        source,
        vec![ClarificationOption::new(
            OptionId::new("fixture_tecnica:option").expect("ID"),
            hypothesis,
        )],
    )
    .expect("clarification")
}

#[test]
fn schemas_are_versioned_strict_and_bound_to_wire_limits() {
    for (schema, expected_id) in [
        (REQUEST_SCHEMA, "urn:nlu-ptbr:protocol:v1:request"),
        (RESPONSE_SCHEMA, "urn:nlu-ptbr:protocol:v1:response"),
    ] {
        let identity: SchemaIdentity = serde_json::from_str(schema).expect("valid schema JSON");
        assert_eq!(
            identity.dialect,
            "https://json-schema.org/draft/2020-12/schema"
        );
        assert_eq!(identity.id, expected_id);
        assert_eq!(identity.protocol_version, v1::VERSION);
        assert_eq!(identity.max_wire_bytes, protocol::MAX_WIRE_BYTES);
        assert!(schema.contains(r#""additionalProperties": false"#));
        assert!(schema.contains(r#""const": 1"#));
    }
}

#[test]
fn canonical_fixtures_match_implementation_bytes_and_schema_tags() {
    let source = fixture_request();
    let request_fixture = fixture_payload(REQUEST_FIXTURE);
    assert_eq!(v1::decode_request(request_fixture), Ok(source.clone()));
    assert_eq!(v1::encode_request(&source), Ok(request_fixture.to_vec()));

    let outcomes = [
        (
            v1::Outcome::Plan(fixture_plan(&source)),
            fixture_payload(PLAN_FIXTURE),
            r#""const": "plan""#,
        ),
        (
            v1::Outcome::Clarification(fixture_clarification(&source)),
            fixture_payload(CLARIFICATION_FIXTURE),
            r#""const": "clarification""#,
        ),
        (
            v1::Outcome::Abstention(AbstentionReason::InsufficientEvidence),
            fixture_payload(ABSTENTION_FIXTURE),
            r#""const": "abstention""#,
        ),
        (
            v1::Outcome::ProtocolError(ProtocolError::InputTooLarge),
            fixture_payload(ERROR_FIXTURE),
            r#""const": "protocol_error""#,
        ),
    ];
    for (outcome, fixture, schema_tag) in outcomes {
        assert_eq!(v1::decode_outcome(fixture, &source), Ok(outcome.clone()));
        assert_eq!(v1::encode_outcome(&outcome), Ok(fixture.to_vec()));
        assert!(RESPONSE_SCHEMA.contains(schema_tag));
    }
}

#[test]
fn schema_declares_every_enforced_foundation_limit() {
    let required_fragments = [
        r#""x-maxWireBytes": 65536"#,
        r#""x-maxNestingDepth": 32"#,
        r#""x-maxStructuralItems": 4096"#,
        r#""maxLength": 128"#,
        r#""maxItems": 64"#,
        r#""maxItems": 256"#,
        r#""maxItems": 32"#,
        r#""maxItems": 16"#,
        r#""maximum": 10000"#,
        r#""x-maxEncodedBytes": 256"#,
    ];
    for fragment in required_fragments {
        assert!(RESPONSE_SCHEMA.contains(fragment), "missing {fragment}");
    }
    assert!(REQUEST_SCHEMA.contains(r#""x-maxUtf8Bytes": 16384"#));
}
