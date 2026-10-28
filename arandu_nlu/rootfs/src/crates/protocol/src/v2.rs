use crate::{
    MAX_PROTOCOL_ERROR_BYTES, ProtocolError, ProtocolErrorCode, RequestVersion, preflight,
};
use core::fmt;
use core::num::NonZeroU64;
use nlu_core::{
    AbstentionReason, ArgumentEndpoint, ArgumentShare, CapabilityId, CatalogGeneration,
    ClauseSemantics, ComposedPlan, EntityId, EntityRef, EvidenceAtom, EvidenceKind,
    GraphExecutionClass, IndependentPair, IntentId, MAX_CLARIFICATION_OPTIONS, MAX_EVIDENCE_SPANS,
    MAX_PLAN_NODES, MAX_RELATIONS, MAX_SLOTS_PER_NODE, NodeId, OperationId, Plan, PlanNode,
    Polarity, Relation, RelationEvidence, RelationKind, RequestText, Slot, SlotId, SlotValue,
    Utf8Span,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};

pub const VERSION: u16 = 2;
pub const MAX_WIRE_BYTES: usize = 131_072;
pub const MAX_DECODED_STRING_BYTES: usize = 16_384;
pub const MAX_NESTING_DEPTH: usize = 32;
pub const MAX_STRUCTURAL_ITEMS: usize = 4_096;
pub const MAX_NUMERIC_TOKEN_BYTES: usize = 20;
pub const MAX_DIAGNOSTICS: usize = 8;
pub const SESSION_ID_BYTES: usize = 32;

const SUPPORTED_VERSIONS: [u16; 2] = [1, VERSION];
const PREFLIGHT_LIMITS: preflight::Limits = preflight::Limits::strict(
    MAX_WIRE_BYTES,
    MAX_DECODED_STRING_BYTES,
    MAX_NESTING_DEPTH,
    MAX_STRUCTURAL_ITEMS,
    MAX_NUMERIC_TOKEN_BYTES,
);

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct SessionId([u8; SESSION_ID_BYTES]);

impl SessionId {
    #[must_use]
    pub const fn from_bytes(bytes: [u8; SESSION_ID_BYTES]) -> Self {
        Self(bytes)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; SESSION_ID_BYTES] {
        &self.0
    }
}

impl fmt::Debug for SessionId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SessionId { bytes: redacted }")
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ConfirmationId(NonZeroU64);

impl ConfirmationId {
    pub fn new(value: u64) -> Result<Self, ProtocolError> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or(ProtocolError::InvalidOutcome)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Request {
    Interpret {
        session_id: SessionId,
        text: RequestText,
    },
    Continue {
        session_id: SessionId,
        selection: EntityRef,
    },
    Confirm {
        session_id: SessionId,
        confirmation_id: ConfirmationId,
        plan: ComposedPlan,
    },
    Cancel {
        session_id: SessionId,
    },
    Health,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum DiagnosticCode {
    InsufficientEvidence,
    Ambiguous,
    Unsupported,
    SessionUnavailable,
    SessionExpired,
    InvalidSelection,
    StaleCatalogGeneration,
    PolicyRuleMissing,
    PolicyDenied,
    UnsupportedGraphClass,
    ConfirmationUnavailable,
    ConfirmationExpired,
    WireByteLimit,
    StringByteLimit,
    StructuralItemLimit,
    PlanNodeLimit,
    RelationLimit,
    SlotLimit,
    EvidenceLimit,
    ReferentLimit,
    DiagnosticLimit,
}

impl DiagnosticCode {
    #[must_use]
    pub const fn fixed_limit(self) -> Option<u32> {
        match self {
            Self::WireByteLimit => Some(MAX_WIRE_BYTES as u32),
            Self::StringByteLimit => Some(MAX_DECODED_STRING_BYTES as u32),
            Self::StructuralItemLimit => Some(MAX_STRUCTURAL_ITEMS as u32),
            Self::PlanNodeLimit => Some(MAX_PLAN_NODES as u32),
            Self::RelationLimit => Some(MAX_RELATIONS as u32),
            Self::SlotLimit => Some(MAX_SLOTS_PER_NODE as u32),
            Self::EvidenceLimit => Some(MAX_EVIDENCE_SPANS as u32),
            Self::ReferentLimit => Some(MAX_CLARIFICATION_OPTIONS as u32),
            Self::DiagnosticLimit => Some(MAX_DIAGNOSTICS as u32),
            Self::InsufficientEvidence
            | Self::Ambiguous
            | Self::Unsupported
            | Self::SessionUnavailable
            | Self::SessionExpired
            | Self::InvalidSelection
            | Self::StaleCatalogGeneration
            | Self::PolicyRuleMissing
            | Self::PolicyDenied
            | Self::UnsupportedGraphClass
            | Self::ConfirmationUnavailable
            | Self::ConfirmationExpired => None,
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Diagnostic {
    code: DiagnosticCode,
    node: Option<NodeId>,
}

impl Diagnostic {
    #[must_use]
    pub const fn new(code: DiagnosticCode, node: Option<NodeId>) -> Self {
        Self { code, node }
    }

    #[must_use]
    pub const fn code(&self) -> DiagnosticCode {
        self.code
    }

    #[must_use]
    pub const fn node(&self) -> Option<&NodeId> {
        self.node.as_ref()
    }

    #[must_use]
    pub const fn limit(&self) -> Option<u32> {
        self.code.fixed_limit()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RiskClass {
    ReadOnly,
    Routine,
    Sensitive,
    Critical,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PolicyDenialReason {
    MissingRule,
    ExplicitDeny,
    UnsupportedGraphClass,
    NonExecutable,
    StaleCatalogGeneration,
    StalePolicyGeneration,
    Contradiction,
    ConfirmationMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CancellationStatus {
    Cancelled,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Readiness {
    Ready,
    Reloading,
    NotReady,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Generation(NonZeroU64);

impl Generation {
    pub fn new(value: u64) -> Result<Self, ProtocolError> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or(ProtocolError::InvalidOutcome)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EntityClarification {
    session_id: SessionId,
    referents: Vec<EntityRef>,
}

impl EntityClarification {
    pub fn new(
        session_id: SessionId,
        mut referents: Vec<EntityRef>,
    ) -> Result<Self, ProtocolError> {
        if referents.is_empty() || referents.len() > MAX_CLARIFICATION_OPTIONS {
            return Err(ProtocolError::InvalidOutcome);
        }
        referents.sort();
        if referents.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(ProtocolError::InvalidOutcome);
        }
        let generation = referents[0].generation();
        if referents
            .iter()
            .any(|referent| referent.generation() != generation)
        {
            return Err(ProtocolError::InvalidOutcome);
        }
        Ok(Self {
            session_id,
            referents,
        })
    }

    #[must_use]
    pub const fn session_id(&self) -> SessionId {
        self.session_id
    }

    #[must_use]
    pub fn referents(&self) -> &[EntityRef] {
        &self.referents
    }

    #[must_use]
    pub const fn is_continuation(&self) -> bool {
        true
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfirmationRequired {
    session_id: SessionId,
    confirmation_id: ConfirmationId,
    risk: RiskClass,
    plan: ComposedPlan,
}

impl ConfirmationRequired {
    #[must_use]
    pub const fn new(
        session_id: SessionId,
        confirmation_id: ConfirmationId,
        risk: RiskClass,
        plan: ComposedPlan,
    ) -> Self {
        Self {
            session_id,
            confirmation_id,
            risk,
            plan,
        }
    }

    #[must_use]
    pub const fn session_id(&self) -> SessionId {
        self.session_id
    }

    #[must_use]
    pub const fn confirmation_id(&self) -> ConfirmationId {
        self.confirmation_id
    }

    #[must_use]
    pub const fn risk(&self) -> RiskClass {
        self.risk
    }

    #[must_use]
    pub const fn plan(&self) -> &ComposedPlan {
        &self.plan
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PolicyAccepted {
    plan: ComposedPlan,
    risk: RiskClass,
}

impl PolicyAccepted {
    #[must_use]
    pub const fn new(plan: ComposedPlan, risk: RiskClass) -> Self {
        Self { plan, risk }
    }

    #[must_use]
    pub const fn plan(&self) -> &ComposedPlan {
        &self.plan
    }

    #[must_use]
    pub const fn risk(&self) -> RiskClass {
        self.risk
    }

    #[must_use]
    pub const fn authorizes_execution(&self) -> bool {
        false
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Health {
    readiness: Readiness,
    catalog_generation: Option<Generation>,
    policy_generation: Option<Generation>,
    configuration_generation: Option<Generation>,
}

impl Health {
    #[must_use]
    pub const fn new(
        readiness: Readiness,
        catalog_generation: Option<Generation>,
        policy_generation: Option<Generation>,
        configuration_generation: Option<Generation>,
    ) -> Self {
        Self {
            readiness,
            catalog_generation,
            policy_generation,
            configuration_generation,
        }
    }

    #[must_use]
    pub const fn readiness(&self) -> Readiness {
        self.readiness
    }

    #[must_use]
    pub const fn supported_versions(&self) -> &[u16; 2] {
        &SUPPORTED_VERSIONS
    }

    #[must_use]
    pub const fn catalog_generation(&self) -> Option<Generation> {
        self.catalog_generation
    }

    #[must_use]
    pub const fn policy_generation(&self) -> Option<Generation> {
        self.policy_generation
    }

    #[must_use]
    pub const fn configuration_generation(&self) -> Option<Generation> {
        self.configuration_generation
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Outcome {
    CompletePlan(ComposedPlan),
    EntityClarification(EntityClarification),
    Abstention(AbstentionReason),
    PolicyDenial(PolicyDenialReason),
    ConfirmationRequired(ConfirmationRequired),
    PolicyAccepted(PolicyAccepted),
    Cancellation(CancellationStatus),
    Health(Health),
    ProtocolError(ProtocolError),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Response {
    outcome: Outcome,
    diagnostics: Vec<Diagnostic>,
}

impl Response {
    pub fn new(outcome: Outcome, mut diagnostics: Vec<Diagnostic>) -> Result<Self, ProtocolError> {
        if diagnostics.len() > MAX_DIAGNOSTICS {
            return Err(ProtocolError::InvalidOutcome);
        }
        diagnostics.sort();
        if diagnostics.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(ProtocolError::InvalidOutcome);
        }
        Ok(Self {
            outcome,
            diagnostics,
        })
    }

    #[must_use]
    pub const fn without_diagnostics(outcome: Outcome) -> Self {
        Self {
            outcome,
            diagnostics: Vec::new(),
        }
    }

    #[must_use]
    pub const fn outcome(&self) -> &Outcome {
        &self.outcome
    }

    #[must_use]
    pub fn diagnostics(&self) -> &[Diagnostic] {
        &self.diagnostics
    }

    #[must_use]
    pub fn into_parts(self) -> (Outcome, Vec<Diagnostic>) {
        (self.outcome, self.diagnostics)
    }
}

pub fn decode_request(bytes: &[u8]) -> Result<Request, ProtocolError> {
    let text = preflight::inspect_with_limits(bytes, PREFLIGHT_LIMITS)?;
    let envelope: RequestEnvelopeDto = decode_json(text.as_bytes())?;
    require_version(envelope.version)?;
    envelope.request.into_public()
}

pub(crate) fn detect_request_version(bytes: &[u8]) -> Result<RequestVersion, ProtocolError> {
    let text = preflight::inspect_with_limits(bytes, PREFLIGHT_LIMITS)?;
    let envelope: VersionProbeDto = decode_json(text.as_bytes())?;
    match envelope.version {
        crate::v1::VERSION => Ok(RequestVersion::V1),
        VERSION => Ok(RequestVersion::V2),
        _ => Err(ProtocolError::UnsupportedVersion),
    }
}

pub fn encode_request(request: &Request) -> Result<Vec<u8>, ProtocolError> {
    if matches!(request, Request::Interpret { text, .. } if text.len() > MAX_DECODED_STRING_BYTES)
        || matches!(request, Request::Confirm { plan, .. } if plan.source().len() > MAX_DECODED_STRING_BYTES)
    {
        return Err(ProtocolError::StringTooLarge);
    }
    let envelope = RequestEnvelopeDto {
        version: VERSION,
        request: RequestDto::from_public(request),
    };
    encode_json(&envelope, MAX_WIRE_BYTES)
}

pub fn decode_response(bytes: &[u8], source: &RequestText) -> Result<Response, ProtocolError> {
    let text = preflight::inspect_with_limits(bytes, PREFLIGHT_LIMITS)?;
    let envelope: ResponseEnvelopeDto = decode_json(text.as_bytes())?;
    require_version(envelope.version)?;
    let outcome = envelope.outcome.into_public(source)?;
    let diagnostics = envelope
        .diagnostics
        .into_iter()
        .map(DiagnosticDto::into_public)
        .collect::<Result<Vec<_>, _>>()?;
    Response::new(outcome, diagnostics)
}

pub fn encode_response(response: &Response) -> Result<Vec<u8>, ProtocolError> {
    let oversized_source = match response.outcome() {
        Outcome::CompletePlan(plan) => plan.source().len() > MAX_DECODED_STRING_BYTES,
        Outcome::ConfirmationRequired(required) => {
            required.plan().source().len() > MAX_DECODED_STRING_BYTES
        }
        Outcome::PolicyAccepted(accepted) => {
            accepted.plan().source().len() > MAX_DECODED_STRING_BYTES
        }
        Outcome::EntityClarification(_)
        | Outcome::Abstention(_)
        | Outcome::PolicyDenial(_)
        | Outcome::Cancellation(_)
        | Outcome::Health(_)
        | Outcome::ProtocolError(_) => false,
    };
    if oversized_source {
        return Err(ProtocolError::StringTooLarge);
    }
    let envelope = ResponseEnvelopeDto::from_public(response);
    let limit = if matches!(response.outcome(), Outcome::ProtocolError(_)) {
        MAX_PROTOCOL_ERROR_BYTES
    } else {
        MAX_WIRE_BYTES
    };
    encode_json(&envelope, limit)
}

pub fn encode_outcome(outcome: &Outcome) -> Result<Vec<u8>, ProtocolError> {
    encode_response(&Response::without_diagnostics(outcome.clone()))
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

fn encode_session_id(id: SessionId) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut bytes = [0_u8; SESSION_ID_BYTES * 2];
    for (index, byte) in id.as_bytes().iter().copied().enumerate() {
        bytes[index * 2] = HEX[usize::from(byte >> 4)];
        bytes[index * 2 + 1] = HEX[usize::from(byte & 0x0f)];
    }
    String::from_utf8(bytes.to_vec()).expect("lowercase hexadecimal is UTF-8")
}

fn decode_session_id(value: &str) -> Result<SessionId, ProtocolError> {
    if value.len() != SESSION_ID_BYTES * 2 {
        return Err(ProtocolError::InvalidIdentifier);
    }
    let mut decoded = [0_u8; SESSION_ID_BYTES];
    let (pairs, remainder) = value.as_bytes().as_chunks::<2>();
    debug_assert!(remainder.is_empty());
    for (index, pair) in pairs.iter().enumerate() {
        let high = decode_hex(pair[0]).ok_or(ProtocolError::InvalidIdentifier)?;
        let low = decode_hex(pair[1]).ok_or(ProtocolError::InvalidIdentifier)?;
        decoded[index] = (high << 4) | low;
    }
    Ok(SessionId::from_bytes(decoded))
}

const fn decode_hex(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RequestEnvelopeDto {
    version: u16,
    request: RequestDto,
}

#[derive(Deserialize)]
struct VersionProbeDto {
    version: u16,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum RequestDto {
    Interpret {
        session_id: String,
        text: String,
    },
    Continue {
        session_id: String,
        selection: EntityRefDto,
    },
    Confirm {
        session_id: String,
        confirmation_id: u64,
        plan: ComposedPlanDto,
    },
    Cancel {
        session_id: String,
    },
    Health {},
}

impl RequestDto {
    fn from_public(request: &Request) -> Self {
        match request {
            Request::Interpret { session_id, text } => Self::Interpret {
                session_id: encode_session_id(*session_id),
                text: text.as_str().into(),
            },
            Request::Continue {
                session_id,
                selection,
            } => Self::Continue {
                session_id: encode_session_id(*session_id),
                selection: EntityRefDto::from_core(selection),
            },
            Request::Confirm {
                session_id,
                confirmation_id,
                plan,
            } => Self::Confirm {
                session_id: encode_session_id(*session_id),
                confirmation_id: confirmation_id.get(),
                plan: ComposedPlanDto::from_core(plan),
            },
            Request::Cancel { session_id } => Self::Cancel {
                session_id: encode_session_id(*session_id),
            },
            Request::Health => Self::Health {},
        }
    }

    fn into_public(self) -> Result<Request, ProtocolError> {
        match self {
            Self::Interpret { session_id, text } => {
                if text.len() > MAX_DECODED_STRING_BYTES {
                    return Err(ProtocolError::StringTooLarge);
                }
                Ok(Request::Interpret {
                    session_id: decode_session_id(&session_id)?,
                    text: RequestText::new(text).map_err(ProtocolError::from)?,
                })
            }
            Self::Continue {
                session_id,
                selection,
            } => Ok(Request::Continue {
                session_id: decode_session_id(&session_id)?,
                selection: selection.into_core()?,
            }),
            Self::Confirm {
                session_id,
                confirmation_id,
                plan,
            } => Ok(Request::Confirm {
                session_id: decode_session_id(&session_id)?,
                confirmation_id: ConfirmationId::new(confirmation_id)?,
                plan: plan.into_core_embedded()?,
            }),
            Self::Cancel { session_id } => Ok(Request::Cancel {
                session_id: decode_session_id(&session_id)?,
            }),
            Self::Health {} => Ok(Request::Health),
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ResponseEnvelopeDto {
    version: u16,
    outcome: OutcomeDto,
    diagnostics: Vec<DiagnosticDto>,
}

impl ResponseEnvelopeDto {
    fn from_public(response: &Response) -> Self {
        Self {
            version: VERSION,
            outcome: OutcomeDto::from_public(response.outcome()),
            diagnostics: response
                .diagnostics()
                .iter()
                .map(DiagnosticDto::from_public)
                .collect(),
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum OutcomeDto {
    CompletePlan {
        plan: ComposedPlanDto,
    },
    EntityClarification {
        continuation: bool,
        session_id: String,
        referents: Vec<EntityRefDto>,
    },
    Abstention {
        reason: AbstentionReasonDto,
    },
    PolicyDenial {
        reason: PolicyDenialReasonDto,
    },
    ConfirmationRequired {
        session_id: String,
        confirmation_id: u64,
        risk: RiskClassDto,
        plan: ComposedPlanDto,
    },
    PolicyAccepted {
        authorizes_execution: bool,
        risk: RiskClassDto,
        plan: ComposedPlanDto,
    },
    Cancellation {
        status: CancellationStatusDto,
    },
    Health {
        readiness: ReadinessDto,
        supported_versions: Vec<u16>,
        catalog_generation: Option<u64>,
        policy_generation: Option<u64>,
        configuration_generation: Option<u64>,
    },
    ProtocolError {
        code: ProtocolErrorCode,
        limit: Option<u32>,
    },
}

impl OutcomeDto {
    fn from_public(outcome: &Outcome) -> Self {
        match outcome {
            Outcome::CompletePlan(plan) => Self::CompletePlan {
                plan: ComposedPlanDto::from_core(plan),
            },
            Outcome::EntityClarification(clarification) => Self::EntityClarification {
                continuation: clarification.is_continuation(),
                session_id: encode_session_id(clarification.session_id()),
                referents: clarification
                    .referents()
                    .iter()
                    .map(EntityRefDto::from_core)
                    .collect(),
            },
            Outcome::Abstention(reason) => Self::Abstention {
                reason: AbstentionReasonDto::from_core(*reason),
            },
            Outcome::PolicyDenial(reason) => Self::PolicyDenial {
                reason: PolicyDenialReasonDto::from_public(*reason),
            },
            Outcome::ConfirmationRequired(required) => Self::ConfirmationRequired {
                session_id: encode_session_id(required.session_id()),
                confirmation_id: required.confirmation_id().get(),
                risk: RiskClassDto::from_public(required.risk()),
                plan: ComposedPlanDto::from_core(required.plan()),
            },
            Outcome::PolicyAccepted(accepted) => Self::PolicyAccepted {
                authorizes_execution: accepted.authorizes_execution(),
                risk: RiskClassDto::from_public(accepted.risk()),
                plan: ComposedPlanDto::from_core(accepted.plan()),
            },
            Outcome::Cancellation(status) => Self::Cancellation {
                status: CancellationStatusDto::from_public(*status),
            },
            Outcome::Health(health) => Self::Health {
                readiness: ReadinessDto::from_public(health.readiness()),
                supported_versions: health.supported_versions().to_vec(),
                catalog_generation: health.catalog_generation().map(Generation::get),
                policy_generation: health.policy_generation().map(Generation::get),
                configuration_generation: health.configuration_generation().map(Generation::get),
            },
            Outcome::ProtocolError(error) => Self::ProtocolError {
                code: error.code(),
                limit: protocol_error_limit(*error),
            },
        }
    }

    fn into_public(self, source: &RequestText) -> Result<Outcome, ProtocolError> {
        match self {
            Self::CompletePlan { plan } => plan.into_core(source).map(Outcome::CompletePlan),
            Self::EntityClarification {
                continuation,
                session_id,
                referents,
            } => {
                if !continuation {
                    return Err(ProtocolError::InvalidOutcome);
                }
                let referents = referents
                    .into_iter()
                    .map(EntityRefDto::into_core)
                    .collect::<Result<Vec<_>, _>>()?;
                EntityClarification::new(decode_session_id(&session_id)?, referents)
                    .map(Outcome::EntityClarification)
            }
            Self::Abstention { reason } => Ok(Outcome::Abstention(reason.into_core())),
            Self::PolicyDenial { reason } => Ok(Outcome::PolicyDenial(reason.into_public())),
            Self::ConfirmationRequired {
                session_id,
                confirmation_id,
                risk,
                plan,
            } => Ok(Outcome::ConfirmationRequired(ConfirmationRequired::new(
                decode_session_id(&session_id)?,
                ConfirmationId::new(confirmation_id)?,
                risk.into_public(),
                plan.into_core(source)?,
            ))),
            Self::PolicyAccepted {
                authorizes_execution,
                risk,
                plan,
            } => {
                if authorizes_execution {
                    return Err(ProtocolError::InvalidOutcome);
                }
                Ok(Outcome::PolicyAccepted(PolicyAccepted::new(
                    plan.into_core(source)?,
                    risk.into_public(),
                )))
            }
            Self::Cancellation { status } => Ok(Outcome::Cancellation(status.into_public())),
            Self::Health {
                readiness,
                supported_versions,
                catalog_generation,
                policy_generation,
                configuration_generation,
            } => {
                if supported_versions != SUPPORTED_VERSIONS {
                    return Err(ProtocolError::InvalidOutcome);
                }
                Ok(Outcome::Health(Health::new(
                    readiness.into_public(),
                    optional_generation(catalog_generation)?,
                    optional_generation(policy_generation)?,
                    optional_generation(configuration_generation)?,
                )))
            }
            Self::ProtocolError { code, limit } => {
                protocol_error_from_wire(code, limit).map(Outcome::ProtocolError)
            }
        }
    }
}

fn optional_generation(value: Option<u64>) -> Result<Option<Generation>, ProtocolError> {
    value.map(Generation::new).transpose()
}

fn protocol_error_limit(error: ProtocolError) -> Option<u32> {
    match error {
        ProtocolError::InputTooLarge => Some(MAX_WIRE_BYTES as u32),
        ProtocolError::StringTooLarge => Some(MAX_DECODED_STRING_BYTES as u32),
        ProtocolError::NestingTooDeep => Some(MAX_NESTING_DEPTH as u32),
        ProtocolError::StructuralLimitExceeded => Some(MAX_STRUCTURAL_ITEMS as u32),
        ProtocolError::NumericTokenTooLong => Some(MAX_NUMERIC_TOKEN_BYTES as u32),
        ProtocolError::InvalidUtf8
        | ProtocolError::NonIntegerNumber
        | ProtocolError::MalformedJson
        | ProtocolError::UnsupportedVersion
        | ProtocolError::InvalidIdentifier
        | ProtocolError::InvalidSpan
        | ProtocolError::InvalidGraph
        | ProtocolError::EmptyPlan
        | ProtocolError::InvalidOutcome
        | ProtocolError::EncodingFailure => None,
    }
}

fn protocol_error_from_wire(
    code: ProtocolErrorCode,
    limit: Option<u32>,
) -> Result<ProtocolError, ProtocolError> {
    let error = match code {
        ProtocolErrorCode::InvalidUtf8 => ProtocolError::InvalidUtf8,
        ProtocolErrorCode::InputTooLarge => ProtocolError::InputTooLarge,
        ProtocolErrorCode::StringTooLarge => ProtocolError::StringTooLarge,
        ProtocolErrorCode::NestingTooDeep => ProtocolError::NestingTooDeep,
        ProtocolErrorCode::StructuralLimitExceeded => ProtocolError::StructuralLimitExceeded,
        ProtocolErrorCode::NumericTokenTooLong => ProtocolError::NumericTokenTooLong,
        ProtocolErrorCode::NonIntegerNumber => ProtocolError::NonIntegerNumber,
        ProtocolErrorCode::MalformedJson => ProtocolError::MalformedJson,
        ProtocolErrorCode::UnsupportedVersion => ProtocolError::UnsupportedVersion,
        ProtocolErrorCode::InvalidIdentifier => ProtocolError::InvalidIdentifier,
        ProtocolErrorCode::InvalidSpan => ProtocolError::InvalidSpan,
        ProtocolErrorCode::InvalidGraph => ProtocolError::InvalidGraph,
        ProtocolErrorCode::EmptyPlan => ProtocolError::EmptyPlan,
        ProtocolErrorCode::InvalidOutcome => ProtocolError::InvalidOutcome,
        ProtocolErrorCode::EncodingFailure => ProtocolError::EncodingFailure,
    };
    if protocol_error_limit(error) == limit {
        Ok(error)
    } else {
        Err(ProtocolError::InvalidOutcome)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct DiagnosticDto {
    code: DiagnosticCodeDto,
    node: Option<String>,
    limit: Option<u32>,
}

impl DiagnosticDto {
    fn from_public(diagnostic: &Diagnostic) -> Self {
        Self {
            code: DiagnosticCodeDto::from_public(diagnostic.code()),
            node: diagnostic.node().map(|node| node.as_str().into()),
            limit: diagnostic.limit(),
        }
    }

    fn into_public(self) -> Result<Diagnostic, ProtocolError> {
        let code = self.code.into_public();
        if code.fixed_limit() != self.limit {
            return Err(ProtocolError::InvalidOutcome);
        }
        let node = self
            .node
            .map(|node| NodeId::new(&node).map_err(ProtocolError::from))
            .transpose()?;
        Ok(Diagnostic::new(code, node))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum DiagnosticCodeDto {
    InsufficientEvidence,
    Ambiguous,
    Unsupported,
    SessionUnavailable,
    SessionExpired,
    InvalidSelection,
    StaleCatalogGeneration,
    PolicyRuleMissing,
    PolicyDenied,
    UnsupportedGraphClass,
    ConfirmationUnavailable,
    ConfirmationExpired,
    WireByteLimit,
    StringByteLimit,
    StructuralItemLimit,
    PlanNodeLimit,
    RelationLimit,
    SlotLimit,
    EvidenceLimit,
    ReferentLimit,
    DiagnosticLimit,
}

impl DiagnosticCodeDto {
    const fn from_public(code: DiagnosticCode) -> Self {
        match code {
            DiagnosticCode::InsufficientEvidence => Self::InsufficientEvidence,
            DiagnosticCode::Ambiguous => Self::Ambiguous,
            DiagnosticCode::Unsupported => Self::Unsupported,
            DiagnosticCode::SessionUnavailable => Self::SessionUnavailable,
            DiagnosticCode::SessionExpired => Self::SessionExpired,
            DiagnosticCode::InvalidSelection => Self::InvalidSelection,
            DiagnosticCode::StaleCatalogGeneration => Self::StaleCatalogGeneration,
            DiagnosticCode::PolicyRuleMissing => Self::PolicyRuleMissing,
            DiagnosticCode::PolicyDenied => Self::PolicyDenied,
            DiagnosticCode::UnsupportedGraphClass => Self::UnsupportedGraphClass,
            DiagnosticCode::ConfirmationUnavailable => Self::ConfirmationUnavailable,
            DiagnosticCode::ConfirmationExpired => Self::ConfirmationExpired,
            DiagnosticCode::WireByteLimit => Self::WireByteLimit,
            DiagnosticCode::StringByteLimit => Self::StringByteLimit,
            DiagnosticCode::StructuralItemLimit => Self::StructuralItemLimit,
            DiagnosticCode::PlanNodeLimit => Self::PlanNodeLimit,
            DiagnosticCode::RelationLimit => Self::RelationLimit,
            DiagnosticCode::SlotLimit => Self::SlotLimit,
            DiagnosticCode::EvidenceLimit => Self::EvidenceLimit,
            DiagnosticCode::ReferentLimit => Self::ReferentLimit,
            DiagnosticCode::DiagnosticLimit => Self::DiagnosticLimit,
        }
    }

    const fn into_public(self) -> DiagnosticCode {
        match self {
            Self::InsufficientEvidence => DiagnosticCode::InsufficientEvidence,
            Self::Ambiguous => DiagnosticCode::Ambiguous,
            Self::Unsupported => DiagnosticCode::Unsupported,
            Self::SessionUnavailable => DiagnosticCode::SessionUnavailable,
            Self::SessionExpired => DiagnosticCode::SessionExpired,
            Self::InvalidSelection => DiagnosticCode::InvalidSelection,
            Self::StaleCatalogGeneration => DiagnosticCode::StaleCatalogGeneration,
            Self::PolicyRuleMissing => DiagnosticCode::PolicyRuleMissing,
            Self::PolicyDenied => DiagnosticCode::PolicyDenied,
            Self::UnsupportedGraphClass => DiagnosticCode::UnsupportedGraphClass,
            Self::ConfirmationUnavailable => DiagnosticCode::ConfirmationUnavailable,
            Self::ConfirmationExpired => DiagnosticCode::ConfirmationExpired,
            Self::WireByteLimit => DiagnosticCode::WireByteLimit,
            Self::StringByteLimit => DiagnosticCode::StringByteLimit,
            Self::StructuralItemLimit => DiagnosticCode::StructuralItemLimit,
            Self::PlanNodeLimit => DiagnosticCode::PlanNodeLimit,
            Self::RelationLimit => DiagnosticCode::RelationLimit,
            Self::SlotLimit => DiagnosticCode::SlotLimit,
            Self::EvidenceLimit => DiagnosticCode::EvidenceLimit,
            Self::ReferentLimit => DiagnosticCode::ReferentLimit,
            Self::DiagnosticLimit => DiagnosticCode::DiagnosticLimit,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ComposedPlanDto {
    source: String,
    catalog_generation: u64,
    execution_class: GraphExecutionClassDto,
    nodes: Vec<PlanNodeDto>,
    relations: Vec<RelationDto>,
    clauses: Vec<ClauseDto>,
    relation_evidence: Vec<RelationEvidenceDto>,
    independent_pairs: Vec<IndependentPairDto>,
    argument_shares: Vec<ArgumentShareDto>,
}

impl ComposedPlanDto {
    fn from_core(composed: &ComposedPlan) -> Self {
        Self {
            source: composed.source().as_str().into(),
            catalog_generation: composed.plan().catalog_generation().get(),
            execution_class: GraphExecutionClassDto::from_core(composed.execution_class()),
            nodes: composed
                .plan()
                .nodes()
                .iter()
                .map(PlanNodeDto::from_core)
                .collect(),
            relations: composed
                .plan()
                .relations()
                .iter()
                .map(RelationDto::from_core)
                .collect(),
            clauses: composed
                .clauses()
                .iter()
                .map(ClauseDto::from_core)
                .collect(),
            relation_evidence: composed
                .relation_evidence()
                .iter()
                .map(RelationEvidenceDto::from_core)
                .collect(),
            independent_pairs: composed
                .independent_pairs()
                .iter()
                .map(IndependentPairDto::from_core)
                .collect(),
            argument_shares: composed
                .argument_shares()
                .iter()
                .map(ArgumentShareDto::from_core)
                .collect(),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<ComposedPlan, ProtocolError> {
        if self.source.as_bytes() != source.as_bytes() {
            return Err(ProtocolError::InvalidOutcome);
        }
        self.into_core_bound(source)
    }

    fn into_core_embedded(self) -> Result<ComposedPlan, ProtocolError> {
        let source = RequestText::new(self.source.clone()).map_err(ProtocolError::from)?;
        self.into_core_bound(&source)
    }

    fn into_core_bound(self, source: &RequestText) -> Result<ComposedPlan, ProtocolError> {
        let generation =
            CatalogGeneration::new(self.catalog_generation).map_err(ProtocolError::from)?;
        let nodes = self
            .nodes
            .into_iter()
            .map(|node| node.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        let relations = self
            .relations
            .into_iter()
            .map(RelationDto::into_core)
            .collect::<Result<Vec<_>, _>>()?;
        let plan = Plan::new(source, generation, nodes, relations).map_err(ProtocolError::from)?;
        let clauses = self
            .clauses
            .into_iter()
            .map(|clause| clause.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        let relation_evidence = self
            .relation_evidence
            .into_iter()
            .map(|relation| relation.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        let independent_pairs = self
            .independent_pairs
            .into_iter()
            .map(IndependentPairDto::into_core)
            .collect::<Result<Vec<_>, _>>()?;
        let argument_shares = self
            .argument_shares
            .into_iter()
            .map(|share| share.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        ComposedPlan::new(
            source,
            plan,
            self.execution_class.into_core(),
            clauses,
            relation_evidence,
            independent_pairs,
            argument_shares,
        )
        .map_err(ProtocolError::from)
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
struct EntityRefDto {
    id: String,
    generation: u64,
}

impl EntityRefDto {
    fn from_core(entity: &EntityRef) -> Self {
        Self {
            id: entity.id().as_str().into(),
            generation: entity.generation().get(),
        }
    }

    fn into_core(self) -> Result<EntityRef, ProtocolError> {
        let id = EntityId::new(&self.id).map_err(ProtocolError::from)?;
        let generation = CatalogGeneration::new(self.generation).map_err(ProtocolError::from)?;
        Ok(EntityRef::new(id, generation))
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
        Ok(Slot::new(id, self.value.into_core(source)?))
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
        Ok(Relation::new(
            NodeId::new(&self.from).map_err(ProtocolError::from)?,
            NodeId::new(&self.to).map_err(ProtocolError::from)?,
            self.kind.into_core(),
        ))
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
struct ClauseDto {
    node: String,
    intent: String,
    polarity: PolarityDto,
    evidence: Vec<EvidenceAtomDto>,
}

impl ClauseDto {
    fn from_core(clause: &ClauseSemantics) -> Self {
        Self {
            node: clause.node().as_str().into(),
            intent: clause.intent().as_str().into(),
            polarity: PolarityDto::from_core(clause.polarity()),
            evidence: clause
                .evidence()
                .iter()
                .map(EvidenceAtomDto::from_core)
                .collect(),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<ClauseSemantics, ProtocolError> {
        let node = NodeId::new(&self.node).map_err(ProtocolError::from)?;
        let intent = IntentId::new(&self.intent).map_err(ProtocolError::from)?;
        let evidence = self
            .evidence
            .into_iter()
            .map(|atom| atom.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        ClauseSemantics::new(node, intent, self.polarity.into_core(), evidence)
            .map_err(ProtocolError::from)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum EvidenceAtomDto {
    Predicate { span: SpanDto },
    Argument { slot: String, span: SpanDto },
    Negation { span: SpanDto },
}

impl EvidenceAtomDto {
    fn from_core(atom: &EvidenceAtom) -> Self {
        match atom.kind() {
            EvidenceKind::Predicate => Self::Predicate {
                span: SpanDto::from_core(atom.span()),
            },
            EvidenceKind::Argument(slot) => Self::Argument {
                slot: slot.as_str().into(),
                span: SpanDto::from_core(atom.span()),
            },
            EvidenceKind::Negation => Self::Negation {
                span: SpanDto::from_core(atom.span()),
            },
        }
    }

    fn into_core(self, source: &RequestText) -> Result<EvidenceAtom, ProtocolError> {
        match self {
            Self::Predicate { span } => Ok(EvidenceAtom::new(
                EvidenceKind::Predicate,
                span.into_core(source)?,
            )),
            Self::Argument { slot, span } => Ok(EvidenceAtom::new(
                EvidenceKind::Argument(SlotId::new(&slot).map_err(ProtocolError::from)?),
                span.into_core(source)?,
            )),
            Self::Negation { span } => Ok(EvidenceAtom::new(
                EvidenceKind::Negation,
                span.into_core(source)?,
            )),
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum PolarityDto {
    Affirmed,
    Negated,
}

impl PolarityDto {
    const fn from_core(polarity: Polarity) -> Self {
        match polarity {
            Polarity::Affirmed => Self::Affirmed,
            Polarity::Negated => Self::Negated,
        }
    }

    const fn into_core(self) -> Polarity {
        match self {
            Self::Affirmed => Polarity::Affirmed,
            Self::Negated => Polarity::Negated,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum GraphExecutionClassDto {
    PartialSafe,
    AtomicOnly,
    NonExecutable,
}

impl GraphExecutionClassDto {
    const fn from_core(class: GraphExecutionClass) -> Self {
        match class {
            GraphExecutionClass::PartialSafe => Self::PartialSafe,
            GraphExecutionClass::AtomicOnly => Self::AtomicOnly,
            GraphExecutionClass::NonExecutable => Self::NonExecutable,
        }
    }

    const fn into_core(self) -> GraphExecutionClass {
        match self {
            Self::PartialSafe => GraphExecutionClass::PartialSafe,
            Self::AtomicOnly => GraphExecutionClass::AtomicOnly,
            Self::NonExecutable => GraphExecutionClass::NonExecutable,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RelationEvidenceDto {
    relation: RelationDto,
    evidence: Vec<SpanDto>,
}

impl RelationEvidenceDto {
    fn from_core(evidence: &RelationEvidence) -> Self {
        Self {
            relation: RelationDto::from_core(evidence.relation()),
            evidence: evidence.evidence().iter().map(SpanDto::from_core).collect(),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<RelationEvidence, ProtocolError> {
        let relation = self.relation.into_core()?;
        let evidence = self
            .evidence
            .into_iter()
            .map(|span| span.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        RelationEvidence::new(relation, evidence).map_err(ProtocolError::from)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct IndependentPairDto {
    left: String,
    right: String,
}

impl IndependentPairDto {
    fn from_core(pair: &IndependentPair) -> Self {
        Self {
            left: pair.left().as_str().into(),
            right: pair.right().as_str().into(),
        }
    }

    fn into_core(self) -> Result<IndependentPair, ProtocolError> {
        IndependentPair::new(
            NodeId::new(&self.left).map_err(ProtocolError::from)?,
            NodeId::new(&self.right).map_err(ProtocolError::from)?,
        )
        .map_err(ProtocolError::from)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct EndpointDto {
    node: String,
    slot: String,
}

impl EndpointDto {
    fn from_core(endpoint: &ArgumentEndpoint) -> Self {
        Self {
            node: endpoint.node().as_str().into(),
            slot: endpoint.slot().as_str().into(),
        }
    }

    fn into_core(self) -> Result<ArgumentEndpoint, ProtocolError> {
        Ok(ArgumentEndpoint::new(
            NodeId::new(&self.node).map_err(ProtocolError::from)?,
            SlotId::new(&self.slot).map_err(ProtocolError::from)?,
        ))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ArgumentShareDto {
    from: EndpointDto,
    to: EndpointDto,
    evidence: Vec<SpanDto>,
}

impl ArgumentShareDto {
    fn from_core(share: &ArgumentShare) -> Self {
        Self {
            from: EndpointDto::from_core(share.from()),
            to: EndpointDto::from_core(share.to()),
            evidence: share.evidence().iter().map(SpanDto::from_core).collect(),
        }
    }

    fn into_core(self, source: &RequestText) -> Result<ArgumentShare, ProtocolError> {
        let evidence = self
            .evidence
            .into_iter()
            .map(|span| span.into_core(source))
            .collect::<Result<Vec<_>, _>>()?;
        ArgumentShare::new(self.from.into_core()?, self.to.into_core()?, evidence)
            .map_err(ProtocolError::from)
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

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum RiskClassDto {
    ReadOnly,
    Routine,
    Sensitive,
    Critical,
}

impl RiskClassDto {
    const fn from_public(risk: RiskClass) -> Self {
        match risk {
            RiskClass::ReadOnly => Self::ReadOnly,
            RiskClass::Routine => Self::Routine,
            RiskClass::Sensitive => Self::Sensitive,
            RiskClass::Critical => Self::Critical,
        }
    }

    const fn into_public(self) -> RiskClass {
        match self {
            Self::ReadOnly => RiskClass::ReadOnly,
            Self::Routine => RiskClass::Routine,
            Self::Sensitive => RiskClass::Sensitive,
            Self::Critical => RiskClass::Critical,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum PolicyDenialReasonDto {
    MissingRule,
    ExplicitDeny,
    UnsupportedGraphClass,
    NonExecutable,
    StaleCatalogGeneration,
    StalePolicyGeneration,
    Contradiction,
    ConfirmationMismatch,
}

impl PolicyDenialReasonDto {
    const fn from_public(reason: PolicyDenialReason) -> Self {
        match reason {
            PolicyDenialReason::MissingRule => Self::MissingRule,
            PolicyDenialReason::ExplicitDeny => Self::ExplicitDeny,
            PolicyDenialReason::UnsupportedGraphClass => Self::UnsupportedGraphClass,
            PolicyDenialReason::NonExecutable => Self::NonExecutable,
            PolicyDenialReason::StaleCatalogGeneration => Self::StaleCatalogGeneration,
            PolicyDenialReason::StalePolicyGeneration => Self::StalePolicyGeneration,
            PolicyDenialReason::Contradiction => Self::Contradiction,
            PolicyDenialReason::ConfirmationMismatch => Self::ConfirmationMismatch,
        }
    }

    const fn into_public(self) -> PolicyDenialReason {
        match self {
            Self::MissingRule => PolicyDenialReason::MissingRule,
            Self::ExplicitDeny => PolicyDenialReason::ExplicitDeny,
            Self::UnsupportedGraphClass => PolicyDenialReason::UnsupportedGraphClass,
            Self::NonExecutable => PolicyDenialReason::NonExecutable,
            Self::StaleCatalogGeneration => PolicyDenialReason::StaleCatalogGeneration,
            Self::StalePolicyGeneration => PolicyDenialReason::StalePolicyGeneration,
            Self::Contradiction => PolicyDenialReason::Contradiction,
            Self::ConfirmationMismatch => PolicyDenialReason::ConfirmationMismatch,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum CancellationStatusDto {
    Cancelled,
    Unavailable,
}

impl CancellationStatusDto {
    const fn from_public(status: CancellationStatus) -> Self {
        match status {
            CancellationStatus::Cancelled => Self::Cancelled,
            CancellationStatus::Unavailable => Self::Unavailable,
        }
    }

    const fn into_public(self) -> CancellationStatus {
        match self {
            Self::Cancelled => CancellationStatus::Cancelled,
            Self::Unavailable => CancellationStatus::Unavailable,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum ReadinessDto {
    Ready,
    Reloading,
    NotReady,
}

impl ReadinessDto {
    const fn from_public(readiness: Readiness) -> Self {
        match readiness {
            Readiness::Ready => Self::Ready,
            Readiness::Reloading => Self::Reloading,
            Readiness::NotReady => Self::NotReady,
        }
    }

    const fn into_public(self) -> Readiness {
        match self {
            Self::Ready => Readiness::Ready,
            Self::Reloading => Readiness::Reloading,
            Self::NotReady => Readiness::NotReady,
        }
    }
}
