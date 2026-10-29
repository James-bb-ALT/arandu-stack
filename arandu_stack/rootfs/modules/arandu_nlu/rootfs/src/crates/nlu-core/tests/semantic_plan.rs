use nlu_core::{
    ArgumentEndpoint, ArgumentShare, CapabilityId, CatalogGeneration, ClauseSemantics,
    CollectionKind, ComposedPlan, CoreError, DuplicateKind, EntityId, EntityRef, EvidenceAtom,
    EvidenceKind, GraphExecutionClass, IndependentPair, IntentId, NodeId, OperationId, Plan,
    PlanNode, Polarity, Relation, RelationEvidence, RelationKind, RequestText,
    SemanticPlanErrorKind, Slot, SlotId, SlotValue, Utf8Span,
};

const PREFIX: &str = "FIXTURE_TECNICA_";

fn source() -> RequestText {
    RequestText::new(format!("{PREFIX}abcdefghijklmnopqrstuvwxyz0123456789"))
        .expect("technical source")
}

fn span(source: &RequestText, index: usize) -> Utf8Span {
    let start = PREFIX.len() + index;
    source
        .span(start as u64, (start + 1) as u64)
        .expect("technical span")
}

fn node_id(suffix: &str) -> NodeId {
    NodeId::new(&format!("fixture_tecnica:node_{suffix}")).expect("node ID")
}

fn slot_id(suffix: &str) -> SlotId {
    SlotId::new(&format!("fixture_tecnica:slot_{suffix}")).expect("slot ID")
}

fn intent_id(suffix: &str) -> IntentId {
    IntentId::new(&format!("fixture_tecnica:intent_{suffix}")).expect("intent ID")
}

fn basic_node(_source: &RequestText, suffix: &str, evidence: Vec<Utf8Span>) -> PlanNode {
    PlanNode::new(
        node_id(suffix),
        CapabilityId::new("fixture_tecnica:capability").expect("capability ID"),
        OperationId::new("fixture_tecnica:operation").expect("operation ID"),
        Vec::new(),
        evidence,
    )
    .expect("plan node")
}

fn clause(node: &str, polarity: Polarity, evidence: Vec<EvidenceAtom>) -> ClauseSemantics {
    ClauseSemantics::new(node_id(node), intent_id(node), polarity, evidence)
        .expect("clause semantics")
}

fn semantic_error(kind: SemanticPlanErrorKind) -> CoreError {
    CoreError::SemanticPlan(kind)
}

#[test]
fn canonical_encoding_has_fixed_typed_snapshot_without_source_text() {
    let source = source();
    let generation = CatalogGeneration::new(7).expect("generation");
    let predicate = span(&source, 0);
    let boolean = span(&source, 1);
    let entity = span(&source, 2);
    let integer = span(&source, 3);
    let text = span(&source, 4);
    let slots = vec![
        Slot::new(slot_id("text"), SlotValue::EvidenceText(text.clone())),
        Slot::new(slot_id("integer"), SlotValue::Integer(-7)),
        Slot::new(
            slot_id("entity"),
            SlotValue::Entity(EntityRef::new(
                EntityId::new("fixture_tecnica:entity").expect("entity ID"),
                generation,
            )),
        ),
        Slot::new(slot_id("boolean"), SlotValue::Boolean(true)),
    ];
    let plan_node = PlanNode::new(
        node_id("a"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability ID"),
        OperationId::new("fixture_tecnica:operation").expect("operation ID"),
        slots,
        vec![
            text.clone(),
            predicate.clone(),
            integer.clone(),
            entity.clone(),
            boolean.clone(),
        ],
    )
    .expect("plan node");
    let plan = Plan::new(&source, generation, vec![plan_node], Vec::new()).expect("plan");
    let clause = clause(
        "a",
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("text")), text),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("integer")), integer),
            EvidenceAtom::new(EvidenceKind::Predicate, predicate),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("entity")), entity),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("boolean")), boolean),
        ],
    );
    let composed = ComposedPlan::new(
        &source,
        plan,
        GraphExecutionClass::PartialSafe,
        vec![clause],
        Vec::new(),
        Vec::new(),
        Vec::new(),
    )
    .expect("composed plan");

    let expected = concat!(
        "{\"schema_version\":\"p11-semantic-plan-v1\",\"catalog_generation\":7,",
        "\"execution_class\":\"partial_safe\",\"nodes\":[{\"id\":\"fixture_tecnica:node_a\",",
        "\"intent\":\"fixture_tecnica:intent_a\",\"capability\":\"fixture_tecnica:capability\",",
        "\"operation\":\"fixture_tecnica:operation\",\"polarity\":\"affirmed\",\"slots\":[",
        "{\"id\":\"fixture_tecnica:slot_boolean\",\"value\":{\"type\":\"boolean\",\"value\":true}},",
        "{\"id\":\"fixture_tecnica:slot_entity\",\"value\":{\"type\":\"entity\",",
        "\"id\":\"fixture_tecnica:entity\",\"generation\":7}},",
        "{\"id\":\"fixture_tecnica:slot_integer\",\"value\":{\"type\":\"integer\",\"value\":-7}},",
        "{\"id\":\"fixture_tecnica:slot_text\",\"value\":{\"type\":\"evidence_text\",",
        "\"span\":{\"start\":20,\"end\":21}}}],\"evidence\":[",
        "{\"kind\":\"predicate\",\"slot\":null,\"span\":{\"start\":16,\"end\":17}},",
        "{\"kind\":\"argument\",\"slot\":\"fixture_tecnica:slot_boolean\",",
        "\"span\":{\"start\":17,\"end\":18}},",
        "{\"kind\":\"argument\",\"slot\":\"fixture_tecnica:slot_entity\",",
        "\"span\":{\"start\":18,\"end\":19}},",
        "{\"kind\":\"argument\",\"slot\":\"fixture_tecnica:slot_integer\",",
        "\"span\":{\"start\":19,\"end\":20}},",
        "{\"kind\":\"argument\",\"slot\":\"fixture_tecnica:slot_text\",",
        "\"span\":{\"start\":20,\"end\":21}}]}],\"relations\":[],",
        "\"independent_pairs\":[],\"argument_shares\":[]}\n"
    )
    .as_bytes()
    .to_vec();
    let bytes = composed.canonical_bytes().expect("canonical bytes");
    assert_eq!(bytes, expected);
    assert!(bytes.is_ascii());
    assert!(
        !bytes
            .windows(PREFIX.len())
            .any(|window| window == PREFIX.as_bytes())
    );
}

#[test]
fn canonical_bytes_ignore_every_input_permutation() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let nodes = vec![
        basic_node(&source, "c", vec![span(&source, 2)]),
        basic_node(&source, "a", vec![span(&source, 0)]),
        basic_node(&source, "b", vec![span(&source, 1)]),
    ];
    let relations = vec![
        Relation::new(node_id("a"), node_id("c"), RelationKind::Requires),
        Relation::new(node_id("b"), node_id("c"), RelationKind::Precedes),
        Relation::new(node_id("a"), node_id("b"), RelationKind::Precedes),
    ];
    let plan = Plan::new(&source, generation, nodes, relations).expect("plan");
    let clauses = vec![
        clause(
            "c",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 2))],
        ),
        clause(
            "a",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0))],
        ),
        clause(
            "b",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 1))],
        ),
    ];
    let relation_evidence = vec![
        RelationEvidence::new(
            Relation::new(node_id("b"), node_id("c"), RelationKind::Precedes),
            vec![span(&source, 5), span(&source, 4)],
        )
        .expect("relation evidence"),
        RelationEvidence::new(
            Relation::new(node_id("a"), node_id("b"), RelationKind::Precedes),
            vec![span(&source, 3)],
        )
        .expect("relation evidence"),
        RelationEvidence::new(
            Relation::new(node_id("a"), node_id("c"), RelationKind::Requires),
            vec![span(&source, 6)],
        )
        .expect("relation evidence"),
    ];

    let first = ComposedPlan::new(
        &source,
        plan.clone(),
        GraphExecutionClass::PartialSafe,
        clauses.clone(),
        relation_evidence.clone(),
        Vec::new(),
        Vec::new(),
    )
    .expect("first composed plan");
    let second = ComposedPlan::new(
        &source,
        plan,
        GraphExecutionClass::PartialSafe,
        clauses.into_iter().rev().collect(),
        relation_evidence.into_iter().rev().collect(),
        Vec::new(),
        Vec::new(),
    )
    .expect("second composed plan");
    assert_eq!(
        first.canonical_bytes().expect("first bytes"),
        second.canonical_bytes().expect("second bytes")
    );
}

#[test]
fn rejects_missing_dangling_and_duplicate_clauses() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let plan = Plan::new(
        &source,
        generation,
        vec![basic_node(&source, "a", vec![span(&source, 0)])],
        Vec::new(),
    )
    .expect("plan");
    let valid = clause(
        "a",
        Polarity::Affirmed,
        vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0))],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan.clone(),
            GraphExecutionClass::PartialSafe,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::MissingClause))
    );
    let dangling = clause(
        "b",
        Polarity::Affirmed,
        vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0))],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan.clone(),
            GraphExecutionClass::PartialSafe,
            vec![dangling],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::DanglingClause))
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![valid.clone(), valid],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::DuplicateClause))
    );
}

#[test]
fn rejects_duplicate_mismatched_and_dangling_typed_evidence() {
    let source = source();
    let predicate = span(&source, 0);
    assert_eq!(
        ClauseSemantics::new(
            node_id("a"),
            intent_id("a"),
            Polarity::Affirmed,
            vec![
                EvidenceAtom::new(EvidenceKind::Predicate, predicate.clone()),
                EvidenceAtom::new(EvidenceKind::Predicate, predicate.clone()),
            ],
        ),
        Err(CoreError::Duplicate {
            kind: DuplicateKind::Evidence
        })
    );

    let generation = CatalogGeneration::new(1).expect("generation");
    let plan = Plan::new(
        &source,
        generation,
        vec![basic_node(
            &source,
            "a",
            vec![predicate.clone(), span(&source, 1)],
        )],
        Vec::new(),
    )
    .expect("plan");
    let incomplete = clause(
        "a",
        Polarity::Affirmed,
        vec![EvidenceAtom::new(
            EvidenceKind::Predicate,
            predicate.clone(),
        )],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![incomplete],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::ClauseEvidenceMismatch
        ))
    );

    let node = PlanNode::new(
        node_id("a"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability"),
        OperationId::new("fixture_tecnica:operation").expect("operation"),
        vec![Slot::new(slot_id("present"), SlotValue::Integer(1))],
        vec![predicate.clone(), span(&source, 1)],
    )
    .expect("node");
    let plan = Plan::new(&source, generation, vec![node], Vec::new()).expect("plan");
    let dangling = clause(
        "a",
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, predicate),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("missing")), span(&source, 1)),
        ],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![dangling],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::DanglingArgument))
    );
}

#[test]
fn rejects_missing_dangling_and_duplicate_relation_evidence() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let relation = Relation::new(node_id("a"), node_id("b"), RelationKind::Precedes);
    let plan = Plan::new(
        &source,
        generation,
        vec![
            basic_node(&source, "a", vec![span(&source, 0)]),
            basic_node(&source, "b", vec![span(&source, 1)]),
        ],
        vec![relation.clone()],
    )
    .expect("plan");
    let clauses = vec![
        clause(
            "a",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0))],
        ),
        clause(
            "b",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 1))],
        ),
    ];
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan.clone(),
            GraphExecutionClass::PartialSafe,
            clauses.clone(),
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::MissingRelationEvidence
        ))
    );
    let dangling = RelationEvidence::new(
        Relation::new(node_id("a"), node_id("b"), RelationKind::Requires),
        vec![span(&source, 2)],
    )
    .expect("relation evidence");
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan.clone(),
            GraphExecutionClass::PartialSafe,
            clauses.clone(),
            vec![dangling],
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::DanglingRelationEvidence
        ))
    );
    let valid = RelationEvidence::new(relation, vec![span(&source, 2)]).expect("relation evidence");
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            clauses,
            vec![valid.clone(), valid],
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::DuplicateRelationEvidence
        ))
    );
    assert_eq!(
        RelationEvidence::new(
            Relation::new(node_id("a"), node_id("b"), RelationKind::Precedes),
            Vec::new(),
        ),
        Err(CoreError::EmptyCollection {
            kind: CollectionKind::RelationEvidence
        })
    );
}

#[test]
fn rejects_unclassified_or_inconsistently_independent_pairs() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let clauses = vec![
        clause(
            "a",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0))],
        ),
        clause(
            "b",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 1))],
        ),
    ];
    let no_relation = Plan::new(
        &source,
        generation,
        vec![
            basic_node(&source, "a", vec![span(&source, 0)]),
            basic_node(&source, "b", vec![span(&source, 1)]),
        ],
        Vec::new(),
    )
    .expect("plan");
    assert_eq!(
        ComposedPlan::new(
            &source,
            no_relation.clone(),
            GraphExecutionClass::PartialSafe,
            clauses.clone(),
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::UnclassifiedNodePair))
    );

    let dangling = IndependentPair::new(node_id("a"), node_id("missing")).expect("canonical pair");
    assert_eq!(
        ComposedPlan::new(
            &source,
            no_relation,
            GraphExecutionClass::AtomicOnly,
            clauses.clone(),
            Vec::new(),
            vec![dangling],
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::DanglingIndependentPair
        ))
    );

    let relation = Relation::new(node_id("a"), node_id("b"), RelationKind::Precedes);
    let related = Plan::new(
        &source,
        generation,
        vec![
            basic_node(&source, "a", vec![span(&source, 0)]),
            basic_node(&source, "b", vec![span(&source, 1)]),
        ],
        vec![relation.clone()],
    )
    .expect("plan");
    assert_eq!(
        ComposedPlan::new(
            &source,
            related,
            GraphExecutionClass::AtomicOnly,
            clauses,
            vec![
                RelationEvidence::new(relation, vec![span(&source, 2)]).expect("relation evidence")
            ],
            vec![IndependentPair::new(node_id("b"), node_id("a")).expect("canonical pair")],
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::RelatedIndependentPair
        ))
    );
}

#[test]
fn enforces_polarity_and_exact_derived_execution_class() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let plan = Plan::new(
        &source,
        generation,
        vec![basic_node(
            &source,
            "a",
            vec![span(&source, 0), span(&source, 1)],
        )],
        Vec::new(),
    )
    .expect("plan");
    let inconsistent = clause(
        "a",
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0)),
            EvidenceAtom::new(EvidenceKind::Negation, span(&source, 1)),
        ],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan.clone(),
            GraphExecutionClass::PartialSafe,
            vec![inconsistent],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::PolarityEvidenceMismatch
        ))
    );

    let negated = clause(
        "a",
        Polarity::Negated,
        vec![
            EvidenceAtom::new(EvidenceKind::Negation, span(&source, 1)),
            EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0)),
        ],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan.clone(),
            GraphExecutionClass::PartialSafe,
            vec![negated.clone()],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::ExecutionClassMismatch
        ))
    );
    let valid = ComposedPlan::new(
        &source,
        plan,
        GraphExecutionClass::NonExecutable,
        vec![negated],
        Vec::new(),
        Vec::new(),
        Vec::new(),
    )
    .expect("non-executable plan");
    assert_eq!(valid.execution_class(), GraphExecutionClass::NonExecutable);
}

#[test]
fn shares_require_valid_unique_equal_acyclic_endpoints() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let predicate = span(&source, 0);
    let cue = span(&source, 1);
    let build = |right_value| {
        let node = PlanNode::new(
            node_id("a"),
            CapabilityId::new("fixture_tecnica:capability").expect("capability"),
            OperationId::new("fixture_tecnica:operation").expect("operation"),
            vec![
                Slot::new(slot_id("left"), SlotValue::Integer(1)),
                Slot::new(slot_id("right"), SlotValue::Integer(right_value)),
            ],
            vec![predicate.clone()],
        )
        .expect("node");
        Plan::new(&source, generation, vec![node], Vec::new()).expect("plan")
    };
    let clause = clause(
        "a",
        Polarity::Affirmed,
        vec![EvidenceAtom::new(
            EvidenceKind::Predicate,
            predicate.clone(),
        )],
    );
    let left = ArgumentEndpoint::new(node_id("a"), slot_id("left"));
    let right = ArgumentEndpoint::new(node_id("a"), slot_id("right"));
    let one_way =
        ArgumentShare::new(left.clone(), right.clone(), vec![cue.clone()]).expect("argument share");
    assert_eq!(
        ComposedPlan::new(
            &source,
            build(2),
            GraphExecutionClass::PartialSafe,
            vec![clause.clone()],
            Vec::new(),
            Vec::new(),
            vec![one_way.clone()],
        ),
        Err(semantic_error(SemanticPlanErrorKind::ShareValueMismatch))
    );

    let reverse =
        ArgumentShare::new(right.clone(), left.clone(), vec![cue.clone()]).expect("share");
    assert_eq!(
        ComposedPlan::new(
            &source,
            build(1),
            GraphExecutionClass::PartialSafe,
            vec![clause.clone()],
            Vec::new(),
            Vec::new(),
            vec![one_way.clone(), reverse],
        ),
        Err(semantic_error(SemanticPlanErrorKind::ShareCycle))
    );

    let dangling = ArgumentShare::new(
        left,
        ArgumentEndpoint::new(node_id("a"), slot_id("missing")),
        vec![cue],
    )
    .expect("syntactically valid share");
    assert_eq!(
        ComposedPlan::new(
            &source,
            build(1),
            GraphExecutionClass::PartialSafe,
            vec![clause],
            Vec::new(),
            Vec::new(),
            vec![dangling],
        ),
        Err(semantic_error(SemanticPlanErrorKind::DanglingArgumentShare))
    );
}

#[test]
fn slot_support_is_exactly_direct_xor_one_inbound_share() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let predicate = span(&source, 0);
    let argument = span(&source, 1);
    let node = PlanNode::new(
        node_id("a"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability"),
        OperationId::new("fixture_tecnica:operation").expect("operation"),
        vec![Slot::new(slot_id("value"), SlotValue::Integer(1))],
        vec![predicate.clone()],
    )
    .expect("node");
    let plan = Plan::new(&source, generation, vec![node], Vec::new()).expect("plan");
    let unsupported = clause(
        "a",
        Polarity::Affirmed,
        vec![EvidenceAtom::new(
            EvidenceKind::Predicate,
            predicate.clone(),
        )],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![unsupported],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::MissingSlotSupport))
    );

    let node = PlanNode::new(
        node_id("a"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability"),
        OperationId::new("fixture_tecnica:operation").expect("operation"),
        vec![
            Slot::new(slot_id("from"), SlotValue::Integer(1)),
            Slot::new(slot_id("to"), SlotValue::Integer(1)),
        ],
        vec![predicate.clone(), argument.clone()],
    )
    .expect("node");
    let plan = Plan::new(&source, generation, vec![node], Vec::new()).expect("plan");
    let direct = clause(
        "a",
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, predicate),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("from")), argument.clone()),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("to")), argument),
        ],
    );
    let share = ArgumentShare::new(
        ArgumentEndpoint::new(node_id("a"), slot_id("from")),
        ArgumentEndpoint::new(node_id("a"), slot_id("to")),
        vec![span(&source, 2)],
    )
    .expect("share");
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![direct],
            Vec::new(),
            Vec::new(),
            vec![share],
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::ConflictingSlotSupport
        ))
    );
}

#[test]
fn rejects_resolved_entity_operation_contradiction() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let entity = EntityRef::new(
        EntityId::new("fixture_tecnica:entity").expect("entity"),
        generation,
    );
    let make_node = |suffix: &str, evidence: Vec<Utf8Span>| {
        PlanNode::new(
            node_id(suffix),
            CapabilityId::new("fixture_tecnica:capability").expect("capability"),
            OperationId::new("fixture_tecnica:operation").expect("operation"),
            vec![Slot::new(
                slot_id("entity"),
                SlotValue::Entity(entity.clone()),
            )],
            evidence,
        )
        .expect("node")
    };
    let plan = Plan::new(
        &source,
        generation,
        vec![
            make_node("a", vec![span(&source, 0), span(&source, 1)]),
            make_node(
                "b",
                vec![span(&source, 2), span(&source, 3), span(&source, 4)],
            ),
        ],
        Vec::new(),
    )
    .expect("plan");
    let clauses = vec![
        clause(
            "a",
            Polarity::Affirmed,
            vec![
                EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 0)),
                EvidenceAtom::new(EvidenceKind::Argument(slot_id("entity")), span(&source, 1)),
            ],
        ),
        clause(
            "b",
            Polarity::Negated,
            vec![
                EvidenceAtom::new(EvidenceKind::Predicate, span(&source, 2)),
                EvidenceAtom::new(EvidenceKind::Argument(slot_id("entity")), span(&source, 3)),
                EvidenceAtom::new(EvidenceKind::Negation, span(&source, 4)),
            ],
        ),
    ];
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::NonExecutable,
            clauses,
            Vec::new(),
            vec![IndependentPair::new(node_id("a"), node_id("b")).expect("pair")],
            Vec::new(),
        ),
        Err(semantic_error(SemanticPlanErrorKind::ContradictoryPolarity))
    );
}

#[test]
fn rejects_foreign_spans_even_when_source_bytes_match() {
    let foreign = source();
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let plan = Plan::new(
        &source,
        generation,
        vec![basic_node(&source, "a", vec![span(&source, 0)])],
        Vec::new(),
    )
    .expect("plan");
    let clause = clause(
        "a",
        Polarity::Affirmed,
        vec![EvidenceAtom::new(
            EvidenceKind::Predicate,
            span(&foreign, 0),
        )],
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![clause],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ),
        Err(CoreError::SpanSourceMismatch)
    );
}

#[test]
fn canonicalizes_shares_and_rejects_duplicate_pairs_or_destinations() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let predicate = span(&source, 0);
    let direct = span(&source, 1);
    let first_cue = span(&source, 2);
    let second_cue = span(&source, 3);
    let slots = vec![
        Slot::new(slot_id("source"), SlotValue::Integer(7)),
        Slot::new(slot_id("target_a"), SlotValue::Integer(7)),
        Slot::new(slot_id("target_b"), SlotValue::Integer(7)),
    ];
    let node = PlanNode::new(
        node_id("a"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability"),
        OperationId::new("fixture_tecnica:operation").expect("operation"),
        slots,
        vec![predicate.clone(), direct.clone()],
    )
    .expect("node");
    let plan = Plan::new(&source, generation, vec![node], Vec::new()).expect("plan");
    let share_clause = clause(
        "a",
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, predicate),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("source")), direct),
        ],
    );
    let source_endpoint = ArgumentEndpoint::new(node_id("a"), slot_id("source"));
    let first = ArgumentShare::new(
        source_endpoint.clone(),
        ArgumentEndpoint::new(node_id("a"), slot_id("target_a")),
        vec![second_cue.clone(), first_cue.clone()],
    )
    .expect("first share");
    let second = ArgumentShare::new(
        source_endpoint,
        ArgumentEndpoint::new(node_id("a"), slot_id("target_b")),
        vec![second_cue],
    )
    .expect("second share");
    let forward = ComposedPlan::new(
        &source,
        plan.clone(),
        GraphExecutionClass::PartialSafe,
        vec![share_clause.clone()],
        Vec::new(),
        Vec::new(),
        vec![first.clone(), second.clone()],
    )
    .expect("forward plan");
    let reverse = ComposedPlan::new(
        &source,
        plan.clone(),
        GraphExecutionClass::PartialSafe,
        vec![share_clause.clone()],
        Vec::new(),
        Vec::new(),
        vec![second, first.clone()],
    )
    .expect("reverse plan");
    assert_eq!(
        forward.canonical_bytes().expect("forward bytes"),
        reverse.canonical_bytes().expect("reverse bytes")
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![share_clause],
            Vec::new(),
            Vec::new(),
            vec![first.clone(), first],
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::DuplicateArgumentShare
        ))
    );

    let duplicate_pair_source =
        RequestText::new(format!("{PREFIX}abcdefghijklmnopqrstuvwxyz0123456789"))
            .expect("technical source");
    let duplicate_pair_plan = Plan::new(
        &duplicate_pair_source,
        generation,
        vec![
            basic_node(
                &duplicate_pair_source,
                "a",
                vec![span(&duplicate_pair_source, 0)],
            ),
            basic_node(
                &duplicate_pair_source,
                "b",
                vec![span(&duplicate_pair_source, 1)],
            ),
        ],
        Vec::new(),
    )
    .expect("pair plan");
    let duplicate_pair_clauses = vec![
        clause(
            "a",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(
                EvidenceKind::Predicate,
                span(&duplicate_pair_source, 0),
            )],
        ),
        clause(
            "b",
            Polarity::Affirmed,
            vec![EvidenceAtom::new(
                EvidenceKind::Predicate,
                span(&duplicate_pair_source, 1),
            )],
        ),
    ];
    let pair = IndependentPair::new(node_id("a"), node_id("b")).expect("pair");
    assert_eq!(
        ComposedPlan::new(
            &duplicate_pair_source,
            duplicate_pair_plan,
            GraphExecutionClass::AtomicOnly,
            duplicate_pair_clauses,
            Vec::new(),
            vec![pair.clone(), pair],
            Vec::new(),
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::DuplicateIndependentPair
        ))
    );
}

#[test]
fn rejects_two_shares_with_the_same_destination_endpoint() {
    let source = source();
    let generation = CatalogGeneration::new(1).expect("generation");
    let predicate = span(&source, 0);
    let first_argument = span(&source, 1);
    let second_argument = span(&source, 2);
    let node = PlanNode::new(
        node_id("a"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability"),
        OperationId::new("fixture_tecnica:operation").expect("operation"),
        vec![
            Slot::new(slot_id("source_a"), SlotValue::Integer(7)),
            Slot::new(slot_id("source_b"), SlotValue::Integer(7)),
            Slot::new(slot_id("target"), SlotValue::Integer(7)),
        ],
        vec![
            predicate.clone(),
            first_argument.clone(),
            second_argument.clone(),
        ],
    )
    .expect("node");
    let plan = Plan::new(&source, generation, vec![node], Vec::new()).expect("plan");
    let clause = clause(
        "a",
        Polarity::Affirmed,
        vec![
            EvidenceAtom::new(EvidenceKind::Predicate, predicate),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("source_a")), first_argument),
            EvidenceAtom::new(EvidenceKind::Argument(slot_id("source_b")), second_argument),
        ],
    );
    let target = ArgumentEndpoint::new(node_id("a"), slot_id("target"));
    let shares = vec![
        ArgumentShare::new(
            ArgumentEndpoint::new(node_id("a"), slot_id("source_a")),
            target.clone(),
            vec![span(&source, 3)],
        )
        .expect("share"),
        ArgumentShare::new(
            ArgumentEndpoint::new(node_id("a"), slot_id("source_b")),
            target,
            vec![span(&source, 4)],
        )
        .expect("share"),
    ];
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![clause],
            Vec::new(),
            Vec::new(),
            shares,
        ),
        Err(semantic_error(
            SemanticPlanErrorKind::DuplicateShareDestination
        ))
    );
}
