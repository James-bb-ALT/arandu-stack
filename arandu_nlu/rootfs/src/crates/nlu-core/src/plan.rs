use crate::{
    CapabilityId, CatalogGeneration, CollectionKind, CoreError, DuplicateKind, MAX_AGGREGATE_ITEMS,
    MAX_EVIDENCE_SPANS, MAX_PLAN_NODES, MAX_RELATIONS, MAX_SLOTS_PER_NODE, NodeId, OperationId,
    RequestText, Slot, SlotValue, Utf8Span,
};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum RelationKind {
    Precedes,
    Requires,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Relation {
    from: NodeId,
    to: NodeId,
    kind: RelationKind,
}

impl Relation {
    #[must_use]
    pub const fn new(from: NodeId, to: NodeId, kind: RelationKind) -> Self {
        Self { from, to, kind }
    }

    #[must_use]
    pub const fn from(&self) -> &NodeId {
        &self.from
    }

    #[must_use]
    pub const fn to(&self) -> &NodeId {
        &self.to
    }

    #[must_use]
    pub const fn kind(&self) -> RelationKind {
        self.kind
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PlanNode {
    id: NodeId,
    capability: CapabilityId,
    operation: OperationId,
    slots: Vec<Slot>,
    evidence: Vec<Utf8Span>,
}

impl PlanNode {
    pub fn new(
        id: NodeId,
        capability: CapabilityId,
        operation: OperationId,
        mut slots: Vec<Slot>,
        mut evidence: Vec<Utf8Span>,
    ) -> Result<Self, CoreError> {
        if slots.len() > MAX_SLOTS_PER_NODE {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::Slots,
                limit: MAX_SLOTS_PER_NODE as u16,
            });
        }
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

        slots.sort_by(|left, right| left.id().cmp(right.id()));
        if slots.windows(2).any(|pair| pair[0].id() == pair[1].id()) {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::Slot,
            });
        }

        evidence.sort();
        if evidence.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::Evidence,
            });
        }

        Ok(Self {
            id,
            capability,
            operation,
            slots,
            evidence,
        })
    }

    #[must_use]
    pub const fn id(&self) -> &NodeId {
        &self.id
    }

    #[must_use]
    pub const fn capability(&self) -> &CapabilityId {
        &self.capability
    }

    #[must_use]
    pub const fn operation(&self) -> &OperationId {
        &self.operation
    }

    #[must_use]
    pub fn slots(&self) -> &[Slot] {
        &self.slots
    }

    #[must_use]
    pub fn evidence(&self) -> &[Utf8Span] {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Plan {
    catalog_generation: CatalogGeneration,
    nodes: Vec<PlanNode>,
    relations: Vec<Relation>,
}

impl Plan {
    pub fn new(
        source: &RequestText,
        catalog_generation: CatalogGeneration,
        mut nodes: Vec<PlanNode>,
        mut relations: Vec<Relation>,
    ) -> Result<Self, CoreError> {
        Self::validate_counts(&nodes, &relations)?;

        nodes.sort_by(|left, right| left.id.cmp(&right.id));
        if nodes.windows(2).any(|pair| pair[0].id == pair[1].id) {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::PlanNode,
            });
        }

        Self::validate_node_values(source, catalog_generation, &nodes)?;

        relations.sort();
        if relations.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::Relation,
            });
        }
        Self::validate_relations(&nodes, &relations)?;

        Ok(Self {
            catalog_generation,
            nodes,
            relations,
        })
    }

    fn validate_counts(nodes: &[PlanNode], relations: &[Relation]) -> Result<(), CoreError> {
        if nodes.is_empty() {
            return Err(CoreError::EmptyCollection {
                kind: CollectionKind::PlanNodes,
            });
        }
        if nodes.len() > MAX_PLAN_NODES {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::PlanNodes,
                limit: MAX_PLAN_NODES as u16,
            });
        }
        if relations.len() > MAX_RELATIONS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::Relations,
                limit: MAX_RELATIONS as u16,
            });
        }

        let aggregate = nodes
            .len()
            .saturating_add(relations.len())
            .saturating_add(nodes.iter().map(|node| node.slots.len()).sum::<usize>())
            .saturating_add(nodes.iter().map(|node| node.evidence.len()).sum::<usize>());
        if aggregate > MAX_AGGREGATE_ITEMS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::AggregateItems,
                limit: MAX_AGGREGATE_ITEMS as u16,
            });
        }
        Ok(())
    }

    fn validate_node_values(
        source: &RequestText,
        catalog_generation: CatalogGeneration,
        nodes: &[PlanNode],
    ) -> Result<(), CoreError> {
        for node in nodes {
            if node.evidence.iter().any(|span| !span.belongs_to(source)) {
                return Err(CoreError::SpanSourceMismatch);
            }
            for slot in &node.slots {
                match slot.value() {
                    SlotValue::EvidenceText(span) if !span.belongs_to(source) => {
                        return Err(CoreError::SpanSourceMismatch);
                    }
                    SlotValue::Entity(entity) if entity.generation() != catalog_generation => {
                        return Err(CoreError::StaleCatalogGeneration);
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    fn validate_relations(nodes: &[PlanNode], relations: &[Relation]) -> Result<(), CoreError> {
        let node_index = |id: &NodeId| nodes.binary_search_by(|node| node.id.cmp(id)).ok();
        let mut adjacency = vec![Vec::new(); nodes.len()];
        let mut indegree = vec![0_usize; nodes.len()];

        for relation in relations {
            if relation.from == relation.to {
                return Err(CoreError::SelfRelation);
            }
            let from = node_index(&relation.from).ok_or(CoreError::DanglingRelation)?;
            let to = node_index(&relation.to).ok_or(CoreError::DanglingRelation)?;
            adjacency[from].push(to);
            indegree[to] += 1;
        }

        for targets in &mut adjacency {
            targets.sort_unstable();
        }

        let mut ready: Vec<usize> = indegree
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

        if visited != nodes.len() {
            return Err(CoreError::RelationCycle);
        }
        Ok(())
    }

    #[must_use]
    pub const fn catalog_generation(&self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub fn nodes(&self) -> &[PlanNode] {
        &self.nodes
    }

    #[must_use]
    pub fn relations(&self) -> &[Relation] {
        &self.relations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{EntityId, EntityRef, SlotId};

    fn fixture_node(source: &RequestText, suffix: &str) -> PlanNode {
        PlanNode::new(
            NodeId::new(&format!("fixture_tecnica:node_{suffix}")).expect("node ID"),
            CapabilityId::new("fixture_tecnica:capability").expect("capability ID"),
            OperationId::new("fixture_tecnica:operation").expect("operation ID"),
            Vec::new(),
            vec![source.span(0, 1).expect("span")],
        )
        .expect("node")
    }

    #[test]
    fn canonicalizes_node_relation_and_slot_order() {
        let source = RequestText::new("FIXTURE_TECNICA_AB".into()).expect("source");
        let generation = CatalogGeneration::new(7).expect("generation");
        let mut first = fixture_node(&source, "a");
        first.slots = vec![
            Slot::new(
                SlotId::new("fixture_tecnica:slot_b").expect("slot ID"),
                SlotValue::Boolean(true),
            ),
            Slot::new(
                SlotId::new("fixture_tecnica:slot_a").expect("slot ID"),
                SlotValue::Integer(1),
            ),
        ];
        first.slots.sort_by(|left, right| left.id().cmp(right.id()));
        let second = fixture_node(&source, "b");
        let relation = Relation::new(
            first.id().clone(),
            second.id().clone(),
            RelationKind::Precedes,
        );
        let plan =
            Plan::new(&source, generation, vec![second, first], vec![relation]).expect("plan");
        assert_eq!(plan.nodes()[0].id().as_str(), "fixture_tecnica:node_a");
        assert_eq!(
            plan.nodes()[0].slots()[0].id().as_str(),
            "fixture_tecnica:slot_a"
        );
    }

    #[test]
    fn rejects_empty_duplicate_dangling_self_and_cyclic_graphs() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("source");
        let generation = CatalogGeneration::new(1).expect("generation");
        assert_eq!(
            Plan::new(&source, generation, Vec::new(), Vec::new()),
            Err(CoreError::EmptyCollection {
                kind: CollectionKind::PlanNodes
            })
        );

        let first = fixture_node(&source, "a");
        assert_eq!(
            Plan::new(
                &source,
                generation,
                vec![first.clone(), first.clone()],
                Vec::new()
            ),
            Err(CoreError::Duplicate {
                kind: DuplicateKind::PlanNode
            })
        );

        let missing = NodeId::new("fixture_tecnica:missing").expect("ID");
        assert_eq!(
            Plan::new(
                &source,
                generation,
                vec![first.clone()],
                vec![Relation::new(
                    first.id().clone(),
                    missing,
                    RelationKind::Requires
                )]
            ),
            Err(CoreError::DanglingRelation)
        );
        assert_eq!(
            Plan::new(
                &source,
                generation,
                vec![first.clone()],
                vec![Relation::new(
                    first.id().clone(),
                    first.id().clone(),
                    RelationKind::Requires
                )]
            ),
            Err(CoreError::SelfRelation)
        );

        let second = fixture_node(&source, "b");
        let forward = Relation::new(
            first.id().clone(),
            second.id().clone(),
            RelationKind::Precedes,
        );
        let backward = Relation::new(
            second.id().clone(),
            first.id().clone(),
            RelationKind::Requires,
        );
        assert_eq!(
            Plan::new(
                &source,
                generation,
                vec![first, second],
                vec![forward, backward]
            ),
            Err(CoreError::RelationCycle)
        );
    }

    #[test]
    fn rejects_foreign_spans_and_stale_entities() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("source");
        let foreign = RequestText::new("FIXTURE_TECNICA_A".into()).expect("source");
        let generation = CatalogGeneration::new(2).expect("generation");
        let stale_generation = CatalogGeneration::new(1).expect("generation");

        let foreign_node = fixture_node(&foreign, "foreign");
        assert_eq!(
            Plan::new(&source, generation, vec![foreign_node], Vec::new()),
            Err(CoreError::SpanSourceMismatch)
        );

        let stale_node = PlanNode::new(
            NodeId::new("fixture_tecnica:node").expect("ID"),
            CapabilityId::new("fixture_tecnica:capability").expect("ID"),
            OperationId::new("fixture_tecnica:operation").expect("ID"),
            vec![Slot::new(
                SlotId::new("fixture_tecnica:entity_slot").expect("ID"),
                SlotValue::Entity(EntityRef::new(
                    EntityId::new("fixture_tecnica:entity").expect("ID"),
                    stale_generation,
                )),
            )],
            vec![source.span(0, 1).expect("span")],
        )
        .expect("node");
        assert_eq!(
            Plan::new(&source, generation, vec![stale_node], Vec::new()),
            Err(CoreError::StaleCatalogGeneration)
        );
    }

    #[test]
    fn accepts_distinct_typed_relations_between_the_same_nodes() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("source");
        let generation = CatalogGeneration::new(1).expect("generation");
        let first = fixture_node(&source, "a");
        let second = fixture_node(&source, "b");
        let relations = vec![
            Relation::new(
                first.id().clone(),
                second.id().clone(),
                RelationKind::Precedes,
            ),
            Relation::new(
                first.id().clone(),
                second.id().clone(),
                RelationKind::Requires,
            ),
        ];
        let plan =
            Plan::new(&source, generation, vec![second, first], relations).expect("valid DAG");
        assert_eq!(plan.relations().len(), 2);
    }
}
