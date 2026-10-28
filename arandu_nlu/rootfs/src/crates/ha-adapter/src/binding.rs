use core::fmt;
use core::num::NonZeroU64;

use crate::{
    AdapterError, CallerId, CapabilityId, CatalogGeneration, ConnectionNonce, ContextId,
    NodeAttemptId, OperationId, PairingEpoch, PlanDigest, Result, SessionId, TypedOperation,
};

pub const AUTHENTICATED_PROTOCOL_VERSION: u16 = 1;
const TRANSCRIPT_DOMAIN: &[u8] = b"ha-adapter-authenticated-request-v1\0";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProtocolVersion(u16);

impl ProtocolVersion {
    pub fn new(value: u16) -> Result<Self> {
        if value != AUTHENTICATED_PROTOCOL_VERSION {
            return Err(AdapterError::InvalidProtocolVersion);
        }
        Ok(Self(value))
    }

    #[must_use]
    pub const fn get(self) -> u16 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Sequence(NonZeroU64);

impl Sequence {
    pub fn new(value: u64) -> Result<Self> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or(AdapterError::InvalidSequence)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Direction {
    AdapterToCompanion,
    CompanionToAdapter,
}

impl Direction {
    const fn transcript_tag(self) -> u8 {
        match self {
            Self::AdapterToCompanion => 1,
            Self::CompanionToAdapter => 2,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum DeliveryKind {
    Initial,
    Retry,
}

impl DeliveryKind {
    const fn transcript_tag(self) -> u8 {
        match self {
            Self::Initial => 1,
            Self::Retry => 2,
        }
    }
}

#[derive(Clone)]
pub struct AuthenticatedRequestParts {
    pub protocol_version: u16,
    pub epoch: PairingEpoch,
    pub direction: Direction,
    pub connection_nonce: ConnectionNonce,
    pub sequence: u64,
    pub delivery: DeliveryKind,
    pub operation_id: OperationId,
    pub node_attempt_id: NodeAttemptId,
    pub plan_digest: PlanDigest,
    pub session_id: SessionId,
    pub capability: CapabilityId,
    pub catalog_generation: CatalogGeneration,
    pub context_id: ContextId,
    pub caller_id: CallerId,
    pub operation: TypedOperation,
}

impl fmt::Debug for AuthenticatedRequestParts {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedRequestParts")
            .field("protocol_version", &self.protocol_version)
            .field("epoch", &self.epoch)
            .field("direction", &self.direction)
            .field("connection_nonce", &self.connection_nonce)
            .field("sequence", &self.sequence)
            .field("delivery", &self.delivery)
            .field("operation_id", &self.operation_id)
            .field("node_attempt_id", &self.node_attempt_id)
            .field("plan_digest", &self.plan_digest)
            .field("session_id", &self.session_id)
            .field("capability", &self.capability)
            .field("catalog_generation", &self.catalog_generation)
            .field("context_id", &self.context_id)
            .field("caller_id", &self.caller_id)
            .field("operation", &self.operation)
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct AuthenticatedRequest {
    protocol_version: ProtocolVersion,
    epoch: PairingEpoch,
    direction: Direction,
    connection_nonce: ConnectionNonce,
    sequence: Sequence,
    delivery: DeliveryKind,
    operation_id: OperationId,
    node_attempt_id: NodeAttemptId,
    plan_digest: PlanDigest,
    session_id: SessionId,
    capability: CapabilityId,
    catalog_generation: CatalogGeneration,
    context_id: ContextId,
    caller_id: CallerId,
    operation: TypedOperation,
}

impl AuthenticatedRequest {
    pub fn new(parts: AuthenticatedRequestParts) -> Result<Self> {
        let protocol_version = ProtocolVersion::new(parts.protocol_version)?;
        if parts.direction != Direction::AdapterToCompanion {
            return Err(AdapterError::WrongDirection);
        }
        let sequence = Sequence::new(parts.sequence)?;
        if parts.capability.as_str() != parts.operation.kind().required_capability() {
            return Err(AdapterError::CapabilityOperationMismatch);
        }
        Ok(Self {
            protocol_version,
            epoch: parts.epoch,
            direction: parts.direction,
            connection_nonce: parts.connection_nonce,
            sequence,
            delivery: parts.delivery,
            operation_id: parts.operation_id,
            node_attempt_id: parts.node_attempt_id,
            plan_digest: parts.plan_digest,
            session_id: parts.session_id,
            capability: parts.capability,
            catalog_generation: parts.catalog_generation,
            context_id: parts.context_id,
            caller_id: parts.caller_id,
            operation: parts.operation,
        })
    }

    #[must_use]
    pub const fn protocol_version(&self) -> ProtocolVersion {
        self.protocol_version
    }

    #[must_use]
    pub const fn epoch(&self) -> PairingEpoch {
        self.epoch
    }

    #[must_use]
    pub const fn direction(&self) -> Direction {
        self.direction
    }

    #[must_use]
    pub const fn connection_nonce(&self) -> ConnectionNonce {
        self.connection_nonce
    }

    #[must_use]
    pub const fn sequence(&self) -> Sequence {
        self.sequence
    }

    #[must_use]
    pub const fn delivery(&self) -> DeliveryKind {
        self.delivery
    }

    #[must_use]
    pub const fn operation_id(&self) -> OperationId {
        self.operation_id
    }

    #[must_use]
    pub const fn node_attempt_id(&self) -> NodeAttemptId {
        self.node_attempt_id
    }

    #[must_use]
    pub const fn plan_digest(&self) -> PlanDigest {
        self.plan_digest
    }

    #[must_use]
    pub const fn session_id(&self) -> SessionId {
        self.session_id
    }

    #[must_use]
    pub const fn capability(&self) -> &CapabilityId {
        &self.capability
    }

    #[must_use]
    pub const fn catalog_generation(&self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub const fn context_id(&self) -> &ContextId {
        &self.context_id
    }

    #[must_use]
    pub const fn caller_id(&self) -> &CallerId {
        &self.caller_id
    }

    #[must_use]
    pub const fn operation(&self) -> &TypedOperation {
        &self.operation
    }

    #[must_use]
    pub fn authenticated_transcript(&self) -> Vec<u8> {
        let mut output = Vec::with_capacity(384);
        output.extend_from_slice(TRANSCRIPT_DOMAIN);
        output.extend_from_slice(&self.protocol_version.get().to_be_bytes());
        output.push(self.direction.transcript_tag());
        output.push(self.delivery.transcript_tag());
        output.extend_from_slice(self.epoch.as_bytes());
        output.extend_from_slice(self.connection_nonce.as_bytes());
        output.extend_from_slice(&self.sequence.get().to_be_bytes());
        output.extend_from_slice(&self.operation_id.get().to_be_bytes());
        output.extend_from_slice(&self.node_attempt_id.get().to_be_bytes());
        output.extend_from_slice(self.plan_digest.as_bytes());
        output.extend_from_slice(self.session_id.as_bytes());
        append_bounded_string(&mut output, self.capability.as_str());
        output.extend_from_slice(&self.catalog_generation.get().to_be_bytes());
        append_bounded_string(&mut output, self.context_id.as_str());
        append_bounded_string(&mut output, self.caller_id.as_str());
        self.operation.append_transcript(&mut output);
        output
    }

    pub(crate) fn same_operation_binding(&self, other: &Self) -> bool {
        self.protocol_version == other.protocol_version
            && self.epoch == other.epoch
            && self.direction == other.direction
            && self.operation_id == other.operation_id
            && self.node_attempt_id == other.node_attempt_id
            && self.plan_digest == other.plan_digest
            && self.session_id == other.session_id
            && self.capability == other.capability
            && self.catalog_generation == other.catalog_generation
            && self.context_id == other.context_id
            && self.caller_id == other.caller_id
            && self.operation == other.operation
    }

    pub(crate) fn same_graph_binding(&self, other: &Self) -> bool {
        self.protocol_version == other.protocol_version
            && self.epoch == other.epoch
            && self.direction == other.direction
            && self.connection_nonce == other.connection_nonce
            && self.delivery == other.delivery
            && self.operation_id == other.operation_id
            && self.plan_digest == other.plan_digest
            && self.session_id == other.session_id
            && self.catalog_generation == other.catalog_generation
            && self.context_id == other.context_id
            && self.caller_id == other.caller_id
    }
}

impl fmt::Debug for AuthenticatedRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedRequest")
            .field("protocol_version", &self.protocol_version)
            .field("epoch", &self.epoch)
            .field("direction", &self.direction)
            .field("connection_nonce", &self.connection_nonce)
            .field("sequence", &self.sequence)
            .field("delivery", &self.delivery)
            .field("operation_id", &self.operation_id)
            .field("node_attempt_id", &self.node_attempt_id)
            .field("plan_digest", &self.plan_digest)
            .field("session_id", &self.session_id)
            .field("capability", &self.capability)
            .field("catalog_generation", &self.catalog_generation)
            .field("context_id", &self.context_id)
            .field("caller_id", &self.caller_id)
            .field("operation", &self.operation)
            .finish()
    }
}

fn append_bounded_string(output: &mut Vec<u8>, value: &str) {
    let length = u16::try_from(value.len()).expect("constructor bounds strings below u16");
    output.extend_from_slice(&length.to_be_bytes());
    output.extend_from_slice(value.as_bytes());
}

pub struct ChannelGuard {
    epoch: PairingEpoch,
    direction: Direction,
    connection_nonce: ConnectionNonce,
    expected_sequence: Option<Sequence>,
    revoked: bool,
}

impl ChannelGuard {
    pub fn new(
        epoch: PairingEpoch,
        direction: Direction,
        connection_nonce: ConnectionNonce,
        first_sequence: u64,
    ) -> Result<Self> {
        if direction != Direction::AdapterToCompanion {
            return Err(AdapterError::WrongDirection);
        }
        Ok(Self {
            epoch,
            direction,
            connection_nonce,
            expected_sequence: Some(Sequence::new(first_sequence)?),
            revoked: false,
        })
    }

    pub fn accept(&mut self, request: &AuthenticatedRequest) -> Result<()> {
        if self.revoked {
            return Err(AdapterError::ChannelRevoked);
        }
        if request.epoch != self.epoch {
            return Err(AdapterError::WrongEpoch);
        }
        if request.direction != self.direction {
            return Err(AdapterError::WrongDirection);
        }
        if request.connection_nonce != self.connection_nonce {
            return Err(AdapterError::WrongConnection);
        }
        let expected = self
            .expected_sequence
            .ok_or(AdapterError::SequenceExhausted)?;
        if request.sequence != expected {
            return Err(AdapterError::UnexpectedSequence);
        }
        self.expected_sequence = expected
            .get()
            .checked_add(1)
            .map(Sequence::new)
            .transpose()?;
        Ok(())
    }

    pub fn revoke(&mut self) {
        self.revoked = true;
        self.expected_sequence = None;
    }

    #[must_use]
    pub const fn is_revoked(&self) -> bool {
        self.revoked
    }
}

impl fmt::Debug for ChannelGuard {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChannelGuard")
            .field("epoch", &self.epoch)
            .field("direction", &self.direction)
            .field("connection_nonce", &self.connection_nonce)
            .field("has_expected_sequence", &self.expected_sequence.is_some())
            .field("revoked", &self.revoked)
            .finish()
    }
}
