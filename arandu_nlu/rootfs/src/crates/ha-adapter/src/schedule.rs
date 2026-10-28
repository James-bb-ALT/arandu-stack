use core::fmt;
use std::collections::BTreeSet;

use crate::{
    AdapterError, AuthenticatedRequest, ExecutionFailure, ExecutionRenderOutcome,
    IndeterminateReason, LogicalTime, NodeExecutionResult, OperationLedger, ReservationOutcome,
    Result, RevalidationFailure,
};

pub const MAX_EXECUTION_NODES: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RevalidationDecision {
    Allow,
    Deny(RevalidationFailure),
}

pub trait NodeRevalidator {
    fn revalidate(
        &mut self,
        request: &AuthenticatedRequest,
        prior_results: &[NodeExecutionResult],
    ) -> RevalidationDecision;
}

pub trait NodeExecutor {
    fn execute(&mut self, request: &AuthenticatedRequest) -> NodeExecutionResult;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ScheduleState {
    Unprepared,
    Ready { next_node: u16 },
    Completed,
    Stopped,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ScheduleProgress {
    node_index: u16,
    result: NodeExecutionResult,
    state: ScheduleState,
}

impl ScheduleProgress {
    #[must_use]
    pub const fn node_index(&self) -> u16 {
        self.node_index
    }

    #[must_use]
    pub const fn result(&self) -> &NodeExecutionResult {
        &self.result
    }

    #[must_use]
    pub const fn state(&self) -> ScheduleState {
        self.state
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionReport {
    state: ScheduleState,
    results: Vec<NodeExecutionResult>,
}

impl ExecutionReport {
    #[must_use]
    pub const fn state(&self) -> ScheduleState {
        self.state
    }

    #[must_use]
    pub fn results(&self) -> &[NodeExecutionResult] {
        &self.results
    }

    #[must_use]
    pub fn render_outcome(&self) -> ExecutionRenderOutcome {
        let completed_nodes = self
            .results
            .iter()
            .filter(|result| matches!(result, NodeExecutionResult::Succeeded(_)))
            .count() as u16;
        match self.state {
            ScheduleState::Completed => ExecutionRenderOutcome::Completed {
                node_count: completed_nodes,
            },
            ScheduleState::Stopped => match self.results.last() {
                Some(NodeExecutionResult::Indeterminate(_)) => {
                    ExecutionRenderOutcome::Indeterminate { completed_nodes }
                }
                Some(NodeExecutionResult::Failed(failure)) => ExecutionRenderOutcome::Stopped {
                    completed_nodes,
                    failure: *failure,
                },
                _ => ExecutionRenderOutcome::NotAttempted,
            },
            ScheduleState::Unprepared => ExecutionRenderOutcome::NotAttempted,
            ScheduleState::Ready { .. } => ExecutionRenderOutcome::InProgress { completed_nodes },
        }
    }
}

pub struct ExecutionSchedule {
    requests: Vec<AuthenticatedRequest>,
    reservations: Option<Vec<ReservationOutcome>>,
    results: Vec<NodeExecutionResult>,
    next: usize,
    state: ScheduleState,
}

impl ExecutionSchedule {
    pub fn new(requests: Vec<AuthenticatedRequest>) -> Result<Self> {
        if requests.is_empty() {
            return Err(AdapterError::EmptySchedule);
        }
        if requests.len() > MAX_EXECUTION_NODES {
            return Err(AdapterError::TooManyScheduleNodes);
        }
        validate_schedule_bindings(&requests)?;
        Ok(Self {
            requests,
            reservations: None,
            results: Vec::new(),
            next: 0,
            state: ScheduleState::Unprepared,
        })
    }

    #[must_use]
    pub const fn state(&self) -> ScheduleState {
        self.state
    }

    pub fn prepare(&mut self, ledger: &OperationLedger, now: LogicalTime) -> Result<()> {
        if self.state != ScheduleState::Unprepared {
            return Err(AdapterError::InvalidLedgerTransition);
        }
        let reservations = ledger.reserve_batch(&self.requests, now)?;
        self.reservations = Some(reservations);
        self.state = ScheduleState::Ready { next_node: 0 };
        Ok(())
    }

    pub fn advance<R, E>(
        &mut self,
        ledger: &OperationLedger,
        now: LogicalTime,
        revalidator: &mut R,
        executor: &mut E,
    ) -> Result<ScheduleProgress>
    where
        R: NodeRevalidator,
        E: NodeExecutor,
    {
        match self.state {
            ScheduleState::Unprepared => return Err(AdapterError::ScheduleNotPrepared),
            ScheduleState::Completed | ScheduleState::Stopped => {
                return Err(AdapterError::ScheduleFinished);
            }
            ScheduleState::Ready { .. } => {}
        }

        let request = &self.requests[self.next];
        let reservation = self
            .reservations
            .as_ref()
            .and_then(|reservations| reservations.get(self.next))
            .cloned()
            .ok_or(AdapterError::ScheduleNotPrepared)?;

        let result = match reservation {
            ReservationOutcome::Reserved => match revalidator.revalidate(request, &self.results) {
                RevalidationDecision::Deny(reason) => {
                    let result =
                        NodeExecutionResult::Failed(ExecutionFailure::Revalidation(reason));
                    ledger.reject_reserved(request, result.clone(), now)?;
                    result
                }
                RevalidationDecision::Allow => {
                    ledger.begin(request, now)?;
                    let result = executor.execute(request);
                    self.finish_dispatched(ledger, request, result, now)
                }
            },
            ReservationOutcome::DuplicateReserved => {
                NodeExecutionResult::Indeterminate(IndeterminateReason::DuplicateReserved)
            }
            ReservationOutcome::DuplicateInFlight => {
                NodeExecutionResult::Indeterminate(IndeterminateReason::DuplicateInFlight)
            }
            ReservationOutcome::Cached(result) => result,
            ReservationOutcome::Indeterminate(reason) => NodeExecutionResult::Indeterminate(reason),
            ReservationOutcome::Expired => NodeExecutionResult::Failed(ExecutionFailure::Expired),
        };

        Ok(self.record(result))
    }

    #[must_use]
    pub fn report(&self) -> ExecutionReport {
        ExecutionReport {
            state: self.state,
            results: self.results.clone(),
        }
    }

    fn finish_dispatched(
        &self,
        ledger: &OperationLedger,
        request: &AuthenticatedRequest,
        result: NodeExecutionResult,
        now: LogicalTime,
    ) -> NodeExecutionResult {
        if !result.is_compatible_with(request.operation(), request.catalog_generation()) {
            let _ =
                ledger.mark_indeterminate(request, IndeterminateReason::InvalidExecutorResult, now);
            return NodeExecutionResult::Indeterminate(IndeterminateReason::InvalidExecutorResult);
        }
        match result {
            NodeExecutionResult::Indeterminate(reason) => {
                let _ = ledger.mark_indeterminate(request, reason, now);
                NodeExecutionResult::Indeterminate(reason)
            }
            definitive => {
                if ledger.complete(request, definitive.clone(), now).is_ok() {
                    definitive
                } else {
                    let _ =
                        ledger.mark_indeterminate(request, IndeterminateReason::LostResponse, now);
                    NodeExecutionResult::Indeterminate(IndeterminateReason::LostResponse)
                }
            }
        }
    }

    fn record(&mut self, result: NodeExecutionResult) -> ScheduleProgress {
        let node_index = self.next as u16;
        let stop = result.is_terminal_stop();
        self.results.push(result.clone());
        self.next += 1;
        self.state = if stop {
            ScheduleState::Stopped
        } else if self.next == self.requests.len() {
            ScheduleState::Completed
        } else {
            ScheduleState::Ready {
                next_node: self.next as u16,
            }
        };
        ScheduleProgress {
            node_index,
            result,
            state: self.state,
        }
    }
}

impl fmt::Debug for ExecutionSchedule {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExecutionSchedule")
            .field("node_count", &self.requests.len())
            .field("completed_result_count", &self.results.len())
            .field("state", &self.state)
            .finish()
    }
}

fn validate_schedule_bindings(requests: &[AuthenticatedRequest]) -> Result<()> {
    let first = &requests[0];
    let mut attempts = BTreeSet::new();
    for (index, request) in requests.iter().enumerate() {
        if !first.same_graph_binding(request) {
            return Err(AdapterError::ScheduleBindingMismatch);
        }
        if !attempts.insert(request.node_attempt_id()) {
            return Err(AdapterError::DuplicateNodeAttempt);
        }
        let expected = first
            .sequence()
            .get()
            .checked_add(index as u64)
            .ok_or(AdapterError::ScheduleBindingMismatch)?;
        if request.sequence().get() != expected {
            return Err(AdapterError::ScheduleBindingMismatch);
        }
    }
    Ok(())
}
