use addon_runtime::{
    CompanionExecutionClass, CompanionGraph, CompanionNode, RuntimeError, projected_plan_digest,
};
use ha_adapter::{
    AUTHENTICATED_PROTOCOL_VERSION, AuthenticatedRequest, AuthenticatedRequestParts, CallerId,
    CapabilityId, CatalogGeneration, ConnectionNonce, ContextId, DeliveryKind, Direction,
    NodeAttemptId, OperationId, PairingEpoch, PlanDigest, RegistryEntryId, SessionId,
    TypedOperation,
};
use ha_catalog::ExternalEntityId;
use serde_json::Value;

fn request(
    plan_digest: [u8; 32],
    sequence: u64,
    attempt: u16,
    operation: TypedOperation,
) -> AuthenticatedRequest {
    AuthenticatedRequest::new(AuthenticatedRequestParts {
        protocol_version: AUTHENTICATED_PROTOCOL_VERSION,
        epoch: PairingEpoch::new([1; 32]).expect("FIXTURE_TECNICA epoch"),
        direction: Direction::AdapterToCompanion,
        connection_nonce: ConnectionNonce::new([2; 32]).expect("FIXTURE_TECNICA nonce"),
        sequence,
        delivery: DeliveryKind::Initial,
        operation_id: OperationId::new(9).expect("FIXTURE_TECNICA operation identity"),
        node_attempt_id: NodeAttemptId::new(attempt).expect("FIXTURE_TECNICA attempt"),
        plan_digest: PlanDigest::new(plan_digest).expect("FIXTURE_TECNICA plan digest"),
        session_id: SessionId::new([4; 32]).expect("FIXTURE_TECNICA session"),
        capability: CapabilityId::new(operation.kind().required_capability())
            .expect("FIXTURE_TECNICA capability"),
        catalog_generation: CatalogGeneration::new(7).expect("FIXTURE_TECNICA generation"),
        context_id: ContextId::new("fixture_context").expect("FIXTURE_TECNICA context"),
        caller_id: CallerId::new("fixture_caller").expect("FIXTURE_TECNICA caller"),
        operation,
    })
    .expect("FIXTURE_TECNICA authenticated request")
}

fn target(value: u8) -> RegistryEntryId {
    RegistryEntryId::new(&format!("{value:032x}")).expect("FIXTURE_TECNICA registry identity")
}

fn external(value: &str) -> ExternalEntityId {
    ExternalEntityId::new(value).expect("FIXTURE_TECNICA external identity")
}

fn node(plan_digest: [u8; 32]) -> CompanionNode {
    CompanionNode::new(
        "fixture:node_a",
        request(
            plan_digest,
            1,
            1,
            TypedOperation::turn_off(target(1)).expect("FIXTURE_TECNICA operation"),
        ),
        vec![external("switch.fixture_a")],
        Vec::new(),
        1_000,
    )
    .expect("FIXTURE_TECNICA companion node")
}

#[test]
fn fixed_width_identities_and_canonical_plan_digest_match_the_python_contract() {
    let provisional = CompanionGraph::new(
        CompanionExecutionClass::PartialSafe,
        [5; 32],
        vec![node([9; 32])],
    )
    .expect_err("provisional digest must not pass");
    assert_eq!(provisional, RuntimeError::PlanDigestMismatch);

    let projection_node = node([9; 32]);
    let digest = projected_plan_digest(CompanionExecutionClass::PartialSafe, &[projection_node])
        .expect("FIXTURE_TECNICA canonical digest");
    let graph = CompanionGraph::new(
        CompanionExecutionClass::PartialSafe,
        [5; 32],
        vec![node(digest)],
    )
    .expect("FIXTURE_TECNICA graph");
    let encoded = graph.encode().expect("FIXTURE_TECNICA wire encoding");
    let value: Value = serde_json::from_slice(&encoded).expect("FIXTURE_TECNICA JSON");

    assert_eq!(value["operation_id"], "00000000000000000000000000000009");
    assert_eq!(
        value["nodes"][0]["node_attempt_id"],
        "00000000000000000000000000000001"
    );
    assert_eq!(value["direction"], "adapter_to_companion");
    assert_eq!(value["nodes"][0]["operation"], "ha:turn_off");
    assert_eq!(
        value["plan_digest"],
        nlu_data::sha256_hex(
            nlu_data::canonical_json(
                &serde_json::json!({
                    "catalog_generation": 7,
                    "execution_class": "partial_safe",
                    "nodes": [value["nodes"][0].clone()],
                }),
                "FIXTURE_TECNICA projection",
            )
            .expect("FIXTURE_TECNICA canonical projection")
            .as_slice()
        )
        .expect("FIXTURE_TECNICA digest")
    );
}

#[test]
fn graph_rejects_target_and_binding_substitution() {
    let mismatched_target = CompanionNode::new(
        "fixture:node_a",
        request(
            [9; 32],
            1,
            1,
            TypedOperation::turn_off(target(1)).expect("FIXTURE_TECNICA operation"),
        ),
        vec![external("switch.fixture_a"), external("switch.fixture_b")],
        Vec::new(),
        1_000,
    )
    .expect_err("target count substitution");
    assert_eq!(mismatched_target, RuntimeError::TargetBindingMismatch);

    let first = node([9; 32]);
    let second = CompanionNode::new(
        "fixture:node_b",
        request(
            [9; 32],
            3,
            2,
            TypedOperation::turn_off(target(2)).expect("FIXTURE_TECNICA operation"),
        ),
        vec![external("switch.fixture_b")],
        Vec::new(),
        1_000,
    )
    .expect("FIXTURE_TECNICA second node");
    assert_eq!(
        CompanionGraph::new(
            CompanionExecutionClass::PartialSafe,
            [5; 32],
            vec![first, second],
        )
        .expect_err("sequence gap"),
        RuntimeError::SequenceMismatch
    );
}

#[test]
fn atomic_graph_accepts_only_multiple_independent_state_reads() {
    let effect = node([9; 32]);
    assert_eq!(
        CompanionGraph::new(CompanionExecutionClass::AtomicOnly, [5; 32], vec![effect],)
            .expect_err("one effect is not an atomic snapshot"),
        RuntimeError::InvalidExecutionClass
    );
}
