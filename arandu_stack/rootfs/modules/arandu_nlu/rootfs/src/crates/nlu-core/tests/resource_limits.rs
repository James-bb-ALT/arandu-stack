use nlu_core::{
    ArgumentEndpoint, ArgumentShare, CapabilityId, CatalogGeneration, Clarification,
    ClarificationOption, ClauseSemantics, CollectionKind, ComposedPlan, Confidence, CoreError,
    EvidenceAtom, EvidenceKind, GraphExecutionClass, Hypothesis, HypothesisSet, IndependentPair,
    IntentId, MAX_AGGREGATE_ITEMS, MAX_ARGUMENT_SHARES, MAX_CANONICAL_PLAN_BYTES,
    MAX_CLARIFICATION_OPTIONS, MAX_EVIDENCE_SPANS, MAX_HYPOTHESES, MAX_IDENTIFIER_BYTES,
    MAX_INDEPENDENT_PAIRS, MAX_PLAN_NODES, MAX_RELATION_EVIDENCE_SPANS, MAX_RELATIONS,
    MAX_REQUEST_BYTES, MAX_SLOTS_PER_NODE, NodeId, OperationId, OptionId, Plan, PlanNode, Polarity,
    Relation, RelationEvidence, RelationKind, RequestText, SemanticPlanErrorKind, Slot, SlotId,
    SlotValue,
};

fn fixture_source(bytes: usize) -> RequestText {
    let prefix = "FIXTURE_TECNICA_";
    let value = format!("{prefix}{}", "A".repeat(bytes - prefix.len()));
    RequestText::new(value).expect("bounded technical source")
}

fn fixture_evidence(source: &RequestText, count: usize) -> Vec<nlu_core::Utf8Span> {
    let start = "FIXTURE_TECNICA_".len();
    (0..count)
        .map(|index| {
            source
                .span((start + index) as u64, (start + index + 1) as u64)
                .expect("technical evidence span")
        })
        .collect()
}

fn fixture_slots(count: usize) -> Vec<Slot> {
    (0..count)
        .map(|index| {
            Slot::new(
                SlotId::new(&format!("fixture_tecnica:slot_{index}")).expect("slot ID"),
                SlotValue::Integer(index as i64),
            )
        })
        .collect()
}

fn fixture_node(
    source: &RequestText,
    index: usize,
    slot_count: usize,
    evidence_count: usize,
) -> PlanNode {
    PlanNode::new(
        NodeId::new(&format!("fixture_tecnica:node_{index}")).expect("node ID"),
        CapabilityId::new("fixture_tecnica:capability").expect("capability ID"),
        OperationId::new("fixture_tecnica:operation").expect("operation ID"),
        fixture_slots(slot_count),
        fixture_evidence(source, evidence_count),
    )
    .expect("bounded node")
}

#[test]
fn request_and_identifier_limits_accept_n_and_reject_n_plus_one() {
    assert_eq!(fixture_source(MAX_REQUEST_BYTES).len(), MAX_REQUEST_BYTES);
    assert_eq!(
        RequestText::new("A".repeat(MAX_REQUEST_BYTES + 1)),
        Err(CoreError::RequestTooLarge {
            limit: MAX_REQUEST_BYTES as u32
        })
    );

    let namespace = "fixture_tecnica:";
    let exact = format!(
        "{namespace}a{}",
        "a".repeat(MAX_IDENTIFIER_BYTES - namespace.len() - 1)
    );
    assert_eq!(exact.len(), MAX_IDENTIFIER_BYTES);
    assert!(IntentId::new(&exact).is_ok());
    let too_long = format!("{exact}a");
    assert_eq!(IntentId::new(&too_long), Err(CoreError::InvalidIdentifier));
}

#[test]
fn hypothesis_and_clarification_limits_accept_n_and_reject_n_plus_one() {
    let source = fixture_source(128);
    let evidence = fixture_evidence(&source, 1);
    let hypotheses: Vec<_> = (0..=MAX_HYPOTHESES)
        .map(|index| {
            Hypothesis::new(
                &source,
                IntentId::new(&format!("fixture_tecnica:intent_{index}")).expect("intent ID"),
                Confidence::from_basis_points(index as u16).expect("score"),
                evidence.clone(),
            )
            .expect("hypothesis")
        })
        .collect();
    assert!(HypothesisSet::new(&source, hypotheses[..MAX_HYPOTHESES].to_vec()).is_ok());
    assert_eq!(
        HypothesisSet::new(&source, hypotheses),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::Hypotheses,
            limit: MAX_HYPOTHESES as u16
        })
    );

    let options: Vec<_> = (0..=MAX_CLARIFICATION_OPTIONS)
        .map(|index| {
            ClarificationOption::new(
                OptionId::new(&format!("fixture_tecnica:option_{index}")).expect("option ID"),
                Hypothesis::new(
                    &source,
                    IntentId::new(&format!("fixture_tecnica:choice_{index}")).expect("intent ID"),
                    Confidence::from_basis_points(index as u16).expect("score"),
                    evidence.clone(),
                )
                .expect("hypothesis"),
            )
        })
        .collect();
    assert!(Clarification::new(&source, options[..MAX_CLARIFICATION_OPTIONS].to_vec()).is_ok());
    assert_eq!(
        Clarification::new(&source, options),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::ClarificationOptions,
            limit: MAX_CLARIFICATION_OPTIONS as u16
        })
    );
}

#[test]
fn node_slot_and_evidence_limits_accept_n_and_reject_n_plus_one() {
    let source = fixture_source(256);
    assert!(
        PlanNode::new(
            NodeId::new("fixture_tecnica:node_slots_n").expect("ID"),
            CapabilityId::new("fixture_tecnica:capability").expect("ID"),
            OperationId::new("fixture_tecnica:operation").expect("ID"),
            fixture_slots(MAX_SLOTS_PER_NODE),
            fixture_evidence(&source, 1),
        )
        .is_ok()
    );
    assert_eq!(
        PlanNode::new(
            NodeId::new("fixture_tecnica:node_slots_n1").expect("ID"),
            CapabilityId::new("fixture_tecnica:capability").expect("ID"),
            OperationId::new("fixture_tecnica:operation").expect("ID"),
            fixture_slots(MAX_SLOTS_PER_NODE + 1),
            fixture_evidence(&source, 1),
        ),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::Slots,
            limit: MAX_SLOTS_PER_NODE as u16
        })
    );

    assert!(
        PlanNode::new(
            NodeId::new("fixture_tecnica:node_evidence_n").expect("ID"),
            CapabilityId::new("fixture_tecnica:capability").expect("ID"),
            OperationId::new("fixture_tecnica:operation").expect("ID"),
            Vec::new(),
            fixture_evidence(&source, MAX_EVIDENCE_SPANS),
        )
        .is_ok()
    );
    assert_eq!(
        PlanNode::new(
            NodeId::new("fixture_tecnica:node_evidence_n1").expect("ID"),
            CapabilityId::new("fixture_tecnica:capability").expect("ID"),
            OperationId::new("fixture_tecnica:operation").expect("ID"),
            Vec::new(),
            fixture_evidence(&source, MAX_EVIDENCE_SPANS + 1),
        ),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::Evidence,
            limit: MAX_EVIDENCE_SPANS as u16
        })
    );
}

#[test]
fn plan_node_relation_and_aggregate_limits_accept_n_and_reject_n_plus_one() {
    let source = fixture_source(256);
    let generation = CatalogGeneration::new(1).expect("generation");
    let nodes: Vec<_> = (0..=MAX_PLAN_NODES)
        .map(|index| fixture_node(&source, index, 0, 1))
        .collect();
    assert!(
        Plan::new(
            &source,
            generation,
            nodes[..MAX_PLAN_NODES].to_vec(),
            Vec::new()
        )
        .is_ok()
    );
    assert_eq!(
        Plan::new(&source, generation, nodes.clone(), Vec::new()),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::PlanNodes,
            limit: MAX_PLAN_NODES as u16
        })
    );

    let bounded_nodes = nodes[..MAX_PLAN_NODES].to_vec();
    let mut relations = Vec::new();
    'outer: for from in 0..bounded_nodes.len() {
        for to in (from + 1)..bounded_nodes.len() {
            relations.push(Relation::new(
                bounded_nodes[from].id().clone(),
                bounded_nodes[to].id().clone(),
                RelationKind::Precedes,
            ));
            if relations.len() == MAX_RELATIONS + 1 {
                break 'outer;
            }
        }
    }
    assert!(
        Plan::new(
            &source,
            generation,
            bounded_nodes.clone(),
            relations[..MAX_RELATIONS].to_vec()
        )
        .is_ok()
    );
    assert_eq!(
        Plan::new(&source, generation, bounded_nodes, relations),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::Relations,
            limit: MAX_RELATIONS as u16
        })
    );

    let exact_aggregate: Vec<_> = (0..MAX_PLAN_NODES)
        .map(|index| fixture_node(&source, index, MAX_SLOTS_PER_NODE, 31))
        .collect();
    assert_eq!(
        MAX_PLAN_NODES + MAX_PLAN_NODES * MAX_SLOTS_PER_NODE + MAX_PLAN_NODES * 31,
        MAX_AGGREGATE_ITEMS
    );
    assert!(Plan::new(&source, generation, exact_aggregate, Vec::new()).is_ok());

    let over_aggregate: Vec<_> = (0..MAX_PLAN_NODES)
        .map(|index| {
            fixture_node(
                &source,
                index,
                MAX_SLOTS_PER_NODE,
                if index == 0 { 32 } else { 31 },
            )
        })
        .collect();
    assert_eq!(
        Plan::new(&source, generation, over_aggregate, Vec::new()),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::AggregateItems,
            limit: MAX_AGGREGATE_ITEMS as u16
        })
    );
}

#[test]
fn semantic_evidence_limits_accept_n_and_reject_n_plus_one() {
    let source = fixture_source(256);
    let spans = fixture_evidence(&source, MAX_RELATION_EVIDENCE_SPANS + 1);
    let atoms: Vec<_> = spans
        .iter()
        .cloned()
        .map(|span| EvidenceAtom::new(EvidenceKind::Predicate, span))
        .collect();
    let node = NodeId::new("fixture_tecnica:node_limit").expect("node ID");
    let intent = IntentId::new("fixture_tecnica:intent_limit").expect("intent ID");
    assert!(
        ClauseSemantics::new(
            node.clone(),
            intent.clone(),
            Polarity::Affirmed,
            atoms[..MAX_EVIDENCE_SPANS].to_vec(),
        )
        .is_ok()
    );
    assert_eq!(
        ClauseSemantics::new(node, intent, Polarity::Affirmed, atoms),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::Evidence,
            limit: MAX_EVIDENCE_SPANS as u16,
        })
    );

    let relation = Relation::new(
        NodeId::new("fixture_tecnica:node_a").expect("node ID"),
        NodeId::new("fixture_tecnica:node_b").expect("node ID"),
        RelationKind::Precedes,
    );
    assert!(
        RelationEvidence::new(
            relation.clone(),
            spans[..MAX_RELATION_EVIDENCE_SPANS].to_vec(),
        )
        .is_ok()
    );
    assert_eq!(
        RelationEvidence::new(relation, spans.clone()),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::RelationEvidence,
            limit: MAX_RELATION_EVIDENCE_SPANS as u16,
        })
    );

    let from = ArgumentEndpoint::new(
        NodeId::new("fixture_tecnica:node_a").expect("node ID"),
        SlotId::new("fixture_tecnica:slot_a").expect("slot ID"),
    );
    let to = ArgumentEndpoint::new(
        NodeId::new("fixture_tecnica:node_b").expect("node ID"),
        SlotId::new("fixture_tecnica:slot_b").expect("slot ID"),
    );
    assert!(
        ArgumentShare::new(
            from.clone(),
            to.clone(),
            spans[..MAX_RELATION_EVIDENCE_SPANS].to_vec(),
        )
        .is_ok()
    );
    assert_eq!(
        ArgumentShare::new(from, to, spans),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::ShareEvidence,
            limit: MAX_RELATION_EVIDENCE_SPANS as u16,
        })
    );
}

#[test]
fn independent_pair_limit_accepts_n_and_rejects_n_plus_one() {
    let source = fixture_source(128);
    let generation = CatalogGeneration::new(1).expect("generation");
    let evidence = fixture_evidence(&source, 1)[0].clone();
    let node_id =
        |index| NodeId::new(&format!("fixture_tecnica:node_pair_{index}")).expect("node ID");
    let nodes: Vec<_> = (0..24)
        .map(|index| {
            PlanNode::new(
                node_id(index),
                CapabilityId::new("fixture_tecnica:capability").expect("capability"),
                OperationId::new("fixture_tecnica:operation").expect("operation"),
                Vec::new(),
                vec![evidence.clone()],
            )
            .expect("node")
        })
        .collect();
    let clauses: Vec<_> = (0..24)
        .map(|index| {
            ClauseSemantics::new(
                node_id(index),
                IntentId::new(&format!("fixture_tecnica:intent_pair_{index}")).expect("intent ID"),
                Polarity::Affirmed,
                vec![EvidenceAtom::new(EvidenceKind::Predicate, evidence.clone())],
            )
            .expect("clause")
        })
        .collect();
    let mut relations = Vec::new();
    let mut relation_evidence = Vec::new();
    let mut independent_pairs = Vec::new();
    for left in 0..24 {
        for right in (left + 1)..24 {
            if relations.len() < 20 {
                let relation = Relation::new(node_id(left), node_id(right), RelationKind::Precedes);
                relation_evidence.push(
                    RelationEvidence::new(relation.clone(), vec![evidence.clone()])
                        .expect("relation evidence"),
                );
                relations.push(relation);
            } else {
                independent_pairs
                    .push(IndependentPair::new(node_id(left), node_id(right)).expect("pair"));
            }
        }
    }
    assert_eq!(independent_pairs.len(), MAX_INDEPENDENT_PAIRS);
    let plan = Plan::new(&source, generation, nodes, relations).expect("plan");
    let composed = ComposedPlan::new(
        &source,
        plan.clone(),
        GraphExecutionClass::AtomicOnly,
        clauses.clone(),
        relation_evidence.clone(),
        independent_pairs.clone(),
        Vec::new(),
    )
    .expect("bounded composed plan");
    assert_eq!(composed.independent_pairs().len(), MAX_INDEPENDENT_PAIRS);

    let mut over = independent_pairs;
    over.push(over[0].clone());
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::AtomicOnly,
            clauses,
            relation_evidence,
            over,
            Vec::new(),
        ),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::IndependentPairs,
            limit: MAX_INDEPENDENT_PAIRS as u16,
        })
    );
}

#[test]
fn argument_share_limit_accepts_n_and_rejects_n_plus_one() {
    let source = fixture_source(128);
    let generation = CatalogGeneration::new(1).expect("generation");
    let predicate = fixture_evidence(&source, 1)[0].clone();
    let cue = fixture_evidence(&source, 2)[1].clone();
    let node_id = |index| NodeId::new(&format!("fixture_tecnica:n{index}")).expect("node ID");
    let slot_id = |index| SlotId::new(&format!("fixture_tecnica:s{index}")).expect("slot ID");
    let endpoint =
        |index| ArgumentEndpoint::new(node_id(index / MAX_SLOTS_PER_NODE), slot_id(index));
    let mut nodes = Vec::new();
    let mut clauses = Vec::new();
    for node_index in 0..9 {
        let start = node_index * MAX_SLOTS_PER_NODE;
        let end = (start + MAX_SLOTS_PER_NODE).min(MAX_ARGUMENT_SHARES + 1);
        let slots: Vec<_> = (start..end)
            .map(|index| Slot::new(slot_id(index), SlotValue::Integer(1)))
            .collect();
        nodes.push(
            PlanNode::new(
                node_id(node_index),
                CapabilityId::new("fixture_tecnica:c").expect("capability"),
                OperationId::new("fixture_tecnica:o").expect("operation"),
                slots,
                vec![predicate.clone()],
            )
            .expect("node"),
        );
        let mut atoms = vec![EvidenceAtom::new(
            EvidenceKind::Predicate,
            predicate.clone(),
        )];
        for index in start..end {
            if index == 0 {
                atoms.push(EvidenceAtom::new(
                    EvidenceKind::Argument(slot_id(index)),
                    predicate.clone(),
                ));
            }
        }
        clauses.push(
            ClauseSemantics::new(
                node_id(node_index),
                IntentId::new(&format!("fixture_tecnica:i{node_index}")).expect("intent ID"),
                Polarity::Affirmed,
                atoms,
            )
            .expect("clause"),
        );
    }
    let independent_pairs: Vec<_> = (0..9)
        .flat_map(|left| {
            let node_id = &node_id;
            (left + 1..9).map(move |right| {
                IndependentPair::new(node_id(left), node_id(right)).expect("pair")
            })
        })
        .collect();
    let shares: Vec<_> = (1..=MAX_ARGUMENT_SHARES)
        .map(|index| {
            ArgumentShare::new(endpoint(0), endpoint(index), vec![cue.clone()]).expect("share")
        })
        .collect();
    let plan = Plan::new(&source, generation, nodes, Vec::new()).expect("plan");
    let composed = ComposedPlan::new(
        &source,
        plan.clone(),
        GraphExecutionClass::AtomicOnly,
        clauses.clone(),
        Vec::new(),
        independent_pairs.clone(),
        shares.clone(),
    )
    .expect("bounded composed plan");
    assert_eq!(composed.argument_shares().len(), MAX_ARGUMENT_SHARES);
    assert!(composed.canonical_bytes().expect("canonical bytes").len() <= MAX_CANONICAL_PLAN_BYTES);

    let mut over = shares;
    over.push(
        ArgumentShare::new(endpoint(0), endpoint(MAX_ARGUMENT_SHARES + 1), vec![cue])
            .expect("one-over share"),
    );
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::AtomicOnly,
            clauses,
            Vec::new(),
            independent_pairs,
            over,
        ),
        Err(CoreError::CollectionTooLarge {
            kind: CollectionKind::ArgumentShares,
            limit: MAX_ARGUMENT_SHARES as u16,
        })
    );
}

#[test]
fn canonical_plan_byte_limit_rejects_one_complete_oversized_graph() {
    assert_eq!(MAX_CANONICAL_PLAN_BYTES, 65_536);
    let source = fixture_source(128);
    let generation = CatalogGeneration::new(1).expect("generation");
    let predicate = fixture_evidence(&source, 1)[0].clone();
    let cue = fixture_evidence(&source, 2)[1].clone();
    let padded = |kind: &str, index: usize| {
        let prefix = format!("fixture_tecnica:{kind}_{index}_");
        format!(
            "{prefix}{}",
            "a".repeat(MAX_IDENTIFIER_BYTES - prefix.len())
        )
    };
    let node_id = |index| NodeId::new(&padded("node", index)).expect("node ID");
    let slot_id = |index| SlotId::new(&padded("slot", index)).expect("slot ID");
    let capability = CapabilityId::new(&padded("capability", 0)).expect("capability ID");
    let operation = OperationId::new(&padded("operation", 0)).expect("operation ID");
    let mut nodes = Vec::new();
    let mut clauses = Vec::new();
    for node_index in 0..9 {
        let start = node_index * MAX_SLOTS_PER_NODE;
        let slots: Vec<_> = (start..start + MAX_SLOTS_PER_NODE)
            .map(|index| Slot::new(slot_id(index), SlotValue::Integer(1)))
            .collect();
        nodes.push(
            PlanNode::new(
                node_id(node_index),
                capability.clone(),
                operation.clone(),
                slots,
                vec![predicate.clone()],
            )
            .expect("node"),
        );
        let mut atoms = vec![EvidenceAtom::new(
            EvidenceKind::Predicate,
            predicate.clone(),
        )];
        atoms.extend((start..start + MAX_SLOTS_PER_NODE).map(|index| {
            EvidenceAtom::new(EvidenceKind::Argument(slot_id(index)), predicate.clone())
        }));
        clauses.push(
            ClauseSemantics::new(
                node_id(node_index),
                IntentId::new(&padded("intent", node_index)).expect("intent ID"),
                Polarity::Affirmed,
                atoms,
            )
            .expect("clause"),
        );
    }
    let mut relations = Vec::new();
    let mut relation_evidence = Vec::new();
    for left in 0..9 {
        for right in left + 1..9 {
            let relation = Relation::new(node_id(left), node_id(right), RelationKind::Precedes);
            relation_evidence.push(
                RelationEvidence::new(relation.clone(), vec![cue.clone()])
                    .expect("relation evidence"),
            );
            relations.push(relation);
        }
    }
    let plan = Plan::new(&source, generation, nodes, relations).expect("plan");
    assert_eq!(
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            clauses,
            relation_evidence,
            Vec::new(),
            Vec::new(),
        ),
        Err(CoreError::SemanticPlan(
            SemanticPlanErrorKind::CanonicalBytesTooLarge
        ))
    );
}
