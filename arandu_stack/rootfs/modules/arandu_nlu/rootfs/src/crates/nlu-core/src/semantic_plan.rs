use crate::{
    CollectionKind, CoreError, EvidenceKind::Argument, IntentId, MAX_AGGREGATE_ITEMS,
    MAX_ARGUMENT_SHARES, MAX_CANONICAL_PLAN_BYTES, MAX_EVIDENCE_SPANS, MAX_INDEPENDENT_PAIRS,
    MAX_RELATION_EVIDENCE_SPANS, MAX_RELATIONS, NodeId, Plan, PlanNode, Relation, RelationKind,
    RequestText, SemanticPlanErrorKind, Slot, SlotId, SlotValue, Utf8Span,
};
use core::fmt::{self, Write};

const SCHEMA_VERSION: &str = "p11-semantic-plan-v1";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum GraphExecutionClass {
    PartialSafe,
    AtomicOnly,
    NonExecutable,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Polarity {
    Affirmed,
    Negated,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum EvidenceKind {
    Predicate,
    Argument(SlotId),
    Negation,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct EvidenceAtom {
    kind: EvidenceKind,
    span: Utf8Span,
}

impl EvidenceAtom {
    #[must_use]
    pub const fn new(kind: EvidenceKind, span: Utf8Span) -> Self {
        Self { kind, span }
    }

    #[must_use]
    pub const fn kind(&self) -> &EvidenceKind {
        &self.kind
    }

    #[must_use]
    pub const fn span(&self) -> &Utf8Span {
        &self.span
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClauseSemantics {
    node: NodeId,
    intent: IntentId,
    polarity: Polarity,
    evidence: Vec<EvidenceAtom>,
}

impl ClauseSemantics {
    pub fn new(
        node: NodeId,
        intent: IntentId,
        polarity: Polarity,
        mut evidence: Vec<EvidenceAtom>,
    ) -> Result<Self, CoreError> {
        if evidence.is_empty() {
            return Err(CoreError::EmptyCollection {
                kind: CollectionKind::Evidence,
            });
        }
        if evidence.len() > MAX_EVIDENCE_SPANS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::Evidence,
                limit: MAX_EVIDENCE_SPANS as u16,
            });
        }
        evidence.sort();
        if evidence.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CoreError::Duplicate {
                kind: crate::DuplicateKind::Evidence,
            });
        }
        Ok(Self {
            node,
            intent,
            polarity,
            evidence,
        })
    }

    #[must_use]
    pub const fn node(&self) -> &NodeId {
        &self.node
    }

    #[must_use]
    pub const fn intent(&self) -> &IntentId {
        &self.intent
    }

    #[must_use]
    pub const fn polarity(&self) -> Polarity {
        self.polarity
    }

    #[must_use]
    pub fn evidence(&self) -> &[EvidenceAtom] {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct RelationEvidence {
    relation: Relation,
    evidence: Vec<Utf8Span>,
}

impl RelationEvidence {
    pub fn new(relation: Relation, mut evidence: Vec<Utf8Span>) -> Result<Self, CoreError> {
        if evidence.is_empty() {
            return Err(CoreError::EmptyCollection {
                kind: CollectionKind::RelationEvidence,
            });
        }
        if evidence.len() > MAX_RELATION_EVIDENCE_SPANS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::RelationEvidence,
                limit: MAX_RELATION_EVIDENCE_SPANS as u16,
            });
        }
        evidence.sort();
        if evidence.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CoreError::Duplicate {
                kind: crate::DuplicateKind::Evidence,
            });
        }
        Ok(Self { relation, evidence })
    }

    #[must_use]
    pub const fn relation(&self) -> &Relation {
        &self.relation
    }

    #[must_use]
    pub fn evidence(&self) -> &[Utf8Span] {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct IndependentPair {
    left: NodeId,
    right: NodeId,
}

impl IndependentPair {
    pub fn new(mut first: NodeId, mut second: NodeId) -> Result<Self, CoreError> {
        if first == second {
            return Err(semantic_error(SemanticPlanErrorKind::SelfIndependentPair));
        }
        if second < first {
            core::mem::swap(&mut first, &mut second);
        }
        Ok(Self {
            left: first,
            right: second,
        })
    }

    #[must_use]
    pub const fn left(&self) -> &NodeId {
        &self.left
    }

    #[must_use]
    pub const fn right(&self) -> &NodeId {
        &self.right
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ArgumentEndpoint {
    node: NodeId,
    slot: SlotId,
}

impl ArgumentEndpoint {
    #[must_use]
    pub const fn new(node: NodeId, slot: SlotId) -> Self {
        Self { node, slot }
    }

    #[must_use]
    pub const fn node(&self) -> &NodeId {
        &self.node
    }

    #[must_use]
    pub const fn slot(&self) -> &SlotId {
        &self.slot
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ArgumentShare {
    from: ArgumentEndpoint,
    to: ArgumentEndpoint,
    evidence: Vec<Utf8Span>,
}

impl ArgumentShare {
    pub fn new(
        from: ArgumentEndpoint,
        to: ArgumentEndpoint,
        mut evidence: Vec<Utf8Span>,
    ) -> Result<Self, CoreError> {
        if from == to {
            return Err(semantic_error(SemanticPlanErrorKind::ShareCycle));
        }
        if evidence.is_empty() {
            return Err(CoreError::EmptyCollection {
                kind: CollectionKind::ShareEvidence,
            });
        }
        if evidence.len() > MAX_RELATION_EVIDENCE_SPANS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::ShareEvidence,
                limit: MAX_RELATION_EVIDENCE_SPANS as u16,
            });
        }
        evidence.sort();
        if evidence.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CoreError::Duplicate {
                kind: crate::DuplicateKind::Evidence,
            });
        }
        Ok(Self { from, to, evidence })
    }

    #[must_use]
    pub const fn from(&self) -> &ArgumentEndpoint {
        &self.from
    }

    #[must_use]
    pub const fn to(&self) -> &ArgumentEndpoint {
        &self.to
    }

    #[must_use]
    pub fn evidence(&self) -> &[Utf8Span] {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ComposedPlan {
    source: RequestText,
    plan: Plan,
    execution_class: GraphExecutionClass,
    clauses: Vec<ClauseSemantics>,
    relation_evidence: Vec<RelationEvidence>,
    independent_pairs: Vec<IndependentPair>,
    argument_shares: Vec<ArgumentShare>,
}

impl ComposedPlan {
    pub fn new(
        source: &RequestText,
        plan: Plan,
        execution_class: GraphExecutionClass,
        mut clauses: Vec<ClauseSemantics>,
        mut relation_evidence: Vec<RelationEvidence>,
        mut independent_pairs: Vec<IndependentPair>,
        mut argument_shares: Vec<ArgumentShare>,
    ) -> Result<Self, CoreError> {
        Self::validate_collection_limits(
            &clauses,
            &relation_evidence,
            &independent_pairs,
            &argument_shares,
        )?;

        clauses.sort_by(|left, right| left.node.cmp(&right.node));
        if clauses.windows(2).any(|pair| pair[0].node == pair[1].node) {
            return Err(semantic_error(SemanticPlanErrorKind::DuplicateClause));
        }

        relation_evidence.sort();
        if relation_evidence
            .windows(2)
            .any(|pair| pair[0].relation == pair[1].relation)
        {
            return Err(semantic_error(
                SemanticPlanErrorKind::DuplicateRelationEvidence,
            ));
        }

        independent_pairs.sort();
        if independent_pairs.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(semantic_error(
                SemanticPlanErrorKind::DuplicateIndependentPair,
            ));
        }

        argument_shares.sort();
        if argument_shares.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(semantic_error(
                SemanticPlanErrorKind::DuplicateArgumentShare,
            ));
        }

        Self::validate_plan_source(source, &plan)?;
        Self::validate_clauses(source, &plan, &clauses)?;
        Self::validate_relation_evidence(source, &plan, &relation_evidence)?;
        Self::validate_independent_pairs(&plan, &independent_pairs)?;
        Self::validate_argument_shares(source, &plan, &clauses, &argument_shares)?;
        Self::validate_pair_classification(&plan, &independent_pairs)?;
        Self::validate_contradictions(&plan, &clauses)?;

        let expected_class = if clauses
            .iter()
            .any(|clause| clause.polarity == Polarity::Negated)
        {
            GraphExecutionClass::NonExecutable
        } else if independent_pairs.is_empty() {
            GraphExecutionClass::PartialSafe
        } else {
            GraphExecutionClass::AtomicOnly
        };
        if execution_class != expected_class {
            return Err(semantic_error(
                SemanticPlanErrorKind::ExecutionClassMismatch,
            ));
        }

        let composed = Self {
            source: source.clone(),
            plan,
            execution_class,
            clauses,
            relation_evidence,
            independent_pairs,
            argument_shares,
        };
        composed.canonical_bytes()?;
        Ok(composed)
    }

    fn validate_collection_limits(
        clauses: &[ClauseSemantics],
        relations: &[RelationEvidence],
        independent_pairs: &[IndependentPair],
        shares: &[ArgumentShare],
    ) -> Result<(), CoreError> {
        if relations.len() > MAX_RELATIONS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::RelationEvidence,
                limit: MAX_RELATIONS as u16,
            });
        }
        if independent_pairs.len() > MAX_INDEPENDENT_PAIRS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::IndependentPairs,
                limit: MAX_INDEPENDENT_PAIRS as u16,
            });
        }
        if shares.len() > MAX_ARGUMENT_SHARES {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::ArgumentShares,
                limit: MAX_ARGUMENT_SHARES as u16,
            });
        }

        let aggregate = clauses
            .len()
            .saturating_add(
                clauses
                    .iter()
                    .map(|clause| clause.evidence.len())
                    .sum::<usize>(),
            )
            .saturating_add(relations.len())
            .saturating_add(
                relations
                    .iter()
                    .map(|relation| relation.evidence.len())
                    .sum::<usize>(),
            )
            .saturating_add(independent_pairs.len())
            .saturating_add(shares.len())
            .saturating_add(
                shares
                    .iter()
                    .map(|share| share.evidence.len())
                    .sum::<usize>(),
            );
        if aggregate > MAX_AGGREGATE_ITEMS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::AggregateItems,
                limit: MAX_AGGREGATE_ITEMS as u16,
            });
        }
        Ok(())
    }

    fn validate_plan_source(source: &RequestText, plan: &Plan) -> Result<(), CoreError> {
        for node in plan.nodes() {
            if node.evidence().iter().any(|span| !span.belongs_to(source)) {
                return Err(CoreError::SpanSourceMismatch);
            }
            if node.slots().iter().any(|slot| {
                matches!(slot.value(), SlotValue::EvidenceText(span) if !span.belongs_to(source))
            }) {
                return Err(CoreError::SpanSourceMismatch);
            }
        }
        Ok(())
    }

    fn validate_clauses(
        source: &RequestText,
        plan: &Plan,
        clauses: &[ClauseSemantics],
    ) -> Result<(), CoreError> {
        for clause in clauses {
            let node = find_node(plan, &clause.node)
                .ok_or_else(|| semantic_error(SemanticPlanErrorKind::DanglingClause))?;
            if clause
                .evidence
                .iter()
                .any(|atom| !atom.span.belongs_to(source))
            {
                return Err(CoreError::SpanSourceMismatch);
            }
            if !clause
                .evidence
                .iter()
                .any(|atom| atom.kind == EvidenceKind::Predicate)
            {
                return Err(semantic_error(SemanticPlanErrorKind::MissingPredicate));
            }

            let has_negation = clause
                .evidence
                .iter()
                .any(|atom| atom.kind == EvidenceKind::Negation);
            if has_negation != (clause.polarity == Polarity::Negated) {
                return Err(semantic_error(
                    SemanticPlanErrorKind::PolarityEvidenceMismatch,
                ));
            }

            for atom in &clause.evidence {
                if let Argument(slot) = &atom.kind
                    && find_slot(node, slot).is_none()
                {
                    return Err(semantic_error(SemanticPlanErrorKind::DanglingArgument));
                }
            }

            let mut union: Vec<_> = clause
                .evidence
                .iter()
                .map(|atom| atom.span.clone())
                .collect();
            union.sort();
            union.dedup();
            if union != node.evidence() {
                return Err(semantic_error(
                    SemanticPlanErrorKind::ClauseEvidenceMismatch,
                ));
            }
        }

        if clauses.len() != plan.nodes().len() {
            return Err(semantic_error(SemanticPlanErrorKind::MissingClause));
        }
        Ok(())
    }

    fn validate_relation_evidence(
        source: &RequestText,
        plan: &Plan,
        relations: &[RelationEvidence],
    ) -> Result<(), CoreError> {
        for relation in relations {
            if relation
                .evidence
                .iter()
                .any(|span| !span.belongs_to(source))
            {
                return Err(CoreError::SpanSourceMismatch);
            }
            if plan.relations().binary_search(&relation.relation).is_err() {
                return Err(semantic_error(
                    SemanticPlanErrorKind::DanglingRelationEvidence,
                ));
            }
        }
        if relations.len() != plan.relations().len() {
            return Err(semantic_error(
                SemanticPlanErrorKind::MissingRelationEvidence,
            ));
        }
        Ok(())
    }

    fn validate_independent_pairs(
        plan: &Plan,
        independent_pairs: &[IndependentPair],
    ) -> Result<(), CoreError> {
        for pair in independent_pairs {
            if pair.left == pair.right {
                return Err(semantic_error(SemanticPlanErrorKind::SelfIndependentPair));
            }
            if find_node(plan, &pair.left).is_none() || find_node(plan, &pair.right).is_none() {
                return Err(semantic_error(
                    SemanticPlanErrorKind::DanglingIndependentPair,
                ));
            }
            if pair_is_related(plan, &pair.left, &pair.right) {
                return Err(semantic_error(
                    SemanticPlanErrorKind::RelatedIndependentPair,
                ));
            }
        }
        Ok(())
    }

    fn validate_argument_shares(
        source: &RequestText,
        plan: &Plan,
        clauses: &[ClauseSemantics],
        shares: &[ArgumentShare],
    ) -> Result<(), CoreError> {
        let mut destinations: Vec<_> = shares.iter().map(|share| &share.to).collect();
        destinations.sort();
        if destinations.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(semantic_error(
                SemanticPlanErrorKind::DuplicateShareDestination,
            ));
        }

        for share in shares {
            if share.evidence.iter().any(|span| !span.belongs_to(source)) {
                return Err(CoreError::SpanSourceMismatch);
            }
            let from = find_endpoint(plan, &share.from)
                .ok_or_else(|| semantic_error(SemanticPlanErrorKind::DanglingArgumentShare))?;
            let to = find_endpoint(plan, &share.to)
                .ok_or_else(|| semantic_error(SemanticPlanErrorKind::DanglingArgumentShare))?;
            if from.value() != to.value() {
                return Err(semantic_error(SemanticPlanErrorKind::ShareValueMismatch));
            }
        }
        validate_share_acyclic(shares)?;

        for node in plan.nodes() {
            let clause = clauses
                .binary_search_by(|clause| clause.node.cmp(node.id()))
                .ok()
                .map(|index| &clauses[index])
                .ok_or_else(|| semantic_error(SemanticPlanErrorKind::MissingClause))?;
            for slot in node.slots() {
                let direct = clause
                    .evidence
                    .iter()
                    .any(|atom| matches!(&atom.kind, Argument(id) if id == slot.id()));
                let inbound = shares
                    .iter()
                    .filter(|share| share.to.node == *node.id() && share.to.slot == *slot.id())
                    .count();
                match (direct, inbound) {
                    (true, 0) | (false, 1) => {}
                    (false, 0) => {
                        return Err(semantic_error(SemanticPlanErrorKind::MissingSlotSupport));
                    }
                    _ => {
                        return Err(semantic_error(
                            SemanticPlanErrorKind::ConflictingSlotSupport,
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    fn validate_pair_classification(
        plan: &Plan,
        independent_pairs: &[IndependentPair],
    ) -> Result<(), CoreError> {
        for (index, left) in plan.nodes().iter().enumerate() {
            for right in &plan.nodes()[index + 1..] {
                if !pair_is_related(plan, left.id(), right.id())
                    && !independent_pairs
                        .iter()
                        .any(|pair| pair.left == *left.id() && pair.right == *right.id())
                {
                    return Err(semantic_error(SemanticPlanErrorKind::UnclassifiedNodePair));
                }
            }
        }
        Ok(())
    }

    fn validate_contradictions(plan: &Plan, clauses: &[ClauseSemantics]) -> Result<(), CoreError> {
        for (index, left_clause) in clauses.iter().enumerate() {
            for right_clause in &clauses[index + 1..] {
                if left_clause.polarity == right_clause.polarity {
                    continue;
                }
                let left = find_node(plan, &left_clause.node)
                    .ok_or_else(|| semantic_error(SemanticPlanErrorKind::DanglingClause))?;
                let right = find_node(plan, &right_clause.node)
                    .ok_or_else(|| semantic_error(SemanticPlanErrorKind::DanglingClause))?;
                if left.capability() == right.capability()
                    && left.operation() == right.operation()
                    && share_resolved_entity(left, right)
                {
                    return Err(semantic_error(SemanticPlanErrorKind::ContradictoryPolarity));
                }
            }
        }
        Ok(())
    }

    #[must_use]
    pub const fn source(&self) -> &RequestText {
        &self.source
    }

    #[must_use]
    pub const fn plan(&self) -> &Plan {
        &self.plan
    }

    #[must_use]
    pub const fn execution_class(&self) -> GraphExecutionClass {
        self.execution_class
    }

    #[must_use]
    pub fn clauses(&self) -> &[ClauseSemantics] {
        &self.clauses
    }

    #[must_use]
    pub fn relation_evidence(&self) -> &[RelationEvidence] {
        &self.relation_evidence
    }

    #[must_use]
    pub fn independent_pairs(&self) -> &[IndependentPair] {
        &self.independent_pairs
    }

    #[must_use]
    pub fn argument_shares(&self) -> &[ArgumentShare] {
        &self.argument_shares
    }

    pub fn canonical_bytes(&self) -> Result<Vec<u8>, CoreError> {
        let mut writer = CanonicalWriter::new();
        writer.raw(r#"{"schema_version":"#)?;
        writer.quoted(SCHEMA_VERSION)?;
        writer.raw(r#","catalog_generation":"#)?;
        writer.unsigned(self.plan.catalog_generation().get())?;
        writer.raw(r#","execution_class":"#)?;
        writer.quoted(execution_class_tag(self.execution_class))?;
        writer.raw(r#","nodes":["#)?;
        for (index, node) in self.plan.nodes().iter().enumerate() {
            if index != 0 {
                writer.raw(",")?;
            }
            let clause_index = self
                .clauses
                .binary_search_by(|clause| clause.node.cmp(node.id()))
                .map_err(|_| semantic_error(SemanticPlanErrorKind::MissingClause))?;
            write_node(&mut writer, node, &self.clauses[clause_index])?;
        }
        writer.raw(r#"],"relations":["#)?;
        for (index, relation) in self.relation_evidence.iter().enumerate() {
            if index != 0 {
                writer.raw(",")?;
            }
            write_relation(&mut writer, relation)?;
        }
        writer.raw(r#"],"independent_pairs":["#)?;
        for (index, pair) in self.independent_pairs.iter().enumerate() {
            if index != 0 {
                writer.raw(",")?;
            }
            writer.raw(r#"{"left":"#)?;
            writer.quoted(pair.left.as_str())?;
            writer.raw(r#","right":"#)?;
            writer.quoted(pair.right.as_str())?;
            writer.raw("}")?;
        }
        writer.raw(r#"],"argument_shares":["#)?;
        for (index, share) in self.argument_shares.iter().enumerate() {
            if index != 0 {
                writer.raw(",")?;
            }
            write_share(&mut writer, share)?;
        }
        writer.raw("]}\n")?;
        Ok(writer.finish())
    }
}

fn semantic_error(kind: SemanticPlanErrorKind) -> CoreError {
    CoreError::SemanticPlan(kind)
}

fn find_node<'a>(plan: &'a Plan, id: &NodeId) -> Option<&'a PlanNode> {
    plan.nodes()
        .binary_search_by(|node| node.id().cmp(id))
        .ok()
        .map(|index| &plan.nodes()[index])
}

fn find_slot<'a>(node: &'a PlanNode, id: &SlotId) -> Option<&'a Slot> {
    node.slots()
        .binary_search_by(|slot| slot.id().cmp(id))
        .ok()
        .map(|index| &node.slots()[index])
}

fn find_endpoint<'a>(plan: &'a Plan, endpoint: &ArgumentEndpoint) -> Option<&'a Slot> {
    find_node(plan, &endpoint.node).and_then(|node| find_slot(node, &endpoint.slot))
}

fn pair_is_related(plan: &Plan, left: &NodeId, right: &NodeId) -> bool {
    plan.relations().iter().any(|relation| {
        (relation.from() == left && relation.to() == right)
            || (relation.from() == right && relation.to() == left)
    })
}

fn share_resolved_entity(left: &PlanNode, right: &PlanNode) -> bool {
    left.slots().iter().any(|left_slot| {
        let SlotValue::Entity(left_entity) = left_slot.value() else {
            return false;
        };
        right.slots().iter().any(
            |right_slot| matches!(right_slot.value(), SlotValue::Entity(right_entity) if right_entity == left_entity),
        )
    })
}

fn validate_share_acyclic(shares: &[ArgumentShare]) -> Result<(), CoreError> {
    let mut endpoints = Vec::with_capacity(shares.len().saturating_mul(2));
    for share in shares {
        endpoints.push(share.from.clone());
        endpoints.push(share.to.clone());
    }
    endpoints.sort();
    endpoints.dedup();

    let mut adjacency = vec![Vec::new(); endpoints.len()];
    let mut indegree = vec![0_usize; endpoints.len()];
    for share in shares {
        let from = endpoints
            .binary_search(&share.from)
            .map_err(|_| semantic_error(SemanticPlanErrorKind::DanglingArgumentShare))?;
        let to = endpoints
            .binary_search(&share.to)
            .map_err(|_| semantic_error(SemanticPlanErrorKind::DanglingArgumentShare))?;
        adjacency[from].push(to);
        indegree[to] += 1;
    }

    let mut ready: Vec<_> = indegree
        .iter()
        .enumerate()
        .filter_map(|(index, degree)| (*degree == 0).then_some(index))
        .collect();
    ready.reverse();
    let mut visited = 0;
    while let Some(index) = ready.pop() {
        visited += 1;
        for target in &adjacency[index] {
            indegree[*target] -= 1;
            if indegree[*target] == 0 {
                match ready.binary_search_by(|candidate| candidate.cmp(target).reverse()) {
                    Ok(_) => {}
                    Err(position) => ready.insert(position, *target),
                }
            }
        }
    }
    if visited != endpoints.len() {
        return Err(semantic_error(SemanticPlanErrorKind::ShareCycle));
    }
    Ok(())
}

struct CanonicalWriter {
    bytes: Vec<u8>,
}

impl CanonicalWriter {
    fn new() -> Self {
        Self {
            bytes: Vec::with_capacity(1_024),
        }
    }

    fn raw(&mut self, value: &str) -> Result<(), CoreError> {
        self.write_str(value)
            .map_err(|_| semantic_error(SemanticPlanErrorKind::CanonicalBytesTooLarge))
    }

    fn quoted(&mut self, value: &str) -> Result<(), CoreError> {
        debug_assert!(value.is_ascii());
        debug_assert!(!value.as_bytes().contains(&b'"'));
        debug_assert!(!value.as_bytes().contains(&b'\\'));
        self.raw("\"")?;
        self.raw(value)?;
        self.raw("\"")
    }

    fn unsigned(&mut self, value: u64) -> Result<(), CoreError> {
        write!(self, "{value}")
            .map_err(|_| semantic_error(SemanticPlanErrorKind::CanonicalBytesTooLarge))
    }

    fn signed(&mut self, value: i64) -> Result<(), CoreError> {
        write!(self, "{value}")
            .map_err(|_| semantic_error(SemanticPlanErrorKind::CanonicalBytesTooLarge))
    }

    fn finish(self) -> Vec<u8> {
        self.bytes
    }
}

impl Write for CanonicalWriter {
    fn write_str(&mut self, value: &str) -> fmt::Result {
        let length = self
            .bytes
            .len()
            .checked_add(value.len())
            .ok_or(fmt::Error)?;
        if length > MAX_CANONICAL_PLAN_BYTES {
            return Err(fmt::Error);
        }
        self.bytes.extend_from_slice(value.as_bytes());
        Ok(())
    }
}

fn write_node(
    writer: &mut CanonicalWriter,
    node: &PlanNode,
    clause: &ClauseSemantics,
) -> Result<(), CoreError> {
    writer.raw(r#"{"id":"#)?;
    writer.quoted(node.id().as_str())?;
    writer.raw(r#","intent":"#)?;
    writer.quoted(clause.intent.as_str())?;
    writer.raw(r#","capability":"#)?;
    writer.quoted(node.capability().as_str())?;
    writer.raw(r#","operation":"#)?;
    writer.quoted(node.operation().as_str())?;
    writer.raw(r#","polarity":"#)?;
    writer.quoted(polarity_tag(clause.polarity))?;
    writer.raw(r#","slots":["#)?;
    for (index, slot) in node.slots().iter().enumerate() {
        if index != 0 {
            writer.raw(",")?;
        }
        write_slot(writer, slot)?;
    }
    writer.raw(r#"],"evidence":["#)?;
    for (index, atom) in clause.evidence.iter().enumerate() {
        if index != 0 {
            writer.raw(",")?;
        }
        write_evidence_atom(writer, atom)?;
    }
    writer.raw("]}")
}

fn write_slot(writer: &mut CanonicalWriter, slot: &Slot) -> Result<(), CoreError> {
    writer.raw(r#"{"id":"#)?;
    writer.quoted(slot.id().as_str())?;
    writer.raw(r#","value":{"type":"#)?;
    match slot.value() {
        SlotValue::EvidenceText(span) => {
            writer.quoted("evidence_text")?;
            writer.raw(r#","span":"#)?;
            write_span(writer, span)?;
        }
        SlotValue::Integer(value) => {
            writer.quoted("integer")?;
            writer.raw(r#","value":"#)?;
            writer.signed(*value)?;
        }
        SlotValue::Boolean(value) => {
            writer.quoted("boolean")?;
            writer.raw(r#","value":"#)?;
            writer.raw(if *value { "true" } else { "false" })?;
        }
        SlotValue::Entity(entity) => {
            writer.quoted("entity")?;
            writer.raw(r#","id":"#)?;
            writer.quoted(entity.id().as_str())?;
            writer.raw(r#","generation":"#)?;
            writer.unsigned(entity.generation().get())?;
        }
    }
    writer.raw("}}")
}

fn write_evidence_atom(writer: &mut CanonicalWriter, atom: &EvidenceAtom) -> Result<(), CoreError> {
    writer.raw(r#"{"kind":"#)?;
    match &atom.kind {
        EvidenceKind::Predicate => {
            writer.quoted("predicate")?;
            writer.raw(r#","slot":null,"span":"#)?;
        }
        Argument(slot) => {
            writer.quoted("argument")?;
            writer.raw(r#","slot":"#)?;
            writer.quoted(slot.as_str())?;
            writer.raw(r#","span":"#)?;
        }
        EvidenceKind::Negation => {
            writer.quoted("negation")?;
            writer.raw(r#","slot":null,"span":"#)?;
        }
    }
    write_span(writer, &atom.span)?;
    writer.raw("}")
}

fn write_relation(
    writer: &mut CanonicalWriter,
    relation: &RelationEvidence,
) -> Result<(), CoreError> {
    writer.raw(r#"{"from":"#)?;
    writer.quoted(relation.relation.from().as_str())?;
    writer.raw(r#","to":"#)?;
    writer.quoted(relation.relation.to().as_str())?;
    writer.raw(r#","kind":"#)?;
    writer.quoted(relation_kind_tag(relation.relation.kind()))?;
    writer.raw(r#","evidence":["#)?;
    write_spans(writer, &relation.evidence)?;
    writer.raw("]}")
}

fn write_share(writer: &mut CanonicalWriter, share: &ArgumentShare) -> Result<(), CoreError> {
    writer.raw(r#"{"from":"#)?;
    write_endpoint(writer, &share.from)?;
    writer.raw(r#","to":"#)?;
    write_endpoint(writer, &share.to)?;
    writer.raw(r#","evidence":["#)?;
    write_spans(writer, &share.evidence)?;
    writer.raw("]}")
}

fn write_endpoint(
    writer: &mut CanonicalWriter,
    endpoint: &ArgumentEndpoint,
) -> Result<(), CoreError> {
    writer.raw(r#"{"node":"#)?;
    writer.quoted(endpoint.node.as_str())?;
    writer.raw(r#","slot":"#)?;
    writer.quoted(endpoint.slot.as_str())?;
    writer.raw("}")
}

fn write_spans(writer: &mut CanonicalWriter, spans: &[Utf8Span]) -> Result<(), CoreError> {
    for (index, span) in spans.iter().enumerate() {
        if index != 0 {
            writer.raw(",")?;
        }
        write_span(writer, span)?;
    }
    Ok(())
}

fn write_span(writer: &mut CanonicalWriter, span: &Utf8Span) -> Result<(), CoreError> {
    writer.raw(r#"{"start":"#)?;
    writer.unsigned(u64::from(span.start()))?;
    writer.raw(r#","end":"#)?;
    writer.unsigned(u64::from(span.end()))?;
    writer.raw("}")
}

const fn execution_class_tag(class: GraphExecutionClass) -> &'static str {
    match class {
        GraphExecutionClass::PartialSafe => "partial_safe",
        GraphExecutionClass::AtomicOnly => "atomic_only",
        GraphExecutionClass::NonExecutable => "non_executable",
    }
}

const fn polarity_tag(polarity: Polarity) -> &'static str {
    match polarity {
        Polarity::Affirmed => "affirmed",
        Polarity::Negated => "negated",
    }
}

const fn relation_kind_tag(kind: RelationKind) -> &'static str {
    match kind {
        RelationKind::Precedes => "precedes",
        RelationKind::Requires => "requires",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_writer_accepts_exact_limit_and_rejects_next_byte() {
        let prefix = "FIXTURE_TECNICA_";
        let exact = format!(
            "{prefix}{}",
            "A".repeat(MAX_CANONICAL_PLAN_BYTES - prefix.len())
        );
        let mut writer = CanonicalWriter::new();
        assert_eq!(writer.raw(&exact), Ok(()));
        assert_eq!(writer.bytes.len(), MAX_CANONICAL_PLAN_BYTES);
        assert_eq!(
            writer.raw("A"),
            Err(CoreError::SemanticPlan(
                SemanticPlanErrorKind::CanonicalBytesTooLarge
            ))
        );
    }
}
