#![forbid(unsafe_code)]

use core::fmt;
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    path::{Component, Path, PathBuf},
};

use lang_ptbr::{MORPHOLOGY_ANALYZER_ID, Morphology, MorphologyLookup};
use nlu_data::{
    DataError, DataErrorCode, MAX_RECORD_BYTES, MAX_RECORDS, canonical_json, parse_strict_json,
    read_bounded_root_file, sha256_hex,
};
use serde::{Deserialize, Serialize};

pub const EXPECTED_MANIFEST_SHA256: &str =
    "2a3ff2ffa947749010c735b1f45af31a9d2a477cdfefa7ed8f7cafabb288a0b7";

const REPORT_SCHEMA_VERSION: u32 = 1;
const MANIFEST_SCHEMA_VERSION: u32 = 1;
const MAX_MANIFEST_BYTES: usize = 1024 * 1024;
const MAX_ARTIFACT_BYTES: usize = 1024 * 1024;
const MAX_RELATIVE_PATH_BYTES: usize = 512;
const MAX_ANALYSES_PER_ROW: usize = 32;
const MAX_FEATURES_PER_ANALYSIS: usize = 32;

const DOMAIN: &str = "morphology";
const DATASET_ID: &str = "p07-morphology-internal-conformance";
const DATASET_VERSION: &str = "1.0.0";
const SPLIT_ID: &str = "p07-morphology-internal-conformance-v1";
const CLAIM_SCOPE: &str = "internal_conformance_only";
const LOCALE: &str = "pt-BR";
const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const SOURCE_MANIFEST_SHA256: &str =
    "d9e1ca32ec0f92aa6b5fc6c1d4f232f93389e0bf8031267d23daddf7287006c5";
const ARTIFACT_PATH: &str = "data/project-authored/p02-v1/morphology.jsonl";
const ARTIFACT_SHA256: &str = "ebee221611e4cbf6206a755022d163e4c96f7eb1a42626d7032773bfb8c793dc";
const ARTIFACT_RECORDS: u64 = 29;
const SURFACE_COUNT: u64 = 28;
const ANALYSIS_COUNT: u64 = 29;
const CASE_IDS_SHA256: &str = "6e481a12ebd00c62635e0fc13381993f96256987fe37ee67f66efdac8b1131bf";
const GROUPING: &str = "exact-surface-analysis-set-v1";
const METRIC_SPEC: &str = "exact-morphology-set-metrics-v1";
const RUNNER_ID: &str = "morphology-eval-v1";
const RUNTIME_PACKAGE_SHA256: &str =
    "ec24f2335f64d694931450fb5ad6aefe3e924d1e29b23e046f128491dfe79e27";
const EXPECTED_ANALYZER_ID: &str = "lang-ptbr-lexical-evidence-morphology-v1";
const GENERATOR_ID: &str = "p02-generator-v1";
const LICENSE: &str = "Apache-2.0";
const FIXTURE_MARKER: &str = "FIXTURE_TECNICA";

const CONFUSION_LABELS: [&str; 3] = ["unknown", "unique", "ambiguous"];
const ERROR_TAXONOMY: [&str; 3] = [
    "missing_analysis",
    "unexpected_analysis",
    "cardinality_mismatch",
];
const LIMITATIONS: [&str; 5] = [
    "project_authored_labels_share_generator_with_runtime_lexicon",
    "feature_bearing_source_analyses_only",
    "four_featureless_lexicon_analyses_unscored",
    "no_expected_unknown_surfaces",
    "internal_conformance_not_independent_accuracy_or_generalization",
];

pub type Result<T> = core::result::Result<T, EvaluationError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EvaluationErrorCode {
    InvalidArguments,
    InvalidManifest,
    InvalidArtifact,
    IntegrityMismatch,
    ResourceLimit,
    AnalyzerUnavailable,
    ReportEncoding,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EvaluationError {
    code: EvaluationErrorCode,
    context: String,
}

impl EvaluationError {
    fn new(code: EvaluationErrorCode, context: impl Into<String>) -> Self {
        let mut context = context.into();
        context.truncate(512);
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> EvaluationErrorCode {
        self.code
    }

    #[must_use]
    pub fn context(&self) -> &str {
        &self.context
    }
}

impl fmt::Display for EvaluationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.context)
    }
}

impl std::error::Error for EvaluationError {}

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd, Serialize)]
pub struct CanonicalAnalysis {
    lemma: String,
    pos: String,
    features: Vec<String>,
}

impl CanonicalAnalysis {
    #[must_use]
    pub fn lemma(&self) -> &str {
        &self.lemma
    }

    #[must_use]
    pub fn pos(&self) -> &str {
        &self.pos
    }

    #[must_use]
    pub fn features(&self) -> &[String] {
        &self.features
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FractionMetric {
    #[serde(flatten)]
    context: MetricContext,
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
pub struct AnalysisCountsMetric {
    #[serde(flatten)]
    context: MetricContext,
    true_positive: u64,
    false_positive: u64,
    false_negative: u64,
}

impl AnalysisCountsMetric {
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
pub struct ConfusionMatrixMetric {
    #[serde(flatten)]
    context: MetricContext,
    labels: Vec<&'static str>,
    matrix: [[u64; 3]; 3],
}

impl ConfusionMatrixMetric {
    #[must_use]
    pub fn labels(&self) -> &[&'static str] {
        &self.labels
    }

    #[must_use]
    pub const fn matrix(&self) -> &[[u64; 3]; 3] {
        &self.matrix
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvaluationMetrics {
    exact_surface_sets: FractionMetric,
    analysis_counts: AnalysisCountsMetric,
    analysis_precision: FractionMetric,
    analysis_recall: FractionMetric,
    ambiguity_preservation: FractionMetric,
    confusion_matrix: ConfusionMatrixMetric,
}

impl EvaluationMetrics {
    #[must_use]
    pub const fn exact_surface_sets(&self) -> &FractionMetric {
        &self.exact_surface_sets
    }

    #[must_use]
    pub const fn analysis_counts(&self) -> &AnalysisCountsMetric {
        &self.analysis_counts
    }

    #[must_use]
    pub const fn analysis_precision(&self) -> &FractionMetric {
        &self.analysis_precision
    }

    #[must_use]
    pub const fn analysis_recall(&self) -> &FractionMetric {
        &self.analysis_recall
    }

    #[must_use]
    pub const fn ambiguity_preservation(&self) -> &FractionMetric {
        &self.ambiguity_preservation
    }

    #[must_use]
    pub const fn confusion_matrix(&self) -> &ConfusionMatrixMetric {
        &self.confusion_matrix
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "error_type", rename_all = "snake_case")]
pub enum EvaluationIssue {
    MissingAnalysis {
        surface: String,
        case_ids: Vec<String>,
        expected_analysis: CanonicalAnalysis,
    },
    UnexpectedAnalysis {
        surface: String,
        case_ids: Vec<String>,
        observed_analysis: CanonicalAnalysis,
    },
    CardinalityMismatch {
        surface: String,
        case_ids: Vec<String>,
        expected: u64,
        observed: u64,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ErrorAnalysis {
    #[serde(flatten)]
    context: MetricContext,
    taxonomy: Vec<&'static str>,
    errors: Vec<EvaluationIssue>,
}

impl ErrorAnalysis {
    #[must_use]
    pub fn taxonomy(&self) -> &[&'static str] {
        &self.taxonomy
    }

    #[must_use]
    pub fn errors(&self) -> &[EvaluationIssue] {
        &self.errors
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvaluationReport {
    schema_version: u32,
    domain: &'static str,
    runner_id: &'static str,
    analyzer_id: &'static str,
    dataset_id: &'static str,
    dataset_version: &'static str,
    split_id: &'static str,
    claim_scope: &'static str,
    locale: &'static str,
    source_id: &'static str,
    source_manifest_sha256: &'static str,
    manifest_sha256: String,
    artifact_sha256: &'static str,
    case_ids_sha256: &'static str,
    runtime_package_sha256: &'static str,
    grouping: &'static str,
    metric_spec: &'static str,
    limitations: Vec<&'static str>,
    metrics: EvaluationMetrics,
    error_analysis: ErrorAnalysis,
}

impl EvaluationReport {
    #[must_use]
    pub const fn metrics(&self) -> &EvaluationMetrics {
        &self.metrics
    }

    #[must_use]
    pub const fn error_analysis(&self) -> &ErrorAnalysis {
        &self.error_analysis
    }

    #[must_use]
    pub fn manifest_sha256(&self) -> &str {
        &self.manifest_sha256
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
struct MetricContext {
    domain: &'static str,
    dataset_id: &'static str,
    dataset_version: &'static str,
    split_id: &'static str,
    claim_scope: &'static str,
    limitations: Vec<&'static str>,
}

impl MetricContext {
    fn frozen() -> Self {
        Self {
            domain: DOMAIN,
            dataset_id: DATASET_ID,
            dataset_version: DATASET_VERSION,
            split_id: SPLIT_ID,
            claim_scope: CLAIM_SCOPE,
            limitations: LIMITATIONS.to_vec(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
struct EvaluationManifest {
    schema_version: u32,
    dataset_id: String,
    dataset_version: String,
    split_id: String,
    claim_scope: String,
    locale: String,
    source_id: String,
    source_manifest_sha256: String,
    artifact_path: String,
    artifact_sha256: String,
    artifact_records: u64,
    surface_count: u64,
    analysis_count: u64,
    case_ids_sha256: String,
    grouping: String,
    metric_spec: String,
    confusion_labels: Vec<String>,
    error_taxonomy: Vec<String>,
    runner_id: String,
    runtime_package_sha256: String,
    limitations: Vec<String>,
}

impl EvaluationManifest {
    fn validate(&self) -> Result<()> {
        let scalar_fields = [
            ("dataset_id", self.dataset_id.as_str(), DATASET_ID),
            (
                "dataset_version",
                self.dataset_version.as_str(),
                DATASET_VERSION,
            ),
            ("split_id", self.split_id.as_str(), SPLIT_ID),
            ("claim_scope", self.claim_scope.as_str(), CLAIM_SCOPE),
            ("locale", self.locale.as_str(), LOCALE),
            ("source_id", self.source_id.as_str(), SOURCE_ID),
            (
                "source_manifest_sha256",
                self.source_manifest_sha256.as_str(),
                SOURCE_MANIFEST_SHA256,
            ),
            ("artifact_path", self.artifact_path.as_str(), ARTIFACT_PATH),
            (
                "artifact_sha256",
                self.artifact_sha256.as_str(),
                ARTIFACT_SHA256,
            ),
            (
                "case_ids_sha256",
                self.case_ids_sha256.as_str(),
                CASE_IDS_SHA256,
            ),
            ("grouping", self.grouping.as_str(), GROUPING),
            ("metric_spec", self.metric_spec.as_str(), METRIC_SPEC),
            ("runner_id", self.runner_id.as_str(), RUNNER_ID),
            (
                "runtime_package_sha256",
                self.runtime_package_sha256.as_str(),
                RUNTIME_PACKAGE_SHA256,
            ),
        ];
        if self.schema_version != MANIFEST_SCHEMA_VERSION {
            return invalid_manifest("schema_version");
        }
        for (field, actual, expected) in scalar_fields {
            if actual != expected {
                return invalid_manifest(field);
            }
        }
        if self.artifact_records != ARTIFACT_RECORDS {
            return invalid_manifest("artifact_records");
        }
        if self.surface_count != SURFACE_COUNT {
            return invalid_manifest("surface_count");
        }
        if self.analysis_count != ANALYSIS_COUNT {
            return invalid_manifest("analysis_count");
        }
        if self
            .confusion_labels
            .iter()
            .map(String::as_str)
            .ne(CONFUSION_LABELS)
        {
            return invalid_manifest("confusion_labels");
        }
        if self
            .error_taxonomy
            .iter()
            .map(String::as_str)
            .ne(ERROR_TAXONOMY)
        {
            return invalid_manifest("error_taxonomy");
        }
        if self.limitations.iter().map(String::as_str).ne(LIMITATIONS) {
            return invalid_manifest("limitations");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MorphologyRow {
    schema_version: u32,
    case_id: String,
    source_id: String,
    corpus_version: String,
    generator_id: String,
    license: String,
    locale: String,
    surface: String,
    expected_analyses: Vec<RowAnalysis>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RowAnalysis {
    lemma: String,
    pos: String,
    features: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExpectedDataset {
    surfaces: BTreeMap<String, ExpectedSurface>,
    case_ids: BTreeSet<String>,
    analysis_count: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExpectedSurface {
    analyses: BTreeMap<CanonicalAnalysis, Vec<String>>,
    case_ids: BTreeSet<String>,
}

impl ExpectedSurface {
    fn new() -> Self {
        Self {
            analyses: BTreeMap::new(),
            case_ids: BTreeSet::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ObservedSurface {
    analyses: BTreeSet<CanonicalAnalysis>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Score {
    exact_surface_sets: u64,
    surface_total: u64,
    true_positive: u64,
    false_positive: u64,
    false_negative: u64,
    ambiguity_preserved: u64,
    ambiguity_total: u64,
    confusion_matrix: [[u64; 3]; 3],
    errors: Vec<EvaluationIssue>,
}

#[derive(Debug)]
struct LoadedManifest {
    manifest: EvaluationManifest,
    sha256: String,
}

/// Evaluates the frozen morphology view using the bundled production analyzer.
pub fn evaluate(root: &Path, manifest_relative_path: &str) -> Result<EvaluationReport> {
    evaluate_with_manifest_pin(root, manifest_relative_path, EXPECTED_MANIFEST_SHA256)
}

/// Evaluates and serializes one canonical JSON report terminated by one newline.
pub fn evaluate_report_bytes(root: &Path, manifest_relative_path: &str) -> Result<Vec<u8>> {
    let report = evaluate(root, manifest_relative_path)?;
    canonical_report_bytes(&report)
}

/// Parses the exact command-line contract and returns buffered success output.
pub fn evaluate_cli<I, T>(arguments: I) -> Result<Vec<u8>>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let (root, manifest) = parse_cli_arguments(arguments)?;
    evaluate_report_bytes(&root, &manifest)
}

/// Serializes an already computed report without consulting ambient state.
pub fn canonical_report_bytes(report: &EvaluationReport) -> Result<Vec<u8>> {
    let value = serde_json::to_value(report).map_err(|error| {
        EvaluationError::new(
            EvaluationErrorCode::ReportEncoding,
            format!("report value: {error}"),
        )
    })?;
    let mut bytes = canonical_json(&value, "morphology evaluation report")
        .map_err(|error| map_data_error(EvaluationErrorCode::ReportEncoding, error))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn evaluate_with_manifest_pin(
    root: &Path,
    manifest_relative_path: &str,
    expected_manifest_sha256: &str,
) -> Result<EvaluationReport> {
    let loaded = load_manifest(root, manifest_relative_path, expected_manifest_sha256)?;
    let artifact = read_bounded_root_file(root, &loaded.manifest.artifact_path, MAX_ARTIFACT_BYTES)
        .map_err(|error| map_input_error(EvaluationErrorCode::InvalidArtifact, error))?;
    let artifact_sha256 = sha256_hex(&artifact)
        .map_err(|error| map_data_error(EvaluationErrorCode::InvalidArtifact, error))?;
    if artifact_sha256 != loaded.manifest.artifact_sha256 {
        return Err(EvaluationError::new(
            EvaluationErrorCode::IntegrityMismatch,
            "morphology artifact SHA-256",
        ));
    }

    let dataset = parse_artifact(&artifact, &loaded.manifest)?;
    if MORPHOLOGY_ANALYZER_ID != EXPECTED_ANALYZER_ID {
        return Err(EvaluationError::new(
            EvaluationErrorCode::AnalyzerUnavailable,
            "morphology analyzer identity",
        ));
    }
    let morphology = Morphology::bundled().map_err(|error| {
        EvaluationError::new(
            EvaluationErrorCode::AnalyzerUnavailable,
            format!("bundled morphology: {}", error.code()),
        )
    })?;
    let observations = observe_dataset(&morphology, &dataset)?;
    let score = score_dataset(&dataset, &observations)?;
    Ok(build_report(loaded.sha256, score))
}

fn load_manifest(
    root: &Path,
    relative_path: &str,
    expected_sha256: &str,
) -> Result<LoadedManifest> {
    validate_relative_path(relative_path)?;
    let bytes = read_bounded_root_file(root, relative_path, MAX_MANIFEST_BYTES)
        .map_err(|error| map_input_error(EvaluationErrorCode::InvalidManifest, error))?;
    parse_manifest_bytes(&bytes, expected_sha256)
}

fn parse_manifest_bytes(bytes: &[u8], expected_sha256: &str) -> Result<LoadedManifest> {
    let actual_sha256 = sha256_hex(bytes)
        .map_err(|error| map_data_error(EvaluationErrorCode::InvalidManifest, error))?;
    if actual_sha256 != expected_sha256 {
        return Err(EvaluationError::new(
            EvaluationErrorCode::IntegrityMismatch,
            "pinned evaluation manifest SHA-256",
        ));
    }
    if bytes.last() != Some(&b'\n') {
        return invalid_manifest("final newline");
    }
    let value = parse_strict_json(bytes, "morphology evaluation manifest")
        .map_err(|error| map_input_error(EvaluationErrorCode::InvalidManifest, error))?;
    let manifest: EvaluationManifest = serde_json::from_value(value).map_err(|error| {
        EvaluationError::new(
            EvaluationErrorCode::InvalidManifest,
            format!("closed manifest shape: {error}"),
        )
    })?;
    manifest.validate()?;
    Ok(LoadedManifest {
        manifest,
        sha256: actual_sha256,
    })
}

fn parse_artifact(bytes: &[u8], manifest: &EvaluationManifest) -> Result<ExpectedDataset> {
    if bytes.len() > MAX_ARTIFACT_BYTES {
        return Err(EvaluationError::new(
            EvaluationErrorCode::ResourceLimit,
            "morphology artifact bytes",
        ));
    }
    if bytes.is_empty() || bytes.last() != Some(&b'\n') {
        return invalid_artifact("final newline");
    }
    core::str::from_utf8(bytes).map_err(|_| {
        EvaluationError::new(
            EvaluationErrorCode::InvalidArtifact,
            "morphology artifact UTF-8",
        )
    })?;
    let record_count = bytes.iter().filter(|byte| **byte == b'\n').count();
    if record_count > MAX_RECORDS {
        return Err(EvaluationError::new(
            EvaluationErrorCode::ResourceLimit,
            "morphology row count",
        ));
    }

    let content = bytes
        .strip_suffix(b"\n")
        .ok_or_else(|| EvaluationError::new(EvaluationErrorCode::InvalidArtifact, "newline"))?;
    let mut dataset = ExpectedDataset {
        surfaces: BTreeMap::new(),
        case_ids: BTreeSet::new(),
        analysis_count: 0,
    };
    for (index, line) in content.split(|byte| *byte == b'\n').enumerate() {
        if line.is_empty() {
            return invalid_artifact("empty morphology row");
        }
        if line.len() > MAX_RECORD_BYTES {
            return Err(EvaluationError::new(
                EvaluationErrorCode::ResourceLimit,
                "morphology row bytes",
            ));
        }
        let value = parse_strict_json(line, &format!("morphology row {}", index + 1))
            .map_err(|error| map_input_error(EvaluationErrorCode::InvalidArtifact, error))?;
        let row: MorphologyRow = serde_json::from_value(value).map_err(|error| {
            EvaluationError::new(
                EvaluationErrorCode::InvalidArtifact,
                format!("closed morphology row {}: {error}", index + 1),
            )
        })?;
        add_row(&mut dataset, row)?;
    }

    let expected_records = usize::try_from(manifest.artifact_records).map_err(|_| {
        EvaluationError::new(
            EvaluationErrorCode::ResourceLimit,
            "manifest artifact record count",
        )
    })?;
    let expected_surfaces = usize::try_from(manifest.surface_count).map_err(|_| {
        EvaluationError::new(EvaluationErrorCode::ResourceLimit, "manifest surface count")
    })?;
    let expected_analyses = usize::try_from(manifest.analysis_count).map_err(|_| {
        EvaluationError::new(
            EvaluationErrorCode::ResourceLimit,
            "manifest analysis count",
        )
    })?;
    if record_count != expected_records
        || dataset.case_ids.len() != expected_records
        || dataset.surfaces.len() != expected_surfaces
        || dataset.analysis_count != expected_analyses
    {
        return Err(EvaluationError::new(
            EvaluationErrorCode::IntegrityMismatch,
            "morphology artifact inventory",
        ));
    }
    let case_ids = dataset.case_ids.iter().collect::<Vec<_>>();
    let case_ids_value = serde_json::to_value(case_ids).map_err(|error| {
        EvaluationError::new(
            EvaluationErrorCode::InvalidArtifact,
            format!("case ID inventory: {error}"),
        )
    })?;
    let case_ids_bytes = canonical_json(&case_ids_value, "morphology case IDs")
        .map_err(|error| map_data_error(EvaluationErrorCode::InvalidArtifact, error))?;
    let case_ids_sha256 = sha256_hex(&case_ids_bytes)
        .map_err(|error| map_data_error(EvaluationErrorCode::InvalidArtifact, error))?;
    if case_ids_sha256 != manifest.case_ids_sha256 {
        return Err(EvaluationError::new(
            EvaluationErrorCode::IntegrityMismatch,
            "morphology case ID inventory",
        ));
    }
    Ok(dataset)
}

fn add_row(dataset: &mut ExpectedDataset, row: MorphologyRow) -> Result<()> {
    if row.schema_version != 1
        || row.source_id != SOURCE_ID
        || row.corpus_version != DATASET_VERSION
        || row.generator_id != GENERATOR_ID
        || row.license != LICENSE
        || row.locale != LOCALE
    {
        return invalid_artifact("morphology row lineage");
    }
    if !valid_identifier(&row.case_id)
        || row.surface.is_empty()
        || contains_fixture_marker(&row.case_id)
        || contains_fixture_marker(&row.surface)
    {
        return invalid_artifact("morphology row identity");
    }
    if row.expected_analyses.is_empty() || row.expected_analyses.len() > MAX_ANALYSES_PER_ROW {
        return Err(EvaluationError::new(
            EvaluationErrorCode::ResourceLimit,
            "expected analyses per morphology row",
        ));
    }
    if !dataset.case_ids.insert(row.case_id.clone()) {
        return invalid_artifact("duplicate morphology case ID");
    }

    let surface = dataset
        .surfaces
        .entry(row.surface.clone())
        .or_insert_with(ExpectedSurface::new);
    surface.case_ids.insert(row.case_id.clone());
    for analysis in row.expected_analyses {
        let canonical = canonicalize_row_analysis(analysis)?;
        if surface
            .analyses
            .insert(canonical, vec![row.case_id.clone()])
            .is_some()
        {
            return invalid_artifact("duplicate expected morphology tuple");
        }
        dataset.analysis_count = dataset.analysis_count.checked_add(1).ok_or_else(|| {
            EvaluationError::new(
                EvaluationErrorCode::ResourceLimit,
                "expected analysis count",
            )
        })?;
    }
    Ok(())
}

fn canonicalize_row_analysis(analysis: RowAnalysis) -> Result<CanonicalAnalysis> {
    if analysis.lemma.is_empty()
        || analysis.pos.is_empty()
        || analysis.features.is_empty()
        || analysis.features.len() > MAX_FEATURES_PER_ANALYSIS
        || contains_fixture_marker(&analysis.lemma)
        || contains_fixture_marker(&analysis.pos)
        || analysis
            .features
            .iter()
            .any(|feature| feature.is_empty() || contains_fixture_marker(feature))
    {
        return invalid_artifact("expected morphology analysis");
    }
    let mut features = analysis.features;
    features.sort();
    if features.windows(2).any(|pair| pair[0] == pair[1]) {
        return invalid_artifact("duplicate morphology feature");
    }
    Ok(CanonicalAnalysis {
        lemma: analysis.lemma,
        pos: analysis.pos,
        features,
    })
}

fn observe_dataset(
    morphology: &Morphology,
    dataset: &ExpectedDataset,
) -> Result<BTreeMap<String, ObservedSurface>> {
    let mut observations = BTreeMap::new();
    for surface in dataset.surfaces.keys() {
        let mut analyses = BTreeSet::new();
        match morphology.analyze(surface) {
            MorphologyLookup::Unknown => {}
            MorphologyLookup::Unique(evidence) => {
                analyses.insert(canonicalize_runtime_analysis(evidence.analysis())?);
            }
            MorphologyLookup::Ambiguous(evidence) => {
                for analysis in evidence.analyses() {
                    analyses.insert(canonicalize_runtime_analysis(analysis)?);
                }
            }
        }
        observations.insert(surface.clone(), ObservedSurface { analyses });
    }
    Ok(observations)
}

fn canonicalize_runtime_analysis(
    analysis: &lang_ptbr::LexicalAnalysis,
) -> Result<CanonicalAnalysis> {
    let mut features = analysis.features().to_vec();
    features.sort();
    if analysis.lemma().is_empty()
        || contains_fixture_marker(analysis.lemma())
        || features
            .iter()
            .any(|feature| contains_fixture_marker(feature))
    {
        return Err(EvaluationError::new(
            EvaluationErrorCode::AnalyzerUnavailable,
            "invalid runtime morphology analysis",
        ));
    }
    Ok(CanonicalAnalysis {
        lemma: analysis.lemma().to_owned(),
        pos: analysis.category().code().to_owned(),
        features,
    })
}

fn score_dataset(
    dataset: &ExpectedDataset,
    observations: &BTreeMap<String, ObservedSurface>,
) -> Result<Score> {
    if observations
        .keys()
        .any(|surface| !dataset.surfaces.contains_key(surface))
    {
        return invalid_artifact("observation outside frozen surface set");
    }
    let mut score = Score {
        exact_surface_sets: 0,
        surface_total: usize_to_u64(dataset.surfaces.len(), "surface total")?,
        true_positive: 0,
        false_positive: 0,
        false_negative: 0,
        ambiguity_preserved: 0,
        ambiguity_total: 0,
        confusion_matrix: [[0; 3]; 3],
        errors: Vec::new(),
    };
    let empty = ObservedSurface {
        analyses: BTreeSet::new(),
    };
    for (surface_text, expected) in &dataset.surfaces {
        let observed = observations.get(surface_text).unwrap_or(&empty);
        let expected_set = expected.analyses.keys().cloned().collect::<BTreeSet<_>>();
        if expected_set == observed.analyses {
            checked_increment(&mut score.exact_surface_sets, "exact surface sets")?;
        }
        let true_positive = expected_set.intersection(&observed.analyses).count();
        let false_negative = expected_set.difference(&observed.analyses).count();
        let false_positive = observed.analyses.difference(&expected_set).count();
        checked_add_usize(
            &mut score.true_positive,
            true_positive,
            "true-positive analyses",
        )?;
        checked_add_usize(
            &mut score.false_negative,
            false_negative,
            "false-negative analyses",
        )?;
        checked_add_usize(
            &mut score.false_positive,
            false_positive,
            "false-positive analyses",
        )?;

        let expected_class = cardinality_class(expected_set.len());
        let observed_class = cardinality_class(observed.analyses.len());
        checked_increment(
            &mut score.confusion_matrix[expected_class][observed_class],
            "confusion matrix",
        )?;
        if expected_class == 2 {
            checked_increment(&mut score.ambiguity_total, "ambiguous surface total")?;
            if observed_class == 2 {
                checked_increment(
                    &mut score.ambiguity_preserved,
                    "preserved ambiguous surfaces",
                )?;
            }
        }

        for missing in expected_set.difference(&observed.analyses) {
            let case_ids = expected.analyses.get(missing).cloned().ok_or_else(|| {
                EvaluationError::new(
                    EvaluationErrorCode::InvalidArtifact,
                    "missing expected case identity",
                )
            })?;
            score.errors.push(EvaluationIssue::MissingAnalysis {
                surface: surface_text.clone(),
                case_ids,
                expected_analysis: missing.clone(),
            });
        }
        let surface_case_ids = expected.case_ids.iter().cloned().collect::<Vec<_>>();
        for unexpected in observed.analyses.difference(&expected_set) {
            score.errors.push(EvaluationIssue::UnexpectedAnalysis {
                surface: surface_text.clone(),
                case_ids: surface_case_ids.clone(),
                observed_analysis: unexpected.clone(),
            });
        }
        if expected_set.len() != observed.analyses.len() {
            score.errors.push(EvaluationIssue::CardinalityMismatch {
                surface: surface_text.clone(),
                case_ids: surface_case_ids,
                expected: usize_to_u64(expected_set.len(), "expected cardinality")?,
                observed: usize_to_u64(observed.analyses.len(), "observed cardinality")?,
            });
        }
    }
    let matrix_total = score
        .confusion_matrix
        .iter()
        .flatten()
        .try_fold(0_u64, |total, value| total.checked_add(*value))
        .ok_or_else(|| {
            EvaluationError::new(EvaluationErrorCode::ResourceLimit, "confusion matrix total")
        })?;
    if matrix_total != score.surface_total {
        return invalid_artifact("confusion matrix reconciliation");
    }
    Ok(score)
}

fn build_report(manifest_sha256: String, score: Score) -> EvaluationReport {
    let precision_denominator = score.true_positive + score.false_positive;
    let recall_denominator = score.true_positive + score.false_negative;
    let metrics = EvaluationMetrics {
        exact_surface_sets: FractionMetric {
            context: MetricContext::frozen(),
            numerator: score.exact_surface_sets,
            denominator: score.surface_total,
        },
        analysis_counts: AnalysisCountsMetric {
            context: MetricContext::frozen(),
            true_positive: score.true_positive,
            false_positive: score.false_positive,
            false_negative: score.false_negative,
        },
        analysis_precision: FractionMetric {
            context: MetricContext::frozen(),
            numerator: score.true_positive,
            denominator: precision_denominator,
        },
        analysis_recall: FractionMetric {
            context: MetricContext::frozen(),
            numerator: score.true_positive,
            denominator: recall_denominator,
        },
        ambiguity_preservation: FractionMetric {
            context: MetricContext::frozen(),
            numerator: score.ambiguity_preserved,
            denominator: score.ambiguity_total,
        },
        confusion_matrix: ConfusionMatrixMetric {
            context: MetricContext::frozen(),
            labels: CONFUSION_LABELS.to_vec(),
            matrix: score.confusion_matrix,
        },
    };
    EvaluationReport {
        schema_version: REPORT_SCHEMA_VERSION,
        domain: DOMAIN,
        runner_id: RUNNER_ID,
        analyzer_id: MORPHOLOGY_ANALYZER_ID,
        dataset_id: DATASET_ID,
        dataset_version: DATASET_VERSION,
        split_id: SPLIT_ID,
        claim_scope: CLAIM_SCOPE,
        locale: LOCALE,
        source_id: SOURCE_ID,
        source_manifest_sha256: SOURCE_MANIFEST_SHA256,
        manifest_sha256,
        artifact_sha256: ARTIFACT_SHA256,
        case_ids_sha256: CASE_IDS_SHA256,
        runtime_package_sha256: RUNTIME_PACKAGE_SHA256,
        grouping: GROUPING,
        metric_spec: METRIC_SPEC,
        limitations: LIMITATIONS.to_vec(),
        error_analysis: ErrorAnalysis {
            context: MetricContext::frozen(),
            taxonomy: ERROR_TAXONOMY.to_vec(),
            errors: score.errors,
        },
        metrics,
    }
}

fn parse_cli_arguments<I, T>(arguments: I) -> Result<(PathBuf, String)>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let mut arguments = arguments.into_iter().map(Into::into);
    arguments.next().ok_or_else(|| {
        EvaluationError::new(
            EvaluationErrorCode::InvalidArguments,
            "missing executable argument",
        )
    })?;
    let mut root = None;
    let mut manifest = None;
    while let Some(flag) = arguments.next() {
        match flag.to_str() {
            Some("--root") if root.is_none() => {
                root = Some(PathBuf::from(arguments.next().ok_or_else(|| {
                    EvaluationError::new(
                        EvaluationErrorCode::InvalidArguments,
                        "--root requires one value",
                    )
                })?));
            }
            Some("--manifest") if manifest.is_none() => {
                let value = arguments.next().ok_or_else(|| {
                    EvaluationError::new(
                        EvaluationErrorCode::InvalidArguments,
                        "--manifest requires one value",
                    )
                })?;
                manifest = Some(value.into_string().map_err(|_| {
                    EvaluationError::new(
                        EvaluationErrorCode::InvalidArguments,
                        "manifest path must be UTF-8",
                    )
                })?);
            }
            Some("--root" | "--manifest") => {
                return Err(EvaluationError::new(
                    EvaluationErrorCode::InvalidArguments,
                    "duplicate command-line option",
                ));
            }
            _ => {
                return Err(EvaluationError::new(
                    EvaluationErrorCode::InvalidArguments,
                    "expected --root ROOT --manifest RELATIVE_PATH",
                ));
            }
        }
    }
    let root = root.ok_or_else(|| {
        EvaluationError::new(EvaluationErrorCode::InvalidArguments, "missing --root")
    })?;
    let manifest = manifest.ok_or_else(|| {
        EvaluationError::new(EvaluationErrorCode::InvalidArguments, "missing --manifest")
    })?;
    validate_relative_path(&manifest)?;
    Ok((root, manifest))
}

fn validate_relative_path(path: &str) -> Result<()> {
    if path.is_empty()
        || path.len() > MAX_RELATIVE_PATH_BYTES
        || path.contains('\\')
        || !Path::new(path)
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err(EvaluationError::new(
            EvaluationErrorCode::InvalidArguments,
            "manifest must be a canonical relative path",
        ));
    }
    Ok(())
}

fn cardinality_class(cardinality: usize) -> usize {
    match cardinality {
        0 => 0,
        1 => 1,
        _ => 2,
    }
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn contains_fixture_marker(value: &str) -> bool {
    value.contains(FIXTURE_MARKER)
}

fn checked_increment(value: &mut u64, context: &str) -> Result<()> {
    *value = value
        .checked_add(1)
        .ok_or_else(|| EvaluationError::new(EvaluationErrorCode::ResourceLimit, context))?;
    Ok(())
}

fn checked_add_usize(value: &mut u64, increment: usize, context: &str) -> Result<()> {
    let increment = usize_to_u64(increment, context)?;
    *value = value
        .checked_add(increment)
        .ok_or_else(|| EvaluationError::new(EvaluationErrorCode::ResourceLimit, context))?;
    Ok(())
}

fn usize_to_u64(value: usize, context: &str) -> Result<u64> {
    u64::try_from(value)
        .map_err(|_| EvaluationError::new(EvaluationErrorCode::ResourceLimit, context))
}

fn map_input_error(default_code: EvaluationErrorCode, error: DataError) -> EvaluationError {
    let code = match error.code() {
        DataErrorCode::IntegrityMismatch => EvaluationErrorCode::IntegrityMismatch,
        DataErrorCode::ResourceLimit => EvaluationErrorCode::ResourceLimit,
        _ => default_code,
    };
    EvaluationError::new(code, error.context())
}

fn map_data_error(code: EvaluationErrorCode, error: DataError) -> EvaluationError {
    let code = if error.code() == DataErrorCode::ResourceLimit {
        EvaluationErrorCode::ResourceLimit
    } else {
        code
    };
    EvaluationError::new(code, error.context())
}

fn invalid_manifest<T>(context: &str) -> Result<T> {
    Err(EvaluationError::new(
        EvaluationErrorCode::InvalidManifest,
        context,
    ))
}

fn invalid_artifact<T>(context: &str) -> Result<T> {
    Err(EvaluationError::new(
        EvaluationErrorCode::InvalidArtifact,
        context,
    ))
}

#[cfg(test)]
mod tests {
    use std::{ffi::OsString, path::PathBuf};

    use serde_json::Value;

    use super::{
        ANALYSIS_COUNT, ARTIFACT_RECORDS, CASE_IDS_SHA256, CONFUSION_LABELS, ERROR_TAXONOMY,
        EXPECTED_MANIFEST_SHA256, EvaluationErrorCode, EvaluationIssue, MAX_ARTIFACT_BYTES,
        MAX_RECORD_BYTES, MAX_RECORDS, Morphology, ObservedSurface, canonical_json,
        canonical_report_bytes, evaluate, evaluate_report_bytes, observe_dataset, parse_artifact,
        parse_cli_arguments, parse_manifest_bytes, parse_strict_json, score_dataset, sha256_hex,
    };

    const MANIFEST_PATH: &str = "data/evaluation/p07/morphology-v1/manifest.json";
    const MANIFEST_BYTES: &[u8] =
        include_bytes!("../../../data/evaluation/p07/morphology-v1/manifest.json");
    const ARTIFACT_BYTES: &[u8] =
        include_bytes!("../../../data/project-authored/p02-v1/morphology.jsonl");

    fn repository_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
    }

    fn manifest() -> super::EvaluationManifest {
        parse_manifest_bytes(MANIFEST_BYTES, EXPECTED_MANIFEST_SHA256)
            .expect("frozen manifest")
            .manifest
    }

    #[test]
    fn exact_baseline_is_canonical_and_reproducible() {
        let root = repository_root();
        let report = evaluate(&root, MANIFEST_PATH).expect("baseline evaluation");
        let metrics = report.metrics();
        assert_eq!(
            (
                metrics.exact_surface_sets().numerator(),
                metrics.exact_surface_sets().denominator()
            ),
            (28, 28)
        );
        assert_eq!(
            (
                metrics.analysis_counts().true_positive(),
                metrics.analysis_counts().false_positive(),
                metrics.analysis_counts().false_negative()
            ),
            (29, 0, 0)
        );
        assert_eq!(
            (
                metrics.analysis_precision().numerator(),
                metrics.analysis_precision().denominator()
            ),
            (29, 29)
        );
        assert_eq!(
            (
                metrics.analysis_recall().numerator(),
                metrics.analysis_recall().denominator()
            ),
            (29, 29)
        );
        assert_eq!(
            (
                metrics.ambiguity_preservation().numerator(),
                metrics.ambiguity_preservation().denominator()
            ),
            (1, 1)
        );
        assert_eq!(
            metrics.confusion_matrix().labels(),
            CONFUSION_LABELS.as_slice()
        );
        assert_eq!(
            metrics.confusion_matrix().matrix(),
            &[[0, 0, 0], [0, 27, 0], [0, 0, 1]]
        );
        assert_eq!(report.error_analysis().taxonomy(), ERROR_TAXONOMY);
        assert!(report.error_analysis().errors().is_empty());
        assert_eq!(report.manifest_sha256(), EXPECTED_MANIFEST_SHA256);

        let first = canonical_report_bytes(&report).expect("canonical report");
        let second = evaluate_report_bytes(&root, MANIFEST_PATH).expect("second evaluation");
        assert_eq!(first, second);
        assert_eq!(first.last(), Some(&b'\n'));
        assert_ne!(first.get(first.len().saturating_sub(2)), Some(&b'\n'));
        assert!(
            !first
                .windows(root.as_os_str().as_encoded_bytes().len())
                .any(|window| window == root.as_os_str().as_encoded_bytes())
        );
        parse_strict_json(&first, "report").expect("strict report JSON");
    }

    #[test]
    fn row_permutation_does_not_change_grouping_or_score() {
        let manifest = manifest();
        let original = parse_artifact(ARTIFACT_BYTES, &manifest).expect("original grouping");
        let mut rows = ARTIFACT_BYTES
            .strip_suffix(b"\n")
            .expect("final newline")
            .split(|byte| *byte == b'\n')
            .collect::<Vec<_>>();
        rows.reverse();
        let mut permuted_bytes = rows
            .iter()
            .flat_map(|row| row.iter().copied().chain(*b"\n"))
            .collect::<Vec<_>>();
        let permuted = parse_artifact(&permuted_bytes, &manifest).expect("permuted grouping");
        permuted_bytes.fill(0);
        assert_eq!(original, permuted);

        let morphology = Morphology::bundled().expect("bundled analyzer");
        let original_observations =
            observe_dataset(&morphology, &original).expect("original observations");
        let permuted_observations =
            observe_dataset(&morphology, &permuted).expect("permuted observations");
        assert_eq!(original_observations, permuted_observations);
        assert_eq!(
            score_dataset(&original, &original_observations).expect("original score"),
            score_dataset(&permuted, &permuted_observations).expect("permuted score")
        );
    }

    #[test]
    fn missing_admitted_analysis_has_stable_nonzero_errors_and_matrix() {
        let dataset = parse_artifact(ARTIFACT_BYTES, &manifest()).expect("dataset");
        let morphology = Morphology::bundled().expect("bundled analyzer");
        let mut observations = observe_dataset(&morphology, &dataset).expect("observations");
        let (surface, expected) = dataset
            .surfaces
            .iter()
            .find(|(_, expected)| expected.analyses.len() == 2)
            .expect("ambiguous admitted surface");
        let removed = expected.analyses.keys().next().expect("analysis").clone();
        assert!(
            observations
                .get_mut(surface)
                .expect("observed surface")
                .analyses
                .remove(&removed)
        );

        let first = score_dataset(&dataset, &observations).expect("mutated score");
        let second = score_dataset(&dataset, &observations).expect("stable mutated score");
        assert_eq!(first, second);
        assert_eq!(
            (
                first.exact_surface_sets,
                first.surface_total,
                first.true_positive,
                first.false_positive,
                first.false_negative,
                first.ambiguity_preserved,
                first.ambiguity_total
            ),
            (27, 28, 28, 0, 1, 0, 1)
        );
        assert_eq!(first.confusion_matrix, [[0, 0, 0], [0, 27, 0], [0, 1, 0]]);
        assert_eq!(first.errors.len(), 2);
        assert!(matches!(
            &first.errors[0],
            EvaluationIssue::MissingAnalysis {
                expected_analysis,
                ..
            } if expected_analysis == &removed
        ));
        assert!(matches!(
            first.errors[1],
            EvaluationIssue::CardinalityMismatch {
                expected: 2,
                observed: 1,
                ..
            }
        ));
    }

    #[test]
    fn rejects_malformed_duplicate_and_fixture_rows() {
        let manifest = manifest();
        let first_line = ARTIFACT_BYTES
            .split(|byte| *byte == b'\n')
            .next()
            .expect("first row");

        let mut duplicate_case = ARTIFACT_BYTES.to_vec();
        duplicate_case.extend_from_slice(first_line);
        duplicate_case.push(b'\n');
        let error = parse_artifact(&duplicate_case, &manifest).expect_err("duplicate case");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);
        assert!(error.context().contains("duplicate morphology case ID"));

        let mut duplicated_key = br#"{"schema_version":1,"schema_version":1}"#.to_vec();
        duplicated_key.push(b'\n');
        let error = parse_artifact(&duplicated_key, &manifest).expect_err("duplicate key");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);

        let malformed_utf8 = [0xff, b'\n'];
        let error = parse_artifact(&malformed_utf8, &manifest).expect_err("malformed UTF-8");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);

        let error = parse_artifact(
            ARTIFACT_BYTES.strip_suffix(b"\n").expect("final newline"),
            &manifest,
        )
        .expect_err("missing final newline");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);

        let mut row_value = parse_strict_json(first_line, "first row").expect("row JSON");
        row_value["surface"] = Value::String("FIXTURE_TECNICA.surface".to_owned());
        let mut fixture_row = canonical_json(&row_value, "fixture row").expect("encode row");
        fixture_row.push(b'\n');
        let error = parse_artifact(&fixture_row, &manifest).expect_err("fixture marker");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);
    }

    #[test]
    fn rejects_duplicate_expected_tuple_before_inventory_scoring() {
        let first_line = ARTIFACT_BYTES
            .split(|byte| *byte == b'\n')
            .next()
            .expect("first row");
        let mut row = parse_strict_json(first_line, "first row").expect("row JSON");
        let analyses = row["expected_analyses"]
            .as_array_mut()
            .expect("expected analyses");
        analyses.push(analyses[0].clone());
        let mut bytes = canonical_json(&row, "duplicated tuple row").expect("encode row");
        bytes.push(b'\n');
        let error = parse_artifact(&bytes, &manifest()).expect_err("duplicate tuple");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);
        assert!(
            error
                .context()
                .contains("duplicate expected morphology tuple")
        );
    }

    #[test]
    fn manifest_is_pinned_before_shape_or_artifact_fields_are_used() {
        let malformed = b"{\n";
        let error =
            parse_manifest_bytes(malformed, EXPECTED_MANIFEST_SHA256).expect_err("wrong pin");
        assert_eq!(error.code(), EvaluationErrorCode::IntegrityMismatch);

        let prefix = b"{\"analysis_count\":29,";
        let mut duplicate = b"{\"analysis_count\":29,\"analysis_count\":29,".to_vec();
        duplicate.extend_from_slice(
            MANIFEST_BYTES
                .strip_prefix(prefix)
                .expect("manifest field prefix"),
        );
        let duplicate_sha256 = sha256_hex(&duplicate).expect("duplicate digest");
        let error =
            parse_manifest_bytes(&duplicate, &duplicate_sha256).expect_err("duplicate field");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidManifest);

        let mut unknown = parse_strict_json(MANIFEST_BYTES, "manifest").expect("manifest JSON");
        unknown
            .as_object_mut()
            .expect("manifest object")
            .insert("FIXTURE_TECNICA".to_owned(), Value::from(0));
        let mut unknown = canonical_json(&unknown, "unknown-field manifest").expect("encode");
        unknown.push(b'\n');
        let unknown_sha256 = sha256_hex(&unknown).expect("unknown-field digest");
        let error = parse_manifest_bytes(&unknown, &unknown_sha256).expect_err("unknown field");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidManifest);

        let mut value = parse_strict_json(MANIFEST_BYTES, "manifest").expect("manifest JSON");
        value["schema_version"] = Value::from(2);
        let mut changed = canonical_json(&value, "changed manifest").expect("encode manifest");
        changed.push(b'\n');
        let changed_sha256 = sha256_hex(&changed).expect("changed digest");
        let error = parse_manifest_bytes(&changed, &changed_sha256).expect_err("wrong constant");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidManifest);

        let without_newline = MANIFEST_BYTES
            .strip_suffix(b"\n")
            .expect("manifest newline");
        let without_newline_sha256 = sha256_hex(without_newline).expect("digest");
        let error = parse_manifest_bytes(without_newline, &without_newline_sha256)
            .expect_err("manifest newline");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidManifest);
    }

    #[test]
    fn enforces_artifact_row_and_record_bounds() {
        let manifest = manifest();
        let oversized_artifact = vec![b' '; MAX_ARTIFACT_BYTES + 1];
        let error =
            parse_artifact(&oversized_artifact, &manifest).expect_err("artifact byte bound");
        assert_eq!(error.code(), EvaluationErrorCode::ResourceLimit);

        let mut oversized_row = vec![b' '; MAX_RECORD_BYTES + 1];
        oversized_row.push(b'\n');
        let error = parse_artifact(&oversized_row, &manifest).expect_err("row byte bound");
        assert_eq!(error.code(), EvaluationErrorCode::ResourceLimit);

        let excessive_rows = vec![b'\n'; MAX_RECORDS + 1];
        let error = parse_artifact(&excessive_rows, &manifest).expect_err("row count bound");
        assert_eq!(error.code(), EvaluationErrorCode::ResourceLimit);
    }

    #[test]
    fn frozen_inventory_constants_match_admitted_bytes() {
        let dataset = parse_artifact(ARTIFACT_BYTES, &manifest()).expect("dataset");
        assert_eq!(dataset.case_ids.len() as u64, ARTIFACT_RECORDS);
        assert_eq!(dataset.analysis_count as u64, ANALYSIS_COUNT);
        let case_ids = dataset.case_ids.iter().collect::<Vec<_>>();
        let value = serde_json::to_value(case_ids).expect("case IDs");
        let bytes = canonical_json(&value, "case IDs").expect("canonical case IDs");
        assert_eq!(sha256_hex(&bytes).expect("case ID hash"), CASE_IDS_SHA256);
    }

    #[test]
    fn command_line_parser_rejects_missing_duplicate_and_unsafe_arguments() {
        for arguments in [
            vec![OsString::from("morphology-eval")],
            vec![
                OsString::from("morphology-eval"),
                OsString::from("--root"),
                OsString::from("."),
            ],
            vec![
                OsString::from("morphology-eval"),
                OsString::from("--root"),
                OsString::from("."),
                OsString::from("--root"),
                OsString::from("."),
                OsString::from("--manifest"),
                OsString::from(MANIFEST_PATH),
            ],
            vec![
                OsString::from("morphology-eval"),
                OsString::from("--root"),
                OsString::from("."),
                OsString::from("--manifest"),
                OsString::from("../manifest.json"),
            ],
        ] {
            let error = parse_cli_arguments(arguments).expect_err("invalid arguments");
            assert_eq!(error.code(), EvaluationErrorCode::InvalidArguments);
        }

        let (root, manifest) = parse_cli_arguments([
            OsString::from("morphology-eval"),
            OsString::from("--manifest"),
            OsString::from(MANIFEST_PATH),
            OsString::from("--root"),
            repository_root().into_os_string(),
        ])
        .expect("valid arguments");
        assert!(root.is_absolute());
        assert_eq!(manifest, MANIFEST_PATH);
    }

    #[test]
    fn scorer_rejects_observations_outside_the_frozen_surface_set() {
        let dataset = parse_artifact(ARTIFACT_BYTES, &manifest()).expect("dataset");
        let mut observations = BTreeMap::new();
        observations.insert(
            "FIXTURE_TECNICA.surface".to_owned(),
            ObservedSurface {
                analyses: BTreeSet::new(),
            },
        );
        let error = score_dataset(&dataset, &observations).expect_err("foreign observation");
        assert_eq!(error.code(), EvaluationErrorCode::InvalidArtifact);
    }

    use std::collections::{BTreeMap, BTreeSet};
}
