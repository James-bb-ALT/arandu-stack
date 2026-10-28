use nlu_core::{
    ArgumentEndpoint, ArgumentShare, CapabilityId, CatalogGeneration, ClauseSemantics,
    ComposedPlan, EvidenceAtom, EvidenceKind, GraphExecutionClass, IndependentPair, IntentId,
    NodeId, OperationId, Plan, PlanNode, Polarity, Relation, RelationEvidence, RelationKind,
    RequestText, Slot, SlotId,
};

use crate::{
    PlanEngineError, PlanEngineErrorCode, Result,
    model::{
        DraftExecutionClass, DraftPolarity, NodeDraft, PlanDraft, RelationDraft, ResolvedBinding,
    },
};

pub(crate) fn build(
    source: &RequestText,
    generation: CatalogGeneration,
    draft: PlanDraft,
) -> Result<ComposedPlan> {
    let mut nodes = Vec::with_capacity(draft.nodes.len());
    let mut clauses = Vec::with_capacity(draft.nodes.len());
    for node in &draft.nodes {
        let (plan_node, clause) = build_node(source, node, &draft)?;
        nodes.push(plan_node);
        clauses.push(clause);
    }

    let mut relations = Vec::with_capacity(draft.relations.len());
    let mut relation_evidence = Vec::with_capacity(draft.relations.len());
    for relation in &draft.relations {
        let relation = build_relation(relation)?;
        relation_evidence.push(
            RelationEvidence::new(relation.clone(), relation_span_evidence(&draft, &relation)?)
                .map_err(core_error)?,
        );
        relations.push(relation);
    }

    let independent_pairs = draft
        .independent_pairs
        .iter()
        .map(|pair| {
            IndependentPair::new(node_id(pair.left_node)?, node_id(pair.right_node)?)
                .map_err(core_error)
        })
        .collect::<Result<Vec<_>>>()?;
    let argument_shares = draft
        .argument_shares
        .iter()
        .map(|share| {
            ArgumentShare::new(
                ArgumentEndpoint::new(node_id(share.from_node)?, slot_id(share.from_slot)?),
                ArgumentEndpoint::new(node_id(share.to_node)?, slot_id(share.to_slot)?),
                share.evidence.clone(),
            )
            .map_err(core_error)
        })
        .collect::<Result<Vec<_>>>()?;

    let plan = Plan::new(source, generation, nodes, relations).map_err(core_error)?;
    ComposedPlan::new(
        source,
        plan,
        execution_class(draft.execution_class),
        clauses,
        relation_evidence,
        independent_pairs,
        argument_shares,
    )
    .map_err(core_error)
}

fn build_node(
    _source: &RequestText,
    node: &NodeDraft,
    plan: &PlanDraft,
) -> Result<(PlanNode, ClauseSemantics)> {
    let node_id = node_id(node.id)?;
    let mut slots = Vec::with_capacity(node.slots.len());
    let mut atoms = vec![EvidenceAtom::new(
        EvidenceKind::Predicate,
        node.predicate.clone(),
    )];
    let mut plan_evidence = vec![node.predicate.clone()];

    for binding in &node.slots {
        slots.push(build_slot(binding)?);
        if !is_share_destination(plan, node.id, binding.slot_id) {
            let id = slot_id(binding.slot_id)?;
            atoms.push(EvidenceAtom::new(
                EvidenceKind::Argument(id),
                binding.evidence.clone(),
            ));
            plan_evidence.push(binding.evidence.clone());
        }
    }
    if let Some(negation) = &node.negation {
        atoms.push(EvidenceAtom::new(EvidenceKind::Negation, negation.clone()));
        plan_evidence.push(negation.clone());
    }

    let plan_node = PlanNode::new(
        node_id.clone(),
        capability_id(node.capability)?,
        operation_id(node.operation)?,
        slots,
        plan_evidence,
    )
    .map_err(core_error)?;
    let clause = ClauseSemantics::new(
        node_id,
        intent_id(node.intent)?,
        polarity(node.polarity),
        atoms,
    )
    .map_err(core_error)?;
    Ok((plan_node, clause))
}

fn build_slot(binding: &ResolvedBinding) -> Result<Slot> {
    Ok(Slot::new(slot_id(binding.slot_id)?, binding.value.clone()))
}

fn build_relation(draft: &RelationDraft) -> Result<Relation> {
    Ok(Relation::new(
        node_id(draft.from_node)?,
        node_id(draft.to_node)?,
        RelationKind::Precedes,
    ))
}

fn relation_span_evidence(
    draft: &PlanDraft,
    relation: &Relation,
) -> Result<Vec<nlu_core::Utf8Span>> {
    draft
        .relations
        .iter()
        .find(|candidate| {
            candidate.from_node == relation.from().as_str()
                && candidate.to_node == relation.to().as_str()
        })
        .map(|candidate| candidate.evidence.clone())
        .ok_or_else(|| PlanEngineError::new(PlanEngineErrorCode::CoreContract))
}

fn is_share_destination(plan: &PlanDraft, node: &str, slot: &str) -> bool {
    plan.argument_shares
        .iter()
        .any(|share| share.to_node == node && share.to_slot == slot)
}

const fn execution_class(value: DraftExecutionClass) -> GraphExecutionClass {
    match value {
        DraftExecutionClass::PartialSafe => GraphExecutionClass::PartialSafe,
        DraftExecutionClass::AtomicOnly => GraphExecutionClass::AtomicOnly,
        DraftExecutionClass::NonExecutable => GraphExecutionClass::NonExecutable,
    }
}

const fn polarity(value: DraftPolarity) -> Polarity {
    match value {
        DraftPolarity::Affirmed => Polarity::Affirmed,
        DraftPolarity::Negated => Polarity::Negated,
    }
}

fn node_id(value: &str) -> Result<NodeId> {
    NodeId::new(value)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))
}

fn slot_id(value: &str) -> Result<SlotId> {
    SlotId::new(value)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))
}

fn intent_id(value: &str) -> Result<IntentId> {
    IntentId::new(value)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))
}

fn capability_id(value: &str) -> Result<CapabilityId> {
    CapabilityId::new(value)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))
}

fn operation_id(value: &str) -> Result<OperationId> {
    OperationId::new(value)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))
}

fn core_error(_: nlu_core::CoreError) -> PlanEngineError {
    PlanEngineError::new(PlanEngineErrorCode::CoreContract)
}
