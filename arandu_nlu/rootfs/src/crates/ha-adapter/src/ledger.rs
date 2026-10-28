use core::fmt;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Mutex, MutexGuard};

use crate::{
    AdapterError, AuthenticatedRequest, DeliveryKind, IndeterminateReason, LogicalTime,
    NodeAttemptId, NodeExecutionResult, OperationId, PairingEpoch, Result,
};

pub const MAX_LEDGER_CAPACITY: usize = 4_096;
pub const MAX_LEDGER_TTL_TICKS: u64 = 86_400_000;
pub const MAX_RETIRED_EPOCHS: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LedgerConfig {
    capacity: usize,
    ttl_ticks: u64,
}

impl LedgerConfig {
    pub fn new(capacity: usize, ttl_ticks: u64) -> Result<Self> {
        if capacity == 0 || capacity > MAX_LEDGER_CAPACITY {
            return Err(AdapterError::InvalidCapacity);
        }
        if ttl_ticks == 0 || ttl_ticks > MAX_LEDGER_TTL_TICKS {
            return Err(AdapterError::InvalidTtl);
        }
        Ok(Self {
            capacity,
            ttl_ticks,
        })
    }

    #[must_use]
    pub const fn capacity(self) -> usize {
        self.capacity
    }

    #[must_use]
    pub const fn ttl_ticks(self) -> u64 {
        self.ttl_ticks
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReservationOutcome {
    Reserved,
    DuplicateReserved,
    DuplicateInFlight,
    Cached(NodeExecutionResult),
    Indeterminate(IndeterminateReason),
    Expired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerStatus {
    Reserved,
    InFlight,
    Completed,
    Indeterminate,
    Expired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LedgerDiagnostics {
    active: bool,
    retired_epochs: usize,
    epoch_history_exhausted: bool,
    entries: usize,
    reserved: usize,
    in_flight: usize,
    completed: usize,
    indeterminate: usize,
    expired: usize,
}

impl LedgerDiagnostics {
    #[must_use]
    pub const fn active(self) -> bool {
        self.active
    }

    #[must_use]
    pub const fn retired_epochs(self) -> usize {
        self.retired_epochs
    }

    #[must_use]
    pub const fn epoch_history_exhausted(self) -> bool {
        self.epoch_history_exhausted
    }

    #[must_use]
    pub const fn entries(self) -> usize {
        self.entries
    }

    #[must_use]
    pub const fn reserved(self) -> usize {
        self.reserved
    }

    #[must_use]
    pub const fn in_flight(self) -> usize {
        self.in_flight
    }

    #[must_use]
    pub const fn completed(self) -> usize {
        self.completed
    }

    #[must_use]
    pub const fn indeterminate(self) -> usize {
        self.indeterminate
    }

    #[must_use]
    pub const fn expired(self) -> usize {
        self.expired
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
struct OperationKey {
    epoch: PairingEpoch,
    operation_id: OperationId,
    node_attempt_id: NodeAttemptId,
}

impl OperationKey {
    fn from_request(request: &AuthenticatedRequest) -> Self {
        Self {
            epoch: request.epoch(),
            operation_id: request.operation_id(),
            node_attempt_id: request.node_attempt_id(),
        }
    }
}

enum EntryState {
    Reserved,
    InFlight,
    Completed(NodeExecutionResult),
    Indeterminate {
        reason: IndeterminateReason,
        retention_expired: bool,
    },
    Expired,
}

impl EntryState {
    const fn status(&self) -> LedgerStatus {
        match self {
            Self::Reserved => LedgerStatus::Reserved,
            Self::InFlight => LedgerStatus::InFlight,
            Self::Completed(_) => LedgerStatus::Completed,
            Self::Indeterminate { .. } => LedgerStatus::Indeterminate,
            Self::Expired => LedgerStatus::Expired,
        }
    }

    fn duplicate_outcome(&self) -> ReservationOutcome {
        match self {
            Self::Reserved => ReservationOutcome::DuplicateReserved,
            Self::InFlight => ReservationOutcome::DuplicateInFlight,
            Self::Completed(result) => ReservationOutcome::Cached(result.clone()),
            Self::Indeterminate { reason, .. } => ReservationOutcome::Indeterminate(*reason),
            Self::Expired => ReservationOutcome::Expired,
        }
    }
}

struct Entry {
    binding: AuthenticatedRequest,
    deadline: LogicalTime,
    state: EntryState,
}

struct State {
    active_epoch: Option<PairingEpoch>,
    retired_epochs: BTreeSet<PairingEpoch>,
    epoch_history_exhausted: bool,
    operation_high_water: u64,
    last_observed: Option<LogicalTime>,
    entries: BTreeMap<OperationKey, Entry>,
}

impl State {
    fn new(epoch: PairingEpoch) -> Self {
        Self {
            active_epoch: Some(epoch),
            retired_epochs: BTreeSet::new(),
            epoch_history_exhausted: false,
            operation_high_water: 0,
            last_observed: None,
            entries: BTreeMap::new(),
        }
    }

    fn observe(&mut self, now: LogicalTime) -> Result<()> {
        if self.last_observed.is_some_and(|last| now < last) {
            self.entries.clear();
            self.operation_high_water = 0;
            self.retire_active();
            self.last_observed = Some(now);
            return Err(AdapterError::ClockRollback);
        }
        self.last_observed = Some(now);
        Ok(())
    }

    fn validate_active(&self, request: &AuthenticatedRequest) -> Result<()> {
        let active = self.active_epoch.ok_or(AdapterError::NoActiveEpoch)?;
        if request.epoch() != active {
            return Err(AdapterError::WrongEpoch);
        }
        Ok(())
    }

    fn expire(&mut self, now: LogicalTime) -> usize {
        let mut count = 0;
        for entry in self.entries.values_mut() {
            if now < entry.deadline {
                continue;
            }
            match &mut entry.state {
                EntryState::Reserved | EntryState::Completed(_) => {
                    entry.state = EntryState::Expired;
                    count += 1;
                }
                EntryState::InFlight => {
                    entry.state = EntryState::Indeterminate {
                        reason: IndeterminateReason::Timeout,
                        retention_expired: true,
                    };
                    count += 1;
                }
                EntryState::Indeterminate {
                    retention_expired, ..
                } if !*retention_expired => {
                    *retention_expired = true;
                    count += 1;
                }
                EntryState::Indeterminate { .. } | EntryState::Expired => {}
            }
        }
        count
    }

    fn make_capacity(&mut self, capacity: usize, needed: usize) -> Result<()> {
        while self.entries.len().saturating_add(needed) > capacity {
            let Some(key) = self.entries.iter().find_map(|(key, entry)| {
                matches!(
                    &entry.state,
                    EntryState::Expired
                        | EntryState::Indeterminate {
                            retention_expired: true,
                            ..
                        }
                )
                .then_some(*key)
            }) else {
                return Err(AdapterError::LedgerCapacityExceeded);
            };
            self.entries.remove(&key);
        }
        Ok(())
    }

    fn reset_for_epoch(&mut self, epoch: PairingEpoch) {
        self.active_epoch = Some(epoch);
        self.operation_high_water = 0;
        self.last_observed = None;
        self.entries.clear();
    }

    fn retire_active(&mut self) {
        let Some(active) = self.active_epoch.take() else {
            return;
        };
        if self.retired_epochs.len() == MAX_RETIRED_EPOCHS {
            self.epoch_history_exhausted = true;
            return;
        }
        self.retired_epochs.insert(active);
    }
}

pub struct OperationLedger {
    config: LedgerConfig,
    state: Mutex<State>,
}

impl OperationLedger {
    #[must_use]
    pub fn new(config: LedgerConfig, epoch: PairingEpoch) -> Self {
        Self {
            config,
            state: Mutex::new(State::new(epoch)),
        }
    }

    pub fn reserve(
        &self,
        request: &AuthenticatedRequest,
        now: LogicalTime,
    ) -> Result<ReservationOutcome> {
        let mut outcomes = self.reserve_batch(core::slice::from_ref(request), now)?;
        Ok(outcomes
            .pop()
            .expect("one request always produces one reservation outcome"))
    }

    pub(crate) fn reserve_batch(
        &self,
        requests: &[AuthenticatedRequest],
        now: LogicalTime,
    ) -> Result<Vec<ReservationOutcome>> {
        if requests.is_empty() {
            return Err(AdapterError::EmptySchedule);
        }
        let mut state = self.lock_prepared(now)?;
        let first = &requests[0];
        state.validate_active(first)?;

        let operation_id = first.operation_id();
        let delivery = first.delivery();
        let mut keys = BTreeSet::new();
        let mut new_keys = BTreeSet::new();
        let mut existing_count = 0_usize;

        for request in requests {
            state.validate_active(request)?;
            if request.operation_id() != operation_id || request.delivery() != delivery {
                return Err(AdapterError::ScheduleBindingMismatch);
            }
            let key = OperationKey::from_request(request);
            if !keys.insert(key) {
                return Err(AdapterError::DuplicateNodeAttempt);
            }
            if let Some(entry) = state.entries.get(&key) {
                if !entry.binding.same_operation_binding(request) {
                    return Err(AdapterError::BindingMismatch);
                }
                existing_count += 1;
            } else {
                new_keys.insert(key);
            }
        }

        if delivery == DeliveryKind::Retry && !new_keys.is_empty() {
            return Err(AdapterError::UnknownDuplicate);
        }
        if delivery == DeliveryKind::Initial && existing_count != 0 && !new_keys.is_empty() {
            return Err(AdapterError::MixedScheduleReplay);
        }
        if delivery == DeliveryKind::Initial
            && !new_keys.is_empty()
            && operation_id.get() <= state.operation_high_water
        {
            return Err(AdapterError::UnknownDuplicate);
        }

        if !new_keys.is_empty() {
            let deadline = now
                .ticks()
                .checked_add(self.config.ttl_ticks())
                .map(LogicalTime::from_ticks)
                .ok_or(AdapterError::DeadlineOverflow)?;
            state.make_capacity(self.config.capacity(), new_keys.len())?;
            for request in requests {
                let key = OperationKey::from_request(request);
                if new_keys.contains(&key) {
                    state.entries.insert(
                        key,
                        Entry {
                            binding: request.clone(),
                            deadline,
                            state: EntryState::Reserved,
                        },
                    );
                }
            }
            state.operation_high_water = operation_id.get();
        }

        requests
            .iter()
            .map(|request| {
                let key = OperationKey::from_request(request);
                if new_keys.contains(&key) {
                    Ok(ReservationOutcome::Reserved)
                } else {
                    state
                        .entries
                        .get(&key)
                        .map(|entry| entry.state.duplicate_outcome())
                        .ok_or(AdapterError::UnknownDuplicate)
                }
            })
            .collect()
    }

    pub fn begin(&self, request: &AuthenticatedRequest, now: LogicalTime) -> Result<()> {
        let mut state = self.lock_prepared(now)?;
        let entry = entry_mut(&mut state, request)?;
        match entry.state {
            EntryState::Reserved => {
                entry.state = EntryState::InFlight;
                Ok(())
            }
            _ => Err(AdapterError::InvalidLedgerTransition),
        }
    }

    pub fn reject_reserved(
        &self,
        request: &AuthenticatedRequest,
        result: NodeExecutionResult,
        now: LogicalTime,
    ) -> Result<()> {
        if !result.is_definitive()
            || !result.is_compatible_with(request.operation(), request.catalog_generation())
        {
            return Err(AdapterError::ResultOperationMismatch);
        }
        let mut state = self.lock_prepared(now)?;
        let entry = entry_mut(&mut state, request)?;
        match entry.state {
            EntryState::Reserved => {
                entry.state = EntryState::Completed(result);
                Ok(())
            }
            _ => Err(AdapterError::InvalidLedgerTransition),
        }
    }

    pub fn complete(
        &self,
        request: &AuthenticatedRequest,
        result: NodeExecutionResult,
        now: LogicalTime,
    ) -> Result<()> {
        if !result.is_definitive()
            || !result.is_compatible_with(request.operation(), request.catalog_generation())
        {
            return Err(AdapterError::ResultOperationMismatch);
        }
        let mut state = self.lock_prepared(now)?;
        let entry = entry_mut(&mut state, request)?;
        match entry.state {
            EntryState::InFlight => {
                entry.state = EntryState::Completed(result);
                Ok(())
            }
            _ => Err(AdapterError::InvalidLedgerTransition),
        }
    }

    pub fn mark_indeterminate(
        &self,
        request: &AuthenticatedRequest,
        reason: IndeterminateReason,
        now: LogicalTime,
    ) -> Result<()> {
        let mut state = self.lock_prepared(now)?;
        let entry = entry_mut(&mut state, request)?;
        match entry.state {
            EntryState::InFlight => {
                entry.state = EntryState::Indeterminate {
                    reason,
                    retention_expired: false,
                };
                Ok(())
            }
            _ => Err(AdapterError::InvalidLedgerTransition),
        }
    }

    pub fn reconcile_completed(
        &self,
        request: &AuthenticatedRequest,
        result: NodeExecutionResult,
        now: LogicalTime,
    ) -> Result<()> {
        if !result.is_definitive()
            || !result.is_compatible_with(request.operation(), request.catalog_generation())
        {
            return Err(AdapterError::ResultOperationMismatch);
        }
        let mut state = self.lock_prepared(now)?;
        let entry = entry_mut(&mut state, request)?;
        match entry.state {
            EntryState::InFlight | EntryState::Indeterminate { .. } | EntryState::Expired => {
                entry.state = EntryState::Completed(result);
                Ok(())
            }
            _ => Err(AdapterError::InvalidLedgerTransition),
        }
    }

    pub fn status(&self, request: &AuthenticatedRequest, now: LogicalTime) -> Result<LedgerStatus> {
        let mut state = self.lock_prepared(now)?;
        let entry = entry_mut(&mut state, request)?;
        Ok(entry.state.status())
    }

    pub fn purge_expired(&self, now: LogicalTime) -> Result<usize> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        Ok(state.expire(now))
    }

    pub fn invalidate_for_restart(&self) -> Result<usize> {
        let mut state = self.lock_state()?;
        let invalidated = state.entries.len();
        state.entries.clear();
        state.operation_high_water = 0;
        state.last_observed = None;
        state.retire_active();
        Ok(invalidated)
    }

    pub fn activate_epoch(&self, epoch: PairingEpoch) -> Result<()> {
        let mut state = self.lock_state()?;
        if state.epoch_history_exhausted {
            return Err(AdapterError::EpochHistoryExhausted);
        }
        if state.active_epoch.is_some() {
            return Err(AdapterError::InvalidLedgerTransition);
        }
        if state.retired_epochs.contains(&epoch) {
            return Err(AdapterError::EpochReuse);
        }
        state.reset_for_epoch(epoch);
        Ok(())
    }

    pub fn rotate_epoch(&self, epoch: PairingEpoch) -> Result<usize> {
        let mut state = self.lock_state()?;
        if state.epoch_history_exhausted {
            return Err(AdapterError::EpochHistoryExhausted);
        }
        let active = state.active_epoch.ok_or(AdapterError::NoActiveEpoch)?;
        if active == epoch || state.retired_epochs.contains(&epoch) {
            return Err(AdapterError::EpochReuse);
        }
        if state.retired_epochs.len() == MAX_RETIRED_EPOCHS {
            return Err(AdapterError::EpochHistoryExhausted);
        }
        let invalidated = state.entries.len();
        state.retired_epochs.insert(active);
        state.reset_for_epoch(epoch);
        Ok(invalidated)
    }

    pub fn diagnostics(&self) -> Result<LedgerDiagnostics> {
        let state = self.lock_state()?;
        let mut diagnostics = LedgerDiagnostics {
            active: state.active_epoch.is_some(),
            retired_epochs: state.retired_epochs.len(),
            epoch_history_exhausted: state.epoch_history_exhausted,
            entries: state.entries.len(),
            reserved: 0,
            in_flight: 0,
            completed: 0,
            indeterminate: 0,
            expired: 0,
        };
        for entry in state.entries.values() {
            match entry.state {
                EntryState::Reserved => diagnostics.reserved += 1,
                EntryState::InFlight => diagnostics.in_flight += 1,
                EntryState::Completed(_) => diagnostics.completed += 1,
                EntryState::Indeterminate { .. } => diagnostics.indeterminate += 1,
                EntryState::Expired => diagnostics.expired += 1,
            }
        }
        Ok(diagnostics)
    }

    fn lock_prepared(&self, now: LogicalTime) -> Result<MutexGuard<'_, State>> {
        let mut state = self.lock_state()?;
        state.observe(now)?;
        state.expire(now);
        Ok(state)
    }

    fn lock_state(&self) -> Result<MutexGuard<'_, State>> {
        self.state.lock().map_err(|_| AdapterError::LockPoisoned)
    }
}

impl fmt::Debug for OperationLedger {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.diagnostics() {
            Ok(diagnostics) => formatter
                .debug_struct("OperationLedger")
                .field("config", &self.config)
                .field("diagnostics", &diagnostics)
                .finish(),
            Err(_) => formatter
                .debug_struct("OperationLedger")
                .field("config", &self.config)
                .field("diagnostics", &"unavailable")
                .finish(),
        }
    }
}

fn entry_mut<'a>(state: &'a mut State, request: &AuthenticatedRequest) -> Result<&'a mut Entry> {
    state.validate_active(request)?;
    let key = OperationKey::from_request(request);
    let entry = state
        .entries
        .get_mut(&key)
        .ok_or(AdapterError::UnknownDuplicate)?;
    if !entry.binding.same_operation_binding(request) {
        return Err(AdapterError::BindingMismatch);
    }
    Ok(entry)
}
