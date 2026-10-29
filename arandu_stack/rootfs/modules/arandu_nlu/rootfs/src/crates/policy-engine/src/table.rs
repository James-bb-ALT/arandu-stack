use core::fmt;
use core::num::NonZeroU64;
use std::collections::BTreeMap;

use nlu_core::{CapabilityId, GraphExecutionClass, OperationId, SlotId};

use crate::{PolicyError, PolicyErrorCode, Result};

pub const PLAN_OPERATION_COUNT: usize = 20;
pub const STANDARD_DESCRIPTOR_COUNT: usize = 21;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct PolicyGeneration(NonZeroU64);

impl PolicyGeneration {
    pub fn new(value: u64) -> Result<Self> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or_else(|| PolicyError::new(PolicyErrorCode::InvalidGeneration))
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum RiskClass {
    Observation,
    LocalControl,
    StateChange,
    Sensitive,
}

impl RiskClass {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Observation => "observation",
            Self::LocalControl => "local_control",
            Self::StateChange => "state_change",
            Self::Sensitive => "sensitive",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PolicyDisposition {
    Deny,
    AllowWithoutConfirmation,
    RequireConfirmation,
}

impl PolicyDisposition {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Deny => "deny",
            Self::AllowWithoutConfirmation => "allow_without_confirmation",
            Self::RequireConfirmation => "require_confirmation",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum SlotKind {
    Entity,
    EvidenceText,
    Integer,
    Boolean,
}

impl SlotKind {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Entity => "entity",
            Self::EvidenceText => "evidence_text",
            Self::Integer => "integer",
            Self::Boolean => "boolean",
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ExpectedSlot {
    id: SlotId,
    kind: SlotKind,
}

impl ExpectedSlot {
    #[must_use]
    pub const fn new(id: SlotId, kind: SlotKind) -> Self {
        Self { id, kind }
    }

    #[must_use]
    pub const fn id(&self) -> &SlotId {
        &self.id
    }

    #[must_use]
    pub const fn kind(&self) -> SlotKind {
        self.kind
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PermittedGraphClasses(u8);

impl PermittedGraphClasses {
    const PARTIAL_SAFE: u8 = 1;
    const ATOMIC_ONLY: u8 = 2;
    const VALID: u8 = Self::PARTIAL_SAFE | Self::ATOMIC_ONLY;

    pub fn new(classes: &[GraphExecutionClass]) -> Result<Self> {
        let mut bits = 0_u8;
        for class in classes {
            let bit = match class {
                GraphExecutionClass::PartialSafe => Self::PARTIAL_SAFE,
                GraphExecutionClass::AtomicOnly => Self::ATOMIC_ONLY,
                GraphExecutionClass::NonExecutable => {
                    return Err(PolicyError::new(PolicyErrorCode::InvalidGraphClasses));
                }
            };
            if bits & bit != 0 {
                return Err(PolicyError::new(PolicyErrorCode::InvalidGraphClasses));
            }
            bits |= bit;
        }
        if bits == 0 || bits & !Self::VALID != 0 {
            return Err(PolicyError::new(PolicyErrorCode::InvalidGraphClasses));
        }
        Ok(Self(bits))
    }

    #[must_use]
    pub const fn partial_safe() -> Self {
        Self(Self::PARTIAL_SAFE)
    }

    #[must_use]
    pub const fn partial_safe_and_atomic_only() -> Self {
        Self(Self::PARTIAL_SAFE | Self::ATOMIC_ONLY)
    }

    #[must_use]
    pub const fn allows(self, class: GraphExecutionClass) -> bool {
        let bit = match class {
            GraphExecutionClass::PartialSafe => Self::PARTIAL_SAFE,
            GraphExecutionClass::AtomicOnly => Self::ATOMIC_ONLY,
            GraphExecutionClass::NonExecutable => 0,
        };
        bit != 0 && self.0 & bit != 0
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct PolicyDescriptor {
    capability: CapabilityId,
    operation: OperationId,
    risk: RiskClass,
    expected_slots: Vec<ExpectedSlot>,
    permitted_graph_classes: PermittedGraphClasses,
    disposition: PolicyDisposition,
}

impl PolicyDescriptor {
    pub fn new(
        capability: CapabilityId,
        operation: OperationId,
        risk: RiskClass,
        mut expected_slots: Vec<ExpectedSlot>,
        permitted_graph_classes: PermittedGraphClasses,
        disposition: PolicyDisposition,
    ) -> Result<Self> {
        if expected_slots.is_empty() {
            return Err(PolicyError::new(PolicyErrorCode::InvalidSlotSchema));
        }
        expected_slots.sort();
        if expected_slots
            .windows(2)
            .any(|pair| pair[0].id == pair[1].id)
        {
            return Err(PolicyError::new(PolicyErrorCode::InvalidSlotSchema));
        }
        Ok(Self {
            capability,
            operation,
            risk,
            expected_slots,
            permitted_graph_classes,
            disposition,
        })
    }

    #[must_use]
    pub const fn capability(&self) -> &CapabilityId {
        &self.capability
    }

    #[must_use]
    pub const fn operation(&self) -> &OperationId {
        &self.operation
    }

    #[must_use]
    pub const fn risk(&self) -> RiskClass {
        self.risk
    }

    #[must_use]
    pub fn expected_slots(&self) -> &[ExpectedSlot] {
        &self.expected_slots
    }

    #[must_use]
    pub const fn permitted_graph_classes(&self) -> PermittedGraphClasses {
        self.permitted_graph_classes
    }

    #[must_use]
    pub const fn disposition(&self) -> PolicyDisposition {
        self.disposition
    }

    #[must_use]
    pub fn with_disposition(mut self, disposition: PolicyDisposition) -> Self {
        self.disposition = disposition;
        self
    }
}

impl fmt::Debug for PolicyDescriptor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PolicyDescriptor")
            .field("capability", &self.capability)
            .field("operation", &self.operation)
            .field("risk", &self.risk)
            .field("slot_count", &self.expected_slots.len())
            .field("permitted_graph_classes", &self.permitted_graph_classes)
            .field("disposition", &self.disposition)
            .finish()
    }
}

#[derive(Clone)]
pub struct PolicyTable {
    generation: PolicyGeneration,
    descriptors: BTreeMap<(CapabilityId, OperationId), PolicyDescriptor>,
}

impl PolicyTable {
    pub fn standard(generation: PolicyGeneration) -> Result<Self> {
        Self::new(generation, Self::standard_descriptors()?)
    }

    pub fn standard_descriptors() -> Result<Vec<PolicyDescriptor>> {
        STANDARD_SPECS.iter().map(build_descriptor).collect()
    }

    pub fn new(generation: PolicyGeneration, descriptors: Vec<PolicyDescriptor>) -> Result<Self> {
        let expected = Self::standard_descriptors()?
            .into_iter()
            .map(|descriptor| {
                (
                    (descriptor.capability.clone(), descriptor.operation.clone()),
                    descriptor,
                )
            })
            .collect::<BTreeMap<_, _>>();
        let mut accepted = BTreeMap::new();

        for descriptor in descriptors {
            let key = (descriptor.capability.clone(), descriptor.operation.clone());
            let Some(contract) = expected.get(&key) else {
                return Err(PolicyError::new(PolicyErrorCode::UnknownDescriptor));
            };
            if accepted.contains_key(&key) {
                return Err(PolicyError::new(PolicyErrorCode::DuplicateDescriptor));
            }
            if descriptor.risk != contract.risk
                || descriptor.expected_slots != contract.expected_slots
                || descriptor.permitted_graph_classes != contract.permitted_graph_classes
                || !disposition_is_no_more_permissive(descriptor.disposition, contract.disposition)
            {
                return Err(PolicyError::new(PolicyErrorCode::DescriptorContract));
            }
            accepted.insert(key, descriptor);
        }

        if accepted.len() != expected.len() {
            return Err(PolicyError::new(PolicyErrorCode::IncompleteConfiguration));
        }
        Ok(Self {
            generation,
            descriptors: accepted,
        })
    }

    #[must_use]
    pub const fn generation(&self) -> PolicyGeneration {
        self.generation
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.descriptors.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.descriptors.is_empty()
    }

    pub fn descriptors(&self) -> impl ExactSizeIterator<Item = &PolicyDescriptor> {
        self.descriptors.values()
    }

    #[must_use]
    pub fn descriptor(
        &self,
        capability: &CapabilityId,
        operation: &OperationId,
    ) -> Option<&PolicyDescriptor> {
        self.descriptors
            .get(&(capability.clone(), operation.clone()))
    }
}

impl fmt::Debug for PolicyTable {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PolicyTable")
            .field("generation", &self.generation)
            .field("descriptor_count", &self.descriptors.len())
            .finish()
    }
}

#[derive(Clone, Copy)]
struct SlotSpec {
    id: &'static str,
    kind: SlotKind,
}

#[derive(Clone, Copy)]
struct DescriptorSpec {
    capability: &'static str,
    operation: &'static str,
    risk: RiskClass,
    slots: &'static [SlotSpec],
    graph_classes: PermittedGraphClasses,
    disposition: PolicyDisposition,
}

const ENTITY: &[SlotSpec] = &[SlotSpec {
    id: "ha:entity",
    kind: SlotKind::Entity,
}];
const TIMER: &[SlotSpec] = &[SlotSpec {
    id: "ha:timer",
    kind: SlotKind::Entity,
}];
const AREA: &[SlotSpec] = &[SlotSpec {
    id: "ha:area",
    kind: SlotKind::EvidenceText,
}];
const PENDING_ACTION: &[SlotSpec] = &[SlotSpec {
    id: "ha:pending_action",
    kind: SlotKind::EvidenceText,
}];
const RESPONSE_TEXT: &[SlotSpec] = &[SlotSpec {
    id: "ha:response_text",
    kind: SlotKind::EvidenceText,
}];
const BROADCAST: &[SlotSpec] = &[
    SlotSpec {
        id: "ha:message",
        kind: SlotKind::EvidenceText,
    },
    SlotSpec {
        id: "ha:entity",
        kind: SlotKind::Entity,
    },
];
const POSITION: &[SlotSpec] = &[
    SlotSpec {
        id: "ha:entity",
        kind: SlotKind::Entity,
    },
    SlotSpec {
        id: "ha:position",
        kind: SlotKind::Integer,
    },
];
const START_TIMER: &[SlotSpec] = &[
    SlotSpec {
        id: "ha:timer",
        kind: SlotKind::Entity,
    },
    SlotSpec {
        id: "ha:duration_seconds",
        kind: SlotKind::Integer,
    },
];
const TIMER_DELTA: &[SlotSpec] = &[
    SlotSpec {
        id: "ha:timer",
        kind: SlotKind::Entity,
    },
    SlotSpec {
        id: "ha:duration_delta_seconds",
        kind: SlotKind::Integer,
    },
];

const PARTIAL: PermittedGraphClasses = PermittedGraphClasses::partial_safe();
const PARTIAL_OR_ATOMIC: PermittedGraphClasses =
    PermittedGraphClasses::partial_safe_and_atomic_only();
const ALLOW: PolicyDisposition = PolicyDisposition::AllowWithoutConfirmation;
const CONFIRM: PolicyDisposition = PolicyDisposition::RequireConfirmation;

const STANDARD_SPECS: [DescriptorSpec; STANDARD_DESCRIPTOR_COUNT] = [
    DescriptorSpec {
        capability: "ha:broadcast",
        operation: "ha:broadcast",
        risk: RiskClass::Sensitive,
        slots: BROADCAST,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:cancel_all_timers",
        risk: RiskClass::Sensitive,
        slots: AREA,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:cancel_timer",
        risk: RiskClass::StateChange,
        slots: TIMER,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:temperature_query",
        operation: "ha:get_temperature",
        risk: RiskClass::Observation,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:decrease_timer",
        risk: RiskClass::StateChange,
        slots: TIMER_DELTA,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:date_query",
        operation: "ha:get_current_date",
        risk: RiskClass::Observation,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:time_query",
        operation: "ha:get_current_time",
        risk: RiskClass::Observation,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:state_query",
        operation: "ha:get_state",
        risk: RiskClass::Observation,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:increase_timer",
        risk: RiskClass::StateChange,
        slots: TIMER_DELTA,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:conversation_control",
        operation: "ha:nevermind",
        risk: RiskClass::LocalControl,
        slots: PENDING_ACTION,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:pause_timer",
        risk: RiskClass::StateChange,
        slots: TIMER,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:response",
        operation: "ha:respond",
        risk: RiskClass::LocalControl,
        slots: RESPONSE_TEXT,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:cover_control",
        operation: "ha:set_position",
        risk: RiskClass::StateChange,
        slots: POSITION,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:start_timer",
        risk: RiskClass::StateChange,
        slots: START_TIMER,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:cover_control",
        operation: "ha:stop_moving",
        risk: RiskClass::StateChange,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:timer_query",
        operation: "ha:timer_status",
        risk: RiskClass::Observation,
        slots: TIMER,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
    DescriptorSpec {
        capability: "ha:fan_control",
        operation: "ha:toggle",
        risk: RiskClass::StateChange,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:switch_control",
        operation: "ha:turn_off",
        risk: RiskClass::StateChange,
        slots: ENTITY,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:light_control",
        operation: "ha:turn_on",
        risk: RiskClass::StateChange,
        slots: ENTITY,
        graph_classes: PARTIAL_OR_ATOMIC,
        disposition: CONFIRM,
    },
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:unpause_timer",
        risk: RiskClass::StateChange,
        slots: TIMER,
        graph_classes: PARTIAL,
        disposition: CONFIRM,
    },
    // Ordered start-timer plans synthesize this exact additional cell.
    DescriptorSpec {
        capability: "ha:timer_control",
        operation: "ha:timer_status",
        risk: RiskClass::Observation,
        slots: TIMER,
        graph_classes: PARTIAL,
        disposition: ALLOW,
    },
];

fn build_descriptor(spec: &DescriptorSpec) -> Result<PolicyDescriptor> {
    let capability = CapabilityId::new(spec.capability)
        .map_err(|_| PolicyError::new(PolicyErrorCode::DescriptorContract))?;
    let operation = OperationId::new(spec.operation)
        .map_err(|_| PolicyError::new(PolicyErrorCode::DescriptorContract))?;
    let slots = spec
        .slots
        .iter()
        .map(|slot| {
            SlotId::new(slot.id)
                .map(|id| ExpectedSlot::new(id, slot.kind))
                .map_err(|_| PolicyError::new(PolicyErrorCode::DescriptorContract))
        })
        .collect::<Result<Vec<_>>>()?;
    PolicyDescriptor::new(
        capability,
        operation,
        spec.risk,
        slots,
        spec.graph_classes,
        spec.disposition,
    )
}

const fn disposition_is_no_more_permissive(
    candidate: PolicyDisposition,
    maximum: PolicyDisposition,
) -> bool {
    matches!(
        (candidate, maximum),
        (PolicyDisposition::Deny, _)
            | (
                PolicyDisposition::RequireConfirmation,
                PolicyDisposition::RequireConfirmation
                    | PolicyDisposition::AllowWithoutConfirmation
            )
            | (
                PolicyDisposition::AllowWithoutConfirmation,
                PolicyDisposition::AllowWithoutConfirmation
            )
    )
}
