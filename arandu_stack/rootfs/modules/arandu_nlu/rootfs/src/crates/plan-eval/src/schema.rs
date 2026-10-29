use serde::{Deserialize, Serialize};
use serde_json::Value;

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

#[derive(Debug, Deserialize)]
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02Context {
    pub(crate) catalog_generation: u64,
    pub(crate) session_snapshot_id: String,
}

#[derive(Debug, Deserialize)]
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedPlan {
    pub(crate) outcome: String,
    pub(crate) intent: String,
    pub(crate) catalog_generation: u64,
    pub(crate) nodes: Vec<P02ExpectedNode>,
    pub(crate) relations: Vec<P02ExpectedRelation>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedNode {
    pub(crate) id: String,
    pub(crate) capability: String,
    pub(crate) operation: String,
    pub(crate) slots: Vec<P02ExpectedSlot>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedSlot {
    pub(crate) id: String,
    pub(crate) kind: String,
    pub(crate) value: Value,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P02ExpectedRelation {
    pub(crate) from: String,
    pub(crate) to: String,
    pub(crate) kind: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P11Row {
    pub(crate) schema_version: u32,
    pub(crate) case_id: String,
    pub(crate) generator_record_id: String,
    pub(crate) canonical_semantic_id: String,
    pub(crate) specification_id: String,
    pub(crate) specification_version: String,
    pub(crate) source_id: String,
    pub(crate) source_type: String,
    pub(crate) corpus_id: String,
    pub(crate) corpus_version: String,
    pub(crate) generator_id: String,
    pub(crate) oracle_origin: String,
    pub(crate) oracle_authorizations: Vec<String>,
    pub(crate) oracle_derivation: String,
    pub(crate) license: String,
    pub(crate) locale: String,
    pub(crate) claim_scope: String,
    pub(crate) split: String,
    pub(crate) family: String,
    pub(crate) stratum: String,
    pub(crate) utterance: String,
    pub(crate) utterance_sha256: String,
    pub(crate) context: P11Context,
    pub(crate) expected: P11Expected,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P11Context {
    pub(crate) catalog_generation: u64,
    pub(crate) entities: Vec<P11ContextEntity>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P11ContextEntity {
    pub(crate) registry_id: String,
    pub(crate) entity_id: String,
    pub(crate) catalog_generation: u64,
    pub(crate) mention: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct P11Expected {
    pub(crate) outcome: String,
    #[serde(default)]
    pub(crate) intent: Option<String>,
    #[serde(default)]
    pub(crate) plan: Option<SemanticPlan>,
    #[serde(default)]
    pub(crate) reason: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticPlan {
    pub(crate) schema_version: String,
    pub(crate) catalog_generation: u64,
    pub(crate) execution_class: String,
    pub(crate) nodes: Vec<SemanticNode>,
    pub(crate) relations: Vec<SemanticRelation>,
    pub(crate) independent_pairs: Vec<SemanticIndependentPair>,
    pub(crate) argument_shares: Vec<SemanticArgumentShare>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticNode {
    pub(crate) id: String,
    pub(crate) intent: String,
    pub(crate) capability: String,
    pub(crate) operation: String,
    pub(crate) polarity: String,
    pub(crate) slots: Vec<SemanticSlot>,
    pub(crate) evidence: Vec<SemanticEvidence>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticSlot {
    pub(crate) id: String,
    pub(crate) kind: String,
    pub(crate) value: Value,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticEvidence {
    pub(crate) kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) slot: Option<String>,
    pub(crate) begin_byte: u32,
    pub(crate) end_byte: u32,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticRelation {
    pub(crate) from: String,
    pub(crate) to: String,
    pub(crate) kind: String,
    pub(crate) evidence: Vec<SemanticSpan>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticIndependentPair {
    pub(crate) left: String,
    pub(crate) right: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticArgumentShare {
    pub(crate) from_node: String,
    pub(crate) from_slot: String,
    pub(crate) to_node: String,
    pub(crate) to_slot: String,
    pub(crate) evidence: Vec<SemanticSpan>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SemanticSpan {
    pub(crate) begin_byte: u32,
    pub(crate) end_byte: u32,
}
