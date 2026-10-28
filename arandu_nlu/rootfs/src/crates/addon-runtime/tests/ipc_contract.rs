use std::fs;
use std::io::Cursor;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread::{self, JoinHandle};

use addon_runtime::{
    CatalogBindings, CatalogEntity, CatalogSeed, CompanionSubmission, CompanionSubmissionKind,
    HelperReply, RuntimeError, interpret_submission, read_helper_reply, read_submission,
    write_helper_reply, write_submission,
};
use nlu_core::{CatalogGeneration, ComposedPlan, EntityId, EntityRef, LogicalTime, RequestText};
use nlu_server::{NluRuntime, RuntimeSnapshot, SnapshotMetadata};
use noise_channel::{ChannelBinding, ConnectionDirection, ConnectionNonce, PairingEpoch};
use policy_engine::{ConfirmationConfig, PolicyGeneration};
use protocol::v2::{
    self, CancellationStatus, ConfirmationId, DiagnosticCode, Outcome, PolicyDenialReason, Request,
    Response, SessionId,
};
use serde_json::{Value, json};
use session_engine::SessionConfig;

const ADMITTED_REQUEST: &str = "desligue interruptor 1 do setor sala";
const ADMITTED_ALIAS: &str = "interruptor 1 do setor sala";
const CONTEXT_ID: &str = "fixture_context";
const CALLER_ID: &str = "fixture_caller";
const CONVERSATION_ID: &str = "fixture_conversation";
const FIRST_REGISTRY: &str = "00000000000000000000000000000001";
const SECOND_REGISTRY: &str = "00000000000000000000000000000002";
const FIRST_CORE_ENTITY: &str = "ha_entity:id_00000000000000000000000000000001";
static NEXT_SOCKET: AtomicU64 = AtomicU64::new(1);

struct PendingConfirmation {
    session_id: SessionId,
    selection: EntityRef,
    alternate: EntityRef,
    confirmation_id: ConfirmationId,
    plan: ComposedPlan,
}

fn source() -> RequestText {
    RequestText::new(ADMITTED_REQUEST.to_owned()).expect("admitted request")
}

fn submission() -> CompanionSubmission {
    CompanionSubmission::new(
        ADMITTED_REQUEST.to_owned(),
        CONTEXT_ID.to_owned(),
        CALLER_ID.to_owned(),
        Some(CONVERSATION_ID.to_owned()),
    )
    .expect("FIXTURE_TECNICA submission")
}

fn protocol_submission(request: &Request) -> CompanionSubmission {
    let encoded = v2::encode_request(request).expect("FIXTURE_TECNICA protocol request");
    CompanionSubmission::from_protocol_v2(
        ADMITTED_REQUEST.to_owned(),
        CONTEXT_ID.to_owned(),
        CALLER_ID.to_owned(),
        Some(CONVERSATION_ID.to_owned()),
        &encoded,
    )
    .expect("FIXTURE_TECNICA continuation submission")
}

fn entity(id: &str) -> EntityRef {
    EntityRef::new(
        EntityId::new(id).expect("FIXTURE_TECNICA entity identity"),
        CatalogGeneration::new(7).expect("FIXTURE_TECNICA catalog generation"),
    )
}

fn catalog_seed() -> CatalogSeed {
    CatalogSeed::new(
        7,
        vec![
            CatalogEntity::new(
                FIRST_REGISTRY,
                "switch.fixture_tecnica_1",
                "FIXTURE_TECNICA_DISPLAY_1".to_owned(),
                vec![ADMITTED_ALIAS.to_owned()],
                vec!["ha:switch_control".to_owned()],
            )
            .expect("FIXTURE_TECNICA first catalog entity"),
            CatalogEntity::new(
                SECOND_REGISTRY,
                "switch.fixture_tecnica_2",
                "FIXTURE_TECNICA_DISPLAY_2".to_owned(),
                vec![ADMITTED_ALIAS.to_owned()],
                vec!["ha:switch_control".to_owned()],
            )
            .expect("FIXTURE_TECNICA second catalog entity"),
        ],
    )
    .expect("FIXTURE_TECNICA catalog seed")
}

fn socket_path() -> PathBuf {
    let serial = NEXT_SOCKET.fetch_add(1, Ordering::Relaxed);
    PathBuf::from(format!(
        "/tmp/FIXTURE_TECNICA_ipc_contract_{}_{}.sock",
        std::process::id(),
        serial
    ))
}

fn runtime_server(request_count: usize) -> (PathBuf, CatalogBindings, JoinHandle<Vec<Request>>) {
    let seed = catalog_seed();
    let bindings = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA catalog bindings");
    bindings
        .activate_operation_epoch([7; 32])
        .expect("FIXTURE_TECNICA active pairing epoch");
    let metadata = SnapshotMetadata::new(1, 7, 1, 1, 1).expect("FIXTURE_TECNICA snapshot metadata");
    let runtime = NluRuntime::standard(
        metadata,
        seed.into_snapshot()
            .expect("FIXTURE_TECNICA runtime catalog"),
        PolicyGeneration::new(1).expect("FIXTURE_TECNICA policy generation"),
        SessionConfig::new(10).expect("FIXTURE_TECNICA session configuration"),
        ConfirmationConfig::new(10).expect("FIXTURE_TECNICA confirmation configuration"),
    )
    .expect("FIXTURE_TECNICA runtime");
    let snapshot = RuntimeSnapshot::new(metadata, runtime);
    let socket = socket_path();
    let listener = UnixListener::bind(&socket).expect("FIXTURE_TECNICA runtime listener");
    let requests = thread::spawn(move || {
        let mut requests = Vec::with_capacity(request_count);
        let mut now = 1_u64;
        for _ in 0..request_count {
            let (mut stream, _) = listener.accept().expect("FIXTURE_TECNICA runtime accept");
            let encoded =
                nlu_server::read_frame(&mut stream).expect("FIXTURE_TECNICA request frame");
            requests.push(
                v2::decode_request(&encoded).expect("FIXTURE_TECNICA decoded runtime request"),
            );
            let response =
                NluRuntime::dispatch_at(&snapshot, &encoded, LogicalTime::from_ticks(now))
                    .expect("FIXTURE_TECNICA runtime dispatch");
            nlu_server::write_frame(&mut stream, &response)
                .expect("FIXTURE_TECNICA response frame");
            now = now.checked_add(1).expect("FIXTURE_TECNICA bounded ticks");
        }
        requests
    });
    (socket, bindings, requests)
}

fn finish_runtime_server(socket: PathBuf, requests: JoinHandle<Vec<Request>>) -> Vec<Request> {
    let requests = requests.join().expect("FIXTURE_TECNICA runtime server");
    fs::remove_file(socket).expect("FIXTURE_TECNICA socket cleanup");
    requests
}

fn channel_binding(nonce: u8) -> ChannelBinding {
    ChannelBinding::new(
        PairingEpoch::new([7; 32]).expect("FIXTURE_TECNICA pairing epoch"),
        ConnectionDirection::CompanionInitiates,
        ConnectionNonce::from_bytes([nonce; 32]).expect("FIXTURE_TECNICA connection nonce"),
    )
}

fn assert_protocol_submission(
    submission: &CompanionSubmission,
    request: &Request,
    expected_kind: CompanionSubmissionKind,
) {
    assert_eq!(submission.kind(), expected_kind);
    assert_eq!(submission.original_source(), ADMITTED_REQUEST);
    assert_eq!(submission.protocol_request(), Some(request));

    let encoded_request = v2::encode_request(request).expect("FIXTURE_TECNICA protocol request");
    let request_value =
        nlu_data::parse_strict_json(&encoded_request, "FIXTURE_TECNICA protocol request")
            .expect("FIXTURE_TECNICA request JSON");
    let expected = nlu_data::canonical_json(
        &json!({
            "caller_id": CALLER_ID,
            "context_id": CONTEXT_ID,
            "conversation_id": CONVERSATION_ID,
            "kind": "protocol_v2_request",
            "language": "pt-BR",
            "original_source": ADMITTED_REQUEST,
            "protocol_request": request_value,
            "version": 1,
        }),
        "FIXTURE_TECNICA continuation submission",
    )
    .expect("FIXTURE_TECNICA canonical continuation");
    let encoded = submission
        .encode()
        .expect("FIXTURE_TECNICA encoded continuation");
    assert_eq!(encoded, expected);
    assert_eq!(
        CompanionSubmission::decode(&encoded).expect("FIXTURE_TECNICA decoded continuation"),
        *submission
    );
}

fn decode_typed_outcome(encoded: &[u8]) -> Response {
    let relay = nlu_data::parse_strict_json(encoded, "FIXTURE_TECNICA typed relay")
        .expect("FIXTURE_TECNICA typed relay JSON");
    let object = relay
        .as_object()
        .expect("FIXTURE_TECNICA typed relay object");
    assert_eq!(object.len(), 3);
    assert_eq!(
        object.get("kind").and_then(Value::as_str),
        Some("typed_outcome")
    );
    assert_eq!(object.get("version").and_then(Value::as_u64), Some(1));
    for field in [
        "caller_id",
        "context_id",
        "execution_class",
        "nodes",
        "operation_id",
        "plan_digest",
    ] {
        assert!(
            object.get(field).is_none(),
            "FIXTURE_TECNICA typed outcome cannot expose an execution graph"
        );
    }

    let response_value = object
        .get("response")
        .filter(|value| value.is_object())
        .expect("FIXTURE_TECNICA complete protocol response");
    let expected = nlu_data::canonical_json(
        &json!({
            "kind": "typed_outcome",
            "response": response_value,
            "version": 1,
        }),
        "FIXTURE_TECNICA typed relay",
    )
    .expect("FIXTURE_TECNICA canonical typed relay");
    assert_eq!(encoded, expected);
    let response = nlu_data::canonical_json(response_value, "FIXTURE_TECNICA protocol response")
        .expect("FIXTURE_TECNICA canonical response");
    v2::decode_response(&response, &source()).expect("FIXTURE_TECNICA decoded response")
}

fn begin_confirmation(socket: &Path, catalog: &CatalogBindings) -> PendingConfirmation {
    let interpreted = interpret_submission(socket, catalog, channel_binding(1), &submission())
        .expect("FIXTURE_TECNICA interpreted clarification");
    let interpreted = decode_typed_outcome(&interpreted);
    let Outcome::EntityClarification(clarification) = interpreted.outcome() else {
        panic!("FIXTURE_TECNICA expected entity clarification");
    };
    assert_eq!(clarification.referents().len(), 2);
    let session_id = clarification.session_id();
    let selection = clarification.referents()[0].clone();
    let alternate = clarification.referents()[1].clone();
    let request = Request::Continue {
        session_id,
        selection: selection.clone(),
    };
    let continuation = protocol_submission(&request);
    assert_protocol_submission(&continuation, &request, CompanionSubmissionKind::Continue);

    let continued = interpret_submission(socket, catalog, channel_binding(2), &continuation)
        .expect("FIXTURE_TECNICA continued confirmation");
    let continued = decode_typed_outcome(&continued);
    let Outcome::ConfirmationRequired(required) = continued.outcome() else {
        panic!("FIXTURE_TECNICA expected confirmation requirement");
    };
    assert_eq!(required.session_id(), session_id);
    PendingConfirmation {
        session_id,
        selection,
        alternate,
        confirmation_id: required.confirmation_id(),
        plan: required.plan().clone(),
    }
}

#[test]
fn legacy_interpret_bytes_remain_compatible_and_round_trip() {
    let submission = CompanionSubmission::new(
        "FIXTURE_TECNICA_ação".to_owned(),
        "fixture_context".to_owned(),
        "fixture_caller".to_owned(),
        Some("fixture_conversation".to_owned()),
    )
    .expect("FIXTURE_TECNICA submission");
    let expected = r#"{"caller_id":"fixture_caller","context_id":"fixture_context","conversation_id":"fixture_conversation","language":"pt-BR","text":"FIXTURE_TECNICA_ação","version":1}"#
        .as_bytes();
    assert_eq!(
        submission
            .encode()
            .expect("FIXTURE_TECNICA legacy submission"),
        expected
    );
    assert_eq!(submission.kind(), CompanionSubmissionKind::Interpret);
    assert_eq!(submission.protocol_request(), None);
    let mut framed = Vec::new();
    write_submission(&mut framed, &submission).expect("FIXTURE_TECNICA write");
    let decoded = read_submission(&mut framed.as_slice()).expect("FIXTURE_TECNICA read");
    assert_eq!(decoded, submission);
    assert_eq!(decoded.text().as_bytes(), "FIXTURE_TECNICA_ação".as_bytes());
    assert!(!format!("{decoded:?}").contains("FIXTURE_TECNICA_ação"));
}

#[test]
fn submission_rejects_open_fields_credentials_and_non_ptbr_language() {
    for bytes in [
        r#"{"caller_id":"fixture_caller","context_id":"fixture_context","conversation_id":null,"credential":"FIXTURE_TECNICA_PRIVATE","language":"pt-BR","text":"FIXTURE_TECNICA_ação","version":1}"#.as_bytes(),
        r#"{"caller_id":"fixture_caller","context_id":"fixture_context","conversation_id":null,"language":"en","text":"FIXTURE_TECNICA_ação","version":1}"#.as_bytes(),
    ] {
        assert_eq!(
            CompanionSubmission::decode(bytes).expect_err("closed submission schema"),
            RuntimeError::InvalidSubmission
        );
    }
}

#[test]
fn continue_and_cancel_submissions_are_closed_canonical_round_trips() {
    let session_id = SessionId::from_bytes([3; v2::SESSION_ID_BYTES]);
    for (request, kind) in [
        (
            Request::Continue {
                session_id,
                selection: entity(FIRST_CORE_ENTITY),
            },
            CompanionSubmissionKind::Continue,
        ),
        (
            Request::Cancel { session_id },
            CompanionSubmissionKind::Cancel,
        ),
    ] {
        let submission = protocol_submission(&request);
        assert_protocol_submission(&submission, &request, kind);
    }
}

#[test]
fn malformed_interpret_and_health_nested_requests_are_rejected_closed() {
    let session_id = SessionId::from_bytes([4; v2::SESSION_ID_BYTES]);
    for request in [
        Request::Interpret {
            session_id,
            text: source(),
        },
        Request::Health,
    ] {
        let encoded = v2::encode_request(&request).expect("FIXTURE_TECNICA smuggled request");
        assert_eq!(
            CompanionSubmission::from_protocol_v2(
                ADMITTED_REQUEST.to_owned(),
                CONTEXT_ID.to_owned(),
                CALLER_ID.to_owned(),
                Some(CONVERSATION_ID.to_owned()),
                &encoded,
            )
            .expect_err("FIXTURE_TECNICA nested request allowlist"),
            RuntimeError::InvalidSubmission
        );
    }

    for encoded in [
        br#"{"request":{"type":"cancel"},"version":2}"#.as_slice(),
        br#"{"request":{"type":"health"},"version":3}"#.as_slice(),
        br#"{"request":{"session_id":"not-a-session","type":"cancel"},"version":2}"#.as_slice(),
    ] {
        assert_eq!(
            CompanionSubmission::from_protocol_v2(
                ADMITTED_REQUEST.to_owned(),
                CONTEXT_ID.to_owned(),
                CALLER_ID.to_owned(),
                Some(CONVERSATION_ID.to_owned()),
                encoded,
            )
            .expect_err("FIXTURE_TECNICA malformed nested request"),
            RuntimeError::InvalidSubmission
        );
    }
}

#[test]
fn changed_conversation_rejects_the_relayed_session_before_server_exchange() {
    let (socket, catalog, server) = runtime_server(1);
    let interpreted = interpret_submission(&socket, &catalog, channel_binding(1), &submission())
        .expect("FIXTURE_TECNICA interpreted clarification");
    let interpreted = decode_typed_outcome(&interpreted);
    let Outcome::EntityClarification(clarification) = interpreted.outcome() else {
        panic!("FIXTURE_TECNICA expected entity clarification");
    };
    let request = Request::Continue {
        session_id: clarification.session_id(),
        selection: clarification.referents()[0].clone(),
    };
    let encoded = v2::encode_request(&request).expect("FIXTURE_TECNICA continuation request");
    let changed_binding = CompanionSubmission::from_protocol_v2(
        ADMITTED_REQUEST.to_owned(),
        CONTEXT_ID.to_owned(),
        CALLER_ID.to_owned(),
        Some("fixture_changed_conversation".to_owned()),
        &encoded,
    )
    .expect("FIXTURE_TECNICA structurally valid changed binding");
    let requests = finish_runtime_server(socket.clone(), server);
    assert!(matches!(requests.as_slice(), [Request::Interpret { .. }]));

    assert_eq!(
        interpret_submission(&socket, &catalog, channel_binding(2), &changed_binding,)
            .expect_err("FIXTURE_TECNICA cross-session continuation"),
        RuntimeError::InvalidSubmission
    );
}

#[test]
fn clarification_continue_confirm_flow_emits_a_graph_only_after_acceptance() {
    let (socket, catalog, server) = runtime_server(3);
    let pending = begin_confirmation(&socket, &catalog);
    let selected_registry = pending
        .selection
        .id()
        .as_str()
        .strip_prefix("ha_entity:id_")
        .expect("FIXTURE_TECNICA core entity prefix")
        .to_owned();
    let confirm = Request::Confirm {
        session_id: pending.session_id,
        confirmation_id: pending.confirmation_id,
        plan: pending.plan.clone(),
    };
    let confirmation = protocol_submission(&confirm);
    assert_protocol_submission(&confirmation, &confirm, CompanionSubmissionKind::Confirm);
    let encoded_confirm = v2::encode_request(&confirm).expect("FIXTURE_TECNICA confirmation");
    assert_eq!(
        CompanionSubmission::from_protocol_v2(
            ADMITTED_ALIAS.to_owned(),
            CONTEXT_ID.to_owned(),
            CALLER_ID.to_owned(),
            Some(CONVERSATION_ID.to_owned()),
            &encoded_confirm,
        )
        .expect_err("FIXTURE_TECNICA mismatched confirmation source"),
        RuntimeError::InvalidSubmission
    );

    let accepted = interpret_submission(&socket, &catalog, channel_binding(3), &confirmation)
        .expect("FIXTURE_TECNICA accepted graph");
    let graph = nlu_data::parse_strict_json(&accepted, "FIXTURE_TECNICA execution graph")
        .expect("FIXTURE_TECNICA graph JSON");
    assert!(graph.get("kind").is_none());
    assert!(graph.get("response").is_none());
    assert_eq!(graph["nodes"].as_array().map(Vec::len), Some(1));
    assert_eq!(
        graph["nodes"][0]["selector"]["registry_entry_ids"][0],
        selected_registry
    );

    let requests = finish_runtime_server(socket, server);
    assert_eq!(
        requests,
        vec![
            Request::Interpret {
                session_id: pending.session_id,
                text: source(),
            },
            Request::Continue {
                session_id: pending.session_id,
                selection: pending.selection,
            },
            confirm,
        ]
    );
}

#[test]
fn cancellation_relay_clears_continuation_and_never_emits_a_graph() {
    let (socket, catalog, server) = runtime_server(3);
    let interpreted = interpret_submission(&socket, &catalog, channel_binding(1), &submission())
        .expect("FIXTURE_TECNICA interpreted clarification");
    let interpreted = decode_typed_outcome(&interpreted);
    let Outcome::EntityClarification(clarification) = interpreted.outcome() else {
        panic!("FIXTURE_TECNICA expected entity clarification");
    };
    let session_id = clarification.session_id();
    let selection = clarification.referents()[0].clone();

    let cancel = Request::Cancel { session_id };
    let cancellation = protocol_submission(&cancel);
    assert_protocol_submission(&cancellation, &cancel, CompanionSubmissionKind::Cancel);
    let cancelled = interpret_submission(&socket, &catalog, channel_binding(2), &cancellation)
        .expect("FIXTURE_TECNICA cancellation relay");
    let cancelled = decode_typed_outcome(&cancelled);
    assert_eq!(
        cancelled.outcome(),
        &Outcome::Cancellation(CancellationStatus::Cancelled)
    );

    let continuation = Request::Continue {
        session_id,
        selection: selection.clone(),
    };
    let after_cancel = protocol_submission(&continuation);
    let unavailable = interpret_submission(&socket, &catalog, channel_binding(3), &after_cancel)
        .expect("FIXTURE_TECNICA unavailable continuation relay");
    let unavailable = decode_typed_outcome(&unavailable);
    assert!(matches!(
        unavailable.outcome(),
        Outcome::Abstention(nlu_core::AbstentionReason::Unsupported)
    ));
    assert_eq!(
        unavailable.diagnostics()[0].code(),
        DiagnosticCode::SessionUnavailable
    );

    let requests = finish_runtime_server(socket, server);
    assert_eq!(
        requests,
        vec![
            Request::Interpret {
                session_id,
                text: source(),
            },
            cancel,
            continuation,
        ]
    );
}

#[test]
fn changed_confirmation_id_is_typed_policy_denial_without_a_graph() {
    let (socket, catalog, server) = runtime_server(3);
    let pending = begin_confirmation(&socket, &catalog);
    let changed_id = ConfirmationId::new(
        pending
            .confirmation_id
            .get()
            .checked_add(1)
            .expect("FIXTURE_TECNICA bounded confirmation identity"),
    )
    .expect("FIXTURE_TECNICA changed confirmation identity");
    let changed = Request::Confirm {
        session_id: pending.session_id,
        confirmation_id: changed_id,
        plan: pending.plan,
    };
    let changed_submission = protocol_submission(&changed);
    let denied = interpret_submission(&socket, &catalog, channel_binding(3), &changed_submission)
        .expect("FIXTURE_TECNICA changed confirmation relay");
    let denied = decode_typed_outcome(&denied);
    assert_eq!(
        denied.outcome(),
        &Outcome::PolicyDenial(PolicyDenialReason::ConfirmationMismatch)
    );

    let requests = finish_runtime_server(socket, server);
    assert_eq!(requests.last(), Some(&changed));
}

#[test]
fn changed_plan_binding_is_typed_policy_denial_without_a_graph() {
    let (socket, catalog, server) = runtime_server(3);
    let pending = begin_confirmation(&socket, &catalog);
    let original = Request::Confirm {
        session_id: pending.session_id,
        confirmation_id: pending.confirmation_id,
        plan: pending.plan,
    };
    let encoded = v2::encode_request(&original).expect("FIXTURE_TECNICA confirmation request");
    let mut value = nlu_data::parse_strict_json(&encoded, "FIXTURE_TECNICA confirmation request")
        .expect("FIXTURE_TECNICA confirmation JSON");
    let entity_id = value
        .pointer_mut("/request/plan/nodes/0/slots/0/value/id")
        .expect("FIXTURE_TECNICA plan entity");
    *entity_id = Value::String(pending.alternate.id().as_str().to_owned());
    let changed_bytes =
        nlu_data::canonical_json(&value, "FIXTURE_TECNICA changed confirmation request")
            .expect("FIXTURE_TECNICA changed confirmation JSON");
    let changed =
        v2::decode_request(&changed_bytes).expect("FIXTURE_TECNICA valid changed confirmation");
    assert_ne!(changed, original);
    let changed_submission = CompanionSubmission::from_protocol_v2(
        ADMITTED_REQUEST.to_owned(),
        CONTEXT_ID.to_owned(),
        CALLER_ID.to_owned(),
        Some(CONVERSATION_ID.to_owned()),
        &changed_bytes,
    )
    .expect("FIXTURE_TECNICA changed plan submission");
    assert_protocol_submission(
        &changed_submission,
        &changed,
        CompanionSubmissionKind::Confirm,
    );

    let denied = interpret_submission(&socket, &catalog, channel_binding(3), &changed_submission)
        .expect("FIXTURE_TECNICA changed plan relay");
    let denied = decode_typed_outcome(&denied);
    assert_eq!(
        denied.outcome(),
        &Outcome::PolicyDenial(PolicyDenialReason::ConfirmationMismatch)
    );

    let requests = finish_runtime_server(socket, server);
    assert_eq!(requests.last(), Some(&changed));
}

#[test]
fn authenticated_helper_reply_is_typed_bounded_and_framed() {
    let wire = br#"{"direction":"adapter_to_companion","version":1}"#;
    let reply = HelperReply::authenticated(wire, true).expect("FIXTURE_TECNICA reply");
    let mut framed = Vec::new();
    write_helper_reply(&mut framed, &reply).expect("FIXTURE_TECNICA write");
    let decoded = read_helper_reply(&mut Cursor::new(framed)).expect("FIXTURE_TECNICA read");
    assert_eq!(decoded, reply);
    let authenticated = decoded
        .authenticated_wire()
        .expect("FIXTURE_TECNICA wire")
        .expect("authenticated variant");
    let parsed: Value =
        serde_json::from_slice(&authenticated).expect("FIXTURE_TECNICA authenticated JSON");
    assert_eq!(parsed["direction"], "adapter_to_companion");

    assert_eq!(
        HelperReply::decode(
            br#"{"kind":"authenticated_request","opened_connection":true,"version":1,"wire":"raw"}"#
        )
        .expect_err("raw payload is prohibited"),
        RuntimeError::InvalidHelperReply
    );
}
