use nlu_core::{EntityRef, SlotValue};

use crate::model::NodeDraft;

pub(crate) fn has_semantic_conflict(nodes: &[NodeDraft]) -> bool {
    for (index, left) in nodes.iter().enumerate() {
        for right in &nodes[index + 1..] {
            if !shares_entity(left, right) {
                continue;
            }
            if left.operation == right.operation && left.polarity != right.polarity {
                return true;
            }
            if opposing_operations(left.operation, right.operation) {
                return true;
            }
            if left.operation == "ha:set_position"
                && right.operation == "ha:set_position"
                && position(left)
                    .zip(position(right))
                    .is_some_and(|(left, right)| left != right)
            {
                return true;
            }
        }
    }
    false
}

fn shares_entity(left: &NodeDraft, right: &NodeDraft) -> bool {
    entities(left)
        .any(|left_entity| entities(right).any(|right_entity| left_entity == right_entity))
}

fn entities(node: &NodeDraft) -> impl Iterator<Item = &EntityRef> {
    node.slots.iter().filter_map(|slot| match &slot.value {
        SlotValue::Entity(entity) => Some(entity),
        SlotValue::EvidenceText(_) | SlotValue::Integer(_) | SlotValue::Boolean(_) => None,
    })
}

fn position(node: &NodeDraft) -> Option<i64> {
    node.slots
        .iter()
        .find(|slot| slot.slot_id == "ha:position")
        .and_then(|slot| match slot.value {
            SlotValue::Integer(value) => Some(value),
            SlotValue::EvidenceText(_) | SlotValue::Boolean(_) | SlotValue::Entity(_) => None,
        })
}

fn opposing_operations(left: &str, right: &str) -> bool {
    matches!(
        (left, right),
        ("ha:turn_on", "ha:turn_off")
            | ("ha:turn_off", "ha:turn_on")
            | ("ha:start_timer", "ha:cancel_timer")
            | ("ha:cancel_timer", "ha:start_timer")
    )
}

#[cfg(test)]
mod tests {
    use nlu_core::{CatalogGeneration, EntityId, EntityRef, RequestText, SlotValue};

    use super::*;
    use crate::model::{DraftPolarity, ResolvedBinding};

    fn fixture_node(
        source: &RequestText,
        operation: &'static str,
        polarity: DraftPolarity,
        entity: EntityRef,
        position: Option<i64>,
    ) -> NodeDraft {
        let span = source.span(0, 1).expect("FIXTURE_TECNICA span");
        let mut slots = vec![ResolvedBinding {
            slot_id: "ha:entity",
            value: SlotValue::Entity(entity),
            evidence: span.clone(),
        }];
        if let Some(value) = position {
            slots.push(ResolvedBinding {
                slot_id: "ha:position",
                value: SlotValue::Integer(value),
                evidence: span.clone(),
            });
        }
        NodeDraft {
            id: "fixture_tecnica:node",
            intent: "fixture_tecnica:intent",
            capability: "fixture_tecnica:capability",
            operation,
            polarity,
            predicate: span,
            negation: None,
            slots,
        }
    }

    fn entity(suffix: &str) -> EntityRef {
        EntityRef::new(
            EntityId::new(&format!("fixture_tecnica:{suffix}")).expect("FIXTURE_TECNICA ID"),
            CatalogGeneration::new(1).expect("FIXTURE_TECNICA generation"),
        )
    }

    #[test]
    fn rejects_required_closed_conflict_classes() {
        let source = RequestText::new("FIXTURE_TECNICA".into()).expect("FIXTURE_TECNICA source");
        let target = entity("target");

        let opposing_polarity = vec![
            fixture_node(
                &source,
                "ha:turn_on",
                DraftPolarity::Affirmed,
                target.clone(),
                None,
            ),
            fixture_node(
                &source,
                "ha:turn_on",
                DraftPolarity::Negated,
                target.clone(),
                None,
            ),
        ];
        assert!(has_semantic_conflict(&opposing_polarity));

        let opposing_operation = vec![
            fixture_node(
                &source,
                "ha:turn_on",
                DraftPolarity::Affirmed,
                target.clone(),
                None,
            ),
            fixture_node(
                &source,
                "ha:turn_off",
                DraftPolarity::Affirmed,
                target.clone(),
                None,
            ),
        ];
        assert!(has_semantic_conflict(&opposing_operation));

        let timer_conflict = vec![
            fixture_node(
                &source,
                "ha:start_timer",
                DraftPolarity::Affirmed,
                target.clone(),
                None,
            ),
            fixture_node(
                &source,
                "ha:cancel_timer",
                DraftPolarity::Affirmed,
                target.clone(),
                None,
            ),
        ];
        assert!(has_semantic_conflict(&timer_conflict));

        let position_conflict = vec![
            fixture_node(
                &source,
                "ha:set_position",
                DraftPolarity::Affirmed,
                target.clone(),
                Some(10),
            ),
            fixture_node(
                &source,
                "ha:set_position",
                DraftPolarity::Affirmed,
                target,
                Some(20),
            ),
        ];
        assert!(has_semantic_conflict(&position_conflict));
    }

    #[test]
    fn permits_distinct_targets() {
        let source = RequestText::new("FIXTURE_TECNICA".into()).expect("FIXTURE_TECNICA source");
        let nodes = vec![
            fixture_node(
                &source,
                "ha:set_position",
                DraftPolarity::Affirmed,
                entity("first"),
                Some(10),
            ),
            fixture_node(
                &source,
                "ha:set_position",
                DraftPolarity::Affirmed,
                entity("second"),
                Some(20),
            ),
        ];
        assert!(!has_semantic_conflict(&nodes));
    }
}
