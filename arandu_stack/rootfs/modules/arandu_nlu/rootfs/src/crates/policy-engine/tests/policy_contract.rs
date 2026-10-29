use std::collections::BTreeSet;
use std::sync::{Arc, Barrier};
use std::thread;

use nlu_core::{
    CapabilityId, CatalogGeneration, ClauseSemantics, ComposedPlan, EntityId, EntityRef,
    EvidenceAtom, EvidenceKind, GraphExecutionClass, IndependentPair, IntentId, LogicalTime,
    NodeId, OperationId, Plan, PlanNode, Polarity, Relation, RelationEvidence, RelationKind,
    RequestText, Slot, SlotId, SlotValue,
};
use policy_engine::{
    CancellationOutcome, ConfirmationConfig, ConfirmationId, ConfirmationRejectionReason,
    ExpectedSlot, MAX_PENDING_CONFIRMATIONS, MAX_TTL_TICKS, PLAN_OPERATION_COUNT,
    PermittedGraphClasses, PolicyContext, PolicyDecision, PolicyDescriptor, PolicyDisposition,
    PolicyEngine, PolicyErrorCode, PolicyGeneration, PolicyTable, RiskClass,
    STANDARD_DESCRIPTOR_COUNT, SlotKind, StoredConfirmationOutcome,
};
use session_engine::SessionId;

#[derive(Clone)]
enum FixtureValue {
    Entity(String),
    EvidenceText,
    Integer(i64),
    Boolean(bool),
}

#[derive(Clone)]
struct FixtureSlot {
    id: String,
    value: FixtureValue,
}

#[derive(Clone)]
struct FixtureNode {
    capability: String,
    operation: String,
    slots: Vec<FixtureSlot>,
    polarity: Polarity,
}

#[derive(Clone, Copy)]
enum FixtureShape {
    Single,
    Ordered,
    Atomic,
}

const EXPECTED_ENTITY_SLOTS: &[(&str, SlotKind)] = &[("ha:entity", SlotKind::Entity)];
const EXPECTED_TIMER_SLOTS: &[(&str, SlotKind)] = &[("ha:timer", SlotKind::Entity)];
const EXPECTED_AREA_SLOTS: &[(&str, SlotKind)] = &[("ha:area", SlotKind::EvidenceText)];
const EXPECTED_PENDING_ACTION_SLOTS: &[(&str, SlotKind)] =
    &[("ha:pending_action", SlotKind::EvidenceText)];
const EXPECTED_RESPONSE_TEXT_SLOTS: &[(&str, SlotKind)] =
    &[("ha:response_text", SlotKind::EvidenceText)];
const EXPECTED_BROADCAST_SLOTS: &[(&str, SlotKind)] = &[
    ("ha:entity", SlotKind::Entity),
    ("ha:message", SlotKind::EvidenceText),
];
const EXPECTED_POSITION_SLOTS: &[(&str, SlotKind)] = &[
    ("ha:entity", SlotKind::Entity),
    ("ha:position", SlotKind::Integer),
];
const EXPECTED_START_TIMER_SLOTS: &[(&str, SlotKind)] = &[
    ("ha:duration_seconds", SlotKind::Integer),
    ("ha:timer", SlotKind::Entity),
];
const EXPECTED_TIMER_DELTA_SLOTS: &[(&str, SlotKind)] = &[
    ("ha:duration_delta_seconds", SlotKind::Integer),
    ("ha:timer", SlotKind::Entity),
];

fn catalog_generation(value: u64) -> CatalogGeneration {
    CatalogGeneration::new(value).expect("FIXTURE_TECNICA catalog generation")
}

fn policy_generation(value: u64) -> PolicyGeneration {
    PolicyGeneration::new(value).expect("FIXTURE_TECNICA policy generation")
}

fn context(catalog: u64, policy: u64) -> PolicyContext {
    PolicyContext::new(catalog_generation(catalog), policy_generation(policy))
}

fn session(value: u8) -> SessionId {
    SessionId::from_bytes([value; 32])
}

fn test_engine(ttl_ticks: u64) -> PolicyEngine {
    PolicyEngine::new(
        PolicyTable::standard(policy_generation(1)).expect("FIXTURE_TECNICA policy table"),
        ConfirmationConfig::new(ttl_ticks).expect("FIXTURE_TECNICA confirmation config"),
    )
}

fn fixture_slots(descriptor: &PolicyDescriptor, suffix: usize) -> Vec<FixtureSlot> {
    descriptor
        .expected_slots()
        .iter()
        .map(|slot| FixtureSlot {
            id: slot.id().as_str().to_owned(),
            value: match slot.kind() {
                SlotKind::Entity => {
                    FixtureValue::Entity(format!("fixture_tecnica:entity_{suffix}"))
                }
                SlotKind::EvidenceText => FixtureValue::EvidenceText,
                SlotKind::Integer => {
                    FixtureValue::Integer(i64::try_from(suffix).expect("FIXTURE_TECNICA integer"))
                }
                SlotKind::Boolean => FixtureValue::Boolean(suffix.is_multiple_of(2)),
            },
        })
        .collect()
}

fn fixture_slots_from_schema(expected: &[(&str, SlotKind)], suffix: usize) -> Vec<FixtureSlot> {
    expected
        .iter()
        .map(|(id, kind)| FixtureSlot {
            id: (*id).to_owned(),
            value: match kind {
                SlotKind::Entity => {
                    FixtureValue::Entity(format!("fixture_tecnica:entity_{suffix}"))
                }
                SlotKind::EvidenceText => FixtureValue::EvidenceText,
                SlotKind::Integer => {
                    FixtureValue::Integer(i64::try_from(suffix).expect("FIXTURE_TECNICA integer"))
                }
                SlotKind::Boolean => FixtureValue::Boolean(suffix.is_multiple_of(2)),
            },
        })
        .collect()
}

fn fixture_node(capability: &str, operation: &str, slots: Vec<FixtureSlot>) -> FixtureNode {
    FixtureNode {
        capability: capability.to_owned(),
        operation: operation.to_owned(),
        slots,
        polarity: Polarity::Affirmed,
    }
}

fn plan_for_descriptor(
    descriptor: &PolicyDescriptor,
    generation: u64,
    suffix: usize,
) -> ComposedPlan {
    composed_plan(
        vec![fixture_node(
            descriptor.capability().as_str(),
            descriptor.operation().as_str(),
            fixture_slots(descriptor, suffix),
        )],
        generation,
        FixtureShape::Single,
    )
}

fn composed_plan(fixtures: Vec<FixtureNode>, generation: u64, shape: FixtureShape) -> ComposedPlan {
    composed_plan_with_source(
        "FIXTURE_TECNICA_PRIVATE_CANARY",
        fixtures,
        generation,
        shape,
    )
}

fn composed_plan_with_source(
    source_text: &str,
    fixtures: Vec<FixtureNode>,
    generation: u64,
    shape: FixtureShape,
) -> ComposedPlan {
    let source = RequestText::new(source_text.into()).expect("FIXTURE_TECNICA source");
    let span = source.span(0, 1).expect("FIXTURE_TECNICA span");
    let catalog_generation = catalog_generation(generation);
    let mut nodes = Vec::new();
    let mut clauses = Vec::new();

    for (index, fixture) in fixtures.iter().enumerate() {
        let node_id = NodeId::new(&format!("fixture_tecnica:node_{}", index + 1))
            .expect("FIXTURE_TECNICA node ID");
        let mut slots = Vec::new();
        let mut clause_evidence = vec![EvidenceAtom::new(EvidenceKind::Predicate, span.clone())];
        for slot in &fixture.slots {
            let slot_id = SlotId::new(&slot.id).expect("FIXTURE_TECNICA slot ID");
            let value = match &slot.value {
                FixtureValue::Entity(id) => SlotValue::Entity(EntityRef::new(
                    EntityId::new(id).expect("FIXTURE_TECNICA entity ID"),
                    catalog_generation,
                )),
                FixtureValue::EvidenceText => SlotValue::EvidenceText(span.clone()),
                FixtureValue::Integer(value) => SlotValue::Integer(*value),
                FixtureValue::Boolean(value) => SlotValue::Boolean(*value),
            };
            clause_evidence.push(EvidenceAtom::new(
                EvidenceKind::Argument(slot_id.clone()),
                span.clone(),
            ));
            slots.push(Slot::new(slot_id, value));
        }
        if fixture.polarity == Polarity::Negated {
            clause_evidence.push(EvidenceAtom::new(EvidenceKind::Negation, span.clone()));
        }
        nodes.push(
            PlanNode::new(
                node_id.clone(),
                CapabilityId::new(&fixture.capability).expect("FIXTURE_TECNICA capability ID"),
                OperationId::new(&fixture.operation).expect("FIXTURE_TECNICA operation ID"),
                slots,
                vec![span.clone()],
            )
            .expect("FIXTURE_TECNICA node"),
        );
        clauses.push(
            ClauseSemantics::new(
                node_id,
                IntentId::new(&format!("fixture_tecnica:intent_{}", index + 1))
                    .expect("FIXTURE_TECNICA intent ID"),
                fixture.polarity,
                clause_evidence,
            )
            .expect("FIXTURE_TECNICA clause"),
        );
    }

    let mut relations = Vec::new();
    let mut relation_evidence = Vec::new();
    let mut independent_pairs = Vec::new();
    match shape {
        FixtureShape::Single => {}
        FixtureShape::Ordered => {
            let relation = Relation::new(
                nodes[0].id().clone(),
                nodes[1].id().clone(),
                RelationKind::Precedes,
            );
            relation_evidence.push(
                RelationEvidence::new(relation.clone(), vec![span.clone()])
                    .expect("FIXTURE_TECNICA relation evidence"),
            );
            relations.push(relation);
        }
        FixtureShape::Atomic => {
            independent_pairs.push(
                IndependentPair::new(nodes[0].id().clone(), nodes[1].id().clone())
                    .expect("FIXTURE_TECNICA independent pair"),
            );
        }
    }

    let execution_class = if fixtures
        .iter()
        .any(|fixture| fixture.polarity == Polarity::Negated)
    {
        GraphExecutionClass::NonExecutable
    } else if matches!(shape, FixtureShape::Atomic) {
        GraphExecutionClass::AtomicOnly
    } else {
        GraphExecutionClass::PartialSafe
    };
    let plan =
        Plan::new(&source, catalog_generation, nodes, relations).expect("FIXTURE_TECNICA plan");
    ComposedPlan::new(
        &source,
        plan,
        execution_class,
        clauses,
        relation_evidence,
        independent_pairs,
        Vec::new(),
    )
    .expect("FIXTURE_TECNICA composed plan")
}

fn entity_slot(id: &str, entity: &str) -> FixtureSlot {
    FixtureSlot {
        id: id.to_owned(),
        value: FixtureValue::Entity(entity.to_owned()),
    }
}

fn text_slot(id: &str) -> FixtureSlot {
    FixtureSlot {
        id: id.to_owned(),
        value: FixtureValue::EvidenceText,
    }
}

fn integer_slot(id: &str, value: i64) -> FixtureSlot {
    FixtureSlot {
        id: id.to_owned(),
        value: FixtureValue::Integer(value),
    }
}

fn denial_reason(decision: PolicyDecision) -> policy_engine::PolicyDenialReason {
    let PolicyDecision::Denied(denial) = decision else {
        panic!("FIXTURE_TECNICA expected denial");
    };
    denial.reason()
}

fn stored_rejection_reason(outcome: StoredConfirmationOutcome) -> ConfirmationRejectionReason {
    let StoredConfirmationOutcome::Rejected(rejection) = outcome else {
        panic!("FIXTURE_TECNICA expected stored rejection");
    };
    rejection.reason()
}

fn begin_stored_confirmation(
    engine: &PolicyEngine,
    plan: &ComposedPlan,
    session: &SessionId,
    policy_context: PolicyContext,
    now: LogicalTime,
) -> ConfirmationId {
    let PolicyDecision::ConfirmationRequired(requirement) = engine
        .evaluate(plan, session, policy_context, now)
        .expect("FIXTURE_TECNICA stored confirmation begin")
    else {
        panic!("FIXTURE_TECNICA expected confirmation requirement");
    };
    requirement
        .confirmation_id()
        .expect("FIXTURE_TECNICA issued confirmation ID")
}

fn descriptor_index(descriptors: &[PolicyDescriptor], capability: &str, operation: &str) -> usize {
    descriptors
        .iter()
        .position(|descriptor| {
            descriptor.capability().as_str() == capability
                && descriptor.operation().as_str() == operation
        })
        .expect("FIXTURE_TECNICA descriptor")
}

fn copy_descriptor(
    descriptor: &PolicyDescriptor,
    risk: RiskClass,
    slots: Vec<ExpectedSlot>,
    classes: PermittedGraphClasses,
    disposition: PolicyDisposition,
) -> PolicyDescriptor {
    PolicyDescriptor::new(
        descriptor.capability().clone(),
        descriptor.operation().clone(),
        risk,
        slots,
        classes,
        disposition,
    )
    .expect("FIXTURE_TECNICA copied descriptor")
}

#[test]
fn standard_matrix_is_exact_exhaustive_and_evaluable() {
    let expected = [
        (
            "ha:broadcast",
            "ha:broadcast",
            RiskClass::Sensitive,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_BROADCAST_SLOTS,
        ),
        (
            "ha:conversation_control",
            "ha:nevermind",
            RiskClass::LocalControl,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_PENDING_ACTION_SLOTS,
        ),
        (
            "ha:cover_control",
            "ha:set_position",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_POSITION_SLOTS,
        ),
        (
            "ha:cover_control",
            "ha:stop_moving",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:date_query",
            "ha:get_current_date",
            RiskClass::Observation,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:fan_control",
            "ha:toggle",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:light_control",
            "ha:turn_on",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:response",
            "ha:respond",
            RiskClass::LocalControl,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_RESPONSE_TEXT_SLOTS,
        ),
        (
            "ha:state_query",
            "ha:get_state",
            RiskClass::Observation,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:switch_control",
            "ha:turn_off",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:temperature_query",
            "ha:get_temperature",
            RiskClass::Observation,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:time_query",
            "ha:get_current_time",
            RiskClass::Observation,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_ENTITY_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:cancel_all_timers",
            RiskClass::Sensitive,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_AREA_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:cancel_timer",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_TIMER_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:decrease_timer",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_TIMER_DELTA_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:increase_timer",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_TIMER_DELTA_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:pause_timer",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_TIMER_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:start_timer",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_START_TIMER_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:timer_status",
            RiskClass::Observation,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_TIMER_SLOTS,
        ),
        (
            "ha:timer_control",
            "ha:unpause_timer",
            RiskClass::StateChange,
            PolicyDisposition::RequireConfirmation,
            EXPECTED_TIMER_SLOTS,
        ),
        (
            "ha:timer_query",
            "ha:timer_status",
            RiskClass::Observation,
            PolicyDisposition::AllowWithoutConfirmation,
            EXPECTED_TIMER_SLOTS,
        ),
    ];
    let table = PolicyTable::standard(policy_generation(1)).expect("FIXTURE_TECNICA policy table");
    assert_eq!(table.len(), STANDARD_DESCRIPTOR_COUNT);
    assert!(!table.is_empty());
    for (descriptor, (capability, operation, risk, disposition, slots)) in
        table.descriptors().zip(expected.iter())
    {
        assert_eq!(descriptor.capability().as_str(), *capability);
        assert_eq!(descriptor.operation().as_str(), *operation);
        assert_eq!(descriptor.risk(), *risk);
        assert_eq!(descriptor.disposition(), *disposition);
        assert_eq!(
            descriptor
                .expected_slots()
                .iter()
                .map(|slot| (slot.id().as_str(), slot.kind()))
                .collect::<Vec<_>>(),
            *slots
        );
    }

    let operations = table
        .descriptors()
        .map(|descriptor| descriptor.operation().as_str())
        .collect::<BTreeSet<_>>();
    assert_eq!(operations.len(), PLAN_OPERATION_COUNT);

    let engine = PolicyEngine::new(
        table,
        ConfirmationConfig::new(MAX_TTL_TICKS).expect("FIXTURE_TECNICA config"),
    );
    for (index, (_descriptor, expected_cell)) in engine
        .table()
        .descriptors()
        .zip(expected.iter())
        .enumerate()
    {
        let (capability, operation, risk, disposition, slots) = *expected_cell;
        let plan = composed_plan(
            vec![fixture_node(
                capability,
                operation,
                fixture_slots_from_schema(slots, index + 1),
            )],
            1,
            FixtureShape::Single,
        );
        let decision = engine
            .evaluate(
                &plan,
                &session(u8::try_from(index + 1).expect("FIXTURE_TECNICA session byte")),
                context(1, 1),
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA matrix evaluation");
        match disposition {
            PolicyDisposition::Deny => {
                assert_eq!(
                    denial_reason(decision),
                    policy_engine::PolicyDenialReason::ExplicitDeny
                );
            }
            PolicyDisposition::AllowWithoutConfirmation => {
                let PolicyDecision::AllowedWithoutConfirmation(acceptance) = decision else {
                    panic!("FIXTURE_TECNICA expected direct acceptance");
                };
                assert_eq!(acceptance.risk(), risk);
                assert!(!acceptance.authorizes_execution());
            }
            PolicyDisposition::RequireConfirmation => {
                let PolicyDecision::ConfirmationRequired(requirement) = decision else {
                    panic!("FIXTURE_TECNICA expected confirmation");
                };
                assert_eq!(requirement.risk(), risk);
                assert!(!requirement.authorizes_execution());
            }
        }
        assert!(!decision.authorizes_execution());
    }
}

#[test]
fn ordered_timer_graph_uses_the_generated_policy_cell() {
    let engine = test_engine(100);
    let plan = composed_plan(
        vec![
            fixture_node(
                "ha:timer_control",
                "ha:start_timer",
                vec![
                    entity_slot("ha:timer", "fixture_tecnica:shared_timer"),
                    integer_slot("ha:duration_seconds", 60),
                ],
            ),
            fixture_node(
                "ha:timer_control",
                "ha:timer_status",
                vec![entity_slot("ha:timer", "fixture_tecnica:shared_timer")],
            ),
        ],
        1,
        FixtureShape::Ordered,
    );
    let session = session(1);
    let decision = engine
        .evaluate(&plan, &session, context(1, 1), LogicalTime::from_ticks(1))
        .expect("FIXTURE_TECNICA ordered timer evaluation");
    let PolicyDecision::ConfirmationRequired(requirement) = decision else {
        panic!("FIXTURE_TECNICA expected ordered timer confirmation");
    };
    assert_eq!(requirement.risk(), RiskClass::StateChange);
    assert_eq!(requirement.capability_count(), 2);
    let confirmation_id = requirement
        .confirmation_id()
        .expect("FIXTURE_TECNICA confirmation ID");

    let outcome = engine
        .confirm_stored(
            &session,
            confirmation_id,
            &plan,
            context(1, 1),
            LogicalTime::from_ticks(2),
        )
        .expect("FIXTURE_TECNICA ordered timer confirmation");
    let StoredConfirmationOutcome::Accepted(accepted) = outcome else {
        panic!("FIXTURE_TECNICA expected ordered timer acceptance");
    };
    let acceptance = accepted.acceptance();
    assert_eq!(acceptance.risk(), RiskClass::StateChange);
    assert_eq!(acceptance.capability_count(), 2);
    assert!(!acceptance.authorizes_execution());
}

#[test]
fn configuration_rejects_malformed_duplicate_incomplete_unknown_and_unsafe_rows() {
    assert_eq!(
        PolicyGeneration::new(0)
            .expect_err("FIXTURE_TECNICA zero policy generation")
            .code(),
        PolicyErrorCode::InvalidGeneration
    );
    assert_eq!(
        PermittedGraphClasses::new(&[])
            .expect_err("FIXTURE_TECNICA empty graph classes")
            .code(),
        PolicyErrorCode::InvalidGraphClasses
    );
    assert_eq!(
        PermittedGraphClasses::new(&[GraphExecutionClass::NonExecutable])
            .expect_err("FIXTURE_TECNICA non-executable graph class")
            .code(),
        PolicyErrorCode::InvalidGraphClasses
    );
    assert_eq!(
        PermittedGraphClasses::new(&[
            GraphExecutionClass::PartialSafe,
            GraphExecutionClass::PartialSafe,
        ])
        .expect_err("FIXTURE_TECNICA duplicate graph class")
        .code(),
        PolicyErrorCode::InvalidGraphClasses
    );

    let capability =
        CapabilityId::new("fixture_tecnica:capability").expect("FIXTURE_TECNICA capability");
    let operation =
        OperationId::new("fixture_tecnica:operation").expect("FIXTURE_TECNICA operation");
    assert_eq!(
        PolicyDescriptor::new(
            capability.clone(),
            operation.clone(),
            RiskClass::Observation,
            Vec::new(),
            PermittedGraphClasses::partial_safe(),
            PolicyDisposition::Deny,
        )
        .expect_err("FIXTURE_TECNICA empty slot schema")
        .code(),
        PolicyErrorCode::InvalidSlotSchema
    );
    let duplicate_slot = ExpectedSlot::new(
        SlotId::new("fixture_tecnica:slot").expect("FIXTURE_TECNICA slot"),
        SlotKind::Boolean,
    );
    assert_eq!(
        PolicyDescriptor::new(
            capability,
            operation,
            RiskClass::Observation,
            vec![duplicate_slot.clone(), duplicate_slot],
            PermittedGraphClasses::partial_safe(),
            PolicyDisposition::Deny,
        )
        .expect_err("FIXTURE_TECNICA duplicate slot schema")
        .code(),
        PolicyErrorCode::InvalidSlotSchema
    );

    let standard =
        PolicyTable::standard_descriptors().expect("FIXTURE_TECNICA standard descriptors");
    let mut duplicate = standard.clone();
    duplicate.push(standard[0].clone());
    assert_eq!(
        PolicyTable::new(policy_generation(1), duplicate)
            .expect_err("FIXTURE_TECNICA duplicate descriptor")
            .code(),
        PolicyErrorCode::DuplicateDescriptor
    );

    let mut incomplete = standard.clone();
    incomplete.pop();
    assert_eq!(
        PolicyTable::new(policy_generation(1), incomplete)
            .expect_err("FIXTURE_TECNICA incomplete descriptors")
            .code(),
        PolicyErrorCode::IncompleteConfiguration
    );

    let mut unknown = standard.clone();
    let template = unknown[0].clone();
    unknown[0] = PolicyDescriptor::new(
        CapabilityId::new("fixture_tecnica:unknown_capability")
            .expect("FIXTURE_TECNICA capability"),
        OperationId::new("fixture_tecnica:unknown_operation").expect("FIXTURE_TECNICA operation"),
        template.risk(),
        template.expected_slots().to_vec(),
        template.permitted_graph_classes(),
        PolicyDisposition::Deny,
    )
    .expect("FIXTURE_TECNICA unknown descriptor");
    assert_eq!(
        PolicyTable::new(policy_generation(1), unknown)
            .expect_err("FIXTURE_TECNICA unknown descriptor")
            .code(),
        PolicyErrorCode::UnknownDescriptor
    );

    let mut wrong_risk = standard.clone();
    let index = descriptor_index(&wrong_risk, "ha:light_control", "ha:turn_on");
    let template = wrong_risk[index].clone();
    wrong_risk[index] = copy_descriptor(
        &template,
        RiskClass::Observation,
        template.expected_slots().to_vec(),
        template.permitted_graph_classes(),
        template.disposition(),
    );
    assert_eq!(
        PolicyTable::new(policy_generation(1), wrong_risk)
            .expect_err("FIXTURE_TECNICA wrong risk")
            .code(),
        PolicyErrorCode::DescriptorContract
    );

    let mut wrong_slots = standard.clone();
    let index = descriptor_index(&wrong_slots, "ha:light_control", "ha:turn_on");
    let template = wrong_slots[index].clone();
    wrong_slots[index] = copy_descriptor(
        &template,
        template.risk(),
        vec![ExpectedSlot::new(
            SlotId::new("ha:entity").expect("FIXTURE_TECNICA slot"),
            SlotKind::Integer,
        )],
        template.permitted_graph_classes(),
        template.disposition(),
    );
    assert_eq!(
        PolicyTable::new(policy_generation(1), wrong_slots)
            .expect_err("FIXTURE_TECNICA wrong slots")
            .code(),
        PolicyErrorCode::DescriptorContract
    );

    let mut wrong_graph_classes = standard.clone();
    let index = descriptor_index(&wrong_graph_classes, "ha:light_control", "ha:turn_on");
    let template = wrong_graph_classes[index].clone();
    wrong_graph_classes[index] = copy_descriptor(
        &template,
        template.risk(),
        template.expected_slots().to_vec(),
        PermittedGraphClasses::partial_safe(),
        template.disposition(),
    );
    assert_eq!(
        PolicyTable::new(policy_generation(1), wrong_graph_classes)
            .expect_err("FIXTURE_TECNICA wrong graph classes")
            .code(),
        PolicyErrorCode::DescriptorContract
    );

    let mut too_permissive = standard.clone();
    let index = descriptor_index(&too_permissive, "ha:light_control", "ha:turn_on");
    too_permissive[index] = too_permissive[index]
        .clone()
        .with_disposition(PolicyDisposition::AllowWithoutConfirmation);
    assert_eq!(
        PolicyTable::new(policy_generation(1), too_permissive)
            .expect_err("FIXTURE_TECNICA permissive disposition")
            .code(),
        PolicyErrorCode::DescriptorContract
    );

    let mut tighter = standard;
    let index = descriptor_index(&tighter, "ha:state_query", "ha:get_state");
    tighter[index] = tighter[index]
        .clone()
        .with_disposition(PolicyDisposition::Deny);
    PolicyTable::new(policy_generation(1), tighter)
        .expect("FIXTURE_TECNICA restrictive disposition");
}

#[test]
fn evaluation_denies_the_complete_graph_and_every_invalid_contract() {
    let engine = test_engine(100);
    let unknown = fixture_node(
        "fixture_tecnica:unknown_capability",
        "fixture_tecnica:unknown_operation",
        vec![entity_slot(
            "fixture_tecnica:slot",
            "fixture_tecnica:entity_2",
        )],
    );
    let mixed = composed_plan(
        vec![
            fixture_node(
                "ha:state_query",
                "ha:get_state",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
            ),
            unknown.clone(),
        ],
        1,
        FixtureShape::Ordered,
    );
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &mixed,
                    &session(1),
                    context(1, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA mixed graph"),
        ),
        policy_engine::PolicyDenialReason::MissingRule
    );

    let unknown_single = composed_plan(vec![unknown], 1, FixtureShape::Single);
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &unknown_single,
                    &session(2),
                    context(1, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA unknown graph"),
        ),
        policy_engine::PolicyDenialReason::MissingRule
    );

    let valid_query_slots = vec![entity_slot("ha:entity", "fixture_tecnica:query_entity")];
    let wrong_slot_cases = [
        Vec::new(),
        vec![integer_slot("ha:entity", 1)],
        vec![entity_slot(
            "fixture_tecnica:wrong_slot",
            "fixture_tecnica:query_entity",
        )],
        vec![
            entity_slot("ha:entity", "fixture_tecnica:query_entity"),
            integer_slot("fixture_tecnica:extra_slot", 1),
        ],
    ];
    for (index, slots) in wrong_slot_cases.into_iter().enumerate() {
        let plan = composed_plan(
            vec![fixture_node("ha:state_query", "ha:get_state", slots)],
            1,
            FixtureShape::Single,
        );
        assert_eq!(
            denial_reason(
                engine
                    .evaluate(
                        &plan,
                        &session(u8::try_from(index + 3).expect("FIXTURE_TECNICA session byte")),
                        context(1, 1),
                        LogicalTime::from_ticks(1),
                    )
                    .expect("FIXTURE_TECNICA slot mismatch"),
            ),
            policy_engine::PolicyDenialReason::SlotContractMismatch
        );
    }

    let query = composed_plan(
        vec![fixture_node(
            "ha:state_query",
            "ha:get_state",
            valid_query_slots,
        )],
        1,
        FixtureShape::Single,
    );
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &query,
                    &session(8),
                    context(2, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA stale catalog"),
        ),
        policy_engine::PolicyDenialReason::StaleCatalogGeneration
    );
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &query,
                    &session(9),
                    context(1, 2),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA stale policy"),
        ),
        policy_engine::PolicyDenialReason::StalePolicyGeneration
    );

    let mut negated = fixture_node(
        "ha:light_control",
        "ha:turn_on",
        vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
    );
    negated.polarity = Polarity::Negated;
    let negated = composed_plan(vec![negated], 1, FixtureShape::Single);
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &negated,
                    &session(10),
                    context(1, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA negated plan"),
        ),
        policy_engine::PolicyDenialReason::NegatedNode
    );

    let atomic = composed_plan(
        vec![
            fixture_node(
                "ha:light_control",
                "ha:turn_on",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
            ),
            fixture_node(
                "ha:light_control",
                "ha:turn_on",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
            ),
        ],
        1,
        FixtureShape::Atomic,
    );
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &atomic,
                    &session(11),
                    context(1, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA atomic plan"),
        ),
        policy_engine::PolicyDenialReason::AtomicOnly
    );

    let contradiction = composed_plan(
        vec![
            fixture_node(
                "ha:light_control",
                "ha:turn_on",
                vec![entity_slot("ha:entity", "fixture_tecnica:shared_entity")],
            ),
            fixture_node(
                "ha:switch_control",
                "ha:turn_off",
                vec![entity_slot("ha:entity", "fixture_tecnica:shared_entity")],
            ),
        ],
        1,
        FixtureShape::Ordered,
    );
    assert_eq!(
        denial_reason(
            engine
                .evaluate(
                    &contradiction,
                    &session(12),
                    context(1, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA contradictory plan"),
        ),
        policy_engine::PolicyDenialReason::ContradictoryPlan
    );

    let mut descriptors = PolicyTable::standard_descriptors().expect("FIXTURE_TECNICA descriptors");
    let index = descriptor_index(&descriptors, "ha:state_query", "ha:get_state");
    descriptors[index] = descriptors[index]
        .clone()
        .with_disposition(PolicyDisposition::Deny);
    let deny_engine = PolicyEngine::new(
        PolicyTable::new(policy_generation(1), descriptors).expect("FIXTURE_TECNICA deny table"),
        ConfirmationConfig::new(100).expect("FIXTURE_TECNICA config"),
    );
    assert_eq!(
        denial_reason(
            deny_engine
                .evaluate(
                    &query,
                    &session(13),
                    context(1, 1),
                    LogicalTime::from_ticks(1),
                )
                .expect("FIXTURE_TECNICA explicit deny"),
        ),
        policy_engine::PolicyDenialReason::ExplicitDeny
    );
}

#[test]
fn confirmation_consumes_once_and_returns_only_non_authorizing_acceptance() {
    let engine = test_engine(100);
    let descriptor = engine
        .table()
        .descriptor(
            &CapabilityId::new("ha:light_control").expect("FIXTURE_TECNICA capability"),
            &OperationId::new("ha:turn_on").expect("FIXTURE_TECNICA operation"),
        )
        .expect("FIXTURE_TECNICA descriptor");
    let first_plan = plan_for_descriptor(descriptor, 1, 1);
    let session = session(1);

    let decision = engine
        .evaluate(
            &first_plan,
            &session,
            context(1, 1),
            LogicalTime::from_ticks(10),
        )
        .expect("FIXTURE_TECNICA confirmation evaluation");
    let PolicyDecision::ConfirmationRequired(requirement) = decision else {
        panic!("FIXTURE_TECNICA expected confirmation");
    };
    assert_eq!(requirement.risk(), RiskClass::StateChange);
    assert_eq!(requirement.catalog_generation(), catalog_generation(1));
    assert_eq!(requirement.policy_generation(), policy_generation(1));
    assert_eq!(requirement.capability_count(), 1);
    assert!(!requirement.authorizes_execution());
    let confirmation_id = requirement
        .confirmation_id()
        .expect("FIXTURE_TECNICA confirmation ID");

    let outcome = engine
        .confirm_stored(
            &session,
            confirmation_id,
            &first_plan,
            context(1, 1),
            LogicalTime::from_ticks(11),
        )
        .expect("FIXTURE_TECNICA confirmation");
    assert!(!outcome.authorizes_execution());
    let StoredConfirmationOutcome::Accepted(accepted) = outcome else {
        panic!("FIXTURE_TECNICA expected acceptance");
    };
    assert_eq!(accepted.plan(), &first_plan);
    let acceptance = accepted.acceptance();
    assert_eq!(acceptance.risk(), RiskClass::StateChange);
    assert_eq!(acceptance.catalog_generation(), catalog_generation(1));
    assert_eq!(acceptance.policy_generation(), policy_generation(1));
    assert_eq!(acceptance.capability_count(), 1);
    assert!(!acceptance.authorizes_execution());
    assert!(!accepted.authorizes_execution());

    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session,
                    confirmation_id,
                    &first_plan,
                    context(1, 1),
                    LogicalTime::from_ticks(12),
                )
                .expect("FIXTURE_TECNICA replay"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn stored_confirmation_returns_the_original_evaluated_plan_and_acceptance() {
    let engine = test_engine(100);
    let plan = composed_plan(
        vec![
            fixture_node(
                "ha:timer_control",
                "ha:start_timer",
                vec![
                    entity_slot("ha:timer", "fixture_tecnica:private_timer"),
                    integer_slot("ha:duration_seconds", 60),
                ],
            ),
            fixture_node(
                "ha:timer_control",
                "ha:timer_status",
                vec![entity_slot("ha:timer", "fixture_tecnica:private_timer")],
            ),
        ],
        1,
        FixtureShape::Ordered,
    );
    let canonical = plan
        .canonical_bytes()
        .expect("FIXTURE_TECNICA canonical plan");
    let session = session(1);
    let confirmation_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session,
        context(1, 1),
        LogicalTime::from_ticks(1),
    );

    let outcome = engine
        .confirm_stored(
            &session,
            confirmation_id,
            &plan,
            context(1, 1),
            LogicalTime::from_ticks(2),
        )
        .expect("FIXTURE_TECNICA stored confirmation");
    assert!(!outcome.authorizes_execution());
    let rendered = format!("{outcome:?}");
    assert!(!rendered.contains("private_timer"));
    assert!(!rendered.contains("FIXTURE_TECNICA"));
    let StoredConfirmationOutcome::Accepted(accepted) = outcome else {
        panic!("FIXTURE_TECNICA expected stored acceptance");
    };
    let accepted_rendered = format!("{accepted:?}");
    assert!(!accepted_rendered.contains("private_timer"));
    assert!(!accepted_rendered.contains("FIXTURE_TECNICA"));
    assert!(!accepted.authorizes_execution());
    assert_eq!(accepted.plan(), &plan);
    let acceptance = accepted.acceptance();
    assert_eq!(acceptance.risk(), RiskClass::StateChange);
    assert_eq!(acceptance.catalog_generation(), catalog_generation(1));
    assert_eq!(acceptance.policy_generation(), policy_generation(1));
    assert_eq!(acceptance.capability_count(), 2);
    assert!(!acceptance.authorizes_execution());

    let (returned_plan, returned_acceptance) = accepted.into_parts();
    assert_eq!(
        returned_plan
            .canonical_bytes()
            .expect("FIXTURE_TECNICA returned canonical plan"),
        canonical
    );
    assert_eq!(
        returned_plan.plan().catalog_generation(),
        returned_acceptance.catalog_generation()
    );
    assert_eq!(
        returned_plan.plan().nodes().len(),
        usize::from(returned_acceptance.capability_count())
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session,
                    confirmation_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA stored replay"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn stale_confirmation_id_cannot_accept_a_same_session_replacement() {
    let engine = test_engine(100);
    let first = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let replacement = composed_plan(
        vec![fixture_node(
            "ha:switch_control",
            "ha:turn_off",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
        )],
        1,
        FixtureShape::Single,
    );
    let addressed = session(91);
    let stale_id = begin_stored_confirmation(
        &engine,
        &first,
        &addressed,
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let replacement_id = begin_stored_confirmation(
        &engine,
        &replacement,
        &addressed,
        context(1, 1),
        LogicalTime::from_ticks(2),
    );
    assert_ne!(stale_id, replacement_id);

    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &addressed,
                    stale_id,
                    &first,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA stale instance")
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &addressed,
                    replacement_id,
                    &replacement,
                    context(1, 1),
                    LogicalTime::from_ticks(4),
                )
                .expect("FIXTURE_TECNICA replacement consumed by mismatch")
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn successor_engine_inherits_one_monotonic_confirmation_sequence() {
    let plan = Arc::new(composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    ));
    let predecessor = test_engine(100);
    let predecessor_id = begin_stored_confirmation(
        &predecessor,
        &plan,
        &session(92),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let successor = test_engine(100);
    successor
        .inherit_confirmation_sequence(&predecessor)
        .expect("FIXTURE_TECNICA pristine successor");
    predecessor
        .invalidate_for_reload(LogicalTime::from_ticks(2))
        .expect("FIXTURE_TECNICA predecessor invalidation");
    let successor_id = begin_stored_confirmation(
        &successor,
        &plan,
        &session(92),
        context(1, 1),
        LogicalTime::from_ticks(3),
    );
    assert!(successor_id > predecessor_id);

    let used_successor = test_engine(100);
    begin_stored_confirmation(
        &used_successor,
        &plan,
        &session(93),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    assert_eq!(
        used_successor
            .inherit_confirmation_sequence(&predecessor)
            .expect_err("FIXTURE_TECNICA used successor must be rejected")
            .code(),
        PolicyErrorCode::PlanContract
    );
}

#[test]
fn shared_confirmation_sequence_is_monotonic_under_predecessor_successor_race() {
    let plan = Arc::new(composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    ));
    let predecessor = Arc::new(test_engine(100));
    let successor = Arc::new(test_engine(100));
    successor
        .inherit_confirmation_sequence(&predecessor)
        .expect("FIXTURE_TECNICA pristine concurrent successor");

    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for (engine, addressed) in [
        (Arc::clone(&predecessor), session(94)),
        (Arc::clone(&successor), session(95)),
    ] {
        let worker_barrier = Arc::clone(&barrier);
        let worker_plan = Arc::clone(&plan);
        workers.push(thread::spawn(move || {
            worker_barrier.wait();
            begin_stored_confirmation(
                engine.as_ref(),
                &worker_plan,
                &addressed,
                context(1, 1),
                LogicalTime::from_ticks(1),
            )
        }));
    }
    barrier.wait();
    let mut issued = workers
        .into_iter()
        .map(|worker| {
            worker
                .join()
                .expect("FIXTURE_TECNICA sequence race worker")
                .get()
        })
        .collect::<Vec<_>>();
    issued.sort_unstable();
    assert_eq!(issued, vec![1, 2]);

    let next = begin_stored_confirmation(
        predecessor.as_ref(),
        &plan,
        &session(96),
        context(1, 1),
        LogicalTime::from_ticks(2),
    );
    assert_eq!(next.get(), 3);
}

#[test]
fn fresh_engine_id_collision_rejects_a_different_addressed_plan() {
    let stale_plan = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let current_plan = composed_plan(
        vec![fixture_node(
            "ha:switch_control",
            "ha:turn_off",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
        )],
        1,
        FixtureShape::Single,
    );
    let addressed = session(97);
    let stale_engine = test_engine(100);
    let stale_id = begin_stored_confirmation(
        &stale_engine,
        &stale_plan,
        &addressed,
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let current_engine = test_engine(100);
    let current_id = begin_stored_confirmation(
        &current_engine,
        &current_plan,
        &addressed,
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    assert_eq!(stale_id, current_id);

    assert_eq!(
        stored_rejection_reason(
            current_engine
                .confirm_stored(
                    &addressed,
                    stale_id,
                    &stale_plan,
                    context(1, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA restart collision")
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            current_engine
                .confirm_stored(
                    &addressed,
                    current_id,
                    &current_plan,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA restart mismatch is terminal")
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn unknown_stored_confirmation_session_preserves_unrelated_entries() {
    let engine = test_engine(100);
    let first = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let second = composed_plan(
        vec![fixture_node(
            "ha:switch_control",
            "ha:turn_off",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
        )],
        1,
        FixtureShape::Single,
    );
    let first_id = begin_stored_confirmation(
        &engine,
        &first,
        &session(1),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let second_id = begin_stored_confirmation(
        &engine,
        &second,
        &session(2),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );

    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(99),
                    first_id,
                    &first,
                    context(1, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA unknown stored session"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA isolated stored diagnostics")
            .pending_confirmations(),
        2
    );

    let StoredConfirmationOutcome::Accepted(first_accepted) = engine
        .confirm_stored(
            &session(1),
            first_id,
            &first,
            context(1, 1),
            LogicalTime::from_ticks(3),
        )
        .expect("FIXTURE_TECNICA first stored confirmation")
    else {
        panic!("FIXTURE_TECNICA expected first stored acceptance");
    };
    assert_eq!(first_accepted.plan(), &first);
    let StoredConfirmationOutcome::Accepted(second_accepted) = engine
        .confirm_stored(
            &session(2),
            second_id,
            &second,
            context(1, 1),
            LogicalTime::from_ticks(4),
        )
        .expect("FIXTURE_TECNICA second stored confirmation")
    else {
        panic!("FIXTURE_TECNICA expected second stored acceptance");
    };
    assert_eq!(second_accepted.plan(), &second);
}

#[test]
fn stored_confirmation_expiry_and_generation_substitutions_are_terminal() {
    let plan = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let engine = test_engine(5);
    let expiry_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(1),
        context(1, 1),
        LogicalTime::from_ticks(10),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(1),
                    expiry_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(15),
                )
                .expect("FIXTURE_TECNICA stored exact deadline"),
        ),
        ConfirmationRejectionReason::Expired
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(1),
                    expiry_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(16),
                )
                .expect("FIXTURE_TECNICA stored expiry replay"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let catalog_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(2),
        context(1, 1),
        LogicalTime::from_ticks(20),
    );
    let policy_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(3),
        context(1, 1),
        LogicalTime::from_ticks(20),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    catalog_id,
                    &plan,
                    context(2, 1),
                    LogicalTime::from_ticks(21),
                )
                .expect("FIXTURE_TECNICA stored catalog substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    catalog_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(22),
                )
                .expect("FIXTURE_TECNICA terminal stored catalog substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(3),
                    policy_id,
                    &plan,
                    context(1, 2),
                    LogicalTime::from_ticks(23),
                )
                .expect("FIXTURE_TECNICA stored policy substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(3),
                    policy_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(24),
                )
                .expect("FIXTURE_TECNICA terminal stored policy substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn stored_confirmation_cancel_reload_and_rollback_are_terminal() {
    let plan = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let engine = test_engine(100);
    let cancelled_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(1),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    assert_eq!(
        engine
            .cancel(&session(1), LogicalTime::from_ticks(2))
            .expect("FIXTURE_TECNICA stored cancellation"),
        CancellationOutcome::Cancelled
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(1),
                    cancelled_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA stored cancelled confirmation"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let reloaded_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(2),
        context(1, 1),
        LogicalTime::from_ticks(4),
    );
    assert_eq!(
        engine
            .invalidate_for_reload(LogicalTime::from_ticks(5))
            .expect("FIXTURE_TECNICA stored reload"),
        1
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    reloaded_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(6),
                )
                .expect("FIXTURE_TECNICA stored reloaded confirmation"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let rollback_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(3),
        context(1, 1),
        LogicalTime::from_ticks(10),
    );
    assert_eq!(
        engine
            .purge_expired(LogicalTime::from_ticks(9))
            .expect_err("FIXTURE_TECNICA stored rollback")
            .code(),
        PolicyErrorCode::ClockRollback
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(3),
                    rollback_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(10),
                )
                .expect("FIXTURE_TECNICA stored rolled-back confirmation"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn concurrent_stored_confirmation_consumption_accepts_at_most_once() {
    let engine = Arc::new(test_engine(100));
    let plan = Arc::new(composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    ));
    let session = session(1);
    let confirmation_id = begin_stored_confirmation(
        engine.as_ref(),
        &plan,
        &session,
        context(1, 1),
        LogicalTime::from_ticks(1),
    );

    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let worker_engine = Arc::clone(&engine);
        let worker_barrier = Arc::clone(&barrier);
        let worker_plan = Arc::clone(&plan);
        workers.push(thread::spawn(move || {
            worker_barrier.wait();
            worker_engine
                .confirm_stored(
                    &session,
                    confirmation_id,
                    &worker_plan,
                    context(1, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA concurrent stored confirmation")
        }));
    }
    barrier.wait();
    let outcomes = workers
        .into_iter()
        .map(|worker| worker.join().expect("FIXTURE_TECNICA stored worker"))
        .collect::<Vec<_>>();
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, StoredConfirmationOutcome::Accepted(_)))
            .count(),
        1
    );
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, StoredConfirmationOutcome::Rejected(_)))
            .count(),
        1
    );
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA concurrent stored diagnostics")
            .pending_confirmations(),
        0
    );
}

#[test]
fn every_confirmation_binding_substitution_is_terminal() {
    let turn_on = || {
        composed_plan(
            vec![fixture_node(
                "ha:light_control",
                "ha:turn_on",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
            )],
            1,
            FixtureShape::Single,
        )
    };

    let engine = test_engine(100);
    let original = turn_on();
    let altered_value = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
        )],
        1,
        FixtureShape::Single,
    );
    let stale_id = begin_stored_confirmation(
        &engine,
        &original,
        &session(1),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let replacement_id = begin_stored_confirmation(
        &engine,
        &altered_value,
        &session(1),
        context(1, 1),
        LogicalTime::from_ticks(2),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(1),
                    stale_id,
                    &original,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA value substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(1),
                    replacement_id,
                    &altered_value,
                    context(1, 1),
                    LogicalTime::from_ticks(4),
                )
                .expect("FIXTURE_TECNICA terminal mismatch"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let engine = test_engine(100);
    let multi = composed_plan(
        vec![
            fixture_node(
                "ha:light_control",
                "ha:turn_on",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
            ),
            fixture_node(
                "ha:switch_control",
                "ha:turn_off",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
            ),
        ],
        1,
        FixtureShape::Ordered,
    );
    let altered_capabilities = composed_plan(
        vec![
            fixture_node(
                "ha:light_control",
                "ha:turn_on",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
            ),
            fixture_node(
                "ha:fan_control",
                "ha:toggle",
                vec![entity_slot("ha:entity", "fixture_tecnica:entity_2")],
            ),
        ],
        1,
        FixtureShape::Ordered,
    );
    let stale_id = begin_stored_confirmation(
        &engine,
        &multi,
        &session(2),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let replacement_id = begin_stored_confirmation(
        &engine,
        &altered_capabilities,
        &session(2),
        context(1, 1),
        LogicalTime::from_ticks(2),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    stale_id,
                    &multi,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA capability substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    replacement_id,
                    &altered_capabilities,
                    context(1, 1),
                    LogicalTime::from_ticks(4),
                )
                .expect("FIXTURE_TECNICA terminal capability substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let engine = test_engine(100);
    let original = turn_on();
    let confirmation_id = begin_stored_confirmation(
        &engine,
        &original,
        &session(3),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(3),
                    confirmation_id,
                    &original,
                    context(2, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA catalog substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(3),
                    confirmation_id,
                    &original,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA terminal catalog substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let engine = test_engine(100);
    let original = turn_on();
    let confirmation_id = begin_stored_confirmation(
        &engine,
        &original,
        &session(4),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(4),
                    confirmation_id,
                    &original,
                    context(1, 2),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA policy substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(4),
                    confirmation_id,
                    &original,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA terminal policy substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let engine = test_engine(100);
    let original = turn_on();
    let sensitive = composed_plan(
        vec![fixture_node(
            "ha:broadcast",
            "ha:broadcast",
            vec![
                text_slot("ha:message"),
                entity_slot("ha:entity", "fixture_tecnica:entity_1"),
            ],
        )],
        1,
        FixtureShape::Single,
    );
    let stale_id = begin_stored_confirmation(
        &engine,
        &original,
        &session(5),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let replacement_id = begin_stored_confirmation(
        &engine,
        &sensitive,
        &session(5),
        context(1, 1),
        LogicalTime::from_ticks(2),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(5),
                    stale_id,
                    &original,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA risk substitution"),
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(5),
                    replacement_id,
                    &sensitive,
                    context(1, 1),
                    LogicalTime::from_ticks(4),
                )
                .expect("FIXTURE_TECNICA terminal risk substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let engine = test_engine(100);
    let original = turn_on();
    let confirmation_id = begin_stored_confirmation(
        &engine,
        &original,
        &session(6),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(7),
                    confirmation_id,
                    &original,
                    context(1, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA session substitution"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_confirmations(),
        1
    );
    assert!(matches!(
        engine
            .confirm_stored(
                &session(6),
                confirmation_id,
                &original,
                context(1, 1),
                LogicalTime::from_ticks(3),
            )
            .expect("FIXTURE_TECNICA isolated original session"),
        StoredConfirmationOutcome::Accepted(_)
    ));
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA consumed diagnostics")
            .pending_confirmations(),
        0
    );
}

#[test]
fn confirmation_rejects_source_text_substitution_with_equal_canonical_offsets() {
    let fixture = || {
        vec![fixture_node(
            "ha:broadcast",
            "ha:broadcast",
            vec![
                text_slot("ha:message"),
                entity_slot("ha:entity", "fixture_tecnica:entity_1"),
            ],
        )]
    };
    let original =
        composed_plan_with_source("FIXTURE_TECNICA_A", fixture(), 1, FixtureShape::Single);
    let substituted =
        composed_plan_with_source("FIXTURE_TECNICA_B", fixture(), 1, FixtureShape::Single);
    assert_eq!(
        original
            .canonical_bytes()
            .expect("FIXTURE_TECNICA canonical original"),
        substituted
            .canonical_bytes()
            .expect("FIXTURE_TECNICA canonical substitution")
    );
    assert_ne!(original, substituted);

    let engine = test_engine(100);
    let stale_id = begin_stored_confirmation(
        &engine,
        &original,
        &session(90),
        context(1, 1),
        LogicalTime::from_ticks(1),
    );
    let replacement_id = begin_stored_confirmation(
        &engine,
        &substituted,
        &session(90),
        context(1, 1),
        LogicalTime::from_ticks(2),
    );
    assert_ne!(stale_id, replacement_id);
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(90),
                    stale_id,
                    &original,
                    context(1, 1),
                    LogicalTime::from_ticks(3),
                )
                .expect("FIXTURE_TECNICA substituted confirmation")
        ),
        ConfirmationRejectionReason::BindingMismatch
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(90),
                    replacement_id,
                    &substituted,
                    context(1, 1),
                    LogicalTime::from_ticks(4),
                )
                .expect("FIXTURE_TECNICA terminal source mismatch")
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn confirmation_limits_expiry_cancel_reload_and_rollback_are_terminal() {
    assert_eq!(
        ConfirmationConfig::new(0)
            .expect_err("FIXTURE_TECNICA zero TTL")
            .code(),
        PolicyErrorCode::InvalidTtl
    );
    assert!(ConfirmationConfig::new(MAX_TTL_TICKS).is_ok());
    assert_eq!(
        ConfirmationConfig::new(MAX_TTL_TICKS + 1)
            .expect_err("FIXTURE_TECNICA excessive TTL")
            .code(),
        PolicyErrorCode::InvalidTtl
    );

    let plan = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let engine = test_engine(5);
    let before_deadline_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(1),
        context(1, 1),
        LogicalTime::from_ticks(10),
    );
    assert!(matches!(
        engine
            .confirm_stored(
                &session(1),
                before_deadline_id,
                &plan,
                context(1, 1),
                LogicalTime::from_ticks(14),
            )
            .expect("FIXTURE_TECNICA before deadline"),
        StoredConfirmationOutcome::Accepted(_)
    ));

    let expiry_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(2),
        context(1, 1),
        LogicalTime::from_ticks(20),
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    expiry_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(25),
                )
                .expect("FIXTURE_TECNICA exact deadline"),
        ),
        ConfirmationRejectionReason::Expired
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(2),
                    expiry_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(26),
                )
                .expect("FIXTURE_TECNICA expiry replay"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let cancelled_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(3),
        context(1, 1),
        LogicalTime::from_ticks(30),
    );
    assert_eq!(
        engine
            .cancel(&session(3), LogicalTime::from_ticks(31))
            .expect("FIXTURE_TECNICA cancellation"),
        CancellationOutcome::Cancelled
    );
    assert_eq!(
        engine
            .cancel(&session(3), LogicalTime::from_ticks(32))
            .expect("FIXTURE_TECNICA repeated cancellation"),
        CancellationOutcome::Unavailable
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(3),
                    cancelled_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(33),
                )
                .expect("FIXTURE_TECNICA cancelled confirmation"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let reloaded_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(4),
        context(1, 1),
        LogicalTime::from_ticks(40),
    );
    begin_stored_confirmation(
        &engine,
        &plan,
        &session(5),
        context(1, 1),
        LogicalTime::from_ticks(40),
    );
    assert_eq!(
        engine
            .invalidate_for_reload(LogicalTime::from_ticks(41))
            .expect("FIXTURE_TECNICA reload"),
        2
    );
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA diagnostics")
            .pending_confirmations(),
        0
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(4),
                    reloaded_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(42),
                )
                .expect("FIXTURE_TECNICA reloaded confirmation"),
        ),
        ConfirmationRejectionReason::Unavailable
    );

    let rollback_id = begin_stored_confirmation(
        &engine,
        &plan,
        &session(6),
        context(1, 1),
        LogicalTime::from_ticks(50),
    );
    assert_eq!(
        engine
            .purge_expired(LogicalTime::from_ticks(49))
            .expect_err("FIXTURE_TECNICA clock rollback")
            .code(),
        PolicyErrorCode::ClockRollback
    );
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA rollback diagnostics")
            .pending_confirmations(),
        0
    );
    assert_eq!(
        stored_rejection_reason(
            engine
                .confirm_stored(
                    &session(6),
                    rollback_id,
                    &plan,
                    context(1, 1),
                    LogicalTime::from_ticks(50),
                )
                .expect("FIXTURE_TECNICA rollback confirmation"),
        ),
        ConfirmationRejectionReason::Unavailable
    );
}

#[test]
fn capacity_deadline_and_re_evaluation_fail_closed() {
    let plan = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    );
    let engine = test_engine(MAX_TTL_TICKS);
    for value in 0..MAX_PENDING_CONFIRMATIONS {
        engine
            .evaluate(
                &plan,
                &session(u8::try_from(value).expect("FIXTURE_TECNICA session byte")),
                context(1, 1),
                LogicalTime::from_ticks(1),
            )
            .expect("FIXTURE_TECNICA capacity entry");
    }
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA capacity diagnostics")
            .pending_confirmations(),
        u16::try_from(MAX_PENDING_CONFIRMATIONS).expect("FIXTURE_TECNICA capacity")
    );
    assert_eq!(
        engine
            .evaluate(
                &plan,
                &session(
                    u8::try_from(MAX_PENDING_CONFIRMATIONS)
                        .expect("FIXTURE_TECNICA capacity session")
                ),
                context(1, 1),
                LogicalTime::from_ticks(1),
            )
            .expect_err("FIXTURE_TECNICA one-over capacity")
            .code(),
        PolicyErrorCode::CapacityExceeded
    );

    let overflow = test_engine(1);
    assert_eq!(
        overflow
            .evaluate(
                &plan,
                &session(1),
                context(1, 1),
                LogicalTime::from_ticks(u64::MAX),
            )
            .expect_err("FIXTURE_TECNICA deadline overflow")
            .code(),
        PolicyErrorCode::DeadlineOverflow
    );
    assert_eq!(
        overflow
            .diagnostics()
            .expect("FIXTURE_TECNICA overflow diagnostics")
            .pending_confirmations(),
        0
    );

    let terminal_construction = test_engine(2);
    terminal_construction
        .evaluate(
            &plan,
            &session(2),
            context(1, 1),
            LogicalTime::from_ticks(u64::MAX - 2),
        )
        .expect("FIXTURE_TECNICA pre-overflow confirmation");
    assert_eq!(
        terminal_construction
            .evaluate(
                &plan,
                &session(2),
                context(1, 1),
                LogicalTime::from_ticks(u64::MAX - 1),
            )
            .expect_err("FIXTURE_TECNICA terminal construction failure")
            .code(),
        PolicyErrorCode::DeadlineOverflow
    );
    assert_eq!(
        terminal_construction
            .diagnostics()
            .expect("FIXTURE_TECNICA terminal construction diagnostics")
            .pending_confirmations(),
        0
    );

    let replacement = test_engine(100);
    replacement
        .evaluate(
            &plan,
            &session(2),
            context(1, 1),
            LogicalTime::from_ticks(1),
        )
        .expect("FIXTURE_TECNICA replacement begin");
    let denied = composed_plan(
        vec![fixture_node(
            "fixture_tecnica:unknown_capability",
            "fixture_tecnica:unknown_operation",
            vec![entity_slot(
                "fixture_tecnica:slot",
                "fixture_tecnica:entity_1",
            )],
        )],
        1,
        FixtureShape::Single,
    );
    assert_eq!(
        denial_reason(
            replacement
                .evaluate(
                    &denied,
                    &session(2),
                    context(1, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA replacement denial"),
        ),
        policy_engine::PolicyDenialReason::MissingRule
    );
    assert_eq!(
        replacement
            .diagnostics()
            .expect("FIXTURE_TECNICA replacement diagnostics")
            .pending_confirmations(),
        0
    );
}

#[test]
fn concurrent_double_consumption_accepts_at_most_once() {
    let engine = Arc::new(test_engine(100));
    let plan = Arc::new(composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", "fixture_tecnica:entity_1")],
        )],
        1,
        FixtureShape::Single,
    ));
    let session = session(1);
    let confirmation_id = begin_stored_confirmation(
        engine.as_ref(),
        &plan,
        &session,
        context(1, 1),
        LogicalTime::from_ticks(1),
    );

    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let worker_engine = Arc::clone(&engine);
        let worker_barrier = Arc::clone(&barrier);
        let worker_plan = Arc::clone(&plan);
        workers.push(thread::spawn(move || {
            worker_barrier.wait();
            worker_engine
                .confirm_stored(
                    &session,
                    confirmation_id,
                    &worker_plan,
                    context(1, 1),
                    LogicalTime::from_ticks(2),
                )
                .expect("FIXTURE_TECNICA concurrent confirmation")
        }));
    }
    barrier.wait();
    let outcomes = workers
        .into_iter()
        .map(|worker| worker.join().expect("FIXTURE_TECNICA worker"))
        .collect::<Vec<_>>();
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, StoredConfirmationOutcome::Accepted(_)))
            .count(),
        1
    );
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, StoredConfirmationOutcome::Rejected(_)))
            .count(),
        1
    );
    assert_eq!(
        engine
            .diagnostics()
            .expect("FIXTURE_TECNICA concurrent diagnostics")
            .pending_confirmations(),
        0
    );
}

#[test]
fn debug_display_and_errors_do_not_expose_private_inputs() {
    let marker = "fixture_tecnica:private_canary";
    let plan = composed_plan(
        vec![fixture_node(
            "ha:light_control",
            "ha:turn_on",
            vec![entity_slot("ha:entity", marker)],
        )],
        1,
        FixtureShape::Single,
    );
    let engine = test_engine(100);
    let decision = engine
        .evaluate(
            &plan,
            &SessionId::from_bytes([0xa5; 32]),
            context(1, 1),
            LogicalTime::from_ticks(1),
        )
        .expect("FIXTURE_TECNICA privacy evaluation");
    let error = ConfirmationConfig::new(0).expect_err("FIXTURE_TECNICA privacy error");
    let rendered = format!(
        "{engine:?} {decision:?} {:?} {:?} {error:?} {error}",
        engine.table(),
        engine.diagnostics().expect("FIXTURE_TECNICA diagnostics")
    );
    assert!(!rendered.contains(marker));
    assert!(!rendered.contains("a5"));
    assert!(!rendered.contains("FIXTURE_TECNICA_PRIVATE_CANARY"));
}
