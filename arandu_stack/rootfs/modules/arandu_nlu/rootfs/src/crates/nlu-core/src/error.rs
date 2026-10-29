use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CollectionKind {
    Hypotheses,
    PlanNodes,
    Relations,
    Slots,
    Evidence,
    RelationEvidence,
    ShareEvidence,
    IndependentPairs,
    ArgumentShares,
    ClarificationOptions,
    AggregateItems,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DuplicateKind {
    Hypothesis,
    PlanNode,
    Relation,
    Slot,
    Evidence,
    ClarificationOption,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SemanticPlanErrorKind {
    MissingClause,
    DuplicateClause,
    DanglingClause,
    ClauseEvidenceMismatch,
    MissingPredicate,
    DanglingArgument,
    MissingSlotSupport,
    ConflictingSlotSupport,
    PolarityEvidenceMismatch,
    MissingRelationEvidence,
    DuplicateRelationEvidence,
    DanglingRelationEvidence,
    SelfIndependentPair,
    DuplicateIndependentPair,
    DanglingIndependentPair,
    RelatedIndependentPair,
    UnclassifiedNodePair,
    DuplicateArgumentShare,
    DanglingArgumentShare,
    DuplicateShareDestination,
    ShareValueMismatch,
    ShareCycle,
    ContradictoryPolarity,
    ExecutionClassMismatch,
    CanonicalBytesTooLarge,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CoreError {
    InvalidUtf8,
    RequestTooLarge { limit: u32 },
    OffsetOverflow,
    SpanReversed,
    SpanOutOfRange,
    SpanNotCharBoundary,
    EmptySpan,
    SpanSourceMismatch,
    InvalidIdentifier,
    InvalidCatalogGeneration,
    StaleCatalogGeneration,
    ScoreOutOfRange,
    EmptyCollection { kind: CollectionKind },
    CollectionTooLarge { kind: CollectionKind, limit: u16 },
    Duplicate { kind: DuplicateKind },
    DanglingRelation,
    SelfRelation,
    RelationCycle,
    SemanticPlan(SemanticPlanErrorKind),
}

impl CoreError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidUtf8 => "invalid_utf8",
            Self::RequestTooLarge { .. } => "request_too_large",
            Self::OffsetOverflow => "offset_overflow",
            Self::SpanReversed => "span_reversed",
            Self::SpanOutOfRange => "span_out_of_range",
            Self::SpanNotCharBoundary => "span_not_char_boundary",
            Self::EmptySpan => "empty_span",
            Self::SpanSourceMismatch => "span_source_mismatch",
            Self::InvalidIdentifier => "invalid_identifier",
            Self::InvalidCatalogGeneration => "invalid_catalog_generation",
            Self::StaleCatalogGeneration => "stale_catalog_generation",
            Self::ScoreOutOfRange => "score_out_of_range",
            Self::EmptyCollection { .. } => "empty_collection",
            Self::CollectionTooLarge { .. } => "collection_too_large",
            Self::Duplicate { .. } => "duplicate",
            Self::DanglingRelation => "dangling_relation",
            Self::SelfRelation => "self_relation",
            Self::RelationCycle => "relation_cycle",
            Self::SemanticPlan(_) => "invalid_semantic_plan",
        }
    }
}

impl fmt::Display for CoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for CoreError {}
