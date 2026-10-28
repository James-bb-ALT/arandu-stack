use nlu_core::{
    AbstentionReason, ArgumentEndpoint, ArgumentShare, CapabilityId, CatalogGeneration,
    ClauseSemantics, ComposedPlan, EntityId, EntityRef, EvidenceAtom, EvidenceKind,
    GraphExecutionClass, IndependentPair, IntentId, NodeId, OperationId, Plan, PlanNode, Polarity,
    Relation, RelationEvidence, RelationKind, RequestText, Slot, SlotId, SlotValue,
};
use protocol::{
    ProtocolError, RequestVersion, detect_request_version,
    v2::{
        self, CancellationStatus, ConfirmationId, ConfirmationRequired, Diagnostic, DiagnosticCode,
        EntityClarification, Generation, Health, Outcome, PolicyAccepted, PolicyDenialReason,
        Readiness, Request, Response, RiskClass, SessionId,
    },
};

const REQUEST_INTERPRET: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_request-interpret-v2.json");
const REQUEST_CONTINUE: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_request-continue-v2.json");
const REQUEST_CONFIRM: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_request-confirm-v2.json");
const REQUEST_CANCEL: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_request-cancel-v2.json");
const REQUEST_HEALTH: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_request-health-v2.json");

const RESPONSE_COMPLETE_PLAN: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-complete-plan-v2.json");
const RESPONSE_ENTITY_CLARIFICATION: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-entity-clarification-v2.json");
const RESPONSE_ABSTENTION: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-abstention-v2.json");
const RESPONSE_POLICY_DENIAL: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-policy-denial-v2.json");
const RESPONSE_CONFIRMATION_REQUIRED: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-confirmation-required-v2.json");
const RESPONSE_POLICY_ACCEPTED: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-policy-accepted-v2.json");
const RESPONSE_CANCELLATION: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-cancellation-v2.json");
const RESPONSE_HEALTH: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_response-health-v2.json");
const RESPONSE_PROTOCOL_ERROR: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-protocol-error-v2.json");

fn payload(file: &'static [u8]) -> &'static [u8] {
    file.strip_suffix(b"\n")
        .expect("tracked JSON fixture has one text-file terminator")
}

fn replace_exact(input: &[u8], needle: &[u8], replacement: &[u8]) -> Vec<u8> {
    let positions = input
        .windows(needle.len())
        .enumerate()
        .filter_map(|(index, window)| (window == needle).then_some(index))
        .collect::<Vec<_>>();
    assert_eq!(
        positions.len(),
        1,
        "FIXTURE_TECNICA mutation target must be unique"
    );
    let index = positions[0];
    let mut output = Vec::with_capacity(input.len() - needle.len() + replacement.len());
    output.extend_from_slice(&input[..index]);
    output.extend_from_slice(replacement);
    output.extend_from_slice(&input[index + needle.len()..]);
    output
}

fn repeated_json_array(item: &[u8], count: usize) -> Vec<u8> {
    let mut output = Vec::with_capacity(2 + count.saturating_mul(item.len() + 1));
    output.push(b'[');
    for index in 0..count {
        if index != 0 {
            output.push(b',');
        }
        output.extend_from_slice(item);
    }
    output.push(b']');
    output
}

fn replace_json_array_field(input: &[u8], field: &[u8], replacement: &[u8]) -> Vec<u8> {
    let mut marker = Vec::with_capacity(field.len() + 4);
    marker.push(b'"');
    marker.extend_from_slice(field);
    marker.extend_from_slice(b"\":[");
    let positions = input
        .windows(marker.len())
        .enumerate()
        .filter_map(|(index, window)| (window == marker).then_some(index))
        .collect::<Vec<_>>();
    assert_eq!(
        positions.len(),
        1,
        "FIXTURE_TECNICA array field must be unique"
    );
    let array_start = positions[0] + marker.len() - 1;
    let mut depth = 0_usize;
    let mut in_string = false;
    let mut escaped = false;
    let mut array_end = None;
    for (offset, byte) in input[array_start..].iter().copied().enumerate() {
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
            continue;
        }
        match byte {
            b'"' => in_string = true,
            b'[' => depth += 1,
            b']' => {
                depth -= 1;
                if depth == 0 {
                    array_end = Some(array_start + offset + 1);
                    break;
                }
            }
            _ => {}
        }
    }
    let array_end = array_end.expect("FIXTURE_TECNICA complete array");
    let mut output =
        Vec::with_capacity(input.len() - (array_end - array_start) + replacement.len());
    output.extend_from_slice(&input[..array_start]);
    output.extend_from_slice(replacement);
    output.extend_from_slice(&input[array_end..]);
    output
}

fn session() -> SessionId {
    SessionId::from_bytes([0_u8; v2::SESSION_ID_BYTES])
}

fn source() -> RequestText {
    RequestText::new("FIXTURE_TECNICA_A".into()).expect("FIXTURE_TECNICA source")
}

fn generation(value: u64) -> CatalogGeneration {
    CatalogGeneration::new(value).expect("FIXTURE_TECNICA generation")
}

fn entity(id: &str, generation_value: u64) -> EntityRef {
    EntityRef::new(
        EntityId::new(id).expect("FIXTURE_TECNICA entity ID"),
        generation(generation_value),
    )
}

fn simple_plan(source: &RequestText) -> ComposedPlan {
    let node_id = NodeId::new("fixture_tecnica:node").expect("FIXTURE_TECNICA node ID");
    let span = source
        .span(0, source.len() as u64)
        .expect("FIXTURE_TECNICA full span");
    let node = PlanNode::new(
        node_id.clone(),
        CapabilityId::new("fixture_tecnica:capability").expect("FIXTURE_TECNICA capability"),
        OperationId::new("fixture_tecnica:operation").expect("FIXTURE_TECNICA operation"),
        Vec::new(),
        vec![span.clone()],
    )
    .expect("FIXTURE_TECNICA node");
    let plan =
        Plan::new(source, generation(1), vec![node], Vec::new()).expect("FIXTURE_TECNICA plan");
    let clause = ClauseSemantics::new(
        node_id,
        IntentId::new("fixture_tecnica:intent").expect("FIXTURE_TECNICA intent"),
        Polarity::Affirmed,
        vec![EvidenceAtom::new(EvidenceKind::Predicate, span)],
    )
    .expect("FIXTURE_TECNICA clause");
    ComposedPlan::new(
        source,
        plan,
        GraphExecutionClass::PartialSafe,
        vec![clause],
        Vec::new(),
        Vec::new(),
        Vec::new(),
    )
    .expect("FIXTURE_TECNICA composed plan")
}

fn rich_plan(source: &RequestText) -> ComposedPlan {
    let node_a = NodeId::new("fixture_tecnica:node_a").expect("node A");
    let node_b = NodeId::new("fixture_tecnica:node_b").expect("node B");
    let node_c = NodeId::new("fixture_tecnica:node_c").expect("node C");
    let capability = CapabilityId::new("fixture_tecnica:capability").expect("capability");
    let operation = OperationId::new("fixture_tecnica:operation").expect("operation");
    let boolean_slot = SlotId::new("fixture_tecnica:boolean").expect("boolean slot");
    let entity_slot = SlotId::new("fixture_tecnica:entity").expect("entity slot");
    let shared_slot = SlotId::new("fixture_tecnica:shared").expect("shared slot");
    let text_slot = SlotId::new("fixture_tecnica:text").expect("text slot");

    let predicate_a = source.span(0, 1).expect("predicate A");
    let argument_a = source.span(1, 2).expect("argument A");
    let predicate_b = source.span(2, 3).expect("predicate B");
    let predicate_c = source.span(3, 4).expect("predicate C");
    let relation_ab_span = source.span(4, 5).expect("relation AB");
    let relation_bc_span = source.span(5, 6).expect("relation BC");
    let share_span = source.span(6, 7).expect("share");

    let first = PlanNode::new(
        node_a.clone(),
        capability.clone(),
        operation.clone(),
        vec![
            Slot::new(boolean_slot.clone(), SlotValue::Boolean(true)),
            Slot::new(
                entity_slot.clone(),
                SlotValue::Entity(entity("fixture_tecnica:entity", 7)),
            ),
            Slot::new(shared_slot.clone(), SlotValue::Integer(7)),
            Slot::new(
                text_slot.clone(),
                SlotValue::EvidenceText(argument_a.clone()),
            ),
        ],
        vec![predicate_a.clone(), argument_a.clone()],
    )
    .expect("first node");
    let second = PlanNode::new(
        node_b.clone(),
        capability.clone(),
        operation.clone(),
        vec![Slot::new(shared_slot.clone(), SlotValue::Integer(7))],
        vec![predicate_b.clone()],
    )
    .expect("second node");
    let third = PlanNode::new(
        node_c.clone(),
        capability,
        operation,
        Vec::new(),
        vec![predicate_c.clone()],
    )
    .expect("third node");

    let relation_ab = Relation::new(node_a.clone(), node_b.clone(), RelationKind::Precedes);
    let relation_bc = Relation::new(node_b.clone(), node_c.clone(), RelationKind::Requires);
    let plan = Plan::new(
        source,
        generation(7),
        vec![third, second, first],
        vec![relation_bc.clone(), relation_ab.clone()],
    )
    .expect("rich plan");

    let clause_a = ClauseSemantics::new(
        node_a.clone(),
        IntentId::new("fixture_tecnica:intent_a").expect("intent A"),
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, predicate_a),
            EvidenceAtom::new(EvidenceKind::Argument(boolean_slot), argument_a.clone()),
            EvidenceAtom::new(EvidenceKind::Argument(entity_slot), argument_a.clone()),
            EvidenceAtom::new(
                EvidenceKind::Argument(shared_slot.clone()),
                argument_a.clone(),
            ),
            EvidenceAtom::new(EvidenceKind::Argument(text_slot), argument_a),
        ],
    )
    .expect("clause A");
    let clause_b = ClauseSemantics::new(
        node_b.clone(),
        IntentId::new("fixture_tecnica:intent_b").expect("intent B"),
        Polarity::Affirmed,
        vec![EvidenceAtom::new(EvidenceKind::Predicate, predicate_b)],
    )
    .expect("clause B");
    let clause_c = ClauseSemantics::new(
        node_c.clone(),
        IntentId::new("fixture_tecnica:intent_c").expect("intent C"),
        Polarity::Affirmed,
        vec![EvidenceAtom::new(EvidenceKind::Predicate, predicate_c)],
    )
    .expect("clause C");
    let relation_evidence = vec![
        RelationEvidence::new(relation_bc, vec![relation_bc_span]).expect("relation BC evidence"),
        RelationEvidence::new(relation_ab, vec![relation_ab_span]).expect("relation AB evidence"),
    ];
    let independent = IndependentPair::new(node_a.clone(), node_c).expect("independent pair A-C");
    let share = ArgumentShare::new(
        ArgumentEndpoint::new(node_a, shared_slot.clone()),
        ArgumentEndpoint::new(node_b, shared_slot),
        vec![share_span],
    )
    .expect("argument share");

    ComposedPlan::new(
        source,
        plan,
        GraphExecutionClass::AtomicOnly,
        vec![clause_c, clause_b, clause_a],
        relation_evidence,
        vec![independent],
        vec![share],
    )
    .expect("rich composed plan")
}

#[test]
fn canonical_request_fixtures_cover_every_closed_variant() {
    let confirmation_source = source();
    let confirmation_plan = simple_plan(&confirmation_source);
    let expected = vec![
        (
            Request::Interpret {
                session_id: session(),
                text: source(),
            },
            payload(REQUEST_INTERPRET),
        ),
        (
            Request::Continue {
                session_id: session(),
                selection: entity("fixture_tecnica:entity", 1),
            },
            payload(REQUEST_CONTINUE),
        ),
        (
            Request::Confirm {
                session_id: session(),
                confirmation_id: ConfirmationId::new(1).expect("confirmation ID"),
                plan: confirmation_plan,
            },
            payload(REQUEST_CONFIRM),
        ),
        (
            Request::Cancel {
                session_id: session(),
            },
            payload(REQUEST_CANCEL),
        ),
        (Request::Health, payload(REQUEST_HEALTH)),
    ];

    for (request, fixture) in expected {
        assert_eq!(v2::decode_request(fixture), Ok(request.clone()));
        assert_eq!(v2::encode_request(&request), Ok(fixture.to_vec()));
    }
}

#[test]
fn canonical_response_fixtures_cover_every_closed_variant() {
    let source = source();
    let plan = simple_plan(&source);
    let expected = vec![
        (
            Response::without_diagnostics(Outcome::CompletePlan(plan.clone())),
            payload(RESPONSE_COMPLETE_PLAN),
        ),
        (
            Response::without_diagnostics(Outcome::EntityClarification(
                EntityClarification::new(session(), vec![entity("fixture_tecnica:entity", 1)])
                    .expect("clarification"),
            )),
            payload(RESPONSE_ENTITY_CLARIFICATION),
        ),
        (
            Response::without_diagnostics(Outcome::Abstention(
                AbstentionReason::InsufficientEvidence,
            )),
            payload(RESPONSE_ABSTENTION),
        ),
        (
            Response::new(
                Outcome::PolicyDenial(PolicyDenialReason::ExplicitDeny),
                vec![Diagnostic::new(
                    DiagnosticCode::PolicyDenied,
                    Some(NodeId::new("fixture_tecnica:node").expect("node")),
                )],
            )
            .expect("denial"),
            payload(RESPONSE_POLICY_DENIAL),
        ),
        (
            Response::without_diagnostics(Outcome::ConfirmationRequired(
                ConfirmationRequired::new(
                    session(),
                    ConfirmationId::new(1).expect("confirmation ID"),
                    RiskClass::Sensitive,
                    plan.clone(),
                ),
            )),
            payload(RESPONSE_CONFIRMATION_REQUIRED),
        ),
        (
            Response::without_diagnostics(Outcome::PolicyAccepted(PolicyAccepted::new(
                plan,
                RiskClass::Routine,
            ))),
            payload(RESPONSE_POLICY_ACCEPTED),
        ),
        (
            Response::without_diagnostics(Outcome::Cancellation(CancellationStatus::Cancelled)),
            payload(RESPONSE_CANCELLATION),
        ),
        (
            Response::without_diagnostics(Outcome::Health(Health::new(
                Readiness::Ready,
                Some(Generation::new(1).expect("catalog generation")),
                Some(Generation::new(2).expect("policy generation")),
                Some(Generation::new(3).expect("configuration generation")),
            ))),
            payload(RESPONSE_HEALTH),
        ),
        (
            Response::without_diagnostics(Outcome::ProtocolError(ProtocolError::InputTooLarge)),
            payload(RESPONSE_PROTOCOL_ERROR),
        ),
    ];

    for (response, fixture) in expected {
        assert_eq!(v2::decode_response(fixture, &source), Ok(response.clone()));
        assert_eq!(v2::encode_response(&response), Ok(fixture.to_vec()));
    }
}

#[test]
fn round_trips_every_composed_plan_field_without_semantic_loss() {
    let source =
        RequestText::new("FIXTURE_TECNICA_AB".into()).expect("FIXTURE_TECNICA rich source");
    let plan = rich_plan(&source);
    let canonical_core = plan.canonical_bytes().expect("core canonical bytes");
    let response = Response::without_diagnostics(Outcome::PolicyAccepted(PolicyAccepted::new(
        plan,
        RiskClass::Critical,
    )));
    let encoded = v2::encode_response(&response).expect("encode");
    let decoded = v2::decode_response(&encoded, &source).expect("decode");
    assert_eq!(decoded, response);

    let Outcome::PolicyAccepted(accepted) = decoded.outcome() else {
        panic!("expected policy acceptance");
    };
    assert!(!accepted.authorizes_execution());
    assert_eq!(
        accepted
            .plan()
            .canonical_bytes()
            .expect("decoded canonical"),
        canonical_core
    );
    assert_eq!(accepted.plan().plan().nodes().len(), 3);
    assert_eq!(accepted.plan().plan().relations().len(), 2);
    assert_eq!(accepted.plan().clauses().len(), 3);
    assert_eq!(accepted.plan().relation_evidence().len(), 2);
    assert_eq!(accepted.plan().independent_pairs().len(), 1);
    assert_eq!(accepted.plan().argument_shares().len(), 1);
}

#[test]
fn rejects_equal_length_source_substitution_for_every_plan_outcome() {
    let original =
        RequestText::new("FIXTURE_TECNICA_A".into()).expect("FIXTURE_TECNICA original source");
    let substituted =
        RequestText::new("FIXTURE_TECNICA_B".into()).expect("FIXTURE_TECNICA substituted source");
    let plan = simple_plan(&original);
    let confirmation_id = ConfirmationId::new(1).expect("FIXTURE_TECNICA confirmation ID");

    let responses = [
        Response::without_diagnostics(Outcome::CompletePlan(plan.clone())),
        Response::without_diagnostics(Outcome::ConfirmationRequired(ConfirmationRequired::new(
            session(),
            confirmation_id,
            RiskClass::Sensitive,
            plan.clone(),
        ))),
        Response::without_diagnostics(Outcome::PolicyAccepted(PolicyAccepted::new(
            plan.clone(),
            RiskClass::Routine,
        ))),
    ];
    for response in responses {
        let encoded = v2::encode_response(&response).expect("FIXTURE_TECNICA source-bound wire");
        assert_eq!(v2::decode_response(&encoded, &original), Ok(response));
        assert_eq!(
            v2::decode_response(&encoded, &substituted),
            Err(ProtocolError::InvalidOutcome)
        );
    }

    let confirmation = Request::Confirm {
        session_id: session(),
        confirmation_id,
        plan,
    };
    let encoded = v2::encode_request(&confirmation).expect("FIXTURE_TECNICA addressed request");
    assert_eq!(v2::decode_request(&encoded), Ok(confirmation));
}

#[test]
fn rejects_unknown_duplicate_escaped_duplicate_missing_and_prohibited_request_fields() {
    let duplicate = br#"{"version":2,"version":2,"request":{"type":"health"}}"#;
    let escaped_duplicate = br#"{"version":2,"\u0076ersion":2,"request":{"type":"health"}}"#;
    let escaped_duplicate_tag =
        br#"{"version":2,"request":{"type":"health","ty\u0070e":"health"}}"#;
    let missing = br#"{"version":2}"#;
    let unknown = br#"{"version":2,"request":{"type":"health","unknown_fixture_tecnica":0}}"#;
    let prohibited = [
        "caller",
        "permission",
        "risk",
        "service",
        "payload",
        concat!("creden", "tial"),
    ];
    for bytes in [
        duplicate.as_slice(),
        escaped_duplicate,
        escaped_duplicate_tag,
        missing,
        unknown,
    ] {
        assert_eq!(v2::decode_request(bytes), Err(ProtocolError::MalformedJson));
    }
    for field in prohibited {
        let hostile = format!(
            r#"{{"version":2,"request":{{"type":"health","{field}":"FIXTURE_TECNICA_PRIVATE_CANARY"}}}}"#
        );
        assert_eq!(
            v2::decode_request(hostile.as_bytes()),
            Err(ProtocolError::MalformedJson)
        );
    }
}

#[test]
fn rejects_unknown_duplicate_and_escaped_duplicate_response_fields_at_every_layer() {
    let source = source();
    let invalid = [
        br#"{"version":2,"version":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[]}"#.as_slice(),
        br#"{"version":2,"\u0076ersion":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[]}"#,
        br#"{"version":2,"outcome":{"type":"abstention","ty\u0070e":"abstention","reason":"unsupported"},"diagnostics":[]}"#,
        br#"{"version":2,"outcome":{"type":"abstention","reason":"unsupported","unknown_fixture_tecnica":0},"diagnostics":[]}"#,
        br#"{"version":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[],"unknown_fixture_tecnica":0}"#,
        br#"{"version":2,"outcome":{"type":"complete_plan","plan":{"catalog_generation":1,"execution_class":"partial_safe","nodes":[{"id":"fixture_tecnica:node","capability":"fixture_tecnica:capability","operation":"fixture_tecnica:operation","slots":[],"evidence":[{"start":0,"start":0,"end":17}]}],"relations":[],"clauses":[{"node":"fixture_tecnica:node","intent":"fixture_tecnica:intent","polarity":"affirmed","evidence":[{"kind":"predicate","span":{"start":0,"end":17}}]}],"relation_evidence":[],"independent_pairs":[],"argument_shares":[]}},"diagnostics":[]}"#,
        br#"{"version":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[{"code":"unsupported","\u0063ode":"unsupported","node":null,"limit":null}]}"#,
    ];
    for bytes in invalid {
        assert_eq!(
            v2::decode_response(bytes, &source),
            Err(ProtocolError::MalformedJson)
        );
    }
}

#[test]
fn rejects_bad_versions_tags_utf8_trailing_input_and_opaque_ids() {
    assert_eq!(v2::decode_request(&[0xff]), Err(ProtocolError::InvalidUtf8));
    assert_eq!(
        v2::decode_request(br#"{"version":1,"request":{"type":"health"}}"#),
        Err(ProtocolError::UnsupportedVersion)
    );
    assert_eq!(
        v2::decode_request(br#"{"version":3,"request":{"type":"health"}}"#),
        Err(ProtocolError::UnsupportedVersion)
    );
    assert_eq!(
        v2::decode_request(br#"{"version":2,"request":{"type":"unknown_fixture_tecnica"}}"#),
        Err(ProtocolError::MalformedJson)
    );
    assert_eq!(
        v2::decode_request(br#"{"version":2,"request":{"type":"health"}} trailing"#),
        Err(ProtocolError::MalformedJson)
    );
    assert_eq!(
        v2::decode_request(b"\xef\xbb\xbf{\"version\":2,\"request\":{\"type\":\"health\"}}"),
        Err(ProtocolError::MalformedJson)
    );

    let confirmation_source = source();
    let valid_confirmation = String::from_utf8(
        v2::encode_request(&Request::Confirm {
            session_id: session(),
            confirmation_id: ConfirmationId::new(1).expect("confirmation ID"),
            plan: simple_plan(&confirmation_source),
        })
        .expect("FIXTURE_TECNICA valid confirmation"),
    )
    .expect("FIXTURE_TECNICA UTF-8 confirmation");
    let uppercase = valid_confirmation.replacen(&"0".repeat(64), &"A".repeat(64), 1);
    let short = valid_confirmation.replacen(&"0".repeat(64), &"0".repeat(63), 1);
    for invalid in [uppercase, short] {
        assert_eq!(
            v2::decode_request(invalid.as_bytes()),
            Err(ProtocolError::InvalidIdentifier)
        );
    }
    let zero_confirmation_id =
        valid_confirmation.replacen(r#""confirmation_id":1"#, r#""confirmation_id":0"#, 1);
    assert_eq!(
        v2::decode_request(zero_confirmation_id.as_bytes()),
        Err(ProtocolError::InvalidOutcome)
    );
}

#[test]
fn request_version_probe_is_structural_bounded_and_closed() {
    assert_eq!(
        detect_request_version(br#"{"version":1,"request":{"text":"FIXTURE_TECNICA"}}"#),
        Ok(RequestVersion::V1)
    );
    assert_eq!(
        detect_request_version(br#"{"version":2,"request":{"type":"health"}}"#),
        Ok(RequestVersion::V2)
    );
    assert_eq!(
        detect_request_version(br#"{"version":3,"request":{"type":"health"}}"#),
        Err(ProtocolError::UnsupportedVersion)
    );
    assert_eq!(
        detect_request_version(br#"{"version":2,"version":1,"request":{"type":"health"}}"#),
        Err(ProtocolError::MalformedJson)
    );
    assert_eq!(
        detect_request_version(br#"{"version":2,"request":{"type":"health"}} trailing"#),
        Err(ProtocolError::MalformedJson)
    );
    assert_eq!(
        detect_request_version(&vec![b' '; v2::MAX_WIRE_BYTES + 1]),
        Err(ProtocolError::InputTooLarge)
    );
}

#[test]
fn enforces_v2_wire_string_number_and_depth_limits() {
    let mut exact_wire = payload(REQUEST_HEALTH).to_vec();
    exact_wire.resize(v2::MAX_WIRE_BYTES, b' ');
    assert_eq!(v2::decode_request(&exact_wire), Ok(Request::Health));
    exact_wire.push(b' ');
    assert_eq!(
        v2::decode_request(&exact_wire),
        Err(ProtocolError::InputTooLarge)
    );

    let exact_text = "A".repeat(v2::MAX_DECODED_STRING_BYTES);
    let exact_string = format!(
        r#"{{"version":2,"request":{{"type":"interpret","session_id":"{}","text":"{exact_text}"}}}}"#,
        "0".repeat(64)
    );
    assert!(v2::decode_request(exact_string.as_bytes()).is_ok());
    let oversized_text = "A".repeat(v2::MAX_DECODED_STRING_BYTES + 1);
    let oversized_string = format!(
        r#"{{"version":2,"request":{{"type":"interpret","session_id":"{}","text":"{oversized_text}"}}}}"#,
        "0".repeat(64)
    );
    assert_eq!(
        v2::decode_request(oversized_string.as_bytes()),
        Err(ProtocolError::StringTooLarge)
    );

    let max_generation = format!(
        r#"{{"version":2,"request":{{"type":"continue","session_id":"{}","selection":{{"id":"fixture_tecnica:entity","generation":18446744073709551615}}}}}}"#,
        "0".repeat(64)
    );
    assert!(v2::decode_request(max_generation.as_bytes()).is_ok());
    let oversized_integer = max_generation.replace("18446744073709551615", "118446744073709551615");
    assert_eq!(
        v2::decode_request(oversized_integer.as_bytes()),
        Err(ProtocolError::NumericTokenTooLong)
    );
    assert_eq!(
        v2::decode_request(br#"{"version":2.0,"request":{"type":"health"}}"#),
        Err(ProtocolError::NonIntegerNumber)
    );

    let too_deep = format!(
        r#"{{"version":2,"request":{{"type":"health","x":{}0{}}}}}"#,
        "[".repeat(v2::MAX_NESTING_DEPTH),
        "]".repeat(v2::MAX_NESTING_DEPTH)
    );
    assert_eq!(
        v2::decode_request(too_deep.as_bytes()),
        Err(ProtocolError::NestingTooDeep)
    );
}

#[test]
fn diagnostics_are_closed_canonical_bounded_and_use_fixed_limits() {
    let codes = [
        DiagnosticCode::InsufficientEvidence,
        DiagnosticCode::Ambiguous,
        DiagnosticCode::Unsupported,
        DiagnosticCode::SessionUnavailable,
        DiagnosticCode::SessionExpired,
        DiagnosticCode::InvalidSelection,
        DiagnosticCode::StaleCatalogGeneration,
        DiagnosticCode::PolicyDenied,
    ];
    let diagnostics = codes
        .into_iter()
        .map(|code| Diagnostic::new(code, None))
        .collect();
    let response = Response::new(
        Outcome::Abstention(AbstentionReason::Ambiguous),
        diagnostics,
    )
    .expect("exact diagnostic limit");
    let encoded = v2::encode_response(&response).expect("encode diagnostics");
    assert_eq!(
        v2::decode_response(&encoded, &source()),
        Ok(response.clone())
    );
    assert_eq!(response.diagnostics().len(), v2::MAX_DIAGNOSTICS);

    let mut too_many = response.diagnostics().to_vec();
    too_many.push(Diagnostic::new(DiagnosticCode::ConfirmationExpired, None));
    assert_eq!(
        Response::new(Outcome::Abstention(AbstentionReason::Ambiguous), too_many),
        Err(ProtocolError::InvalidOutcome)
    );

    let limited = Response::new(
        Outcome::Abstention(AbstentionReason::Unsupported),
        vec![Diagnostic::new(DiagnosticCode::ReferentLimit, None)],
    )
    .expect("limited diagnostic");
    let limited_bytes = v2::encode_response(&limited).expect("limited encoding");
    assert!(
        String::from_utf8(limited_bytes)
            .expect("UTF-8")
            .contains(r#""code":"referent_limit","node":null,"limit":16"#)
    );

    let wrong_limit = br#"{"version":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[{"code":"referent_limit","node":null,"limit":15}]}"#;
    assert_eq!(
        v2::decode_response(wrong_limit, &source()),
        Err(ProtocolError::InvalidOutcome)
    );
    let duplicate = br#"{"version":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[{"code":"unsupported","node":null,"limit":null},{"code":"unsupported","node":null,"limit":null}]}"#;
    assert_eq!(
        v2::decode_response(duplicate, &source()),
        Err(ProtocolError::InvalidOutcome)
    );

    let too_many_wire = br#"{"version":2,"outcome":{"type":"abstention","reason":"unsupported"},"diagnostics":[{"code":"insufficient_evidence","node":null,"limit":null},{"code":"ambiguous","node":null,"limit":null},{"code":"unsupported","node":null,"limit":null},{"code":"session_unavailable","node":null,"limit":null},{"code":"session_expired","node":null,"limit":null},{"code":"invalid_selection","node":null,"limit":null},{"code":"stale_catalog_generation","node":null,"limit":null},{"code":"policy_rule_missing","node":null,"limit":null},{"code":"policy_denied","node":null,"limit":null}]}"#;
    assert_eq!(
        v2::decode_response(too_many_wire, &source()),
        Err(ProtocolError::InvalidOutcome)
    );
}

#[test]
fn entity_clarification_is_typed_generation_bound_and_limited_to_sixteen() {
    let exact = (0..nlu_core::MAX_CLARIFICATION_OPTIONS)
        .map(|index| entity(&format!("fixture_tecnica:entity_{index}"), 7))
        .collect();
    let clarification = EntityClarification::new(session(), exact).expect("exact referent limit");
    assert_eq!(
        clarification.referents().len(),
        nlu_core::MAX_CLARIFICATION_OPTIONS
    );
    assert!(clarification.is_continuation());

    let too_many = (0..=nlu_core::MAX_CLARIFICATION_OPTIONS)
        .map(|index| entity(&format!("fixture_tecnica:entity_{index}"), 7))
        .collect();
    assert_eq!(
        EntityClarification::new(session(), too_many),
        Err(ProtocolError::InvalidOutcome)
    );
    assert_eq!(
        EntityClarification::new(
            session(),
            vec![
                entity("fixture_tecnica:entity_a", 1),
                entity("fixture_tecnica:entity_b", 2),
            ],
        ),
        Err(ProtocolError::InvalidOutcome)
    );

    let continuation_false = br#"{"version":2,"outcome":{"type":"entity_clarification","continuation":false,"session_id":"0000000000000000000000000000000000000000000000000000000000000000","referents":[{"id":"fixture_tecnica:entity","generation":1}]},"diagnostics":[]}"#;
    assert_eq!(
        v2::decode_response(continuation_false, &source()),
        Err(ProtocolError::InvalidOutcome)
    );

    let too_many_referents = repeated_json_array(
        br#"{"id":"fixture_tecnica:entity","generation":1}"#,
        nlu_core::MAX_CLARIFICATION_OPTIONS + 1,
    );
    let too_many_wire = replace_json_array_field(
        payload(RESPONSE_ENTITY_CLARIFICATION),
        b"referents",
        &too_many_referents,
    );
    assert_eq!(
        v2::decode_response(&too_many_wire, &source()),
        Err(ProtocolError::InvalidOutcome)
    );
}

#[test]
fn rejects_invalid_spans_graphs_and_incompatible_response_constants() {
    let source = source();

    let invalid_span = replace_exact(
        payload(RESPONSE_COMPLETE_PLAN),
        br#""evidence":[{"start":0,"end":17}]"#,
        br#""evidence":[{"start":0,"end":18}]"#,
    );
    assert_eq!(
        v2::decode_response(&invalid_span, &source),
        Err(ProtocolError::InvalidSpan)
    );

    let missing_clause =
        replace_json_array_field(payload(RESPONSE_COMPLETE_PLAN), b"clauses", b"[]");
    assert_eq!(
        v2::decode_response(&missing_clause, &source),
        Err(ProtocolError::InvalidGraph)
    );

    let wrong_class = replace_exact(
        payload(RESPONSE_COMPLETE_PLAN),
        br#""execution_class":"partial_safe""#,
        br#""execution_class":"atomic_only""#,
    );
    assert_eq!(
        v2::decode_response(&wrong_class, &source),
        Err(ProtocolError::InvalidGraph)
    );

    let false_position = payload(RESPONSE_POLICY_ACCEPTED)
        .windows(b"false".len())
        .position(|window| window == b"false")
        .expect("false marker");
    let mut authorizing = payload(RESPONSE_POLICY_ACCEPTED).to_vec();
    authorizing.splice(
        false_position..false_position + b"false".len(),
        b"true".iter().copied(),
    );
    assert_eq!(
        v2::decode_response(&authorizing, &source),
        Err(ProtocolError::InvalidOutcome)
    );

    let bad_health = br#"{"version":2,"outcome":{"type":"health","readiness":"ready","supported_versions":[2],"catalog_generation":null,"policy_generation":null,"configuration_generation":null},"diagnostics":[]}"#;
    assert_eq!(
        v2::decode_response(bad_health, &source),
        Err(ProtocolError::InvalidOutcome)
    );
    let bad_error = br#"{"version":2,"outcome":{"type":"protocol_error","code":"input_too_large","limit":65536},"diagnostics":[]}"#;
    assert_eq!(
        v2::decode_response(bad_error, &source),
        Err(ProtocolError::InvalidOutcome)
    );
}

#[test]
fn enforces_every_core_collection_limit_before_accepting_a_graph() {
    let source = source();

    let node = br#"{"id":"fixture_tecnica:node","capability":"fixture_tecnica:capability","operation":"fixture_tecnica:operation","slots":[],"evidence":[{"start":0,"end":17}]}"#;
    let nodes = replace_json_array_field(
        payload(RESPONSE_COMPLETE_PLAN),
        b"nodes",
        &repeated_json_array(node, nlu_core::MAX_PLAN_NODES + 1),
    );
    assert_eq!(
        v2::decode_response(&nodes, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );

    let slot = br#"{"id":"fixture_tecnica:slot","value":{"type":"boolean","value":true}}"#;
    let slots = replace_json_array_field(
        payload(RESPONSE_COMPLETE_PLAN),
        b"slots",
        &repeated_json_array(slot, nlu_core::MAX_SLOTS_PER_NODE + 1),
    );
    assert_eq!(
        v2::decode_response(&slots, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );

    let mut evidence_replacement = br#""evidence":"#.to_vec();
    evidence_replacement.extend_from_slice(&repeated_json_array(
        br#"{"start":0,"end":1}"#,
        nlu_core::MAX_EVIDENCE_SPANS + 1,
    ));
    let evidence = replace_exact(
        payload(RESPONSE_COMPLETE_PLAN),
        br#""evidence":[{"start":0,"end":17}]"#,
        &evidence_replacement,
    );
    assert_eq!(
        v2::decode_response(&evidence, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );

    let relation =
        br#"{"from":"fixture_tecnica:node","to":"fixture_tecnica:node","kind":"precedes"}"#;
    let relations = replace_json_array_field(
        payload(RESPONSE_COMPLETE_PLAN),
        b"relations",
        &repeated_json_array(relation, nlu_core::MAX_RELATIONS + 1),
    );
    assert_eq!(
        v2::decode_response(&relations, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );

    let relation_evidence_item = br#"{"relation":{"from":"fixture_tecnica:node","to":"fixture_tecnica:node","kind":"precedes"},"evidence":[{"start":0,"end":1}]}"#;
    let relation_evidence = replace_json_array_field(
        payload(RESPONSE_COMPLETE_PLAN),
        b"relation_evidence",
        &repeated_json_array(relation_evidence_item, nlu_core::MAX_RELATIONS + 1),
    );
    assert_eq!(
        v2::decode_response(&relation_evidence, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );

    let independent_pair = br#"{"left":"fixture_tecnica:node","right":"fixture_tecnica:other"}"#;
    let independent_pairs = replace_json_array_field(
        payload(RESPONSE_COMPLETE_PLAN),
        b"independent_pairs",
        &repeated_json_array(independent_pair, nlu_core::MAX_INDEPENDENT_PAIRS + 1),
    );
    assert_eq!(
        v2::decode_response(&independent_pairs, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );

    let argument_share = br#"{"from":{"node":"fixture_tecnica:node","slot":"fixture_tecnica:slot"},"to":{"node":"fixture_tecnica:other","slot":"fixture_tecnica:slot"},"evidence":[{"start":0,"end":1}]}"#;
    let argument_shares = replace_json_array_field(
        payload(RESPONSE_COMPLETE_PLAN),
        b"argument_shares",
        &repeated_json_array(argument_share, nlu_core::MAX_ARGUMENT_SHARES + 1),
    );
    assert_eq!(
        v2::decode_response(&argument_shares, &source),
        Err(ProtocolError::StructuralLimitExceeded)
    );
}

#[test]
fn rejects_dangling_or_incomplete_composed_plan_semantics() {
    let source =
        RequestText::new("FIXTURE_TECNICA_AB".into()).expect("FIXTURE_TECNICA rich source");
    let response = Response::without_diagnostics(Outcome::CompletePlan(rich_plan(&source)));
    let encoded = v2::encode_response(&response).expect("rich response");
    let dangling_relation = replace_exact(
        &encoded,
        br#""relations":[{"from":"fixture_tecnica:node_a","to":"fixture_tecnica:node_b","kind":"precedes"},{"from":"fixture_tecnica:node_b","to":"fixture_tecnica:node_c","kind":"requires"}]"#,
        br#""relations":[{"from":"fixture_tecnica:node_a","to":"fixture_tecnica:node_b","kind":"precedes"},{"from":"fixture_tecnica:node_b","to":"fixture_tecnica:missing","kind":"requires"}]"#,
    );
    assert_eq!(
        v2::decode_response(&dangling_relation, &source),
        Err(ProtocolError::InvalidGraph)
    );

    let missing_relation_evidence = replace_json_array_field(&encoded, b"relation_evidence", b"[]");
    assert_eq!(
        v2::decode_response(&missing_relation_evidence, &source),
        Err(ProtocolError::InvalidGraph)
    );

    let related_independent_pair = replace_exact(
        &encoded,
        br#"{"left":"fixture_tecnica:node_a","right":"fixture_tecnica:node_c"}"#,
        br#"{"left":"fixture_tecnica:node_a","right":"fixture_tecnica:node_b"}"#,
    );
    assert_eq!(
        v2::decode_response(&related_independent_pair, &source),
        Err(ProtocolError::InvalidGraph)
    );

    let dangling_share = replace_exact(
        &encoded,
        br#""to":{"node":"fixture_tecnica:node_b","slot":"fixture_tecnica:shared"}"#,
        br#""to":{"node":"fixture_tecnica:node_b","slot":"fixture_tecnica:missing"}"#,
    );
    assert_eq!(
        v2::decode_response(&dangling_share, &source),
        Err(ProtocolError::InvalidGraph)
    );

    let clause_evidence_mismatch = replace_exact(
        &encoded,
        br#""span":{"start":0,"end":1}"#,
        br#""span":{"start":0,"end":2}"#,
    );
    assert_eq!(
        v2::decode_response(&clause_evidence_mismatch, &source),
        Err(ProtocolError::InvalidGraph)
    );
}

#[test]
fn round_trips_every_closed_policy_health_and_diagnostic_enum_value() {
    let source = source();
    for reason in [
        AbstentionReason::InsufficientEvidence,
        AbstentionReason::Ambiguous,
        AbstentionReason::Unsupported,
    ] {
        let response = Response::without_diagnostics(Outcome::Abstention(reason));
        let encoded = v2::encode_response(&response).expect("abstention");
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }
    for reason in [
        PolicyDenialReason::MissingRule,
        PolicyDenialReason::ExplicitDeny,
        PolicyDenialReason::UnsupportedGraphClass,
        PolicyDenialReason::NonExecutable,
        PolicyDenialReason::StaleCatalogGeneration,
        PolicyDenialReason::StalePolicyGeneration,
        PolicyDenialReason::Contradiction,
        PolicyDenialReason::ConfirmationMismatch,
    ] {
        let response = Response::without_diagnostics(Outcome::PolicyDenial(reason));
        let encoded = v2::encode_response(&response).expect("policy denial");
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }
    for risk in [
        RiskClass::ReadOnly,
        RiskClass::Routine,
        RiskClass::Sensitive,
        RiskClass::Critical,
    ] {
        let response = Response::without_diagnostics(Outcome::ConfirmationRequired(
            ConfirmationRequired::new(
                session(),
                ConfirmationId::new(1).expect("confirmation ID"),
                risk,
                simple_plan(&source),
            ),
        ));
        let encoded = v2::encode_response(&response).expect("confirmation required");
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }
    for status in [
        CancellationStatus::Cancelled,
        CancellationStatus::Unavailable,
    ] {
        let response = Response::without_diagnostics(Outcome::Cancellation(status));
        let encoded = v2::encode_response(&response).expect("cancellation");
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }
    for readiness in [Readiness::Ready, Readiness::Reloading, Readiness::NotReady] {
        let response = Response::without_diagnostics(Outcome::Health(Health::new(
            readiness, None, None, None,
        )));
        let encoded = v2::encode_response(&response).expect("health");
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }

    let codes = [
        DiagnosticCode::InsufficientEvidence,
        DiagnosticCode::Ambiguous,
        DiagnosticCode::Unsupported,
        DiagnosticCode::SessionUnavailable,
        DiagnosticCode::SessionExpired,
        DiagnosticCode::InvalidSelection,
        DiagnosticCode::StaleCatalogGeneration,
        DiagnosticCode::PolicyRuleMissing,
        DiagnosticCode::PolicyDenied,
        DiagnosticCode::UnsupportedGraphClass,
        DiagnosticCode::ConfirmationUnavailable,
        DiagnosticCode::ConfirmationExpired,
        DiagnosticCode::WireByteLimit,
        DiagnosticCode::StringByteLimit,
        DiagnosticCode::StructuralItemLimit,
        DiagnosticCode::PlanNodeLimit,
        DiagnosticCode::RelationLimit,
        DiagnosticCode::SlotLimit,
        DiagnosticCode::EvidenceLimit,
        DiagnosticCode::ReferentLimit,
        DiagnosticCode::DiagnosticLimit,
    ];
    for code in codes {
        let response = Response::new(
            Outcome::Abstention(AbstentionReason::Unsupported),
            vec![Diagnostic::new(code, None)],
        )
        .expect("diagnostic response");
        let encoded = v2::encode_response(&response).expect("diagnostic");
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }
}

#[test]
fn round_trips_non_executable_negated_evidence() {
    let source = source();
    let node_id = NodeId::new("fixture_tecnica:node").expect("node");
    let predicate = source.span(0, 1).expect("predicate");
    let negation = source.span(1, 2).expect("negation");
    let node = PlanNode::new(
        node_id.clone(),
        CapabilityId::new("fixture_tecnica:capability").expect("capability"),
        OperationId::new("fixture_tecnica:operation").expect("operation"),
        Vec::new(),
        vec![predicate.clone(), negation.clone()],
    )
    .expect("node");
    let plan = Plan::new(&source, generation(1), vec![node], Vec::new()).expect("plan");
    let clause = ClauseSemantics::new(
        node_id,
        IntentId::new("fixture_tecnica:intent").expect("intent"),
        Polarity::Negated,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, predicate),
            EvidenceAtom::new(EvidenceKind::Negation, negation),
        ],
    )
    .expect("clause");
    let composed = ComposedPlan::new(
        &source,
        plan,
        GraphExecutionClass::NonExecutable,
        vec![clause],
        Vec::new(),
        Vec::new(),
        Vec::new(),
    )
    .expect("non-executable plan");
    let response = Response::without_diagnostics(Outcome::CompletePlan(composed));
    let encoded = v2::encode_response(&response).expect("encode");
    assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
}

#[test]
fn protocol_errors_are_bounded_closed_and_use_v2_limits() {
    let source = source();
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
        let response = Response::without_diagnostics(Outcome::ProtocolError(error));
        let encoded = v2::encode_response(&response).expect("protocol error encoding");
        assert!(encoded.len() <= protocol::MAX_PROTOCOL_ERROR_BYTES);
        assert_eq!(v2::decode_response(&encoded, &source), Ok(response));
    }
    assert_eq!(
        v2::encode_protocol_error(ProtocolError::InputTooLarge),
        Ok(payload(RESPONSE_PROTOCOL_ERROR).to_vec())
    );
}

#[test]
fn errors_debug_and_wire_output_never_echo_hostile_or_residential_text() {
    let canary = "FIXTURE_TECNICA_PRIVATE_CANARY";
    let hostile = format!(r#"{{"version":2,"request":{{"type":"health","{canary}":"{canary}"}}}}"#);
    let error = v2::decode_request(hostile.as_bytes()).expect_err("must reject");
    let encoded = v2::encode_protocol_error(error).expect("safe protocol error");
    assert!(!error.to_string().contains(canary));
    assert!(!format!("{error:?}").contains(canary));
    assert!(!String::from_utf8(encoded).expect("UTF-8").contains(canary));

    let request = Request::Interpret {
        session_id: session(),
        text: RequestText::new(canary.into()).expect("canary request"),
    };
    assert!(!format!("{request:?}").contains(canary));
    assert!(!format!("{:?}", session()).contains(&"0".repeat(64)));
}
