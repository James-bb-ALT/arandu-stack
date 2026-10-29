use core::fmt;

use crate::{
    AdapterError, MAX_OPERATION_TARGETS, NodeOrdinal, OperationKind, Result, TargetSet,
    TypedOperation,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TargetScope {
    ExactEntity,
    Expanding,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WyomingSafetyAssessment {
    reviewed_read_only: bool,
    non_sensitive: bool,
    non_user_scoped: bool,
    timeout_safe: bool,
    partial_success_safe: bool,
}

impl WyomingSafetyAssessment {
    #[must_use]
    pub const fn new(
        reviewed_read_only: bool,
        non_sensitive: bool,
        non_user_scoped: bool,
        timeout_safe: bool,
        partial_success_safe: bool,
    ) -> Self {
        Self {
            reviewed_read_only,
            non_sensitive,
            non_user_scoped,
            timeout_safe,
            partial_success_safe,
        }
    }

    #[must_use]
    pub const fn reviewed() -> Self {
        Self::new(true, true, true, true, true)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WyomingSafetyProof {
    _private: (),
}

impl WyomingSafetyProof {
    pub fn new(assessment: WyomingSafetyAssessment) -> Result<Self> {
        if !assessment.reviewed_read_only
            || !assessment.non_sensitive
            || !assessment.non_user_scoped
            || !assessment.timeout_safe
            || !assessment.partial_success_safe
        {
            return Err(AdapterError::InvalidSafetyProof);
        }
        Ok(Self { _private: () })
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct RouteNode {
    ordinal: NodeOrdinal,
    operation: TypedOperation,
    target_scope: TargetScope,
    wyoming_safety: Option<WyomingSafetyProof>,
    caller_authorization_required: bool,
    sensitive: bool,
}

impl RouteNode {
    #[must_use]
    pub const fn new(
        ordinal: NodeOrdinal,
        operation: TypedOperation,
        target_scope: TargetScope,
        wyoming_safety: Option<WyomingSafetyProof>,
        caller_authorization_required: bool,
        sensitive: bool,
    ) -> Self {
        Self {
            ordinal,
            operation,
            target_scope,
            wyoming_safety,
            caller_authorization_required,
            sensitive,
        }
    }

    #[must_use]
    pub const fn ordinal(&self) -> NodeOrdinal {
        self.ordinal
    }

    #[must_use]
    pub const fn operation(&self) -> &TypedOperation {
        &self.operation
    }
}

impl fmt::Debug for RouteNode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RouteNode")
            .field("ordinal", &self.ordinal)
            .field("operation", &self.operation)
            .field("target_scope", &self.target_scope)
            .field("has_wyoming_safety", &self.wyoming_safety.is_some())
            .field(
                "caller_authorization_required",
                &self.caller_authorization_required,
            )
            .field("sensitive", &self.sensitive)
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GraphTopology {
    Single,
    UnorderedIndependent,
    Ordered,
    Dependent,
    Conflicting,
    Contradictory,
    AtomicRequired,
}

#[derive(Clone, Eq, PartialEq)]
pub struct RoutePlan {
    nodes: Vec<RouteNode>,
    topology: GraphTopology,
    partial_completion_safe: bool,
    full_clarification_required: bool,
    continuation: bool,
    companion_enabled: bool,
    atomic_mapping: Option<TypedOperation>,
}

impl RoutePlan {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        mut nodes: Vec<RouteNode>,
        topology: GraphTopology,
        partial_completion_safe: bool,
        full_clarification_required: bool,
        continuation: bool,
        companion_enabled: bool,
        atomic_mapping: Option<TypedOperation>,
    ) -> Result<Self> {
        if nodes.is_empty() {
            return Err(AdapterError::EmptySchedule);
        }
        if nodes.len() > MAX_OPERATION_TARGETS {
            return Err(AdapterError::TooManyScheduleNodes);
        }
        nodes.sort_by_key(RouteNode::ordinal);
        if nodes
            .windows(2)
            .any(|pair| pair[0].ordinal == pair[1].ordinal)
        {
            return Err(AdapterError::DuplicateNodeAttempt);
        }
        match topology {
            GraphTopology::Single if nodes.len() != 1 => {
                return Err(AdapterError::InvalidRoute);
            }
            GraphTopology::UnorderedIndependent
            | GraphTopology::Ordered
            | GraphTopology::Dependent
                if nodes.len() < 2 =>
            {
                return Err(AdapterError::InvalidRoute);
            }
            _ => {}
        }
        if atomic_mapping.is_some() && topology != GraphTopology::AtomicRequired {
            return Err(AdapterError::InvalidRoute);
        }
        if let Some(mapping) = &atomic_mapping {
            validate_atomic_mapping(&nodes, mapping)?;
        }
        Ok(Self {
            nodes,
            topology,
            partial_completion_safe,
            full_clarification_required,
            continuation,
            companion_enabled,
            atomic_mapping,
        })
    }

    #[must_use]
    pub fn nodes(&self) -> &[RouteNode] {
        &self.nodes
    }
}

impl fmt::Debug for RoutePlan {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RoutePlan")
            .field("node_count", &self.nodes.len())
            .field("topology", &self.topology)
            .field("partial_completion_safe", &self.partial_completion_safe)
            .field(
                "full_clarification_required",
                &self.full_clarification_required,
            )
            .field("continuation", &self.continuation)
            .field("companion_enabled", &self.companion_enabled)
            .field("has_atomic_mapping", &self.atomic_mapping.is_some())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CompanionMode {
    Sequential,
    AtomicReadBundle,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RouteAbstention {
    ConflictingGraph,
    ContradictoryGraph,
    AtomicCapabilityUnavailable,
    UnsafePartialCompletion,
    CompanionUnavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransportRoute {
    WyomingSafeRead { node_count: u16 },
    Companion(CompanionMode),
    Abstain(RouteAbstention),
}

#[must_use]
pub fn select_transport(plan: &RoutePlan) -> TransportRoute {
    match plan.topology {
        GraphTopology::Conflicting => {
            return TransportRoute::Abstain(RouteAbstention::ConflictingGraph);
        }
        GraphTopology::Contradictory => {
            return TransportRoute::Abstain(RouteAbstention::ContradictoryGraph);
        }
        GraphTopology::AtomicRequired => {
            return if plan.atomic_mapping.is_some() && plan.companion_enabled {
                TransportRoute::Companion(CompanionMode::AtomicReadBundle)
            } else {
                TransportRoute::Abstain(RouteAbstention::AtomicCapabilityUnavailable)
            };
        }
        _ => {}
    }

    if wyoming_safe(plan) {
        return TransportRoute::WyomingSafeRead {
            node_count: plan.nodes.len() as u16,
        };
    }

    if !plan.companion_enabled {
        return TransportRoute::Abstain(RouteAbstention::CompanionUnavailable);
    }
    if plan.nodes.len() == 1 || plan.partial_completion_safe {
        TransportRoute::Companion(CompanionMode::Sequential)
    } else {
        TransportRoute::Abstain(RouteAbstention::UnsafePartialCompletion)
    }
}

fn wyoming_safe(plan: &RoutePlan) -> bool {
    matches!(
        plan.topology,
        GraphTopology::Single | GraphTopology::UnorderedIndependent
    ) && plan.partial_completion_safe
        && !plan.full_clarification_required
        && !plan.continuation
        && plan.nodes.iter().all(|node| {
            node.operation.kind() == OperationKind::ReadEntityState
                && node.operation.targets().len() == 1
                && node.target_scope == TargetScope::ExactEntity
                && node.wyoming_safety.is_some()
                && !node.caller_authorization_required
                && !node.sensitive
        })
}

fn validate_atomic_mapping(nodes: &[RouteNode], mapping: &TypedOperation) -> Result<()> {
    if mapping.kind() != OperationKind::ReadBundledSnapshot || nodes.len() < 2 {
        return Err(AdapterError::InvalidRoute);
    }
    let mut targets = Vec::with_capacity(nodes.len());
    for node in nodes {
        if node.operation.kind() != OperationKind::ReadEntityState
            || node.operation.targets().len() != 1
            || node.target_scope != TargetScope::ExactEntity
        {
            return Err(AdapterError::InvalidRoute);
        }
        targets.push(node.operation.targets().as_slice()[0].clone());
    }
    let expected = TargetSet::new(targets).map_err(|_| AdapterError::InvalidRoute)?;
    if mapping.targets() != &expected {
        return Err(AdapterError::InvalidRoute);
    }
    Ok(())
}
