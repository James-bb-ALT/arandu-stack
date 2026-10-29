use crate::{
    MAX_DECODED_STRING_BYTES, MAX_PROTOCOL_ERROR_BYTES, MAX_WIRE_BYTES, ProtocolError,
    ProtocolErrorCode, preflight,
};
use nlu_core::{
    AbstentionReason, CapabilityId, CatalogGeneration, Clarification, ClarificationOption,
    Confidence, EntityId, EntityRef, Hypothesis, IntentId, NodeId, OperationId, OptionId, Plan,
    PlanNode, Relation, RelationKind, RequestText, SemanticOutcome, Slot, SlotId, SlotValue,
    Utf8Span,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};

pub const VERSION: u16 = 1;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Outcome {
    Plan(Plan),
    Clarification(Clarification),
    Abstention(AbstentionReason),
    ProtocolError(ProtocolError),
}

pub fn decode_request(bytes: &[u8]) -> Result<RequestText, ProtocolError> {
    let text = preflight::inspect(bytes)?;
    let envelope: RequestEnvelopeDto = decode_json(text.as_bytes())?;
    require_version(envelope.version)?;
    RequestText::new(envelope.request.text).map_err(ProtocolError::from)
}

pub fn encode_request(request: &RequestText) -> Result<Vec<u8>, ProtocolError> {
    if request.len() > MAX_DECODED_STRING_BYTES {
        return Err(ProtocolError::StringTooLarge);
    }
    let envelope = RequestEnvelopeRef {
        version: VERSION,
        request: RequestRef {
            text: request.as_str(),
        },
    };
    encode_json(&envelope, MAX_WIRE_BYTES)
}

pub fn decode_outcome(bytes: &[u8], source: &RequestText) -> Result<Outcome, ProtocolError> {
    let text = preflight::inspect(bytes)?;
    let envelope: ResponseEnvelopeDto = decode_json(text.as_bytes())?;
    require_version(envelope.version)?;
    envelope.outcome.into_core(source)
}

pub fn encode_outcome(outcome: &Outcome) -> Result<Vec<u8>, ProtocolError> {
    let dto = ResponseEnvelopeDto::from_core(outcome);
    let limit = if matches!(outcome, Outcome::ProtocolError(_)) {
        MAX_PROTOCOL_ERROR_BYTES
    } else {
        MAX_WIRE_BYTES
    };
    encode_json(&dto, limit)
}

pub fn encode_semantic_outcome(outcome: &SemanticOutcome) -> Result<Vec<u8>, ProtocolError> {
    let outcome = match outcome {
        SemanticOutcome::Plan(plan) => Outcome::Plan(plan.clone()),
        SemanticOutcome::Clarification(clarification) => {
            Outcome::Clarification(clarification.clone())
        }
        SemanticOutcome::Abstention(reason) => Outcome::Abstention(*reason),
    };
    encode_outcome(&outcome)
}

pub fn encode_protocol_error(error: ProtocolError) -> Result<Vec<u8>, ProtocolError> {
    encode_outcome(&Outcome::ProtocolError(error))
}

fn require_version(version: u16) -> Result<(), ProtocolError> {
    if version == VERSION {
        Ok(())
    } else {
        Err(ProtocolError::UnsupportedVersion)
    }
}

fn decode_json<T: DeserializeOwned>(bytes: &[u8]) -> Result<T, ProtocolError> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let value = T::deserialize(&mut deserializer).map_err(|_| ProtocolError::MalformedJson)?;
    deserializer
        .end()
        .map_err(|_| ProtocolError::MalformedJson)?;
    Ok(value)
}

fn encode_json<T: Serialize>(value: &T, limit: usize) -> Result<Vec<u8>, ProtocolError> {
    let bytes = serde_json::to_vec(value).map_err(|_| ProtocolError::EncodingFailure)?;
    if bytes.len() > limit {
        return Err(if limit == MAX_PROTOCOL_ERROR_BYTES {
            ProtocolError::EncodingFailure
        } else {
            ProtocolError::InputTooLarge
        });
    }
    Ok(bytes)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RequestEnvelopeDto {
    version: u16,
    request: RequestDto,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RequestDto {
    text: String,
}

#[derive(Serialize)]
struct RequestEnvelopeRef<'a> {
    version: u16,
    request: RequestRef<'a>,
}

#[derive(Serialize)]
struct RequestRef<'a> {
    text: &'a str,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ResponseEnvelopeDto {
    version: u16,
    outcome: OutcomeDto,
}

impl ResponseEnvelopeDto {
    fn from_core(outcome: &Outcome) -> Self {
        Self {
            version: VERSION,
            outcome: OutcomeDto::from_core(outcome),
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum OutcomeDto {
    Plan {
        catalog_generation: u64,
        nodes: Vec<PlanNodeDto>,
        relations: Vec<RelationDto>,
    },
    Clarification {
        options: Vec<ClarificationOptionDto>,
    },
    Abstention {
        reason: AbstentionReasonDto,
    },
    ProtocolError {
        code: ProtocolErrorCode,
        limit: Option<u32>,
    },
}

impl OutcomeDto {
    fn from_core(outcome: &Outcome) -> Self {
        match outcome {
            Outcome::Plan(plan) => Self::Plan {
                catalog_generation: plan.catalog_generation().get(),
                nodes: plan.nodes().iter().map(PlanNodeDto::from_core).collect(),
                relations: plan
                    .relations()
                    .iter()
                    .map(RelationDto::from_core)
                    .collect(),
            },
            Outcome::Clarification(clarification) => Self::Clarification {
                options: clarification
                    .options()
                    .iter()
                    .map(ClarificationOptionDto::from_core)
                    .collect(),
            },
            Outcome::Abstention(reason) => Self::Abstention {
                reason: AbstentionReasonDto::from_core(*reason),
            },
            Outcome::ProtocolError(error) => Self::ProtocolError {
                code: error.code(),
                limit: error.limit(),
            },
        }
    }

    fn into_core(self, source: &RequestText) -> Result<Outcome, ProtocolError> {
        match self {
            Self::Plan {
                catalog_generation,
                nodes,
                relations,
            } => {
                let generation =
                    CatalogGeneration::new(catalog_generation).map_err(ProtocolError::from)?;
                let nodes = nodes
                    .into_iter()
                    .map(|node| node.into_core(source))
                    .collect::<Result<Vec<_>, _>>()?;
                let relations = relations
                    .into_iter()
                    .map(RelationDto::into_core)
                    .collect::<Result<Vec<_>, _>>()?;
                Plan::new(source, generation, nodes, relations)
                    .map(Outcome::Plan)
                    .map_err(ProtocolError::from)
            }
            Self::Clarification { options } => {
                let options = options
                    .into_iter()
                    .map(|option| option.into_core(source))
                    .collect::<Result<Vec<_>, _>>()?;
                Clarification::new(source, options)
                    .map(Outcome::Clarification)
                    .map_err(ProtocolError::from)
            }
            Self::Abstention { reason } => Ok(Outcome::Abstention(reason.into_core())),
            Self::ProtocolError { code, limit } => {
                ProtocolError::from_wire(code, limit).map(Outcome::ProtocolError)
            }
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SpanDto {
    start: u64,
    end: u64,
}

impl SpanDto {
    fn from_core(span: &Utf8Span) -> Self {
        Self {
            start: u64::from(span.start()),
            end: u64::from(span.end()),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<Utf8Span, ProtocolError> {
        source
            .span(self.start, self.end)
            .map_err(ProtocolError::from)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PlanNodeDto {
    id: String,
    capability: String,
    operation: String,
    slots: Vec<SlotDto>,
    evidence: Vec<SpanDto>,
}

impl PlanNodeDto {
    fn from_core(node: &PlanNode) -> Self {
        Self {
            id: node.id().as_str().into(),
            capability: node.capability().as_str().into(),
            operation: node.operation().as_str().into(),
            slots: node.slots().iter().map(SlotDto::from_core).collect(),
            evidence: node.evidence().iter().map(SpanDto::from_core).collect(),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<PlanNode, ProtocolError> {
        let id = NodeId::new(&self.id).map_err(ProtocolError::from)?;
        let capability = CapabilityId::new(&self.capability).map_err(ProtocolError::from)?;
        let operation = OperationId::new(&self.operation).map_err(ProtocolError::from)?;
        let slots = self
            .slots
            .into_iter()
            .map(|slot| slot.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        let evidence = self
            .evidence
            .into_iter()
            .map(|span| span.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        PlanNode::new(id, capability, operation, slots, evidence).map_err(ProtocolError::from)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SlotDto {
    id: String,
    value: SlotValueDto,
}

impl SlotDto {
    fn from_core(slot: &Slot) -> Self {
        Self {
            id: slot.id().as_str().into(),
            value: SlotValueDto::from_core(slot.value()),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<Slot, ProtocolError> {
        let id = SlotId::new(&self.id).map_err(ProtocolError::from)?;
        let value = self.value.into_core(source)?;
        Ok(Slot::new(id, value))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum SlotValueDto {
    EvidenceText { span: SpanDto },
    Integer { value: i64 },
    Boolean { value: bool },
    Entity { id: String, generation: u64 },
}

impl SlotValueDto {
    fn from_core(value: &SlotValue) -> Self {
        match value {
            SlotValue::EvidenceText(span) => Self::EvidenceText {
                span: SpanDto::from_core(span),
            },
            SlotValue::Integer(value) => Self::Integer { value: *value },
            SlotValue::Boolean(value) => Self::Boolean { value: *value },
            SlotValue::Entity(entity) => Self::Entity {
                id: entity.id().as_str().into(),
                generation: entity.generation().get(),
            },
        }
    }

    fn into_core(self, source: &RequestText) -> Result<SlotValue, ProtocolError> {
        match self {
            Self::EvidenceText { span } => span.into_core(source).map(SlotValue::EvidenceText),
            Self::Integer { value } => Ok(SlotValue::Integer(value)),
            Self::Boolean { value } => Ok(SlotValue::Boolean(value)),
            Self::Entity { id, generation } => {
                let id = EntityId::new(&id).map_err(ProtocolError::from)?;
                let generation = CatalogGeneration::new(generation).map_err(ProtocolError::from)?;
                Ok(SlotValue::Entity(EntityRef::new(id, generation)))
            }
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RelationDto {
    from: String,
    to: String,
    kind: RelationKindDto,
}

impl RelationDto {
    fn from_core(relation: &Relation) -> Self {
        Self {
            from: relation.from().as_str().into(),
            to: relation.to().as_str().into(),
            kind: RelationKindDto::from_core(relation.kind()),
        }
    }

    fn into_core(self) -> Result<Relation, ProtocolError> {
        let from = NodeId::new(&self.from).map_err(ProtocolError::from)?;
        let to = NodeId::new(&self.to).map_err(ProtocolError::from)?;
        Ok(Relation::new(from, to, self.kind.into_core()))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum RelationKindDto {
    Precedes,
    Requires,
}

impl RelationKindDto {
    const fn from_core(kind: RelationKind) -> Self {
        match kind {
            RelationKind::Precedes => Self::Precedes,
            RelationKind::Requires => Self::Requires,
        }
    }

    const fn into_core(self) -> RelationKind {
        match self {
            Self::Precedes => RelationKind::Precedes,
            Self::Requires => RelationKind::Requires,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ClarificationOptionDto {
    id: String,
    intent: String,
    confidence_bps: u16,
    evidence: Vec<SpanDto>,
}

impl ClarificationOptionDto {
    fn from_core(option: &ClarificationOption) -> Self {
        Self {
            id: option.id().as_str().into(),
            intent: option.hypothesis().intent().as_str().into(),
            confidence_bps: option.hypothesis().confidence().basis_points(),
            evidence: option
                .hypothesis()
                .evidence()
                .iter()
                .map(SpanDto::from_core)
                .collect(),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<ClarificationOption, ProtocolError> {
        let id = OptionId::new(&self.id).map_err(ProtocolError::from)?;
        let intent = IntentId::new(&self.intent).map_err(ProtocolError::from)?;
        let confidence =
            Confidence::from_basis_points(self.confidence_bps).map_err(ProtocolError::from)?;
        let evidence = self
            .evidence
            .into_iter()
            .map(|span| span.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        let hypothesis =
            Hypothesis::new(source, intent, confidence, evidence).map_err(ProtocolError::from)?;
        Ok(ClarificationOption::new(id, hypothesis))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum AbstentionReasonDto {
    InsufficientEvidence,
    Ambiguous,
    Unsupported,
}

impl AbstentionReasonDto {
    const fn from_core(reason: AbstentionReason) -> Self {
        match reason {
            AbstentionReason::InsufficientEvidence => Self::InsufficientEvidence,
            AbstentionReason::Ambiguous => Self::Ambiguous,
            AbstentionReason::Unsupported => Self::Unsupported,
        }
    }

    const fn into_core(self) -> AbstentionReason {
        match self {
            Self::InsufficientEvidence => AbstentionReason::InsufficientEvidence,
            Self::Ambiguous => AbstentionReason::Ambiguous,
            Self::Unsupported => AbstentionReason::Unsupported,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_encoding_is_compact_and_field_ordered() {
        let request = RequestText::new("FIXTURE_TECNICA_A".into()).expect("request");
        assert_eq!(
            encode_request(&request),
            Ok(br#"{"version":1,"request":{"text":"FIXTURE_TECNICA_A"}}"#.to_vec())
        );
    }

    #[test]
    fn request_encoding_enforces_the_decode_string_limit() {
        let request =
            RequestText::new("A".repeat(MAX_DECODED_STRING_BYTES + 1)).expect("core request");
        assert_eq!(encode_request(&request), Err(ProtocolError::StringTooLarge));
    }

    #[test]
    fn protocol_error_is_bounded_and_canonical() {
        let encoded = encode_protocol_error(ProtocolError::InputTooLarge).expect("encoding");
        assert_eq!(
            encoded,
            br#"{"version":1,"outcome":{"type":"protocol_error","code":"input_too_large","limit":65536}}"#
        );
        assert!(encoded.len() <= MAX_PROTOCOL_ERROR_BYTES);
    }

    #[test]
    fn rejects_malformed_error_metadata() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("request");
        let malformed = br#"{"version":1,"outcome":{"type":"protocol_error","code":"input_too_large","limit":1}}"#;
        assert_eq!(
            decode_outcome(malformed, &source),
            Err(ProtocolError::InvalidOutcome)
        );
    }
}
