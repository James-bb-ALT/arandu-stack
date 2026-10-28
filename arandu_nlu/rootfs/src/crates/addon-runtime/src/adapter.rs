use std::collections::BTreeMap;
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;

use ha_adapter::{
    AUTHENTICATED_PROTOCOL_VERSION, AuthenticatedRequest, AuthenticatedRequestParts, CallerId,
    CapabilityId, CatalogGeneration, ConnectionNonce, ContextId, DeliveryKind, Direction,
    NodeAttemptId, OperationId, PairingEpoch, PlanDigest, RegistryEntryId, SessionId,
    TypedOperation,
};
use ha_catalog::ExternalEntityId;
use nlu_core::{ComposedPlan, GraphExecutionClass, RequestText, SlotValue};
use noise_channel::ChannelBinding;
use protocol::v2::{self, Outcome, Request, Response};

use crate::{
    CatalogSeed, CompanionExecutionClass, CompanionGraph, CompanionNode, CompanionSubmission,
    Result, RuntimeError, projected_plan_digest,
};

const SERVER_IO_TIMEOUT: Duration = Duration::from_secs(5);
const NODE_TIMEOUT_MILLIS: u32 = 5_000;
const MAX_OPERATION_IDENTITIES_PER_EPOCH: usize = 1_024;
const CORE_ENTITY_PREFIX: &str = "ha_entity:id_";
const SESSION_DOMAIN: &[u8] = b"ptbr-nlu-companion-session-v1\0";
const SUBMISSION_IDENTITY_DOMAIN: &[u8] = b"ptbr-nlu-companion-submission-v1\0";
const WYOMING_SESSION_DOMAIN: &[u8] = b"ptbr-nlu-wyoming-session-v1\0";

#[derive(Clone)]
struct CatalogBinding {
    registry_id: RegistryEntryId,
    external_id: ExternalEntityId,
}

pub struct CatalogBindings {
    generation: u64,
    entities: BTreeMap<Box<str>, CatalogBinding>,
    operation_registry: Mutex<OperationRegistry>,
}

impl CatalogBindings {
    pub fn from_seed(seed: &CatalogSeed) -> Result<Self> {
        let mut entities = BTreeMap::new();
        for entity in seed.entities() {
            let registry_id = RegistryEntryId::new(entity.registry_id())
                .map_err(|_| RuntimeError::PlanMapping)?;
            let external_id = ExternalEntityId::new(entity.external_id())
                .map_err(|_| RuntimeError::PlanMapping)?;
            let core_id = format!("{CORE_ENTITY_PREFIX}{}", entity.registry_id());
            if entities
                .insert(
                    core_id.into_boxed_str(),
                    CatalogBinding {
                        registry_id,
                        external_id,
                    },
                )
                .is_some()
            {
                return Err(RuntimeError::PlanMapping);
            }
        }
        Ok(Self {
            generation: seed.generation(),
            entities,
            operation_registry: Mutex::new(OperationRegistry::new(
                MAX_OPERATION_IDENTITIES_PER_EPOCH,
            )),
        })
    }

    #[must_use]
    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub fn activate_operation_epoch(&self, epoch: [u8; 32]) -> Result<()> {
        let mut registry = self
            .operation_registry
            .lock()
            .map_err(|_| RuntimeError::PlanMapping)?;
        registry.activate_epoch(epoch);
        Ok(())
    }

    fn operation_epoch_is_active(&self, epoch: [u8; 32]) -> Result<bool> {
        Ok(self
            .operation_registry
            .lock()
            .map_err(|_| RuntimeError::PlanMapping)?
            .epoch
            == Some(epoch))
    }

    fn register_operation(
        &self,
        epoch: [u8; 32],
        identity: [u8; 32],
    ) -> Result<RegisteredOperation> {
        self.operation_registry
            .lock()
            .map_err(|_| RuntimeError::PlanMapping)?
            .register(epoch, identity)
    }
}

impl core::fmt::Debug for CatalogBindings {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CatalogBindings")
            .field("generation", &self.generation)
            .field("entity_count", &self.entities.len())
            .field("residential_data", &"redacted")
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RegisteredOperation {
    id: OperationId,
    delivery: DeliveryKind,
}

struct OperationRegistry {
    epoch: Option<[u8; 32]>,
    identities: BTreeMap<[u8; 32], OperationId>,
    next_operation_id: Option<u64>,
    capacity: usize,
}

impl OperationRegistry {
    fn new(capacity: usize) -> Self {
        Self {
            epoch: None,
            identities: BTreeMap::new(),
            next_operation_id: Some(1),
            capacity,
        }
    }

    fn activate_epoch(&mut self, epoch: [u8; 32]) {
        if self.epoch == Some(epoch) {
            return;
        }
        self.epoch = Some(epoch);
        self.identities.clear();
        self.next_operation_id = Some(1);
    }

    fn register(&mut self, epoch: [u8; 32], identity: [u8; 32]) -> Result<RegisteredOperation> {
        match self.epoch {
            None => self.activate_epoch(epoch),
            Some(active) if active != epoch => {
                return Err(RuntimeError::PlanMapping);
            }
            Some(_) => {}
        }
        if let Some(id) = self.identities.get(&identity).copied() {
            return Ok(RegisteredOperation {
                id,
                delivery: DeliveryKind::Retry,
            });
        }
        if self.identities.len() >= self.capacity {
            return Err(RuntimeError::PlanMapping);
        }

        let value = self.next_operation_id.ok_or(RuntimeError::PlanMapping)?;
        let id = OperationId::new(value).map_err(|_| RuntimeError::PlanMapping)?;
        self.next_operation_id = value.checked_add(1);
        if self.identities.insert(identity, id).is_some() {
            return Err(RuntimeError::PlanMapping);
        }
        Ok(RegisteredOperation {
            id,
            delivery: DeliveryKind::Initial,
        })
    }
}

pub fn interpret_submission(
    server_socket: &Path,
    catalog: &CatalogBindings,
    channel_binding: ChannelBinding,
    submission: &CompanionSubmission,
) -> Result<Vec<u8>> {
    if !catalog.operation_epoch_is_active(*channel_binding.epoch().as_bytes())? {
        return Err(RuntimeError::PlanMapping);
    }
    let source = RequestText::new(submission.text().to_owned())
        .map_err(|_| RuntimeError::InvalidSubmission)?;
    let session_id = derive_session_id(submission)?;
    let request = submission_request(submission, &source, session_id)?;
    let response = exchange_request(server_socket, &source, &request)?;
    match response.outcome() {
        Outcome::PolicyAccepted(accepted) => graph_from_plan(
            accepted.plan(),
            catalog,
            channel_binding,
            submission,
            session_id,
        )?
        .encode(),
        _ => encode_typed_outcome(&response),
    }
}

pub fn recognize_wyoming(
    server_socket: &Path,
    catalog: &CatalogBindings,
    text: &str,
) -> Result<Vec<String>> {
    let source = RequestText::new(text.to_owned()).map_err(|_| RuntimeError::InvalidSubmission)?;
    let session_id = domain_digest(WYOMING_SESSION_DOMAIN, &[source.as_bytes()])?;
    let request = Request::Interpret {
        session_id: v2::SessionId::from_bytes(session_id),
        text: source.clone(),
    };
    let response = exchange_request(server_socket, &source, &request)?;
    let Outcome::PolicyAccepted(accepted) = response.outcome() else {
        return Err(RuntimeError::InterpretationRejected);
    };
    let plan = accepted.plan();
    if plan.plan().catalog_generation().get() != catalog.generation
        || plan.execution_class() != GraphExecutionClass::PartialSafe
        || !plan.plan().relations().is_empty()
    {
        return Err(RuntimeError::InterpretationRejected);
    }

    let mut external_ids = Vec::with_capacity(plan.plan().nodes().len());
    for node in plan.plan().nodes() {
        if node.operation().as_str() != "ha:get_state"
            || node.capability().as_str() != "ha:state_query"
        {
            return Err(RuntimeError::InterpretationRejected);
        }
        let entity = exactly_one_entity(node)?;
        if entity.generation().get() != catalog.generation {
            return Err(RuntimeError::PlanMapping);
        }
        let binding = catalog
            .entities
            .get(entity.id().as_str())
            .ok_or(RuntimeError::PlanMapping)?;
        external_ids.push(binding.external_id.as_str().to_owned());
    }
    external_ids.sort();
    if external_ids.is_empty() || external_ids.windows(2).any(|pair| pair[0] == pair[1]) {
        return Err(RuntimeError::InterpretationRejected);
    }
    Ok(external_ids)
}

fn submission_request(
    submission: &CompanionSubmission,
    source: &RequestText,
    session_id: [u8; 32],
) -> Result<Request> {
    let Some(request) = submission.protocol_request() else {
        return Ok(Request::Interpret {
            session_id: v2::SessionId::from_bytes(session_id),
            text: source.clone(),
        });
    };
    let addressed_session = match request {
        Request::Continue { session_id, .. }
        | Request::Confirm { session_id, .. }
        | Request::Cancel { session_id } => *session_id,
        Request::Interpret { .. } | Request::Health => return Err(RuntimeError::InvalidSubmission),
    };
    if addressed_session.as_bytes() != &session_id
        || matches!(request, Request::Confirm { plan, .. } if plan.source() != source)
    {
        return Err(RuntimeError::InvalidSubmission);
    }
    Ok(request.clone())
}

fn exchange_request(
    server_socket: &Path,
    source: &RequestText,
    request: &Request,
) -> Result<Response> {
    let request = v2::encode_request(request).map_err(|_| RuntimeError::ProtocolExchange)?;

    let mut server =
        UnixStream::connect(server_socket).map_err(|_| RuntimeError::ServerExchange)?;
    server
        .set_read_timeout(Some(SERVER_IO_TIMEOUT))
        .and_then(|()| server.set_write_timeout(Some(SERVER_IO_TIMEOUT)))
        .map_err(|_| RuntimeError::ServerExchange)?;
    nlu_server::write_frame(&mut server, &request).map_err(|_| RuntimeError::ServerExchange)?;
    let response = nlu_server::read_frame(&mut server).map_err(|_| RuntimeError::ServerExchange)?;
    let _ = server.shutdown(Shutdown::Both);
    v2::decode_response(&response, source).map_err(|_| RuntimeError::ProtocolExchange)
}

fn encode_typed_outcome(response: &Response) -> Result<Vec<u8>> {
    let encoded_response =
        v2::encode_response(response).map_err(|_| RuntimeError::ProtocolExchange)?;
    let response_value =
        nlu_data::parse_strict_json(&encoded_response, "validated protocol-v2 response")
            .map_err(|_| RuntimeError::ProtocolExchange)?;
    if !response_value.is_object() {
        return Err(RuntimeError::ProtocolExchange);
    }
    let encoded = nlu_data::canonical_json(
        &serde_json::json!({
            "kind": "typed_outcome",
            "response": response_value,
            "version": 1,
        }),
        "typed outcome relay",
    )
    .map_err(|_| RuntimeError::WireEncoding)?;
    if encoded.is_empty() || encoded.len() > crate::MAX_COMPANION_WIRE_BYTES {
        return Err(RuntimeError::WireTooLarge);
    }
    Ok(encoded)
}

fn graph_from_plan(
    plan: &ComposedPlan,
    catalog: &CatalogBindings,
    channel_binding: ChannelBinding,
    submission: &CompanionSubmission,
    session_id: [u8; 32],
) -> Result<CompanionGraph> {
    if plan.plan().catalog_generation().get() != catalog.generation {
        return Err(RuntimeError::PlanMapping);
    }
    let execution_class = match plan.execution_class() {
        GraphExecutionClass::PartialSafe => CompanionExecutionClass::PartialSafe,
        GraphExecutionClass::AtomicOnly => CompanionExecutionClass::AtomicOnly,
        GraphExecutionClass::NonExecutable => return Err(RuntimeError::InterpretationRejected),
    };

    let mut dependencies = BTreeMap::<&str, Vec<Box<str>>>::new();
    for relation in plan.plan().relations() {
        if relation.from().as_str() >= relation.to().as_str() {
            return Err(RuntimeError::PlanMapping);
        }
        dependencies
            .entry(relation.to().as_str())
            .or_default()
            .push(relation.from().as_str().into());
    }

    let mapped = plan
        .plan()
        .nodes()
        .iter()
        .map(|node| {
            let entity = exactly_one_entity(node)?;
            let binding = catalog
                .entities
                .get(entity.id().as_str())
                .ok_or(RuntimeError::PlanMapping)?;
            if entity.generation().get() != catalog.generation {
                return Err(RuntimeError::PlanMapping);
            }
            let operation = map_operation(node.operation().as_str(), binding.registry_id.clone())?;
            if node.capability().as_str() != operation.kind().required_capability() {
                return Err(RuntimeError::PlanMapping);
            }
            Ok(MappedNode {
                id: node.id().as_str().into(),
                capability: node.capability().as_str().into(),
                operation,
                external_id: binding.external_id.clone(),
                depends_on: dependencies.remove(node.id().as_str()).unwrap_or_default(),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    if !dependencies.is_empty() {
        return Err(RuntimeError::PlanMapping);
    }

    let epoch = PairingEpoch::new(*channel_binding.epoch().as_bytes())
        .map_err(|_| RuntimeError::PlanMapping)?;
    let connection_nonce = ConnectionNonce::new(*channel_binding.connection_nonce().as_bytes())
        .map_err(|_| RuntimeError::PlanMapping)?;
    let session_id = SessionId::new(session_id).map_err(|_| RuntimeError::PlanMapping)?;
    let context_id =
        ContextId::new(submission.context_id()).map_err(|_| RuntimeError::PlanMapping)?;
    let caller_id = CallerId::new(submission.caller_id()).map_err(|_| RuntimeError::PlanMapping)?;
    let catalog_generation =
        CatalogGeneration::new(catalog.generation).map_err(|_| RuntimeError::PlanMapping)?;
    let input_digest = digest(submission.text().as_bytes())?;

    let provisional = build_nodes(
        &mapped,
        [0xff; 32],
        epoch,
        connection_nonce,
        OperationId::new(1).map_err(|_| RuntimeError::PlanMapping)?,
        DeliveryKind::Initial,
        session_id,
        catalog_generation,
        &context_id,
        &caller_id,
    )?;
    let plan_digest = projected_plan_digest(execution_class, &provisional)?;
    let identity = canonical_submission_identity(submission)?;
    let operation = catalog.register_operation(*channel_binding.epoch().as_bytes(), identity)?;
    let nodes = build_nodes(
        &mapped,
        plan_digest,
        epoch,
        connection_nonce,
        operation.id,
        operation.delivery,
        session_id,
        catalog_generation,
        &context_id,
        &caller_id,
    )?;
    CompanionGraph::new(execution_class, input_digest, nodes)
}

struct MappedNode {
    id: Box<str>,
    capability: Box<str>,
    operation: TypedOperation,
    external_id: ExternalEntityId,
    depends_on: Vec<Box<str>>,
}

#[allow(clippy::too_many_arguments)]
fn build_nodes(
    mapped: &[MappedNode],
    plan_digest: [u8; 32],
    epoch: PairingEpoch,
    connection_nonce: ConnectionNonce,
    operation_id: OperationId,
    delivery: DeliveryKind,
    session_id: SessionId,
    catalog_generation: CatalogGeneration,
    context_id: &ContextId,
    caller_id: &CallerId,
) -> Result<Vec<CompanionNode>> {
    mapped
        .iter()
        .enumerate()
        .map(|(index, node)| {
            let ordinal = u16::try_from(index + 1).map_err(|_| RuntimeError::PlanMapping)?;
            let sequence = u64::try_from(index + 1).map_err(|_| RuntimeError::PlanMapping)?;
            let request = AuthenticatedRequest::new(AuthenticatedRequestParts {
                protocol_version: AUTHENTICATED_PROTOCOL_VERSION,
                epoch,
                direction: Direction::AdapterToCompanion,
                connection_nonce,
                sequence,
                delivery,
                operation_id,
                node_attempt_id: NodeAttemptId::new(ordinal)
                    .map_err(|_| RuntimeError::PlanMapping)?,
                plan_digest: PlanDigest::new(plan_digest).map_err(|_| RuntimeError::PlanMapping)?,
                session_id,
                capability: CapabilityId::new(&node.capability)
                    .map_err(|_| RuntimeError::PlanMapping)?,
                catalog_generation,
                context_id: context_id.clone(),
                caller_id: caller_id.clone(),
                operation: node.operation.clone(),
            })
            .map_err(|_| RuntimeError::PlanMapping)?;
            CompanionNode::new(
                &node.id,
                request,
                vec![node.external_id.clone()],
                node.depends_on.clone(),
                NODE_TIMEOUT_MILLIS,
            )
        })
        .collect()
}

fn exactly_one_entity(node: &nlu_core::PlanNode) -> Result<&nlu_core::EntityRef> {
    if node.slots().len() != 1 || node.slots()[0].id().as_str() != "ha:entity" {
        return Err(RuntimeError::PlanMapping);
    }
    let SlotValue::Entity(entity) = node.slots()[0].value() else {
        return Err(RuntimeError::PlanMapping);
    };
    Ok(entity)
}

fn map_operation(operation: &str, target: RegistryEntryId) -> Result<TypedOperation> {
    match operation {
        "ha:get_state" => TypedOperation::read_entity_state(target),
        "ha:turn_on" => TypedOperation::turn_on(target),
        "ha:turn_off" => TypedOperation::turn_off(target),
        _ => return Err(RuntimeError::PlanMapping),
    }
    .map_err(|_| RuntimeError::PlanMapping)
}

fn derive_session_id(submission: &CompanionSubmission) -> Result<[u8; 32]> {
    let addressed = submission
        .conversation_id()
        .unwrap_or_else(|| submission.context_id());
    domain_digest(
        SESSION_DOMAIN,
        &[
            addressed.as_bytes(),
            submission.context_id().as_bytes(),
            submission.caller_id().as_bytes(),
        ],
    )
}

fn canonical_submission_identity(submission: &CompanionSubmission) -> Result<[u8; 32]> {
    let canonical = submission.encode()?;
    domain_digest(SUBMISSION_IDENTITY_DOMAIN, &[&canonical])
}

fn domain_digest(domain: &[u8], fields: &[&[u8]]) -> Result<[u8; 32]> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(domain);
    for field in fields {
        let length = u32::try_from(field.len()).map_err(|_| RuntimeError::PlanMapping)?;
        bytes.extend_from_slice(&length.to_be_bytes());
        bytes.extend_from_slice(field);
    }
    digest(&bytes)
}

fn digest(bytes: &[u8]) -> Result<[u8; 32]> {
    let encoded = nlu_data::sha256_hex(bytes).map_err(|_| RuntimeError::PlanMapping)?;
    let mut output = [0_u8; 32];
    for (index, pair) in encoded.as_bytes().as_chunks::<2>().0.iter().enumerate() {
        let high = hex_nibble(pair[0]).ok_or(RuntimeError::PlanMapping)?;
        let low = hex_nibble(pair[1]).ok_or(RuntimeError::PlanMapping)?;
        output[index] = (high << 4) | low;
    }
    Ok(output)
}

const fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::os::unix::net::UnixListener;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::thread;

    use nlu_core::{
        CapabilityId as CoreCapabilityId, CatalogGeneration as CoreCatalogGeneration,
        ClauseSemantics, EvidenceAtom, EvidenceKind, IntentId, NodeId,
        OperationId as CoreOperationId, Plan, PlanNode, Polarity, Slot, SlotId,
    };
    use noise_channel::{
        ConnectionDirection, ConnectionNonce as NoiseConnectionNonce,
        PairingEpoch as NoisePairingEpoch,
    };
    use protocol::{
        ProtocolError,
        v2::{
            CancellationStatus, ConfirmationId, ConfirmationRequired, Diagnostic, DiagnosticCode,
            EntityClarification, Generation, Health, PolicyAccepted, PolicyDenialReason, Readiness,
            Response, RiskClass, SessionId,
        },
    };
    use serde_json::Value;

    use super::*;
    use crate::CatalogEntity;

    const FIXTURE_TECNICA_REGISTRY: &str = "11111111111111111111111111111111";
    const FIXTURE_TECNICA_EXTERNAL: &str = "sensor.fixture_tecnica";
    static NEXT_SOCKET: AtomicU64 = AtomicU64::new(1);

    fn fixture_seed() -> CatalogSeed {
        CatalogSeed::new(
            7,
            vec![
                CatalogEntity::new(
                    FIXTURE_TECNICA_REGISTRY,
                    FIXTURE_TECNICA_EXTERNAL,
                    "FIXTURE_TECNICA_DISPLAY".to_owned(),
                    vec!["FIXTURE_TECNICA_ALIAS".to_owned()],
                    vec!["ha:state_query".to_owned()],
                )
                .expect("FIXTURE_TECNICA catalog entity"),
            ],
        )
        .expect("FIXTURE_TECNICA catalog seed")
    }

    fn fixture_submission() -> CompanionSubmission {
        fixture_submission_with_context("FIXTURE_TECNICA_CONTEXT")
    }

    fn fixture_submission_with_context(context_id: &str) -> CompanionSubmission {
        CompanionSubmission::new(
            "FIXTURE_TECNICA_INPUT".to_owned(),
            context_id.to_owned(),
            "FIXTURE_TECNICA_CALLER".to_owned(),
            Some("FIXTURE_TECNICA_CONVERSATION".to_owned()),
        )
        .expect("FIXTURE_TECNICA submission")
    }

    fn fixture_source() -> RequestText {
        RequestText::new("FIXTURE_TECNICA_INPUT".to_owned()).expect("FIXTURE_TECNICA source")
    }

    fn fixture_plan(entity_id: &str) -> ComposedPlan {
        let source = fixture_source();
        let end = u64::try_from(source.len()).expect("FIXTURE_TECNICA source length");
        let evidence = source.span(0, end).expect("FIXTURE_TECNICA span");
        let node_id = NodeId::new("fixture_tecnica:node").expect("FIXTURE_TECNICA node identity");
        let node = PlanNode::new(
            node_id.clone(),
            CoreCapabilityId::new("ha:state_query").expect("FIXTURE_TECNICA capability"),
            CoreOperationId::new("ha:get_state").expect("FIXTURE_TECNICA operation"),
            vec![Slot::new(
                SlotId::new("ha:entity").expect("FIXTURE_TECNICA slot identity"),
                SlotValue::Entity(nlu_core::EntityRef::new(
                    nlu_core::EntityId::new(entity_id).expect("FIXTURE_TECNICA entity identity"),
                    CoreCatalogGeneration::new(7).expect("FIXTURE_TECNICA entity generation"),
                )),
            )],
            vec![evidence.clone()],
        )
        .expect("FIXTURE_TECNICA node");
        let plan = Plan::new(
            &source,
            CoreCatalogGeneration::new(7).expect("FIXTURE_TECNICA generation"),
            vec![node],
            Vec::new(),
        )
        .expect("FIXTURE_TECNICA plan");
        let clause = ClauseSemantics::new(
            node_id,
            IntentId::new("fixture_tecnica:intent").expect("FIXTURE_TECNICA intent"),
            Polarity::Affirmed,
            vec![
                EvidenceAtom::new(EvidenceKind::Predicate, evidence.clone()),
                EvidenceAtom::new(
                    EvidenceKind::Argument(
                        SlotId::new("ha:entity").expect("FIXTURE_TECNICA argument slot"),
                    ),
                    evidence,
                ),
            ],
        )
        .expect("FIXTURE_TECNICA clause");
        ComposedPlan::new(
            &source,
            plan,
            GraphExecutionClass::PartialSafe,
            vec![clause],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        )
        .expect("FIXTURE_TECNICA composed plan")
    }

    fn fixture_binding(nonce: u8) -> ChannelBinding {
        fixture_binding_for_epoch(7, nonce)
    }

    fn fixture_binding_for_epoch(epoch: u8, nonce: u8) -> ChannelBinding {
        ChannelBinding::new(
            NoisePairingEpoch::new([epoch; 32]).expect("FIXTURE_TECNICA epoch"),
            ConnectionDirection::CompanionInitiates,
            NoiseConnectionNonce::from_bytes([nonce; 32]).expect("FIXTURE_TECNICA nonce"),
        )
    }

    fn fixture_socket() -> std::path::PathBuf {
        let serial = NEXT_SOCKET.fetch_add(1, Ordering::Relaxed);
        std::path::PathBuf::from(format!(
            "/tmp/FIXTURE_TECNICA_adapter_{}_{}.sock",
            std::process::id(),
            serial
        ))
    }

    fn exchange_fixture_response(response: &[u8]) -> Result<Vec<u8>> {
        let socket = fixture_socket();
        let listener = UnixListener::bind(&socket).expect("FIXTURE_TECNICA listener");
        let response = response.to_vec();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("FIXTURE_TECNICA accept");
            let request =
                nlu_server::read_frame(&mut stream).expect("FIXTURE_TECNICA request frame");
            let request = v2::decode_request(&request).expect("FIXTURE_TECNICA protocol request");
            assert!(matches!(request, Request::Interpret { .. }));
            nlu_server::write_frame(&mut stream, &response)
                .expect("FIXTURE_TECNICA response frame");
        });
        let seed = fixture_seed();
        let catalog = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA bindings");
        catalog
            .activate_operation_epoch([7; 32])
            .expect("FIXTURE_TECNICA operation epoch");
        let result =
            interpret_submission(&socket, &catalog, fixture_binding(8), &fixture_submission());
        server.join().expect("FIXTURE_TECNICA server");
        fs::remove_file(socket).expect("FIXTURE_TECNICA cleanup");
        result
    }

    fn assert_typed_outcome(
        encoded: &[u8],
        response: &Response,
        expected_outcome_type: &str,
    ) -> Value {
        let encoded_response =
            v2::encode_response(response).expect("FIXTURE_TECNICA protocol response");
        let response_value =
            nlu_data::parse_strict_json(&encoded_response, "FIXTURE_TECNICA protocol response")
                .expect("FIXTURE_TECNICA response JSON");
        let expected = nlu_data::canonical_json(
            &serde_json::json!({
                "kind": "typed_outcome",
                "response": response_value,
                "version": 1,
            }),
            "FIXTURE_TECNICA typed outcome",
        )
        .expect("FIXTURE_TECNICA canonical typed outcome");
        assert_eq!(encoded, expected);

        let value = nlu_data::parse_strict_json(encoded, "FIXTURE_TECNICA relay response")
            .expect("FIXTURE_TECNICA relay JSON");
        let object = value.as_object().expect("FIXTURE_TECNICA relay object");
        assert_eq!(object.len(), 3);
        assert_eq!(
            object.get("kind").and_then(Value::as_str),
            Some("typed_outcome")
        );
        assert_eq!(object.get("version").and_then(Value::as_u64), Some(1));
        for execution_field in [
            "caller_id",
            "context_id",
            "execution_class",
            "nodes",
            "operation_id",
            "plan_digest",
        ] {
            assert!(
                object.get(execution_field).is_none(),
                "FIXTURE_TECNICA typed outcome cannot be an execution request"
            );
        }
        let outcome = object
            .get("response")
            .and_then(Value::as_object)
            .and_then(|response| response.get("outcome"))
            .cloned()
            .expect("FIXTURE_TECNICA complete response outcome");
        assert_eq!(
            outcome.get("type").and_then(Value::as_str),
            Some(expected_outcome_type)
        );
        outcome
    }

    #[test]
    fn bounded_server_exchange_maps_only_the_accepted_catalog_bound_plan() {
        let socket = fixture_socket();
        let listener = UnixListener::bind(&socket).expect("FIXTURE_TECNICA listener");
        let plan = fixture_plan(&format!("{CORE_ENTITY_PREFIX}{FIXTURE_TECNICA_REGISTRY}"));
        let expected_plan = plan.clone();
        let response = v2::encode_response(&Response::without_diagnostics(
            Outcome::PolicyAccepted(PolicyAccepted::new(plan, RiskClass::ReadOnly)),
        ))
        .expect("FIXTURE_TECNICA response");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("FIXTURE_TECNICA accept");
            let request =
                nlu_server::read_frame(&mut stream).expect("FIXTURE_TECNICA request frame");
            let request = v2::decode_request(&request).expect("FIXTURE_TECNICA protocol request");
            assert!(matches!(request, Request::Interpret { .. }));
            nlu_server::write_frame(&mut stream, &response)
                .expect("FIXTURE_TECNICA response frame");
        });

        let seed = fixture_seed();
        let expected_bindings =
            CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA expected bindings");
        let bindings = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA bindings");
        bindings
            .activate_operation_epoch([7; 32])
            .expect("FIXTURE_TECNICA operation epoch");
        let submission = fixture_submission();
        let session = derive_session_id(&submission).expect("FIXTURE_TECNICA session");
        let expected = graph_from_plan(
            &expected_plan,
            &expected_bindings,
            fixture_binding(8),
            &submission,
            session,
        )
        .expect("FIXTURE_TECNICA expected graph")
        .encode()
        .expect("FIXTURE_TECNICA expected graph wire");
        let encoded = interpret_submission(&socket, &bindings, fixture_binding(8), &submission)
            .expect("FIXTURE_TECNICA interpreted graph");
        assert_eq!(encoded, expected);
        let value: Value = serde_json::from_slice(&encoded).expect("FIXTURE_TECNICA graph JSON");

        assert_eq!(value["catalog_generation"], 7);
        assert_eq!(value["execution_class"], "partial_safe");
        assert_eq!(value["nodes"][0]["operation"], "ha:get_state");
        assert_eq!(
            value["nodes"][0]["selector"]["registry_entry_ids"][0],
            FIXTURE_TECNICA_REGISTRY
        );
        assert_eq!(
            value["nodes"][0]["bound_targets"][0],
            FIXTURE_TECNICA_EXTERNAL
        );
        assert_eq!(value["context_id"], "FIXTURE_TECNICA_CONTEXT");
        assert_eq!(value["caller_id"], "FIXTURE_TECNICA_CALLER");
        assert_eq!(
            value["input_digest"],
            nlu_data::sha256_hex(b"FIXTURE_TECNICA_INPUT").expect("FIXTURE_TECNICA digest")
        );

        server.join().expect("FIXTURE_TECNICA server");
        fs::remove_file(socket).expect("FIXTURE_TECNICA cleanup");
    }

    #[test]
    fn retry_after_intervening_submissions_keeps_operation_identity() {
        let seed = fixture_seed();
        let catalog = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA bindings");
        let plan = fixture_plan(&format!("{CORE_ENTITY_PREFIX}{FIXTURE_TECNICA_REGISTRY}"));
        let first_submission = fixture_submission();
        let first_session =
            derive_session_id(&first_submission).expect("FIXTURE_TECNICA first session");
        let first = graph_from_plan(
            &plan,
            &catalog,
            fixture_binding(8),
            &first_submission,
            first_session,
        )
        .expect("FIXTURE_TECNICA first graph")
        .encode()
        .expect("FIXTURE_TECNICA first wire");

        for (ordinal, context) in [
            "FIXTURE_TECNICA_CONTEXT_INTERVENING_A",
            "FIXTURE_TECNICA_CONTEXT_INTERVENING_B",
        ]
        .into_iter()
        .enumerate()
        {
            let submission = fixture_submission_with_context(context);
            let session =
                derive_session_id(&submission).expect("FIXTURE_TECNICA intervening session");
            let encoded = graph_from_plan(
                &plan,
                &catalog,
                fixture_binding(u8::try_from(ordinal + 9).expect("FIXTURE_TECNICA nonce")),
                &submission,
                session,
            )
            .expect("FIXTURE_TECNICA intervening graph")
            .encode()
            .expect("FIXTURE_TECNICA intervening wire");
            let value: Value =
                serde_json::from_slice(&encoded).expect("FIXTURE_TECNICA intervening JSON");
            assert_eq!(value["delivery"].as_str(), Some("initial"));
            assert_eq!(
                value["operation_id"].as_str(),
                Some(format!("{:032x}", ordinal + 2).as_str())
            );
        }

        let repeated = graph_from_plan(
            &plan,
            &catalog,
            fixture_binding(11),
            &first_submission,
            first_session,
        )
        .expect("FIXTURE_TECNICA repeated graph")
        .encode()
        .expect("FIXTURE_TECNICA repeated wire");
        let first: Value = serde_json::from_slice(&first).expect("FIXTURE_TECNICA first JSON");
        let repeated: Value =
            serde_json::from_slice(&repeated).expect("FIXTURE_TECNICA repeated JSON");

        assert_eq!(first["operation_id"], repeated["operation_id"]);
        assert_eq!(
            first["operation_id"].as_str(),
            Some("00000000000000000000000000000001")
        );
        assert_eq!(first["delivery"].as_str(), Some("initial"));
        assert_eq!(repeated["delivery"].as_str(), Some("retry"));
        assert_ne!(first["connection_nonce"], repeated["connection_nonce"]);
    }

    #[test]
    fn operation_registry_capacity_never_evicts_existing_identities() {
        let mut registry = OperationRegistry::new(2);
        let epoch = [7; 32];
        let first = registry
            .register(epoch, [1; 32])
            .expect("FIXTURE_TECNICA first registration");
        let second = registry
            .register(epoch, [2; 32])
            .expect("FIXTURE_TECNICA second registration");

        assert_eq!(first.id.get(), 1);
        assert_eq!(first.delivery, DeliveryKind::Initial);
        assert_eq!(second.id.get(), 2);
        assert_eq!(second.delivery, DeliveryKind::Initial);
        assert_eq!(
            registry
                .register(epoch, [3; 32])
                .expect_err("FIXTURE_TECNICA capacity"),
            RuntimeError::PlanMapping
        );
        assert_eq!(
            registry
                .register(epoch, [1; 32])
                .expect("FIXTURE_TECNICA retained first"),
            RegisteredOperation {
                id: first.id,
                delivery: DeliveryKind::Retry,
            }
        );
        assert_eq!(
            registry
                .register(epoch, [3; 32])
                .expect_err("FIXTURE_TECNICA capacity remains exhausted"),
            RuntimeError::PlanMapping
        );
    }

    #[test]
    fn operation_registry_exhaustion_never_wraps_to_zero() {
        let mut registry = OperationRegistry::new(2);
        registry.activate_epoch([7; 32]);
        registry.next_operation_id = Some(u64::MAX);
        let last = registry
            .register([7; 32], [1; 32])
            .expect("FIXTURE_TECNICA last representable operation");

        assert_eq!(last.id.get(), u64::MAX);
        assert_eq!(last.delivery, DeliveryKind::Initial);
        assert_eq!(
            registry
                .register([7; 32], [2; 32])
                .expect_err("FIXTURE_TECNICA exhausted operation sequence"),
            RuntimeError::PlanMapping
        );
        assert_eq!(
            registry
                .register([7; 32], [1; 32])
                .expect("FIXTURE_TECNICA retained last operation"),
            RegisteredOperation {
                id: last.id,
                delivery: DeliveryKind::Retry,
            }
        );
    }

    #[test]
    fn pairing_epoch_rotation_erases_history_and_restarts_sequence() {
        let seed = fixture_seed();
        let catalog = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA bindings");
        let plan = fixture_plan(&format!("{CORE_ENTITY_PREFIX}{FIXTURE_TECNICA_REGISTRY}"));
        let submission = fixture_submission();
        let session = derive_session_id(&submission).expect("FIXTURE_TECNICA session");
        let first = graph_from_plan(
            &plan,
            &catalog,
            fixture_binding_for_epoch(7, 8),
            &submission,
            session,
        )
        .expect("FIXTURE_TECNICA first graph")
        .encode()
        .expect("FIXTURE_TECNICA first wire");
        let intervening_submission =
            fixture_submission_with_context("FIXTURE_TECNICA_CONTEXT_INTERVENING");
        let intervening_session = derive_session_id(&intervening_submission)
            .expect("FIXTURE_TECNICA intervening session");
        let intervening = graph_from_plan(
            &plan,
            &catalog,
            fixture_binding_for_epoch(7, 9),
            &intervening_submission,
            intervening_session,
        )
        .expect("FIXTURE_TECNICA intervening graph")
        .encode()
        .expect("FIXTURE_TECNICA intervening wire");
        catalog
            .activate_operation_epoch([8; 32])
            .expect("FIXTURE_TECNICA rotate operation epoch");
        let rotated = graph_from_plan(
            &plan,
            &catalog,
            fixture_binding_for_epoch(8, 10),
            &submission,
            session,
        )
        .expect("FIXTURE_TECNICA rotated graph")
        .encode()
        .expect("FIXTURE_TECNICA rotated wire");
        let repeated = graph_from_plan(
            &plan,
            &catalog,
            fixture_binding_for_epoch(8, 11),
            &submission,
            session,
        )
        .expect("FIXTURE_TECNICA repeated graph")
        .encode()
        .expect("FIXTURE_TECNICA repeated wire");
        let first: Value = serde_json::from_slice(&first).expect("FIXTURE_TECNICA first JSON");
        let intervening: Value =
            serde_json::from_slice(&intervening).expect("FIXTURE_TECNICA intervening JSON");
        let rotated: Value =
            serde_json::from_slice(&rotated).expect("FIXTURE_TECNICA rotated JSON");
        let repeated: Value =
            serde_json::from_slice(&repeated).expect("FIXTURE_TECNICA repeated JSON");

        assert_eq!(
            first["operation_id"].as_str(),
            Some("00000000000000000000000000000001")
        );
        assert_eq!(
            intervening["operation_id"].as_str(),
            Some("00000000000000000000000000000002")
        );
        assert_eq!(
            rotated["operation_id"].as_str(),
            Some("00000000000000000000000000000001")
        );
        assert_eq!(rotated["delivery"].as_str(), Some("initial"));
        assert_eq!(rotated["operation_id"], repeated["operation_id"]);
        assert_eq!(repeated["delivery"].as_str(), Some("retry"));
        assert_ne!(rotated["connection_nonce"], repeated["connection_nonce"]);
    }

    #[test]
    fn delayed_old_epoch_registration_cannot_reactivate_retired_history() {
        let mut registry = OperationRegistry::new(2);
        let old_epoch = [7; 32];
        let new_epoch = [8; 32];
        let old = registry
            .register(old_epoch, [1; 32])
            .expect("FIXTURE_TECNICA old operation");
        registry.activate_epoch(new_epoch);

        assert_eq!(
            registry
                .register(old_epoch, [1; 32])
                .expect_err("FIXTURE_TECNICA retired epoch"),
            RuntimeError::PlanMapping
        );
        let current = registry
            .register(new_epoch, [2; 32])
            .expect("FIXTURE_TECNICA current operation");
        assert_eq!(old.id.get(), 1);
        assert_eq!(current.id.get(), 1);
        assert_eq!(current.delivery, DeliveryKind::Initial);
    }

    #[test]
    fn wyoming_projection_accepts_only_independent_catalog_bound_state_query() {
        let socket = fixture_socket();
        let listener = UnixListener::bind(&socket).expect("FIXTURE_TECNICA listener");
        let plan = fixture_plan(&format!("{CORE_ENTITY_PREFIX}{FIXTURE_TECNICA_REGISTRY}"));
        let response = v2::encode_response(&Response::without_diagnostics(
            Outcome::PolicyAccepted(PolicyAccepted::new(plan, RiskClass::ReadOnly)),
        ))
        .expect("FIXTURE_TECNICA response");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("FIXTURE_TECNICA accept");
            let _ = nlu_server::read_frame(&mut stream).expect("FIXTURE_TECNICA request frame");
            nlu_server::write_frame(&mut stream, &response)
                .expect("FIXTURE_TECNICA response frame");
        });
        let seed = fixture_seed();
        let catalog = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA bindings");

        assert_eq!(
            recognize_wyoming(&socket, &catalog, "FIXTURE_TECNICA_INPUT")
                .expect("FIXTURE_TECNICA recognition"),
            vec![FIXTURE_TECNICA_EXTERNAL.to_owned()]
        );
        server.join().expect("FIXTURE_TECNICA server");
        fs::remove_file(socket).expect("FIXTURE_TECNICA cleanup");
    }

    #[test]
    fn unknown_catalog_entity_fails_closed() {
        let seed = fixture_seed();
        let catalog = CatalogBindings::from_seed(&seed).expect("FIXTURE_TECNICA bindings");
        let submission = fixture_submission();
        let session = derive_session_id(&submission).expect("FIXTURE_TECNICA session");
        let unknown = fixture_plan("ha_entity:id_22222222222222222222222222222222");

        assert_eq!(
            graph_from_plan(&unknown, &catalog, fixture_binding(8), &submission, session,)
                .expect_err("FIXTURE_TECNICA unknown entity"),
            RuntimeError::PlanMapping
        );
    }

    #[test]
    fn entity_clarification_continuation_is_relayed_without_execution_graph() {
        let response = Response::new(
            Outcome::EntityClarification(
                EntityClarification::new(
                    SessionId::from_bytes([3; v2::SESSION_ID_BYTES]),
                    vec![
                        nlu_core::EntityRef::new(
                            nlu_core::EntityId::new(
                                "ha_entity:id_11111111111111111111111111111111",
                            )
                            .expect("FIXTURE_TECNICA first entity"),
                            CoreCatalogGeneration::new(7)
                                .expect("FIXTURE_TECNICA first generation"),
                        ),
                        nlu_core::EntityRef::new(
                            nlu_core::EntityId::new(
                                "ha_entity:id_22222222222222222222222222222222",
                            )
                            .expect("FIXTURE_TECNICA second entity"),
                            CoreCatalogGeneration::new(7)
                                .expect("FIXTURE_TECNICA second generation"),
                        ),
                    ],
                )
                .expect("FIXTURE_TECNICA clarification"),
            ),
            vec![Diagnostic::new(DiagnosticCode::Ambiguous, None)],
        )
        .expect("FIXTURE_TECNICA response");
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed clarification");
        let outcome = assert_typed_outcome(&encoded, &response, "entity_clarification");

        assert_eq!(
            outcome.get("continuation").and_then(Value::as_bool),
            Some(true)
        );
        assert!(outcome.get("session_id").is_some());
        assert_eq!(
            outcome
                .get("referents")
                .and_then(Value::as_array)
                .map(Vec::len),
            Some(2)
        );
    }

    #[test]
    fn confirmation_required_continuation_is_relayed_without_execution_graph() {
        let response = Response::new(
            Outcome::ConfirmationRequired(ConfirmationRequired::new(
                SessionId::from_bytes([4; v2::SESSION_ID_BYTES]),
                ConfirmationId::new(9).expect("FIXTURE_TECNICA confirmation identity"),
                RiskClass::Sensitive,
                fixture_plan(&format!("{CORE_ENTITY_PREFIX}{FIXTURE_TECNICA_REGISTRY}")),
            )),
            vec![Diagnostic::new(DiagnosticCode::PolicyDenied, None)],
        )
        .expect("FIXTURE_TECNICA response");
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed confirmation");
        let outcome = assert_typed_outcome(&encoded, &response, "confirmation_required");

        assert!(outcome.get("session_id").is_some());
        assert_eq!(
            outcome.get("confirmation_id").and_then(Value::as_u64),
            Some(9)
        );
        assert!(outcome.get("plan").is_some());
    }

    #[test]
    fn abstention_is_relayed_as_noncontinuation_without_execution_graph() {
        let response = Response::new(
            Outcome::Abstention(nlu_core::AbstentionReason::Unsupported),
            vec![Diagnostic::new(DiagnosticCode::Unsupported, None)],
        )
        .expect("FIXTURE_TECNICA response");
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed abstention");
        let outcome = assert_typed_outcome(&encoded, &response, "abstention");

        assert!(outcome.get("continuation").is_none());
        assert!(outcome.get("session_id").is_none());
        assert_eq!(
            outcome.get("reason").and_then(Value::as_str),
            Some("unsupported")
        );
    }

    #[test]
    fn complete_plan_is_relayed_without_executable_graph_fields() {
        let response = Response::without_diagnostics(Outcome::CompletePlan(fixture_plan(
            &format!("{CORE_ENTITY_PREFIX}{FIXTURE_TECNICA_REGISTRY}"),
        )));
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed complete plan");

        assert_typed_outcome(&encoded, &response, "complete_plan");
    }

    #[test]
    fn policy_denial_is_relayed_without_executable_graph_fields() {
        let response = Response::new(
            Outcome::PolicyDenial(PolicyDenialReason::ExplicitDeny),
            vec![Diagnostic::new(DiagnosticCode::PolicyDenied, None)],
        )
        .expect("FIXTURE_TECNICA response");
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed policy denial");

        assert_typed_outcome(&encoded, &response, "policy_denial");
    }

    #[test]
    fn cancellation_is_relayed_without_executable_graph_fields() {
        let response =
            Response::without_diagnostics(Outcome::Cancellation(CancellationStatus::Cancelled));
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed cancellation");

        assert_typed_outcome(&encoded, &response, "cancellation");
    }

    #[test]
    fn health_is_relayed_without_executable_graph_fields() {
        let response = Response::without_diagnostics(Outcome::Health(Health::new(
            Readiness::Ready,
            Some(Generation::new(7).expect("FIXTURE_TECNICA catalog generation")),
            Some(Generation::new(8).expect("FIXTURE_TECNICA policy generation")),
            Some(Generation::new(9).expect("FIXTURE_TECNICA configuration generation")),
        )));
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded = exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed health");

        assert_typed_outcome(&encoded, &response, "health");
    }

    #[test]
    fn protocol_error_is_relayed_without_executable_graph_fields() {
        let response =
            Response::without_diagnostics(Outcome::ProtocolError(ProtocolError::InputTooLarge));
        let protocol = v2::encode_response(&response).expect("FIXTURE_TECNICA protocol response");
        let encoded =
            exchange_fixture_response(&protocol).expect("FIXTURE_TECNICA typed protocol error");

        assert_typed_outcome(&encoded, &response, "protocol_error");
    }

    #[test]
    fn malformed_and_unsupported_protocol_responses_fail_closed() {
        for response in [
            br#"{"diagnostics":[],"outcome":"#.as_slice(),
            br#"{"diagnostics":[],"outcome":{"reason":"unsupported","type":"abstention"},"version":3}"#
                .as_slice(),
        ] {
            assert_eq!(
                exchange_fixture_response(response)
                    .expect_err("FIXTURE_TECNICA invalid protocol response"),
                RuntimeError::ProtocolExchange
            );
        }
    }
}
