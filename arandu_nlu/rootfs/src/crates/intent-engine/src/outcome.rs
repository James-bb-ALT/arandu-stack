use nlu_core::{IntentId, RequestText, SlotId, Utf8Span};
use nlu_data::canonical_json;
use serde::Serialize;

use crate::{Result, invalid_output, span_source_mismatch};

pub const INTENT_OUTPUT_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum IntentSlotValue {
    Mention(Utf8Span),
    Text(Utf8Span),
    Integer(i64),
}

impl IntentSlotValue {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::Mention(_) => "mention",
            Self::Text(_) => "text",
            Self::Integer(_) => "integer",
        }
    }

    #[must_use]
    pub const fn integer(&self) -> Option<i64> {
        match self {
            Self::Integer(value) => Some(*value),
            Self::Mention(_) | Self::Text(_) => None,
        }
    }

    #[must_use]
    pub const fn text_span(&self) -> Option<&Utf8Span> {
        match self {
            Self::Mention(span) | Self::Text(span) => Some(span),
            Self::Integer(_) => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SlotBinding {
    id: SlotId,
    role: Box<str>,
    occurrence: u16,
    value: IntentSlotValue,
    evidence: Utf8Span,
}

impl SlotBinding {
    pub(crate) fn new(
        source: &RequestText,
        id: SlotId,
        role: Box<str>,
        occurrence: u16,
        value: IntentSlotValue,
        evidence: Utf8Span,
    ) -> Result<Self> {
        if !evidence.belongs_to(source)
            || value
                .text_span()
                .is_some_and(|span| !span.belongs_to(source) || span != &evidence)
        {
            return Err(span_source_mismatch());
        }
        Ok(Self {
            id,
            role,
            occurrence,
            value,
            evidence,
        })
    }

    #[must_use]
    pub const fn id(&self) -> &SlotId {
        &self.id
    }

    #[must_use]
    pub fn role(&self) -> &str {
        &self.role
    }

    #[must_use]
    pub const fn occurrence(&self) -> u16 {
        self.occurrence
    }

    #[must_use]
    pub const fn value(&self) -> &IntentSlotValue {
        &self.value
    }

    #[must_use]
    pub const fn evidence(&self) -> &Utf8Span {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntentMatch {
    intent: IntentId,
    score: u16,
    evidence: Box<[Utf8Span]>,
    slots: Box<[SlotBinding]>,
}

impl IntentMatch {
    pub(crate) fn new(
        source: &RequestText,
        intent: IntentId,
        score: u16,
        mut evidence: Vec<Utf8Span>,
        mut slots: Vec<SlotBinding>,
    ) -> Result<Self> {
        if evidence.is_empty() || evidence.iter().any(|span| !span.belongs_to(source)) {
            return Err(span_source_mismatch());
        }
        evidence.sort();
        evidence.dedup();
        slots.sort_by(|left, right| {
            (left.id(), left.role(), left.occurrence()).cmp(&(
                right.id(),
                right.role(),
                right.occurrence(),
            ))
        });
        if slots.windows(2).any(|pair| {
            (pair[0].id(), pair[0].role(), pair[0].occurrence())
                == (pair[1].id(), pair[1].role(), pair[1].occurrence())
        }) {
            return Err(invalid_output("duplicate slot binding"));
        }
        Ok(Self {
            intent,
            score,
            evidence: evidence.into_boxed_slice(),
            slots: slots.into_boxed_slice(),
        })
    }

    #[must_use]
    pub const fn intent(&self) -> &IntentId {
        &self.intent
    }

    #[must_use]
    pub const fn score(&self) -> u16 {
        self.score
    }

    #[must_use]
    pub fn evidence(&self) -> &[Utf8Span] {
        &self.evidence
    }

    #[must_use]
    pub fn slots(&self) -> &[SlotBinding] {
        &self.slots
    }

    pub(crate) fn semantic_key(&self, source: &RequestText) -> Result<Vec<u8>> {
        let value = serde_json::to_value(MatchView::new(self, source, false)?)
            .map_err(|_| invalid_output("semantic key value"))?;
        canonical_json(&value, "intent semantic key")
            .map_err(|_| invalid_output("semantic key encoding"))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntentClarification {
    alternatives: Box<[IntentMatch]>,
}

impl IntentClarification {
    pub(crate) fn new(alternatives: Vec<IntentMatch>) -> Result<Self> {
        if alternatives.len() < 2 {
            return Err(invalid_output("clarification alternatives"));
        }
        Ok(Self {
            alternatives: alternatives.into_boxed_slice(),
        })
    }

    #[must_use]
    pub fn alternatives(&self) -> &[IntentMatch] {
        &self.alternatives
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum RecognitionAbstentionReason {
    InsufficientEvidence,
}

impl RecognitionAbstentionReason {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InsufficientEvidence => "insufficient_evidence",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RecognitionOutcome {
    Match(IntentMatch),
    Clarification(IntentClarification),
    Abstention(RecognitionAbstentionReason),
}

impl RecognitionOutcome {
    pub fn canonical_bytes(&self, source: &RequestText) -> Result<Vec<u8>> {
        let view = match self {
            Self::Match(intent_match) => OutputView {
                schema_version: INTENT_OUTPUT_SCHEMA_VERSION,
                outcome: "match",
                intent_match: Some(MatchView::new(intent_match, source, true)?),
                alternatives: None,
                reason: None,
            },
            Self::Clarification(clarification) => {
                let alternatives = clarification
                    .alternatives()
                    .iter()
                    .map(|candidate| MatchView::new(candidate, source, true))
                    .collect::<Result<Vec<_>>>()?;
                OutputView {
                    schema_version: INTENT_OUTPUT_SCHEMA_VERSION,
                    outcome: "clarification",
                    intent_match: None,
                    alternatives: Some(alternatives),
                    reason: None,
                }
            }
            Self::Abstention(reason) => OutputView {
                schema_version: INTENT_OUTPUT_SCHEMA_VERSION,
                outcome: "abstention",
                intent_match: None,
                alternatives: None,
                reason: Some(reason.code()),
            },
        };
        let value =
            serde_json::to_value(view).map_err(|_| invalid_output("intent output value"))?;
        let mut bytes = canonical_json(&value, "intent output")
            .map_err(|_| invalid_output("intent output encoding"))?;
        bytes.push(b'\n');
        Ok(bytes)
    }
}

#[derive(Serialize)]
struct OutputView<'a> {
    schema_version: u32,
    outcome: &'static str,
    #[serde(rename = "match", skip_serializing_if = "Option::is_none")]
    intent_match: Option<MatchView<'a>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    alternatives: Option<Vec<MatchView<'a>>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<&'static str>,
}

#[derive(Serialize)]
struct MatchView<'a> {
    intent_id: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    score: Option<u16>,
    evidence: Vec<SpanView>,
    slots: Vec<SlotView<'a>>,
}

impl<'a> MatchView<'a> {
    fn new(value: &'a IntentMatch, source: &'a RequestText, include_score: bool) -> Result<Self> {
        let evidence = value
            .evidence()
            .iter()
            .map(|span| SpanView::new(span, source))
            .collect::<Result<Vec<_>>>()?;
        let slots = value
            .slots()
            .iter()
            .map(|slot| SlotView::new(slot, source))
            .collect::<Result<Vec<_>>>()?;
        Ok(Self {
            intent_id: value.intent().as_str(),
            score: include_score.then_some(value.score()),
            evidence,
            slots,
        })
    }
}

#[derive(Clone, Copy, Serialize)]
struct SpanView {
    begin_byte: u32,
    end_byte: u32,
}

impl SpanView {
    fn new(span: &Utf8Span, source: &RequestText) -> Result<Self> {
        if !span.belongs_to(source) {
            return Err(span_source_mismatch());
        }
        Ok(Self {
            begin_byte: span.start(),
            end_byte: span.end(),
        })
    }
}

#[derive(Serialize)]
struct SlotView<'a> {
    slot_id: &'a str,
    role: &'a str,
    occurrence: u16,
    value: ValueView<'a>,
    evidence: SpanView,
}

impl<'a> SlotView<'a> {
    fn new(binding: &'a SlotBinding, source: &'a RequestText) -> Result<Self> {
        let value = match binding.value() {
            IntentSlotValue::Mention(span) => ValueView {
                kind: "mention",
                text: Some(span.slice(source).map_err(|_| span_source_mismatch())?),
                integer: None,
            },
            IntentSlotValue::Text(span) => ValueView {
                kind: "text",
                text: Some(span.slice(source).map_err(|_| span_source_mismatch())?),
                integer: None,
            },
            IntentSlotValue::Integer(value) => ValueView {
                kind: "integer",
                text: None,
                integer: Some(*value),
            },
        };
        Ok(Self {
            slot_id: binding.id().as_str(),
            role: binding.role(),
            occurrence: binding.occurrence(),
            value,
            evidence: SpanView::new(binding.evidence(), source)?,
        })
    }
}

#[derive(Serialize)]
struct ValueView<'a> {
    kind: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    integer: Option<i64>,
}
