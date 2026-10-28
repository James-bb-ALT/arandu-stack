use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
};

use intent_engine::{
    INTENT_ALGORITHM_ID, INTENT_CONFIGURATION_ID, INTENT_MANIFEST_SHA256, INTENT_PACKAGE_SHA256,
    INTENT_SCHEMA_ID,
};
use nlu_data::{canonical_json, sha256_hex};
use serde::Serialize;

use crate::{
    Result, catalog,
    engine_adapter::{
        COMPOSER_ALGORITHM_ID, COMPOSER_SCHEMA_ID, ComposerDisposition, Engines, PredictedOutcome,
        Prediction, RecognizerDisposition,
    },
    error::{invalid_arguments, output_error, reconciliation_error, resource_limit},
    oracle::{self, GoldCase, GoldOutcome, GoldSource, core_canonical_bytes},
};

const RUNNER_ID: &str = "plan-eval-v1";
const METRIC_SPECIFICATION: &str = "exact-p11-outcome-full-graph-and-canonical-bytes-v1";
const P02_SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const P11_SOURCE_ID: &str = "project-authored-synthetic-ptbr-p11-negation-v1";
const P02_PROJECTION_ID: &str = "p11-semantic-plan-oracle-projection-v1";
const P02_PROJECTION_SHA256: &str =
    "581b852b8cd2ec24b4c02b8c68914b27a928d953470a10665357e50425783f75";
const P09_PROJECTION_ID: &str = "p09-pre-resolution-oracle-projection-v1";
const P09_PROJECTION_SHA256: &str =
    "5f661bb96b85667a1d0c9ec4d85cb61c443e1b24ab4dfc6c1ab849c6df936ee4";
const P02_RECORDS: u64 = 960;
const P11_RECORDS: u64 = 3;
const TOTAL_RECORDS: u64 = P02_RECORDS + P11_RECORDS;
const MAX_REPORT_BYTES: usize = 256 * 1024;
const LIMITATIONS: [&str; 6] = [
    "project_authored_internal_conformance_only",
    "not_independent_language_accuracy",
    "p02_and_p11_sources_reported_separately",
    "no_heldout_access",
    "graph_execution_class_is_not_execution_authority",
    "no_acceptance_threshold_defined",
];
const GRAPH_MATCH_FIELDS: [&str; 13] = [
    "intent",
    "capability",
    "operation",
    "entity_slot_value",
    "integer_slot_value",
    "text_slot_value",
    "typed_evidence_span",
    "polarity",
    "relation_evidence",
    "independence",
    "argument_share",
    "execution_class",
    "canonical_bytes",
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

    pub(crate) const fn p02_path(self) -> &'static str {
        match self {
            Self::Train => "data/project-authored/p02-v1/train.jsonl",
            Self::Development => "data/project-authored/p02-v1/development.jsonl",
        }
    }

    pub(crate) const fn p02_sha256(self) -> &'static str {
        match self {
            Self::Train => "23d2bc8c8fcde80d1a9560d42219484bc34e9198c791ccadf5d4b56413a81b64",
            Self::Development => "75400570ddfc7196ed982da98dcf49c4e6bda5820200a2fefd93a8ea6f049161",
        }
    }

    pub(crate) const fn p11_path(self) -> &'static str {
        match self {
            Self::Train => "data/project-authored/p11-v1/train.jsonl",
            Self::Development => "data/project-authored/p11-v1/development.jsonl",
        }
    }

    pub(crate) const fn p11_sha256(self) -> &'static str {
        match self {
            Self::Train => "8b00c1af9aedb2ff6c946bf24c0a538b97ee8f9f882a4da901b91ba9f4432b8d",
            Self::Development => "daca8033117351caee07ac9697ab49877ca240f896985418c6c7325680772675",
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
pub struct SourceIdentity {
    source_id: &'static str,
    source_type: &'static str,
    corpus_version: &'static str,
    generator_id: &'static str,
    oracle_origin: &'static str,
    claim_scope: &'static str,
    split: &'static str,
    physical_sha256: &'static str,
    records: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ProjectionIdentity {
    pre_resolution_projection_id: &'static str,
    pre_resolution_projection_sha256: &'static str,
    plan_projection_id: &'static str,
    plan_projection_sha256: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RecognizerIdentity {
    schema_id: &'static str,
    algorithm_id: &'static str,
    configuration_id: &'static str,
    package_sha256: &'static str,
    package_manifest_sha256: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ComposerIdentity {
    schema_id: &'static str,
    algorithm_id: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ComparisonContract {
    outcome_fields: Vec<&'static str>,
    full_graph_fields: Vec<&'static str>,
    canonical_bytes_required: bool,
    acceptance_threshold: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize)]
pub struct RecognizerOutcomeCounts {
    matches: u64,
    clarifications: u64,
    abstentions: u64,
    errors: u64,
}

impl RecognizerOutcomeCounts {
    fn increment(&mut self, disposition: RecognizerDisposition) {
        match disposition {
            RecognizerDisposition::Match => self.matches += 1,
            RecognizerDisposition::Clarification => self.clarifications += 1,
            RecognizerDisposition::Abstention => self.abstentions += 1,
            RecognizerDisposition::Error => self.errors += 1,
        }
    }

    const fn total(&self) -> u64 {
        self.matches + self.clarifications + self.abstentions + self.errors
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize)]
pub struct ComposerOutcomeCounts {
    plans: u64,
    clarifications: u64,
    abstentions: u64,
    not_run: u64,
    errors: u64,
}

impl ComposerOutcomeCounts {
    fn increment(&mut self, disposition: ComposerDisposition) {
        match disposition {
            ComposerDisposition::Plan => self.plans += 1,
            ComposerDisposition::Clarification => self.clarifications += 1,
            ComposerDisposition::Abstention => self.abstentions += 1,
            ComposerDisposition::NotRun => self.not_run += 1,
            ComposerDisposition::Error => self.errors += 1,
        }
    }

    const fn total(&self) -> u64 {
        self.plans + self.clarifications + self.abstentions + self.not_run + self.errors
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SourceCounts {
    p02: u64,
    p11_negation: u64,
    total: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize)]
pub struct ExpectedOutcomeCounts {
    plans: u64,
    abstentions: u64,
}

impl ExpectedOutcomeCounts {
    const fn total(&self) -> u64 {
        self.plans + self.abstentions
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct GraphStratum {
    source: &'static str,
    stratum: String,
    graph_shape: String,
    expected_outcome: &'static str,
    total: u64,
    intent_exact: u64,
    outcome_exact: u64,
    graph_exact: u64,
    canonical_bytes_exact: u64,
    exact_semantics: u64,
    recognizer_outcomes: RecognizerOutcomeCounts,
    composer_outcomes: ComposerOutcomeCounts,
}

#[derive(Clone, Debug, Default)]
struct GraphStratumAccumulator {
    total: u64,
    intent_exact: u64,
    outcome_exact: u64,
    graph_exact: u64,
    canonical_bytes_exact: u64,
    exact_semantics: u64,
    recognizer_outcomes: RecognizerOutcomeCounts,
    composer_outcomes: ComposerOutcomeCounts,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Reconciliation {
    records_expected: u64,
    records_observed: u64,
    source_total: u64,
    expected_outcome_total: u64,
    recognizer_outcome_total: u64,
    composer_outcome_total: u64,
    stratum_total: u64,
    plan_graph_denominator: u64,
    graph_comparisons_accounted: u64,
    complete: bool,
}

impl Reconciliation {
    #[must_use]
    pub const fn complete(&self) -> bool {
        self.complete
    }

    #[must_use]
    pub const fn records_observed(&self) -> u64 {
        self.records_observed
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvaluationResults {
    source_counts: SourceCounts,
    expected_outcomes: ExpectedOutcomeCounts,
    recognizer_outcomes: RecognizerOutcomeCounts,
    composer_outcomes: ComposerOutcomeCounts,
    intent_exact: FractionMetric,
    outcome_exact: FractionMetric,
    graph_exact: FractionMetric,
    canonical_bytes_exact: FractionMetric,
    exact_semantics: FractionMetric,
    graph_strata: Vec<GraphStratum>,
    prediction_digest_sha256: String,
    reconciliation: Reconciliation,
}

impl EvaluationResults {
    #[must_use]
    pub const fn exact_semantics(&self) -> &FractionMetric {
        &self.exact_semantics
    }

    #[must_use]
    pub const fn graph_exact(&self) -> &FractionMetric {
        &self.graph_exact
    }

    #[must_use]
    pub const fn reconciliation(&self) -> &Reconciliation {
        &self.reconciliation
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct PlanEvaluationReport {
    schema_version: u32,
    runner_id: &'static str,
    metric_specification: &'static str,
    split: &'static str,
    sources: Vec<SourceIdentity>,
    projections: ProjectionIdentity,
    recognizer: RecognizerIdentity,
    composer: ComposerIdentity,
    comparison_contract: ComparisonContract,
    results: EvaluationResults,
    limitations: Vec<&'static str>,
}

impl PlanEvaluationReport {
    #[must_use]
    pub const fn results(&self) -> &EvaluationResults {
        &self.results
    }

    pub fn canonical_bytes(&self) -> Result<Vec<u8>> {
        let value = serde_json::to_value(self)
            .map_err(|_| reconciliation_error("evaluation report value"))?;
        let mut bytes = canonical_json(&value, "P11 plan evaluation report")
            .map_err(|_| reconciliation_error("evaluation report encoding"))?;
        bytes.push(b'\n');
        if bytes.len() > MAX_REPORT_BYTES {
            return Err(resource_limit("evaluation report bytes"));
        }
        Ok(bytes)
    }
}

pub fn evaluate(root: &Path, split: EvaluationSplit) -> Result<PlanEvaluationReport> {
    let cases = oracle::load(root, split)?;
    let engines = Engines::new()?;
    let mut expected_outcomes = ExpectedOutcomeCounts::default();
    let mut recognizer_outcomes = RecognizerOutcomeCounts::default();
    let mut composer_outcomes = ComposerOutcomeCounts::default();
    let mut strata =
        BTreeMap::<(GoldSource, String, String, &'static str), GraphStratumAccumulator>::new();
    let mut intent_exact = 0_u64;
    let mut outcome_exact = 0_u64;
    let mut graph_exact = 0_u64;
    let mut canonical_exact = 0_u64;
    let mut exact_semantics = 0_u64;
    let mut graph_comparisons_accounted = 0_u64;
    let mut prediction_digest_input = Vec::new();

    for case in &cases {
        match &case.expected {
            GoldOutcome::Plan(_) => expected_outcomes.plans += 1,
            GoldOutcome::Abstention(_) => expected_outcomes.abstentions += 1,
        }
        let snapshot = catalog::build(case)?;
        let prediction = engines.evaluate(case, &snapshot)?;
        recognizer_outcomes.increment(prediction.recognizer);
        composer_outcomes.increment(prediction.composer);
        append_prediction(&mut prediction_digest_input, &prediction)?;

        let comparison = compare(case, &prediction)?;
        intent_exact += u64::from(comparison.intent_exact);
        outcome_exact += u64::from(comparison.outcome_exact);
        graph_exact += u64::from(comparison.graph_exact);
        canonical_exact += u64::from(comparison.canonical_exact);
        exact_semantics += u64::from(comparison.exact_semantics);
        if matches!(&case.expected, GoldOutcome::Plan(_)) {
            graph_comparisons_accounted += 1;
        }

        let key = (
            case.source,
            case.stratum.clone(),
            case.graph_shape.clone(),
            case.expected.code(),
        );
        let stratum = strata.entry(key).or_default();
        stratum.total += 1;
        stratum.intent_exact += u64::from(comparison.intent_exact);
        stratum.outcome_exact += u64::from(comparison.outcome_exact);
        stratum.graph_exact += u64::from(comparison.graph_exact);
        stratum.canonical_bytes_exact += u64::from(comparison.canonical_exact);
        stratum.exact_semantics += u64::from(comparison.exact_semantics);
        stratum.recognizer_outcomes.increment(prediction.recognizer);
        stratum.composer_outcomes.increment(prediction.composer);
    }

    let graph_strata = strata
        .into_iter()
        .map(
            |((source, stratum, graph_shape, expected_outcome), value)| GraphStratum {
                source: source.code(),
                stratum,
                graph_shape,
                expected_outcome,
                total: value.total,
                intent_exact: value.intent_exact,
                outcome_exact: value.outcome_exact,
                graph_exact: value.graph_exact,
                canonical_bytes_exact: value.canonical_bytes_exact,
                exact_semantics: value.exact_semantics,
                recognizer_outcomes: value.recognizer_outcomes,
                composer_outcomes: value.composer_outcomes,
            },
        )
        .collect::<Vec<_>>();
    let records_observed =
        u64::try_from(cases.len()).map_err(|_| resource_limit("record count"))?;
    let stratum_total = graph_strata.iter().map(|stratum| stratum.total).sum();
    let source_counts = source_counts(&cases)?;
    let reconciliation = Reconciliation {
        records_expected: TOTAL_RECORDS,
        records_observed,
        source_total: source_counts.total,
        expected_outcome_total: expected_outcomes.total(),
        recognizer_outcome_total: recognizer_outcomes.total(),
        composer_outcome_total: composer_outcomes.total(),
        stratum_total,
        plan_graph_denominator: expected_outcomes.plans,
        graph_comparisons_accounted,
        complete: records_observed == TOTAL_RECORDS
            && source_counts.p02 == P02_RECORDS
            && source_counts.p11_negation == P11_RECORDS
            && source_counts.total == records_observed
            && expected_outcomes.total() == records_observed
            && recognizer_outcomes.total() == records_observed
            && composer_outcomes.total() == records_observed
            && stratum_total == records_observed
            && graph_comparisons_accounted == expected_outcomes.plans,
    };
    if !reconciliation.complete {
        return Err(reconciliation_error("aggregate denominator mismatch"));
    }
    let prediction_digest_sha256 = sha256_hex(&prediction_digest_input)
        .map_err(|_| reconciliation_error("prediction digest"))?;

    Ok(PlanEvaluationReport {
        schema_version: 1,
        runner_id: RUNNER_ID,
        metric_specification: METRIC_SPECIFICATION,
        split: split.code(),
        sources: vec![
            SourceIdentity {
                source_id: P02_SOURCE_ID,
                source_type: "PROJECT_AUTHORED_SYNTHETIC",
                corpus_version: "1.0.0",
                generator_id: "p02-generator-v1",
                oracle_origin: "pre_engine_generator_specification",
                claim_scope: "internal_conformance_only",
                split: split.code(),
                physical_sha256: split.p02_sha256(),
                records: P02_RECORDS,
            },
            SourceIdentity {
                source_id: P11_SOURCE_ID,
                source_type: "PROJECT_AUTHORED_SYNTHETIC",
                corpus_version: "1.0.0",
                generator_id: "p11-negation-generator-v1",
                oracle_origin: "pre_p11_composer_generator_specification",
                claim_scope: "internal_conformance_only",
                split: split.code(),
                physical_sha256: split.p11_sha256(),
                records: P11_RECORDS,
            },
        ],
        projections: ProjectionIdentity {
            pre_resolution_projection_id: P09_PROJECTION_ID,
            pre_resolution_projection_sha256: P09_PROJECTION_SHA256,
            plan_projection_id: P02_PROJECTION_ID,
            plan_projection_sha256: P02_PROJECTION_SHA256,
        },
        recognizer: RecognizerIdentity {
            schema_id: INTENT_SCHEMA_ID,
            algorithm_id: INTENT_ALGORITHM_ID,
            configuration_id: INTENT_CONFIGURATION_ID,
            package_sha256: INTENT_PACKAGE_SHA256,
            package_manifest_sha256: INTENT_MANIFEST_SHA256,
        },
        composer: ComposerIdentity {
            schema_id: COMPOSER_SCHEMA_ID,
            algorithm_id: COMPOSER_ALGORITHM_ID,
        },
        comparison_contract: ComparisonContract {
            outcome_fields: vec!["outcome_kind", "abstention_reason"],
            full_graph_fields: GRAPH_MATCH_FIELDS.to_vec(),
            canonical_bytes_required: true,
            acceptance_threshold: None,
        },
        results: EvaluationResults {
            source_counts,
            expected_outcomes: expected_outcomes.clone(),
            recognizer_outcomes,
            composer_outcomes,
            intent_exact: FractionMetric {
                numerator: intent_exact,
                denominator: records_observed,
            },
            outcome_exact: FractionMetric {
                numerator: outcome_exact,
                denominator: records_observed,
            },
            graph_exact: FractionMetric {
                numerator: graph_exact,
                denominator: expected_outcomes.plans,
            },
            canonical_bytes_exact: FractionMetric {
                numerator: canonical_exact,
                denominator: expected_outcomes.plans,
            },
            exact_semantics: FractionMetric {
                numerator: exact_semantics,
                denominator: records_observed,
            },
            graph_strata,
            prediction_digest_sha256,
            reconciliation,
        },
        limitations: LIMITATIONS.to_vec(),
    })
}

struct Comparison {
    intent_exact: bool,
    outcome_exact: bool,
    graph_exact: bool,
    canonical_exact: bool,
    exact_semantics: bool,
}

fn compare(case: &GoldCase, prediction: &Prediction) -> Result<Comparison> {
    match (&case.expected, &prediction.final_outcome) {
        (
            GoldOutcome::Plan(expected),
            PredictedOutcome::Plan {
                semantic,
                canonical_bytes,
            },
        ) => {
            let graph_exact = semantic == expected;
            let canonical_exact = canonical_bytes == &core_canonical_bytes(expected)?;
            Ok(Comparison {
                intent_exact: prediction.intent_exact,
                outcome_exact: true,
                graph_exact,
                canonical_exact,
                exact_semantics: prediction.intent_exact && graph_exact && canonical_exact,
            })
        }
        (GoldOutcome::Plan(_), _) => Ok(Comparison {
            intent_exact: prediction.intent_exact,
            outcome_exact: false,
            graph_exact: false,
            canonical_exact: false,
            exact_semantics: false,
        }),
        (GoldOutcome::Abstention(expected), PredictedOutcome::Abstention(actual)) => {
            let exact = prediction.intent_exact && expected == actual;
            Ok(Comparison {
                intent_exact: prediction.intent_exact,
                outcome_exact: exact,
                graph_exact: false,
                canonical_exact: false,
                exact_semantics: exact,
            })
        }
        (GoldOutcome::Abstention(_), _) => Ok(Comparison {
            intent_exact: prediction.intent_exact,
            outcome_exact: false,
            graph_exact: false,
            canonical_exact: false,
            exact_semantics: false,
        }),
    }
}

fn source_counts(cases: &[GoldCase]) -> Result<SourceCounts> {
    let counts = oracle::source_counts(cases);
    let p02 = u64::try_from(counts.get(&GoldSource::P02).copied().unwrap_or_default())
        .map_err(|_| resource_limit("P02 count"))?;
    let p11_negation = u64::try_from(
        counts
            .get(&GoldSource::P11Negation)
            .copied()
            .unwrap_or_default(),
    )
    .map_err(|_| resource_limit("P11 count"))?;
    Ok(SourceCounts {
        p02,
        p11_negation,
        total: p02
            .checked_add(p11_negation)
            .ok_or_else(|| resource_limit("source total"))?,
    })
}

fn append_prediction(target: &mut Vec<u8>, prediction: &Prediction) -> Result<()> {
    target.extend_from_slice(
        &u64::try_from(prediction.digest_bytes.len())
            .map_err(|_| resource_limit("prediction bytes"))?
            .to_be_bytes(),
    );
    target.extend_from_slice(&prediction.digest_bytes);
    Ok(())
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
    Ok(b"PLAN_EVALUATE_PASS\n".to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::PlanEvaluationErrorCode;

    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .find(|candidate| candidate.join("AGENTS.md").is_file())
            .expect("repository root")
            .to_path_buf()
    }

    #[test]
    fn cli_rejects_every_non_admitted_split_without_forming_a_dataset_path() {
        for split in ["heldout", "test", "validation", "HOLDOUT", "../heldout"] {
            assert_eq!(
                evaluate_cli([
                    "plan-evaluate",
                    "--root",
                    ".",
                    "--split",
                    split,
                    "--output",
                    "report.json",
                ])
                .expect_err("split rejection")
                .code(),
                PlanEvaluationErrorCode::InvalidArguments
            );
        }
        assert_eq!(
            evaluate_cli(["plan-evaluate"])
                .expect_err("missing arguments")
                .code(),
            PlanEvaluationErrorCode::InvalidArguments
        );
    }

    #[test]
    fn train_and_development_reconcile_exact_denominators() {
        for split in [EvaluationSplit::Train, EvaluationSplit::Development] {
            let report = evaluate(&repository_root(), split).expect("evaluation");
            assert!(report.results().reconciliation().complete());
            assert_eq!(report.results().reconciliation().records_observed(), 963);
            assert_eq!(report.results().exact_semantics().denominator(), 963);
            assert_eq!(report.results().graph_exact().denominator(), 962);
        }
    }

    #[test]
    fn canonical_report_bytes_are_stable_and_newline_terminated() {
        let first = evaluate(&repository_root(), EvaluationSplit::Train).expect("first evaluation");
        let second =
            evaluate(&repository_root(), EvaluationSplit::Train).expect("second evaluation");
        let first = first.canonical_bytes().expect("first report bytes");
        let second = second.canonical_bytes().expect("second report bytes");
        assert_eq!(first, second);
        assert_eq!(first.last(), Some(&b'\n'));
        assert!(first.len() <= MAX_REPORT_BYTES);
    }

    #[test]
    fn production_crates_do_not_depend_on_plan_eval() {
        let root = repository_root();
        for manifest in [
            "crates/ha-catalog/Cargo.toml",
            "crates/intent-engine/Cargo.toml",
            "crates/lang-ptbr/Cargo.toml",
            "crates/nlu-core/Cargo.toml",
            "crates/plan-engine/Cargo.toml",
            "crates/protocol/Cargo.toml",
        ] {
            let bytes = std::fs::read(root.join(manifest)).expect("production manifest");
            let text = std::str::from_utf8(&bytes).expect("UTF-8 manifest");
            assert!(!text.contains("plan-eval"), "{manifest}");
        }
    }
}
