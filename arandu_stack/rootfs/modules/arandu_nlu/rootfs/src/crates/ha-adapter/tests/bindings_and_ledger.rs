mod support;

use ha_adapter::{
    AdapterError, AuthenticatedRequest, CallerId, CapabilityId, CatalogGeneration, ChannelGuard,
    ContextId, DeliveryKind, Direction, EntityState, IndeterminateReason, LedgerConfig,
    LedgerStatus, LogicalTime, NodeAttemptId, OperationId, OperationLedger, ReservationOutcome,
    TypedOperation,
};
use support::{digest, epoch, nonce, parts, reconnect, request, session, state_result, target};

#[test]
fn strict_request_constructor_rejects_invalid_or_mismatched_fields() {
    let operation = TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read");

    let mut candidate = parts(1, 1, 1, DeliveryKind::Initial, operation.clone());
    candidate.protocol_version = 2;
    assert_eq!(
        AuthenticatedRequest::new(candidate),
        Err(AdapterError::InvalidProtocolVersion)
    );

    let mut candidate = parts(1, 1, 1, DeliveryKind::Initial, operation.clone());
    candidate.direction = Direction::CompanionToAdapter;
    assert_eq!(
        AuthenticatedRequest::new(candidate),
        Err(AdapterError::WrongDirection)
    );

    let mut candidate = parts(1, 1, 1, DeliveryKind::Initial, operation);
    candidate.sequence = 0;
    assert_eq!(
        AuthenticatedRequest::new(candidate),
        Err(AdapterError::InvalidSequence)
    );

    let mut candidate = parts(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    candidate.capability =
        CapabilityId::new("ha:light_control").expect("FIXTURE_TECNICA capability");
    assert_eq!(
        AuthenticatedRequest::new(candidate),
        Err(AdapterError::CapabilityOperationMismatch)
    );

    assert_eq!(OperationId::new(0), Err(AdapterError::InvalidOperationId));
    assert_eq!(
        NodeAttemptId::new(0),
        Err(AdapterError::InvalidNodeAttemptId)
    );
    assert_eq!(
        CatalogGeneration::new(0),
        Err(AdapterError::InvalidCatalogGeneration)
    );
    assert!(ContextId::new("").is_err());
    assert!(CallerId::new("fixture tecnica").is_err());
}

#[test]
fn every_authenticated_field_changes_the_transcript_or_fails_construction() {
    let base_parts = parts(
        11,
        1,
        5,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let base = AuthenticatedRequest::new(base_parts.clone()).expect("FIXTURE_TECNICA request");
    let baseline = base.authenticated_transcript();

    let mut mutations = Vec::new();

    let mut changed = base_parts.clone();
    changed.epoch = epoch(9);
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.connection_nonce = nonce(9);
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.sequence = 6;
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.delivery = DeliveryKind::Retry;
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.operation_id = OperationId::new(12).expect("FIXTURE_TECNICA operation");
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.node_attempt_id = NodeAttemptId::new(2).expect("FIXTURE_TECNICA attempt");
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.plan_digest = digest(8);
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.session_id = session(8);
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.catalog_generation = CatalogGeneration::new(8).expect("FIXTURE_TECNICA generation");
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.context_id =
        ContextId::new("fixture_tecnica_context_02").expect("FIXTURE_TECNICA context");
    mutations.push(changed);
    let mut changed = base_parts.clone();
    changed.caller_id = CallerId::new("fixture_tecnica_caller_02").expect("FIXTURE_TECNICA caller");
    mutations.push(changed);
    let mut changed = base_parts;
    changed.operation = TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read");
    mutations.push(changed);

    for mutation in mutations {
        let request = AuthenticatedRequest::new(mutation).expect("valid one-field mutation");
        assert_ne!(request.authenticated_transcript(), baseline);
    }
}

#[test]
fn debug_output_redacts_identifiers_targets_and_residential_bindings() {
    let request = request(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(0xed)).expect("FIXTURE_TECNICA read"),
    );
    let rendered = format!("{request:?}");
    for marker in [
        "fixture_tecnica_context_01",
        "fixture_tecnica_caller_01",
        "000000000000000000000000000000ed",
    ] {
        assert!(!rendered.contains(marker));
    }
}

#[test]
fn channel_guard_rejects_replay_reflection_cross_connection_and_old_epoch() {
    let first = request(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let mut guard = ChannelGuard::new(epoch(1), Direction::AdapterToCompanion, nonce(2), 1)
        .expect("FIXTURE_TECNICA channel");
    assert_eq!(guard.accept(&first), Ok(()));
    assert_eq!(guard.accept(&first), Err(AdapterError::UnexpectedSequence));

    let wrong_connection = reconnect(&first, 8, 2, DeliveryKind::Retry);
    assert_eq!(
        guard.accept(&wrong_connection),
        Err(AdapterError::WrongConnection)
    );

    let mut changed = parts(1, 1, 2, DeliveryKind::Retry, first.operation().clone());
    changed.epoch = epoch(8);
    let old_epoch = AuthenticatedRequest::new(changed).expect("FIXTURE_TECNICA old epoch");
    assert_eq!(guard.accept(&old_epoch), Err(AdapterError::WrongEpoch));

    let second = AuthenticatedRequest::new({
        let mut value = parts(1, 1, 2, DeliveryKind::Retry, first.operation().clone());
        value.connection_nonce = nonce(2);
        value
    })
    .expect("FIXTURE_TECNICA second request");
    assert_eq!(guard.accept(&second), Ok(()));
    guard.revoke();
    assert_eq!(guard.accept(&second), Err(AdapterError::ChannelRevoked));
}

#[test]
fn ledger_reserves_before_dispatch_and_handles_all_duplicate_states() {
    let request = request(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let ledger = OperationLedger::new(
        LedgerConfig::new(8, 100).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    );

    assert_eq!(
        ledger.reserve(&request, LogicalTime::from_ticks(1)),
        Ok(ReservationOutcome::Reserved)
    );
    let retry = reconnect(&request, 9, 77, DeliveryKind::Retry);
    assert_eq!(
        ledger.reserve(&retry, LogicalTime::from_ticks(2)),
        Ok(ReservationOutcome::DuplicateReserved)
    );

    ledger
        .begin(&request, LogicalTime::from_ticks(3))
        .expect("begin operation");
    assert_eq!(
        ledger.reserve(&retry, LogicalTime::from_ticks(4)),
        Ok(ReservationOutcome::DuplicateInFlight)
    );

    let completed = state_result(&request, EntityState::On);
    ledger
        .complete(&request, completed.clone(), LogicalTime::from_ticks(5))
        .expect("complete operation");
    assert_eq!(
        ledger.reserve(&retry, LogicalTime::from_ticks(6)),
        Ok(ReservationOutcome::Cached(completed))
    );
    assert_eq!(
        ledger.status(&retry, LogicalTime::from_ticks(7)),
        Ok(LedgerStatus::Completed)
    );
}

#[test]
fn ledger_rejects_binding_substitution_unknown_retries_and_timeout_redispatch() {
    let initial = request(
        10,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::turn_on(target(1)).expect("FIXTURE_TECNICA turn on"),
    );
    let ledger = OperationLedger::new(
        LedgerConfig::new(8, 100).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    );
    ledger
        .reserve(&initial, LogicalTime::from_ticks(1))
        .expect("reserve");
    ledger
        .begin(&initial, LogicalTime::from_ticks(2))
        .expect("begin");
    ledger
        .mark_indeterminate(
            &initial,
            IndeterminateReason::Timeout,
            LogicalTime::from_ticks(3),
        )
        .expect("mark timeout");
    let retry = reconnect(&initial, 7, 50, DeliveryKind::Retry);
    assert_eq!(
        ledger.reserve(&retry, LogicalTime::from_ticks(4)),
        Ok(ReservationOutcome::Indeterminate(
            IndeterminateReason::Timeout
        ))
    );

    let mut substituted = parts(10, 1, 51, DeliveryKind::Retry, initial.operation().clone());
    substituted.connection_nonce = nonce(7);
    substituted.plan_digest = digest(9);
    let substituted = AuthenticatedRequest::new(substituted).expect("FIXTURE_TECNICA substitution");
    assert_eq!(
        ledger.reserve(&substituted, LogicalTime::from_ticks(5)),
        Err(AdapterError::BindingMismatch)
    );

    let unknown = request(
        99,
        1,
        1,
        DeliveryKind::Retry,
        TypedOperation::turn_on(target(2)).expect("FIXTURE_TECNICA turn on"),
    );
    assert_eq!(
        ledger.reserve(&unknown, LogicalTime::from_ticks(6)),
        Err(AdapterError::UnknownDuplicate)
    );
}

#[test]
fn expiry_capacity_and_high_water_never_turn_old_work_into_new_work() {
    let ledger = OperationLedger::new(
        LedgerConfig::new(1, 2).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    );
    let first = request(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    ledger
        .reserve(&first, LogicalTime::from_ticks(0))
        .expect("reserve first");
    assert_eq!(ledger.purge_expired(LogicalTime::from_ticks(2)), Ok(1));
    let first_retry = reconnect(&first, 9, 1, DeliveryKind::Retry);
    assert_eq!(
        ledger.reserve(&first_retry, LogicalTime::from_ticks(2)),
        Ok(ReservationOutcome::Expired)
    );

    let second = request(
        2,
        1,
        2,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read"),
    );
    assert_eq!(
        ledger.reserve(&second, LogicalTime::from_ticks(2)),
        Ok(ReservationOutcome::Reserved)
    );
    assert_eq!(
        ledger.reserve(&first, LogicalTime::from_ticks(3)),
        Err(AdapterError::UnknownDuplicate)
    );
}

#[test]
fn in_flight_retention_expiry_remains_indeterminate() {
    let ledger = OperationLedger::new(
        LedgerConfig::new(1, 2).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    );
    let initial = request(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::turn_on(target(1)).expect("FIXTURE_TECNICA turn on"),
    );
    ledger
        .reserve(&initial, LogicalTime::from_ticks(0))
        .expect("reserve");
    ledger
        .begin(&initial, LogicalTime::from_ticks(1))
        .expect("begin");
    assert_eq!(ledger.purge_expired(LogicalTime::from_ticks(2)), Ok(1));
    let retry = reconnect(&initial, 9, 10, DeliveryKind::Retry);
    assert_eq!(
        ledger.reserve(&retry, LogicalTime::from_ticks(2)),
        Ok(ReservationOutcome::Indeterminate(
            IndeterminateReason::Timeout
        ))
    );
}

#[test]
fn rollback_restart_and_epoch_rotation_invalidate_all_old_operations() {
    let ledger = OperationLedger::new(
        LedgerConfig::new(4, 100).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    );
    let old = request(
        1,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    ledger
        .reserve(&old, LogicalTime::from_ticks(10))
        .expect("reserve old");
    assert_eq!(
        ledger.status(&old, LogicalTime::from_ticks(9)),
        Err(AdapterError::ClockRollback)
    );
    assert!(!ledger.diagnostics().expect("diagnostics").active());
    assert_eq!(
        ledger.activate_epoch(epoch(1)),
        Err(AdapterError::EpochReuse)
    );
    ledger.activate_epoch(epoch(2)).expect("activate new epoch");
    assert_eq!(
        ledger.reserve(&old, LogicalTime::from_ticks(0)),
        Err(AdapterError::WrongEpoch)
    );

    assert_eq!(ledger.invalidate_for_restart(), Ok(0));
    assert_eq!(
        ledger.activate_epoch(epoch(2)),
        Err(AdapterError::EpochReuse)
    );
    ledger
        .activate_epoch(epoch(3))
        .expect("re-pair after restart");
    assert_eq!(
        ledger.rotate_epoch(epoch(4)),
        Ok(0),
        "rotation revokes the active epoch even with no entries"
    );
    assert_eq!(
        ledger.rotate_epoch(epoch(1)),
        Err(AdapterError::EpochReuse),
        "every retired epoch remains rejected"
    );
}

#[test]
fn retired_epoch_history_is_bounded_and_fails_closed_when_full() {
    let ledger = OperationLedger::new(
        LedgerConfig::new(4, 100).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    );
    for tag in 2..=65 {
        ledger
            .rotate_epoch(epoch(tag))
            .expect("FIXTURE_TECNICA bounded epoch rotation");
    }
    assert_eq!(
        ledger.rotate_epoch(epoch(66)),
        Err(AdapterError::EpochHistoryExhausted)
    );
    assert_eq!(ledger.rotate_epoch(epoch(1)), Err(AdapterError::EpochReuse));
    let diagnostics = ledger.diagnostics().expect("diagnostics");
    assert_eq!(diagnostics.retired_epochs(), 64);
    assert!(!diagnostics.epoch_history_exhausted());
}
