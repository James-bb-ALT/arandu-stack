#![forbid(unsafe_code)]

mod conflict;
mod core_adapter;
mod error;
mod model;
mod pattern;
mod resolution;
mod table;

#[cfg(test)]
mod internal_contract_tests;

use core::fmt;

use ha_catalog::{CatalogSnapshot, EntityClarification};
use intent_engine::IntentMatch;
use nlu_core::{
    CapabilityId, CatalogGeneration, ComposedPlan, EntityRef, MAX_CLARIFICATION_OPTIONS, NodeId,
    RequestText, SlotId, SlotValue, Utf8Span,
};

pub use error::{PlanEngineError, PlanEngineErrorCode, Result};

use crate::{
    conflict::has_semantic_conflict,
    model::{
        ArgumentShareDraft, IndependentPairDraft, NodeDraft, PlanDraft, RelationDraft,
        ResolvedBinding,
    },
    pattern::{PatternFailure, SupportedPattern},
    resolution::{
        BindingResolution, PendingBindingResolution, ResolutionFailure, ResumableBindingResolution,
    },
    table::{BindingKind, Shape, TemplateSpec},
};

pub const PLAN_COMPOSITION_SCHEMA_ID: &str = "p11-semantic-plan-v1";
pub const PLAN_COMPOSITION_ALGORITHM_ID: &str = "p11-closed-train-template-composer-v1";
pub const MAX_COMPOSITION_TEMPLATES: usize = 32;

const NODE_1: &str = "p11:node_1";
const NODE_2: &str = "p11:node_2";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum CompositionAbstentionReason {
    UnsupportedIntent,
    UnsupportedPattern,
    IncompleteClause,
    NegationScope,
    EntityResolution,
    MultipleEntityClarifications,
    StaleCatalogGeneration,
    SemanticConflict,
}

impl CompositionAbstentionReason {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::UnsupportedIntent => "unsupported_intent",
            Self::UnsupportedPattern => "unsupported_pattern",
            Self::IncompleteClause => "incomplete_clause",
            Self::NegationScope => "negation_scope",
            Self::EntityResolution => "entity_resolution",
            Self::MultipleEntityClarifications => "multiple_entity_clarifications",
            Self::StaleCatalogGeneration => "stale_catalog_generation",
            Self::SemanticConflict => "semantic_conflict",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CompositionOutcome {
    Plan(ComposedPlan),
    EntityClarification(EntityClarification),
    Abstention(CompositionAbstentionReason),
}

#[derive(Debug, Eq, PartialEq)]
pub struct PendingEndpoint {
    node: NodeId,
    slot: SlotId,
}

impl PendingEndpoint {
    #[must_use]
    pub const fn node(&self) -> &NodeId {
        &self.node
    }

    #[must_use]
    pub const fn slot(&self) -> &SlotId {
        &self.slot
    }
}

/// A source-bound composition with exactly one unresolved entity binding.
///
/// The value is intentionally non-cloneable so a caller must transfer ownership
/// when it attempts completion.
///
/// ```compile_fail
/// fn requires_clone<T: Clone>() {}
/// requires_clone::<plan_engine::PendingEntityComposition>();
/// ```
pub struct PendingEntityComposition {
    source: RequestText,
    template: &'static TemplateSpec,
    pattern: SupportedPattern,
    catalog_generation: CatalogGeneration,
    capability: CapabilityId,
    endpoint: PendingEndpoint,
    candidates: Box<[EntityRef]>,
    unresolved_index: usize,
    unresolved_evidence: Utf8Span,
    resolved_bindings: Vec<(usize, ResolvedBinding)>,
}

impl PendingEntityComposition {
    #[must_use]
    pub const fn capability(&self) -> &CapabilityId {
        &self.capability
    }

    #[must_use]
    pub const fn catalog_generation(&self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub const fn endpoint(&self) -> &PendingEndpoint {
        &self.endpoint
    }

    #[must_use]
    pub fn candidates(&self) -> &[EntityRef] {
        &self.candidates
    }

    pub fn complete(self, selected: EntityRef) -> Result<Option<ComposedPlan>> {
        if selected.generation() != self.catalog_generation
            || self.candidates.binary_search(&selected).is_err()
        {
            return Ok(None);
        }

        let expected_endpoint = pending_endpoint(self.template, self.unresolved_index)?;
        if self.endpoint != expected_endpoint
            || self.capability.as_str() != self.template.capability
        {
            return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
        }

        let Self {
            source,
            template,
            pattern,
            catalog_generation,
            unresolved_index,
            unresolved_evidence,
            resolved_bindings,
            ..
        } = self;
        let expected = template
            .bindings
            .get(unresolved_index)
            .ok_or_else(invalid_configuration)?;
        if expected.kind != BindingKind::Mention {
            return Err(invalid_configuration());
        }

        let mut bindings = (0..template.bindings.len())
            .map(|_| None)
            .collect::<Vec<Option<ResolvedBinding>>>();
        for (index, binding) in resolved_bindings {
            let Some(destination) = bindings.get_mut(index) else {
                return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
            };
            if index == unresolved_index
                || binding.slot_id != template.bindings[index].slot_id
                || destination.replace(binding).is_some()
            {
                return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
            }
        }
        let unresolved = bindings
            .get_mut(unresolved_index)
            .ok_or_else(invalid_configuration)?;
        if unresolved.is_some() {
            return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
        }
        *unresolved = Some(ResolvedBinding {
            slot_id: expected.slot_id,
            value: SlotValue::Entity(selected),
            evidence: unresolved_evidence,
        });
        let bindings = bindings
            .into_iter()
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| PlanEngineError::new(PlanEngineErrorCode::CatalogContract))?;

        let draft = build_draft(template, pattern, bindings)?;
        if has_semantic_conflict(&draft.nodes) {
            return Ok(None);
        }
        core_adapter::build(&source, catalog_generation, draft).map(Some)
    }
}

impl fmt::Debug for PendingEntityComposition {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PendingEntityComposition")
            .field("source_bytes", &self.source.len())
            .field("candidate_count", &self.candidates.len())
            .field("resolved_binding_count", &self.resolved_bindings.len())
            .finish()
    }
}

#[derive(Debug)]
pub enum ResumableCompositionOutcome {
    Plan(ComposedPlan),
    Pending(PendingEntityComposition),
    Abstention(CompositionAbstentionReason),
}

#[derive(Clone, Copy)]
pub struct PlanEngine;

impl PlanEngine {
    pub fn new() -> Result<Self> {
        table::validate()?;
        Ok(Self)
    }

    pub fn bundled() -> Result<Self> {
        Self::new()
    }

    pub fn compose(
        &self,
        source: &RequestText,
        intent_match: &IntentMatch,
        snapshot: &CatalogSnapshot,
    ) -> Result<CompositionOutcome> {
        let Some(template) = table::lookup(intent_match.intent()) else {
            return Ok(CompositionOutcome::Abstention(
                CompositionAbstentionReason::UnsupportedIntent,
            ));
        };
        let (supported, bindings) = match pattern::validate(source, intent_match, template)? {
            Ok(value) => value,
            Err(failure) => {
                return Ok(CompositionOutcome::Abstention(map_pattern_failure(failure)));
            }
        };

        let capability_evidence = capability_evidence(template, &supported)?;
        let resolved = match resolution::resolve_bindings(
            source,
            snapshot,
            template,
            &bindings,
            &capability_evidence,
        )? {
            BindingResolution::Resolved(bindings) => bindings,
            BindingResolution::Clarification(options) => {
                return Ok(CompositionOutcome::EntityClarification(options));
            }
            BindingResolution::Abstention(reason) => {
                return Ok(CompositionOutcome::Abstention(map_resolution_failure(
                    reason,
                )));
            }
        };

        let draft = build_draft(template, supported, resolved)?;
        if has_semantic_conflict(&draft.nodes) {
            return Ok(CompositionOutcome::Abstention(
                CompositionAbstentionReason::SemanticConflict,
            ));
        }
        core_adapter::build(source, snapshot.generation(), draft).map(CompositionOutcome::Plan)
    }

    pub fn compose_resumable(
        &self,
        source: &RequestText,
        intent_match: &IntentMatch,
        snapshot: &CatalogSnapshot,
    ) -> Result<ResumableCompositionOutcome> {
        let Some(template) = table::lookup(intent_match.intent()) else {
            return Ok(ResumableCompositionOutcome::Abstention(
                CompositionAbstentionReason::UnsupportedIntent,
            ));
        };
        let (supported, bindings) = match pattern::validate(source, intent_match, template)? {
            Ok(value) => value,
            Err(failure) => {
                return Ok(ResumableCompositionOutcome::Abstention(
                    map_pattern_failure(failure),
                ));
            }
        };

        let capability_evidence = capability_evidence(template, &supported)?;
        let resolved = resolution::resolve_bindings_resumable(
            source,
            snapshot,
            template,
            &bindings,
            &capability_evidence,
        )?;
        let resolved = match resolved {
            ResumableBindingResolution::Resolved(bindings) => bindings,
            ResumableBindingResolution::Pending(pending) => {
                return pending_composition(
                    source,
                    snapshot.generation(),
                    template,
                    supported,
                    pending,
                );
            }
            ResumableBindingResolution::Abstention(reason) => {
                return Ok(ResumableCompositionOutcome::Abstention(
                    map_resolution_failure(reason),
                ));
            }
        };

        let draft = build_draft(template, supported, resolved)?;
        if has_semantic_conflict(&draft.nodes) {
            return Ok(ResumableCompositionOutcome::Abstention(
                CompositionAbstentionReason::SemanticConflict,
            ));
        }
        core_adapter::build(source, snapshot.generation(), draft)
            .map(ResumableCompositionOutcome::Plan)
    }
}

impl fmt::Debug for PlanEngine {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PlanEngine")
            .field("template_count", &table::TEMPLATE_COUNT)
            .finish()
    }
}

fn pending_composition(
    source: &RequestText,
    catalog_generation: CatalogGeneration,
    template: &'static TemplateSpec,
    pattern: SupportedPattern,
    pending: PendingBindingResolution,
) -> Result<ResumableCompositionOutcome> {
    let PendingBindingResolution {
        clarification,
        unresolved_index,
        unresolved_evidence,
        resolved,
    } = pending;
    if resolved.len().checked_add(1) != Some(template.bindings.len())
        || !unresolved_evidence.belongs_to(source)
        || resolved.iter().any(|(index, binding)| {
            *index >= template.bindings.len()
                || *index == unresolved_index
                || binding.slot_id != template.bindings[*index].slot_id
                || !binding.evidence.belongs_to(source)
        })
        || resolved.windows(2).any(|pair| pair[0].0 == pair[1].0)
    {
        return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
    }
    if resolved.iter().any(|(_, binding)| {
        matches!(
            &binding.value,
            SlotValue::Entity(entity) if entity.generation() != catalog_generation
        )
    }) {
        return Ok(ResumableCompositionOutcome::Abstention(
            CompositionAbstentionReason::StaleCatalogGeneration,
        ));
    }

    let mut candidates = clarification
        .options()
        .iter()
        .map(|option| option.entity().clone())
        .collect::<Vec<_>>();
    if candidates.len() < 2 || candidates.len() > MAX_CLARIFICATION_OPTIONS {
        return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
    }
    if candidates
        .iter()
        .any(|candidate| candidate.generation() != catalog_generation)
    {
        return Ok(ResumableCompositionOutcome::Abstention(
            CompositionAbstentionReason::StaleCatalogGeneration,
        ));
    }
    candidates.sort();
    if candidates.windows(2).any(|pair| pair[0] == pair[1]) {
        return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
    }

    let capability = CapabilityId::new(template.capability).map_err(|_| invalid_configuration())?;
    let endpoint = pending_endpoint(template, unresolved_index)?;
    Ok(ResumableCompositionOutcome::Pending(
        PendingEntityComposition {
            source: source.clone(),
            template,
            pattern,
            catalog_generation,
            capability,
            endpoint,
            candidates: candidates.into_boxed_slice(),
            unresolved_index,
            unresolved_evidence,
            resolved_bindings: resolved,
        },
    ))
}

fn pending_endpoint(template: &TemplateSpec, binding_index: usize) -> Result<PendingEndpoint> {
    let binding = template
        .bindings
        .get(binding_index)
        .ok_or_else(invalid_configuration)?;
    if binding.kind != BindingKind::Mention {
        return Err(invalid_configuration());
    }
    let node = match template.shape {
        Shape::Single | Shape::OrderedTimer => NODE_1,
        Shape::ParallelTurnOn => match binding_index {
            0 => NODE_1,
            1 => NODE_2,
            _ => return Err(invalid_configuration()),
        },
    };
    Ok(PendingEndpoint {
        node: NodeId::new(node).map_err(|_| invalid_configuration())?,
        slot: SlotId::new(binding.slot_id).map_err(|_| invalid_configuration())?,
    })
}

fn capability_evidence(
    template: &TemplateSpec,
    pattern: &SupportedPattern,
) -> Result<Vec<Utf8Span>> {
    let evidence = match template.shape {
        Shape::Single => {
            let predicate = pattern
                .clauses
                .first()
                .ok_or_else(invalid_configuration)?
                .predicate
                .clone();
            vec![predicate; template.bindings.len()]
        }
        Shape::ParallelTurnOn => {
            if template.bindings.len() != 2 || pattern.clauses.len() != 2 {
                return Err(invalid_configuration());
            }
            vec![
                pattern.clauses[0].predicate.clone(),
                pattern.clauses[1].predicate.clone(),
            ]
        }
        Shape::OrderedTimer => {
            let predicate = pattern
                .clauses
                .first()
                .ok_or_else(invalid_configuration)?
                .predicate
                .clone();
            vec![predicate; template.bindings.len()]
        }
    };
    Ok(evidence)
}

fn build_draft(
    template: &TemplateSpec,
    pattern: SupportedPattern,
    mut bindings: Vec<ResolvedBinding>,
) -> Result<PlanDraft> {
    match template.shape {
        Shape::Single => {
            if pattern.clauses.len() != 1 {
                return Err(invalid_configuration());
            }
            let clause = &pattern.clauses[0];
            Ok(PlanDraft {
                execution_class: pattern.execution_class,
                nodes: vec![NodeDraft {
                    id: NODE_1,
                    intent: template.intent,
                    capability: template.capability,
                    operation: template.operation,
                    polarity: clause.polarity,
                    predicate: clause.predicate.clone(),
                    negation: clause.negation.clone(),
                    slots: bindings,
                }],
                relations: Vec::new(),
                independent_pairs: Vec::new(),
                argument_shares: Vec::new(),
            })
        }
        Shape::ParallelTurnOn => {
            if pattern.clauses.len() != 2 || bindings.len() != 2 {
                return Err(invalid_configuration());
            }
            let second_binding = bindings.pop().ok_or_else(invalid_configuration)?;
            let first_binding = bindings.pop().ok_or_else(invalid_configuration)?;
            Ok(PlanDraft {
                execution_class: pattern.execution_class,
                nodes: vec![
                    NodeDraft {
                        id: NODE_1,
                        intent: template.intent,
                        capability: template.capability,
                        operation: template.operation,
                        polarity: pattern.clauses[0].polarity,
                        predicate: pattern.clauses[0].predicate.clone(),
                        negation: pattern.clauses[0].negation.clone(),
                        slots: vec![first_binding],
                    },
                    NodeDraft {
                        id: NODE_2,
                        intent: template.intent,
                        capability: template.capability,
                        operation: template.operation,
                        polarity: pattern.clauses[1].polarity,
                        predicate: pattern.clauses[1].predicate.clone(),
                        negation: pattern.clauses[1].negation.clone(),
                        slots: vec![second_binding],
                    },
                ],
                relations: Vec::new(),
                independent_pairs: vec![IndependentPairDraft {
                    left_node: NODE_1,
                    right_node: NODE_2,
                }],
                argument_shares: Vec::new(),
            })
        }
        Shape::OrderedTimer => {
            if pattern.clauses.len() != 2 || bindings.len() != 2 {
                return Err(invalid_configuration());
            }
            let shared_timer = bindings[0].clone();
            let relation_evidence = pattern
                .relation_evidence
                .clone()
                .ok_or_else(invalid_configuration)?;
            let share_evidence = pattern
                .share_evidence
                .clone()
                .ok_or_else(invalid_configuration)?;
            Ok(PlanDraft {
                execution_class: pattern.execution_class,
                nodes: vec![
                    NodeDraft {
                        id: NODE_1,
                        intent: template.intent,
                        capability: template.capability,
                        operation: template.operation,
                        polarity: pattern.clauses[0].polarity,
                        predicate: pattern.clauses[0].predicate.clone(),
                        negation: pattern.clauses[0].negation.clone(),
                        slots: bindings,
                    },
                    NodeDraft {
                        id: NODE_2,
                        intent: "ha:hass_timer_status",
                        capability: "ha:timer_control",
                        operation: "ha:timer_status",
                        polarity: pattern.clauses[1].polarity,
                        predicate: pattern.clauses[1].predicate.clone(),
                        negation: pattern.clauses[1].negation.clone(),
                        slots: vec![shared_timer],
                    },
                ],
                relations: vec![RelationDraft {
                    from_node: NODE_1,
                    to_node: NODE_2,
                    evidence: vec![relation_evidence],
                }],
                independent_pairs: Vec::new(),
                argument_shares: vec![ArgumentShareDraft {
                    from_node: NODE_1,
                    from_slot: "ha:timer",
                    to_node: NODE_2,
                    to_slot: "ha:timer",
                    evidence: vec![share_evidence],
                }],
            })
        }
    }
}

const fn map_pattern_failure(reason: PatternFailure) -> CompositionAbstentionReason {
    match reason {
        PatternFailure::IncompleteClause => CompositionAbstentionReason::IncompleteClause,
        PatternFailure::UnsupportedPattern => CompositionAbstentionReason::UnsupportedPattern,
        PatternFailure::NegationScope => CompositionAbstentionReason::NegationScope,
    }
}

const fn map_resolution_failure(reason: ResolutionFailure) -> CompositionAbstentionReason {
    match reason {
        ResolutionFailure::EntityResolution => CompositionAbstentionReason::EntityResolution,
        ResolutionFailure::MultipleEntityClarifications => {
            CompositionAbstentionReason::MultipleEntityClarifications
        }
        ResolutionFailure::StaleCatalogGeneration => {
            CompositionAbstentionReason::StaleCatalogGeneration
        }
    }
}

fn invalid_configuration() -> PlanEngineError {
    PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use nlu_core::IntentId;

    use super::*;

    #[test]
    fn closed_table_contains_exactly_the_twenty_p09_intents() {
        let expected = BTreeSet::from([
            "ha:hass_broadcast",
            "ha:hass_cancel_all_timers",
            "ha:hass_cancel_timer",
            "ha:hass_climate_get_temperature",
            "ha:hass_decrease_timer",
            "ha:hass_get_current_date",
            "ha:hass_get_current_time",
            "ha:hass_get_state",
            "ha:hass_increase_timer",
            "ha:hass_nevermind",
            "ha:hass_pause_timer",
            "ha:hass_respond",
            "ha:hass_set_position",
            "ha:hass_start_timer",
            "ha:hass_stop_moving",
            "ha:hass_timer_status",
            "ha:hass_toggle",
            "ha:hass_turn_off",
            "ha:hass_turn_on",
            "ha:hass_unpause_timer",
        ]);
        let actual = table::TEMPLATES
            .iter()
            .map(|template| template.intent)
            .collect::<BTreeSet<_>>();
        assert_eq!(actual, expected);
        for value in expected {
            let id = IntentId::new(value).expect("FIXTURE_TECNICA P09 intent ID");
            assert!(table::lookup(&id).is_some());
        }
    }

    #[test]
    fn debug_and_closed_errors_do_not_expose_private_text() {
        let canary = "FIXTURE_TECNICA_PRIVATE_CANARY";
        let engine = PlanEngine::new().expect("FIXTURE_TECNICA engine");
        assert!(!format!("{engine:?}").contains(canary));
        for code in [
            PlanEngineErrorCode::InvalidStaticConfiguration,
            PlanEngineErrorCode::MatchSourceMismatch,
            PlanEngineErrorCode::TextProcessing,
            PlanEngineErrorCode::CatalogContract,
            PlanEngineErrorCode::CoreContract,
        ] {
            let error = PlanEngineError::new(code);
            assert!(!format!("{error:?}").contains(canary));
            assert!(!error.to_string().contains(canary));
        }
    }
}
