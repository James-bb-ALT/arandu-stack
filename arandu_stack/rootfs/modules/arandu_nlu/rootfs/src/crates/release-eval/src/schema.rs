use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) enum EvaluationSplit {
    Heldout,
    Performance,
}

impl EvaluationSplit {
    pub(crate) const fn code(self) -> &'static str {
        match self {
            Self::Heldout => "heldout",
            Self::Performance => "performance",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Manifest {
    pub(crate) schema_version: u32,
    pub(crate) corpus: ManifestCorpus,
    pub(crate) contract: ManifestContract,
    pub(crate) freeze: ManifestFreeze,
    pub(crate) quotas: ManifestQuotas,
    pub(crate) taxonomies: ManifestTaxonomies,
    pub(crate) artifacts: Vec<ManifestArtifact>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestCorpus {
    pub(crate) id: String,
    pub(crate) version: String,
    pub(crate) status: String,
    pub(crate) authorization: String,
    pub(crate) locale: String,
    pub(crate) license: String,
    pub(crate) claim_scope: String,
    pub(crate) generator_id: String,
    pub(crate) oracle_origin: String,
    pub(crate) specification_sha256: String,
    pub(crate) generator_sha256: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestContract {
    pub(crate) source_id: String,
    pub(crate) commit: String,
    pub(crate) path: String,
    pub(crate) sha256: String,
    pub(crate) allowed_use: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestFreeze {
    pub(crate) state: String,
    pub(crate) sequence: String,
    pub(crate) before_nlu_implementation: bool,
    pub(crate) family_disjoint: bool,
    pub(crate) text_disjoint: bool,
    pub(crate) semantic_identity_disjoint: bool,
    pub(crate) heldout_access_after_freeze: String,
    pub(crate) self_oracle_allowed: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestQuotas {
    pub(crate) minimum_scored_cases: u64,
    pub(crate) minimum_per_supported_stratum: u64,
    pub(crate) train_per_intent: u64,
    pub(crate) development_per_intent: u64,
    pub(crate) heldout_per_intent: u64,
    pub(crate) performance_per_intent: u64,
    pub(crate) pos_per_split: u64,
    pub(crate) weighting: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestTaxonomies {
    pub(crate) dimensions: Vec<String>,
    pub(crate) intents: Vec<String>,
    pub(crate) heldout_counts: BTreeMap<String, BTreeMap<String, u64>>,
    pub(crate) performance_counts: BTreeMap<String, BTreeMap<String, u64>>,
    pub(crate) suite_classes: BTreeMap<String, Vec<String>>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestArtifact {
    pub(crate) path: String,
    pub(crate) bytes: u64,
    pub(crate) sha256: String,
    pub(crate) records: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P09Projection {
    pub(crate) schema_version: u32,
    pub(crate) projection_id: String,
    pub(crate) schema_id: String,
    pub(crate) source: P09ProjectionSource,
    pub(crate) span_derivation: SpanDerivation,
    pub(crate) intent_projections: Vec<IntentProjection>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P09ProjectionSource {
    pub(crate) source_id: String,
    pub(crate) corpus_version: String,
    pub(crate) generator_id: String,
    pub(crate) oracle_origin: String,
    pub(crate) specification_sha256: String,
    pub(crate) generator_sha256: String,
    pub(crate) development_sha256: String,
    pub(crate) development_records: u64,
    pub(crate) claim_scope: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SpanDerivation {
    pub(crate) algorithm: String,
    pub(crate) require_unique_match: bool,
    pub(crate) source_output_allowed: bool,
    pub(crate) normalization_allowed: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct IntentProjection {
    pub(crate) external_intent: String,
    pub(crate) intent_id: String,
    pub(crate) slots: Vec<SlotProjection>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SlotProjection {
    pub(crate) expected_slot_id: String,
    pub(crate) expected_kind: String,
    pub(crate) slot_id: String,
    pub(crate) role: String,
    pub(crate) occurrence: u16,
    pub(crate) parameter: String,
    pub(crate) transform: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P11Projection {
    pub(crate) schema_version: u32,
    pub(crate) projection_id: String,
    pub(crate) semantic_plan_schema: String,
    pub(crate) source: P11ProjectionSource,
    pub(crate) pre_resolution_projection: ProjectionBinding,
    pub(crate) catalog_projection: CatalogProjection,
    pub(crate) evidence_projection: EvidenceProjection,
    pub(crate) graph_shapes: Vec<GraphShapeProjection>,
    pub(crate) limitations: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P11ProjectionSource {
    pub(crate) source_id: String,
    pub(crate) corpus_version: String,
    pub(crate) generator_id: String,
    pub(crate) oracle_origin: String,
    pub(crate) specification_sha256: String,
    pub(crate) generator_sha256: String,
    pub(crate) train_sha256: String,
    pub(crate) development_sha256: String,
    pub(crate) records_per_split: u64,
    pub(crate) claim_scope: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProjectionBinding {
    pub(crate) path: String,
    pub(crate) sha256: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CatalogProjection {
    pub(crate) generation: u64,
    pub(crate) registry_id_algorithm: String,
    pub(crate) core_entity_id_prefix: String,
    pub(crate) mention_source: String,
    pub(crate) display_matching_allowed: bool,
    pub(crate) nlu_output_allowed: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct EvidenceProjection {
    pub(crate) argument: String,
    pub(crate) single_predicate: String,
    pub(crate) parallel_predicate: String,
    pub(crate) normalization_allowed: bool,
    pub(crate) nlu_output_allowed: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct GraphShapeProjection {
    pub(crate) shape: String,
    pub(crate) required_external_intent: Option<String>,
    pub(crate) node_ids: Vec<String>,
    pub(crate) slot_occurrences: Option<Vec<u16>>,
    pub(crate) execution_class: String,
    pub(crate) secondary_capability: Option<String>,
    pub(crate) secondary_operation: Option<String>,
    pub(crate) secondary_predicate_cues: Option<SplitCue>,
    pub(crate) relation_cues: Option<SplitCue>,
    pub(crate) relations: Vec<ProjectedRelation>,
    pub(crate) independent_pairs: Vec<ProjectedIndependentPair>,
    pub(crate) argument_shares: Vec<ProjectedArgumentShare>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SplitCue {
    pub(crate) train: String,
    pub(crate) development: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProjectedRelation {
    pub(crate) from: String,
    pub(crate) to: String,
    pub(crate) kind: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProjectedIndependentPair {
    pub(crate) left: String,
    pub(crate) right: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProjectedArgumentShare {
    pub(crate) from_node: String,
    pub(crate) from_slot: String,
    pub(crate) to_node: String,
    pub(crate) to_slot: String,
    pub(crate) evidence: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02Row {
    pub(crate) schema_version: u32,
    pub(crate) case_id: String,
    pub(crate) generator_record_id: String,
    pub(crate) canonical_semantic_id: String,
    pub(crate) source_id: String,
    pub(crate) corpus_version: String,
    pub(crate) generator_id: String,
    pub(crate) oracle_origin: String,
    pub(crate) license: String,
    pub(crate) locale: String,
    pub(crate) split: String,
    pub(crate) utterance: String,
    pub(crate) utterance_sha256: String,
    pub(crate) context: P02Context,
    pub(crate) dimensions: P02Dimensions,
    pub(crate) expected: P02ExpectedPlan,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02Context {
    pub(crate) catalog_generation: u64,
    pub(crate) session_snapshot_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02Dimensions {
    pub(crate) source: String,
    pub(crate) family: String,
    pub(crate) intent: String,
    pub(crate) domain: String,
    pub(crate) slot_kind: String,
    pub(crate) graph_shape: String,
    pub(crate) outcome: String,
    pub(crate) ambiguity: String,
    pub(crate) noise: String,
    pub(crate) target_cardinality: String,
}

impl P02Dimensions {
    pub(crate) fn frozen_values(&self) -> [(&'static str, &str); 9] {
        [
            ("source", &self.source),
            ("family", &self.family),
            ("intent", &self.intent),
            ("domain", &self.domain),
            ("slot_kind", &self.slot_kind),
            ("graph_shape", &self.graph_shape),
            ("outcome", &self.outcome),
            ("ambiguity", &self.ambiguity),
            ("noise", &self.noise),
        ]
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedPlan {
    pub(crate) outcome: String,
    pub(crate) intent: String,
    pub(crate) catalog_generation: u64,
    pub(crate) nodes: Vec<P02ExpectedNode>,
    pub(crate) relations: Vec<P02ExpectedRelation>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedNode {
    pub(crate) id: String,
    pub(crate) capability: String,
    pub(crate) operation: String,
    pub(crate) slots: Vec<P02ExpectedSlot>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedSlot {
    pub(crate) id: String,
    pub(crate) kind: String,
    pub(crate) value: Value,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedRelation {
    pub(crate) from: String,
    pub(crate) to: String,
    pub(crate) kind: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct NegativeRow {
    pub(crate) schema_version: u32,
    pub(crate) suite_id: String,
    pub(crate) case_id: String,
    pub(crate) generator_record_id: String,
    pub(crate) canonical_semantic_id: String,
    pub(crate) coverage_class: String,
    pub(crate) source_id: String,
    pub(crate) corpus_version: String,
    pub(crate) generator_id: String,
    pub(crate) oracle_origin: String,
    pub(crate) license: String,
    pub(crate) locale: String,
    pub(crate) utterance: String,
    pub(crate) utterance_sha256: String,
    pub(crate) context: NegativeContext,
    pub(crate) expected: NegativeExpected,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct NegativeContext {
    pub(crate) condition: String,
    pub(crate) catalog_generation: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct NegativeExpected {
    pub(crate) outcome: String,
    pub(crate) reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum ExpectedValue {
    Entity { id: String, generation: u64 },
    Integer(i64),
    Text(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ExpectedSlot {
    pub(crate) id: String,
    pub(crate) value: ExpectedValue,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ExpectedNode {
    pub(crate) id: String,
    pub(crate) intent: String,
    pub(crate) capability: String,
    pub(crate) operation: String,
    pub(crate) polarity: String,
    pub(crate) slots: Vec<ExpectedSlot>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ExpectedGraph {
    pub(crate) catalog_generation: u64,
    pub(crate) execution_class: String,
    pub(crate) nodes: Vec<ExpectedNode>,
    pub(crate) relations: Vec<(String, String, String)>,
    pub(crate) independent_pairs: Vec<(String, String)>,
    pub(crate) argument_shares: Vec<(String, String, String, String)>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct CatalogEntity {
    pub(crate) registry_id: String,
    pub(crate) external_id: String,
    pub(crate) domain: String,
    pub(crate) mention: String,
    pub(crate) capabilities: Vec<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct EvaluationCase {
    pub(crate) ordinal: u64,
    pub(crate) utterance: String,
    pub(crate) word_count: u64,
    pub(crate) dimensions: P02Dimensions,
    pub(crate) expected: ExpectedGraph,
    pub(crate) catalog_entities: Vec<CatalogEntity>,
}

#[derive(Clone, Debug)]
pub(crate) struct NegativeCase {
    pub(crate) ordinal: u64,
    pub(crate) suite_id: String,
    pub(crate) expected_outcome: String,
    pub(crate) catalog_generation: u64,
    pub(crate) utterance: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct SourceIdentity {
    pub(crate) source_id: &'static str,
    pub(crate) corpus_version: &'static str,
    pub(crate) claim_scope: &'static str,
    pub(crate) manifest_sha256: &'static str,
    pub(crate) specification_sha256: &'static str,
    pub(crate) projection_sha256: BTreeMap<&'static str, &'static str>,
    pub(crate) split_sha256: &'static str,
    pub(crate) records: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct WilsonInterval {
    pub(crate) lower_ppm: u64,
    pub(crate) upper_ppm: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Metric {
    pub(crate) numerator: u64,
    pub(crate) denominator: u64,
    pub(crate) rate_ppm: Option<u64>,
    pub(crate) wilson_95: Option<WilsonInterval>,
    pub(crate) support: &'static str,
    pub(crate) minimum_supported_denominator: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SemanticMetricSet {
    pub(crate) intent_exact: Metric,
    pub(crate) slot_exact: Metric,
    pub(crate) entity_exact: Metric,
    pub(crate) graph_exact: Metric,
    pub(crate) final_outcome_exact: Metric,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct OutcomeMetrics {
    pub(crate) plans: u64,
    pub(crate) clarifications: u64,
    pub(crate) abstentions_or_denials: u64,
    pub(crate) errors: u64,
    pub(crate) clarification_exact: Metric,
    pub(crate) abstention_exact: Metric,
    pub(crate) false_plan_rate: Metric,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct StratumReport {
    pub(crate) value: String,
    pub(crate) records: u64,
    pub(crate) metrics: SemanticMetricSet,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MacroMetric {
    pub(crate) strata_included: u64,
    pub(crate) mean_rate_ppm: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MacroMetricSet {
    pub(crate) intent_exact: MacroMetric,
    pub(crate) slot_exact: MacroMetric,
    pub(crate) entity_exact: MacroMetric,
    pub(crate) graph_exact: MacroMetric,
    pub(crate) final_outcome_exact: MacroMetric,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct NegativeSuiteReport {
    pub(crate) suite: String,
    pub(crate) expected_records: u64,
    pub(crate) observed_records: u64,
    pub(crate) false_plans: u64,
    pub(crate) zero_false_plan_gate: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct InsufficientStratum {
    pub(crate) metric: &'static str,
    pub(crate) dimension: &'static str,
    pub(crate) value: &'static str,
    pub(crate) eligible_records: u64,
    pub(crate) required_records: u64,
    pub(crate) disposition: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Reconciliation {
    pub(crate) expected_records: u64,
    pub(crate) observed_records: u64,
    pub(crate) dimension_denominators_complete: bool,
    pub(crate) negative_suite_denominators_complete: bool,
    pub(crate) complete: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct AggregateReport {
    pub(crate) schema_version: u32,
    pub(crate) runner_id: &'static str,
    pub(crate) metric_specification: &'static str,
    pub(crate) split: &'static str,
    pub(crate) source: SourceIdentity,
    pub(crate) reconciliation: Reconciliation,
    pub(crate) overall: SemanticMetricSet,
    pub(crate) outcomes: OutcomeMetrics,
    pub(crate) dimensions: BTreeMap<String, Vec<StratumReport>>,
    pub(crate) macro_by_dimension: BTreeMap<String, MacroMetricSet>,
    pub(crate) negative_suites: Vec<NegativeSuiteReport>,
    pub(crate) insufficiently_evaluated: Vec<InsufficientStratum>,
    pub(crate) limitations: Vec<&'static str>,
}

impl AggregateReport {
    #[must_use]
    pub const fn reconciliation(&self) -> &Reconciliation {
        &self.reconciliation
    }

    #[must_use]
    pub const fn overall(&self) -> &SemanticMetricSet {
        &self.overall
    }
}

impl Reconciliation {
    #[must_use]
    pub const fn complete(&self) -> bool {
        self.complete
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct InputSummary {
    pub(crate) schema_version: u32,
    pub(crate) heldout_records: u64,
    pub(crate) performance_records: u64,
    pub(crate) negative_records: u64,
    pub(crate) frozen_dimensions: Vec<&'static str>,
    pub(crate) hash_admission: &'static str,
}

impl InputSummary {
    #[must_use]
    pub const fn heldout_records(&self) -> u64 {
        self.heldout_records
    }

    #[must_use]
    pub const fn performance_records(&self) -> u64 {
        self.performance_records
    }

    #[must_use]
    pub const fn negative_records(&self) -> u64 {
        self.negative_records
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ArtifactSizeInput {
    pub label: String,
    pub bytes: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct BenchmarkIdentityInput {
    pub cpu_model: Option<String>,
    pub architecture: Option<String>,
    pub core_allocation: Option<String>,
    pub operating_system: Option<String>,
    pub kernel: Option<String>,
    pub compiler: Option<String>,
    pub build_profile: Option<String>,
    pub thread_count: Option<u32>,
    pub runner_executable_sha256: Option<String>,
    pub source_commit: Option<String>,
    pub source_tree: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BenchmarkIdentityReport {
    pub(crate) cpu_model: Option<String>,
    pub(crate) architecture: Option<String>,
    pub(crate) core_allocation: Option<String>,
    pub(crate) operating_system: Option<String>,
    pub(crate) kernel: Option<String>,
    pub(crate) compiler: Option<String>,
    pub(crate) build_profile: Option<String>,
    pub(crate) thread_count: Option<u32>,
    pub(crate) single_thread_asserted: bool,
    pub(crate) missing_fields: Vec<&'static str>,
    pub(crate) complete: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RunnerBinding {
    pub(crate) runner_id: &'static str,
    pub(crate) runner_schema_id: &'static str,
    pub(crate) report_schema_id: &'static str,
    pub(crate) package_version: &'static str,
    pub(crate) executable_sha256: Option<String>,
    pub(crate) source_commit: Option<String>,
    pub(crate) source_tree: Option<String>,
    pub(crate) missing_fields: Vec<&'static str>,
    pub(crate) complete: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BenchmarkCorpusIdentity {
    pub(crate) source_id: &'static str,
    pub(crate) corpus_version: &'static str,
    pub(crate) claim_scope: &'static str,
    pub(crate) split: &'static str,
    pub(crate) split_sha256: &'static str,
    pub(crate) manifest_sha256: &'static str,
    pub(crate) specification_sha256: &'static str,
    pub(crate) projection_sha256: BTreeMap<&'static str, &'static str>,
    pub(crate) utterances_per_cycle: u64,
    pub(crate) words_per_cycle: u64,
    pub(crate) word_counter_id: &'static str,
    pub(crate) unicode_version: &'static str,
    pub(crate) frozen_dimensions: Vec<&'static str>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RawBenchmarkSample {
    pub(crate) run: u32,
    pub(crate) elapsed_ns: u64,
    pub(crate) complete_corpus_cycles: u32,
    pub(crate) utterances_per_cycle: u64,
    pub(crate) words_per_cycle: u64,
    pub(crate) total_utterances: u64,
    pub(crate) total_words: u64,
    pub(crate) eligible_utterances: u64,
    pub(crate) eligible_words: u64,
    pub(crate) words_per_second_milli: u64,
    pub(crate) utterances_per_second_milli: u64,
    pub(crate) cycle_semantic_signature_sha256: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BenchmarkPercentiles {
    pub(crate) p50_ns: u64,
    pub(crate) p95_ns: u64,
    pub(crate) p99_ns: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BenchmarkSummary {
    pub(crate) elapsed_ns: BenchmarkPercentiles,
    pub(crate) median_words_per_second_milli: u64,
    pub(crate) median_utterances_per_second_milli: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct StratumPreflight {
    pub(crate) records: u64,
    pub(crate) words: u64,
    pub(crate) eligible_utterances: u64,
    pub(crate) eligible_words: u64,
    pub(crate) semantic_signature_sha256: String,
    pub(crate) exact_semantic_pass: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct StratumBenchmark {
    pub(crate) value: String,
    pub(crate) preflight: StratumPreflight,
    pub(crate) release_gate_eligible: bool,
    pub(crate) threshold_met: Option<bool>,
    pub(crate) raw_samples: Vec<RawBenchmarkSample>,
    pub(crate) summary: BenchmarkSummary,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BoundaryBenchmark {
    pub(crate) boundary_id: &'static str,
    pub(crate) definition: &'static str,
    pub(crate) release_gate: &'static str,
    pub(crate) release_gate_eligible: bool,
    pub(crate) threshold_metric: Option<&'static str>,
    pub(crate) threshold_value_milli: Option<u64>,
    pub(crate) threshold_met: Option<bool>,
    pub(crate) all_strata_threshold_met: Option<bool>,
    pub(crate) single_thread_asserted: bool,
    pub(crate) warmups: u32,
    pub(crate) measured_runs: u32,
    pub(crate) minimum_sample_elapsed_ns: u64,
    pub(crate) maximum_complete_corpus_cycles: u32,
    pub(crate) preflight_semantic_signature_sha256: String,
    pub(crate) preflight_exact_semantic_pass: bool,
    pub(crate) raw_samples: Vec<RawBenchmarkSample>,
    pub(crate) summary: BenchmarkSummary,
    pub(crate) strata: BTreeMap<String, Vec<StratumBenchmark>>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ExternalE2EStatus {
    pub(crate) boundary_id: &'static str,
    pub(crate) required_components: Vec<&'static str>,
    pub(crate) status: &'static str,
    pub(crate) release_gate_eligible: bool,
    pub(crate) reason: &'static str,
    pub(crate) required_warmups: u32,
    pub(crate) required_measured_runs: u32,
    pub(crate) required_metric: &'static str,
    pub(crate) required_threshold_value_milli: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BenchmarkReport {
    pub(crate) schema_version: u32,
    pub(crate) benchmark_specification: &'static str,
    pub(crate) semantic_preflight_sha256: String,
    pub(crate) semantic_preflight_reconciled: bool,
    pub(crate) identity: BenchmarkIdentityReport,
    pub(crate) runner: RunnerBinding,
    pub(crate) corpus: BenchmarkCorpusIdentity,
    pub(crate) complete_release_gate_eligible: bool,
    pub(crate) startup_ns: u64,
    pub(crate) peak_rss_bytes: Option<u64>,
    pub(crate) artifact_sizes: BTreeMap<String, u64>,
    pub(crate) artifact_size_total: u64,
    pub(crate) boundaries: Vec<BoundaryBenchmark>,
    pub(crate) external_e2e: ExternalE2EStatus,
}
