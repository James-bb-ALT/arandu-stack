use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
};

use intent_engine::{
    INTENT_ALGORITHM_ID, INTENT_CONFIGURATION_ID, INTENT_MANIFEST_SHA256, INTENT_PACKAGE_SHA256,
    INTENT_SCHEMA_ID, IntentEngine, IntentMatch, IntentSlotValue, RecognitionOutcome, SlotBinding,
};
use nlu_core::{IntentId, RequestText, SlotId};
use nlu_data::{
    DataError, DataErrorCode, canonical_json, parse_strict_json, read_bounded_root_file, sha256_hex,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{IntentEvaluationError, IntentEvaluationErrorCode, error::Result};

const RUNNER_ID: &str = "intent-eval-v1";
const DATASET_ID: &str = "p09-intent-internal-conformance";
const METRIC_SPEC: &str = "exact-pre-resolution-intent-and-slot-aggregate-v1";
const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const CORPUS_VERSION: &str = "1.0.0";
const GENERATOR_ID: &str = "p02-generator-v1";
const ORACLE_ORIGIN: &str = "pre_engine_generator_specification";
const SOURCE_LICENSE: &str = "Apache-2.0";
const LOCALE: &str = "pt-BR";
const CLAIM_SCOPE: &str = "internal_conformance_only";
const PROJECTION_ID: &str = "p09-pre-resolution-oracle-projection-v1";
const PROJECTION_PATH: &str = "data/evaluation/p09/intent-v1/projection.json";
const PROJECTION_SHA256: &str = "5f661bb96b85667a1d0c9ec4d85cb61c443e1b24ab4dfc6c1ab849c6df936ee4";
const SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
const GENERATOR_SHA256: &str = "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
const TRAIN_SHA256: &str = "23d2bc8c8fcde80d1a9560d42219484bc34e9198c791ccadf5d4b56413a81b64";
const DEVELOPMENT_SHA256: &str = "75400570ddfc7196ed982da98dcf49c4e6bda5820200a2fefd93a8ea6f049161";
const RECORDS_PER_SPLIT: u64 = 960;
const RECORDS_PER_INTENT: u64 = 48;
const MAX_DATASET_BYTES: usize = 2 * 1024 * 1024;
const MAX_PROJECTION_BYTES: usize = 64 * 1024;
const MAX_RECORD_BYTES: usize = 8 * 1024;
const MAX_RECORDS: usize = 1_024;
const AREAS: [&str; 20] = [
    "sala",
    "cozinha",
    "quarto",
    "escritorio",
    "corredor",
    "varanda",
    "garagem",
    "lavanderia",
    "biblioteca",
    "atelie",
    "copa",
    "despensa",
    "banheiro",
    "suite",
    "jardim",
    "porao",
    "sotao",
    "oficina",
    "estudio",
    "academia",
];
const LIMITATIONS: [&str; 5] = [
    "project_authored_templates_share_source_with_runtime_markers",
    "single_source_templatic_internal_conformance_not_independent_accuracy",
    "development_results_do_not_authorize_runtime_rule_changes",
    "heldout_not_accessed",
    "entity_resolution_and_plan_graph_exactness_out_of_scope",
];

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum EvaluationSplit {
    Train,
    Development,
}

impl EvaluationSplit {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Train => "train",
            Self::Development => "development",
        }
    }

    const fn path(self) -> &'static str {
        match self {
            Self::Train => "data/project-authored/p02-v1/train.jsonl",
            Self::Development => "data/project-authored/p02-v1/development.jsonl",
        }
    }

    const fn sha256(self) -> &'static str {
        match self {
            Self::Train => TRAIN_SHA256,
            Self::Development => DEVELOPMENT_SHA256,
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "train" => Some(Self::Train),
            "development" => Some(Self::Development),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FractionMetric {
    numerator: u64,
    denominator: u64,
}

impl FractionMetric {
    #[must_use]
    pub const fn numerator(&self) -> u64 {
        self.numerator
    }

    #[must_use]
    pub const fn denominator(&self) -> u64 {
        self.denominator
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct OutcomeCounts {
    matches: u64,
    clarifications: u64,
    abstentions: u64,
    errors: u64,
}

impl OutcomeCounts {
    #[must_use]
    pub const fn matches(&self) -> u64 {
        self.matches
    }

    #[must_use]
    pub const fn clarifications(&self) -> u64 {
        self.clarifications
    }

    #[must_use]
    pub const fn abstentions(&self) -> u64 {
        self.abstentions
    }

    #[must_use]
    pub const fn errors(&self) -> u64 {
        self.errors
    }

    const fn total(&self) -> u64 {
        self.matches + self.clarifications + self.abstentions + self.errors
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct IntentStratum {
    intent_id: String,
    total: u64,
    exact_semantics: u64,
    matches: u64,
    clarifications: u64,
    abstentions: u64,
    errors: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SlotStratum {
    intent_id: String,
    slot_id: String,
    role: String,
    occurrence: u16,
    value_kind: String,
    total: u64,
    value_exact: u64,
    value_incorrect: u64,
    span_exact: u64,
    span_incorrect: u64,
    missing: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SlotMetrics {
    total: u64,
    value_exact: u64,
    value_incorrect: u64,
    span_exact: u64,
    span_incorrect: u64,
    missing: u64,
    unexpected: u64,
}

impl SlotMetrics {
    #[must_use]
    pub const fn total(&self) -> u64 {
        self.total
    }

    #[must_use]
    pub const fn value_exact(&self) -> u64 {
        self.value_exact
    }

    #[must_use]
    pub const fn span_exact(&self) -> u64 {
        self.span_exact
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Reconciliation {
    records_expected: u64,
    records_observed: u64,
    outcome_total: u64,
    intent_strata_total: u64,
    slots_expected: u64,
    slot_strata_total: u64,
    value_accounted: u64,
    span_accounted: u64,
    complete: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct DatasetIdentity {
    dataset_id: &'static str,
    source_id: &'static str,
    corpus_version: &'static str,
    generator_id: &'static str,
    oracle_origin: &'static str,
    locale: &'static str,
    split: &'static str,
    claim_scope: &'static str,
    physical_sha256: &'static str,
    records: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RecognizerIdentity {
    schema_id: &'static str,
    algorithm_id: &'static str,
    configuration_id: &'static str,
    package_sha256: &'static str,
    package_manifest_sha256: &'static str,
    projection_id: &'static str,
    projection_sha256: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvaluationResults {
    exact_semantics: FractionMetric,
    outcomes: OutcomeCounts,
    slots: SlotMetrics,
    intent_strata: Vec<IntentStratum>,
    slot_strata: Vec<SlotStratum>,
    prediction_digest_sha256: String,
    reconciliation: Reconciliation,
}

impl EvaluationResults {
    #[must_use]
    pub const fn exact_semantics(&self) -> &FractionMetric {
        &self.exact_semantics
    }

    #[must_use]
    pub const fn outcomes(&self) -> &OutcomeCounts {
        &self.outcomes
    }

    #[must_use]
    pub const fn slots(&self) -> &SlotMetrics {
        &self.slots
    }

    #[must_use]
    pub const fn reconciliation(&self) -> &Reconciliation {
        &self.reconciliation
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct IntentEvaluationReport {
    schema_version: u32,
    runner_id: &'static str,
    metric_specification: &'static str,
    dataset: DatasetIdentity,
    recognizer: RecognizerIdentity,
    results: EvaluationResults,
    limitations: Vec<&'static str>,
}

impl IntentEvaluationReport {
    #[must_use]
    pub const fn results(&self) -> &EvaluationResults {
        &self.results
    }

    pub fn canonical_bytes(&self) -> Result<Vec<u8>> {
        let value = serde_json::to_value(self).map_err(|_| {
            evaluation_error(IntentEvaluationErrorCode::Reconciliation, "report value")
        })?;
        let mut bytes = canonical_json(&value, "P09 intent evaluation report")
            .map_err(|error| map_data_error(IntentEvaluationErrorCode::Reconciliation, error))?;
        bytes.push(b'\n');
        Ok(bytes)
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Projection {
    schema_version: u32,
    projection_id: String,
    schema_id: String,
    source: ProjectionSource,
    span_derivation: SpanDerivation,
    intent_projections: Vec<IntentProjection>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProjectionSource {
    source_id: String,
    corpus_version: String,
    generator_id: String,
    oracle_origin: String,
    specification_sha256: String,
    generator_sha256: String,
    development_sha256: String,
    development_records: u64,
    claim_scope: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SpanDerivation {
    algorithm: String,
    require_unique_match: bool,
    source_output_allowed: bool,
    normalization_allowed: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct IntentProjection {
    external_intent: String,
    intent_id: String,
    slots: Vec<SlotProjection>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SlotProjection {
    expected_slot_id: String,
    expected_kind: String,
    slot_id: String,
    role: String,
    occurrence: u16,
    parameter: String,
    transform: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CorpusRow {
    schema_version: u32,
    case_id: String,
    generator_record_id: String,
    canonical_semantic_id: String,
    source_id: String,
    corpus_version: String,
    generator_id: String,
    oracle_origin: String,
    license: String,
    locale: String,
    split: String,
    utterance: String,
    utterance_sha256: String,
    context: CorpusContext,
    dimensions: CorpusDimensions,
    expected: ExpectedPlan,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CorpusContext {
    catalog_generation: u64,
    session_snapshot_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CorpusDimensions {
    source: String,
    family: String,
    intent: String,
    domain: String,
    slot_kind: String,
    graph_shape: String,
    outcome: String,
    ambiguity: String,
    noise: String,
    target_cardinality: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedPlan {
    outcome: String,
    intent: String,
    catalog_generation: u64,
    nodes: Vec<ExpectedNode>,
    relations: Vec<ExpectedRelation>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedNode {
    id: String,
    capability: String,
    operation: String,
    slots: Vec<ExpectedSlot>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedSlot {
    id: String,
    kind: String,
    value: Value,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedRelation {
    from: String,
    to: String,
    kind: String,
}

#[derive(Clone, Copy)]
struct GeneratorContract {
    external_intent: &'static str,
    slug: &'static str,
    target_noun: &'static str,
    train_template: &'static str,
    development_template: &'static str,
}

#[derive(Clone)]
struct RenderedParameter {
    text: String,
    begin_byte: u32,
    end_byte: u32,
}

struct RenderedOracle {
    utterance: String,
    parameters: BTreeMap<String, RenderedParameter>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct SlotKey {
    intent_id: String,
    slot_id: String,
    role: String,
    occurrence: u16,
    value_kind: String,
}

enum ExpectedValue {
    Text(String),
    Integer(i64),
}

struct ExpectedBinding {
    key: SlotKey,
    value: ExpectedValue,
    begin_byte: u32,
    end_byte: u32,
}

pub fn evaluate(root: &Path, split: EvaluationSplit) -> Result<IntentEvaluationReport> {
    let projection = load_projection(root)?;
    let rows = load_dataset(root, split, &projection)?;
    let engine = IntentEngine::bundled().map_err(|_| {
        evaluation_error(
            IntentEvaluationErrorCode::InvalidSchema,
            "bundled intent engine",
        )
    })?;

    let mut intent_strata = projection
        .intent_projections
        .iter()
        .map(|intent| {
            (
                intent.intent_id.clone(),
                IntentStratum {
                    intent_id: intent.intent_id.clone(),
                    total: 0,
                    exact_semantics: 0,
                    matches: 0,
                    clarifications: 0,
                    abstentions: 0,
                    errors: 0,
                },
            )
        })
        .collect::<BTreeMap<_, _>>();
    let mut slot_strata = projection
        .intent_projections
        .iter()
        .flat_map(|intent| {
            intent.slots.iter().map(|slot| {
                let key = projection_slot_key(intent, slot);
                (
                    key.clone(),
                    SlotStratum {
                        intent_id: key.intent_id,
                        slot_id: key.slot_id,
                        role: key.role,
                        occurrence: key.occurrence,
                        value_kind: key.value_kind,
                        total: 0,
                        value_exact: 0,
                        value_incorrect: 0,
                        span_exact: 0,
                        span_incorrect: 0,
                        missing: 0,
                    },
                )
            })
        })
        .collect::<BTreeMap<_, _>>();
    let mut outcomes = OutcomeCounts {
        matches: 0,
        clarifications: 0,
        abstentions: 0,
        errors: 0,
    };
    let mut slot_metrics = SlotMetrics {
        total: 0,
        value_exact: 0,
        value_incorrect: 0,
        span_exact: 0,
        span_incorrect: 0,
        missing: 0,
        unexpected: 0,
    };
    let mut exact_semantics = 0_u64;
    let mut prediction_digest_input = Vec::new();

    for row in &rows {
        let intent_projection = projection
            .intent_projections
            .iter()
            .find(|candidate| candidate.external_intent == row.expected.intent)
            .ok_or_else(|| invalid_projection("missing row intent projection"))?;
        let oracle = render_oracle(row, split, intent_projection)?;
        let expected = expected_bindings(intent_projection, &oracle)?;
        reconcile_expected_plan(row, intent_projection)?;
        let source =
            RequestText::new(row.utterance.clone()).map_err(|_| invalid_dataset("request text"))?;
        let recognition = engine.recognize(&source);

        let intent_counter = intent_strata
            .get_mut(&intent_projection.intent_id)
            .ok_or_else(|| reconciliation_error("intent stratum"))?;
        intent_counter.total += 1;
        let mut exact = false;
        match &recognition {
            Ok(RecognitionOutcome::Match(_)) => {
                outcomes.matches += 1;
                intent_counter.matches += 1;
            }
            Ok(RecognitionOutcome::Clarification(_)) => {
                outcomes.clarifications += 1;
                intent_counter.clarifications += 1;
            }
            Ok(RecognitionOutcome::Abstention(_)) => {
                outcomes.abstentions += 1;
                intent_counter.abstentions += 1;
            }
            Err(_) => {
                outcomes.errors += 1;
                intent_counter.errors += 1;
            }
        }

        let selected = recognition
            .as_ref()
            .ok()
            .and_then(|outcome| expected_candidate(outcome, &intent_projection.intent_id));
        let intent_is_exact = matches!(
            &recognition,
            Ok(RecognitionOutcome::Match(candidate))
                if candidate.intent().as_str() == intent_projection.intent_id
        );
        let mut predicted_by_key = BTreeMap::new();
        if let Some(candidate) = selected {
            for binding in candidate.slots() {
                let key = predicted_slot_key(candidate, binding);
                if predicted_by_key.insert(key, binding).is_some() {
                    return Err(reconciliation_error("duplicate predicted slot"));
                }
            }
        }

        let mut row_slots_exact = intent_is_exact;
        for expected_binding in &expected {
            slot_metrics.total += 1;
            let stratum = slot_strata
                .get_mut(&expected_binding.key)
                .ok_or_else(|| reconciliation_error("slot stratum"))?;
            stratum.total += 1;
            let predicted = predicted_by_key.remove(&expected_binding.key);
            let Some(predicted) = predicted else {
                slot_metrics.missing += 1;
                stratum.missing += 1;
                row_slots_exact = false;
                continue;
            };
            if value_matches(predicted, expected_binding, &source)? {
                slot_metrics.value_exact += 1;
                stratum.value_exact += 1;
            } else {
                slot_metrics.value_incorrect += 1;
                stratum.value_incorrect += 1;
                row_slots_exact = false;
            }
            if predicted.evidence().start() == expected_binding.begin_byte
                && predicted.evidence().end() == expected_binding.end_byte
            {
                slot_metrics.span_exact += 1;
                stratum.span_exact += 1;
            } else {
                slot_metrics.span_incorrect += 1;
                stratum.span_incorrect += 1;
                row_slots_exact = false;
            }
        }
        slot_metrics.unexpected += usize_to_u64(predicted_by_key.len(), "unexpected slots")?;
        if !predicted_by_key.is_empty() {
            row_slots_exact = false;
        }
        if row_slots_exact {
            exact = true;
            exact_semantics += 1;
            intent_counter.exact_semantics += 1;
        }
        debug_assert_eq!(exact, row_slots_exact);

        let prediction_bytes = match recognition {
            Ok(outcome) => {
                let first = outcome
                    .canonical_bytes(&source)
                    .map_err(|_| reconciliation_error("canonical prediction"))?;
                let second = outcome
                    .canonical_bytes(&source)
                    .map_err(|_| reconciliation_error("canonical prediction replay"))?;
                if first != second {
                    return Err(reconciliation_error("nondeterministic prediction"));
                }
                first
            }
            Err(error) => format!("error:{:?}\n", error.code()).into_bytes(),
        };
        prediction_digest_input.extend_from_slice(
            &u64::try_from(prediction_bytes.len())
                .map_err(|_| resource_limit("prediction bytes"))?
                .to_be_bytes(),
        );
        prediction_digest_input.extend_from_slice(&prediction_bytes);
    }

    let intent_strata = intent_strata.into_values().collect::<Vec<_>>();
    let slot_strata = slot_strata.into_values().collect::<Vec<_>>();
    let records_observed = usize_to_u64(rows.len(), "records")?;
    let intent_strata_total = intent_strata.iter().map(|value| value.total).sum();
    let slot_strata_total = slot_strata.iter().map(|value| value.total).sum();
    let value_accounted =
        slot_metrics.value_exact + slot_metrics.value_incorrect + slot_metrics.missing;
    let span_accounted =
        slot_metrics.span_exact + slot_metrics.span_incorrect + slot_metrics.missing;
    let reconciliation = Reconciliation {
        records_expected: RECORDS_PER_SPLIT,
        records_observed,
        outcome_total: outcomes.total(),
        intent_strata_total,
        slots_expected: slot_metrics.total,
        slot_strata_total,
        value_accounted,
        span_accounted,
        complete: records_observed == RECORDS_PER_SPLIT
            && outcomes.total() == records_observed
            && intent_strata_total == records_observed
            && slot_strata_total == slot_metrics.total
            && value_accounted == slot_metrics.total
            && span_accounted == slot_metrics.total,
    };
    if !reconciliation.complete {
        return Err(reconciliation_error("aggregate denominator mismatch"));
    }

    Ok(IntentEvaluationReport {
        schema_version: 1,
        runner_id: RUNNER_ID,
        metric_specification: METRIC_SPEC,
        dataset: DatasetIdentity {
            dataset_id: DATASET_ID,
            source_id: SOURCE_ID,
            corpus_version: CORPUS_VERSION,
            generator_id: GENERATOR_ID,
            oracle_origin: ORACLE_ORIGIN,
            locale: LOCALE,
            split: split.code(),
            claim_scope: CLAIM_SCOPE,
            physical_sha256: split.sha256(),
            records: records_observed,
        },
        recognizer: RecognizerIdentity {
            schema_id: INTENT_SCHEMA_ID,
            algorithm_id: INTENT_ALGORITHM_ID,
            configuration_id: INTENT_CONFIGURATION_ID,
            package_sha256: INTENT_PACKAGE_SHA256,
            package_manifest_sha256: INTENT_MANIFEST_SHA256,
            projection_id: PROJECTION_ID,
            projection_sha256: PROJECTION_SHA256,
        },
        results: EvaluationResults {
            exact_semantics: FractionMetric {
                numerator: exact_semantics,
                denominator: records_observed,
            },
            outcomes,
            slots: slot_metrics,
            intent_strata,
            slot_strata,
            prediction_digest_sha256: sha256_hex(&prediction_digest_input).map_err(|error| {
                map_data_error(IntentEvaluationErrorCode::Reconciliation, error)
            })?,
            reconciliation,
        },
        limitations: LIMITATIONS.to_vec(),
    })
}

pub fn evaluate_to_path(root: &Path, split: EvaluationSplit, output: &Path) -> Result<()> {
    let bytes = evaluate(root, split)?.canonical_bytes()?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)
        .map_err(|error| output_error("create evaluation report", error))?;
    file.write_all(&bytes)
        .map_err(|error| output_error("write evaluation report", error))?;
    file.sync_all()
        .map_err(|error| output_error("sync evaluation report", error))
}

pub fn evaluate_cli<I, T>(arguments: I) -> Result<Vec<u8>>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let arguments = arguments.into_iter().map(Into::into).collect::<Vec<_>>();
    let mut root = None;
    let mut split = None;
    let mut output = None;
    let mut index = 1_usize;
    while index < arguments.len() {
        let option = arguments[index]
            .to_str()
            .ok_or_else(|| invalid_arguments("non-UTF-8 option"))?;
        index = index
            .checked_add(1)
            .ok_or_else(|| invalid_arguments("argument index"))?;
        let value = arguments
            .get(index)
            .ok_or_else(|| invalid_arguments(format!("missing value for {option}")))?;
        match option {
            "--root" if root.is_none() => root = Some(PathBuf::from(value)),
            "--split" if split.is_none() => {
                split = Some(
                    value
                        .to_str()
                        .and_then(EvaluationSplit::parse)
                        .ok_or_else(|| invalid_arguments("invalid split"))?,
                );
            }
            "--output" if output.is_none() => output = Some(PathBuf::from(value)),
            "--root" | "--split" | "--output" => {
                return Err(invalid_arguments(format!("duplicate option {option}")));
            }
            _ => return Err(invalid_arguments(format!("unknown option {option}"))),
        }
        index = index
            .checked_add(1)
            .ok_or_else(|| invalid_arguments("argument index"))?;
    }
    evaluate_to_path(
        &root.ok_or_else(|| invalid_arguments("missing --root"))?,
        split.ok_or_else(|| invalid_arguments("missing --split"))?,
        &output.ok_or_else(|| invalid_arguments("missing --output"))?,
    )?;
    Ok(b"INTENT_EVALUATE_PASS\n".to_vec())
}

fn load_projection(root: &Path) -> Result<Projection> {
    let bytes = read_bounded_root_file(root, PROJECTION_PATH, MAX_PROJECTION_BYTES)
        .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidProjection, error))?;
    if sha256_hex(&bytes)
        .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidProjection, error))?
        != PROJECTION_SHA256
    {
        return Err(invalid_projection("projection SHA-256"));
    }
    let value = parse_strict_json(&bytes, "P09 oracle projection")
        .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidProjection, error))?;
    let mut projection: Projection =
        serde_json::from_value(value).map_err(|_| invalid_projection("projection shape"))?;
    validate_projection(&projection)?;
    for intent in &mut projection.intent_projections {
        intent.slots.sort_by(|left, right| {
            (&left.slot_id, &left.role, left.occurrence).cmp(&(
                &right.slot_id,
                &right.role,
                right.occurrence,
            ))
        });
    }
    projection
        .intent_projections
        .sort_by(|left, right| left.intent_id.cmp(&right.intent_id));
    Ok(projection)
}

fn validate_projection(projection: &Projection) -> Result<()> {
    if projection.schema_version != 1
        || projection.projection_id != PROJECTION_ID
        || projection.schema_id != INTENT_SCHEMA_ID
        || projection.source.source_id != SOURCE_ID
        || projection.source.corpus_version != CORPUS_VERSION
        || projection.source.generator_id != GENERATOR_ID
        || projection.source.oracle_origin != ORACLE_ORIGIN
        || projection.source.specification_sha256 != SPECIFICATION_SHA256
        || projection.source.generator_sha256 != GENERATOR_SHA256
        || projection.source.development_sha256 != DEVELOPMENT_SHA256
        || projection.source.development_records != RECORDS_PER_SPLIT
        || projection.source.claim_scope != CLAIM_SCOPE
        || projection.span_derivation.algorithm != "p02-generator-parameter-exact-utf8-substring-v1"
        || !projection.span_derivation.require_unique_match
        || projection.span_derivation.source_output_allowed
        || projection.span_derivation.normalization_allowed
        || projection.intent_projections.len() != 20
    {
        return Err(invalid_projection("projection identity"));
    }
    let mut external = BTreeSet::new();
    let mut internal = BTreeSet::new();
    for intent in &projection.intent_projections {
        IntentId::new(&intent.intent_id).map_err(|_| invalid_projection("intent ID"))?;
        if !external.insert(intent.external_intent.as_str())
            || !internal.insert(intent.intent_id.as_str())
            || generator_contract(&intent.external_intent).is_none()
            || intent.slots.is_empty()
        {
            return Err(invalid_projection("intent projection inventory"));
        }
        let mut slots = BTreeSet::new();
        for slot in &intent.slots {
            SlotId::new(&slot.slot_id).map_err(|_| invalid_projection("slot ID"))?;
            if slot.expected_slot_id.is_empty()
                || !matches!(slot.expected_kind.as_str(), "entity" | "integer" | "text")
                || slot.role.is_empty()
                || !slots.insert((&slot.slot_id, &slot.role, slot.occurrence))
                || !matches!(
                    slot.parameter.as_str(),
                    "target" | "target2" | "position" | "minutes" | "scope" | "message"
                )
                || !valid_transform(slot)
            {
                return Err(invalid_projection("slot projection"));
            }
        }
    }
    Ok(())
}

fn valid_transform(slot: &SlotProjection) -> bool {
    matches!(
        (slot.expected_kind.as_str(), slot.transform.as_str()),
        ("entity" | "text", "evidence_text") | ("integer", "integer" | "minutes_to_seconds")
    )
}

fn load_dataset(
    root: &Path,
    split: EvaluationSplit,
    projection: &Projection,
) -> Result<Vec<CorpusRow>> {
    let bytes = read_bounded_root_file(root, split.path(), MAX_DATASET_BYTES)
        .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidDataset, error))?;
    if sha256_hex(&bytes)
        .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidDataset, error))?
        != split.sha256()
    {
        return Err(invalid_dataset("dataset SHA-256"));
    }
    let mut rows = Vec::new();
    let mut offset = 0_usize;
    for line in bytes.split(|byte| *byte == b'\n') {
        if line.is_empty() {
            if offset == bytes.len() || offset + 1 == bytes.len() {
                break;
            }
            return Err(invalid_dataset("blank dataset row"));
        }
        if line.len() > MAX_RECORD_BYTES || rows.len() >= MAX_RECORDS {
            return Err(resource_limit("dataset row limits"));
        }
        let value = parse_strict_json(line, "P09 semantic row")
            .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidDataset, error))?;
        let row: CorpusRow =
            serde_json::from_value(value).map_err(|_| invalid_dataset("semantic row shape"))?;
        rows.push(row);
        offset = offset
            .checked_add(line.len() + 1)
            .ok_or_else(|| resource_limit("dataset offset"))?;
    }
    if rows.len() != RECORDS_PER_SPLIT as usize {
        return Err(invalid_dataset("dataset record count"));
    }
    validate_rows(&rows, split, projection)?;
    Ok(rows)
}

fn validate_rows(
    rows: &[CorpusRow],
    split: EvaluationSplit,
    projection: &Projection,
) -> Result<()> {
    let allowed = projection
        .intent_projections
        .iter()
        .map(|intent| intent.external_intent.as_str())
        .collect::<BTreeSet<_>>();
    let mut cases = BTreeSet::new();
    let mut counts = BTreeMap::<&str, u64>::new();
    for row in rows {
        let contract = generator_contract(&row.expected.intent)
            .ok_or_else(|| invalid_dataset("unknown external intent"))?;
        let index = row_index(row, split, contract)?;
        let expected_session = format!("p02:{}_{}_{index:03}", split.code(), contract.slug);
        let expected_family = format!("{}-{}-family-v1", split.code(), contract.slug);
        if row.schema_version != 1
            || row.generator_record_id != format!("generator-{}", row.case_id)
            || !is_sha256(&row.canonical_semantic_id)
            || row.source_id != SOURCE_ID
            || row.corpus_version != CORPUS_VERSION
            || row.generator_id != GENERATOR_ID
            || row.oracle_origin != ORACLE_ORIGIN
            || row.license != SOURCE_LICENSE
            || row.locale != LOCALE
            || row.split != split.code()
            || sha256_hex(row.utterance.as_bytes())
                .map_err(|error| map_data_error(IntentEvaluationErrorCode::InvalidDataset, error))?
                != row.utterance_sha256
            || row.context.catalog_generation != 1
            || row.context.session_snapshot_id != expected_session
            || row.dimensions.source != SOURCE_ID
            || row.dimensions.family != expected_family
            || row.dimensions.intent != row.expected.intent
            || row.dimensions.domain.is_empty()
            || row.dimensions.slot_kind.is_empty()
            || row.dimensions.graph_shape.is_empty()
            || row.dimensions.outcome != "plan"
            || row.dimensions.ambiguity != "unambiguous"
            || row.dimensions.noise != "clean_text"
            || !matches!(
                row.dimensions.target_cardinality.as_str(),
                "zero" | "one" | "two"
            )
            || row.expected.outcome != "plan"
            || row.expected.catalog_generation != 1
            || row.expected.nodes.is_empty()
            || !allowed.contains(row.expected.intent.as_str())
            || !cases.insert(row.case_id.as_str())
        {
            return Err(invalid_dataset("semantic row identity"));
        }
        validate_expected_plan(&row.expected)?;
        *counts.entry(&row.expected.intent).or_default() += 1;
    }
    if counts.len() != allowed.len()
        || counts
            .iter()
            .any(|(intent, count)| !allowed.contains(*intent) || *count != RECORDS_PER_INTENT)
    {
        return Err(invalid_dataset("intent stratum count"));
    }
    Ok(())
}

fn validate_expected_plan(plan: &ExpectedPlan) -> Result<()> {
    let mut node_ids = BTreeSet::new();
    for node in &plan.nodes {
        if node.id.is_empty()
            || node.capability.is_empty()
            || node.operation.is_empty()
            || node.slots.is_empty()
            || !node_ids.insert(node.id.as_str())
        {
            return Err(invalid_dataset("expected node"));
        }
        let mut slot_values = BTreeSet::new();
        for slot in &node.slots {
            let value = canonical_json(&slot.value, "expected slot value").map_err(|error| {
                map_data_error(IntentEvaluationErrorCode::InvalidDataset, error)
            })?;
            if slot.id.is_empty()
                || !matches!(slot.kind.as_str(), "entity" | "integer" | "text")
                || !slot_values.insert((slot.id.as_str(), slot.kind.as_str(), value))
            {
                return Err(invalid_dataset("expected slot"));
            }
        }
    }
    for relation in &plan.relations {
        if !node_ids.contains(relation.from.as_str())
            || !node_ids.contains(relation.to.as_str())
            || relation.from == relation.to
            || relation.kind != "precedes"
        {
            return Err(invalid_dataset("expected relation"));
        }
    }
    Ok(())
}

fn row_index(row: &CorpusRow, split: EvaluationSplit, contract: GeneratorContract) -> Result<u64> {
    let prefix = format!("p02-v1-{}-{}-", split.code(), contract.slug);
    let suffix = row
        .case_id
        .strip_prefix(&prefix)
        .ok_or_else(|| invalid_dataset("case ID prefix"))?;
    if suffix.len() != 3 || !suffix.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(invalid_dataset("case ID index"));
    }
    let index = suffix
        .parse::<u64>()
        .map_err(|_| invalid_dataset("case index"))?;
    if !(1..=RECORDS_PER_INTENT).contains(&index) {
        return Err(invalid_dataset("case index range"));
    }
    Ok(index)
}

fn render_oracle(
    row: &CorpusRow,
    split: EvaluationSplit,
    projection: &IntentProjection,
) -> Result<RenderedOracle> {
    let contract = generator_contract(&projection.external_intent)
        .ok_or_else(|| invalid_projection("generator contract"))?;
    let index = row_index(row, split, contract)?;
    let target = target_value(contract.target_noun, index, 0)?;
    let target2 = target_value(contract.target_noun, index, 120)?;
    let area =
        AREAS[usize::try_from(index - 1).map_err(|_| resource_limit("area index"))? % AREAS.len()];
    let mut parameters = BTreeMap::new();
    parameters.insert("target", target);
    parameters.insert("target2", target2);
    parameters.insert("position", ((index - 1) % 101).to_string());
    parameters.insert("minutes", index.to_string());
    parameters.insert("scope", format!("{index:03}"));
    parameters.insert("message", format!("confirmacao {index} do setor {area}"));
    let template = match split {
        EvaluationSplit::Train => contract.train_template,
        EvaluationSplit::Development => contract.development_template,
    };
    let rendered = render_template(template, &parameters)?;
    if rendered.utterance.as_bytes() != row.utterance.as_bytes() {
        return Err(invalid_dataset("generator rendering mismatch"));
    }
    Ok(rendered)
}

fn target_value(noun: &str, index: u64, offset: u64) -> Result<String> {
    let adjusted = ((index - 1)
        .checked_add(offset)
        .ok_or_else(|| resource_limit("adjusted target index"))?
        % 240)
        + 1;
    let area_index = usize::try_from(adjusted - 1)
        .map_err(|_| resource_limit("target area index"))?
        % AREAS.len();
    let number = ((adjusted - 1) / AREAS.len() as u64) + 1;
    Ok(format!("{noun} {number} do setor {}", AREAS[area_index]))
}

fn render_template(template: &str, parameters: &BTreeMap<&str, String>) -> Result<RenderedOracle> {
    let mut utterance = String::new();
    let mut rendered = BTreeMap::new();
    let mut cursor = 0_usize;
    while let Some(relative) = template[cursor..].find("%{") {
        let start = cursor + relative;
        utterance.push_str(&template[cursor..start]);
        let name_start = start + 2;
        let close = template[name_start..]
            .find('}')
            .map(|relative| name_start + relative)
            .ok_or_else(|| invalid_projection("unterminated generator placeholder"))?;
        let name = &template[name_start..close];
        let text = parameters
            .get(name)
            .ok_or_else(|| invalid_projection("unknown generator parameter"))?;
        let begin_byte =
            u32::try_from(utterance.len()).map_err(|_| resource_limit("parameter begin"))?;
        utterance.push_str(text);
        let end_byte =
            u32::try_from(utterance.len()).map_err(|_| resource_limit("parameter end"))?;
        if rendered
            .insert(
                name.to_owned(),
                RenderedParameter {
                    text: text.clone(),
                    begin_byte,
                    end_byte,
                },
            )
            .is_some()
        {
            return Err(invalid_projection("duplicate generator parameter"));
        }
        cursor = close + 1;
    }
    utterance.push_str(&template[cursor..]);
    Ok(RenderedOracle {
        utterance,
        parameters: rendered,
    })
}

fn expected_bindings(
    projection: &IntentProjection,
    oracle: &RenderedOracle,
) -> Result<Vec<ExpectedBinding>> {
    projection
        .slots
        .iter()
        .map(|slot| {
            let parameter = oracle
                .parameters
                .get(&slot.parameter)
                .ok_or_else(|| invalid_projection("unused slot parameter"))?;
            let value_kind = projected_value_kind(slot)?;
            let value = match slot.transform.as_str() {
                "evidence_text" => ExpectedValue::Text(parameter.text.clone()),
                "integer" => ExpectedValue::Integer(
                    parameter
                        .text
                        .parse::<i64>()
                        .map_err(|_| invalid_projection("integer parameter"))?,
                ),
                "minutes_to_seconds" => {
                    let minutes = parameter
                        .text
                        .parse::<i64>()
                        .map_err(|_| invalid_projection("minutes parameter"))?;
                    ExpectedValue::Integer(
                        minutes
                            .checked_mul(60)
                            .ok_or_else(|| resource_limit("minutes transform"))?,
                    )
                }
                _ => return Err(invalid_projection("slot transform")),
            };
            Ok(ExpectedBinding {
                key: SlotKey {
                    intent_id: projection.intent_id.clone(),
                    slot_id: slot.slot_id.clone(),
                    role: slot.role.clone(),
                    occurrence: slot.occurrence,
                    value_kind: value_kind.to_owned(),
                },
                value,
                begin_byte: parameter.begin_byte,
                end_byte: parameter.end_byte,
            })
        })
        .collect()
}

fn projected_value_kind(slot: &SlotProjection) -> Result<&'static str> {
    match (slot.expected_kind.as_str(), slot.transform.as_str()) {
        ("entity", "evidence_text") => Ok("mention"),
        ("text", "evidence_text") => Ok("text"),
        ("integer", "integer" | "minutes_to_seconds") => Ok("integer"),
        _ => Err(invalid_projection("projected value kind")),
    }
}

fn reconcile_expected_plan(row: &CorpusRow, projection: &IntentProjection) -> Result<()> {
    let mut logical = BTreeSet::new();
    for node in &row.expected.nodes {
        for slot in &node.slots {
            let value = canonical_json(&slot.value, "expected plan slot").map_err(|error| {
                map_data_error(IntentEvaluationErrorCode::InvalidDataset, error)
            })?;
            logical.insert((slot.id.as_str(), slot.kind.as_str(), value));
        }
    }
    let mut actual = BTreeMap::<(&str, &str), u64>::new();
    for (id, kind, _) in logical {
        *actual.entry((id, kind)).or_default() += 1;
    }
    let mut expected = BTreeMap::<(&str, &str), u64>::new();
    for slot in &projection.slots {
        *expected
            .entry((&slot.expected_slot_id, &slot.expected_kind))
            .or_default() += 1;
    }
    if actual != expected {
        return Err(reconciliation_error("plan/projection slot inventory"));
    }
    Ok(())
}

fn projection_slot_key(intent: &IntentProjection, slot: &SlotProjection) -> SlotKey {
    SlotKey {
        intent_id: intent.intent_id.clone(),
        slot_id: slot.slot_id.clone(),
        role: slot.role.clone(),
        occurrence: slot.occurrence,
        value_kind: projected_value_kind(slot).unwrap_or("invalid").to_owned(),
    }
}

fn predicted_slot_key(candidate: &IntentMatch, binding: &SlotBinding) -> SlotKey {
    SlotKey {
        intent_id: candidate.intent().as_str().to_owned(),
        slot_id: binding.id().as_str().to_owned(),
        role: binding.role().to_owned(),
        occurrence: binding.occurrence(),
        value_kind: binding.value().kind().to_owned(),
    }
}

fn expected_candidate<'a>(
    outcome: &'a RecognitionOutcome,
    expected_intent: &str,
) -> Option<&'a IntentMatch> {
    match outcome {
        RecognitionOutcome::Match(candidate) => {
            (candidate.intent().as_str() == expected_intent).then_some(candidate)
        }
        RecognitionOutcome::Clarification(value) => {
            let mut candidates = value
                .alternatives()
                .iter()
                .filter(|candidate| candidate.intent().as_str() == expected_intent);
            let first = candidates.next()?;
            candidates.next().is_none().then_some(first)
        }
        RecognitionOutcome::Abstention(_) => None,
    }
}

fn value_matches(
    predicted: &SlotBinding,
    expected: &ExpectedBinding,
    source: &RequestText,
) -> Result<bool> {
    match (predicted.value(), &expected.value) {
        (IntentSlotValue::Mention(span), ExpectedValue::Text(value))
        | (IntentSlotValue::Text(span), ExpectedValue::Text(value)) => span
            .slice(source)
            .map(|actual| actual == value)
            .map_err(|_| reconciliation_error("predicted text span")),
        (IntentSlotValue::Integer(actual), ExpectedValue::Integer(expected)) => {
            Ok(actual == expected)
        }
        _ => Ok(false),
    }
}

fn generator_contract(external_intent: &str) -> Option<GeneratorContract> {
    // These bytes are copied exactly from the pinned P02 specification.
    const CONTRACTS: [GeneratorContract; 20] = [
        GeneratorContract {
            external_intent: "HassTurnOff",
            slug: "hass_turn_off",
            target_noun: "interruptor",
            train_template: "desligue %{target}",
            development_template: "por favor desligue %{target}",
        },
        GeneratorContract {
            external_intent: "HassTurnOn",
            slug: "hass_turn_on",
            target_noun: "luz",
            train_template: "ligue %{target} e %{target2}",
            development_template: "por favor ligue %{target} junto com %{target2}",
        },
        GeneratorContract {
            external_intent: "HassToggle",
            slug: "hass_toggle",
            target_noun: "ventilador",
            train_template: "altere %{target}",
            development_template: "por favor mude o estado de %{target}",
        },
        GeneratorContract {
            external_intent: "HassGetState",
            slug: "hass_get_state",
            target_noun: "sensor",
            train_template: "consulte %{target}",
            development_template: "qual é o estado de %{target}",
        },
        GeneratorContract {
            external_intent: "HassNevermind",
            slug: "hass_nevermind",
            target_noun: "pedido",
            train_template: "cancele %{target}",
            development_template: "deixe %{target} para lá",
        },
        GeneratorContract {
            external_intent: "HassSetPosition",
            slug: "hass_set_position",
            target_noun: "persiana",
            train_template: "ajuste %{target} para %{position} por cento",
            development_template: "coloque %{target} em %{position} por cento",
        },
        GeneratorContract {
            external_intent: "HassStopMoving",
            slug: "hass_stop_moving",
            target_noun: "cortina",
            train_template: "pare %{target}",
            development_template: "interrompa o movimento de %{target}",
        },
        GeneratorContract {
            external_intent: "HassStartTimer",
            slug: "hass_start_timer",
            target_noun: "temporizador",
            train_template: "inicie %{target} por %{minutes} minutos e consulte o estado depois",
            development_template: "comece %{target} com %{minutes} minutos e então verifique o estado",
        },
        GeneratorContract {
            external_intent: "HassCancelTimer",
            slug: "hass_cancel_timer",
            target_noun: "temporizador",
            train_template: "cancele %{target}",
            development_template: "por favor cancele %{target}",
        },
        GeneratorContract {
            external_intent: "HassCancelAllTimers",
            slug: "hass_cancel_all_timers",
            target_noun: "temporizador",
            train_template: "cancele todos os temporizadores do setor %{scope}",
            development_template: "por favor remova cada temporizador do setor %{scope}",
        },
        GeneratorContract {
            external_intent: "HassIncreaseTimer",
            slug: "hass_increase_timer",
            target_noun: "temporizador",
            train_template: "aumente %{target} em %{minutes} minutos",
            development_template: "adicione %{minutes} minutos a %{target}",
        },
        GeneratorContract {
            external_intent: "HassDecreaseTimer",
            slug: "hass_decrease_timer",
            target_noun: "temporizador",
            train_template: "reduza %{target} em %{minutes} minutos",
            development_template: "retire %{minutes} minutos de %{target}",
        },
        GeneratorContract {
            external_intent: "HassPauseTimer",
            slug: "hass_pause_timer",
            target_noun: "temporizador",
            train_template: "pause %{target}",
            development_template: "por favor pause %{target}",
        },
        GeneratorContract {
            external_intent: "HassUnpauseTimer",
            slug: "hass_unpause_timer",
            target_noun: "temporizador",
            train_template: "continue %{target}",
            development_template: "por favor retome %{target}",
        },
        GeneratorContract {
            external_intent: "HassTimerStatus",
            slug: "hass_timer_status",
            target_noun: "temporizador",
            train_template: "consulte %{target}",
            development_template: "qual é o estado de %{target}",
        },
        GeneratorContract {
            external_intent: "HassGetCurrentDate",
            slug: "hass_get_current_date",
            target_noun: "painel",
            train_template: "mostre a data em %{target}",
            development_template: "qual é a data para %{target}",
        },
        GeneratorContract {
            external_intent: "HassGetCurrentTime",
            slug: "hass_get_current_time",
            target_noun: "relógio",
            train_template: "mostre a hora em %{target}",
            development_template: "qual é o horário para %{target}",
        },
        GeneratorContract {
            external_intent: "HassRespond",
            slug: "hass_respond",
            target_noun: "resposta",
            train_template: "responda %{message}",
            development_template: "diga como resposta %{message}",
        },
        GeneratorContract {
            external_intent: "HassBroadcast",
            slug: "hass_broadcast",
            target_noun: "alto-falante",
            train_template: "transmita %{message} em %{target}",
            development_template: "envie o aviso %{message} para %{target}",
        },
        GeneratorContract {
            external_intent: "HassClimateGetTemperature",
            slug: "hass_climate_get_temperature",
            target_noun: "termômetro",
            train_template: "consulte a temperatura em %{target}",
            development_template: "qual é a temperatura de %{target}",
        },
    ];
    CONTRACTS
        .iter()
        .copied()
        .find(|contract| contract.external_intent == external_intent)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn usize_to_u64(value: usize, context: &'static str) -> Result<u64> {
    u64::try_from(value).map_err(|_| resource_limit(context))
}

fn map_data_error(code: IntentEvaluationErrorCode, error: DataError) -> IntentEvaluationError {
    let code = if error.code() == DataErrorCode::ResourceLimit {
        IntentEvaluationErrorCode::ResourceLimit
    } else {
        code
    };
    IntentEvaluationError::new(code, error.context())
}

fn evaluation_error(
    code: IntentEvaluationErrorCode,
    context: impl Into<String>,
) -> IntentEvaluationError {
    IntentEvaluationError::new(code, context)
}

fn invalid_arguments(context: impl Into<String>) -> IntentEvaluationError {
    evaluation_error(IntentEvaluationErrorCode::InvalidArguments, context)
}

fn invalid_dataset(context: impl Into<String>) -> IntentEvaluationError {
    evaluation_error(IntentEvaluationErrorCode::InvalidDataset, context)
}

fn invalid_projection(context: impl Into<String>) -> IntentEvaluationError {
    evaluation_error(IntentEvaluationErrorCode::InvalidProjection, context)
}

fn reconciliation_error(context: impl Into<String>) -> IntentEvaluationError {
    evaluation_error(IntentEvaluationErrorCode::Reconciliation, context)
}

fn resource_limit(context: impl Into<String>) -> IntentEvaluationError {
    evaluation_error(IntentEvaluationErrorCode::ResourceLimit, context)
}

fn output_error(context: &str, error: std::io::Error) -> IntentEvaluationError {
    evaluation_error(
        IntentEvaluationErrorCode::OutputFailure,
        format!("{context}: {:?}", error.kind()),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .expect("repository root")
    }

    #[test]
    fn all_train_records_are_exact_before_candidate_freeze() {
        let report = evaluate(&repository_root(), EvaluationSplit::Train).expect("train report");
        assert_eq!(
            report.results().exact_semantics(),
            &FractionMetric {
                numerator: 960,
                denominator: 960
            }
        );
        assert_eq!(report.results().slots().total(), 1_248);
        assert_eq!(report.results().slots().value_exact(), 1_248);
        assert_eq!(report.results().slots().span_exact(), 1_248);
        assert!(report.results().reconciliation().complete);
    }

    #[test]
    fn development_report_is_canonical_and_completely_reconciled() {
        let first =
            evaluate(&repository_root(), EvaluationSplit::Development).expect("development");
        let second =
            evaluate(&repository_root(), EvaluationSplit::Development).expect("development");
        assert_eq!(
            first.canonical_bytes().expect("first bytes"),
            second.canonical_bytes().expect("second bytes")
        );
        assert_eq!(first.results().exact_semantics().denominator(), 960);
        assert_eq!(first.results().slots().total(), 1_248);
        assert!(first.results().reconciliation().complete);
    }

    #[test]
    fn cli_rejects_heldout_and_incomplete_arguments() {
        assert_eq!(
            evaluate_cli([
                "intent-evaluate",
                "--root",
                ".",
                "--split",
                "heldout",
                "--output",
                "report.json",
            ])
            .expect_err("heldout")
            .code(),
            IntentEvaluationErrorCode::InvalidArguments
        );
        assert_eq!(
            evaluate_cli(["intent-evaluate"])
                .expect_err("missing")
                .code(),
            IntentEvaluationErrorCode::InvalidArguments
        );
    }
}
