use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

use nlu_core::{CapabilityId, IntentId, NodeId, OperationId, RequestText, SlotId};
use nlu_data::{DataError, DataErrorCode, parse_strict_json, read_bounded_root_file, sha256_hex};
use serde::de::DeserializeOwned;
use serde_json::Value;

use super::templates;
use crate::{
    PlanEvaluationError, PlanEvaluationErrorCode, Result,
    error::{invalid_dataset, invalid_projection, resource_limit},
    evaluator::EvaluationSplit,
    schema::{
        GraphShapeProjection, IntentProjection, P02ExpectedPlan, P02ExpectedSlot, P02Row,
        P09Projection, P11Projection, P11Row, SemanticPlan, SlotProjection,
    },
};

pub(super) const P09_PROJECTION_PATH: &str = "data/evaluation/p09/intent-v1/projection.json";
pub(super) const P09_PROJECTION_SHA256: &str =
    "5f661bb96b85667a1d0c9ec4d85cb61c443e1b24ab4dfc6c1ab849c6df936ee4";
pub(super) const P11_PROJECTION_PATH: &str = "data/evaluation/p11/plan-v1/projection.json";
pub(super) const P11_PROJECTION_SHA256: &str =
    "581b852b8cd2ec24b4c02b8c68914b27a928d953470a10665357e50425783f75";
pub(super) const P02_RECORDS: usize = 960;
pub(super) const P11_RECORDS: usize = 3;

const P02_SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const P02_CORPUS_VERSION: &str = "1.0.0";
const P02_GENERATOR_ID: &str = "p02-generator-v1";
const P02_ORACLE_ORIGIN: &str = "pre_engine_generator_specification";
const P02_SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
const P02_GENERATOR_SHA256: &str =
    "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
const P11_SOURCE_ID: &str = "project-authored-synthetic-ptbr-p11-negation-v1";
const P11_GENERATOR_ID: &str = "p11-negation-generator-v1";
const P11_ORACLE_ORIGIN: &str = "pre_p11_composer_generator_specification";
const SOURCE_LICENSE: &str = "Apache-2.0";
const LOCALE: &str = "pt-BR";
const CLAIM_SCOPE: &str = "internal_conformance_only";
const MAX_P02_BYTES: usize = 2 * 1024 * 1024;
const MAX_P11_BYTES: usize = 16 * 1024;
const MAX_PROJECTION_BYTES: usize = 64 * 1024;
const MAX_P02_ROW_BYTES: usize = 8 * 1024;
const MAX_P11_ROW_BYTES: usize = 8 * 1024;

pub(super) struct FrozenInputs {
    pub(super) p09_projection: P09Projection,
    pub(super) p11_projection: P11Projection,
    pub(super) p02_rows: Vec<P02Row>,
    pub(super) p11_rows: Vec<P11Row>,
}

pub(super) fn load(root: &Path, split: EvaluationSplit) -> Result<FrozenInputs> {
    let p09_bytes = read_verified(
        root,
        P09_PROJECTION_PATH,
        MAX_PROJECTION_BYTES,
        P09_PROJECTION_SHA256,
        PlanEvaluationErrorCode::InvalidProjection,
        "P09 projection",
    )?;
    let p09_projection: P09Projection =
        parse_json(&p09_bytes, "P09 projection", invalid_projection)?;
    validate_p09_projection(&p09_projection)?;

    let p11_bytes = read_verified(
        root,
        P11_PROJECTION_PATH,
        MAX_PROJECTION_BYTES,
        P11_PROJECTION_SHA256,
        PlanEvaluationErrorCode::InvalidProjection,
        "P11 projection",
    )?;
    let p11_projection: P11Projection =
        parse_json(&p11_bytes, "P11 projection", invalid_projection)?;
    validate_p11_projection(&p11_projection)?;

    let p02_bytes = read_verified(
        root,
        split.p02_path(),
        MAX_P02_BYTES,
        split.p02_sha256(),
        PlanEvaluationErrorCode::InvalidDataset,
        "P02 split",
    )?;
    let p02_rows = parse_jsonl::<P02Row>(
        &p02_bytes,
        P02_RECORDS,
        MAX_P02_ROW_BYTES,
        "P02 semantic row",
    )?;
    validate_p02_rows(&p02_rows, split, &p09_projection)?;

    let p11_bytes = read_verified(
        root,
        split.p11_path(),
        MAX_P11_BYTES,
        split.p11_sha256(),
        PlanEvaluationErrorCode::InvalidDataset,
        "P11 supplement",
    )?;
    let p11_rows = parse_jsonl::<P11Row>(
        &p11_bytes,
        P11_RECORDS,
        MAX_P11_ROW_BYTES,
        "P11 negation row",
    )?;
    validate_p11_rows(&p11_rows, split)?;

    Ok(FrozenInputs {
        p09_projection,
        p11_projection,
        p02_rows,
        p11_rows,
    })
}

fn read_verified(
    root: &Path,
    relative_path: &str,
    maximum_bytes: usize,
    expected_sha256: &str,
    code: PlanEvaluationErrorCode,
    context: &'static str,
) -> Result<Vec<u8>> {
    let bytes = read_bounded_root_file(root, relative_path, maximum_bytes)
        .map_err(|error| map_data_error(code, error))?;
    verify_hash(&bytes, expected_sha256, code, context)?;
    Ok(bytes)
}

fn verify_hash(
    bytes: &[u8],
    expected_sha256: &str,
    code: PlanEvaluationErrorCode,
    context: &'static str,
) -> Result<()> {
    let actual = sha256_hex(bytes).map_err(|error| map_data_error(code, error))?;
    if actual != expected_sha256 {
        return Err(PlanEvaluationError::new(
            code,
            format!("{context} SHA-256 substitution"),
        ));
    }
    Ok(())
}

fn parse_json<T>(
    bytes: &[u8],
    context: &'static str,
    error: fn(String) -> PlanEvaluationError,
) -> Result<T>
where
    T: DeserializeOwned,
{
    let value = parse_strict_json(bytes, context).map_err(|failure| {
        if failure.code() == DataErrorCode::ResourceLimit {
            resource_limit(failure.context())
        } else {
            error(format!("{context} JSON"))
        }
    })?;
    serde_json::from_value(value).map_err(|_| error(format!("{context} schema")))
}

fn parse_jsonl<T>(
    bytes: &[u8],
    expected_records: usize,
    maximum_row_bytes: usize,
    context: &'static str,
) -> Result<Vec<T>>
where
    T: DeserializeOwned,
{
    let mut rows = Vec::new();
    let mut offset = 0_usize;
    for line in bytes.split(|byte| *byte == b'\n') {
        if line.is_empty() {
            if offset == bytes.len() || offset.saturating_add(1) == bytes.len() {
                break;
            }
            return Err(invalid_dataset("blank dataset row"));
        }
        if line.len() > maximum_row_bytes || rows.len() >= expected_records {
            return Err(resource_limit("dataset row bounds"));
        }
        let value = parse_strict_json(line, context).map_err(|error| {
            if error.code() == DataErrorCode::ResourceLimit {
                resource_limit(error.context())
            } else {
                invalid_dataset(format!("{context} JSON"))
            }
        })?;
        let row = serde_json::from_value(value)
            .map_err(|_| invalid_dataset(format!("{context} schema")))?;
        rows.push(row);
        offset = offset
            .checked_add(line.len().saturating_add(1))
            .ok_or_else(|| resource_limit("dataset offset"))?;
    }
    if rows.len() != expected_records {
        return Err(invalid_dataset("dataset record count"));
    }
    Ok(rows)
}

fn validate_p09_projection(projection: &P09Projection) -> Result<()> {
    if projection.schema_version != 1
        || projection.projection_id != "p09-pre-resolution-oracle-projection-v1"
        || projection.schema_id != "p09-intent-schema-v1"
        || projection.source.source_id != P02_SOURCE_ID
        || projection.source.corpus_version != P02_CORPUS_VERSION
        || projection.source.generator_id != P02_GENERATOR_ID
        || projection.source.oracle_origin != P02_ORACLE_ORIGIN
        || projection.source.specification_sha256 != P02_SPECIFICATION_SHA256
        || projection.source.generator_sha256 != P02_GENERATOR_SHA256
        || projection.source.development_sha256 != EvaluationSplit::Development.p02_sha256()
        || projection.source.development_records != P02_RECORDS as u64
        || projection.source.claim_scope != CLAIM_SCOPE
        || projection.span_derivation.algorithm != "p02-generator-parameter-exact-utf8-substring-v1"
        || !projection.span_derivation.require_unique_match
        || projection.span_derivation.source_output_allowed
        || projection.span_derivation.normalization_allowed
        || projection.intent_projections.len() != 20
    {
        return Err(invalid_projection("P09 projection identity"));
    }

    let mut external = BTreeSet::new();
    let mut internal = BTreeSet::new();
    for intent in &projection.intent_projections {
        IntentId::new(&intent.intent_id).map_err(|_| invalid_projection("P09 intent ID"))?;
        if !external.insert(intent.external_intent.as_str())
            || !internal.insert(intent.intent_id.as_str())
            || templates::contract(&intent.external_intent).is_none()
            || intent.slots.is_empty()
        {
            return Err(invalid_projection("P09 intent inventory"));
        }
        validate_slot_projections(intent)?;
    }
    Ok(())
}

fn validate_slot_projections(intent: &IntentProjection) -> Result<()> {
    let mut keys = BTreeSet::new();
    for slot in &intent.slots {
        SlotId::new(&slot.slot_id).map_err(|_| invalid_projection("P09 slot ID"))?;
        if slot.expected_slot_id.is_empty()
            || !matches!(slot.expected_kind.as_str(), "entity" | "integer" | "text")
            || slot.role.is_empty()
            || !keys.insert((&slot.slot_id, &slot.role, slot.occurrence))
            || !matches!(
                slot.parameter.as_str(),
                "target" | "target2" | "position" | "minutes" | "scope" | "message"
            )
            || !matches!(
                (slot.expected_kind.as_str(), slot.transform.as_str()),
                ("entity" | "text", "evidence_text")
                    | ("integer", "integer" | "minutes_to_seconds")
            )
        {
            return Err(invalid_projection("P09 slot projection"));
        }
    }
    Ok(())
}

fn validate_p11_projection(projection: &P11Projection) -> Result<()> {
    const LIMITATIONS: [&str; 5] = [
        "project_authored_internal_conformance_not_independent_accuracy",
        "typed_evidence_is_a_pre_composer_mechanical_projection",
        "development_results_do_not_authorize_runtime_rule_changes",
        "heldout_not_accessed",
        "graph_class_does_not_authorize_execution",
    ];
    if projection.schema_version != 1
        || projection.projection_id != "p11-semantic-plan-oracle-projection-v1"
        || projection.semantic_plan_schema != "p11-semantic-plan-v1"
        || projection.source.source_id != P02_SOURCE_ID
        || projection.source.corpus_version != P02_CORPUS_VERSION
        || projection.source.generator_id != P02_GENERATOR_ID
        || projection.source.oracle_origin != P02_ORACLE_ORIGIN
        || projection.source.specification_sha256 != P02_SPECIFICATION_SHA256
        || projection.source.generator_sha256 != P02_GENERATOR_SHA256
        || projection.source.train_sha256 != EvaluationSplit::Train.p02_sha256()
        || projection.source.development_sha256 != EvaluationSplit::Development.p02_sha256()
        || projection.source.records_per_split != P02_RECORDS as u64
        || projection.source.claim_scope != CLAIM_SCOPE
        || projection.pre_resolution_projection.path != P09_PROJECTION_PATH
        || projection.pre_resolution_projection.sha256 != P09_PROJECTION_SHA256
        || projection.catalog_projection.generation != 1
        || projection.catalog_projection.registry_id_algorithm
            != "sha256(p11-catalog-v1-nul || external_entity_id_utf8)[0:32]"
        || projection.catalog_projection.core_entity_id_prefix != "ha_entity:id_"
        || projection.catalog_projection.mention_source
            != "pre_resolution_parameter_exact_utf8_span"
        || projection.catalog_projection.display_matching_allowed
        || projection.catalog_projection.nlu_output_allowed
        || projection.evidence_projection.argument != "pre_resolution_parameter_exact_utf8_span"
        || projection.evidence_projection.single_predicate
            != "trimmed_initial_template_literal_before_first_parameter"
        || projection.evidence_projection.parallel_predicate
            != "shared_trimmed_initial_template_literal_before_first_parameter"
        || projection.evidence_projection.normalization_allowed
        || projection.evidence_projection.nlu_output_allowed
        || projection
            .limitations
            .iter()
            .map(String::as_str)
            .ne(LIMITATIONS)
        || projection.graph_shapes.len() != 3
    {
        return Err(invalid_projection("P11 projection identity"));
    }

    let shapes = projection
        .graph_shapes
        .iter()
        .map(|shape| (shape.shape.as_str(), shape))
        .collect::<BTreeMap<_, _>>();
    if shapes.len() != 3 {
        return Err(invalid_projection("P11 shape inventory"));
    }
    validate_single_shape(
        shapes
            .get("single")
            .ok_or_else(|| invalid_projection("single shape"))?,
    )?;
    validate_parallel_shape(
        shapes
            .get("parallel_pair")
            .ok_or_else(|| invalid_projection("parallel shape"))?,
    )?;
    validate_ordered_shape(
        shapes
            .get("ordered_pair")
            .ok_or_else(|| invalid_projection("ordered shape"))?,
    )
}

fn validate_single_shape(shape: &GraphShapeProjection) -> Result<()> {
    validate_node_ids(&shape.node_ids)?;
    if shape.node_ids != ["p11:node_1"]
        || shape.required_external_intent.is_some()
        || shape.slot_occurrences.is_some()
        || shape.execution_class != "partial_safe"
        || shape.secondary_capability.is_some()
        || shape.secondary_operation.is_some()
        || shape.secondary_predicate_cues.is_some()
        || shape.relation_cues.is_some()
        || !shape.relations.is_empty()
        || !shape.independent_pairs.is_empty()
        || !shape.argument_shares.is_empty()
    {
        return Err(invalid_projection("single shape contract"));
    }
    Ok(())
}

fn validate_parallel_shape(shape: &GraphShapeProjection) -> Result<()> {
    validate_node_ids(&shape.node_ids)?;
    let pair = shape.independent_pairs.first();
    if shape.node_ids != ["p11:node_1", "p11:node_2"]
        || shape.required_external_intent.as_deref() != Some("HassTurnOn")
        || shape.slot_occurrences.as_deref() != Some(&[0, 1])
        || shape.execution_class != "atomic_only"
        || shape.secondary_capability.is_some()
        || shape.secondary_operation.is_some()
        || shape.secondary_predicate_cues.is_some()
        || shape.relation_cues.is_some()
        || !shape.relations.is_empty()
        || shape.independent_pairs.len() != 1
        || !pair.is_some_and(|value| value.left == "p11:node_1" && value.right == "p11:node_2")
        || !shape.argument_shares.is_empty()
    {
        return Err(invalid_projection("parallel shape contract"));
    }
    Ok(())
}

fn validate_ordered_shape(shape: &GraphShapeProjection) -> Result<()> {
    validate_node_ids(&shape.node_ids)?;
    let predicate = shape.secondary_predicate_cues.as_ref();
    let relation_cues = shape.relation_cues.as_ref();
    let relation = shape.relations.first();
    let share = shape.argument_shares.first();
    if shape.node_ids != ["p11:node_1", "p11:node_2"]
        || shape.required_external_intent.as_deref() != Some("HassStartTimer")
        || shape.slot_occurrences.is_some()
        || shape.execution_class != "partial_safe"
        || shape.secondary_capability.as_deref() != Some("ha:timer_control")
        || shape.secondary_operation.as_deref() != Some("ha:timer_status")
        || !predicate.is_some_and(|value| {
            value.train == "consulte o estado" && value.development == "verifique o estado"
        })
        || !relation_cues.is_some_and(|value| {
            value.train == "e consulte o estado depois"
                && value.development == "e então verifique o estado"
        })
        || shape.relations.len() != 1
        || !relation.is_some_and(|value| {
            value.from == "p11:node_1" && value.to == "p11:node_2" && value.kind == "precedes"
        })
        || !shape.independent_pairs.is_empty()
        || shape.argument_shares.len() != 1
        || !share.is_some_and(|value| {
            value.from_node == "p11:node_1"
                && value.from_slot == "ha:timer"
                && value.to_node == "p11:node_2"
                && value.to_slot == "ha:timer"
                && value.evidence == "source_argument"
        })
    {
        return Err(invalid_projection("ordered shape contract"));
    }
    Ok(())
}

fn validate_node_ids(ids: &[String]) -> Result<()> {
    for id in ids {
        NodeId::new(id).map_err(|_| invalid_projection("P11 node ID"))?;
    }
    Ok(())
}

fn validate_p02_rows(
    rows: &[P02Row],
    split: EvaluationSplit,
    projection: &P09Projection,
) -> Result<()> {
    let allowed = projection
        .intent_projections
        .iter()
        .map(|intent| intent.external_intent.as_str())
        .collect::<BTreeSet<_>>();
    let mut cases = BTreeSet::new();
    let mut semantic_ids = BTreeSet::new();
    let mut intent_counts = BTreeMap::<&str, usize>::new();
    let mut shape_counts = BTreeMap::<&str, usize>::new();
    for row in rows {
        let contract = templates::contract(&row.expected.intent)
            .ok_or_else(|| invalid_dataset("P02 external intent"))?;
        let index = row_index(row, split, contract.slug)?;
        let rendered = templates::render(split, &row.expected.intent, index)?;
        let expected_session = format!("p02:{}_{}_{index:03}", split.code(), contract.slug);
        let expected_family = format!("{}-{}-family-v1", split.code(), contract.slug);
        if row.schema_version != 1
            || row.generator_record_id != format!("generator-{}", row.case_id)
            || !is_sha256(&row.canonical_semantic_id)
            || !semantic_ids.insert(row.canonical_semantic_id.as_str())
            || row.source_id != P02_SOURCE_ID
            || row.corpus_version != P02_CORPUS_VERSION
            || row.generator_id != P02_GENERATOR_ID
            || row.oracle_origin != P02_ORACLE_ORIGIN
            || row.license != SOURCE_LICENSE
            || row.locale != LOCALE
            || row.split != split.code()
            || hash_text(&row.utterance)? != row.utterance_sha256
            || row.utterance.as_bytes() != rendered.utterance.as_bytes()
            || row.context.catalog_generation != 1
            || row.context.session_snapshot_id != expected_session
            || row.dimensions.source != P02_SOURCE_ID
            || row.dimensions.family != expected_family
            || row.dimensions.intent != row.expected.intent
            || row.dimensions.domain.is_empty()
            || row.dimensions.slot_kind.is_empty()
            || row.dimensions.outcome != "plan"
            || row.dimensions.ambiguity != "unambiguous"
            || row.dimensions.noise != "clean_text"
            || !matches!(
                row.dimensions.target_cardinality.as_str(),
                "zero" | "one" | "two"
            )
            || row.expected.outcome != "plan"
            || row.expected.catalog_generation != 1
            || !allowed.contains(row.expected.intent.as_str())
            || !cases.insert(row.case_id.as_str())
        {
            return Err(invalid_dataset("P02 row identity"));
        }
        validate_p02_plan(&row.expected, &row.dimensions.graph_shape)?;
        *intent_counts.entry(&row.expected.intent).or_default() += 1;
        *shape_counts.entry(&row.dimensions.graph_shape).or_default() += 1;
    }
    if intent_counts.len() != 20
        || intent_counts.values().any(|count| *count != 48)
        || shape_counts.get("single") != Some(&864)
        || shape_counts.get("parallel_pair") != Some(&48)
        || shape_counts.get("ordered_pair") != Some(&48)
        || shape_counts.len() != 3
    {
        return Err(invalid_dataset("P02 stratum counts"));
    }
    Ok(())
}

fn validate_p02_plan(plan: &P02ExpectedPlan, shape: &str) -> Result<()> {
    let expected_nodes = match shape {
        "single" => 1,
        "parallel_pair" | "ordered_pair" => 2,
        _ => return Err(invalid_dataset("P02 graph shape")),
    };
    if plan.nodes.len() != expected_nodes {
        return Err(invalid_dataset("P02 node count"));
    }
    for (index, node) in plan.nodes.iter().enumerate() {
        if node.id != format!("p02:node_{}", index + 1)
            || node.slots.is_empty()
            || CapabilityId::new(&node.capability).is_err()
            || OperationId::new(&node.operation).is_err()
        {
            return Err(invalid_dataset("P02 expected node"));
        }
        let mut slots = BTreeSet::new();
        for slot in &node.slots {
            validate_p02_slot(slot)?;
            let encoded = serde_json::to_vec(&slot.value)
                .map_err(|_| invalid_dataset("P02 slot value encoding"))?;
            if !slots.insert((slot.id.as_str(), slot.kind.as_str(), encoded)) {
                return Err(invalid_dataset("P02 duplicate slot"));
            }
        }
    }
    match shape {
        "ordered_pair" => {
            let relation = plan.relations.first();
            if plan.relations.len() != 1
                || !relation.is_some_and(|value| {
                    value.from == "p02:node_1"
                        && value.to == "p02:node_2"
                        && value.kind == "precedes"
                })
            {
                return Err(invalid_dataset("P02 ordered relation"));
            }
        }
        _ if !plan.relations.is_empty() => {
            return Err(invalid_dataset("P02 unexpected relation"));
        }
        _ => {}
    }
    Ok(())
}

fn validate_p02_slot(slot: &P02ExpectedSlot) -> Result<()> {
    SlotId::new(&slot.id).map_err(|_| invalid_dataset("P02 slot ID"))?;
    match slot.kind.as_str() {
        "integer" if slot.value.as_i64().is_some() => Ok(()),
        "text" if slot.value.as_str().is_some() => Ok(()),
        "entity" => {
            let entity = slot
                .value
                .as_object()
                .ok_or_else(|| invalid_dataset("P02 entity value"))?;
            if entity.len() != 2
                || entity.get("id").and_then(Value::as_str).is_none()
                || entity.get("catalog_generation").and_then(Value::as_u64) != Some(1)
            {
                return Err(invalid_dataset("P02 entity value shape"));
            }
            Ok(())
        }
        _ => Err(invalid_dataset("P02 slot value kind")),
    }
}

fn row_index(row: &P02Row, split: EvaluationSplit, slug: &str) -> Result<u64> {
    let prefix = format!("p02-v1-{}-{slug}-", split.code());
    let suffix = row
        .case_id
        .strip_prefix(&prefix)
        .ok_or_else(|| invalid_dataset("P02 case prefix"))?;
    if suffix.len() != 3 || !suffix.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(invalid_dataset("P02 case index"));
    }
    let index = suffix
        .parse::<u64>()
        .map_err(|_| invalid_dataset("P02 case index"))?;
    if !(1..=48).contains(&index) {
        return Err(invalid_dataset("P02 case index range"));
    }
    Ok(index)
}

fn validate_p11_rows(rows: &[P11Row], split: EvaluationSplit) -> Result<()> {
    let mut cases = BTreeSet::new();
    let mut semantic_ids = BTreeSet::new();
    let mut strata = BTreeSet::new();
    for row in rows {
        let case_key = row
            .case_id
            .strip_prefix("p11-v1-")
            .ok_or_else(|| invalid_dataset("P11 case ID"))?;
        if row.schema_version != 1
            || row.generator_record_id != format!("generator-{}", row.case_id)
            || !is_sha256(&row.canonical_semantic_id)
            || !semantic_ids.insert(row.canonical_semantic_id.as_str())
            || row.specification_id != "p11-multi-intent-negation-specification-v1"
            || row.specification_version != "1.0.0"
            || row.source_id != P11_SOURCE_ID
            || row.source_type != "PROJECT_AUTHORED_SYNTHETIC"
            || row.corpus_id != P11_SOURCE_ID
            || row.corpus_version != "1.0.0"
            || row.generator_id != P11_GENERATOR_ID
            || row.oracle_origin != P11_ORACLE_ORIGIN
            || row.oracle_authorizations != ["USR-016", "USER_EXPLICIT_2026-08-28"]
            || row.oracle_derivation != "specification_only_no_nlu_output"
            || row.license != SOURCE_LICENSE
            || row.locale != LOCALE
            || row.claim_scope != CLAIM_SCOPE
            || row.split != split.code()
            || !row.family.starts_with(&format!("p11-{}-", split.code()))
            || !row.family.ends_with("-v1")
            || !matches!(
                row.stratum.as_str(),
                "clear_first_node_negation"
                    | "clear_second_node_negation"
                    | "ambiguous_shared_scope"
            )
            || !strata.insert(row.stratum.as_str())
            || hash_text(&row.utterance)? != row.utterance_sha256
            || row.context.catalog_generation != 1
            || row.context.entities.len() != 2
            || !cases.insert(row.case_id.as_str())
        {
            return Err(invalid_dataset("P11 row identity"));
        }
        validate_p11_context(row, case_key)?;
        validate_p11_expected(row)?;
    }
    if strata
        != BTreeSet::from([
            "ambiguous_shared_scope",
            "clear_first_node_negation",
            "clear_second_node_negation",
        ])
    {
        return Err(invalid_dataset("P11 negation strata"));
    }
    Ok(())
}

fn validate_p11_context(row: &P11Row, case_key: &str) -> Result<()> {
    let mut registry_ids = BTreeSet::new();
    let mut mentions = BTreeSet::new();
    for (index, entity) in row.context.entities.iter().enumerate() {
        let seed = format!("p11-negation-registry-v1\0{case_key}\0target-{}", index + 1);
        let expected_registry = hash_text(&seed)?
            .get(..32)
            .ok_or_else(|| invalid_dataset("P11 registry digest"))?
            .to_owned();
        if entity.registry_id != expected_registry
            || entity.entity_id != format!("ha_entity:id_{}", entity.registry_id)
            || entity.catalog_generation != 1
            || entity.mention.is_empty()
            || row.utterance.match_indices(&entity.mention).count() != 1
            || !registry_ids.insert(entity.registry_id.as_str())
            || !mentions.insert(entity.mention.as_str())
        {
            return Err(invalid_dataset("P11 catalog context"));
        }
    }
    Ok(())
}

fn validate_p11_expected(row: &P11Row) -> Result<()> {
    match row.stratum.as_str() {
        "ambiguous_shared_scope" => {
            if row.expected.outcome != "abstention"
                || row.expected.reason.as_deref() != Some("negation_scope")
                || row.expected.intent.is_some()
                || row.expected.plan.is_some()
            {
                return Err(invalid_dataset("P11 abstention oracle"));
            }
        }
        "clear_first_node_negation" | "clear_second_node_negation" => {
            let plan = row
                .expected
                .plan
                .as_ref()
                .ok_or_else(|| invalid_dataset("P11 plan oracle"))?;
            if row.expected.outcome != "plan"
                || row.expected.intent.as_deref() != Some("HassTurnOn")
                || row.expected.reason.is_some()
            {
                return Err(invalid_dataset("P11 plan outcome"));
            }
            validate_p11_plan(row, plan)?;
        }
        _ => return Err(invalid_dataset("P11 stratum")),
    }
    Ok(())
}

fn validate_p11_plan(row: &P11Row, plan: &SemanticPlan) -> Result<()> {
    if plan.schema_version != "p11-semantic-plan-v1"
        || plan.catalog_generation != 1
        || plan.execution_class != "non_executable"
        || plan.nodes.len() != 2
        || !plan.relations.is_empty()
        || plan.independent_pairs
            != [crate::schema::SemanticIndependentPair {
                left: "p11:node_1".to_owned(),
                right: "p11:node_2".to_owned(),
            }]
        || !plan.argument_shares.is_empty()
    {
        return Err(invalid_dataset("P11 semantic plan shape"));
    }
    let source =
        RequestText::new(row.utterance.clone()).map_err(|_| invalid_dataset("P11 utterance"))?;
    for (index, node) in plan.nodes.iter().enumerate() {
        let expected_polarity = match (row.stratum.as_str(), index) {
            ("clear_first_node_negation", 0) | ("clear_second_node_negation", 1) => "negated",
            _ => "affirmed",
        };
        if node.id != format!("p11:node_{}", index + 1)
            || node.intent != "HassTurnOn"
            || node.capability != "ha:light_control"
            || node.operation != "ha:turn_on"
            || node.polarity != expected_polarity
            || node.slots.len() != 1
        {
            return Err(invalid_dataset("P11 semantic node"));
        }
        let slot = &node.slots[0];
        let context = &row.context.entities[index];
        let value = slot
            .value
            .as_object()
            .ok_or_else(|| invalid_dataset("P11 entity slot"))?;
        if slot.id != "ha:entity"
            || slot.kind != "entity"
            || value.len() != 2
            || value.get("id").and_then(Value::as_str) != Some(context.entity_id.as_str())
            || value.get("catalog_generation").and_then(Value::as_u64) != Some(1)
        {
            return Err(invalid_dataset("P11 entity slot value"));
        }
        validate_p11_evidence(&source, node, context.mention.as_str(), expected_polarity)?;
    }
    Ok(())
}

fn validate_p11_evidence(
    source: &RequestText,
    node: &crate::schema::SemanticNode,
    mention: &str,
    polarity: &str,
) -> Result<()> {
    let expected_count = if polarity == "negated" { 3 } else { 2 };
    if node.evidence.len() != expected_count {
        return Err(invalid_dataset("P11 evidence count"));
    }
    let mut kinds = BTreeSet::new();
    for atom in &node.evidence {
        let span = source
            .span(u64::from(atom.begin_byte), u64::from(atom.end_byte))
            .map_err(|_| invalid_dataset("P11 UTF-8 evidence span"))?;
        let text = span
            .slice(source)
            .map_err(|_| invalid_dataset("P11 evidence source"))?;
        if !kinds.insert(atom.kind.as_str()) {
            return Err(invalid_dataset("P11 duplicate evidence kind"));
        }
        match atom.kind.as_str() {
            "predicate" if atom.slot.is_none() && text == "ligue" => {}
            "argument" if atom.slot.as_deref() == Some("ha:entity") && text == mention => {}
            "negation" if atom.slot.is_none() && polarity == "negated" && text == "não" => {}
            _ => return Err(invalid_dataset("P11 evidence semantics")),
        }
    }
    if !kinds.contains("predicate")
        || !kinds.contains("argument")
        || (polarity == "negated") != kinds.contains("negation")
    {
        return Err(invalid_dataset("P11 evidence inventory"));
    }
    Ok(())
}

pub(super) fn find_intent<'a>(
    projection: &'a P09Projection,
    external_intent: &str,
) -> Result<&'a IntentProjection> {
    projection
        .intent_projections
        .iter()
        .find(|intent| intent.external_intent == external_intent)
        .ok_or_else(|| invalid_projection("missing intent projection"))
}

pub(super) fn find_shape<'a>(
    projection: &'a P11Projection,
    shape: &str,
) -> Result<&'a GraphShapeProjection> {
    projection
        .graph_shapes
        .iter()
        .find(|candidate| candidate.shape == shape)
        .ok_or_else(|| invalid_projection("missing graph shape"))
}

pub(super) fn projected_slot<'a>(
    projection: &'a IntentProjection,
    slot_id: &str,
    kind: &str,
    occurrence: Option<u16>,
) -> Result<&'a SlotProjection> {
    let mut candidates = projection.slots.iter().filter(|slot| {
        slot.expected_slot_id == slot_id
            && slot.expected_kind == kind
            && occurrence.is_none_or(|value| slot.occurrence == value)
    });
    let first = candidates
        .next()
        .ok_or_else(|| invalid_projection("missing slot projection"))?;
    if candidates.next().is_some() {
        return Err(invalid_projection("ambiguous slot projection"));
    }
    Ok(first)
}

pub(super) fn p02_row_index(row: &P02Row, split: EvaluationSplit) -> Result<u64> {
    let contract = templates::contract(&row.expected.intent)
        .ok_or_else(|| invalid_dataset("P02 generator contract"))?;
    row_index(row, split, contract.slug)
}

fn hash_text(value: &str) -> Result<String> {
    sha256_hex(value.as_bytes())
        .map_err(|error| map_data_error(PlanEvaluationErrorCode::InvalidDataset, error))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn map_data_error(code: PlanEvaluationErrorCode, error: DataError) -> PlanEvaluationError {
    if error.code() == DataErrorCode::ResourceLimit {
        resource_limit(error.context())
    } else {
        PlanEvaluationError::new(code, error.context())
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;

    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .find(|candidate| candidate.join("AGENTS.md").is_file())
            .expect("repository root")
            .to_path_buf()
    }

    #[test]
    fn rejects_hash_substitution_for_each_frozen_input_class() {
        let root = repository_root();
        for (path, expected, code, context) in [
            (
                P09_PROJECTION_PATH,
                P09_PROJECTION_SHA256,
                PlanEvaluationErrorCode::InvalidProjection,
                "P09 projection",
            ),
            (
                P11_PROJECTION_PATH,
                P11_PROJECTION_SHA256,
                PlanEvaluationErrorCode::InvalidProjection,
                "P11 projection",
            ),
            (
                EvaluationSplit::Train.p02_path(),
                EvaluationSplit::Train.p02_sha256(),
                PlanEvaluationErrorCode::InvalidDataset,
                "P02 split",
            ),
            (
                EvaluationSplit::Train.p11_path(),
                EvaluationSplit::Train.p11_sha256(),
                PlanEvaluationErrorCode::InvalidDataset,
                "P11 supplement",
            ),
        ] {
            let mut bytes = std::fs::read(root.join(path)).expect("read frozen input");
            bytes[0] ^= 1;
            let error = verify_hash(&bytes, expected, code, context).expect_err("substitution");
            assert_eq!(error.code(), code);
        }
    }

    #[test]
    fn strict_rows_reject_duplicate_keys_and_malformed_json() {
        let duplicate = br#"{"schema_version":1,"schema_version":1}"#;
        assert_eq!(
            parse_jsonl::<P02Row>(duplicate, 1, 1_024, "duplicate")
                .expect_err("duplicate")
                .code(),
            PlanEvaluationErrorCode::InvalidDataset
        );
        assert_eq!(
            parse_jsonl::<P02Row>(b"{", 1, 1_024, "malformed")
                .expect_err("malformed")
                .code(),
            PlanEvaluationErrorCode::InvalidDataset
        );
    }

    #[test]
    fn rejects_source_lineage_mismatch_after_strict_parsing() {
        let root = repository_root();
        let bytes = std::fs::read(root.join(EvaluationSplit::Train.p02_path())).expect("dataset");
        let mut rows =
            parse_jsonl::<P02Row>(&bytes, P02_RECORDS, MAX_P02_ROW_BYTES, "P02").expect("parse");
        rows[0].source_id = "fixture_tecnica:substitution".to_owned();
        let projection_bytes = std::fs::read(root.join(P09_PROJECTION_PATH)).expect("projection");
        let projection: P09Projection =
            parse_json(&projection_bytes, "P09", invalid_projection).expect("parse projection");
        assert_eq!(
            validate_p02_rows(&rows, EvaluationSplit::Train, &projection)
                .expect_err("lineage")
                .code(),
            PlanEvaluationErrorCode::InvalidDataset
        );
    }

    #[test]
    fn projection_semantics_are_validated_beyond_the_physical_hash() {
        let root = repository_root();
        let bytes = std::fs::read(root.join(P11_PROJECTION_PATH)).expect("projection");
        let mut projection: P11Projection =
            parse_json(&bytes, "P11", invalid_projection).expect("parse");
        projection.catalog_projection.generation = 2;
        assert_eq!(
            validate_p11_projection(&projection)
                .expect_err("projection mutation")
                .code(),
            PlanEvaluationErrorCode::InvalidProjection
        );
    }
}
