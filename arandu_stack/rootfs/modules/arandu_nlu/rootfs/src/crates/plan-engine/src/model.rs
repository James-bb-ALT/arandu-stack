use nlu_core::{SlotValue, Utf8Span};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DraftPolarity {
    Affirmed,
    Negated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DraftExecutionClass {
    PartialSafe,
    AtomicOnly,
    NonExecutable,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ResolvedBinding {
    pub(crate) slot_id: &'static str,
    pub(crate) value: SlotValue,
    pub(crate) evidence: Utf8Span,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct NodeDraft {
    pub(crate) id: &'static str,
    pub(crate) intent: &'static str,
    pub(crate) capability: &'static str,
    pub(crate) operation: &'static str,
    pub(crate) polarity: DraftPolarity,
    pub(crate) predicate: Utf8Span,
    pub(crate) negation: Option<Utf8Span>,
    pub(crate) slots: Vec<ResolvedBinding>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct RelationDraft {
    pub(crate) from_node: &'static str,
    pub(crate) to_node: &'static str,
    pub(crate) evidence: Vec<Utf8Span>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct IndependentPairDraft {
    pub(crate) left_node: &'static str,
    pub(crate) right_node: &'static str,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ArgumentShareDraft {
    pub(crate) from_node: &'static str,
    pub(crate) from_slot: &'static str,
    pub(crate) to_node: &'static str,
    pub(crate) to_slot: &'static str,
    pub(crate) evidence: Vec<Utf8Span>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct PlanDraft {
    pub(crate) execution_class: DraftExecutionClass,
    pub(crate) nodes: Vec<NodeDraft>,
    pub(crate) relations: Vec<RelationDraft>,
    pub(crate) independent_pairs: Vec<IndependentPairDraft>,
    pub(crate) argument_shares: Vec<ArgumentShareDraft>,
}
