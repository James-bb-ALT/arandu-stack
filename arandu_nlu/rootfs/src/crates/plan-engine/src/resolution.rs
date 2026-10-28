use ha_catalog::{
    CatalogError, CatalogSnapshot, EntityClarification, EntityConstraint, EntityQuery,
    EntityResolution, resolve_entity,
};
use intent_engine::{IntentSlotValue, SlotBinding};
use nlu_core::{CapabilityId, RequestText, SlotValue, Utf8Span};

use crate::{
    PlanEngineError, PlanEngineErrorCode, Result,
    model::ResolvedBinding,
    table::{BindingKind, TemplateSpec},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ResolutionFailure {
    EntityResolution,
    MultipleEntityClarifications,
    StaleCatalogGeneration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum BindingResolution {
    Resolved(Vec<ResolvedBinding>),
    Clarification(EntityClarification),
    Abstention(ResolutionFailure),
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct PendingBindingResolution {
    pub(crate) clarification: EntityClarification,
    pub(crate) unresolved_index: usize,
    pub(crate) unresolved_evidence: Utf8Span,
    pub(crate) resolved: Vec<(usize, ResolvedBinding)>,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum ResumableBindingResolution {
    Resolved(Vec<ResolvedBinding>),
    Pending(PendingBindingResolution),
    Abstention(ResolutionFailure),
}

pub(crate) fn resolve_bindings(
    source: &RequestText,
    snapshot: &CatalogSnapshot,
    template: &TemplateSpec,
    bindings: &[&SlotBinding],
    capability_evidence: &[Utf8Span],
) -> Result<BindingResolution> {
    Ok(
        match resolve_bindings_resumable(source, snapshot, template, bindings, capability_evidence)?
        {
            ResumableBindingResolution::Resolved(bindings) => BindingResolution::Resolved(bindings),
            ResumableBindingResolution::Pending(pending) => {
                BindingResolution::Clarification(pending.clarification)
            }
            ResumableBindingResolution::Abstention(reason) => BindingResolution::Abstention(reason),
        },
    )
}

pub(crate) fn resolve_bindings_resumable(
    source: &RequestText,
    snapshot: &CatalogSnapshot,
    template: &TemplateSpec,
    bindings: &[&SlotBinding],
    capability_evidence: &[Utf8Span],
) -> Result<ResumableBindingResolution> {
    if bindings.len() != template.bindings.len()
        || capability_evidence.len() != template.bindings.len()
    {
        return Err(PlanEngineError::new(
            PlanEngineErrorCode::InvalidStaticConfiguration,
        ));
    }

    let capability = CapabilityId::new(template.capability)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))?;
    let mut resolved = Vec::with_capacity(bindings.len());
    let mut clarification = None;

    for (((binding, expected), constraint_evidence), index) in bindings
        .iter()
        .zip(template.bindings)
        .zip(capability_evidence)
        .zip(0_usize..)
    {
        let value = match (expected.kind, binding.value()) {
            (BindingKind::Mention, IntentSlotValue::Mention(mention)) => {
                let query = EntityQuery::new(
                    source,
                    snapshot.generation(),
                    mention.clone(),
                    vec![EntityConstraint::capability(
                        capability.clone(),
                        constraint_evidence.clone(),
                    )],
                )
                .map_err(map_query_error)?;
                match resolve_entity(snapshot, &query) {
                    Ok(EntityResolution::Resolved(entity_match)) => {
                        if entity_match.entity().generation() != snapshot.generation() {
                            return Ok(ResumableBindingResolution::Abstention(
                                ResolutionFailure::StaleCatalogGeneration,
                            ));
                        }
                        Some(SlotValue::Entity(entity_match.entity().clone()))
                    }
                    Ok(EntityResolution::Clarification(options)) => {
                        if clarification
                            .replace((index, binding.evidence().clone(), options))
                            .is_some()
                        {
                            return Ok(ResumableBindingResolution::Abstention(
                                ResolutionFailure::MultipleEntityClarifications,
                            ));
                        }
                        None
                    }
                    Ok(EntityResolution::Abstained(_)) => {
                        return Ok(ResumableBindingResolution::Abstention(
                            ResolutionFailure::EntityResolution,
                        ));
                    }
                    Err(CatalogError::StaleCatalogGeneration)
                    | Err(CatalogError::StaleEntityGeneration) => {
                        return Ok(ResumableBindingResolution::Abstention(
                            ResolutionFailure::StaleCatalogGeneration,
                        ));
                    }
                    Err(error) => return Err(map_catalog_error(error)),
                }
            }
            (BindingKind::Text, IntentSlotValue::Text(span)) => {
                Some(SlotValue::EvidenceText(span.clone()))
            }
            (BindingKind::Integer { .. }, IntentSlotValue::Integer(value)) => {
                Some(SlotValue::Integer(*value))
            }
            _ => {
                return Err(PlanEngineError::new(
                    PlanEngineErrorCode::InvalidStaticConfiguration,
                ));
            }
        };

        if let Some(value) = value {
            resolved.push((
                index,
                ResolvedBinding {
                    slot_id: expected.slot_id,
                    value,
                    evidence: binding.evidence().clone(),
                },
            ));
        }
    }

    resolved.sort_by_key(|(index, _)| *index);
    if let Some((unresolved_index, unresolved_evidence, clarification)) = clarification {
        return Ok(ResumableBindingResolution::Pending(
            PendingBindingResolution {
                clarification,
                unresolved_index,
                unresolved_evidence,
                resolved,
            },
        ));
    }
    if resolved.len() != bindings.len() {
        return Err(PlanEngineError::new(PlanEngineErrorCode::CatalogContract));
    }
    Ok(ResumableBindingResolution::Resolved(
        resolved.into_iter().map(|(_, binding)| binding).collect(),
    ))
}

fn map_query_error(error: CatalogError) -> PlanEngineError {
    match error {
        CatalogError::SpanSourceMismatch => {
            PlanEngineError::new(PlanEngineErrorCode::MatchSourceMismatch)
        }
        CatalogError::StaleCatalogGeneration | CatalogError::StaleEntityGeneration => {
            PlanEngineError::new(PlanEngineErrorCode::CatalogContract)
        }
        _ => PlanEngineError::new(PlanEngineErrorCode::CatalogContract),
    }
}

fn map_catalog_error(error: CatalogError) -> PlanEngineError {
    match error {
        CatalogError::SpanSourceMismatch => {
            PlanEngineError::new(PlanEngineErrorCode::MatchSourceMismatch)
        }
        _ => PlanEngineError::new(PlanEngineErrorCode::CatalogContract),
    }
}
