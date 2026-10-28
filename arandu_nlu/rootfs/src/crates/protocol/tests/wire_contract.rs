use nlu_core::{
    AbstentionReason, CapabilityId, CatalogGeneration, Clarification, ClarificationOption,
    Confidence, EntityId, EntityRef, Hypothesis, IntentId, NodeId, OperationId, OptionId, Plan,
    PlanNode, Relation, RelationKind, RequestText, SemanticOutcome, Slot, SlotId, SlotValue,
};
use protocol::{
    MAX_DECODED_STRING_BYTES, MAX_NESTING_DEPTH, MAX_PROTOCOL_ERROR_BYTES, MAX_STRUCTURAL_ITEMS,
    MAX_WIRE_BYTES, ProtocolError,
    v1::{self, Outcome},
};

fn fixture_request() -> RequestText {
    RequestText::new("FIXTURE_TECNICA_A".into()).expect("request")
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

#[test]
fn round_trips_all_four_outcomes_with_stable_tags() {
    let source = fixture_request();
    let hypothesis = Hypothesis::new(
        &source,
        IntentId::new("fixture_tecnica:intent").expect("ID"),
        Confidence::from_basis_points(5_000).expect("score"),
        vec![source.span(0, source.len() as u64).expect("span")],
    )
    .expect("hypothesis");
    let clarification = Clarification::new(
        &source,
        vec![ClarificationOption::new(
            OptionId::new("fixture_tecnica:option").expect("ID"),
            hypothesis,
        )],
    )
    .expect("clarification");
    let outcomes = [
        Outcome::Plan(fixture_plan(&source)),
        Outcome::Clarification(clarification),
        Outcome::Abstention(AbstentionReason::InsufficientEvidence),
        Outcome::ProtocolError(ProtocolError::MalformedJson),
    ];

    for outcome in outcomes {
        let encoded = v1::encode_outcome(&outcome).expect("encode");
        assert_eq!(v1::decode_outcome(&encoded, &source), Ok(outcome));
    }
}

#[test]
fn emits_exact_canonical_plan_bytes() {
    let source = fixture_request();
    let outcome = SemanticOutcome::Plan(fixture_plan(&source));
    let encoded = v1::encode_semantic_outcome(&outcome).expect("encode");
    assert_eq!(
        encoded,
        br#"{"version":1,"outcome":{"type":"plan","catalog_generation":1,"nodes":[{"id":"fixture_tecnica:node","capability":"fixture_tecnica:capability","operation":"fixture_tecnica:operation","slots":[],"evidence":[{"start":0,"end":17}]}],"relations":[]}}"#
    );
}

#[test]
fn accepts_field_order_and_whitespace_permutations_then_canonicalizes() {
    let source = fixture_request();
    let permuted = br#"
      {
        "outcome": {
          "relations": [],
          "nodes": [{
            "evidence": [{"end": 17, "start": 0}],
            "slots": [],
            "operation": "fixture_tecnica:operation",
            "capability": "fixture_tecnica:capability",
            "id": "fixture_tecnica:node"
          }],
          "catalog_generation": 1,
          "type": "plan"
        },
        "version": 1
      }
    "#;
    let decoded = v1::decode_outcome(permuted, &source).expect("decode");
    let canonical = v1::encode_outcome(&decoded).expect("encode");
    assert_eq!(
        canonical,
        v1::encode_outcome(&Outcome::Plan(fixture_plan(&source))).expect("encode")
    );
}

#[test]
fn rejects_duplicate_unknown_missing_and_escaped_duplicate_fields() {
    let duplicate =
        br#"{"version":1,"request":{"text":"FIXTURE_TECNICA_A","text":"FIXTURE_TECNICA_B"}}"#;
    let escaped_duplicate =
        br#"{"version":1,"request":{"text":"FIXTURE_TECNICA_A","te\u0078t":"FIXTURE_TECNICA_B"}}"#;
    let unknown =
        br#"{"version":1,"request":{"text":"FIXTURE_TECNICA_A","unknown_fixture_tecnica":0}}"#;
    let missing = br#"{"request":{"text":"FIXTURE_TECNICA_A"}}"#;
    for bytes in [duplicate.as_slice(), escaped_duplicate, unknown, missing] {
        assert_eq!(v1::decode_request(bytes), Err(ProtocolError::MalformedJson));
    }
}

#[test]
fn rejects_bad_encoding_trailing_input_and_unsupported_versions() {
    assert_eq!(v1::decode_request(&[0xff]), Err(ProtocolError::InvalidUtf8));
    assert_eq!(
        v1::decode_request(br#"{"version":1,"request":{"text":"FIXTURE_TECNICA_A"}} trailing"#),
        Err(ProtocolError::MalformedJson)
    );
    assert_eq!(
        v1::decode_request(br#"{"version":2,"request":{"text":"FIXTURE_TECNICA_A"}}"#),
        Err(ProtocolError::UnsupportedVersion)
    );
    assert_eq!(
        v1::decode_request(
            b"\xef\xbb\xbf{\"version\":1,\"request\":{\"text\":\"FIXTURE_TECNICA_A\"}}"
        ),
        Err(ProtocolError::MalformedJson)
    );
}

#[test]
fn enforces_wire_string_depth_number_and_collection_boundaries() {
    assert_eq!(
        v1::decode_request(&vec![b' '; MAX_WIRE_BYTES + 1]),
        Err(ProtocolError::InputTooLarge)
    );

    let exact_text = "A".repeat(MAX_DECODED_STRING_BYTES);
    let exact_wire = format!(r#"{{"version":1,"request":{{"text":"{exact_text}"}}}}"#);
    assert!(v1::decode_request(exact_wire.as_bytes()).is_ok());
    let too_long_text = "A".repeat(MAX_DECODED_STRING_BYTES + 1);
    let too_long_wire = format!(r#"{{"version":1,"request":{{"text":"{too_long_text}"}}}}"#);
    assert_eq!(
        v1::decode_request(too_long_wire.as_bytes()),
        Err(ProtocolError::StringTooLarge)
    );

    let nested = format!(
        r#"{{"version":1,"request":{{"text":"FIXTURE_TECNICA_A","unknown":{}0{}}}}}"#,
        "[".repeat(MAX_NESTING_DEPTH),
        "]".repeat(MAX_NESTING_DEPTH)
    );
    assert_eq!(
        v1::decode_request(nested.as_bytes()),
        Err(ProtocolError::NestingTooDeep)
    );
    assert_eq!(
        v1::decode_request(br#"{"version":1.0,"request":{"text":"FIXTURE_TECNICA_A"}}"#),
        Err(ProtocolError::NonIntegerNumber)
    );

    let oversized_array = format!(
        r#"{{"version":1,"outcome":{{"type":"clarification","options":[{}]}}}}"#,
        vec!["false"; MAX_STRUCTURAL_ITEMS + 1].join(",")
    );
    assert_eq!(
        v1::decode_outcome(oversized_array.as_bytes(), &fixture_request()),
        Err(ProtocolError::StructuralLimitExceeded)
    );
}

#[test]
fn rejects_invalid_identifiers_spans_graphs_and_outcomes() {
    let source = fixture_request();
    let invalid_id = br#"{"version":1,"outcome":{"type":"plan","catalog_generation":1,"nodes":[{"id":"INVALID","capability":"fixture_tecnica:capability","operation":"fixture_tecnica:operation","slots":[],"evidence":[{"start":0,"end":17}]}],"relations":[]}}"#;
    assert_eq!(
        v1::decode_outcome(invalid_id, &source),
        Err(ProtocolError::InvalidIdentifier)
    );

    let invalid_span = br#"{"version":1,"outcome":{"type":"plan","catalog_generation":1,"nodes":[{"id":"fixture_tecnica:node","capability":"fixture_tecnica:capability","operation":"fixture_tecnica:operation","slots":[],"evidence":[{"start":17,"end":17}]}],"relations":[]}}"#;
    assert_eq!(
        v1::decode_outcome(invalid_span, &source),
        Err(ProtocolError::InvalidSpan)
    );

    let empty_plan = br#"{"version":1,"outcome":{"type":"plan","catalog_generation":1,"nodes":[],"relations":[]}}"#;
    assert_eq!(
        v1::decode_outcome(empty_plan, &source),
        Err(ProtocolError::EmptyPlan)
    );

    let unknown_tag = br#"{"version":1,"outcome":{"type":"fixture_tecnica_unknown"}}"#;
    assert_eq!(
        v1::decode_outcome(unknown_tag, &source),
        Err(ProtocolError::MalformedJson)
    );
}

#[test]
fn errors_never_echo_hostile_input() {
    let canary = "FIXTURE_TECNICA_PRIVATE_CANARY";
    let hostile = format!(
        r#"{{"version":1,"request":{{"text":"FIXTURE_TECNICA_A","{canary}":"{canary}"}}}}"#
    );
    let error = v1::decode_request(hostile.as_bytes()).expect_err("must reject");
    let encoded = v1::encode_protocol_error(error).expect("safe error encoding");
    assert!(!error.to_string().contains(canary));
    assert!(!format!("{error:?}").contains(canary));
    assert!(
        !String::from_utf8(encoded.clone())
            .expect("UTF-8")
            .contains(canary)
    );
    assert!(encoded.len() <= MAX_PROTOCOL_ERROR_BYTES);
}

#[test]
fn round_trips_every_slot_relation_and_abstention_variant() {
    let source = fixture_request();
    let generation = CatalogGeneration::new(1).expect("generation");
    let first_id = NodeId::new("fixture_tecnica:node_a").expect("ID");
    let second_id = NodeId::new("fixture_tecnica:node_b").expect("ID");
    let first = PlanNode::new(
        first_id.clone(),
        CapabilityId::new("fixture_tecnica:capability").expect("ID"),
        OperationId::new("fixture_tecnica:operation").expect("ID"),
        vec![
            Slot::new(
                SlotId::new("fixture_tecnica:boolean").expect("ID"),
                SlotValue::Boolean(true),
            ),
            Slot::new(
                SlotId::new("fixture_tecnica:entity").expect("ID"),
                SlotValue::Entity(EntityRef::new(
                    EntityId::new("fixture_tecnica:entity_a").expect("ID"),
                    generation,
                )),
            ),
            Slot::new(
                SlotId::new("fixture_tecnica:integer").expect("ID"),
                SlotValue::Integer(-7),
            ),
            Slot::new(
                SlotId::new("fixture_tecnica:text").expect("ID"),
                SlotValue::EvidenceText(
                    source.span(0, source.len() as u64).expect("evidence span"),
                ),
            ),
        ],
        vec![source.span(0, 1).expect("span")],
    )
    .expect("node");
    let second = PlanNode::new(
        second_id.clone(),
        CapabilityId::new("fixture_tecnica:capability").expect("ID"),
        OperationId::new("fixture_tecnica:operation").expect("ID"),
        Vec::new(),
        vec![source.span(1, 2).expect("span")],
    )
    .expect("node");
    let plan = Plan::new(
        &source,
        generation,
        vec![second, first],
        vec![
            Relation::new(first_id.clone(), second_id.clone(), RelationKind::Precedes),
            Relation::new(first_id, second_id, RelationKind::Requires),
        ],
    )
    .expect("plan");
    let outcome = Outcome::Plan(plan);
    let encoded = v1::encode_outcome(&outcome).expect("encode");
    assert_eq!(v1::decode_outcome(&encoded, &source), Ok(outcome));

    for reason in [
        AbstentionReason::InsufficientEvidence,
        AbstentionReason::Ambiguous,
        AbstentionReason::Unsupported,
    ] {
        let outcome = Outcome::Abstention(reason);
        let encoded = v1::encode_outcome(&outcome).expect("encode");
        assert_eq!(v1::decode_outcome(&encoded, &source), Ok(outcome));
    }
}

#[test]
fn every_closed_protocol_error_variant_is_bounded_and_round_trips() {
    let source = fixture_request();
    let errors = [
        ProtocolError::InvalidUtf8,
        ProtocolError::InputTooLarge,
        ProtocolError::StringTooLarge,
        ProtocolError::NestingTooDeep,
        ProtocolError::StructuralLimitExceeded,
        ProtocolError::NumericTokenTooLong,
        ProtocolError::NonIntegerNumber,
        ProtocolError::MalformedJson,
        ProtocolError::UnsupportedVersion,
        ProtocolError::InvalidIdentifier,
        ProtocolError::InvalidSpan,
        ProtocolError::InvalidGraph,
        ProtocolError::EmptyPlan,
        ProtocolError::InvalidOutcome,
        ProtocolError::EncodingFailure,
    ];
    for error in errors {
        let outcome = Outcome::ProtocolError(error);
        let encoded = v1::encode_outcome(&outcome).expect("encode");
        assert!(encoded.len() <= MAX_PROTOCOL_ERROR_BYTES);
        assert_eq!(v1::decode_outcome(&encoded, &source), Ok(outcome));
    }
}
