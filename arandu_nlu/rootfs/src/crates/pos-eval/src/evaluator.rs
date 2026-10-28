use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    path::{Path, PathBuf},
};

use lang_ptbr::{
    NormalizedText, POS_BASELINE_ID, POS_MODEL_ID, POS_MODEL_PACKAGE_SHA256, POS_TAGGER_ID,
    PosLookup, PosTagger,
};
use nlu_data::{
    canonical_json, parse_strict_json,
    pos::{
        MAX_POS_MANIFEST_BYTES, MAX_POS_PACKAGE_BYTES, PosModelIdentity,
        decode_pos_transition_package,
    },
    read_bounded_root_file,
};
use serde::{Deserialize, Serialize};

use crate::{
    MODEL_ALGORITHM_ID, MODEL_COMPILER_ID, MODEL_CONFIG_ID, MODEL_ID,
    common::{
        CLAIM_SCOPE, CORPUS_VERSION, HELDOUT_CASE_IDS_SHA256, HELDOUT_DOCUMENT_IDS_SHA256,
        HELDOUT_PATH, HELDOUT_SHA256, HELDOUT_TEXT_SHA256S_SHA256, LOCALE, ORIGINAL_POS_SHA256,
        ORIGINS_SHA256, PosDataset, PosEvaluationError, PosEvaluationErrorCode, Result, SOURCE_ID,
        SOURCE_MANIFEST_SHA256, SPLIT_MANIFEST_PATH, SPLIT_MANIFEST_SHA256, canonical_bytes,
        collection_digest, collection_digest_strings, invalid_manifest, load_split_manifest,
        map_data_error, parse_pos_slice, read_split_slice, sha256, validate_relative_path,
    },
};

pub const EVALUATION_MANIFEST_PATH: &str = "data/evaluation/p08/pos-v1/manifest.json";
pub const EVALUATION_MANIFEST_SHA256: &str =
    "c4ca9531c45eaff9df8a79f48a060f7a1716c3102b37b4caa24a5cba708ff048";

const DOMAIN: &str = "pos";
const RUNNER_ID: &str = "pos-eval-v1";
const DATASET_ID: &str = "p08-pos-internal-conformance";
const SPLIT: &str = "heldout";
const METRIC_SPEC: &str = "exact-pos-candidate-set-aggregate-v1";
const MODEL_PACKAGE_PATH: &str = "data/pos/p08/package.bin";
const MODEL_MANIFEST_PATH: &str = "data/pos/p08/package-manifest.json";
const MODEL_PACKAGE_SHA256: &str =
    "23beb6dd464c4bd03d69bb374be210fd3a187661c37673194f466b4270b8020c";
const MODEL_MANIFEST_SHA256: &str =
    "f89eaa786f37456d5e1d47e522ceea7bd96f41b272bdc9a6ca99407d7cc2b0bc";
const CONFUSION_LABELS: [&str; 3] = ["unknown", "unique", "ambiguous"];
const EXPECTED_LABELS: [&str; 7] = ["ADJ", "ADP", "DET", "NOUN", "NUM", "VERB", "X"];
const ERROR_TAXONOMY: [&str; 5] = [
    "exact_set_mismatch",
    "missing_expected_candidate",
    "unexpected_candidate",
    "unexpected_unknown",
    "unresolved_ambiguity",
];
const LIMITATIONS: [&str; 5] = [
    "project_authored_labels_share_generator_with_runtime_lexicon",
    "single_source_origin_not_origin_disjoint",
    "templatic_internal_conformance_not_independent_accuracy",
    "heldout_unknown_subset_contains_four_expected_x_tokens",
    "no_unseen_language_or_cross_origin_generalization_claim",
];
const EXPECTED_HELDOUT_LABEL_COUNTS: [(&str, u64); 7] = [
    ("ADJ", 80),
    ("ADP", 80),
    ("DET", 81),
    ("NOUN", 157),
    ("NUM", 160),
    ("VERB", 81),
    ("X", 4),
];

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
pub struct CandidateCounts {
    true_positive: u64,
    false_positive: u64,
    false_negative: u64,
}

impl CandidateCounts {
    #[must_use]
    pub const fn true_positive(&self) -> u64 {
        self.true_positive
    }

    #[must_use]
    pub const fn false_positive(&self) -> u64 {
        self.false_positive
    }

    #[must_use]
    pub const fn false_negative(&self) -> u64 {
        self.false_negative
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct UnknownMetrics {
    expected: u64,
    correct: u64,
    unexpected: u64,
}

impl UnknownMetrics {
    #[must_use]
    pub const fn expected(&self) -> u64 {
        self.expected
    }

    #[must_use]
    pub const fn correct(&self) -> u64 {
        self.correct
    }

    #[must_use]
    pub const fn unexpected(&self) -> u64 {
        self.unexpected
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AmbiguityMetrics {
    exact: u64,
    compatible: u64,
    unresolved: u64,
    total: u64,
}

impl AmbiguityMetrics {
    #[must_use]
    pub const fn exact(&self) -> u64 {
        self.exact
    }

    #[must_use]
    pub const fn compatible(&self) -> u64 {
        self.compatible
    }

    #[must_use]
    pub const fn unresolved(&self) -> u64 {
        self.unresolved
    }

    #[must_use]
    pub const fn total(&self) -> u64 {
        self.total
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ConfusionMatrix {
    labels: Vec<&'static str>,
    matrix: [[u64; 3]; 3],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ErrorCategories {
    exact_set_mismatch: u64,
    missing_expected_candidate: u64,
    unexpected_candidate: u64,
    unexpected_unknown: u64,
    unresolved_ambiguity: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MethodMetrics {
    method_id: &'static str,
    exact_token_sets: FractionMetric,
    candidate_counts: CandidateCounts,
    sentence_exact: FractionMetric,
    document_exact: FractionMetric,
    origin_exact: FractionMetric,
    unknown: UnknownMetrics,
    ambiguity: AmbiguityMetrics,
    confusion_matrix: ConfusionMatrix,
    error_categories: ErrorCategories,
}

impl MethodMetrics {
    #[must_use]
    pub const fn exact_token_sets(&self) -> &FractionMetric {
        &self.exact_token_sets
    }

    #[must_use]
    pub const fn candidate_counts(&self) -> &CandidateCounts {
        &self.candidate_counts
    }

    #[must_use]
    pub const fn unknown(&self) -> &UnknownMetrics {
        &self.unknown
    }

    #[must_use]
    pub const fn ambiguity(&self) -> &AmbiguityMetrics {
        &self.ambiguity
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ComparisonResults {
    baseline: MethodMetrics,
    selected: MethodMetrics,
}

impl ComparisonResults {
    #[must_use]
    pub const fn baseline(&self) -> &MethodMetrics {
        &self.baseline
    }

    #[must_use]
    pub const fn selected(&self) -> &MethodMetrics {
        &self.selected
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BaselineDelta {
    direction: &'static str,
    exact_token_sets: i64,
    candidate_true_positive: i64,
    candidate_false_positive: i64,
    candidate_false_negative: i64,
    sentence_exact: i64,
    document_exact: i64,
    origin_exact: i64,
    unknown_correct: i64,
    unexpected_unknown: i64,
    ambiguity_exact: i64,
    ambiguity_compatible: i64,
    ambiguity_unresolved: i64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvaluationReport {
    schema_version: u32,
    domain: &'static str,
    runner_id: &'static str,
    dataset_id: &'static str,
    dataset_version: &'static str,
    split: &'static str,
    claim_scope: &'static str,
    locale: &'static str,
    source_id: &'static str,
    source_manifest_sha256: &'static str,
    original_artifact_sha256: &'static str,
    manifest_sha256: &'static str,
    split_manifest_sha256: &'static str,
    heldout_sha256: &'static str,
    model_id: &'static str,
    model_package_sha256: &'static str,
    baseline_id: &'static str,
    selected_id: &'static str,
    metric_spec: &'static str,
    confusion_labels: Vec<&'static str>,
    error_taxonomy: Vec<&'static str>,
    limitations: Vec<&'static str>,
    results: ComparisonResults,
    baseline_delta: BaselineDelta,
}

impl EvaluationReport {
    #[must_use]
    pub const fn results(&self) -> &ComparisonResults {
        &self.results
    }

    #[must_use]
    pub const fn baseline_delta(&self) -> &BaselineDelta {
        &self.baseline_delta
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EvaluationManifest {
    schema_version: u32,
    domain: String,
    runner_id: String,
    dataset_id: String,
    dataset_version: String,
    split: String,
    claim_scope: String,
    locale: String,
    source_id: String,
    source_manifest_sha256: String,
    original_artifact_sha256: String,
    split_manifest_path: String,
    split_manifest_sha256: String,
    baseline_id: String,
    selected_id: String,
    metric_spec: String,
    confusion_labels: Vec<String>,
    expected_labels: Vec<String>,
    error_taxonomy: Vec<String>,
    limitations: Vec<String>,
    heldout: HeldoutManifest,
    model: ModelManifest,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct HeldoutManifest {
    path: String,
    sha256: String,
    records: u64,
    documents: u64,
    origin_count: u64,
    tokens: u64,
    case_ids_sha256: String,
    document_ids_sha256: String,
    text_sha256s_sha256: String,
    origins_sha256: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ModelManifest {
    model_id: String,
    algorithm_id: String,
    compiler_id: String,
    config_id: String,
    package_path: String,
    package_sha256: String,
    package_bytes: u64,
    manifest_path: String,
    manifest_sha256: String,
}

impl EvaluationManifest {
    fn validate(&self) -> Result<()> {
        if self.schema_version != 1
            || self.domain != DOMAIN
            || self.runner_id != RUNNER_ID
            || self.dataset_id != DATASET_ID
            || self.dataset_version != CORPUS_VERSION
            || self.split != SPLIT
            || self.claim_scope != CLAIM_SCOPE
            || self.locale != LOCALE
            || self.source_id != SOURCE_ID
            || self.source_manifest_sha256 != SOURCE_MANIFEST_SHA256
            || self.original_artifact_sha256 != ORIGINAL_POS_SHA256
            || self.split_manifest_path != SPLIT_MANIFEST_PATH
            || self.split_manifest_sha256 != SPLIT_MANIFEST_SHA256
            || self.baseline_id != POS_BASELINE_ID
            || self.selected_id != POS_TAGGER_ID
            || self.metric_spec != METRIC_SPEC
            || self
                .confusion_labels
                .iter()
                .map(String::as_str)
                .ne(CONFUSION_LABELS)
            || self
                .expected_labels
                .iter()
                .map(String::as_str)
                .ne(EXPECTED_LABELS)
            || self
                .error_taxonomy
                .iter()
                .map(String::as_str)
                .ne(ERROR_TAXONOMY)
            || self.limitations.iter().map(String::as_str).ne(LIMITATIONS)
        {
            return Err(invalid_manifest("P08 evaluation manifest identity"));
        }

        let heldout = &self.heldout;
        if heldout.path != HELDOUT_PATH
            || heldout.sha256 != HELDOUT_SHA256
            || heldout.records != 81
            || heldout.documents != 9
            || heldout.origin_count != 1
            || heldout.tokens != 643
            || heldout.case_ids_sha256 != HELDOUT_CASE_IDS_SHA256
            || heldout.document_ids_sha256 != HELDOUT_DOCUMENT_IDS_SHA256
            || heldout.text_sha256s_sha256 != HELDOUT_TEXT_SHA256S_SHA256
            || heldout.origins_sha256 != ORIGINS_SHA256
        {
            return Err(invalid_manifest("P08 heldout manifest inventory"));
        }

        let model = &self.model;
        if model.model_id != MODEL_ID
            || model.algorithm_id != MODEL_ALGORITHM_ID
            || model.compiler_id != MODEL_COMPILER_ID
            || model.config_id != MODEL_CONFIG_ID
            || model.package_path != MODEL_PACKAGE_PATH
            || model.package_sha256 != MODEL_PACKAGE_SHA256
            || model.package_bytes != 47
            || model.manifest_path != MODEL_MANIFEST_PATH
            || model.manifest_sha256 != MODEL_MANIFEST_SHA256
        {
            return Err(invalid_manifest("P08 model manifest identity"));
        }
        Ok(())
    }
}

struct LoadedManifest {
    manifest: EvaluationManifest,
}

#[derive(Clone, Debug)]
struct MethodAccumulator {
    method_id: &'static str,
    token_total: u64,
    exact_tokens: u64,
    true_positive: u64,
    false_positive: u64,
    false_negative: u64,
    sentence_total: u64,
    exact_sentences: u64,
    documents: BTreeMap<String, bool>,
    origins: BTreeMap<String, bool>,
    unknown_expected: u64,
    unknown_correct: u64,
    unknown_unexpected: u64,
    ambiguity_exact: u64,
    ambiguity_compatible: u64,
    ambiguity_unresolved: u64,
    ambiguity_total: u64,
    confusion_matrix: [[u64; 3]; 3],
    errors: ErrorCategories,
}

impl MethodAccumulator {
    fn new(method_id: &'static str) -> Self {
        Self {
            method_id,
            token_total: 0,
            exact_tokens: 0,
            true_positive: 0,
            false_positive: 0,
            false_negative: 0,
            sentence_total: 0,
            exact_sentences: 0,
            documents: BTreeMap::new(),
            origins: BTreeMap::new(),
            unknown_expected: 0,
            unknown_correct: 0,
            unknown_unexpected: 0,
            ambiguity_exact: 0,
            ambiguity_compatible: 0,
            ambiguity_unresolved: 0,
            ambiguity_total: 0,
            confusion_matrix: [[0; 3]; 3],
            errors: ErrorCategories {
                exact_set_mismatch: 0,
                missing_expected_candidate: 0,
                unexpected_candidate: 0,
                unexpected_unknown: 0,
                unresolved_ambiguity: 0,
            },
        }
    }

    fn add_sentence(
        &mut self,
        document_id: &str,
        origin_id: &str,
        expected: &[BTreeSet<String>],
        observed: &[BTreeSet<String>],
        ambiguity_subset: &[bool],
    ) -> Result<()> {
        if expected.len() != observed.len() || expected.len() != ambiguity_subset.len() {
            return Err(PosEvaluationError::new(
                PosEvaluationErrorCode::IntegrityMismatch,
                "POS sentence scorer cardinality",
            ));
        }
        let mut sentence_exact = true;
        for ((expected, observed), ambiguity_subset) in
            expected.iter().zip(observed).zip(ambiguity_subset)
        {
            checked_increment(&mut self.token_total, "token total")?;
            let exact = expected == observed;
            if exact {
                checked_increment(&mut self.exact_tokens, "exact tokens")?;
            } else {
                sentence_exact = false;
                checked_increment(&mut self.errors.exact_set_mismatch, "exact-set mismatches")?;
            }

            checked_add(
                &mut self.true_positive,
                expected.intersection(observed).count(),
                "true-positive candidates",
            )?;
            let false_positive = observed.difference(expected).count();
            let false_negative = expected.difference(observed).count();
            checked_add(
                &mut self.false_positive,
                false_positive,
                "false-positive candidates",
            )?;
            checked_add(
                &mut self.false_negative,
                false_negative,
                "false-negative candidates",
            )?;
            checked_add(
                &mut self.errors.unexpected_candidate,
                false_positive,
                "unexpected candidate errors",
            )?;
            checked_add(
                &mut self.errors.missing_expected_candidate,
                false_negative,
                "missing expected candidate errors",
            )?;

            let expected_class = cardinality_class(expected.len());
            let observed_class = cardinality_class(observed.len());
            checked_increment(
                &mut self.confusion_matrix[expected_class][observed_class],
                "confusion matrix",
            )?;

            if expected.is_empty() {
                checked_increment(&mut self.unknown_expected, "expected unknowns")?;
                if observed.is_empty() {
                    checked_increment(&mut self.unknown_correct, "correct unknowns")?;
                }
            } else if observed.is_empty() {
                checked_increment(&mut self.unknown_unexpected, "unexpected unknowns")?;
                checked_increment(
                    &mut self.errors.unexpected_unknown,
                    "unexpected unknown errors",
                )?;
            }

            if *ambiguity_subset {
                checked_increment(&mut self.ambiguity_total, "ambiguity total")?;
                if exact {
                    checked_increment(&mut self.ambiguity_exact, "ambiguity exact")?;
                }
                if expected.is_subset(observed) {
                    checked_increment(&mut self.ambiguity_compatible, "ambiguity compatible")?;
                }
                if observed.len() > 1 {
                    checked_increment(&mut self.ambiguity_unresolved, "ambiguity unresolved")?;
                    checked_increment(
                        &mut self.errors.unresolved_ambiguity,
                        "unresolved ambiguity errors",
                    )?;
                }
            }
        }

        checked_increment(&mut self.sentence_total, "sentence total")?;
        if sentence_exact {
            checked_increment(&mut self.exact_sentences, "exact sentences")?;
        }
        self.documents
            .entry(document_id.to_owned())
            .and_modify(|exact| *exact &= sentence_exact)
            .or_insert(sentence_exact);
        self.origins
            .entry(origin_id.to_owned())
            .and_modify(|exact| *exact &= sentence_exact)
            .or_insert(sentence_exact);
        Ok(())
    }

    fn finish(self) -> Result<MethodMetrics> {
        let document_total = usize_to_u64(self.documents.len(), "document total")?;
        let origin_total = usize_to_u64(self.origins.len(), "origin total")?;
        let document_exact = usize_to_u64(
            self.documents.values().filter(|exact| **exact).count(),
            "exact documents",
        )?;
        let origin_exact = usize_to_u64(
            self.origins.values().filter(|exact| **exact).count(),
            "exact origins",
        )?;
        let metrics = MethodMetrics {
            method_id: self.method_id,
            exact_token_sets: FractionMetric {
                numerator: self.exact_tokens,
                denominator: self.token_total,
            },
            candidate_counts: CandidateCounts {
                true_positive: self.true_positive,
                false_positive: self.false_positive,
                false_negative: self.false_negative,
            },
            sentence_exact: FractionMetric {
                numerator: self.exact_sentences,
                denominator: self.sentence_total,
            },
            document_exact: FractionMetric {
                numerator: document_exact,
                denominator: document_total,
            },
            origin_exact: FractionMetric {
                numerator: origin_exact,
                denominator: origin_total,
            },
            unknown: UnknownMetrics {
                expected: self.unknown_expected,
                correct: self.unknown_correct,
                unexpected: self.unknown_unexpected,
            },
            ambiguity: AmbiguityMetrics {
                exact: self.ambiguity_exact,
                compatible: self.ambiguity_compatible,
                unresolved: self.ambiguity_unresolved,
                total: self.ambiguity_total,
            },
            confusion_matrix: ConfusionMatrix {
                labels: CONFUSION_LABELS.to_vec(),
                matrix: self.confusion_matrix,
            },
            error_categories: self.errors,
        };
        validate_method_reconciliation(&metrics)?;
        Ok(metrics)
    }
}

pub fn evaluate(root: &Path, manifest_relative_path: &str) -> Result<EvaluationReport> {
    let loaded = load_evaluation_manifest(root, manifest_relative_path)?;
    validate_model_artifacts(root, &loaded.manifest)?;
    let split_manifest = load_split_manifest(root, &loaded.manifest.split_manifest_path)?;
    let heldout_entry = split_manifest.entry(SPLIT)?;
    let heldout_bytes = read_split_slice(root, heldout_entry, HELDOUT_PATH, HELDOUT_SHA256)?;
    let dataset = parse_pos_slice(&heldout_bytes, SPLIT)?;
    validate_heldout_inventory(&dataset)?;
    score_dataset(&dataset)
}

pub fn evaluate_report_bytes(root: &Path, manifest_relative_path: &str) -> Result<Vec<u8>> {
    let report = evaluate(root, manifest_relative_path)?;
    canonical_bytes(
        &report,
        "P08 POS evaluation report",
        PosEvaluationErrorCode::ReportEncoding,
    )
}

pub fn evaluate_cli<I, T>(arguments: I) -> Result<Vec<u8>>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let arguments = arguments.into_iter().map(Into::into).collect::<Vec<_>>();
    let (root, manifest) = parse_cli_arguments(&arguments)?;
    evaluate_report_bytes(&root, &manifest)
}

fn load_evaluation_manifest(root: &Path, relative_path: &str) -> Result<LoadedManifest> {
    if relative_path != EVALUATION_MANIFEST_PATH {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "evaluation manifest path is not the frozen P08 path",
        ));
    }
    validate_relative_path(relative_path)?;
    let bytes = read_bounded_root_file(root, relative_path, 64 * 1024)
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    if sha256(&bytes, PosEvaluationErrorCode::InvalidManifest)? != EVALUATION_MANIFEST_SHA256 {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "evaluation manifest SHA-256",
        ));
    }
    if bytes.last() != Some(&b'\n') {
        return Err(invalid_manifest("evaluation manifest final newline"));
    }
    let value = parse_strict_json(&bytes, "P08 evaluation manifest")
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    let mut canonical = canonical_json(&value, "P08 evaluation manifest")
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    canonical.push(b'\n');
    if canonical != bytes {
        return Err(invalid_manifest("noncanonical evaluation manifest"));
    }
    let manifest: EvaluationManifest = serde_json::from_value(value).map_err(|error| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidManifest,
            format!("closed evaluation manifest shape: {error}"),
        )
    })?;
    manifest.validate()?;
    Ok(LoadedManifest { manifest })
}

fn validate_model_artifacts(root: &Path, manifest: &EvaluationManifest) -> Result<()> {
    let package = read_bounded_root_file(root, &manifest.model.package_path, MAX_POS_PACKAGE_BYTES)
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    let package_manifest =
        read_bounded_root_file(root, &manifest.model.manifest_path, MAX_POS_MANIFEST_BYTES)
            .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    if sha256(&package, PosEvaluationErrorCode::InvalidManifest)? != MODEL_PACKAGE_SHA256
        || sha256(&package_manifest, PosEvaluationErrorCode::InvalidManifest)?
            != MODEL_MANIFEST_SHA256
        || u64::try_from(package.len()).map_err(|_| {
            PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, "model package bytes")
        })? != manifest.model.package_bytes
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "P08 model artifact",
        ));
    }
    let identity = PosModelIdentity::new(
        MODEL_ID,
        MODEL_ALGORITHM_ID,
        MODEL_COMPILER_ID,
        MODEL_CONFIG_ID,
    );
    let decoded = decode_pos_transition_package(&package, &package_manifest, &identity)
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    if decoded.labels() != ["ADP", "DET", "NOUN", "NUM", "VERB"] || decoded.transitions().len() != 7
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "P08 model inventory",
        ));
    }
    Ok(())
}

fn validate_heldout_inventory(dataset: &PosDataset) -> Result<()> {
    let expected_counts = EXPECTED_HELDOUT_LABEL_COUNTS
        .into_iter()
        .map(|(label, count)| (label.to_owned(), count))
        .collect::<BTreeMap<_, _>>();
    let case_ids = dataset.rows.keys().map(String::as_str).collect::<Vec<_>>();
    let ambiguity_rows = dataset
        .rows
        .values()
        .filter(|row| row.ambiguity_preserved == Some(true))
        .count();
    if dataset.rows.len() != 81
        || dataset.documents.len() != 9
        || dataset.origins.len() != 1
        || dataset.token_count != 643
        || dataset.label_counts != expected_counts
        || dataset
            .families
            .iter()
            .map(String::as_str)
            .ne(["pos-ambiguity-heldout-v1", "pos-state-heldout-v1"])
        || ambiguity_rows != 1
        || dataset
            .rows
            .values()
            .any(|row| row.ambiguity_preserved == Some(false))
        || collection_digest_strings(case_ids)? != HELDOUT_CASE_IDS_SHA256
        || collection_digest(&dataset.documents)? != HELDOUT_DOCUMENT_IDS_SHA256
        || collection_digest(&dataset.text_sha256s)? != HELDOUT_TEXT_SHA256S_SHA256
        || collection_digest(&dataset.origins)? != ORIGINS_SHA256
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "heldout slice inventory",
        ));
    }
    Ok(())
}

fn score_dataset(dataset: &PosDataset) -> Result<EvaluationReport> {
    if POS_BASELINE_ID != "lang-ptbr-independent-evidence-pos-baseline-v1"
        || POS_TAGGER_ID != "lang-ptbr-adjacent-transition-pos-v1"
        || POS_MODEL_ID != MODEL_ID
        || POS_MODEL_PACKAGE_SHA256 != MODEL_PACKAGE_SHA256
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::RuntimeUnavailable,
            "production POS identity",
        ));
    }
    let tagger = PosTagger::bundled().map_err(|error| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::RuntimeUnavailable,
            format!("bundled POS tagger: {}", error.code()),
        )
    })?;
    let mut baseline = MethodAccumulator::new(POS_BASELINE_ID);
    let mut selected = MethodAccumulator::new(POS_TAGGER_ID);
    let mut ambiguity_subset_total = 0_u64;

    for row in dataset.rows.values() {
        let text = NormalizedText::new(row.text.clone()).map_err(|error| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::RuntimeUnavailable,
                format!("normalize heldout row: {error}"),
            )
        })?;
        if text.as_str() != row.text {
            return Err(PosEvaluationError::new(
                PosEvaluationErrorCode::IntegrityMismatch,
                "heldout normalization changed source bytes",
            ));
        }
        let tokens = text.tokenize().map_err(|error| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::RuntimeUnavailable,
                format!("tokenize heldout row: {error}"),
            )
        })?;
        validate_runtime_tokens(row, &text, tokens.tokens())?;
        let baseline_tagging = tagger.baseline(tokens.tokens(), &text).map_err(|error| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::RuntimeUnavailable,
                format!("baseline POS tagging: {error}"),
            )
        })?;
        let selected_tagging = tagger.tag(tokens.tokens(), &text).map_err(|error| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::RuntimeUnavailable,
                format!("selected POS tagging: {error}"),
            )
        })?;
        let expected = row
            .tokens
            .iter()
            .map(|token| {
                if token.allowed_pos[0] == "X" {
                    BTreeSet::new()
                } else {
                    BTreeSet::from([token.allowed_pos[0].clone()])
                }
            })
            .collect::<Vec<_>>();
        let baseline_observed = canonical_candidates(baseline_tagging.lookups());
        let selected_observed = canonical_candidates(selected_tagging.lookups());
        let ambiguity_subset = baseline_observed
            .iter()
            .map(|candidates| row.ambiguity_preserved == Some(true) && candidates.len() > 1)
            .collect::<Vec<_>>();
        if row.ambiguity_preserved != Some(true)
            && baseline_observed
                .iter()
                .any(|candidates| candidates.len() > 1)
        {
            return Err(PosEvaluationError::new(
                PosEvaluationErrorCode::IntegrityMismatch,
                "ambiguity outside frozen subset",
            ));
        }
        checked_add(
            &mut ambiguity_subset_total,
            ambiguity_subset
                .iter()
                .filter(|selected| **selected)
                .count(),
            "ambiguity subset total",
        )?;
        baseline.add_sentence(
            &row.document_id,
            &row.source_id,
            &expected,
            &baseline_observed,
            &ambiguity_subset,
        )?;
        selected.add_sentence(
            &row.document_id,
            &row.source_id,
            &expected,
            &selected_observed,
            &ambiguity_subset,
        )?;
    }
    if ambiguity_subset_total != 2 {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "frozen ambiguity subset inventory",
        ));
    }

    let baseline = baseline.finish()?;
    let selected = selected.finish()?;
    let baseline_delta = build_delta(&baseline, &selected)?;
    Ok(EvaluationReport {
        schema_version: 1,
        domain: DOMAIN,
        runner_id: RUNNER_ID,
        dataset_id: DATASET_ID,
        dataset_version: CORPUS_VERSION,
        split: SPLIT,
        claim_scope: CLAIM_SCOPE,
        locale: LOCALE,
        source_id: SOURCE_ID,
        source_manifest_sha256: SOURCE_MANIFEST_SHA256,
        original_artifact_sha256: ORIGINAL_POS_SHA256,
        manifest_sha256: EVALUATION_MANIFEST_SHA256,
        split_manifest_sha256: SPLIT_MANIFEST_SHA256,
        heldout_sha256: HELDOUT_SHA256,
        model_id: MODEL_ID,
        model_package_sha256: MODEL_PACKAGE_SHA256,
        baseline_id: POS_BASELINE_ID,
        selected_id: POS_TAGGER_ID,
        metric_spec: METRIC_SPEC,
        confusion_labels: CONFUSION_LABELS.to_vec(),
        error_taxonomy: ERROR_TAXONOMY.to_vec(),
        limitations: LIMITATIONS.to_vec(),
        results: ComparisonResults { baseline, selected },
        baseline_delta,
    })
}

fn validate_runtime_tokens(
    row: &crate::common::PosRow,
    text: &NormalizedText,
    tokens: &[lang_ptbr::Token],
) -> Result<()> {
    if tokens.len() != row.tokens.len() {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "runtime token count",
        ));
    }
    for (token, expected) in tokens.iter().zip(&row.tokens) {
        if u64::from(token.original_span().start()) != expected.begin_byte
            || u64::from(token.original_span().end()) != expected.end_byte
            || u64::from(token.normalized_span().start()) != expected.begin_byte
            || u64::from(token.normalized_span().end()) != expected.end_byte
            || token.original_slice(text).ok() != Some(expected.text.as_str())
            || token.normalized_slice(text).ok() != Some(expected.text.as_str())
        {
            return Err(PosEvaluationError::new(
                PosEvaluationErrorCode::IntegrityMismatch,
                "runtime token text or span",
            ));
        }
    }
    Ok(())
}

fn canonical_candidates(lookups: &[PosLookup<'_>]) -> Vec<BTreeSet<String>> {
    lookups
        .iter()
        .map(|lookup| {
            lookup
                .candidates()
                .iter()
                .map(|candidate| candidate.tag().code().to_owned())
                .collect()
        })
        .collect()
}

fn validate_method_reconciliation(metrics: &MethodMetrics) -> Result<()> {
    let matrix_total = metrics
        .confusion_matrix
        .matrix
        .iter()
        .flatten()
        .try_fold(0_u64, |total, value| total.checked_add(*value))
        .ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::ResourceLimit,
                "confusion matrix total",
            )
        })?;
    if metrics.exact_token_sets.denominator != 643
        || metrics.sentence_exact.denominator != 81
        || metrics.document_exact.denominator != 9
        || metrics.origin_exact.denominator != 1
        || metrics.unknown.expected != 4
        || metrics.unknown.correct > metrics.unknown.expected
        || metrics.ambiguity.total != 2
        || metrics.ambiguity.exact > metrics.ambiguity.compatible
        || metrics.ambiguity.compatible > metrics.ambiguity.total
        || metrics.ambiguity.unresolved > metrics.ambiguity.total
        || matrix_total != metrics.exact_token_sets.denominator
        || metrics
            .exact_token_sets
            .numerator
            .checked_add(metrics.error_categories.exact_set_mismatch)
            != Some(metrics.exact_token_sets.denominator)
        || metrics
            .candidate_counts
            .true_positive
            .checked_add(metrics.candidate_counts.false_negative)
            != Some(639)
        || metrics.error_categories.missing_expected_candidate
            != metrics.candidate_counts.false_negative
        || metrics.error_categories.unexpected_candidate != metrics.candidate_counts.false_positive
        || metrics.error_categories.unexpected_unknown != metrics.unknown.unexpected
        || metrics.error_categories.unresolved_ambiguity != metrics.ambiguity.unresolved
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "POS metric reconciliation",
        ));
    }
    Ok(())
}

fn build_delta(baseline: &MethodMetrics, selected: &MethodMetrics) -> Result<BaselineDelta> {
    Ok(BaselineDelta {
        direction: "selected_minus_baseline",
        exact_token_sets: signed_delta(
            selected.exact_token_sets.numerator,
            baseline.exact_token_sets.numerator,
        )?,
        candidate_true_positive: signed_delta(
            selected.candidate_counts.true_positive,
            baseline.candidate_counts.true_positive,
        )?,
        candidate_false_positive: signed_delta(
            selected.candidate_counts.false_positive,
            baseline.candidate_counts.false_positive,
        )?,
        candidate_false_negative: signed_delta(
            selected.candidate_counts.false_negative,
            baseline.candidate_counts.false_negative,
        )?,
        sentence_exact: signed_delta(
            selected.sentence_exact.numerator,
            baseline.sentence_exact.numerator,
        )?,
        document_exact: signed_delta(
            selected.document_exact.numerator,
            baseline.document_exact.numerator,
        )?,
        origin_exact: signed_delta(
            selected.origin_exact.numerator,
            baseline.origin_exact.numerator,
        )?,
        unknown_correct: signed_delta(selected.unknown.correct, baseline.unknown.correct)?,
        unexpected_unknown: signed_delta(selected.unknown.unexpected, baseline.unknown.unexpected)?,
        ambiguity_exact: signed_delta(selected.ambiguity.exact, baseline.ambiguity.exact)?,
        ambiguity_compatible: signed_delta(
            selected.ambiguity.compatible,
            baseline.ambiguity.compatible,
        )?,
        ambiguity_unresolved: signed_delta(
            selected.ambiguity.unresolved,
            baseline.ambiguity.unresolved,
        )?,
    })
}

fn parse_cli_arguments(arguments: &[OsString]) -> Result<(PathBuf, String)> {
    if arguments.is_empty() {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "missing program name",
        ));
    }
    let mut root = None;
    let mut manifest = None;
    let mut index = 1;
    while index < arguments.len() {
        let option = arguments[index].to_str().ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidArguments,
                "command-line option is not UTF-8",
            )
        })?;
        index += 1;
        let value = arguments.get(index).ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidArguments,
                format!("missing value for {option}"),
            )
        })?;
        index += 1;
        match option {
            "--root" if root.is_none() => root = Some(PathBuf::from(value)),
            "--manifest" if manifest.is_none() => {
                manifest = Some(
                    value
                        .to_str()
                        .ok_or_else(|| {
                            PosEvaluationError::new(
                                PosEvaluationErrorCode::InvalidArguments,
                                "manifest path is not UTF-8",
                            )
                        })?
                        .to_owned(),
                );
            }
            "--root" | "--manifest" => {
                return Err(PosEvaluationError::new(
                    PosEvaluationErrorCode::InvalidArguments,
                    format!("duplicate option {option}"),
                ));
            }
            _ => {
                return Err(PosEvaluationError::new(
                    PosEvaluationErrorCode::InvalidArguments,
                    format!("unknown option {option}"),
                ));
            }
        }
    }
    let root = root.ok_or_else(|| {
        PosEvaluationError::new(PosEvaluationErrorCode::InvalidArguments, "missing --root")
    })?;
    let manifest = manifest.ok_or_else(|| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "missing --manifest",
        )
    })?;
    if manifest != EVALUATION_MANIFEST_PATH {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "manifest path is not the frozen P08 path",
        ));
    }
    Ok((root, manifest))
}

fn cardinality_class(cardinality: usize) -> usize {
    match cardinality {
        0 => 0,
        1 => 1,
        _ => 2,
    }
}

fn checked_increment(value: &mut u64, context: &str) -> Result<()> {
    *value = value
        .checked_add(1)
        .ok_or_else(|| PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, context))?;
    Ok(())
}

fn checked_add(value: &mut u64, increment: usize, context: &str) -> Result<()> {
    let increment = usize_to_u64(increment, context)?;
    *value = value
        .checked_add(increment)
        .ok_or_else(|| PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, context))?;
    Ok(())
}

fn usize_to_u64(value: usize, context: &str) -> Result<u64> {
    u64::try_from(value)
        .map_err(|_| PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, context))
}

fn signed_delta(selected: u64, baseline: u64) -> Result<i64> {
    let selected = i64::try_from(selected).map_err(|_| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::ResourceLimit,
            "selected metric delta",
        )
    })?;
    let baseline = i64::try_from(baseline).map_err(|_| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::ResourceLimit,
            "baseline metric delta",
        )
    })?;
    selected.checked_sub(baseline).ok_or_else(|| {
        PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, "metric delta")
    })
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::{EVALUATION_MANIFEST_PATH, evaluate, validate_method_reconciliation};

    #[test]
    fn frozen_results_match_and_reconcile() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let report = evaluate(&root, EVALUATION_MANIFEST_PATH).expect("evaluate frozen heldout");
        let baseline = report.results().baseline();
        let selected = report.results().selected();

        assert_eq!(
            (
                baseline.exact_token_sets().numerator(),
                baseline.exact_token_sets().denominator()
            ),
            (561, 643)
        );
        assert_eq!(
            (
                selected.exact_token_sets().numerator(),
                selected.exact_token_sets().denominator()
            ),
            (562, 643)
        );
        assert_eq!(
            (
                baseline.candidate_counts().true_positive(),
                baseline.candidate_counts().false_positive(),
                baseline.candidate_counts().false_negative()
            ),
            (559, 2, 80)
        );
        assert_eq!(
            (
                selected.candidate_counts().true_positive(),
                selected.candidate_counts().false_positive(),
                selected.candidate_counts().false_negative()
            ),
            (559, 1, 80)
        );
    }

    #[test]
    fn unknown_and_ambiguity_aggregates_are_frozen() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let report = evaluate(&root, EVALUATION_MANIFEST_PATH).expect("evaluate frozen heldout");
        let baseline = report.results().baseline();
        let selected = report.results().selected();
        assert_eq!(
            (
                baseline.unknown().expected(),
                baseline.unknown().correct(),
                baseline.unknown().unexpected()
            ),
            (4, 4, 80)
        );
        assert_eq!(
            (
                baseline.ambiguity().exact(),
                baseline.ambiguity().compatible(),
                baseline.ambiguity().unresolved(),
                baseline.ambiguity().total()
            ),
            (0, 2, 2, 2)
        );
        assert_eq!(
            (
                selected.ambiguity().exact(),
                selected.ambiguity().compatible(),
                selected.ambiguity().unresolved(),
                selected.ambiguity().total()
            ),
            (1, 2, 1, 2)
        );
    }

    #[test]
    fn reconciliation_rejects_mutated_totals() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let report = evaluate(&root, EVALUATION_MANIFEST_PATH).expect("evaluate frozen heldout");
        let mut metrics = report.results().baseline().clone();
        metrics.exact_token_sets.numerator += 1;
        assert!(validate_method_reconciliation(&metrics).is_err());

        let mut metrics = report.results().selected().clone();
        metrics.confusion_matrix.matrix[1][1] += 1;
        assert!(validate_method_reconciliation(&metrics).is_err());
    }
}
