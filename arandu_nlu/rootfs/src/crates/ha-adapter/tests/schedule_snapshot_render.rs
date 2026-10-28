mod support;

use std::collections::{BTreeMap, VecDeque};

use ha_adapter::{
    AdapterError, BundledSnapshotContract, CatalogGeneration, DeliveryKind, EntityState,
    ExecutionFailure, ExecutionRenderOutcome, ExecutionSchedule, ExecutionSuccess,
    IndeterminateReason, InterpretationOutcome, InterpretationReason, LedgerConfig, LedgerStatus,
    LogicalTime, NodeExecutionResult, NodeExecutor, NodeRevalidator, OperationKind,
    OperationLedger, ReadOnlySnapshotSource, RevalidationDecision, RevalidationFailure,
    ScheduleState, SnapshotSourceError, TargetSet, TypedOperation,
};
use support::{epoch, parts, reconnect, request, state_result, target};

struct MockRevalidator {
    decisions: VecDeque<RevalidationDecision>,
    prior_counts: Vec<usize>,
}

impl MockRevalidator {
    fn allowing() -> Self {
        Self {
            decisions: VecDeque::new(),
            prior_counts: Vec::new(),
        }
    }
}

impl NodeRevalidator for MockRevalidator {
    fn revalidate(
        &mut self,
        _request: &ha_adapter::AuthenticatedRequest,
        prior_results: &[NodeExecutionResult],
    ) -> RevalidationDecision {
        self.prior_counts.push(prior_results.len());
        self.decisions
            .pop_front()
            .unwrap_or(RevalidationDecision::Allow)
    }
}

struct MockExecutor {
    results: VecDeque<NodeExecutionResult>,
    calls: usize,
}

impl MockExecutor {
    fn new(results: Vec<NodeExecutionResult>) -> Self {
        Self {
            results: results.into(),
            calls: 0,
        }
    }
}

impl NodeExecutor for MockExecutor {
    fn execute(&mut self, _request: &ha_adapter::AuthenticatedRequest) -> NodeExecutionResult {
        self.calls += 1;
        self.results
            .pop_front()
            .expect("FIXTURE_TECNICA executor result")
    }
}

fn ledger() -> OperationLedger {
    OperationLedger::new(
        LedgerConfig::new(32, 1_000).expect("FIXTURE_TECNICA ledger config"),
        epoch(1),
    )
}

#[test]
fn schedule_revalidates_each_node_and_stops_after_first_failure() {
    let first = request(
        20,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let second = request(
        20,
        2,
        2,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read"),
    );
    let third = request(
        20,
        3,
        3,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(3)).expect("FIXTURE_TECNICA read"),
    );
    let ledger = ledger();
    let mut schedule =
        ExecutionSchedule::new(vec![first.clone(), second, third.clone()]).expect("schedule");
    schedule
        .prepare(&ledger, LogicalTime::from_ticks(0))
        .expect("prepare");

    let mut revalidator = MockRevalidator::allowing();
    let mut executor = MockExecutor::new(vec![
        state_result(&first, EntityState::On),
        NodeExecutionResult::Failed(ExecutionFailure::RejectedBeforeEffect),
        state_result(&third, EntityState::Off),
    ]);
    assert_eq!(
        schedule
            .advance(
                &ledger,
                LogicalTime::from_ticks(1),
                &mut revalidator,
                &mut executor,
            )
            .expect("first node")
            .state(),
        ScheduleState::Ready { next_node: 1 }
    );
    assert_eq!(
        schedule.report().render_outcome(),
        ExecutionRenderOutcome::InProgress { completed_nodes: 1 }
    );
    assert_eq!(
        schedule
            .advance(
                &ledger,
                LogicalTime::from_ticks(2),
                &mut revalidator,
                &mut executor,
            )
            .expect("second node")
            .state(),
        ScheduleState::Stopped
    );
    assert_eq!(revalidator.prior_counts, vec![0, 1]);
    assert_eq!(executor.calls, 2);
    assert_eq!(
        schedule.report().render_outcome(),
        ExecutionRenderOutcome::Stopped {
            completed_nodes: 1,
            failure: ExecutionFailure::RejectedBeforeEffect,
        }
    );
    assert_eq!(
        ledger.status(&third, LogicalTime::from_ticks(3)),
        Ok(LedgerStatus::Reserved)
    );
    assert_eq!(
        schedule.advance(
            &ledger,
            LogicalTime::from_ticks(3),
            &mut revalidator,
            &mut executor,
        ),
        Err(AdapterError::ScheduleFinished)
    );
}

#[test]
fn revalidation_denial_is_cached_without_dispatching_the_denied_node() {
    let first = request(
        21,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let second = request(
        21,
        2,
        2,
        DeliveryKind::Initial,
        TypedOperation::turn_on(target(2)).expect("FIXTURE_TECNICA turn on"),
    );
    let ledger = ledger();
    let mut schedule =
        ExecutionSchedule::new(vec![first.clone(), second.clone()]).expect("schedule");
    schedule
        .prepare(&ledger, LogicalTime::from_ticks(0))
        .expect("prepare");
    let mut revalidator = MockRevalidator {
        decisions: vec![
            RevalidationDecision::Allow,
            RevalidationDecision::Deny(RevalidationFailure::PermissionDenied),
        ]
        .into(),
        prior_counts: Vec::new(),
    };
    let mut executor = MockExecutor::new(vec![state_result(&first, EntityState::On)]);

    schedule
        .advance(
            &ledger,
            LogicalTime::from_ticks(1),
            &mut revalidator,
            &mut executor,
        )
        .expect("first");
    let denied = schedule
        .advance(
            &ledger,
            LogicalTime::from_ticks(2),
            &mut revalidator,
            &mut executor,
        )
        .expect("denied");
    assert_eq!(
        denied.result(),
        &NodeExecutionResult::Failed(ExecutionFailure::Revalidation(
            RevalidationFailure::PermissionDenied
        ))
    );
    assert_eq!(executor.calls, 1);

    let retry = reconnect(&second, 9, 100, DeliveryKind::Retry);
    assert_eq!(
        ledger.reserve(&retry, LogicalTime::from_ticks(3)),
        Ok(ha_adapter::ReservationOutcome::Cached(
            NodeExecutionResult::Failed(ExecutionFailure::Revalidation(
                RevalidationFailure::PermissionDenied
            ))
        ))
    );
}

#[test]
fn completed_duplicate_schedule_returns_cache_without_revalidation_or_effect() {
    let first = request(
        30,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let second = request(
        30,
        2,
        2,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read"),
    );
    let ledger = ledger();
    let mut original =
        ExecutionSchedule::new(vec![first.clone(), second.clone()]).expect("original schedule");
    original
        .prepare(&ledger, LogicalTime::from_ticks(0))
        .expect("prepare original");
    let mut original_revalidator = MockRevalidator::allowing();
    let mut original_executor = MockExecutor::new(vec![
        state_result(&first, EntityState::On),
        state_result(&second, EntityState::Off),
    ]);
    original
        .advance(
            &ledger,
            LogicalTime::from_ticks(1),
            &mut original_revalidator,
            &mut original_executor,
        )
        .expect("first");
    original
        .advance(
            &ledger,
            LogicalTime::from_ticks(2),
            &mut original_revalidator,
            &mut original_executor,
        )
        .expect("second");
    assert_eq!(original.state(), ScheduleState::Completed);

    let retry_first = reconnect(&first, 9, 100, DeliveryKind::Retry);
    let retry_second = reconnect(&second, 9, 101, DeliveryKind::Retry);
    let mut duplicate =
        ExecutionSchedule::new(vec![retry_first, retry_second]).expect("duplicate schedule");
    duplicate
        .prepare(&ledger, LogicalTime::from_ticks(3))
        .expect("prepare duplicate");
    let mut duplicate_revalidator = MockRevalidator::allowing();
    let mut duplicate_executor = MockExecutor::new(Vec::new());
    duplicate
        .advance(
            &ledger,
            LogicalTime::from_ticks(4),
            &mut duplicate_revalidator,
            &mut duplicate_executor,
        )
        .expect("cached first");
    duplicate
        .advance(
            &ledger,
            LogicalTime::from_ticks(5),
            &mut duplicate_revalidator,
            &mut duplicate_executor,
        )
        .expect("cached second");
    assert_eq!(duplicate.state(), ScheduleState::Completed);
    assert_eq!(duplicate_executor.calls, 0);
    assert!(duplicate_revalidator.prior_counts.is_empty());
}

#[test]
fn indeterminate_duplicate_schedule_never_dispatches_again() {
    let first = request(
        31,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::turn_on(target(1)).expect("FIXTURE_TECNICA turn on"),
    );
    let ledger = ledger();
    let mut original = ExecutionSchedule::new(vec![first.clone()]).expect("schedule");
    original
        .prepare(&ledger, LogicalTime::from_ticks(0))
        .expect("prepare");
    let mut revalidator = MockRevalidator::allowing();
    let mut executor = MockExecutor::new(vec![NodeExecutionResult::Indeterminate(
        IndeterminateReason::Timeout,
    )]);
    original
        .advance(
            &ledger,
            LogicalTime::from_ticks(1),
            &mut revalidator,
            &mut executor,
        )
        .expect("indeterminate");

    let retry = reconnect(&first, 9, 100, DeliveryKind::Retry);
    let mut duplicate = ExecutionSchedule::new(vec![retry]).expect("duplicate");
    duplicate
        .prepare(&ledger, LogicalTime::from_ticks(2))
        .expect("prepare duplicate");
    let mut no_revalidation = MockRevalidator::allowing();
    let mut no_executor = MockExecutor::new(Vec::new());
    let progress = duplicate
        .advance(
            &ledger,
            LogicalTime::from_ticks(3),
            &mut no_revalidation,
            &mut no_executor,
        )
        .expect("duplicate result");
    assert_eq!(
        progress.result(),
        &NodeExecutionResult::Indeterminate(IndeterminateReason::Timeout)
    );
    assert_eq!(no_executor.calls, 0);
    assert!(no_revalidation.prior_counts.is_empty());
}

#[test]
fn malformed_executor_result_becomes_indeterminate_and_stops() {
    let first = request(
        40,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let ledger = ledger();
    let mut schedule = ExecutionSchedule::new(vec![first.clone()]).expect("schedule");
    schedule
        .prepare(&ledger, LogicalTime::from_ticks(0))
        .expect("prepare");
    let mut revalidator = MockRevalidator::allowing();
    let mut executor = MockExecutor::new(vec![NodeExecutionResult::Succeeded(
        ExecutionSuccess::EffectApplied(OperationKind::TurnOn),
    )]);
    let progress = schedule
        .advance(
            &ledger,
            LogicalTime::from_ticks(1),
            &mut revalidator,
            &mut executor,
        )
        .expect("advance");
    assert_eq!(
        progress.result(),
        &NodeExecutionResult::Indeterminate(IndeterminateReason::InvalidExecutorResult)
    );
    assert_eq!(
        ledger.status(&first, LogicalTime::from_ticks(2)),
        Ok(LedgerStatus::Indeterminate)
    );
}

#[test]
fn schedule_constructor_rejects_binding_sequence_and_attempt_mutations() {
    let first = request(
        50,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(1)).expect("FIXTURE_TECNICA read"),
    );
    let duplicate_attempt = request(
        50,
        1,
        2,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read"),
    );
    assert!(matches!(
        ExecutionSchedule::new(vec![first.clone(), duplicate_attempt]),
        Err(AdapterError::DuplicateNodeAttempt)
    ));

    let sequence_gap = request(
        50,
        2,
        3,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read"),
    );
    assert!(matches!(
        ExecutionSchedule::new(vec![first.clone(), sequence_gap]),
        Err(AdapterError::ScheduleBindingMismatch)
    ));

    let mut changed = parts(
        50,
        2,
        2,
        DeliveryKind::Initial,
        TypedOperation::read_entity_state(target(2)).expect("FIXTURE_TECNICA read"),
    );
    changed.context_id = ha_adapter::ContextId::new("fixture_tecnica_context_99")
        .expect("FIXTURE_TECNICA changed context");
    let changed = ha_adapter::AuthenticatedRequest::new(changed).expect("changed request");
    assert!(matches!(
        ExecutionSchedule::new(vec![first, changed]),
        Err(AdapterError::ScheduleBindingMismatch)
    ));
}

struct MockSnapshotSource {
    generation: CatalogGeneration,
    values: BTreeMap<ha_adapter::RegistryEntryId, EntityState>,
    fail_at: Option<usize>,
    change_generation_after: Option<usize>,
    reads: usize,
}

impl MockSnapshotSource {
    fn complete(targets: &TargetSet, generation: CatalogGeneration) -> Self {
        let values = targets
            .as_slice()
            .iter()
            .enumerate()
            .map(|(index, target)| {
                (
                    target.clone(),
                    if index % 2 == 0 {
                        EntityState::On
                    } else {
                        EntityState::Off
                    },
                )
            })
            .collect();
        Self {
            generation,
            values,
            fail_at: None,
            change_generation_after: None,
            reads: 0,
        }
    }
}

impl ReadOnlySnapshotSource for MockSnapshotSource {
    fn catalog_generation(&self) -> CatalogGeneration {
        self.generation
    }

    fn read_state(
        &mut self,
        target: &ha_adapter::RegistryEntryId,
    ) -> core::result::Result<EntityState, SnapshotSourceError> {
        let index = self.reads;
        self.reads += 1;
        if self.fail_at == Some(index) {
            return Err(SnapshotSourceError::Unavailable);
        }
        let state = self
            .values
            .get(target)
            .copied()
            .ok_or(SnapshotSourceError::MissingTarget)?;
        if self.change_generation_after == Some(index) {
            self.generation =
                CatalogGeneration::new(self.generation.get() + 1).expect("next generation");
        }
        Ok(state)
    }
}

fn bundle_request() -> ha_adapter::AuthenticatedRequest {
    let targets =
        TargetSet::new(vec![target(3), target(1), target(2)]).expect("FIXTURE_TECNICA targets");
    request(
        60,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::read_bundled_snapshot(targets).expect("FIXTURE_TECNICA bundle"),
    )
}

#[test]
fn bundled_snapshot_returns_all_sorted_entries_or_no_result() {
    let request = bundle_request();
    let contract = BundledSnapshotContract::from_request(&request).expect("snapshot contract");
    let mut source = MockSnapshotSource::complete(contract.targets(), contract.generation());
    let snapshot = contract.capture(&mut source).expect("complete snapshot");
    assert_eq!(snapshot.entries().len(), 3);
    assert!(
        snapshot
            .entries()
            .windows(2)
            .all(|pair| pair[0].target() < pair[1].target())
    );

    for failure_index in 0..contract.targets().len() {
        let mut source = MockSnapshotSource::complete(contract.targets(), contract.generation());
        source.fail_at = Some(failure_index);
        assert_eq!(
            contract.capture(&mut source),
            Err(AdapterError::SnapshotUnavailable)
        );
        assert_eq!(source.reads, failure_index + 1);
    }

    let mut stale = MockSnapshotSource::complete(
        contract.targets(),
        CatalogGeneration::new(contract.generation().get() + 1).expect("stale generation"),
    );
    assert_eq!(
        contract.capture(&mut stale),
        Err(AdapterError::SnapshotGenerationMismatch)
    );
    assert_eq!(stale.reads, 0);

    let mut changes = MockSnapshotSource::complete(contract.targets(), contract.generation());
    changes.change_generation_after = Some(0);
    assert_eq!(
        contract.capture(&mut changes),
        Err(AdapterError::SnapshotGenerationMismatch)
    );
}

#[test]
fn renderer_keeps_interpretation_and_execution_outputs_separate_and_deterministic() {
    let renderer = ha_adapter::ResponseRenderer;
    assert_eq!(
        renderer
            .render_interpretation(InterpretationOutcome::Abstained(
                InterpretationReason::Ambiguous
            ))
            .as_str(),
        "O pedido ficou ambíguo."
    );
    assert_eq!(
        renderer
            .render_execution(ExecutionRenderOutcome::Indeterminate { completed_nodes: 1 })
            .as_str(),
        "Não foi possível confirmar o resultado da execução."
    );
}

#[test]
fn typed_transcript_contains_no_raw_service_name_or_arbitrary_payload_shape() {
    let request = request(
        70,
        1,
        1,
        DeliveryKind::Initial,
        TypedOperation::turn_on(target(1)).expect("FIXTURE_TECNICA turn on"),
    );
    let transcript = request.authenticated_transcript();
    assert!(
        !transcript
            .windows(b"turn_on".len())
            .any(|part| part == b"turn_on")
    );
    assert!(!transcript.contains(&b'{'));
    assert!(!transcript.contains(&b'}'));
}
