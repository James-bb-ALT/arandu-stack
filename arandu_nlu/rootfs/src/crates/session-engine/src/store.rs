use core::fmt;
use std::collections::BTreeMap;
use std::sync::Mutex;

use nlu_core::{
    CapabilityId, CatalogGeneration, ComposedPlan, EntityRef, InvocationId, LogicalTime, NodeId,
    SlotId,
};
use plan_engine::PendingEntityComposition;

use crate::{PairingEpoch, Result, SessionBinding, SessionError, SessionErrorCode, SessionId};

pub const MAX_ACTIVE_SESSIONS: usize = 64;
pub const MAX_PENDING_REFERENTS: usize = nlu_core::MAX_CLARIFICATION_OPTIONS;
pub const MAX_TTL_TICKS: u64 = 300_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionConfig {
    ttl_ticks: u64,
}

impl SessionConfig {
    pub fn new(ttl_ticks: u64) -> Result<Self> {
        if ttl_ticks == 0 || ttl_ticks > MAX_TTL_TICKS {
            return Err(SessionError::new(SessionErrorCode::InvalidTtl));
        }
        Ok(Self { ttl_ticks })
    }

    #[must_use]
    pub const fn ttl_ticks(self) -> u64 {
        self.ttl_ticks
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContinuationEndpoint {
    node: NodeId,
    slot: SlotId,
}

impl ContinuationEndpoint {
    #[must_use]
    pub const fn new(node: NodeId, slot: SlotId) -> Self {
        Self { node, slot }
    }

    #[must_use]
    pub const fn node(&self) -> &NodeId {
        &self.node
    }

    #[must_use]
    pub const fn slot(&self) -> &SlotId {
        &self.slot
    }
}

pub enum ReferentResolution {
    Selected(EntityRef),
    UnresolvedTie,
}

impl fmt::Debug for ReferentResolution {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Selected(_) => formatter.write_str("Selected(redacted)"),
            Self::UnresolvedTie => formatter.write_str("UnresolvedTie"),
        }
    }
}

pub struct ContinuationResult {
    session: SessionId,
    binding: Option<SessionBinding>,
    origin: InvocationId,
    capability: CapabilityId,
    generation: CatalogGeneration,
    endpoint: ContinuationEndpoint,
    resolution: ReferentResolution,
}

impl ContinuationResult {
    #[must_use]
    pub const fn new(
        session: SessionId,
        origin: InvocationId,
        capability: CapabilityId,
        generation: CatalogGeneration,
        endpoint: ContinuationEndpoint,
        resolution: ReferentResolution,
    ) -> Self {
        Self {
            session,
            binding: None,
            origin,
            capability,
            generation,
            endpoint,
            resolution,
        }
    }

    #[must_use]
    pub const fn new_bound(
        session: SessionId,
        binding: SessionBinding,
        origin: InvocationId,
        capability: CapabilityId,
        generation: CatalogGeneration,
        endpoint: ContinuationEndpoint,
        resolution: ReferentResolution,
    ) -> Self {
        Self {
            session,
            binding: Some(binding),
            origin,
            capability,
            generation,
            endpoint,
            resolution,
        }
    }
}

impl fmt::Debug for ContinuationResult {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ContinuationResult")
            .field("residential_data", &"redacted")
            .finish()
    }
}

pub enum ContinuationOutcome {
    Completed(ComposedPlan),
    Unavailable,
}

impl fmt::Debug for ContinuationOutcome {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Completed(_) => formatter.write_str("Completed(redacted)"),
            Self::Unavailable => formatter.write_str("Unavailable"),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CancellationOutcome {
    Cancelled,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionDiagnostics {
    pending_sessions: u16,
}

impl SessionDiagnostics {
    #[must_use]
    pub const fn pending_sessions(self) -> u16 {
        self.pending_sessions
    }
}

struct PendingSession {
    binding: Option<SessionBinding>,
    origin: InvocationId,
    capability: CapabilityId,
    generation: CatalogGeneration,
    endpoint: ContinuationEndpoint,
    deadline: LogicalTime,
    pending: PendingEntityComposition,
}

#[derive(Default)]
struct State {
    last_observed: Option<LogicalTime>,
    sessions: BTreeMap<SessionId, PendingSession>,
}

impl State {
    fn observe(&mut self, now: LogicalTime) -> Result<()> {
        if self.last_observed.is_some_and(|last| now < last) {
            self.sessions.clear();
            self.last_observed = Some(now);
            return Err(SessionError::new(SessionErrorCode::ClockRollback));
        }
        self.last_observed = Some(now);
        Ok(())
    }

    fn purge_expired(&mut self, now: LogicalTime) -> usize {
        let before = self.sessions.len();
        self.sessions.retain(|_, pending| now < pending.deadline);
        before - self.sessions.len()
    }
}

pub struct SessionStore {
    config: SessionConfig,
    state: Mutex<State>,
}

impl SessionStore {
    #[must_use]
    pub fn new(config: SessionConfig) -> Self {
        Self {
            config,
            state: Mutex::new(State::default()),
        }
    }

    pub fn begin(
        &self,
        session: SessionId,
        origin: InvocationId,
        pending: PendingEntityComposition,
        now: LogicalTime,
    ) -> Result<()> {
        self.begin_inner(session, None, origin, pending, now)
    }

    pub fn begin_bound(
        &self,
        session: SessionId,
        binding: SessionBinding,
        origin: InvocationId,
        pending: PendingEntityComposition,
        now: LogicalTime,
    ) -> Result<()> {
        self.begin_inner(session, Some(binding), origin, pending, now)
    }

    fn begin_inner(
        &self,
        session: SessionId,
        binding: Option<SessionBinding>,
        origin: InvocationId,
        pending: PendingEntityComposition,
        now: LogicalTime,
    ) -> Result<()> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        state.observe(now)?;
        state.purge_expired(now);

        validate_referent_count(pending.candidates().len())?;
        let deadline_ticks = now
            .ticks()
            .checked_add(self.config.ttl_ticks())
            .ok_or_else(|| SessionError::new(SessionErrorCode::DeadlineOverflow))?;
        let deadline = LogicalTime::from_ticks(deadline_ticks);
        let capability = pending.capability().clone();
        let generation = pending.catalog_generation();
        let endpoint = ContinuationEndpoint::new(
            pending.endpoint().node().clone(),
            pending.endpoint().slot().clone(),
        );
        if state.sessions.contains_key(&session) {
            return Err(SessionError::new(SessionErrorCode::DuplicateSession));
        }
        if state.sessions.len() >= MAX_ACTIVE_SESSIONS {
            return Err(SessionError::new(SessionErrorCode::CapacityExceeded));
        }
        state.sessions.insert(
            session,
            PendingSession {
                binding,
                origin,
                capability,
                generation,
                endpoint,
                deadline,
                pending,
            },
        );
        Ok(())
    }

    pub fn complete(
        &self,
        session: &SessionId,
        result: ContinuationResult,
        current_generation: CatalogGeneration,
        now: LogicalTime,
    ) -> Result<ContinuationOutcome> {
        let pending = {
            let mut state = self
                .state
                .lock()
                .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
            state.observe(now)?;
            state.purge_expired(now);
            if &result.session != session {
                return Ok(ContinuationOutcome::Unavailable);
            }
            state.sessions.remove(session)
        };
        let Some(pending) = pending else {
            return Ok(ContinuationOutcome::Unavailable);
        };

        if pending.origin != result.origin
            || pending.binding != result.binding
            || pending.capability != result.capability
            || pending.generation != result.generation
            || pending.generation != current_generation
            || pending.endpoint != result.endpoint
        {
            return Ok(ContinuationOutcome::Unavailable);
        }
        let ReferentResolution::Selected(referent) = result.resolution else {
            return Ok(ContinuationOutcome::Unavailable);
        };
        if referent.generation() != current_generation {
            return Ok(ContinuationOutcome::Unavailable);
        }

        pending
            .pending
            .complete(referent)
            .map(|plan| {
                plan.map_or(
                    ContinuationOutcome::Unavailable,
                    ContinuationOutcome::Completed,
                )
            })
            .map_err(|_| SessionError::new(SessionErrorCode::PlanContract))
    }

    pub fn continue_with_selection(
        &self,
        session: &SessionId,
        selection: EntityRef,
        current_generation: CatalogGeneration,
        now: LogicalTime,
    ) -> Result<ContinuationOutcome> {
        self.continue_with_selection_inner(session, None, selection, current_generation, now)
    }

    pub fn continue_with_selection_bound(
        &self,
        session: &SessionId,
        binding: SessionBinding,
        selection: EntityRef,
        current_generation: CatalogGeneration,
        now: LogicalTime,
    ) -> Result<ContinuationOutcome> {
        self.continue_with_selection_inner(
            session,
            Some(binding),
            selection,
            current_generation,
            now,
        )
    }

    fn continue_with_selection_inner(
        &self,
        session: &SessionId,
        binding: Option<SessionBinding>,
        selection: EntityRef,
        current_generation: CatalogGeneration,
        now: LogicalTime,
    ) -> Result<ContinuationOutcome> {
        let pending = {
            let mut state = self
                .state
                .lock()
                .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
            state.observe(now)?;
            state.purge_expired(now);
            state.sessions.remove(session)
        };
        let Some(pending) = pending else {
            return Ok(ContinuationOutcome::Unavailable);
        };

        if pending.binding != binding
            || pending.generation != current_generation
            || selection.generation() != current_generation
        {
            return Ok(ContinuationOutcome::Unavailable);
        }

        pending
            .pending
            .complete(selection)
            .map(|plan| {
                plan.map_or(
                    ContinuationOutcome::Unavailable,
                    ContinuationOutcome::Completed,
                )
            })
            .map_err(|_| SessionError::new(SessionErrorCode::PlanContract))
    }

    pub fn cancel(
        &self,
        session: &SessionId,
        origin: &InvocationId,
        now: LogicalTime,
    ) -> Result<CancellationOutcome> {
        self.cancel_inner(session, None, origin, now)
    }

    pub fn cancel_bound(
        &self,
        session: &SessionId,
        binding: SessionBinding,
        origin: &InvocationId,
        now: LogicalTime,
    ) -> Result<CancellationOutcome> {
        self.cancel_inner(session, Some(binding), origin, now)
    }

    fn cancel_inner(
        &self,
        session: &SessionId,
        binding: Option<SessionBinding>,
        origin: &InvocationId,
        now: LogicalTime,
    ) -> Result<CancellationOutcome> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        state.observe(now)?;
        state.purge_expired(now);
        let matches = state
            .sessions
            .get(session)
            .is_some_and(|pending| pending.binding == binding && &pending.origin == origin);
        if !matches {
            return Ok(CancellationOutcome::Unavailable);
        }
        state.sessions.remove(session);
        Ok(CancellationOutcome::Cancelled)
    }

    pub fn invalidate_epoch(&self, epoch: PairingEpoch, now: LogicalTime) -> Result<usize> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        state.observe(now)?;
        state.purge_expired(now);
        let before = state.sessions.len();
        state.sessions.retain(|_, pending| {
            pending
                .binding
                .is_none_or(|binding| binding.epoch() != epoch)
        });
        Ok(before - state.sessions.len())
    }

    pub fn purge_expired(&self, now: LogicalTime) -> Result<usize> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        state.observe(now)?;
        Ok(state.purge_expired(now))
    }

    pub fn invalidate_for_reload(&self, now: LogicalTime) -> Result<usize> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        state.observe(now)?;
        let removed = state.sessions.len();
        state.sessions.clear();
        Ok(removed)
    }

    pub fn invalidate_catalog(
        &self,
        current_generation: CatalogGeneration,
        now: LogicalTime,
    ) -> Result<usize> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        state.observe(now)?;
        state.purge_expired(now);
        let before = state.sessions.len();
        state
            .sessions
            .retain(|_, pending| pending.generation == current_generation);
        Ok(before - state.sessions.len())
    }

    pub fn diagnostics(&self) -> Result<SessionDiagnostics> {
        let state = self
            .state
            .lock()
            .map_err(|_| SessionError::new(SessionErrorCode::LockPoisoned))?;
        let pending_sessions = u16::try_from(state.sessions.len())
            .map_err(|_| SessionError::new(SessionErrorCode::InvalidPendingState))?;
        Ok(SessionDiagnostics { pending_sessions })
    }
}

impl fmt::Debug for SessionStore {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SessionStore { residential_data: redacted }")
    }
}

fn validate_referent_count(count: usize) -> Result<()> {
    if count == 0 || count > MAX_PENDING_REFERENTS {
        return Err(SessionError::new(SessionErrorCode::InvalidPendingState));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::thread;

    use super::*;

    #[test]
    fn configuration_and_referent_limits_are_exact() {
        assert_eq!(
            SessionConfig::new(0),
            Err(SessionError::new(SessionErrorCode::InvalidTtl))
        );
        assert!(SessionConfig::new(1).is_ok());
        assert!(SessionConfig::new(MAX_TTL_TICKS).is_ok());
        assert_eq!(
            SessionConfig::new(MAX_TTL_TICKS + 1),
            Err(SessionError::new(SessionErrorCode::InvalidTtl))
        );

        assert!(validate_referent_count(MAX_PENDING_REFERENTS).is_ok());
        assert_eq!(
            validate_referent_count(MAX_PENDING_REFERENTS + 1),
            Err(SessionError::new(SessionErrorCode::InvalidPendingState))
        );
    }

    #[test]
    fn poisoned_lock_returns_a_closed_error() {
        let store = Arc::new(SessionStore::new(
            SessionConfig::new(1).expect("FIXTURE_TECNICA configuration"),
        ));
        let poisoner = Arc::clone(&store);
        let _ = thread::spawn(move || {
            let _guard = poisoner
                .state
                .lock()
                .expect("FIXTURE_TECNICA unpoisoned lock");
            panic!("FIXTURE_TECNICA deliberate lock poison");
        })
        .join();
        assert_eq!(
            store
                .diagnostics()
                .expect_err("FIXTURE_TECNICA poisoned lock")
                .code(),
            SessionErrorCode::LockPoisoned
        );
    }
}
