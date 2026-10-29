use core::fmt;
use std::collections::BTreeMap;
use std::num::NonZeroU64;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use nlu_core::{
    CapabilityId, CatalogGeneration, ComposedPlan, GraphExecutionClass, LogicalTime, PlanNode,
    Polarity, SlotValue,
};
use session_engine::SessionId;

use crate::{
    PolicyDisposition, PolicyError, PolicyErrorCode, PolicyGeneration, PolicyTable, Result,
    RiskClass, SlotKind,
};

pub const MAX_PENDING_CONFIRMATIONS: usize = 64;
pub const MAX_TTL_TICKS: u64 = 300_000;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ConfirmationId(NonZeroU64);

impl ConfirmationId {
    pub fn new(value: u64) -> Result<Self> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or_else(|| PolicyError::new(PolicyErrorCode::PlanContract))
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConfirmationConfig {
    ttl_ticks: u64,
}

impl ConfirmationConfig {
    pub fn new(ttl_ticks: u64) -> Result<Self> {
        if ttl_ticks == 0 || ttl_ticks > MAX_TTL_TICKS {
            return Err(PolicyError::new(PolicyErrorCode::InvalidTtl));
        }
        Ok(Self { ttl_ticks })
    }

    #[must_use]
    pub const fn ttl_ticks(self) -> u64 {
        self.ttl_ticks
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PolicyContext {
    catalog_generation: CatalogGeneration,
    policy_generation: PolicyGeneration,
}

impl PolicyContext {
    #[must_use]
    pub const fn new(
        catalog_generation: CatalogGeneration,
        policy_generation: PolicyGeneration,
    ) -> Self {
        Self {
            catalog_generation,
            policy_generation,
        }
    }

    #[must_use]
    pub const fn catalog_generation(self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub const fn policy_generation(self) -> PolicyGeneration {
        self.policy_generation
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PolicyDenialReason {
    StaleCatalogGeneration,
    StalePolicyGeneration,
    ContradictoryPlan,
    NegatedNode,
    NonExecutable,
    AtomicOnly,
    MissingRule,
    UnsupportedGraphClass,
    SlotContractMismatch,
    ExplicitDeny,
}

impl PolicyDenialReason {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::StaleCatalogGeneration => "stale_catalog_generation",
            Self::StalePolicyGeneration => "stale_policy_generation",
            Self::ContradictoryPlan => "contradictory_plan",
            Self::NegatedNode => "negated_node",
            Self::NonExecutable => "non_executable",
            Self::AtomicOnly => "atomic_only",
            Self::MissingRule => "missing_rule",
            Self::UnsupportedGraphClass => "unsupported_graph_class",
            Self::SlotContractMismatch => "slot_contract_mismatch",
            Self::ExplicitDeny => "explicit_deny",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PolicyDenial {
    reason: PolicyDenialReason,
}

impl PolicyDenial {
    #[must_use]
    pub const fn reason(self) -> PolicyDenialReason {
        self.reason
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PolicyAcceptance {
    risk: RiskClass,
    catalog_generation: CatalogGeneration,
    policy_generation: PolicyGeneration,
    capability_count: u16,
}

impl PolicyAcceptance {
    #[must_use]
    pub const fn risk(self) -> RiskClass {
        self.risk
    }

    #[must_use]
    pub const fn catalog_generation(self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub const fn policy_generation(self) -> PolicyGeneration {
        self.policy_generation
    }

    #[must_use]
    pub const fn capability_count(self) -> u16 {
        self.capability_count
    }

    #[must_use]
    pub const fn authorizes_execution(self) -> bool {
        false
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConfirmationRequirement {
    confirmation_id: Option<ConfirmationId>,
    risk: RiskClass,
    catalog_generation: CatalogGeneration,
    policy_generation: PolicyGeneration,
    capability_count: u16,
}

impl ConfirmationRequirement {
    #[must_use]
    pub const fn confirmation_id(self) -> Option<ConfirmationId> {
        self.confirmation_id
    }

    #[must_use]
    pub const fn risk(self) -> RiskClass {
        self.risk
    }

    #[must_use]
    pub const fn catalog_generation(self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub const fn policy_generation(self) -> PolicyGeneration {
        self.policy_generation
    }

    #[must_use]
    pub const fn capability_count(self) -> u16 {
        self.capability_count
    }

    #[must_use]
    pub const fn authorizes_execution(self) -> bool {
        false
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PolicyDecision {
    Denied(PolicyDenial),
    AllowedWithoutConfirmation(PolicyAcceptance),
    ConfirmationRequired(ConfirmationRequirement),
}

impl PolicyDecision {
    #[must_use]
    pub const fn authorizes_execution(self) -> bool {
        false
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConfirmationRejectionReason {
    Unavailable,
    Expired,
    BindingMismatch,
}

impl ConfirmationRejectionReason {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Unavailable => "confirmation_unavailable",
            Self::Expired => "confirmation_expired",
            Self::BindingMismatch => "confirmation_binding_mismatch",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConfirmationRejection {
    reason: ConfirmationRejectionReason,
}

impl ConfirmationRejection {
    #[must_use]
    pub const fn reason(self) -> ConfirmationRejectionReason {
        self.reason
    }
}

pub struct StoredConfirmationAcceptance {
    plan: ComposedPlan,
    acceptance: PolicyAcceptance,
}

impl StoredConfirmationAcceptance {
    #[must_use]
    pub const fn plan(&self) -> &ComposedPlan {
        &self.plan
    }

    #[must_use]
    pub const fn acceptance(&self) -> PolicyAcceptance {
        self.acceptance
    }

    #[must_use]
    pub fn into_parts(self) -> (ComposedPlan, PolicyAcceptance) {
        (self.plan, self.acceptance)
    }

    #[must_use]
    pub const fn authorizes_execution(&self) -> bool {
        false
    }
}

impl fmt::Debug for StoredConfirmationAcceptance {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("StoredConfirmationAcceptance { plan: redacted, authorization: false }")
    }
}

pub enum StoredConfirmationOutcome {
    Accepted(StoredConfirmationAcceptance),
    Rejected(ConfirmationRejection),
}

impl StoredConfirmationOutcome {
    #[must_use]
    pub const fn authorizes_execution(&self) -> bool {
        false
    }
}

impl fmt::Debug for StoredConfirmationOutcome {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Accepted(_) => formatter.write_str("Accepted(redacted)"),
            Self::Rejected(rejection) => {
                formatter.debug_tuple("Rejected").field(rejection).finish()
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CancellationOutcome {
    Cancelled,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PolicyDiagnostics {
    pending_confirmations: u16,
}

impl PolicyDiagnostics {
    #[must_use]
    pub const fn pending_confirmations(self) -> u16 {
        self.pending_confirmations
    }
}

struct EvaluatedBinding {
    risk: RiskClass,
    capabilities: Vec<CapabilityId>,
    catalog_generation: CatalogGeneration,
    policy_generation: PolicyGeneration,
}

enum Evaluation {
    Denied(PolicyDenialReason),
    Allowed(EvaluatedBinding),
    RequiresConfirmation(EvaluatedBinding),
}

struct PendingConfirmation {
    confirmation_id: ConfirmationId,
    session: SessionId,
    plan: ComposedPlan,
    canonical_plan: Vec<u8>,
    capabilities: Vec<CapabilityId>,
    catalog_generation: CatalogGeneration,
    policy_generation: PolicyGeneration,
    risk: RiskClass,
    deadline: LogicalTime,
}

enum TakenConfirmation {
    Unavailable,
    Expired,
    Pending(Box<PendingConfirmation>),
}

#[derive(Clone, Default)]
struct ConfirmationSequence {
    last_issued: Arc<AtomicU64>,
}

impl ConfirmationSequence {
    fn next(&self) -> Result<ConfirmationId> {
        let previous = self
            .last_issued
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |value| {
                value.checked_add(1)
            })
            .map_err(|_| PolicyError::new(PolicyErrorCode::PlanContract))?;
        ConfirmationId::new(previous + 1)
    }

    fn is_pristine(&self) -> bool {
        self.last_issued.load(Ordering::SeqCst) == 0
    }

    fn shares_source_with(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.last_issued, &other.last_issued)
    }
}

#[derive(Default)]
struct ConfirmationState {
    last_observed: Option<LogicalTime>,
    confirmations: BTreeMap<SessionId, Box<PendingConfirmation>>,
    sequence: ConfirmationSequence,
}

impl ConfirmationState {
    fn observe(&mut self, now: LogicalTime) -> Result<()> {
        if self.last_observed.is_some_and(|last| now < last) {
            self.confirmations.clear();
            self.last_observed = Some(now);
            return Err(PolicyError::new(PolicyErrorCode::ClockRollback));
        }
        self.last_observed = Some(now);
        Ok(())
    }

    fn purge_expired(&mut self, now: LogicalTime) -> usize {
        let before = self.confirmations.len();
        self.confirmations
            .retain(|_, pending| now < pending.deadline);
        before - self.confirmations.len()
    }
}

pub struct PolicyEngine {
    table: PolicyTable,
    config: ConfirmationConfig,
    state: Mutex<ConfirmationState>,
}

impl PolicyEngine {
    #[must_use]
    pub fn new(table: PolicyTable, config: ConfirmationConfig) -> Self {
        Self {
            table,
            config,
            state: Mutex::new(ConfirmationState::default()),
        }
    }

    #[must_use]
    pub const fn table(&self) -> &PolicyTable {
        &self.table
    }

    pub fn assess(&self, plan: &ComposedPlan, context: PolicyContext) -> Result<PolicyDecision> {
        match self.evaluate_plan(plan, context) {
            Evaluation::Denied(reason) => Ok(PolicyDecision::Denied(PolicyDenial { reason })),
            Evaluation::Allowed(binding) => Ok(PolicyDecision::AllowedWithoutConfirmation(
                acceptance(&binding)?,
            )),
            Evaluation::RequiresConfirmation(binding) => Ok(PolicyDecision::ConfirmationRequired(
                requirement(&binding, None)?,
            )),
        }
    }

    pub fn evaluate(
        &self,
        plan: &ComposedPlan,
        session: &SessionId,
        context: PolicyContext,
        now: LogicalTime,
    ) -> Result<PolicyDecision> {
        match self.evaluate_plan(plan, context) {
            Evaluation::Denied(reason) => {
                self.discard_for_new_evaluation(session, now)?;
                Ok(PolicyDecision::Denied(PolicyDenial { reason }))
            }
            Evaluation::Allowed(binding) => {
                self.discard_for_new_evaluation(session, now)?;
                Ok(PolicyDecision::AllowedWithoutConfirmation(acceptance(
                    &binding,
                )?))
            }
            Evaluation::RequiresConfirmation(binding) => {
                let canonical_plan = match plan.canonical_bytes() {
                    Ok(bytes) => bytes,
                    Err(_) => {
                        self.discard_for_new_evaluation(session, now)?;
                        return Err(PolicyError::new(PolicyErrorCode::PlanContract));
                    }
                };
                let confirmation_id =
                    self.begin_confirmation(session, plan.clone(), &binding, canonical_plan, now)?;
                let requirement = requirement(&binding, Some(confirmation_id))?;
                Ok(PolicyDecision::ConfirmationRequired(requirement))
            }
        }
    }

    pub fn confirm_stored(
        &self,
        session: &SessionId,
        confirmation_id: ConfirmationId,
        addressed_plan: &ComposedPlan,
        context: PolicyContext,
        now: LogicalTime,
    ) -> Result<StoredConfirmationOutcome> {
        let pending = match self.take_confirmation(session, now)? {
            TakenConfirmation::Unavailable => {
                return Ok(stored_rejected(ConfirmationRejectionReason::Unavailable));
            }
            TakenConfirmation::Expired => {
                return Ok(stored_rejected(ConfirmationRejectionReason::Expired));
            }
            TakenConfirmation::Pending(pending) => *pending,
        };

        if pending.confirmation_id != confirmation_id
            || pending.session != *session
            || pending.plan != *addressed_plan
            || pending.catalog_generation != context.catalog_generation
            || pending.policy_generation != context.policy_generation
        {
            return Ok(stored_rejected(
                ConfirmationRejectionReason::BindingMismatch,
            ));
        }

        let canonical_plan = pending
            .plan
            .canonical_bytes()
            .map_err(|_| PolicyError::new(PolicyErrorCode::PlanContract))?;
        let Evaluation::RequiresConfirmation(binding) = self.evaluate_plan(&pending.plan, context)
        else {
            return Ok(stored_rejected(
                ConfirmationRejectionReason::BindingMismatch,
            ));
        };
        if pending.canonical_plan != canonical_plan
            || pending.capabilities != binding.capabilities
            || pending.catalog_generation != binding.catalog_generation
            || pending.policy_generation != binding.policy_generation
            || pending.risk != binding.risk
        {
            return Ok(stored_rejected(
                ConfirmationRejectionReason::BindingMismatch,
            ));
        }

        let acceptance = acceptance_from_pending(&pending)?;
        Ok(StoredConfirmationOutcome::Accepted(
            StoredConfirmationAcceptance {
                plan: pending.plan,
                acceptance,
            },
        ))
    }

    pub fn cancel(&self, session: &SessionId, now: LogicalTime) -> Result<CancellationOutcome> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        state.purge_expired(now);
        Ok(if state.confirmations.remove(session).is_some() {
            CancellationOutcome::Cancelled
        } else {
            CancellationOutcome::Unavailable
        })
    }

    pub fn invalidate_for_reload(&self, now: LogicalTime) -> Result<usize> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        let removed = state.confirmations.len();
        state.confirmations.clear();
        Ok(removed)
    }

    pub fn inherit_confirmation_sequence(&self, predecessor: &Self) -> Result<()> {
        let predecessor_sequence = predecessor.lock_state()?.sequence.clone();
        let mut state = self.lock_state()?;
        if state.last_observed.is_some()
            || !state.confirmations.is_empty()
            || (!state.sequence.shares_source_with(&predecessor_sequence)
                && !state.sequence.is_pristine())
        {
            return Err(PolicyError::new(PolicyErrorCode::PlanContract));
        }
        state.sequence = predecessor_sequence;
        Ok(())
    }

    pub fn purge_expired(&self, now: LogicalTime) -> Result<usize> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        Ok(state.purge_expired(now))
    }

    pub fn diagnostics(&self) -> Result<PolicyDiagnostics> {
        let state = self.lock_state()?;
        let pending_confirmations = u16::try_from(state.confirmations.len())
            .map_err(|_| PolicyError::new(PolicyErrorCode::PlanContract))?;
        Ok(PolicyDiagnostics {
            pending_confirmations,
        })
    }

    fn begin_confirmation(
        &self,
        session: &SessionId,
        plan: ComposedPlan,
        binding: &EvaluatedBinding,
        canonical_plan: Vec<u8>,
        now: LogicalTime,
    ) -> Result<ConfirmationId> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        state.purge_expired(now);
        state.confirmations.remove(session);

        let deadline_ticks = now
            .ticks()
            .checked_add(self.config.ttl_ticks())
            .ok_or_else(|| PolicyError::new(PolicyErrorCode::DeadlineOverflow))?;
        if state.confirmations.len() >= MAX_PENDING_CONFIRMATIONS {
            return Err(PolicyError::new(PolicyErrorCode::CapacityExceeded));
        }
        let confirmation_id = state.sequence.next()?;

        state.confirmations.insert(
            *session,
            Box::new(PendingConfirmation {
                confirmation_id,
                session: *session,
                plan,
                canonical_plan,
                capabilities: binding.capabilities.clone(),
                catalog_generation: binding.catalog_generation,
                policy_generation: binding.policy_generation,
                risk: binding.risk,
                deadline: LogicalTime::from_ticks(deadline_ticks),
            }),
        );
        Ok(confirmation_id)
    }

    fn take_confirmation(
        &self,
        session: &SessionId,
        now: LogicalTime,
    ) -> Result<TakenConfirmation> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        let pending = state.confirmations.remove(session);
        state.purge_expired(now);
        Ok(match pending {
            None => TakenConfirmation::Unavailable,
            Some(pending) if now >= pending.deadline => TakenConfirmation::Expired,
            Some(pending) => TakenConfirmation::Pending(pending),
        })
    }

    fn discard_for_new_evaluation(&self, session: &SessionId, now: LogicalTime) -> Result<()> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        state.purge_expired(now);
        state.confirmations.remove(session);
        Ok(())
    }

    fn evaluate_plan(&self, plan: &ComposedPlan, context: PolicyContext) -> Evaluation {
        if context.policy_generation != self.table.generation() {
            return Evaluation::Denied(PolicyDenialReason::StalePolicyGeneration);
        }
        if context.catalog_generation != plan.plan().catalog_generation() {
            return Evaluation::Denied(PolicyDenialReason::StaleCatalogGeneration);
        }
        if has_contradiction(plan) {
            return Evaluation::Denied(PolicyDenialReason::ContradictoryPlan);
        }
        if plan
            .clauses()
            .iter()
            .any(|clause| clause.polarity() == Polarity::Negated)
        {
            return Evaluation::Denied(PolicyDenialReason::NegatedNode);
        }
        match plan.execution_class() {
            GraphExecutionClass::NonExecutable => {
                return Evaluation::Denied(PolicyDenialReason::NonExecutable);
            }
            GraphExecutionClass::AtomicOnly => {
                return Evaluation::Denied(PolicyDenialReason::AtomicOnly);
            }
            GraphExecutionClass::PartialSafe => {}
        }

        let mut risk = RiskClass::Observation;
        let mut capabilities = Vec::with_capacity(plan.plan().nodes().len());
        let mut requires_confirmation = false;

        for node in plan.plan().nodes() {
            let Some(descriptor) = self.table.descriptor(node.capability(), node.operation())
            else {
                return Evaluation::Denied(PolicyDenialReason::MissingRule);
            };
            if !descriptor
                .permitted_graph_classes()
                .allows(plan.execution_class())
            {
                return Evaluation::Denied(PolicyDenialReason::UnsupportedGraphClass);
            }
            if !slot_contract_matches(node, descriptor.expected_slots()) {
                return Evaluation::Denied(PolicyDenialReason::SlotContractMismatch);
            }
            match descriptor.disposition() {
                PolicyDisposition::Deny => {
                    return Evaluation::Denied(PolicyDenialReason::ExplicitDeny);
                }
                PolicyDisposition::AllowWithoutConfirmation => {}
                PolicyDisposition::RequireConfirmation => requires_confirmation = true,
            }
            risk = risk.max(descriptor.risk());
            capabilities.push(node.capability().clone());
        }

        let binding = EvaluatedBinding {
            risk,
            capabilities,
            catalog_generation: context.catalog_generation,
            policy_generation: context.policy_generation,
        };
        if requires_confirmation {
            Evaluation::RequiresConfirmation(binding)
        } else {
            Evaluation::Allowed(binding)
        }
    }

    fn lock_state(&self) -> Result<std::sync::MutexGuard<'_, ConfirmationState>> {
        self.state
            .lock()
            .map_err(|_| PolicyError::new(PolicyErrorCode::LockPoisoned))
    }
}

impl fmt::Debug for PolicyEngine {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PolicyEngine { residential_data: redacted }")
    }
}

fn acceptance(binding: &EvaluatedBinding) -> Result<PolicyAcceptance> {
    Ok(PolicyAcceptance {
        risk: binding.risk,
        catalog_generation: binding.catalog_generation,
        policy_generation: binding.policy_generation,
        capability_count: capability_count(binding)?,
    })
}

fn acceptance_from_pending(pending: &PendingConfirmation) -> Result<PolicyAcceptance> {
    Ok(PolicyAcceptance {
        risk: pending.risk,
        catalog_generation: pending.catalog_generation,
        policy_generation: pending.policy_generation,
        capability_count: u16::try_from(pending.capabilities.len())
            .map_err(|_| PolicyError::new(PolicyErrorCode::PlanContract))?,
    })
}

fn requirement(
    binding: &EvaluatedBinding,
    confirmation_id: Option<ConfirmationId>,
) -> Result<ConfirmationRequirement> {
    Ok(ConfirmationRequirement {
        confirmation_id,
        risk: binding.risk,
        catalog_generation: binding.catalog_generation,
        policy_generation: binding.policy_generation,
        capability_count: capability_count(binding)?,
    })
}

fn capability_count(binding: &EvaluatedBinding) -> Result<u16> {
    u16::try_from(binding.capabilities.len())
        .map_err(|_| PolicyError::new(PolicyErrorCode::PlanContract))
}

const fn stored_rejected(reason: ConfirmationRejectionReason) -> StoredConfirmationOutcome {
    StoredConfirmationOutcome::Rejected(ConfirmationRejection { reason })
}

fn slot_contract_matches(node: &PlanNode, expected: &[crate::ExpectedSlot]) -> bool {
    node.slots().len() == expected.len()
        && node.slots().iter().zip(expected).all(|(actual, expected)| {
            actual.id() == expected.id()
                && match (expected.kind(), actual.value()) {
                    (SlotKind::Entity, SlotValue::Entity(_))
                    | (SlotKind::EvidenceText, SlotValue::EvidenceText(_))
                    | (SlotKind::Integer, SlotValue::Integer(_))
                    | (SlotKind::Boolean, SlotValue::Boolean(_)) => true,
                    (
                        SlotKind::Entity,
                        SlotValue::EvidenceText(_) | SlotValue::Integer(_) | SlotValue::Boolean(_),
                    )
                    | (
                        SlotKind::EvidenceText,
                        SlotValue::Entity(_) | SlotValue::Integer(_) | SlotValue::Boolean(_),
                    )
                    | (
                        SlotKind::Integer,
                        SlotValue::EvidenceText(_) | SlotValue::Boolean(_) | SlotValue::Entity(_),
                    )
                    | (
                        SlotKind::Boolean,
                        SlotValue::EvidenceText(_) | SlotValue::Integer(_) | SlotValue::Entity(_),
                    ) => false,
                }
        })
}

fn has_contradiction(plan: &ComposedPlan) -> bool {
    let nodes = plan.plan().nodes();
    for (index, left) in nodes.iter().enumerate() {
        for right in &nodes[index + 1..] {
            if !share_entity(left, right) {
                continue;
            }
            let left_polarity = polarity_for(plan, left);
            let right_polarity = polarity_for(plan, right);
            if left.capability() == right.capability()
                && left.operation() == right.operation()
                && left_polarity != right_polarity
            {
                return true;
            }
            if opposing_operations(left.operation().as_str(), right.operation().as_str()) {
                return true;
            }
            if left.operation().as_str() == "ha:set_position"
                && right.operation().as_str() == "ha:set_position"
                && integer_slot(left, "ha:position")
                    .zip(integer_slot(right, "ha:position"))
                    .is_some_and(|(left, right)| left != right)
            {
                return true;
            }
        }
    }
    false
}

fn polarity_for(plan: &ComposedPlan, node: &PlanNode) -> Option<Polarity> {
    plan.clauses()
        .binary_search_by(|clause| clause.node().cmp(node.id()))
        .ok()
        .map(|index| plan.clauses()[index].polarity())
}

fn share_entity(left: &PlanNode, right: &PlanNode) -> bool {
    left.slots().iter().any(|left_slot| {
        let SlotValue::Entity(left_entity) = left_slot.value() else {
            return false;
        };
        right.slots().iter().any(
            |right_slot| matches!(right_slot.value(), SlotValue::Entity(right_entity) if right_entity == left_entity),
        )
    })
}

fn opposing_operations(left: &str, right: &str) -> bool {
    matches!(
        (left, right),
        ("ha:turn_on", "ha:turn_off")
            | ("ha:turn_off", "ha:turn_on")
            | ("ha:start_timer", "ha:cancel_timer")
            | ("ha:cancel_timer", "ha:start_timer")
    )
}

fn integer_slot(node: &PlanNode, id: &str) -> Option<i64> {
    node.slots()
        .iter()
        .find(|slot| slot.id().as_str() == id)
        .and_then(|slot| match slot.value() {
            SlotValue::Integer(value) => Some(*value),
            SlotValue::EvidenceText(_) | SlotValue::Boolean(_) | SlotValue::Entity(_) => None,
        })
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::thread;

    use super::*;

    #[test]
    fn poisoned_confirmation_lock_fails_closed() {
        let engine = Arc::new(PolicyEngine::new(
            PolicyTable::standard(
                PolicyGeneration::new(1).expect("FIXTURE_TECNICA policy generation"),
            )
            .expect("FIXTURE_TECNICA policy table"),
            ConfirmationConfig::new(1).expect("FIXTURE_TECNICA confirmation config"),
        ));
        let poisoner = Arc::clone(&engine);
        let _ = thread::spawn(move || {
            let _guard = poisoner
                .state
                .lock()
                .expect("FIXTURE_TECNICA unpoisoned lock");
            panic!("FIXTURE_TECNICA deliberate lock poison");
        })
        .join();

        assert_eq!(
            engine
                .diagnostics()
                .expect_err("FIXTURE_TECNICA poisoned lock")
                .code(),
            PolicyErrorCode::LockPoisoned
        );
    }
}
