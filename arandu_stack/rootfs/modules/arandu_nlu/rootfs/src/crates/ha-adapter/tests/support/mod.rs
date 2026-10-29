#![allow(dead_code)]

use ha_adapter::{
    AUTHENTICATED_PROTOCOL_VERSION, AuthenticatedRequest, AuthenticatedRequestParts, CallerId,
    CapabilityId, CatalogGeneration, ConnectionNonce, ContextId, DeliveryKind, Direction,
    EntitySnapshot, EntityState, ExecutionSuccess, NodeAttemptId, NodeExecutionResult, OperationId,
    PairingEpoch, PlanDigest, RegistryEntryId, SessionId, TypedOperation,
};

pub fn epoch(tag: u8) -> PairingEpoch {
    PairingEpoch::new([tag; 32]).expect("FIXTURE_TECNICA epoch")
}

pub fn nonce(tag: u8) -> ConnectionNonce {
    ConnectionNonce::new([tag; 32]).expect("FIXTURE_TECNICA nonce")
}

pub fn digest(tag: u8) -> PlanDigest {
    PlanDigest::new([tag; 32]).expect("FIXTURE_TECNICA digest")
}

pub fn session(tag: u8) -> SessionId {
    SessionId::new([tag; 32]).expect("FIXTURE_TECNICA session")
}

pub fn target(value: u8) -> RegistryEntryId {
    RegistryEntryId::new(&format!("{value:032x}")).expect("FIXTURE_TECNICA target")
}

pub fn parts(
    operation_id: u64,
    attempt: u16,
    sequence: u64,
    delivery: DeliveryKind,
    operation: TypedOperation,
) -> AuthenticatedRequestParts {
    AuthenticatedRequestParts {
        protocol_version: AUTHENTICATED_PROTOCOL_VERSION,
        epoch: epoch(1),
        direction: Direction::AdapterToCompanion,
        connection_nonce: nonce(2),
        sequence,
        delivery,
        operation_id: OperationId::new(operation_id).expect("FIXTURE_TECNICA operation ID"),
        node_attempt_id: NodeAttemptId::new(attempt).expect("FIXTURE_TECNICA attempt ID"),
        plan_digest: digest(3),
        session_id: session(4),
        capability: CapabilityId::new(operation.kind().required_capability())
            .expect("FIXTURE_TECNICA capability"),
        catalog_generation: CatalogGeneration::new(7).expect("FIXTURE_TECNICA catalog generation"),
        context_id: ContextId::new("fixture_tecnica_context_01").expect("FIXTURE_TECNICA context"),
        caller_id: CallerId::new("fixture_tecnica_caller_01").expect("FIXTURE_TECNICA caller"),
        operation,
    }
}

pub fn request(
    operation_id: u64,
    attempt: u16,
    sequence: u64,
    delivery: DeliveryKind,
    operation: TypedOperation,
) -> AuthenticatedRequest {
    AuthenticatedRequest::new(parts(operation_id, attempt, sequence, delivery, operation))
        .expect("FIXTURE_TECNICA authenticated request")
}

pub fn reconnect(
    original: &AuthenticatedRequest,
    nonce_tag: u8,
    sequence: u64,
    delivery: DeliveryKind,
) -> AuthenticatedRequest {
    AuthenticatedRequest::new(AuthenticatedRequestParts {
        protocol_version: original.protocol_version().get(),
        epoch: original.epoch(),
        direction: original.direction(),
        connection_nonce: nonce(nonce_tag),
        sequence,
        delivery,
        operation_id: original.operation_id(),
        node_attempt_id: original.node_attempt_id(),
        plan_digest: original.plan_digest(),
        session_id: original.session_id(),
        capability: original.capability().clone(),
        catalog_generation: original.catalog_generation(),
        context_id: original.context_id().clone(),
        caller_id: original.caller_id().clone(),
        operation: original.operation().clone(),
    })
    .expect("FIXTURE_TECNICA reconnect request")
}

pub fn state_result(request: &AuthenticatedRequest, state: EntityState) -> NodeExecutionResult {
    NodeExecutionResult::Succeeded(ExecutionSuccess::StateRead(EntitySnapshot::new(
        request.catalog_generation(),
        request.operation().targets().as_slice()[0].clone(),
        state,
    )))
}
