use intent_engine::{IntentMatch, IntentSlotValue, SlotBinding};
use lang_ptbr::NormalizedText;
use nlu_core::{RequestText, Utf8Span};

use crate::{
    PlanEngineError, PlanEngineErrorCode, Result,
    model::{DraftExecutionClass, DraftPolarity},
    table::{BindingKind, Shape, TemplateSpec},
};

const PARALLEL_CLEAR_FIRST: &[&str] = &["não ligue ", " e ligue ", ""];
const PARALLEL_CLEAR_SECOND: &[&str] = &["ligue ", " e não ligue ", ""];
const PARALLEL_AMBIGUOUS_NEGATION: &[&str] = &["não ligue ", " e ", ""];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PatternFailure {
    IncompleteClause,
    UnsupportedPattern,
    NegationScope,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ClausePatternEvidence {
    pub(crate) predicate: Utf8Span,
    pub(crate) polarity: DraftPolarity,
    pub(crate) negation: Option<Utf8Span>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SupportedPattern {
    pub(crate) execution_class: DraftExecutionClass,
    pub(crate) clauses: Vec<ClausePatternEvidence>,
    pub(crate) relation_evidence: Option<Utf8Span>,
    pub(crate) share_evidence: Option<Utf8Span>,
}

pub(crate) fn validate<'a>(
    source: &RequestText,
    intent_match: &'a IntentMatch,
    template: &TemplateSpec,
) -> Result<core::result::Result<(SupportedPattern, Vec<&'a SlotBinding>), PatternFailure>> {
    validate_match_evidence_source(source, intent_match)?;
    let bindings = match validate_bindings(source, intent_match, template)? {
        Ok(bindings) => bindings,
        Err(failure) => return Ok(Err(failure)),
    };

    let pattern = match template.shape {
        Shape::Single => validate_single(source, template, &bindings)?,
        Shape::ParallelTurnOn => validate_parallel(source, template, &bindings)?,
        Shape::OrderedTimer => validate_ordered(source, template, &bindings)?,
    };
    Ok(pattern.map(|supported| (supported, bindings)))
}

fn validate_match_evidence_source(source: &RequestText, intent_match: &IntentMatch) -> Result<()> {
    if intent_match
        .evidence()
        .iter()
        .any(|span| !span.belongs_to(source))
        || intent_match.slots().iter().any(|binding| {
            !binding.evidence().belongs_to(source)
                || binding
                    .value()
                    .text_span()
                    .is_some_and(|span| !span.belongs_to(source))
        })
    {
        return Err(PlanEngineError::new(
            PlanEngineErrorCode::MatchSourceMismatch,
        ));
    }
    Ok(())
}

fn validate_bindings<'a>(
    source: &RequestText,
    intent_match: &'a IntentMatch,
    template: &TemplateSpec,
) -> Result<core::result::Result<Vec<&'a SlotBinding>, PatternFailure>> {
    if intent_match.slots().len() != template.bindings.len() {
        return Ok(Err(PatternFailure::IncompleteClause));
    }

    let mut ordered = Vec::with_capacity(template.bindings.len());
    for expected in template.bindings {
        let mut candidates = intent_match.slots().iter().filter(|binding| {
            binding.id().as_str() == expected.slot_id
                && binding.role() == expected.role
                && binding.occurrence() == expected.occurrence
        });
        let Some(binding) = candidates.next() else {
            return Ok(Err(PatternFailure::IncompleteClause));
        };
        if candidates.next().is_some() || !value_matches(source, binding, expected.kind)? {
            return Ok(Err(PatternFailure::IncompleteClause));
        }
        ordered.push(binding);
    }
    Ok(Ok(ordered))
}

fn value_matches(
    source: &RequestText,
    binding: &SlotBinding,
    expected: BindingKind,
) -> Result<bool> {
    let matches = match (expected, binding.value()) {
        (BindingKind::Mention, IntentSlotValue::Mention(span))
        | (BindingKind::Text, IntentSlotValue::Text(span)) => span == binding.evidence(),
        (BindingKind::Integer { scale }, IntentSlotValue::Integer(value)) => {
            let text = binding
                .evidence()
                .slice(source)
                .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::MatchSourceMismatch))?;
            let parsed = text
                .bytes()
                .all(|byte| byte.is_ascii_digit())
                .then(|| text.parse::<i64>().ok())
                .flatten();
            parsed.and_then(|number| number.checked_mul(scale)) == Some(*value)
        }
        (BindingKind::Mention | BindingKind::Text, IntentSlotValue::Integer(_))
        | (BindingKind::Mention, IntentSlotValue::Text(_))
        | (BindingKind::Text, IntentSlotValue::Mention(_))
        | (BindingKind::Integer { .. }, IntentSlotValue::Mention(_))
        | (BindingKind::Integer { .. }, IntentSlotValue::Text(_)) => false,
    };
    Ok(matches)
}

fn validate_single(
    source: &RequestText,
    template: &TemplateSpec,
    bindings: &[&SlotBinding],
) -> Result<core::result::Result<SupportedPattern, PatternFailure>> {
    if !sequence_matches(source, bindings, template.literals) {
        return unsupported_or_negated(source);
    }
    let predicate_len = template.literals[0].trim_end_matches(' ').len();
    let predicate = checked_span(source, 0, predicate_len)?;
    Ok(Ok(SupportedPattern {
        execution_class: DraftExecutionClass::PartialSafe,
        clauses: vec![ClausePatternEvidence {
            predicate,
            polarity: DraftPolarity::Affirmed,
            negation: None,
        }],
        relation_evidence: None,
        share_evidence: None,
    }))
}

fn validate_parallel(
    source: &RequestText,
    template: &TemplateSpec,
    bindings: &[&SlotBinding],
) -> Result<core::result::Result<SupportedPattern, PatternFailure>> {
    if sequence_matches(source, bindings, template.literals) {
        let predicate = checked_span(source, 0, "ligue".len())?;
        return Ok(Ok(SupportedPattern {
            execution_class: DraftExecutionClass::AtomicOnly,
            clauses: vec![
                ClausePatternEvidence {
                    predicate: predicate.clone(),
                    polarity: DraftPolarity::Affirmed,
                    negation: None,
                },
                ClausePatternEvidence {
                    predicate,
                    polarity: DraftPolarity::Affirmed,
                    negation: None,
                },
            ],
            relation_evidence: None,
            share_evidence: None,
        }));
    }

    if sequence_matches(source, bindings, PARALLEL_CLEAR_FIRST) {
        let second_start = checked_add(binding_end(bindings[0]), " e ".len())?;
        return Ok(Ok(SupportedPattern {
            execution_class: DraftExecutionClass::NonExecutable,
            clauses: vec![
                ClausePatternEvidence {
                    predicate: checked_span(source, "não ".len(), "ligue".len())?,
                    polarity: DraftPolarity::Negated,
                    negation: Some(checked_span(source, 0, "não".len())?),
                },
                ClausePatternEvidence {
                    predicate: checked_span(source, second_start, "ligue".len())?,
                    polarity: DraftPolarity::Affirmed,
                    negation: None,
                },
            ],
            relation_evidence: None,
            share_evidence: None,
        }));
    }

    if sequence_matches(source, bindings, PARALLEL_CLEAR_SECOND) {
        let negation_start = checked_add(binding_end(bindings[0]), " e ".len())?;
        let second_predicate_start = checked_add(negation_start, "não ".len())?;
        return Ok(Ok(SupportedPattern {
            execution_class: DraftExecutionClass::NonExecutable,
            clauses: vec![
                ClausePatternEvidence {
                    predicate: checked_span(source, 0, "ligue".len())?,
                    polarity: DraftPolarity::Affirmed,
                    negation: None,
                },
                ClausePatternEvidence {
                    predicate: checked_span(source, second_predicate_start, "ligue".len())?,
                    polarity: DraftPolarity::Negated,
                    negation: Some(checked_span(source, negation_start, "não".len())?),
                },
            ],
            relation_evidence: None,
            share_evidence: None,
        }));
    }

    if sequence_matches(source, bindings, PARALLEL_AMBIGUOUS_NEGATION) {
        return Ok(Err(PatternFailure::NegationScope));
    }
    unsupported_or_negated(source)
}

fn validate_ordered(
    source: &RequestText,
    template: &TemplateSpec,
    bindings: &[&SlotBinding],
) -> Result<core::result::Result<SupportedPattern, PatternFailure>> {
    if !sequence_matches(source, bindings, template.literals) {
        return unsupported_or_negated(source);
    }

    let duration_end = binding_end(bindings[1]);
    let second_predicate_start = checked_add(duration_end, " minutos e ".len())?;
    let relation_start = checked_add(duration_end, " minutos ".len())?;
    Ok(Ok(SupportedPattern {
        execution_class: DraftExecutionClass::PartialSafe,
        clauses: vec![
            ClausePatternEvidence {
                predicate: checked_span(source, 0, "inicie".len())?,
                polarity: DraftPolarity::Affirmed,
                negation: None,
            },
            ClausePatternEvidence {
                predicate: checked_span(source, second_predicate_start, "consulte o estado".len())?,
                polarity: DraftPolarity::Affirmed,
                negation: None,
            },
        ],
        relation_evidence: Some(checked_span(
            source,
            relation_start,
            source.len().saturating_sub(relation_start),
        )?),
        share_evidence: Some(bindings[0].evidence().clone()),
    }))
}

fn unsupported_or_negated(
    source: &RequestText,
) -> Result<core::result::Result<SupportedPattern, PatternFailure>> {
    let normalized = NormalizedText::from_request(source.clone())
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::TextProcessing))?;
    let tokenization = normalized
        .tokenize()
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::TextProcessing))?;
    let has_negation = tokenization.tokens().iter().any(|token| {
        token
            .normalized_slice(&normalized)
            .is_ok_and(|value| value == "não")
    });
    Ok(Err(if has_negation {
        PatternFailure::NegationScope
    } else {
        PatternFailure::UnsupportedPattern
    }))
}

fn sequence_matches(source: &RequestText, bindings: &[&SlotBinding], literals: &[&str]) -> bool {
    if literals.len() != bindings.len() + 1 {
        return false;
    }
    let mut cursor = 0_usize;
    for (literal, binding) in literals.iter().zip(bindings) {
        let start = binding.evidence().start() as usize;
        let end = binding.evidence().end() as usize;
        if start < cursor
            || end < start
            || end > source.len()
            || source.as_str().get(cursor..start) != Some(*literal)
        {
            return false;
        }
        cursor = end;
    }
    source.as_str().get(cursor..) == literals.last().copied()
}

fn binding_end(binding: &SlotBinding) -> usize {
    binding.evidence().end() as usize
}

fn checked_add(left: usize, right: usize) -> Result<usize> {
    left.checked_add(right)
        .ok_or_else(|| PlanEngineError::new(PlanEngineErrorCode::CoreContract))
}

fn checked_span(source: &RequestText, start: usize, len: usize) -> Result<Utf8Span> {
    let end = checked_add(start, len)?;
    source
        .span(start as u64, end as u64)
        .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::CoreContract))
}
