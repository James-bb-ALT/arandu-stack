#![forbid(unsafe_code)]

mod error;
mod id;
mod limits;
mod outcome;
mod plan;
mod providers;
mod semantic_plan;
mod text;
mod value;

pub use error::{CollectionKind, CoreError, DuplicateKind, SemanticPlanErrorKind};
pub use id::{
    BinaryId, CapabilityId, ConfigurationId, EntityId, IntentId, InvocationId, LanguagePackageId,
    NodeId, OperationId, OptionId, SessionSnapshotId, SlotId,
};
pub use limits::{
    MAX_AGGREGATE_ITEMS, MAX_ARGUMENT_SHARES, MAX_CANONICAL_PLAN_BYTES, MAX_CLARIFICATION_OPTIONS,
    MAX_EVIDENCE_SPANS, MAX_HYPOTHESES, MAX_IDENTIFIER_BYTES, MAX_INDEPENDENT_PAIRS,
    MAX_PLAN_NODES, MAX_RELATION_EVIDENCE_SPANS, MAX_RELATIONS, MAX_REQUEST_BYTES,
    MAX_SLOTS_PER_NODE,
};
pub use outcome::{AbstentionReason, Clarification, ClarificationOption, SemanticOutcome};
pub use plan::{Plan, PlanNode, Relation, RelationKind};
pub use providers::{
    IdentifierProvider, LogicalClock, LogicalTime, SemanticEnvelope, SemanticEnvelopeParts,
};
pub use semantic_plan::{
    ArgumentEndpoint, ArgumentShare, ClauseSemantics, ComposedPlan, EvidenceAtom, EvidenceKind,
    GraphExecutionClass, IndependentPair, Polarity, RelationEvidence,
};
pub use text::{RequestText, Utf8Span};
pub use value::{
    CatalogGeneration, Confidence, EntityRef, Hypothesis, HypothesisSet, Slot, SlotValue,
};
