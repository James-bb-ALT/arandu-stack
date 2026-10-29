use std::collections::BTreeSet;

use ha_adapter::{
    AUTHENTICATED_PROTOCOL_VERSION, AuthenticatedRequest, DeliveryKind, Direction, OperationKind,
};
use ha_catalog::ExternalEntityId;
use serde_json::{Value, json};

use crate::{Result, RuntimeError};

pub const MAX_COMPANION_NODES: usize = 64;
pub const MAX_COMPANION_TIMEOUT_MILLIS: u32 = 5_000;
pub const MAX_COMPANION_WIRE_BYTES: usize = 65_431;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CompanionExecutionClass {
    PartialSafe,
    AtomicOnly,
}

impl CompanionExecutionClass {
    const fn wire_name(self) -> &'static str {
        match self {
            Self::PartialSafe => "partial_safe",
            Self::AtomicOnly => "atomic_only",
        }
    }
}

#[derive(Clone)]
pub struct CompanionNode {
    node_id: Box<str>,
    request: AuthenticatedRequest,
    bound_targets: Vec<ExternalEntityId>,
    depends_on: Vec<Box<str>>,
    timeout_millis: u32,
}

impl CompanionNode {
    pub fn new(
        node_id: &str,
        request: AuthenticatedRequest,
        mut bound_targets: Vec<ExternalEntityId>,
        mut depends_on: Vec<Box<str>>,
        timeout_millis: u32,
    ) -> Result<Self> {
        if !valid_stable_identifier(node_id) {
            return Err(RuntimeError::InvalidNodeId);
        }
        if !(1..=MAX_COMPANION_TIMEOUT_MILLIS).contains(&timeout_millis) {
            return Err(RuntimeError::InvalidTimeout);
        }
        if bound_targets.len() != request.operation().targets().len() {
            return Err(RuntimeError::TargetBindingMismatch);
        }
        bound_targets.sort_by(|left, right| left.as_str().cmp(right.as_str()));
        if bound_targets
            .windows(2)
            .any(|pair| pair[0].as_str() == pair[1].as_str())
        {
            return Err(RuntimeError::TargetBindingMismatch);
        }
        depends_on.sort();
        if depends_on
            .iter()
            .any(|dependency| !valid_stable_identifier(dependency))
            || depends_on.windows(2).any(|pair| pair[0] == pair[1])
        {
            return Err(RuntimeError::InvalidDependency);
        }
        operation_name(request.operation().kind())?;
        Ok(Self {
            node_id: node_id.into(),
            request,
            bound_targets,
            depends_on,
            timeout_millis,
        })
    }

    fn projection(&self) -> Result<Value> {
        let operation = self.request.operation();
        let registry_entry_ids = operation
            .targets()
            .as_slice()
            .iter()
            .map(|target| Value::String(target.as_str().to_owned()))
            .collect::<Vec<_>>();
        let bound_targets = self
            .bound_targets
            .iter()
            .map(|target| Value::String(target.as_str().to_owned()))
            .collect::<Vec<_>>();
        let depends_on = self
            .depends_on
            .iter()
            .map(|dependency| Value::String(dependency.to_string()))
            .collect::<Vec<_>>();
        Ok(json!({
            "bound_targets": bound_targets,
            "capability": self.request.capability().as_str(),
            "depends_on": depends_on,
            "node_attempt_id": fixed_hex_u16(self.request.node_attempt_id().get()),
            "node_id": self.node_id.as_ref(),
            "operation": operation_name(operation.kind())?,
            "selector": {
                "kind": "registry_entries",
                "registry_entry_ids": registry_entry_ids,
            },
            "timeout_ms": self.timeout_millis,
        }))
    }
}

impl core::fmt::Debug for CompanionNode {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CompanionNode")
            .field("target_count", &self.bound_targets.len())
            .field("dependency_count", &self.depends_on.len())
            .field("timeout_millis", &self.timeout_millis)
            .field("content", &"redacted")
            .finish()
    }
}

pub struct CompanionGraph {
    execution_class: CompanionExecutionClass,
    input_digest: [u8; 32],
    nodes: Vec<CompanionNode>,
}

impl CompanionGraph {
    pub fn new(
        execution_class: CompanionExecutionClass,
        input_digest: [u8; 32],
        mut nodes: Vec<CompanionNode>,
    ) -> Result<Self> {
        if nodes.is_empty() {
            return Err(RuntimeError::EmptyGraph);
        }
        if nodes.len() > MAX_COMPANION_NODES {
            return Err(RuntimeError::TooManyNodes);
        }
        nodes.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        if nodes
            .windows(2)
            .any(|pair| pair[0].node_id == pair[1].node_id)
        {
            return Err(RuntimeError::DuplicateNodeId);
        }
        validate_bindings(&nodes)?;
        validate_dependencies(&nodes)?;
        validate_execution_class(execution_class, &nodes)?;
        let graph = Self {
            execution_class,
            input_digest,
            nodes,
        };
        graph.require_plan_digest()?;
        Ok(graph)
    }

    pub fn plan_digest(&self) -> Result<[u8; 32]> {
        projected_plan_digest(self.execution_class, &self.nodes)
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.require_plan_digest()?;
        let first = &self.nodes[0].request;
        let plan_digest = self.plan_digest()?;
        let nodes = self
            .nodes
            .iter()
            .map(CompanionNode::projection)
            .collect::<Result<Vec<_>>>()?;
        let document = json!({
            "caller_id": first.caller_id().as_str(),
            "catalog_generation": first.catalog_generation().get(),
            "connection_nonce": hex(first.connection_nonce().as_bytes()),
            "context_id": first.context_id().as_str(),
            "delivery": delivery_name(first.delivery()),
            "direction": "adapter_to_companion",
            "epoch": hex(first.epoch().as_bytes()),
            "execution_class": self.execution_class.wire_name(),
            "input_digest": hex(&self.input_digest),
            "nodes": nodes,
            "operation_id": fixed_hex_u64(first.operation_id().get()),
            "plan_digest": hex(&plan_digest),
            "sequence": first.sequence().get(),
            "session_id": hex(first.session_id().as_bytes()),
            "version": AUTHENTICATED_PROTOCOL_VERSION,
        });
        let encoded = nlu_data::canonical_json(&document, "companion request")
            .map_err(|_| RuntimeError::WireEncoding)?;
        if encoded.is_empty() || encoded.len() > MAX_COMPANION_WIRE_BYTES {
            return Err(RuntimeError::WireTooLarge);
        }
        Ok(encoded)
    }

    fn require_plan_digest(&self) -> Result<()> {
        let expected = self.plan_digest()?;
        if self
            .nodes
            .iter()
            .any(|node| node.request.plan_digest().as_bytes() != &expected)
        {
            return Err(RuntimeError::PlanDigestMismatch);
        }
        Ok(())
    }
}

pub fn projected_plan_digest(
    execution_class: CompanionExecutionClass,
    nodes: &[CompanionNode],
) -> Result<[u8; 32]> {
    if nodes.is_empty() {
        return Err(RuntimeError::EmptyGraph);
    }
    if nodes.len() > MAX_COMPANION_NODES {
        return Err(RuntimeError::TooManyNodes);
    }
    let mut ordered = nodes.to_vec();
    ordered.sort_by(|left, right| left.node_id.cmp(&right.node_id));
    if ordered
        .windows(2)
        .any(|pair| pair[0].node_id == pair[1].node_id)
    {
        return Err(RuntimeError::DuplicateNodeId);
    }
    validate_bindings(&ordered)?;
    validate_dependencies(&ordered)?;
    validate_execution_class(execution_class, &ordered)?;
    let first = &ordered[0].request;
    let node_values = ordered
        .iter()
        .map(CompanionNode::projection)
        .collect::<Result<Vec<_>>>()?;
    let projection = json!({
        "catalog_generation": first.catalog_generation().get(),
        "execution_class": execution_class.wire_name(),
        "nodes": node_values,
    });
    let canonical = nlu_data::canonical_json(&projection, "companion plan")
        .map_err(|_| RuntimeError::WireEncoding)?;
    sha256_bytes(&canonical)
}

impl core::fmt::Debug for CompanionGraph {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("CompanionGraph")
            .field("execution_class", &self.execution_class)
            .field("node_count", &self.nodes.len())
            .field("content", &"redacted")
            .finish()
    }
}

fn validate_bindings(nodes: &[CompanionNode]) -> Result<()> {
    let first = &nodes[0].request;
    let mut attempts = BTreeSet::new();
    for (index, node) in nodes.iter().enumerate() {
        let request = &node.request;
        if request.protocol_version() != first.protocol_version()
            || request.protocol_version().get() != AUTHENTICATED_PROTOCOL_VERSION
            || request.epoch() != first.epoch()
            || request.direction() != Direction::AdapterToCompanion
            || request.direction() != first.direction()
            || request.connection_nonce() != first.connection_nonce()
            || request.delivery() != first.delivery()
            || request.operation_id() != first.operation_id()
            || request.plan_digest() != first.plan_digest()
            || request.session_id() != first.session_id()
            || request.catalog_generation() != first.catalog_generation()
            || request.context_id() != first.context_id()
            || request.caller_id() != first.caller_id()
        {
            return Err(RuntimeError::GraphBindingMismatch);
        }
        if !attempts.insert(request.node_attempt_id()) {
            return Err(RuntimeError::DuplicateNodeAttempt);
        }
        let expected_sequence = first
            .sequence()
            .get()
            .checked_add(index as u64)
            .ok_or(RuntimeError::SequenceMismatch)?;
        if request.sequence().get() != expected_sequence
            || request.sequence().get() > i64::MAX as u64
        {
            return Err(RuntimeError::SequenceMismatch);
        }
    }
    Ok(())
}

fn validate_dependencies(nodes: &[CompanionNode]) -> Result<()> {
    let mut preceding = BTreeSet::<&str>::new();
    for node in nodes {
        if node.node_id.as_ref() == node.depends_on.first().map(Box::as_ref).unwrap_or("")
            || node
                .depends_on
                .iter()
                .any(|dependency| !preceding.contains(dependency.as_ref()))
        {
            return Err(RuntimeError::InvalidDependency);
        }
        preceding.insert(node.node_id.as_ref());
    }
    Ok(())
}

fn validate_execution_class(
    execution_class: CompanionExecutionClass,
    nodes: &[CompanionNode],
) -> Result<()> {
    if execution_class == CompanionExecutionClass::AtomicOnly
        && (nodes.len() < 2
            || nodes.iter().any(|node| {
                node.request.operation().kind() != OperationKind::ReadEntityState
                    || !node.depends_on.is_empty()
            }))
    {
        return Err(RuntimeError::InvalidExecutionClass);
    }
    Ok(())
}

fn operation_name(kind: OperationKind) -> Result<&'static str> {
    match kind {
        OperationKind::ReadEntityState => Ok("ha:get_state"),
        OperationKind::TurnOn => Ok("ha:turn_on"),
        OperationKind::TurnOff => Ok("ha:turn_off"),
        OperationKind::ReadBundledSnapshot | OperationKind::SetCoverPosition => {
            Err(RuntimeError::UnsupportedOperation)
        }
    }
}

const fn delivery_name(delivery: DeliveryKind) -> &'static str {
    match delivery {
        DeliveryKind::Initial => "initial",
        DeliveryKind::Retry => "retry",
    }
}

fn valid_stable_identifier(value: &str) -> bool {
    let mut parts = value.split(':');
    let Some(namespace) = parts.next() else {
        return false;
    };
    let Some(local) = parts.next() else {
        return false;
    };
    parts.next().is_none() && valid_component(namespace) && valid_component(local)
}

fn valid_component(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes[0].is_ascii_lowercase()
        && bytes
            .last()
            .is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        && bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-' || *byte == b'_'
        })
}

fn fixed_hex_u64(value: u64) -> String {
    format!("{value:032x}")
}

fn fixed_hex_u16(value: u16) -> String {
    format!("{value:032x}")
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(char::from(DIGITS[usize::from(byte >> 4)]));
        output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
    output
}

fn sha256_bytes(bytes: &[u8]) -> Result<[u8; 32]> {
    let digest = nlu_data::sha256_hex(bytes).map_err(|_| RuntimeError::WireEncoding)?;
    let mut output = [0_u8; 32];
    for (index, pair) in digest.as_bytes().as_chunks::<2>().0.iter().enumerate() {
        let high = hex_nibble(pair[0]).ok_or(RuntimeError::WireEncoding)?;
        let low = hex_nibble(pair[1]).ok_or(RuntimeError::WireEncoding)?;
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
