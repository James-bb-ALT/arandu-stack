use std::{collections::BTreeMap, fmt::Write};

use nlu_data::sha256_hex;
use serde::Serialize;
use serde_json::{Map, Value};

use super::{
    frozen::{FrozenInputs, find_intent, find_shape, p02_row_index, projected_slot},
    templates::{self, RenderedOracle, RenderedParameter},
};
use crate::{
    Result,
    error::{invalid_dataset, invalid_projection, reconciliation_error, resource_limit},
    evaluator::EvaluationSplit,
    schema::{
        P02ExpectedSlot, P02Row, P11Row, SemanticArgumentShare, SemanticEvidence,
        SemanticIndependentPair, SemanticNode, SemanticPlan, SemanticRelation, SemanticSlot,
        SemanticSpan, SlotProjection,
    },
};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum GoldSource {
    P02,
    P11Negation,
}

impl GoldSource {
    pub(crate) const fn code(self) -> &'static str {
        match self {
            Self::P02 => "p02",
            Self::P11Negation => "p11_negation",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum GoldOutcome {
    Plan(SemanticPlan),
    Abstention(String),
}

impl GoldOutcome {
    pub(crate) const fn code(&self) -> &'static str {
        match self {
            Self::Plan(_) => "plan",
            Self::Abstention(_) => "abstention",
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct GoldCatalogEntity {
    pub(crate) registry_id: String,
    pub(crate) external_id: String,
    pub(crate) domain: String,
    pub(crate) mention: String,
    pub(crate) capabilities: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct GoldCase {
    pub(crate) case_id: String,
    pub(crate) source: GoldSource,
    pub(crate) stratum: String,
    pub(crate) graph_shape: String,
    pub(crate) utterance: String,
    pub(crate) expected_external_intent: String,
    pub(crate) expected_intent_id: String,
    pub(crate) catalog_entities: Vec<GoldCatalogEntity>,
    pub(crate) expected: GoldOutcome,
}

pub(super) fn project(frozen: FrozenInputs, split: EvaluationSplit) -> Result<Vec<GoldCase>> {
    let mut cases = Vec::with_capacity(frozen.p02_rows.len() + frozen.p11_rows.len());
    for row in &frozen.p02_rows {
        cases.push(project_p02(
            row,
            split,
            &frozen.p09_projection,
            &frozen.p11_projection,
        )?);
    }
    for row in &frozen.p11_rows {
        cases.push(project_p11(row, &frozen.p09_projection)?);
    }
    if cases.len() != 963 {
        return Err(reconciliation_error("projected case count"));
    }
    Ok(cases)
}

fn project_p02(
    row: &P02Row,
    split: EvaluationSplit,
    p09: &crate::schema::P09Projection,
    p11: &crate::schema::P11Projection,
) -> Result<GoldCase> {
    let intent_projection = find_intent(p09, &row.expected.intent)?;
    let shape_projection = find_shape(p11, &row.dimensions.graph_shape)?;
    if shape_projection
        .required_external_intent
        .as_deref()
        .is_some_and(|required| required != row.expected.intent)
    {
        return Err(invalid_projection("shape intent binding"));
    }
    let index = p02_row_index(row, split)?;
    let rendered = templates::render(split, &row.expected.intent, index)?;
    let mut catalog = BTreeMap::<String, GoldCatalogEntity>::new();
    let mut nodes = Vec::with_capacity(row.expected.nodes.len());

    for (node_index, expected_node) in row.expected.nodes.iter().enumerate() {
        let node_id = shape_projection
            .node_ids
            .get(node_index)
            .ok_or_else(|| invalid_projection("projected node ID"))?
            .clone();
        let predicate = predicate_for_node(
            split,
            &row.dimensions.graph_shape,
            node_index,
            &rendered,
            shape_projection,
        )?;
        let mut evidence = vec![SemanticEvidence {
            kind: "predicate".to_owned(),
            slot: None,
            begin_byte: predicate.begin_byte,
            end_byte: predicate.end_byte,
        }];
        let mut slots = Vec::with_capacity(expected_node.slots.len());
        for expected_slot in &expected_node.slots {
            let occurrence = if row.dimensions.graph_shape == "parallel_pair"
                && expected_slot.kind == "entity"
            {
                Some(
                    u16::try_from(node_index)
                        .map_err(|_| resource_limit("parallel slot occurrence"))?,
                )
            } else {
                None
            };
            let slot_projection = projected_slot(
                intent_projection,
                &expected_slot.id,
                &expected_slot.kind,
                occurrence,
            )?;
            let parameter = rendered
                .parameters
                .get(&slot_projection.parameter)
                .ok_or_else(|| invalid_projection("slot parameter rendering"))?;
            let value = project_slot_value(
                row,
                expected_node.capability.as_str(),
                expected_slot,
                slot_projection,
                parameter,
                &mut catalog,
            )?;
            slots.push(SemanticSlot {
                id: expected_slot.id.clone(),
                kind: expected_slot.kind.clone(),
                value,
            });
            let is_shared_destination = row.dimensions.graph_shape == "ordered_pair"
                && node_index == 1
                && expected_slot.id == "ha:timer";
            if !is_shared_destination {
                evidence.push(SemanticEvidence {
                    kind: "argument".to_owned(),
                    slot: Some(expected_slot.id.clone()),
                    begin_byte: parameter.begin_byte,
                    end_byte: parameter.end_byte,
                });
            }
        }
        slots.sort_by(|left, right| left.id.cmp(&right.id));
        sort_evidence(&mut evidence);
        nodes.push(SemanticNode {
            id: node_id,
            intent: if row.dimensions.graph_shape == "ordered_pair" && node_index == 1 {
                "ha:hass_timer_status".to_owned()
            } else {
                intent_projection.intent_id.clone()
            },
            capability: expected_node.capability.clone(),
            operation: expected_node.operation.clone(),
            polarity: "affirmed".to_owned(),
            slots,
            evidence,
        });
    }
    nodes.sort_by(|left, right| left.id.cmp(&right.id));

    let relations = projected_relations(split, row, &rendered, shape_projection)?;
    let independent_pairs = shape_projection
        .independent_pairs
        .iter()
        .map(|pair| SemanticIndependentPair {
            left: pair.left.clone(),
            right: pair.right.clone(),
        })
        .collect();
    let argument_shares = projected_shares(row, &rendered, shape_projection, intent_projection)?;
    let plan = SemanticPlan {
        schema_version: p11.semantic_plan_schema.clone(),
        catalog_generation: p11.catalog_projection.generation,
        execution_class: shape_projection.execution_class.clone(),
        nodes,
        relations,
        independent_pairs,
        argument_shares,
    };
    validate_semantic_plan_order(&plan)?;

    Ok(GoldCase {
        case_id: row.case_id.clone(),
        source: GoldSource::P02,
        stratum: row.dimensions.graph_shape.clone(),
        graph_shape: row.dimensions.graph_shape.clone(),
        utterance: row.utterance.clone(),
        expected_external_intent: row.expected.intent.clone(),
        expected_intent_id: intent_projection.intent_id.clone(),
        catalog_entities: catalog.into_values().collect(),
        expected: GoldOutcome::Plan(plan),
    })
}

fn predicate_for_node(
    split: EvaluationSplit,
    shape: &str,
    node_index: usize,
    rendered: &RenderedOracle,
    projection: &crate::schema::GraphShapeProjection,
) -> Result<RenderedParameter> {
    match (shape, node_index) {
        ("single" | "parallel_pair" | "ordered_pair", 0) | ("parallel_pair", 1) => {
            Ok(rendered.initial_predicate.clone())
        }
        ("ordered_pair", 1) => {
            let cues = projection
                .secondary_predicate_cues
                .as_ref()
                .ok_or_else(|| invalid_projection("secondary predicate cues"))?;
            let cue = match split {
                EvaluationSplit::Train => &cues.train,
                EvaluationSplit::Development => &cues.development,
            };
            exact_span(&rendered.utterance, cue)
        }
        _ => Err(invalid_projection("predicate node shape")),
    }
}

fn project_slot_value(
    row: &P02Row,
    capability: &str,
    expected: &P02ExpectedSlot,
    projection: &SlotProjection,
    parameter: &RenderedParameter,
    catalog: &mut BTreeMap<String, GoldCatalogEntity>,
) -> Result<Value> {
    match expected.kind.as_str() {
        "entity" => {
            let external_id = expected
                .value
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| invalid_dataset("P02 external entity ID"))?;
            let generation = expected
                .value
                .get("catalog_generation")
                .and_then(Value::as_u64);
            if generation != Some(1) {
                return Err(invalid_dataset("P02 entity generation"));
            }
            let registry_id = projected_registry_id(external_id)?;
            let core_id = format!("ha_entity:id_{registry_id}");
            let entry =
                catalog
                    .entry(external_id.to_owned())
                    .or_insert_with(|| GoldCatalogEntity {
                        registry_id: registry_id.clone(),
                        external_id: external_id.to_owned(),
                        domain: row.dimensions.domain.clone(),
                        mention: parameter.text.clone(),
                        capabilities: Vec::new(),
                    });
            if entry.registry_id != registry_id
                || entry.domain != row.dimensions.domain
                || entry.mention != parameter.text
            {
                return Err(reconciliation_error("entity projection collision"));
            }
            if !entry
                .capabilities
                .iter()
                .any(|candidate| candidate == capability)
            {
                entry.capabilities.push(capability.to_owned());
                entry.capabilities.sort();
            }
            let mut value = Map::new();
            value.insert("id".to_owned(), Value::String(core_id));
            value.insert("catalog_generation".to_owned(), Value::from(1_u64));
            Ok(Value::Object(value))
        }
        "integer" => {
            let rendered = transformed_integer(projection, parameter)?;
            if expected.value.as_i64() != Some(rendered) {
                return Err(invalid_dataset("P02 integer projection mismatch"));
            }
            Ok(Value::from(rendered))
        }
        "text" => {
            if expected.value.as_str().is_none() {
                return Err(invalid_dataset("P02 text semantic value"));
            }
            Ok(Value::String(parameter.text.clone()))
        }
        _ => Err(invalid_dataset("P02 projected slot kind")),
    }
}

fn transformed_integer(projection: &SlotProjection, parameter: &RenderedParameter) -> Result<i64> {
    let value = parameter
        .text
        .parse::<i64>()
        .map_err(|_| invalid_projection("integer parameter"))?;
    match projection.transform.as_str() {
        "integer" => Ok(value),
        "minutes_to_seconds" => value
            .checked_mul(60)
            .ok_or_else(|| resource_limit("minutes transform")),
        _ => Err(invalid_projection("integer transform")),
    }
}

fn projected_registry_id(external_id: &str) -> Result<String> {
    let mut seed = b"p11-catalog-v1\0".to_vec();
    seed.extend_from_slice(external_id.as_bytes());
    let digest = sha256_hex(&seed).map_err(|_| reconciliation_error("catalog registry digest"))?;
    digest
        .get(..32)
        .map(ToOwned::to_owned)
        .ok_or_else(|| reconciliation_error("catalog registry digest width"))
}

fn projected_relations(
    split: EvaluationSplit,
    row: &P02Row,
    rendered: &RenderedOracle,
    projection: &crate::schema::GraphShapeProjection,
) -> Result<Vec<SemanticRelation>> {
    if projection.relations.is_empty() {
        if !row.expected.relations.is_empty() {
            return Err(reconciliation_error("unexpected source relation"));
        }
        return Ok(Vec::new());
    }
    if projection.relations.len() != row.expected.relations.len() {
        return Err(reconciliation_error("relation projection count"));
    }
    let cues = projection
        .relation_cues
        .as_ref()
        .ok_or_else(|| invalid_projection("relation cues"))?;
    let cue = match split {
        EvaluationSplit::Train => &cues.train,
        EvaluationSplit::Development => &cues.development,
    };
    let span = exact_span(&rendered.utterance, cue)?;
    let mut relations = Vec::with_capacity(projection.relations.len());
    for projected in &projection.relations {
        let source = row
            .expected
            .relations
            .first()
            .ok_or_else(|| reconciliation_error("source relation"))?;
        if source.kind != projected.kind {
            return Err(reconciliation_error("relation kind projection"));
        }
        relations.push(SemanticRelation {
            from: projected.from.clone(),
            to: projected.to.clone(),
            kind: projected.kind.clone(),
            evidence: vec![SemanticSpan {
                begin_byte: span.begin_byte,
                end_byte: span.end_byte,
            }],
        });
    }
    relations.sort_by(|left, right| {
        (&left.from, &left.to, &left.kind).cmp(&(&right.from, &right.to, &right.kind))
    });
    Ok(relations)
}

fn projected_shares(
    row: &P02Row,
    rendered: &RenderedOracle,
    projection: &crate::schema::GraphShapeProjection,
    intent: &crate::schema::IntentProjection,
) -> Result<Vec<SemanticArgumentShare>> {
    let mut shares = Vec::with_capacity(projection.argument_shares.len());
    for projected in &projection.argument_shares {
        if projected.evidence != "source_argument" {
            return Err(invalid_projection("share evidence projection"));
        }
        let slot_projection = projected_slot(intent, &projected.from_slot, "entity", Some(0))?;
        let parameter = rendered
            .parameters
            .get(&slot_projection.parameter)
            .ok_or_else(|| invalid_projection("share parameter"))?;
        let source_values = row
            .expected
            .nodes
            .iter()
            .flat_map(|node| node.slots.iter())
            .filter(|slot| slot.id == projected.from_slot && slot.kind == "entity")
            .map(|slot| &slot.value)
            .collect::<Vec<_>>();
        if source_values.len() != 2 || source_values[0] != source_values[1] {
            return Err(reconciliation_error("shared source value"));
        }
        shares.push(SemanticArgumentShare {
            from_node: projected.from_node.clone(),
            from_slot: projected.from_slot.clone(),
            to_node: projected.to_node.clone(),
            to_slot: projected.to_slot.clone(),
            evidence: vec![SemanticSpan {
                begin_byte: parameter.begin_byte,
                end_byte: parameter.end_byte,
            }],
        });
    }
    shares.sort_by(|left, right| {
        (
            &left.from_node,
            &left.from_slot,
            &left.to_node,
            &left.to_slot,
        )
            .cmp(&(
                &right.from_node,
                &right.from_slot,
                &right.to_node,
                &right.to_slot,
            ))
    });
    Ok(shares)
}

fn project_p11(row: &P11Row, p09: &crate::schema::P09Projection) -> Result<GoldCase> {
    let intent_projection = find_intent(p09, "HassTurnOn")?;
    let expected = match (&row.expected.plan, &row.expected.reason) {
        (Some(plan), None) => {
            let mut normalized = plan.clone();
            for node in &mut normalized.nodes {
                if node.intent != "HassTurnOn" {
                    return Err(invalid_dataset("P11 node intent projection"));
                }
                node.intent.clone_from(&intent_projection.intent_id);
            }
            GoldOutcome::Plan(normalized)
        }
        (None, Some(reason)) => GoldOutcome::Abstention(reason.clone()),
        _ => return Err(invalid_dataset("P11 expected outcome")),
    };
    let mut catalog_entities = row
        .context
        .entities
        .iter()
        .map(|entity| GoldCatalogEntity {
            registry_id: entity.registry_id.clone(),
            external_id: format!("light.p11_{}", entity.registry_id),
            domain: "light".to_owned(),
            mention: entity.mention.clone(),
            capabilities: vec!["ha:light_control".to_owned()],
        })
        .collect::<Vec<_>>();
    catalog_entities.sort();
    Ok(GoldCase {
        case_id: row.case_id.clone(),
        source: GoldSource::P11Negation,
        stratum: row.stratum.clone(),
        graph_shape: if matches!(expected, GoldOutcome::Plan(_)) {
            "negated_parallel_pair".to_owned()
        } else {
            "negation_scope_abstention".to_owned()
        },
        utterance: row.utterance.clone(),
        expected_external_intent: "HassTurnOn".to_owned(),
        expected_intent_id: intent_projection.intent_id.clone(),
        catalog_entities,
        expected,
    })
}

fn exact_span(source: &str, needle: &str) -> Result<RenderedParameter> {
    let mut matches = source.match_indices(needle);
    let (begin, matched) = matches
        .next()
        .ok_or_else(|| invalid_dataset("projected cue missing"))?;
    if matches.next().is_some() {
        return Err(invalid_dataset("projected cue not unique"));
    }
    let end = begin
        .checked_add(matched.len())
        .ok_or_else(|| resource_limit("projected cue end"))?;
    Ok(RenderedParameter {
        text: matched.to_owned(),
        begin_byte: u32::try_from(begin).map_err(|_| resource_limit("cue begin"))?,
        end_byte: u32::try_from(end).map_err(|_| resource_limit("cue end"))?,
    })
}

fn sort_evidence(evidence: &mut [SemanticEvidence]) {
    evidence.sort_by(|left, right| {
        (
            evidence_rank(&left.kind),
            left.slot.as_deref(),
            left.begin_byte,
            left.end_byte,
        )
            .cmp(&(
                evidence_rank(&right.kind),
                right.slot.as_deref(),
                right.begin_byte,
                right.end_byte,
            ))
    });
}

fn evidence_rank(kind: &str) -> u8 {
    match kind {
        "predicate" => 0,
        "argument" => 1,
        "negation" => 2,
        _ => u8::MAX,
    }
}

fn validate_semantic_plan_order(plan: &SemanticPlan) -> Result<()> {
    if !is_sorted_by(&plan.nodes, |left, right| left.id <= right.id)
        || plan.nodes.iter().any(|node| {
            !is_sorted_by(&node.slots, |left, right| left.id <= right.id)
                || !is_sorted_by(&node.evidence, |left, right| {
                    (
                        evidence_rank(&left.kind),
                        left.slot.as_deref(),
                        left.begin_byte,
                        left.end_byte,
                    ) <= (
                        evidence_rank(&right.kind),
                        right.slot.as_deref(),
                        right.begin_byte,
                        right.end_byte,
                    )
                })
        })
    {
        return Err(reconciliation_error("gold canonical ordering"));
    }
    Ok(())
}

fn is_sorted_by<T>(values: &[T], predicate: impl Fn(&T, &T) -> bool) -> bool {
    values.windows(2).all(|pair| predicate(&pair[0], &pair[1]))
}

pub(crate) fn core_canonical_bytes(plan: &SemanticPlan) -> Result<Vec<u8>> {
    let mut output = String::with_capacity(1_024);
    write!(
        output,
        "{{\"schema_version\":\"{}\",\"catalog_generation\":{},\"execution_class\":\"{}\",\"nodes\":[",
        plan.schema_version, plan.catalog_generation, plan.execution_class
    )
    .map_err(|_| reconciliation_error("gold canonical header"))?;
    for (node_index, node) in plan.nodes.iter().enumerate() {
        if node_index != 0 {
            output.push(',');
        }
        write!(
            output,
            "{{\"id\":\"{}\",\"intent\":\"{}\",\"capability\":\"{}\",\"operation\":\"{}\",\"polarity\":\"{}\",\"slots\":[",
            node.id, node.intent, node.capability, node.operation, node.polarity
        )
        .map_err(|_| reconciliation_error("gold canonical node"))?;
        for (slot_index, slot) in node.slots.iter().enumerate() {
            if slot_index != 0 {
                output.push(',');
            }
            write!(output, "{{\"id\":\"{}\",\"value\":{{", slot.id)
                .map_err(|_| reconciliation_error("gold canonical slot"))?;
            match slot.kind.as_str() {
                "entity" => {
                    let id = slot
                        .value
                        .get("id")
                        .and_then(Value::as_str)
                        .ok_or_else(|| reconciliation_error("gold canonical entity ID"))?;
                    let generation = slot
                        .value
                        .get("catalog_generation")
                        .and_then(Value::as_u64)
                        .ok_or_else(|| reconciliation_error("gold canonical entity generation"))?;
                    write!(
                        output,
                        "\"type\":\"entity\",\"id\":\"{id}\",\"generation\":{generation}"
                    )
                    .map_err(|_| reconciliation_error("gold canonical entity"))?;
                }
                "integer" => {
                    let value = slot
                        .value
                        .as_i64()
                        .ok_or_else(|| reconciliation_error("gold canonical integer"))?;
                    write!(output, "\"type\":\"integer\",\"value\":{value}")
                        .map_err(|_| reconciliation_error("gold canonical integer"))?;
                }
                "text" => {
                    let (begin_byte, end_byte) = direct_argument_span(node, &slot.id)?;
                    write!(
                        output,
                        "\"type\":\"evidence_text\",\"span\":{{\"start\":{},\"end\":{}}}",
                        begin_byte, end_byte
                    )
                    .map_err(|_| reconciliation_error("gold canonical text"))?;
                }
                _ => return Err(reconciliation_error("gold canonical slot kind")),
            }
            output.push_str("}}");
        }
        output.push_str("],\"evidence\":[");
        for (evidence_index, atom) in node.evidence.iter().enumerate() {
            if evidence_index != 0 {
                output.push(',');
            }
            write!(output, "{{\"kind\":\"{}\",\"slot\":", atom.kind)
                .map_err(|_| reconciliation_error("gold canonical evidence"))?;
            if let Some(slot) = &atom.slot {
                write!(output, "\"{slot}\"")
                    .map_err(|_| reconciliation_error("gold canonical evidence slot"))?;
            } else {
                output.push_str("null");
            }
            write!(
                output,
                ",\"span\":{{\"start\":{},\"end\":{}}}}}",
                atom.begin_byte, atom.end_byte
            )
            .map_err(|_| reconciliation_error("gold canonical evidence span"))?;
        }
        output.push_str("]}");
    }
    output.push_str("],\"relations\":[");
    for (index, relation) in plan.relations.iter().enumerate() {
        if index != 0 {
            output.push(',');
        }
        write!(
            output,
            "{{\"from\":\"{}\",\"to\":\"{}\",\"kind\":\"{}\",\"evidence\":[",
            relation.from, relation.to, relation.kind
        )
        .map_err(|_| reconciliation_error("gold canonical relation"))?;
        write_spans(&mut output, &relation.evidence)?;
        output.push_str("]}");
    }
    output.push_str("],\"independent_pairs\":[");
    for (index, pair) in plan.independent_pairs.iter().enumerate() {
        if index != 0 {
            output.push(',');
        }
        write!(
            output,
            "{{\"left\":\"{}\",\"right\":\"{}\"}}",
            pair.left, pair.right
        )
        .map_err(|_| reconciliation_error("gold canonical independent pair"))?;
    }
    output.push_str("],\"argument_shares\":[");
    for (index, share) in plan.argument_shares.iter().enumerate() {
        if index != 0 {
            output.push(',');
        }
        write!(
            output,
            "{{\"from\":{{\"node\":\"{}\",\"slot\":\"{}\"}},\"to\":{{\"node\":\"{}\",\"slot\":\"{}\"}},\"evidence\":[",
            share.from_node, share.from_slot, share.to_node, share.to_slot
        )
        .map_err(|_| reconciliation_error("gold canonical share"))?;
        write_spans(&mut output, &share.evidence)?;
        output.push_str("]}");
    }
    output.push_str("]}\n");
    if output.len() > nlu_core::MAX_CANONICAL_PLAN_BYTES {
        return Err(resource_limit("gold canonical plan bytes"));
    }
    Ok(output.into_bytes())
}

fn direct_argument_span(node: &SemanticNode, slot_id: &str) -> Result<(u32, u32)> {
    let mut matches = node
        .evidence
        .iter()
        .filter(|atom| atom.kind == "argument" && atom.slot.as_deref() == Some(slot_id));
    let first = matches
        .next()
        .ok_or_else(|| reconciliation_error("text slot argument evidence"))?;
    if matches.next().is_some() {
        return Err(reconciliation_error(
            "duplicate text slot argument evidence",
        ));
    }
    Ok((first.begin_byte, first.end_byte))
}

fn write_spans(output: &mut String, spans: &[SemanticSpan]) -> Result<()> {
    for (index, span) in spans.iter().enumerate() {
        if index != 0 {
            output.push(',');
        }
        write!(
            output,
            "{{\"start\":{},\"end\":{}}}",
            span.begin_byte, span.end_byte
        )
        .map_err(|_| reconciliation_error("gold canonical span"))?;
    }
    Ok(())
}

pub(crate) fn source_counts(cases: &[GoldCase]) -> BTreeMap<GoldSource, usize> {
    let mut counts = BTreeMap::new();
    for case in cases {
        *counts.entry(case.source).or_default() += 1;
    }
    counts
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_projection_is_stable_and_namespace_separated() {
        assert_eq!(
            projected_registry_id("light.train_hass_turn_on_001").expect("registry"),
            "253c94199036578ca36b2e6eb786e372"
        );
        assert_ne!(
            projected_registry_id("light.train_hass_turn_on_001").expect("first"),
            projected_registry_id("light.train_hass_turn_on_002").expect("second")
        );
    }
}
