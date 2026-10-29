use std::sync::{Arc, Barrier};
use std::thread;

use ha_catalog::{
    AliasProvenance, CapabilityDescriptorInput, CatalogSnapshot, CatalogSnapshotInput, Domain,
    EntityInput, EntityInputParts, EntityVisibility, ExplicitAlias, ExternalEntityId,
    RegistryEntryId, SensitiveText,
};
use intent_engine::{IntentEngine, RecognitionOutcome};
use nlu_core::{
    CapabilityId, CatalogGeneration, EntityId, EntityRef, InvocationId, LogicalTime, NodeId,
    RequestText, SlotId, SlotValue,
};
use plan_engine::{PendingEntityComposition, PlanEngine, ResumableCompositionOutcome};
use session_engine::{
    CallerBinding, CancellationOutcome, ContextBinding, ContinuationEndpoint, ContinuationOutcome,
    ContinuationResult, MAX_ACTIVE_SESSIONS, MAX_PENDING_REFERENTS, PairingEpoch,
    ReferentResolution, SESSION_BINDING_BYTES, SessionBinding, SessionConfig, SessionErrorCode,
    SessionId, SessionStore,
};

const REQUEST: &str = "desligue interruptor 1 do setor sala";
const ALIAS: &str = "interruptor 1 do setor sala";

#[derive(Clone)]
struct Binding {
    origin: InvocationId,
    capability: CapabilityId,
    generation: CatalogGeneration,
    endpoint: ContinuationEndpoint,
    candidates: Vec<EntityRef>,
}

fn generation(value: u64) -> CatalogGeneration {
    CatalogGeneration::new(value).expect("FIXTURE_TECNICA generation")
}

fn session(value: u8) -> SessionId {
    let mut bytes = [0_u8; session_engine::SESSION_ID_BYTES];
    bytes[0] = value;
    SessionId::from_bytes(bytes)
}

fn origin(value: usize) -> InvocationId {
    InvocationId::new(&format!("fixture_tecnica:origin_{value}")).expect("FIXTURE_TECNICA origin")
}

fn sensitive(value: impl Into<String>) -> SensitiveText {
    SensitiveText::new(value).expect("FIXTURE_TECNICA sensitive text")
}

fn snapshot(generation_value: u64, candidate_count: usize) -> CatalogSnapshot {
    let capability = CapabilityId::new("ha:switch_control").expect("FIXTURE_TECNICA capability ID");
    let domain = Domain::new("switch").expect("FIXTURE_TECNICA domain");
    let entities = (1..=candidate_count)
        .map(|index| {
            EntityInput::new(EntityInputParts {
                generation: generation(generation_value),
                registry_id: RegistryEntryId::new(&format!("{index:032x}"))
                    .expect("FIXTURE_TECNICA registry ID"),
                external_id: ExternalEntityId::new(&format!("switch.fixture_tecnica_{index}"))
                    .expect("FIXTURE_TECNICA external entity ID"),
                domain: domain.clone(),
                display_name: sensitive(format!("FIXTURE_TECNICA_DISPLAY_{index}")),
                aliases: vec![ExplicitAlias::new(
                    sensitive(ALIAS),
                    AliasProvenance::EntityRegistry,
                )],
                capabilities: vec![capability.clone()],
                area_id: None,
                floor_id: None,
                device_id: None,
                visibility: EntityVisibility::exposed(),
            })
        })
        .collect();
    CatalogSnapshot::build(CatalogSnapshotInput {
        generation: generation(generation_value),
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors: vec![CapabilityDescriptorInput {
            id: capability,
            enabled: true,
            state_query_domains: Vec::new(),
        }],
        entities,
    })
    .expect("FIXTURE_TECNICA catalog")
}

fn make_pending(
    generation_value: u64,
    candidate_count: usize,
    origin_value: usize,
) -> (PendingEntityComposition, Binding) {
    let source = RequestText::new(REQUEST.to_owned()).expect("FIXTURE_TECNICA request");
    let recognition = IntentEngine::bundled()
        .expect("FIXTURE_TECNICA intent engine")
        .recognize(&source)
        .expect("FIXTURE_TECNICA recognition");
    let RecognitionOutcome::Match(intent_match) = recognition else {
        panic!("FIXTURE_TECNICA request must match");
    };
    let outcome = PlanEngine::new()
        .expect("FIXTURE_TECNICA plan engine")
        .compose_resumable(
            &source,
            &intent_match,
            &snapshot(generation_value, candidate_count),
        )
        .expect("FIXTURE_TECNICA resumable composition");
    let ResumableCompositionOutcome::Pending(pending) = outcome else {
        panic!("FIXTURE_TECNICA collision must produce pending composition");
    };
    let binding = Binding {
        origin: origin(origin_value),
        capability: pending.capability().clone(),
        generation: pending.catalog_generation(),
        endpoint: ContinuationEndpoint::new(
            pending.endpoint().node().clone(),
            pending.endpoint().slot().clone(),
        ),
        candidates: pending.candidates().to_vec(),
    };
    (pending, binding)
}

fn selected(session: SessionId, binding: &Binding, candidate: usize) -> ContinuationResult {
    ContinuationResult::new(
        session,
        binding.origin.clone(),
        binding.capability.clone(),
        binding.generation,
        binding.endpoint.clone(),
        ReferentResolution::Selected(binding.candidates[candidate].clone()),
    )
}

fn session_binding(epoch: u64, caller: u8, context: u8) -> SessionBinding {
    SessionBinding::new(
        PairingEpoch::new([u8::try_from(epoch).expect("FIXTURE_TECNICA epoch byte"); 32])
            .expect("FIXTURE_TECNICA epoch"),
        CallerBinding::from_bytes([caller; SESSION_BINDING_BYTES]).expect("FIXTURE_TECNICA caller"),
        ContextBinding::from_bytes([context; SESSION_BINDING_BYTES])
            .expect("FIXTURE_TECNICA context"),
    )
}

fn selected_bound(
    session: SessionId,
    session_binding: SessionBinding,
    binding: &Binding,
    candidate: usize,
) -> ContinuationResult {
    ContinuationResult::new_bound(
        session,
        session_binding,
        binding.origin.clone(),
        binding.capability.clone(),
        binding.generation,
        binding.endpoint.clone(),
        ReferentResolution::Selected(binding.candidates[candidate].clone()),
    )
}

fn unresolved(session: SessionId, binding: &Binding) -> ContinuationResult {
    ContinuationResult::new(
        session,
        binding.origin.clone(),
        binding.capability.clone(),
        binding.generation,
        binding.endpoint.clone(),
        ReferentResolution::UnresolvedTie,
    )
}

fn new_store(ttl: u64) -> SessionStore {
    SessionStore::new(SessionConfig::new(ttl).expect("FIXTURE_TECNICA TTL"))
}

fn completed(outcome: &ContinuationOutcome) -> bool {
    matches!(outcome, ContinuationOutcome::Completed(_))
}

#[test]
fn caller_context_and_epoch_mismatch_is_terminal() {
    let store = new_store(10);
    let addressed = session(90);
    let expected = session_binding(7, 0x11, 0x22);
    let foreign = session_binding(7, 0x33, 0x22);
    let (pending, plan_binding) = make_pending(1, 2, 900);
    store
        .begin_bound(
            addressed,
            expected,
            plan_binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(1),
        )
        .expect("FIXTURE_TECNICA bound begin");

    assert!(matches!(
        store
            .complete(
                &addressed,
                selected_bound(addressed, foreign, &plan_binding, 0),
                plan_binding.generation,
                LogicalTime::from_ticks(2),
            )
            .expect("FIXTURE_TECNICA foreign caller"),
        ContinuationOutcome::Unavailable
    ));
    assert!(matches!(
        store
            .complete(
                &addressed,
                selected_bound(addressed, expected, &plan_binding, 0),
                plan_binding.generation,
                LogicalTime::from_ticks(2),
            )
            .expect("FIXTURE_TECNICA consumed mismatch"),
        ContinuationOutcome::Unavailable
    ));
}

#[test]
fn epoch_invalidation_removes_only_affected_bound_sessions() {
    let store = new_store(10);
    let first_session = session(91);
    let second_session = session(92);
    let first_binding = session_binding(7, 0x11, 0x21);
    let second_binding = session_binding(8, 0x12, 0x22);
    let (first_pending, first_plan) = make_pending(1, 2, 901);
    let (second_pending, second_plan) = make_pending(1, 2, 902);
    store
        .begin_bound(
            first_session,
            first_binding,
            first_plan.origin.clone(),
            first_pending,
            LogicalTime::from_ticks(1),
        )
        .expect("FIXTURE_TECNICA first bound begin");
    store
        .begin_bound(
            second_session,
            second_binding,
            second_plan.origin.clone(),
            second_pending,
            LogicalTime::from_ticks(1),
        )
        .expect("FIXTURE_TECNICA second bound begin");

    assert_eq!(
        store
            .invalidate_epoch(
                PairingEpoch::new([7; 32]).expect("FIXTURE_TECNICA epoch"),
                LogicalTime::from_ticks(2),
            )
            .expect("FIXTURE_TECNICA epoch invalidation"),
        1
    );
    assert!(matches!(
        store
            .complete(
                &first_session,
                selected_bound(first_session, first_binding, &first_plan, 0),
                first_plan.generation,
                LogicalTime::from_ticks(3),
            )
            .expect("FIXTURE_TECNICA invalidated session"),
        ContinuationOutcome::Unavailable
    ));
    assert!(completed(
        &store
            .complete(
                &second_session,
                selected_bound(second_session, second_binding, &second_plan, 0),
                second_plan.generation,
                LogicalTime::from_ticks(3),
            )
            .expect("FIXTURE_TECNICA retained session")
    ));
}

#[test]
fn valid_selection_completes_exactly_once_and_purges_terminal_state() {
    let store = new_store(10);
    let session = session(1);
    let (pending, binding) = make_pending(1, 2, 1);
    let selected_entity = binding.candidates[0].clone();
    store
        .begin(
            session,
            binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(5),
        )
        .expect("FIXTURE_TECNICA begin");

    let outcome = store
        .complete(
            &session,
            selected(session, &binding, 0),
            binding.generation,
            LogicalTime::from_ticks(6),
        )
        .expect("FIXTURE_TECNICA complete");
    let ContinuationOutcome::Completed(plan) = outcome else {
        panic!("FIXTURE_TECNICA selection must complete");
    };
    let SlotValue::Entity(entity) = plan.plan().nodes()[0].slots()[0].value() else {
        panic!("FIXTURE_TECNICA completed target must be an entity");
    };
    assert_eq!(entity, &selected_entity);
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_sessions(),
        0
    );

    assert!(matches!(
        store
            .complete(
                &session,
                selected(session, &binding, 0),
                binding.generation,
                LogicalTime::from_ticks(6),
            )
            .expect("FIXTURE_TECNICA replay"),
        ContinuationOutcome::Unavailable
    ));
}

#[test]
fn engine_owned_selection_uses_stored_binding_and_replays_unavailable() {
    let store = new_store(10);
    let addressed = session(81);
    let (pending, binding) = make_pending(1, 2, 801);
    let selected_entity = binding.candidates[1].clone();
    store
        .begin(
            addressed,
            binding.origin,
            pending,
            LogicalTime::from_ticks(5),
        )
        .expect("FIXTURE_TECNICA engine-owned begin");

    let outcome = store
        .continue_with_selection(
            &addressed,
            selected_entity.clone(),
            binding.generation,
            LogicalTime::from_ticks(6),
        )
        .expect("FIXTURE_TECNICA engine-owned continuation");
    let ContinuationOutcome::Completed(plan) = outcome else {
        panic!("FIXTURE_TECNICA stored binding must complete");
    };
    let SlotValue::Entity(entity) = plan.plan().nodes()[0].slots()[0].value() else {
        panic!("FIXTURE_TECNICA completed target must be an entity");
    };
    assert_eq!(entity, &selected_entity);

    assert!(matches!(
        store
            .continue_with_selection(
                &addressed,
                selected_entity,
                binding.generation,
                LogicalTime::from_ticks(6),
            )
            .expect("FIXTURE_TECNICA engine-owned replay"),
        ContinuationOutcome::Unavailable
    ));
}

#[test]
fn engine_owned_selection_preserves_unrelated_sessions_on_substitution() {
    let store = new_store(10);
    let session_a = session(82);
    let session_b = session(83);
    let (pending_a, binding_a) = make_pending(1, 2, 802);
    let (pending_b, binding_b) = make_pending(1, 2, 803);
    store
        .begin(
            session_a,
            binding_a.origin,
            pending_a,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA session A");
    store
        .begin(
            session_b,
            binding_b.origin,
            pending_b,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA session B");

    assert!(matches!(
        store
            .continue_with_selection(
                &session(84),
                binding_a.candidates[0].clone(),
                binding_a.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA unknown substituted session"),
        ContinuationOutcome::Unavailable
    ));
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA sessions preserved")
            .pending_sessions(),
        2
    );

    let foreign = EntityRef::new(
        EntityId::new("fixture_tecnica:substituted_entity")
            .expect("FIXTURE_TECNICA substituted entity"),
        binding_a.generation,
    );
    assert!(matches!(
        store
            .continue_with_selection(
                &session_a,
                foreign,
                binding_a.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA noncandidate selection"),
        ContinuationOutcome::Unavailable
    ));
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA unrelated session survives")
            .pending_sessions(),
        1
    );
    assert!(completed(
        &store
            .continue_with_selection(
                &session_b,
                binding_b.candidates[0].clone(),
                binding_b.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA unrelated completion")
    ));
}

#[test]
fn engine_owned_selection_rejects_independent_generation_substitutions() {
    let current_store = new_store(10);
    let current_session = session(85);
    let (current_pending, current_binding) = make_pending(1, 2, 805);
    current_store
        .begin(
            current_session,
            current_binding.origin,
            current_pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA current-generation begin");
    assert!(matches!(
        current_store
            .continue_with_selection(
                &current_session,
                current_binding.candidates[0].clone(),
                generation(2),
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA current-generation substitution"),
        ContinuationOutcome::Unavailable
    ));

    let selected_store = new_store(10);
    let selected_session = session(86);
    let (selected_pending, selected_binding) = make_pending(1, 2, 806);
    selected_store
        .begin(
            selected_session,
            selected_binding.origin,
            selected_pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA selected-generation begin");
    let substituted_selection =
        EntityRef::new(selected_binding.candidates[0].id().clone(), generation(2));
    assert!(matches!(
        selected_store
            .continue_with_selection(
                &selected_session,
                substituted_selection,
                selected_binding.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA selected-generation substitution"),
        ContinuationOutcome::Unavailable
    ));
}

#[test]
fn engine_owned_selection_expires_at_the_exact_deadline() {
    let store = new_store(2);
    let addressed = session(87);
    let (pending, binding) = make_pending(1, 2, 807);
    store
        .begin(
            addressed,
            binding.origin,
            pending,
            LogicalTime::from_ticks(10),
        )
        .expect("FIXTURE_TECNICA exact-deadline begin");

    assert!(matches!(
        store
            .continue_with_selection(
                &addressed,
                binding.candidates[0].clone(),
                binding.generation,
                LogicalTime::from_ticks(12),
            )
            .expect("FIXTURE_TECNICA exact-deadline continuation"),
        ContinuationOutcome::Unavailable
    ));
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA exact-deadline diagnostics")
            .pending_sessions(),
        0
    );
}

#[test]
fn exact_ttl_overflow_and_clock_rollback_fail_closed() {
    let before = new_store(2);
    let (pending, binding) = make_pending(1, 2, 2);
    before
        .begin(
            session(2),
            binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(10),
        )
        .expect("FIXTURE_TECNICA begin before deadline");
    assert!(completed(
        &before
            .complete(
                &session(2),
                selected(session(2), &binding, 0),
                binding.generation,
                LogicalTime::from_ticks(11),
            )
            .expect("FIXTURE_TECNICA before deadline")
    ));

    let exact = new_store(2);
    let (pending, binding) = make_pending(1, 2, 3);
    exact
        .begin(
            session(3),
            binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(10),
        )
        .expect("FIXTURE_TECNICA begin exact deadline");
    assert!(matches!(
        exact
            .complete(
                &session(3),
                selected(session(3), &binding, 0),
                binding.generation,
                LogicalTime::from_ticks(12),
            )
            .expect("FIXTURE_TECNICA exact deadline"),
        ContinuationOutcome::Unavailable
    ));

    let overflow = new_store(2);
    let (pending, binding) = make_pending(1, 2, 4);
    assert_eq!(
        overflow
            .begin(
                session(4),
                binding.origin,
                pending,
                LogicalTime::from_ticks(u64::MAX - 1),
            )
            .expect_err("FIXTURE_TECNICA deadline overflow"),
        session_engine::SessionError::from(SessionErrorCode::DeadlineOverflow)
    );

    let rollback = new_store(10);
    let (pending, binding) = make_pending(1, 2, 5);
    rollback
        .begin(
            session(5),
            binding.origin,
            pending,
            LogicalTime::from_ticks(10),
        )
        .expect("FIXTURE_TECNICA rollback begin");
    assert_eq!(
        rollback
            .purge_expired(LogicalTime::from_ticks(9))
            .expect_err("FIXTURE_TECNICA rollback"),
        session_engine::SessionError::from(SessionErrorCode::ClockRollback)
    );
    assert_eq!(
        rollback
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_sessions(),
        0
    );
}

#[test]
fn rollback_is_observed_before_deadline_overflow_and_purges_prior_state() {
    let store = new_store(3);
    let (first, first_binding) = make_pending(1, 2, 6);
    store
        .begin(
            session(6),
            first_binding.origin.clone(),
            first,
            LogicalTime::from_ticks(u64::MAX - 3),
        )
        .expect("FIXTURE_TECNICA maximum representable deadline");
    assert_eq!(
        store
            .purge_expired(LogicalTime::from_ticks(u64::MAX - 1))
            .expect("FIXTURE_TECNICA advance clock"),
        0
    );

    let (second, second_binding) = make_pending(1, 2, 7);
    assert_eq!(
        store
            .begin(
                session(7),
                second_binding.origin,
                second,
                LogicalTime::from_ticks(u64::MAX - 2),
            )
            .expect_err("FIXTURE_TECNICA rollback precedes overflow")
            .code(),
        SessionErrorCode::ClockRollback
    );
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_sessions(),
        0
    );
    assert!(matches!(
        store
            .complete(
                &session(6),
                selected(session(6), &first_binding, 0),
                first_binding.generation,
                LogicalTime::from_ticks(u64::MAX - 1),
            )
            .expect("FIXTURE_TECNICA purged completion"),
        ContinuationOutcome::Unavailable
    ));
}

#[test]
fn session_capacity_rejection_is_atomic_and_referent_limits_are_exact() {
    let store = new_store(10);
    let mut bindings = Vec::with_capacity(MAX_ACTIVE_SESSIONS);
    for index in 0..MAX_ACTIVE_SESSIONS {
        let (pending, binding) = make_pending(1, 2, 100 + index);
        store
            .begin(
                session(index as u8),
                binding.origin.clone(),
                pending,
                LogicalTime::from_ticks(0),
            )
            .expect("FIXTURE_TECNICA exact session capacity");
        bindings.push(binding);
    }
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_sessions(),
        MAX_ACTIVE_SESSIONS as u16
    );
    let (one_over, rejected_binding) = make_pending(1, 2, 999);
    assert_eq!(
        store
            .begin(
                session(200),
                rejected_binding.origin.clone(),
                one_over,
                LogicalTime::from_ticks(0),
            )
            .expect_err("FIXTURE_TECNICA one-over capacity")
            .code(),
        SessionErrorCode::CapacityExceeded
    );
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics after rejected insertion")
            .pending_sessions(),
        MAX_ACTIVE_SESSIONS as u16
    );
    for (index, binding) in bindings.iter().enumerate() {
        assert!(completed(
            &store
                .complete(
                    &session(index as u8),
                    selected(session(index as u8), binding, 0),
                    binding.generation,
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA every original session survives capacity rejection")
        ));
    }
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics after original completions")
            .pending_sessions(),
        0
    );
    assert!(matches!(
        store
            .complete(
                &session(200),
                selected(session(200), &rejected_binding, 0),
                rejected_binding.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA rejected session was never inserted"),
        ContinuationOutcome::Unavailable
    ));

    let exact_referents = new_store(10);
    let (pending, binding) = make_pending(1, MAX_PENDING_REFERENTS, 1_000);
    assert_eq!(binding.candidates.len(), MAX_PENDING_REFERENTS);
    exact_referents
        .begin(
            session(201),
            binding.origin,
            pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA exact referent capacity");
    assert_eq!(MAX_PENDING_REFERENTS, 16);
    assert_eq!(nlu_core::MAX_CLARIFICATION_OPTIONS, MAX_PENDING_REFERENTS);
}

#[test]
fn every_bound_field_and_unknown_or_unresolved_referent_produces_no_plan() {
    for mutation in 0..8 {
        let store = new_store(10);
        let addressed_session = session(20 + mutation);
        let (pending, binding) = make_pending(1, 2, 200 + usize::from(mutation));
        store
            .begin(
                addressed_session,
                binding.origin.clone(),
                pending,
                LogicalTime::from_ticks(0),
            )
            .expect("FIXTURE_TECNICA begin mutation");

        let mut current = binding.generation;
        let result = match mutation {
            0 => ContinuationResult::new(
                session(100 + mutation),
                binding.origin.clone(),
                binding.capability.clone(),
                binding.generation,
                binding.endpoint.clone(),
                ReferentResolution::Selected(binding.candidates[0].clone()),
            ),
            1 => ContinuationResult::new(
                addressed_session,
                origin(9_000),
                binding.capability.clone(),
                binding.generation,
                binding.endpoint.clone(),
                ReferentResolution::Selected(binding.candidates[0].clone()),
            ),
            2 => ContinuationResult::new(
                addressed_session,
                binding.origin.clone(),
                CapabilityId::new("fixture_tecnica:forged_capability")
                    .expect("FIXTURE_TECNICA capability"),
                binding.generation,
                binding.endpoint.clone(),
                ReferentResolution::Selected(binding.candidates[0].clone()),
            ),
            3 => ContinuationResult::new(
                addressed_session,
                binding.origin.clone(),
                binding.capability.clone(),
                binding.generation,
                ContinuationEndpoint::new(
                    NodeId::new("fixture_tecnica:forged_node").expect("FIXTURE_TECNICA node"),
                    binding.endpoint.slot().clone(),
                ),
                ReferentResolution::Selected(binding.candidates[0].clone()),
            ),
            4 => ContinuationResult::new(
                addressed_session,
                binding.origin.clone(),
                binding.capability.clone(),
                binding.generation,
                ContinuationEndpoint::new(
                    binding.endpoint.node().clone(),
                    SlotId::new("fixture_tecnica:forged_slot").expect("FIXTURE_TECNICA slot"),
                ),
                ReferentResolution::Selected(binding.candidates[0].clone()),
            ),
            5 => ContinuationResult::new(
                addressed_session,
                binding.origin.clone(),
                binding.capability.clone(),
                generation(2),
                binding.endpoint.clone(),
                ReferentResolution::Selected(binding.candidates[0].clone()),
            ),
            6 => {
                let foreign = EntityRef::new(
                    EntityId::new("fixture_tecnica:foreign_entity")
                        .expect("FIXTURE_TECNICA entity"),
                    binding.generation,
                );
                ContinuationResult::new(
                    addressed_session,
                    binding.origin.clone(),
                    binding.capability.clone(),
                    binding.generation,
                    binding.endpoint.clone(),
                    ReferentResolution::Selected(foreign),
                )
            }
            7 => unresolved(addressed_session, &binding),
            _ => unreachable!("FIXTURE_TECNICA closed mutation set"),
        };
        if mutation == 5 {
            current = generation(2);
        }
        assert!(matches!(
            store
                .complete(
                    &addressed_session,
                    result,
                    current,
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA mutated completion"),
            ContinuationOutcome::Unavailable
        ));
        if mutation == 0 {
            assert!(completed(
                &store
                    .complete(
                        &addressed_session,
                        selected(addressed_session, &binding, 0),
                        binding.generation,
                        LogicalTime::from_ticks(1),
                    )
                    .expect("FIXTURE_TECNICA session mismatch preserves addressed state")
            ));
        }
        assert_eq!(
            store
                .diagnostics()
                .expect("FIXTURE_TECNICA diagnostics")
                .pending_sessions(),
            0
        );
    }
}

#[test]
fn result_and_current_generation_substitutions_fail_independently() {
    let result_store = new_store(10);
    let result_session = session(28);
    let (result_pending, result_binding) = make_pending(1, 2, 228);
    result_store
        .begin(
            result_session,
            result_binding.origin.clone(),
            result_pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA result-generation begin");
    let forged_result_generation = ContinuationResult::new(
        result_session,
        result_binding.origin.clone(),
        result_binding.capability.clone(),
        generation(2),
        result_binding.endpoint.clone(),
        ReferentResolution::Selected(result_binding.candidates[0].clone()),
    );
    assert!(matches!(
        result_store
            .complete(
                &result_session,
                forged_result_generation,
                result_binding.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA forged result generation"),
        ContinuationOutcome::Unavailable
    ));

    let current_store = new_store(10);
    let current_session = session(29);
    let (current_pending, current_binding) = make_pending(1, 2, 229);
    current_store
        .begin(
            current_session,
            current_binding.origin.clone(),
            current_pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA current-generation begin");
    assert!(matches!(
        current_store
            .complete(
                &current_session,
                selected(current_session, &current_binding, 0),
                generation(2),
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA forged current generation"),
        ContinuationOutcome::Unavailable
    ));
}

#[test]
fn sessions_are_isolated_and_identical_bindings_cannot_cross_sessions() {
    let store = new_store(10);
    let (pending_a, binding_a) = make_pending(1, 2, 300);
    let (pending_b, binding_b) = make_pending(1, 2, 300);
    let session_a = session(30);
    let session_b = session(31);
    store
        .begin(
            session_a,
            binding_a.origin.clone(),
            pending_a,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA session A");
    store
        .begin(
            session_b,
            binding_b.origin.clone(),
            pending_b,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA session B");

    assert!(matches!(
        store
            .complete(
                &session_b,
                selected(session_a, &binding_a, 0),
                binding_a.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA identical-binding cross-session attempt"),
        ContinuationOutcome::Unavailable
    ));
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_sessions(),
        2
    );
    assert!(completed(
        &store
            .complete(
                &session_b,
                selected(session_b, &binding_b, 0),
                binding_b.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA second session completion")
    ));
    assert!(completed(
        &store
            .complete(
                &session_a,
                selected(session_a, &binding_a, 0),
                binding_a.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA first session completion")
    ));
}

#[test]
fn cancellation_and_catalog_change_purge_only_matching_state() {
    let store = new_store(10);
    let (pending, binding) = make_pending(1, 2, 400);
    store
        .begin(
            session(40),
            binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA cancellation begin");
    assert_eq!(
        store
            .cancel(&session(40), &origin(9_001), LogicalTime::from_ticks(1))
            .expect("FIXTURE_TECNICA wrong cancellation"),
        CancellationOutcome::Unavailable
    );
    assert_eq!(
        store
            .cancel(&session(40), &binding.origin, LogicalTime::from_ticks(1))
            .expect("FIXTURE_TECNICA cancellation"),
        CancellationOutcome::Cancelled
    );
    assert_eq!(
        store
            .cancel(&session(40), &binding.origin, LogicalTime::from_ticks(1))
            .expect("FIXTURE_TECNICA cancellation replay"),
        CancellationOutcome::Unavailable
    );

    let (generation_one, binding_one) = make_pending(1, 2, 401);
    let (generation_two, binding_two) = make_pending(2, 2, 402);
    store
        .begin(
            session(41),
            binding_one.origin,
            generation_one,
            LogicalTime::from_ticks(2),
        )
        .expect("FIXTURE_TECNICA generation one");
    store
        .begin(
            session(42),
            binding_two.origin,
            generation_two,
            LogicalTime::from_ticks(2),
        )
        .expect("FIXTURE_TECNICA generation two");
    assert_eq!(
        store
            .invalidate_catalog(generation(2), LogicalTime::from_ticks(3))
            .expect("FIXTURE_TECNICA catalog invalidation"),
        1
    );
    assert_eq!(
        store
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_sessions(),
        1
    );
}

#[derive(Clone, Copy)]
enum Action {
    Take,
    Cancel,
    Expire,
    Invalidate,
    Rollback,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ActionResult {
    Completed,
    Unavailable,
    Cancelled,
    Purged(usize),
    ClockRollback,
}

#[derive(Debug, Eq, PartialEq)]
struct ScheduleReplay {
    actions: Vec<ActionResult>,
    pending_sessions: u16,
}

fn replay_schedule(actions: &[Action], exact_deadline: bool) -> ScheduleReplay {
    let ttl = if exact_deadline { 1 } else { 10 };
    let store = new_store(ttl);
    let (pending, binding) = make_pending(1, 2, 500);
    store
        .begin(
            session(50),
            binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(10),
        )
        .expect("FIXTURE_TECNICA schedule begin");
    let now = LogicalTime::from_ticks(11);
    let mut current_generation = binding.generation;
    let mut results = Vec::with_capacity(actions.len());
    for action in actions {
        let result = match action {
            Action::Take => match store
                .complete(
                    &session(50),
                    selected(session(50), &binding, 0),
                    current_generation,
                    now,
                )
                .expect("FIXTURE_TECNICA scheduled take")
            {
                ContinuationOutcome::Completed(_) => ActionResult::Completed,
                ContinuationOutcome::Unavailable => ActionResult::Unavailable,
            },
            Action::Cancel => match store
                .cancel(&session(50), &binding.origin, now)
                .expect("FIXTURE_TECNICA scheduled cancel")
            {
                CancellationOutcome::Cancelled => ActionResult::Cancelled,
                CancellationOutcome::Unavailable => ActionResult::Unavailable,
            },
            Action::Expire => ActionResult::Purged(
                store
                    .purge_expired(now)
                    .expect("FIXTURE_TECNICA scheduled expiry"),
            ),
            Action::Invalidate => {
                current_generation = generation(2);
                ActionResult::Purged(
                    store
                        .invalidate_catalog(generation(2), now)
                        .expect("FIXTURE_TECNICA scheduled invalidation"),
                )
            }
            Action::Rollback => match store.purge_expired(LogicalTime::from_ticks(9)) {
                Err(error) if error.code() == SessionErrorCode::ClockRollback => {
                    ActionResult::ClockRollback
                }
                result => panic!("FIXTURE_TECNICA scheduled rollback differed: {result:?}"),
            },
        };
        results.push(result);
    }
    ScheduleReplay {
        actions: results,
        pending_sessions: store
            .diagnostics()
            .expect("FIXTURE_TECNICA final schedule state")
            .pending_sessions(),
    }
}

#[test]
fn deterministic_schedules_replay_exact_results_and_final_state() {
    let schedules: &[(&[Action], bool)] = &[
        (&[Action::Take, Action::Take], false),
        (&[Action::Take, Action::Cancel], false),
        (&[Action::Cancel, Action::Take], false),
        (&[Action::Take, Action::Expire], true),
        (&[Action::Expire, Action::Take], true),
        (&[Action::Take, Action::Invalidate], false),
        (&[Action::Invalidate, Action::Take], false),
        (&[Action::Cancel, Action::Invalidate], false),
        (&[Action::Invalidate, Action::Cancel], false),
        (&[Action::Rollback, Action::Take], false),
        (&[Action::Take, Action::Rollback], false),
        (&[Action::Rollback, Action::Cancel], false),
        (&[Action::Cancel, Action::Rollback], false),
        (&[Action::Rollback, Action::Invalidate], false),
        (&[Action::Invalidate, Action::Rollback], false),
        (&[Action::Rollback, Action::Expire], false),
        (&[Action::Expire, Action::Rollback], false),
    ];
    for (schedule, exact_deadline) in schedules {
        let first = replay_schedule(schedule, *exact_deadline);
        let second = replay_schedule(schedule, *exact_deadline);
        assert_eq!(first, second);
        assert_eq!(first.pending_sessions, 0);
        assert!(
            first
                .actions
                .iter()
                .filter(|item| **item == ActionResult::Completed)
                .count()
                <= 1
        );
    }
    assert_eq!(
        replay_schedule(&[Action::Take, Action::Invalidate], false)
            .actions
            .first(),
        Some(&ActionResult::Completed)
    );
    assert!(
        !replay_schedule(&[Action::Invalidate, Action::Take], false)
            .actions
            .contains(&ActionResult::Completed)
    );
    assert!(
        !replay_schedule(&[Action::Rollback, Action::Take], false)
            .actions
            .contains(&ActionResult::Completed)
    );
    assert!(
        !replay_schedule(&[Action::Take, Action::Expire], true)
            .actions
            .contains(&ActionResult::Completed)
    );
}

#[test]
fn simultaneous_double_completion_is_atomic() {
    let store = Arc::new(new_store(10));
    let (pending, binding) = make_pending(1, 2, 600);
    store
        .begin(
            session(60),
            binding.origin.clone(),
            pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA concurrent begin");
    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let store = Arc::clone(&store);
        let barrier = Arc::clone(&barrier);
        let binding = binding.clone();
        workers.push(thread::spawn(move || {
            barrier.wait();
            store
                .complete(
                    &session(60),
                    selected(session(60), &binding, 0),
                    binding.generation,
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA concurrent completion")
        }));
    }
    barrier.wait();
    let outcomes = workers
        .into_iter()
        .map(|worker| worker.join().expect("FIXTURE_TECNICA worker"))
        .collect::<Vec<_>>();
    assert_eq!(
        outcomes.iter().filter(|outcome| completed(outcome)).count(),
        1
    );
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, ContinuationOutcome::Unavailable))
            .count(),
        1
    );
}

#[test]
fn simultaneous_engine_owned_selection_consumes_at_most_once() {
    let store = Arc::new(new_store(10));
    let (pending, binding) = make_pending(1, 2, 808);
    let addressed = session(88);
    store
        .begin(
            addressed,
            binding.origin,
            pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA concurrent engine-owned begin");
    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let store = Arc::clone(&store);
        let barrier = Arc::clone(&barrier);
        let selection = binding.candidates[0].clone();
        let current_generation = binding.generation;
        workers.push(thread::spawn(move || {
            barrier.wait();
            store
                .continue_with_selection(
                    &addressed,
                    selection,
                    current_generation,
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA concurrent engine-owned continuation")
        }));
    }
    barrier.wait();
    let outcomes = workers
        .into_iter()
        .map(|worker| worker.join().expect("FIXTURE_TECNICA worker"))
        .collect::<Vec<_>>();
    assert_eq!(
        outcomes.iter().filter(|outcome| completed(outcome)).count(),
        1
    );
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, ContinuationOutcome::Unavailable))
            .count(),
        1
    );
}

#[test]
fn duplicate_insertion_is_rejected_without_replacement() {
    let store = new_store(10);
    let (first, first_binding) = make_pending(1, 2, 700);
    store
        .begin(
            session(70),
            first_binding.origin.clone(),
            first,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA first insertion");
    let (duplicate, duplicate_binding) = make_pending(1, 2, 701);
    assert_eq!(
        store
            .begin(
                session(70),
                duplicate_binding.origin,
                duplicate,
                LogicalTime::from_ticks(0),
            )
            .expect_err("FIXTURE_TECNICA duplicate insertion")
            .code(),
        SessionErrorCode::DuplicateSession
    );
    assert!(completed(
        &store
            .complete(
                &session(70),
                selected(session(70), &first_binding, 0),
                first_binding.generation,
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA original completion")
    ));
}

#[test]
fn state_is_memory_only_restart_empty_and_diagnostics_are_redacted() {
    let canary = "fixture_tecnica:private_origin_canary";
    let origin = InvocationId::new(canary).expect("FIXTURE_TECNICA canary origin");
    let store = new_store(10);
    let (pending, binding) = make_pending(1, 2, 800);
    assert!(!format!("{pending:?}").contains(REQUEST));
    store
        .begin(
            session(80),
            origin.clone(),
            pending,
            LogicalTime::from_ticks(0),
        )
        .expect("FIXTURE_TECNICA privacy begin");
    let result = ContinuationResult::new(
        session(80),
        origin,
        binding.capability,
        binding.generation,
        binding.endpoint,
        ReferentResolution::Selected(binding.candidates[0].clone()),
    );
    let rendered = format!("{store:?} {result:?} {:?}", session(80));
    assert!(!rendered.contains(REQUEST));
    assert!(!rendered.contains(ALIAS));
    assert!(!rendered.contains(canary));
    assert!(!rendered.contains("fixture_tecnica_1"));
    drop(store);

    let restarted = new_store(10);
    assert_eq!(
        restarted
            .diagnostics()
            .expect("FIXTURE_TECNICA restarted diagnostics")
            .pending_sessions(),
        0
    );
}
