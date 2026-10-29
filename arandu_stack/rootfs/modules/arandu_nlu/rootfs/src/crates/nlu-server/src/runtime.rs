use core::fmt;
use std::sync::Mutex;

use ha_catalog::CatalogSnapshot;
use intent_engine::{IntentEngine, RecognitionOutcome};
use nlu_core::{AbstentionReason, ComposedPlan, InvocationId, LogicalClock, LogicalTime};
use plan_engine::{
    CompositionAbstentionReason, CompositionOutcome, PlanEngine, ResumableCompositionOutcome,
};
use policy_engine::{
    ConfirmationConfig, ConfirmationId as PolicyConfirmationId, ConfirmationRejectionReason,
    PolicyContext, PolicyDecision, PolicyEngine, PolicyGeneration, PolicyTable,
    RiskClass as EngineRiskClass, StoredConfirmationOutcome,
};
use protocol::{
    ProtocolError, RequestVersion, detect_request_version, v1,
    v2::{
        self, CancellationStatus, ConfirmationId as WireConfirmationId, ConfirmationRequired,
        Diagnostic, DiagnosticCode, EntityClarification, Generation, Health, Outcome,
        PolicyAccepted, PolicyDenialReason as WirePolicyDenialReason, Readiness, Request, Response,
        RiskClass as WireRiskClass, SessionId as WireSessionId,
    },
};
use session_engine::{
    CancellationOutcome as SessionCancellationOutcome, ContinuationOutcome, SessionConfig,
    SessionId, SessionStore,
};

use crate::{
    RequestHandler, Result, RuntimeSnapshot, ServerError, ServerErrorCode, SnapshotMetadata,
    SnapshotState,
};

pub struct NluRuntime {
    metadata: SnapshotMetadata,
    catalog: CatalogSnapshot,
    intents: IntentEngine,
    plans: PlanEngine,
    sessions: SessionStore,
    policy: PolicyEngine,
}

impl NluRuntime {
    pub fn standard(
        metadata: SnapshotMetadata,
        catalog: CatalogSnapshot,
        policy_generation: PolicyGeneration,
        session_config: SessionConfig,
        confirmation_config: ConfirmationConfig,
    ) -> Result<Self> {
        let intents = IntentEngine::bundled().map_err(|_| runtime_configuration())?;
        let plans = PlanEngine::bundled().map_err(|_| runtime_configuration())?;
        let table =
            PolicyTable::standard(policy_generation).map_err(|_| runtime_configuration())?;
        let runtime = Self {
            metadata,
            catalog,
            intents,
            plans,
            sessions: SessionStore::new(session_config),
            policy: PolicyEngine::new(table, confirmation_config),
        };
        runtime.validate_snapshot_metadata(metadata)?;
        Ok(runtime)
    }

    #[must_use]
    pub const fn catalog(&self) -> &CatalogSnapshot {
        &self.catalog
    }

    #[must_use]
    pub const fn policy(&self) -> &PolicyEngine {
        &self.policy
    }

    pub fn pending_state_counts(&self) -> Result<(u16, u16)> {
        let sessions = self
            .sessions
            .diagnostics()
            .map_err(|_| request_processing())?
            .pending_sessions();
        let confirmations = self
            .policy
            .diagnostics()
            .map_err(|_| request_processing())?
            .pending_confirmations();
        Ok((sessions, confirmations))
    }

    pub fn dispatch_at(
        snapshot: &RuntimeSnapshot<Self>,
        request: &[u8],
        now: LogicalTime,
    ) -> Result<Vec<u8>> {
        snapshot.ensure_active()?;
        validate_snapshot(snapshot)?;
        snapshot.state().observe_time(now)?;
        match detect_request_version(request) {
            Ok(RequestVersion::V1) => snapshot.state().dispatch_v1(request),
            Ok(RequestVersion::V2) => {
                snapshot
                    .state()
                    .dispatch_v2(snapshot.metadata(), request, now)
            }
            Err(error) => encode_v2_protocol_error(error),
        }
    }

    fn dispatch_v1(&self, request: &[u8]) -> Result<Vec<u8>> {
        let source = match v1::decode_request(request) {
            Ok(source) => source,
            Err(error) => return encode_v1_protocol_error(error),
        };
        let recognition = self
            .intents
            .recognize(&source)
            .map_err(|_| request_processing())?;
        let outcome = match recognition {
            RecognitionOutcome::Match(intent_match) => {
                match self
                    .plans
                    .compose(&source, &intent_match, &self.catalog)
                    .map_err(|_| request_processing())?
                {
                    CompositionOutcome::Plan(plan) => match self
                        .policy
                        .assess(&plan, self.policy_context())
                        .map_err(|_| request_processing())?
                    {
                        PolicyDecision::AllowedWithoutConfirmation(_) => {
                            v1::Outcome::Plan(plan.plan().clone())
                        }
                        PolicyDecision::Denied(_) | PolicyDecision::ConfirmationRequired(_) => {
                            v1::Outcome::Abstention(AbstentionReason::Unsupported)
                        }
                    },
                    CompositionOutcome::EntityClarification(_) => {
                        v1::Outcome::Abstention(AbstentionReason::Ambiguous)
                    }
                    CompositionOutcome::Abstention(reason) => {
                        v1::Outcome::Abstention(map_composition_abstention(reason))
                    }
                }
            }
            RecognitionOutcome::Clarification(_) => {
                v1::Outcome::Abstention(AbstentionReason::Ambiguous)
            }
            RecognitionOutcome::Abstention(_) => {
                v1::Outcome::Abstention(AbstentionReason::InsufficientEvidence)
            }
        };
        v1::encode_outcome(&outcome).map_err(|_| request_processing())
    }

    fn dispatch_v2(
        &self,
        metadata: SnapshotMetadata,
        request: &[u8],
        now: LogicalTime,
    ) -> Result<Vec<u8>> {
        let request = match v2::decode_request(request) {
            Ok(request) => request,
            Err(error) => return encode_v2_protocol_error(error),
        };
        let response = match request {
            Request::Interpret { session_id, text } => self.interpret_v2(session_id, text, now)?,
            Request::Continue {
                session_id,
                selection,
            } => self.continue_v2(session_id, selection, now)?,
            Request::Confirm {
                session_id,
                confirmation_id,
                plan,
            } => self.confirm_v2(session_id, confirmation_id, &plan, now)?,
            Request::Cancel { session_id } => self.cancel_v2(session_id, now)?,
            Request::Health => health_response(metadata)?,
        };
        v2::encode_response(&response).map_err(|_| request_processing())
    }

    fn interpret_v2(
        &self,
        wire_session: WireSessionId,
        text: nlu_core::RequestText,
        now: LogicalTime,
    ) -> Result<Response> {
        let session = engine_session(wire_session);
        let origin = invocation_for(wire_session)?;
        self.clear_addressed_session(&session, &origin, now)?;

        let recognition = self
            .intents
            .recognize(&text)
            .map_err(|_| request_processing())?;
        match recognition {
            RecognitionOutcome::Match(intent_match) => {
                match self
                    .plans
                    .compose_resumable(&text, &intent_match, &self.catalog)
                    .map_err(|_| request_processing())?
                {
                    ResumableCompositionOutcome::Plan(plan) => {
                        self.evaluate_plan(plan, wire_session, &session, now)
                    }
                    ResumableCompositionOutcome::Pending(pending) => {
                        let referents = pending.candidates().to_vec();
                        self.sessions
                            .begin(session, origin, pending, now)
                            .map_err(|_| request_processing())?;
                        let clarification = EntityClarification::new(wire_session, referents)
                            .map_err(|_| request_processing())?;
                        Ok(Response::without_diagnostics(Outcome::EntityClarification(
                            clarification,
                        )))
                    }
                    ResumableCompositionOutcome::Abstention(reason) => {
                        abstention_response(map_composition_abstention(reason))
                    }
                }
            }
            RecognitionOutcome::Clarification(_) => {
                abstention_response(AbstentionReason::Ambiguous)
            }
            RecognitionOutcome::Abstention(_) => {
                abstention_response(AbstentionReason::InsufficientEvidence)
            }
        }
    }

    fn continue_v2(
        &self,
        wire_session: WireSessionId,
        selection: nlu_core::EntityRef,
        now: LogicalTime,
    ) -> Result<Response> {
        let session = engine_session(wire_session);
        match self
            .sessions
            .continue_with_selection(&session, selection, self.catalog.generation(), now)
            .map_err(|_| request_processing())?
        {
            ContinuationOutcome::Completed(plan) => {
                self.evaluate_plan(plan, wire_session, &session, now)
            }
            ContinuationOutcome::Unavailable => response_with_diagnostic(
                Outcome::Abstention(AbstentionReason::Unsupported),
                DiagnosticCode::SessionUnavailable,
            ),
        }
    }

    fn confirm_v2(
        &self,
        wire_session: WireSessionId,
        wire_confirmation: WireConfirmationId,
        addressed_plan: &ComposedPlan,
        now: LogicalTime,
    ) -> Result<Response> {
        let session = engine_session(wire_session);
        let confirmation_id =
            PolicyConfirmationId::new(wire_confirmation.get()).map_err(|_| request_processing())?;
        match self
            .policy
            .confirm_stored(
                &session,
                confirmation_id,
                addressed_plan,
                self.policy_context(),
                now,
            )
            .map_err(|_| request_processing())?
        {
            StoredConfirmationOutcome::Accepted(accepted) => {
                let (plan, acceptance) = accepted.into_parts();
                let outcome = PolicyAccepted::new(plan, map_risk(acceptance.risk()));
                Ok(Response::without_diagnostics(Outcome::PolicyAccepted(
                    outcome,
                )))
            }
            StoredConfirmationOutcome::Rejected(rejection) => match rejection.reason() {
                ConfirmationRejectionReason::Unavailable => response_with_diagnostic(
                    Outcome::PolicyDenial(WirePolicyDenialReason::ConfirmationMismatch),
                    DiagnosticCode::ConfirmationUnavailable,
                ),
                ConfirmationRejectionReason::Expired => response_with_diagnostic(
                    Outcome::PolicyDenial(WirePolicyDenialReason::ConfirmationMismatch),
                    DiagnosticCode::ConfirmationExpired,
                ),
                ConfirmationRejectionReason::BindingMismatch => response_with_diagnostic(
                    Outcome::PolicyDenial(WirePolicyDenialReason::ConfirmationMismatch),
                    DiagnosticCode::PolicyDenied,
                ),
            },
        }
    }

    fn cancel_v2(&self, wire_session: WireSessionId, now: LogicalTime) -> Result<Response> {
        let session = engine_session(wire_session);
        let origin = invocation_for(wire_session)?;
        let session_result = self.sessions.cancel(&session, &origin, now);
        let confirmation_result = self.policy.cancel(&session, now);
        let session_cancelled = session_result.map_err(|_| request_processing())?
            == SessionCancellationOutcome::Cancelled;
        let confirmation_cancelled = confirmation_result.map_err(|_| request_processing())?
            == policy_engine::CancellationOutcome::Cancelled;
        let status = if session_cancelled || confirmation_cancelled {
            CancellationStatus::Cancelled
        } else {
            CancellationStatus::Unavailable
        };
        Ok(Response::without_diagnostics(Outcome::Cancellation(status)))
    }

    fn clear_addressed_session(
        &self,
        session: &SessionId,
        origin: &InvocationId,
        now: LogicalTime,
    ) -> Result<()> {
        let session_result = self.sessions.cancel(session, origin, now);
        let confirmation_result = self.policy.cancel(session, now);
        session_result.map_err(|_| request_processing())?;
        confirmation_result.map_err(|_| request_processing())?;
        Ok(())
    }

    fn observe_time(&self, now: LogicalTime) -> Result<()> {
        let session_result = self.sessions.purge_expired(now);
        let policy_result = self.policy.purge_expired(now);
        session_result.map_err(|_| request_processing())?;
        policy_result.map_err(|_| request_processing())?;
        Ok(())
    }

    fn evaluate_plan(
        &self,
        plan: ComposedPlan,
        wire_session: WireSessionId,
        session: &SessionId,
        now: LogicalTime,
    ) -> Result<Response> {
        match self
            .policy
            .evaluate(&plan, session, self.policy_context(), now)
            .map_err(|_| request_processing())?
        {
            PolicyDecision::Denied(denial) => {
                let (reason, diagnostic) = map_policy_denial(denial.reason());
                response_with_diagnostic(Outcome::PolicyDenial(reason), diagnostic)
            }
            PolicyDecision::AllowedWithoutConfirmation(acceptance) => {
                let accepted = PolicyAccepted::new(plan, map_risk(acceptance.risk()));
                Ok(Response::without_diagnostics(Outcome::PolicyAccepted(
                    accepted,
                )))
            }
            PolicyDecision::ConfirmationRequired(requirement) => {
                let issued = requirement
                    .confirmation_id()
                    .ok_or_else(request_processing)?;
                let confirmation_id =
                    WireConfirmationId::new(issued.get()).map_err(|_| request_processing())?;
                let required = ConfirmationRequired::new(
                    wire_session,
                    confirmation_id,
                    map_risk(requirement.risk()),
                    plan,
                );
                Ok(Response::without_diagnostics(
                    Outcome::ConfirmationRequired(required),
                ))
            }
        }
    }

    fn policy_context(&self) -> PolicyContext {
        PolicyContext::new(self.catalog.generation(), self.policy.table().generation())
    }
}

impl SnapshotState for NluRuntime {
    fn validate_snapshot_metadata(&self, metadata: SnapshotMetadata) -> Result<()> {
        if self.metadata != metadata
            || self.catalog.generation().get() != metadata.catalog_generation()
            || self.policy.table().generation().get() != metadata.policy_generation()
        {
            return Err(ServerError::new(ServerErrorCode::RuntimeState));
        }
        Ok(())
    }

    fn prepare_successor(&self, successor: &Self) -> Result<()> {
        successor
            .policy
            .inherit_confirmation_sequence(&self.policy)
            .map_err(|_| runtime_state())
    }

    fn invalidate_for_reload(&self, now: LogicalTime) -> Result<()> {
        let session_result = self.sessions.invalidate_for_reload(now);
        let policy_result = self.policy.invalidate_for_reload(now);
        session_result.map_err(|_| request_processing())?;
        policy_result.map_err(|_| request_processing())?;
        Ok(())
    }
}

impl fmt::Debug for NluRuntime {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NluRuntime")
            .field("catalog_generation", &self.catalog.generation().get())
            .field("policy_generation", &self.policy.table().generation().get())
            .field("residential_data", &"redacted")
            .finish()
    }
}

pub struct NluRequestHandler<C> {
    clock: C,
    request_order: Mutex<()>,
}

impl<C> NluRequestHandler<C> {
    #[must_use]
    pub const fn new(clock: C) -> Self {
        Self {
            clock,
            request_order: Mutex::new(()),
        }
    }
}

impl<C> RequestHandler<NluRuntime> for NluRequestHandler<C>
where
    C: LogicalClock + Send + Sync + 'static,
{
    fn handle(&self, snapshot: &RuntimeSnapshot<NluRuntime>, request: &[u8]) -> Result<Vec<u8>> {
        // Keep the clock sample ordered with every state observation that consumes it.
        let _request_order = self
            .request_order
            .lock()
            .map_err(|_| ServerError::new(ServerErrorCode::LockPoisoned))?;
        NluRuntime::dispatch_at(snapshot, request, self.clock.now())
    }
}

impl<C> fmt::Debug for NluRequestHandler<C> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NluRequestHandler { clock: injected }")
    }
}

fn validate_snapshot(snapshot: &RuntimeSnapshot<NluRuntime>) -> Result<()> {
    snapshot
        .state()
        .validate_snapshot_metadata(snapshot.metadata())
}

fn health_response(metadata: SnapshotMetadata) -> Result<Response> {
    let catalog = Generation::new(metadata.catalog_generation()).map_err(|_| runtime_state())?;
    let policy = Generation::new(metadata.policy_generation()).map_err(|_| runtime_state())?;
    let configuration =
        Generation::new(metadata.configuration_generation()).map_err(|_| runtime_state())?;
    Ok(Response::without_diagnostics(Outcome::Health(Health::new(
        Readiness::Ready,
        Some(catalog),
        Some(policy),
        Some(configuration),
    ))))
}

fn abstention_response(reason: AbstentionReason) -> Result<Response> {
    let diagnostic = match reason {
        AbstentionReason::InsufficientEvidence => DiagnosticCode::InsufficientEvidence,
        AbstentionReason::Ambiguous => DiagnosticCode::Ambiguous,
        AbstentionReason::Unsupported => DiagnosticCode::Unsupported,
    };
    response_with_diagnostic(Outcome::Abstention(reason), diagnostic)
}

fn response_with_diagnostic(outcome: Outcome, code: DiagnosticCode) -> Result<Response> {
    Response::new(outcome, vec![Diagnostic::new(code, None)]).map_err(|_| request_processing())
}

const fn map_composition_abstention(reason: CompositionAbstentionReason) -> AbstentionReason {
    match reason {
        CompositionAbstentionReason::IncompleteClause
        | CompositionAbstentionReason::EntityResolution => AbstentionReason::InsufficientEvidence,
        CompositionAbstentionReason::MultipleEntityClarifications => AbstentionReason::Ambiguous,
        CompositionAbstentionReason::UnsupportedIntent
        | CompositionAbstentionReason::UnsupportedPattern
        | CompositionAbstentionReason::NegationScope
        | CompositionAbstentionReason::StaleCatalogGeneration
        | CompositionAbstentionReason::SemanticConflict => AbstentionReason::Unsupported,
    }
}

const fn map_risk(risk: EngineRiskClass) -> WireRiskClass {
    match risk {
        EngineRiskClass::Observation => WireRiskClass::ReadOnly,
        EngineRiskClass::LocalControl => WireRiskClass::Routine,
        EngineRiskClass::StateChange => WireRiskClass::Sensitive,
        EngineRiskClass::Sensitive => WireRiskClass::Critical,
    }
}

const fn map_policy_denial(
    reason: policy_engine::PolicyDenialReason,
) -> (WirePolicyDenialReason, DiagnosticCode) {
    match reason {
        policy_engine::PolicyDenialReason::MissingRule => (
            WirePolicyDenialReason::MissingRule,
            DiagnosticCode::PolicyRuleMissing,
        ),
        policy_engine::PolicyDenialReason::ExplicitDeny
        | policy_engine::PolicyDenialReason::SlotContractMismatch => (
            WirePolicyDenialReason::ExplicitDeny,
            DiagnosticCode::PolicyDenied,
        ),
        policy_engine::PolicyDenialReason::UnsupportedGraphClass
        | policy_engine::PolicyDenialReason::AtomicOnly => (
            WirePolicyDenialReason::UnsupportedGraphClass,
            DiagnosticCode::UnsupportedGraphClass,
        ),
        policy_engine::PolicyDenialReason::NonExecutable
        | policy_engine::PolicyDenialReason::NegatedNode => (
            WirePolicyDenialReason::NonExecutable,
            DiagnosticCode::PolicyDenied,
        ),
        policy_engine::PolicyDenialReason::StaleCatalogGeneration => (
            WirePolicyDenialReason::StaleCatalogGeneration,
            DiagnosticCode::StaleCatalogGeneration,
        ),
        policy_engine::PolicyDenialReason::StalePolicyGeneration => (
            WirePolicyDenialReason::StalePolicyGeneration,
            DiagnosticCode::PolicyDenied,
        ),
        policy_engine::PolicyDenialReason::ContradictoryPlan => (
            WirePolicyDenialReason::Contradiction,
            DiagnosticCode::PolicyDenied,
        ),
    }
}

fn engine_session(session: WireSessionId) -> SessionId {
    SessionId::from_bytes(*session.as_bytes())
}

fn invocation_for(session: WireSessionId) -> Result<InvocationId> {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut local = [0_u8; v2::SESSION_ID_BYTES * 2];
    for (index, byte) in session.as_bytes().iter().copied().enumerate() {
        local[index * 2] = HEX[usize::from(byte >> 4)];
        local[index * 2 + 1] = HEX[usize::from(byte & 0x0f)];
    }
    let local = core::str::from_utf8(&local).map_err(|_| request_processing())?;
    InvocationId::new(&format!("p13:s_{local}")).map_err(|_| request_processing())
}

fn encode_v1_protocol_error(error: ProtocolError) -> Result<Vec<u8>> {
    v1::encode_protocol_error(error).map_err(|_| request_processing())
}

fn encode_v2_protocol_error(error: ProtocolError) -> Result<Vec<u8>> {
    v2::encode_protocol_error(error).map_err(|_| request_processing())
}

const fn runtime_configuration() -> ServerError {
    ServerError::new(ServerErrorCode::RuntimeConfiguration)
}

const fn runtime_state() -> ServerError {
    ServerError::new(ServerErrorCode::RuntimeState)
}

const fn request_processing() -> ServerError {
    ServerError::new(ServerErrorCode::RequestProcessing)
}
