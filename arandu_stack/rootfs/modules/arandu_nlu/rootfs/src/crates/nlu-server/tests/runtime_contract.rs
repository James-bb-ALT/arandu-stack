use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

use ha_catalog::{
    AliasProvenance, CapabilityDescriptorInput, CatalogSnapshot, CatalogSnapshotInput, Domain,
    EntityInput, EntityInputParts, EntityVisibility, ExplicitAlias, ExternalEntityId,
    RegistryEntryId, SensitiveText,
};
use nlu_core::{
    CapabilityId, CatalogGeneration, ComposedPlan, LogicalClock, LogicalTime, RequestText,
    SlotValue,
};
use nlu_server::{
    NluRequestHandler, NluRuntime, RequestHandler, RuntimeSnapshot, ServerConfig, ServerErrorCode,
    SnapshotMetadata, SnapshotStore, UnixServer, read_frame, write_frame,
};
use policy_engine::{ConfirmationConfig, PolicyGeneration};
use protocol::{
    ProtocolError, v1,
    v2::{
        self, CancellationStatus, ConfirmationId, DiagnosticCode, Outcome, PolicyDenialReason,
        Request, SessionId,
    },
};
use session_engine::SessionConfig;

const ADMITTED_REQUEST: &str = "desligue interruptor 1 do setor sala";
const ADMITTED_ALIAS: &str = "interruptor 1 do setor sala";
const SANITIZED_TEST_ENVIRONMENT: [(&str, &str); 7] = [
    ("HOME", "/nonexistent"),
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("PATH", "/usr/bin:/bin"),
    ("RUST_BACKTRACE", "0"),
    ("TMPDIR", "/tmp"),
    ("TZ", "UTC"),
];

#[derive(Clone, Copy)]
struct FixedClock;

impl LogicalClock for FixedClock {
    fn now(&self) -> LogicalTime {
        LogicalTime::from_ticks(1)
    }
}

#[derive(Clone)]
struct OverlappingClock {
    state: Arc<(Mutex<OverlappingClockState>, Condvar)>,
}

struct OverlappingClockState {
    samples: u8,
    later_request_finished: bool,
}

impl OverlappingClock {
    fn new() -> Self {
        Self {
            state: Arc::new((
                Mutex::new(OverlappingClockState {
                    samples: 0,
                    later_request_finished: false,
                }),
                Condvar::new(),
            )),
        }
    }

    fn wait_for_first_sample(&self) {
        let (state, changed) = &*self.state;
        let state = state.lock().expect("FIXTURE_TECNICA overlap clock");
        drop(
            changed
                .wait_while(state, |state| state.samples == 0)
                .expect("FIXTURE_TECNICA first clock sample"),
        );
    }

    fn mark_later_request_finished(&self) {
        let (state, changed) = &*self.state;
        let mut state = state.lock().expect("FIXTURE_TECNICA overlap clock");
        state.later_request_finished = true;
        changed.notify_all();
    }
}

impl LogicalClock for OverlappingClock {
    fn now(&self) -> LogicalTime {
        let (state, changed) = &*self.state;
        let mut state = state.lock().expect("FIXTURE_TECNICA overlap clock");
        let sample = state.samples;
        state.samples = state
            .samples
            .checked_add(1)
            .expect("FIXTURE_TECNICA bounded clock samples");
        changed.notify_all();
        if sample == 0 {
            let (next, _) = changed
                .wait_timeout_while(state, Duration::from_secs(1), |state| {
                    !state.later_request_finished
                })
                .expect("FIXTURE_TECNICA overlapping request");
            drop(next);
            LogicalTime::from_ticks(10)
        } else {
            assert_eq!(sample, 1, "FIXTURE_TECNICA exactly two clock samples");
            LogicalTime::from_ticks(11)
        }
    }
}

struct SequentialRollbackClock {
    samples: AtomicU8,
}

impl SequentialRollbackClock {
    const fn new() -> Self {
        Self {
            samples: AtomicU8::new(0),
        }
    }
}

impl LogicalClock for SequentialRollbackClock {
    fn now(&self) -> LogicalTime {
        match self.samples.fetch_add(1, Ordering::AcqRel) {
            0 => LogicalTime::from_ticks(11),
            1 => LogicalTime::from_ticks(10),
            _ => panic!("FIXTURE_TECNICA exactly two rollback clock samples"),
        }
    }
}

fn generation(value: u64) -> CatalogGeneration {
    CatalogGeneration::new(value).expect("FIXTURE_TECNICA catalog generation")
}

fn session(value: u8) -> SessionId {
    let mut bytes = [0_u8; v2::SESSION_ID_BYTES];
    bytes[0] = value;
    SessionId::from_bytes(bytes)
}

fn catalog_at(entity_count: usize, generation_value: u64) -> CatalogSnapshot {
    let capability = CapabilityId::new("ha:switch_control").expect("FIXTURE_TECNICA capability");
    let domain = Domain::new("switch").expect("FIXTURE_TECNICA domain");
    let entities = (1..=entity_count)
        .map(|index| {
            EntityInput::new(EntityInputParts {
                generation: generation(generation_value),
                registry_id: RegistryEntryId::new(&format!("{index:032x}"))
                    .expect("FIXTURE_TECNICA registry ID"),
                external_id: ExternalEntityId::new(&format!("switch.fixture_tecnica_{index}"))
                    .expect("FIXTURE_TECNICA external ID"),
                domain: domain.clone(),
                display_name: SensitiveText::new(format!("FIXTURE_TECNICA_DISPLAY_{index}"))
                    .expect("FIXTURE_TECNICA display"),
                aliases: vec![ExplicitAlias::new(
                    SensitiveText::new(ADMITTED_ALIAS).expect("admitted alias"),
                    AliasProvenance::EntityRegistry,
                )],
                capabilities: vec![capability.clone()],
                area_id: None,
                floor_id: None,
                device_id: None,
                visibility: EntityVisibility::exposed(),
            })
        })
        .collect();
    CatalogSnapshot::build(CatalogSnapshotInput {
        generation: generation(generation_value),
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors: vec![CapabilityDescriptorInput {
            id: capability,
            enabled: true,
            state_query_domains: Vec::new(),
        }],
        entities,
    })
    .expect("FIXTURE_TECNICA catalog")
}

fn catalog(entity_count: usize) -> CatalogSnapshot {
    catalog_at(entity_count, 1)
}

fn metadata(catalog_generation: u64, policy_generation: u64) -> SnapshotMetadata {
    metadata_at(1, catalog_generation, policy_generation)
}

fn metadata_at(
    runtime_generation: u64,
    catalog_generation: u64,
    policy_generation: u64,
) -> SnapshotMetadata {
    SnapshotMetadata::new(
        runtime_generation,
        catalog_generation,
        policy_generation,
        1,
        1,
    )
    .expect("FIXTURE_TECNICA metadata")
}

fn runtime(entity_count: usize) -> Arc<RuntimeSnapshot<NluRuntime>> {
    let metadata = metadata(1, 1);
    let runtime = NluRuntime::standard(
        metadata,
        catalog(entity_count),
        PolicyGeneration::new(1).expect("FIXTURE_TECNICA policy generation"),
        SessionConfig::new(10).expect("FIXTURE_TECNICA session config"),
        ConfirmationConfig::new(10).expect("FIXTURE_TECNICA confirmation config"),
    )
    .expect("FIXTURE_TECNICA runtime");
    Arc::new(RuntimeSnapshot::new(metadata, runtime))
}

fn source() -> RequestText {
    RequestText::new(ADMITTED_REQUEST.to_owned()).expect("admitted request")
}

fn dispatch_v2(
    runtime: &RuntimeSnapshot<NluRuntime>,
    request: &Request,
    ticks: u64,
    response_source: &RequestText,
) -> v2::Response {
    let request = v2::encode_request(request).expect("FIXTURE_TECNICA request encoding");
    let response = NluRuntime::dispatch_at(runtime, &request, LogicalTime::from_ticks(ticks))
        .expect("FIXTURE_TECNICA dispatch");
    v2::decode_response(&response, response_source).expect("FIXTURE_TECNICA response decoding")
}

fn confirmation_id(response: &v2::Response) -> ConfirmationId {
    let Outcome::ConfirmationRequired(required) = response.outcome() else {
        panic!("FIXTURE_TECNICA expected confirmation requirement");
    };
    required.confirmation_id()
}

fn confirmation_plan(response: &v2::Response) -> ComposedPlan {
    let Outcome::ConfirmationRequired(required) = response.outcome() else {
        panic!("FIXTURE_TECNICA expected addressed confirmation plan");
    };
    required.plan().clone()
}

#[test]
fn v2_interpret_confirm_replay_health_and_v1_share_the_plan_semantics() {
    let runtime = runtime(1);
    let source = source();
    let addressed = session(1);
    let interpreted = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: addressed,
            text: source.clone(),
        },
        1,
        &source,
    );
    let confirmation_id = confirmation_id(&interpreted);
    let confirmation_plan = confirmation_plan(&interpreted);
    assert!(matches!(
        interpreted.outcome(),
        Outcome::ConfirmationRequired(required)
            if required.session_id() == addressed && !required.risk().eq(&v2::RiskClass::ReadOnly)
    ));

    let confirmed = dispatch_v2(
        &runtime,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id,
            plan: confirmation_plan.clone(),
        },
        2,
        &source,
    );
    let Outcome::PolicyAccepted(accepted) = confirmed.outcome() else {
        panic!("FIXTURE_TECNICA confirmation must return the stored plan");
    };
    assert!(!accepted.authorizes_execution());
    assert_eq!(
        accepted.plan().plan().nodes()[0].operation().as_str(),
        "ha:turn_off"
    );

    let replay = dispatch_v2(
        &runtime,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id,
            plan: confirmation_plan,
        },
        3,
        &source,
    );
    assert_eq!(
        replay.outcome(),
        &Outcome::PolicyDenial(PolicyDenialReason::ConfirmationMismatch)
    );
    assert_eq!(
        replay.diagnostics()[0].code(),
        DiagnosticCode::ConfirmationUnavailable
    );

    let health = dispatch_v2(&runtime, &Request::Health, 3, &source);
    let Outcome::Health(health) = health.outcome() else {
        panic!("FIXTURE_TECNICA health response");
    };
    assert_eq!(health.supported_versions(), &[1, 2]);
    assert_eq!(
        health.catalog_generation().map(v2::Generation::get),
        Some(1)
    );
    assert_eq!(health.policy_generation().map(v2::Generation::get), Some(1));

    let v1_request = v1::encode_request(&source).expect("FIXTURE_TECNICA v1 request");
    let v1_response = NluRuntime::dispatch_at(&runtime, &v1_request, LogicalTime::from_ticks(3))
        .expect("FIXTURE_TECNICA v1 dispatch");
    assert_eq!(
        v1::decode_outcome(&v1_response, &source).expect("FIXTURE_TECNICA v1 response"),
        v1::Outcome::Abstention(nlu_core::AbstentionReason::Unsupported)
    );
}

#[test]
fn stale_confirmation_prompt_cannot_accept_a_same_session_replacement() {
    let runtime = runtime(1);
    let source = source();
    let addressed = session(61);
    let first = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: addressed,
            text: source.clone(),
        },
        1,
        &source,
    );
    let stale_id = confirmation_id(&first);
    let stale_plan = confirmation_plan(&first);
    let replacement = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: addressed,
            text: source.clone(),
        },
        2,
        &source,
    );
    let replacement_id = confirmation_id(&replacement);
    let replacement_plan = confirmation_plan(&replacement);
    assert_ne!(stale_id, replacement_id);

    let stale = dispatch_v2(
        &runtime,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id: stale_id,
            plan: stale_plan,
        },
        3,
        &source,
    );
    assert_eq!(
        stale.outcome(),
        &Outcome::PolicyDenial(PolicyDenialReason::ConfirmationMismatch)
    );
    assert_eq!(stale.diagnostics()[0].code(), DiagnosticCode::PolicyDenied);

    let replacement_after_mismatch = dispatch_v2(
        &runtime,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id: replacement_id,
            plan: replacement_plan,
        },
        4,
        &source,
    );
    assert_eq!(
        replacement_after_mismatch.diagnostics()[0].code(),
        DiagnosticCode::ConfirmationUnavailable
    );
}

#[test]
fn stale_pre_reload_prompt_cannot_confirm_a_post_reload_plan() {
    let initial = runtime(1);
    let store = SnapshotStore::new(Arc::clone(&initial)).expect("FIXTURE_TECNICA snapshot store");
    let source = source();
    let addressed = session(62);
    let stale = dispatch_v2(
        &initial,
        &Request::Interpret {
            session_id: addressed,
            text: source.clone(),
        },
        1,
        &source,
    );
    let stale_id = confirmation_id(&stale);
    let stale_plan = confirmation_plan(&stale);

    let next_metadata = metadata_at(2, 1, 2);
    let next_runtime = NluRuntime::standard(
        next_metadata,
        catalog(1),
        PolicyGeneration::new(2).expect("FIXTURE_TECNICA policy generation"),
        SessionConfig::new(10).expect("FIXTURE_TECNICA session config"),
        ConfirmationConfig::new(10).expect("FIXTURE_TECNICA confirmation config"),
    )
    .expect("FIXTURE_TECNICA successor runtime");
    let next = Arc::new(RuntimeSnapshot::new(next_metadata, next_runtime));
    store
        .replace_at(Arc::clone(&next), LogicalTime::from_ticks(2))
        .expect("FIXTURE_TECNICA reload");

    let replacement = dispatch_v2(
        &next,
        &Request::Interpret {
            session_id: addressed,
            text: source.clone(),
        },
        3,
        &source,
    );
    let replacement_id = confirmation_id(&replacement);
    let replacement_plan = confirmation_plan(&replacement);
    assert_ne!(stale_id, replacement_id);

    let stale_result = dispatch_v2(
        &next,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id: stale_id,
            plan: stale_plan,
        },
        4,
        &source,
    );
    assert_eq!(
        stale_result.outcome(),
        &Outcome::PolicyDenial(PolicyDenialReason::ConfirmationMismatch)
    );
    assert_eq!(
        stale_result.diagnostics()[0].code(),
        DiagnosticCode::PolicyDenied
    );

    let replacement_after_mismatch = dispatch_v2(
        &next,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id: replacement_id,
            plan: replacement_plan,
        },
        5,
        &source,
    );
    assert_eq!(
        replacement_after_mismatch.diagnostics()[0].code(),
        DiagnosticCode::ConfirmationUnavailable
    );
}

#[test]
fn credential_canary_is_rejected_without_runtime_state_retention() {
    if std::env::var_os("HOME").as_deref() != Some(std::ffi::OsStr::new("/nonexistent")) {
        let status = Command::new(std::env::current_exe().expect("FIXTURE_TECNICA test binary"))
            .arg("--exact")
            .arg("credential_canary_is_rejected_without_runtime_state_retention")
            .arg("--nocapture")
            .env_clear()
            .envs(SANITIZED_TEST_ENVIRONMENT)
            .status()
            .expect("FIXTURE_TECNICA sanitized runtime child");
        assert!(status.success(), "sanitized runtime child failed");
        return;
    }

    let runtime = runtime(1);
    let store =
        Arc::new(SnapshotStore::new(Arc::clone(&runtime)).expect("FIXTURE_TECNICA snapshot store"));
    let directory =
        std::env::temp_dir().join(format!("FIXTURE_TECNICA_runtime_{}", std::process::id()));
    fs::create_dir(&directory).expect("FIXTURE_TECNICA private server directory");
    fs::set_permissions(&directory, fs::Permissions::from_mode(0o770))
        .expect("FIXTURE_TECNICA private directory mode");
    let socket = directory.join("server.sock");
    let config = ServerConfig::with_limits(socket.clone(), 1, 1, 1, 200)
        .expect("FIXTURE_TECNICA server config");
    let server = Arc::new(
        UnixServer::bind(config, store, Arc::new(NluRequestHandler::new(FixedClock)))
            .expect("FIXTURE_TECNICA server bind"),
    );
    let stop = Arc::new(AtomicBool::new(false));
    let runner = {
        let server = Arc::clone(&server);
        let stop = Arc::clone(&stop);
        thread::spawn(move || server.run_until(&stop))
    };

    let source = source();
    let before = runtime
        .state()
        .pending_state_counts()
        .expect("FIXTURE_TECNICA state fingerprint");
    let make_canary = || ["FIXTURE_TECNICA", "CREDENTIAL", "MEMORY", "CANARY"].join("_");
    let mut request = format!(
        r#"{{"version":2,"request":{{"type":"health","credential":"{}"}}}}"#,
        make_canary()
    )
    .into_bytes();
    let mut client = UnixStream::connect(&socket).expect("FIXTURE_TECNICA local client");
    write_frame(&mut client, &request).expect("FIXTURE_TECNICA credential request");
    request.fill(0);
    drop(request);
    let response = read_frame(&mut client).expect("FIXTURE_TECNICA bounded response");
    assert_eq!(
        v2::decode_response(&response, &source)
            .expect("FIXTURE_TECNICA bounded protocol response")
            .outcome(),
        &Outcome::ProtocolError(ProtocolError::MalformedJson)
    );
    let canary = make_canary();
    assert!(!String::from_utf8_lossy(&response).contains(&canary));
    assert!(!format!("{runtime:?} {:?}", runtime.state()).contains(&canary));
    assert_eq!(
        runtime
            .state()
            .pending_state_counts()
            .expect("FIXTURE_TECNICA retained-state fingerprint"),
        before
    );

    let health = dispatch_v2(&runtime, &Request::Health, 2, &source);
    assert!(matches!(health.outcome(), Outcome::Health(_)));
    assert!(!format!("{health:?}").contains(&canary));

    stop.store(true, Ordering::Release);
    assert_eq!(
        runner.join().expect("FIXTURE_TECNICA server runner"),
        Ok(())
    );
    drop(client);
    drop(server);
    assert!(socket.exists(), "FIXTURE_TECNICA socket is retained");
    fs::remove_file(&socket).expect("FIXTURE_TECNICA socket retirement");
    fs::remove_dir(&directory).expect("FIXTURE_TECNICA runtime directory cleanup");
}

#[test]
fn continuation_uses_only_the_addressed_engine_state_and_selection() {
    let runtime = runtime(2);
    let source = source();
    let addressed = session(2);
    let clarification = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: addressed,
            text: source.clone(),
        },
        1,
        &source,
    );
    let Outcome::EntityClarification(clarification) = clarification.outcome() else {
        panic!("FIXTURE_TECNICA expected entity clarification");
    };
    assert_eq!(clarification.referents().len(), 2);
    let selected = clarification.referents()[0].clone();

    let unknown = dispatch_v2(
        &runtime,
        &Request::Continue {
            session_id: session(99),
            selection: selected.clone(),
        },
        2,
        &source,
    );
    assert_eq!(
        unknown.diagnostics()[0].code(),
        DiagnosticCode::SessionUnavailable
    );

    let continued = dispatch_v2(
        &runtime,
        &Request::Continue {
            session_id: addressed,
            selection: selected.clone(),
        },
        3,
        &source,
    );
    assert!(matches!(
        continued.outcome(),
        Outcome::ConfirmationRequired(required) if required.session_id() == addressed
    ));
    let confirmation_id = confirmation_id(&continued);
    let confirmation_plan = confirmation_plan(&continued);

    let confirmed = dispatch_v2(
        &runtime,
        &Request::Confirm {
            session_id: addressed,
            confirmation_id,
            plan: confirmation_plan,
        },
        4,
        &source,
    );
    let Outcome::PolicyAccepted(accepted) = confirmed.outcome() else {
        panic!("FIXTURE_TECNICA continued confirmation");
    };
    let SlotValue::Entity(actual) = accepted.plan().plan().nodes()[0].slots()[0].value() else {
        panic!("FIXTURE_TECNICA selected entity");
    };
    assert_eq!(actual, &selected);
}

#[test]
fn cancellation_removes_pending_continuations_and_confirmations() {
    let source = source();

    let continuation_runtime = runtime(2);
    let continuation_session = session(3);
    let clarification = dispatch_v2(
        &continuation_runtime,
        &Request::Interpret {
            session_id: continuation_session,
            text: source.clone(),
        },
        1,
        &source,
    );
    let Outcome::EntityClarification(clarification) = clarification.outcome() else {
        panic!("FIXTURE_TECNICA pending continuation");
    };
    let selection = clarification.referents()[0].clone();
    let cancelled = dispatch_v2(
        &continuation_runtime,
        &Request::Cancel {
            session_id: continuation_session,
        },
        2,
        &source,
    );
    assert_eq!(
        cancelled.outcome(),
        &Outcome::Cancellation(CancellationStatus::Cancelled)
    );
    let after_cancel = dispatch_v2(
        &continuation_runtime,
        &Request::Continue {
            session_id: continuation_session,
            selection,
        },
        3,
        &source,
    );
    assert_eq!(
        after_cancel.diagnostics()[0].code(),
        DiagnosticCode::SessionUnavailable
    );

    let confirmation_runtime = runtime(1);
    let confirmation_session = session(4);
    let pending = dispatch_v2(
        &confirmation_runtime,
        &Request::Interpret {
            session_id: confirmation_session,
            text: source.clone(),
        },
        1,
        &source,
    );
    assert!(matches!(
        pending.outcome(),
        Outcome::ConfirmationRequired(_)
    ));
    let confirmation_id = confirmation_id(&pending);
    let confirmation_plan = confirmation_plan(&pending);
    let cancelled = dispatch_v2(
        &confirmation_runtime,
        &Request::Cancel {
            session_id: confirmation_session,
        },
        2,
        &source,
    );
    assert_eq!(
        cancelled.outcome(),
        &Outcome::Cancellation(CancellationStatus::Cancelled)
    );
    let after_cancel = dispatch_v2(
        &confirmation_runtime,
        &Request::Confirm {
            session_id: confirmation_session,
            confirmation_id,
            plan: confirmation_plan,
        },
        3,
        &source,
    );
    assert_eq!(
        after_cancel.diagnostics()[0].code(),
        DiagnosticCode::ConfirmationUnavailable
    );
}

#[test]
fn clock_rollback_invalidates_both_session_and_confirmation_state() {
    let assert_rollback_invalidates_state = |rollback_request: &[u8]| {
        let runtime = runtime(2);
        let source = source();
        let pending_session = session(44);
        let confirmation_session = session(45);
        let first_pending = dispatch_v2(
            &runtime,
            &Request::Interpret {
                session_id: pending_session,
                text: source.clone(),
            },
            10,
            &source,
        );
        let Outcome::EntityClarification(first_clarification) = first_pending.outcome() else {
            panic!("FIXTURE_TECNICA first pending continuation");
        };
        let pending_selection = first_clarification.referents()[0].clone();
        let second_pending = dispatch_v2(
            &runtime,
            &Request::Interpret {
                session_id: confirmation_session,
                text: source.clone(),
            },
            10,
            &source,
        );
        let Outcome::EntityClarification(second_clarification) = second_pending.outcome() else {
            panic!("FIXTURE_TECNICA second pending continuation");
        };
        let confirmation_selection = second_clarification.referents()[0].clone();
        let confirmation = dispatch_v2(
            &runtime,
            &Request::Continue {
                session_id: confirmation_session,
                selection: confirmation_selection,
            },
            11,
            &source,
        );
        assert!(matches!(
            confirmation.outcome(),
            Outcome::ConfirmationRequired(_)
        ));
        let confirmation_id = confirmation_id(&confirmation);
        let confirmation_plan = confirmation_plan(&confirmation);

        assert_eq!(
            NluRuntime::dispatch_at(&runtime, rollback_request, LogicalTime::from_ticks(9))
                .expect_err("FIXTURE_TECNICA rollback must fail closed before version dispatch")
                .code(),
            ServerErrorCode::RequestProcessing
        );

        let after_rollback = dispatch_v2(
            &runtime,
            &Request::Confirm {
                session_id: confirmation_session,
                confirmation_id,
                plan: confirmation_plan,
            },
            11,
            &source,
        );
        assert_eq!(
            after_rollback.outcome(),
            &Outcome::PolicyDenial(PolicyDenialReason::ConfirmationMismatch)
        );
        assert_eq!(
            after_rollback.diagnostics()[0].code(),
            DiagnosticCode::ConfirmationUnavailable
        );

        let continuation_after_rollback = dispatch_v2(
            &runtime,
            &Request::Continue {
                session_id: pending_session,
                selection: pending_selection,
            },
            11,
            &source,
        );
        assert_eq!(
            continuation_after_rollback.diagnostics()[0].code(),
            DiagnosticCode::SessionUnavailable
        );
    };

    let malformed_rollback = br#"{"version":2,"request":{}}"#;
    assert_rollback_invalidates_state(malformed_rollback);

    let v1_rollback =
        v1::encode_request(&source()).expect("FIXTURE_TECNICA valid v1 rollback request");
    assert_rollback_invalidates_state(&v1_rollback);

    let unsupported_version_rollback = br#"{"version":3,"request":{"type":"health"}}"#;
    assert_rollback_invalidates_state(unsupported_version_rollback);
}

#[test]
fn overlapping_handler_clock_samples_preserve_pending_state_in_request_order() {
    let runtime = runtime(2);
    let source = source();
    let pending_session = session(46);
    let confirmation_session = session(47);
    let pending = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: pending_session,
            text: source.clone(),
        },
        9,
        &source,
    );
    assert!(matches!(pending.outcome(), Outcome::EntityClarification(_)));
    let clarification = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: confirmation_session,
            text: source.clone(),
        },
        9,
        &source,
    );
    let Outcome::EntityClarification(clarification) = clarification.outcome() else {
        panic!("FIXTURE_TECNICA confirmation clarification");
    };
    let confirmation = dispatch_v2(
        &runtime,
        &Request::Continue {
            session_id: confirmation_session,
            selection: clarification.referents()[0].clone(),
        },
        9,
        &source,
    );
    assert!(matches!(
        confirmation.outcome(),
        Outcome::ConfirmationRequired(_)
    ));
    assert_eq!(
        runtime
            .state()
            .pending_state_counts()
            .expect("FIXTURE_TECNICA pending state"),
        (1, 1)
    );

    let clock = OverlappingClock::new();
    let handler = Arc::new(NluRequestHandler::new(clock.clone()));
    let request =
        Arc::new(v2::encode_request(&Request::Health).expect("FIXTURE_TECNICA health request"));
    let first = {
        let runtime = Arc::clone(&runtime);
        let handler = Arc::clone(&handler);
        let request = Arc::clone(&request);
        thread::spawn(move || handler.handle(&runtime, request.as_slice()))
    };
    clock.wait_for_first_sample();
    let second = {
        let runtime = Arc::clone(&runtime);
        let handler = Arc::clone(&handler);
        let request = Arc::clone(&request);
        let clock = clock.clone();
        thread::spawn(move || {
            let result = handler.handle(&runtime, request.as_slice());
            clock.mark_later_request_finished();
            result
        })
    };

    let first = first
        .join()
        .expect("FIXTURE_TECNICA first request thread")
        .expect("FIXTURE_TECNICA tick 10 request");
    let second = second
        .join()
        .expect("FIXTURE_TECNICA second request thread")
        .expect("FIXTURE_TECNICA tick 11 request");
    assert!(matches!(
        v2::decode_response(&first, &source)
            .expect("FIXTURE_TECNICA first health response")
            .outcome(),
        Outcome::Health(_)
    ));
    assert!(matches!(
        v2::decode_response(&second, &source)
            .expect("FIXTURE_TECNICA second health response")
            .outcome(),
        Outcome::Health(_)
    ));
    assert_eq!(
        runtime
            .state()
            .pending_state_counts()
            .expect("FIXTURE_TECNICA preserved pending state"),
        (1, 1)
    );
}

#[test]
fn sequential_handler_clock_rollback_still_fails_closed_and_purges_state() {
    let runtime = runtime(2);
    let source = source();
    let pending_session = session(48);
    let pending = dispatch_v2(
        &runtime,
        &Request::Interpret {
            session_id: pending_session,
            text: source.clone(),
        },
        1,
        &source,
    );
    assert!(matches!(pending.outcome(), Outcome::EntityClarification(_)));
    assert_eq!(
        runtime
            .state()
            .pending_state_counts()
            .expect("FIXTURE_TECNICA pending state"),
        (1, 0)
    );

    let handler = NluRequestHandler::new(SequentialRollbackClock::new());
    let health = v2::encode_request(&Request::Health).expect("FIXTURE_TECNICA health request");
    handler
        .handle(&runtime, &health)
        .expect("FIXTURE_TECNICA tick 11 request");
    assert_eq!(
        handler
            .handle(&runtime, &health)
            .expect_err("FIXTURE_TECNICA sequential rollback")
            .code(),
        ServerErrorCode::RequestProcessing
    );
    assert_eq!(
        runtime
            .state()
            .pending_state_counts()
            .expect("FIXTURE_TECNICA purged pending state"),
        (0, 0)
    );
}

#[test]
fn malformed_requests_are_closed_and_snapshot_substitution_fails_before_dispatch() {
    let runtime = runtime(1);
    let source = source();
    let unknown_version = br#"{"version":3,"request":{"type":"health"}}"#;
    let response = NluRuntime::dispatch_at(&runtime, unknown_version, LogicalTime::from_ticks(1))
        .expect("FIXTURE_TECNICA closed protocol error");
    assert_eq!(
        v2::decode_response(&response, &source)
            .expect("FIXTURE_TECNICA error response")
            .outcome(),
        &Outcome::ProtocolError(ProtocolError::UnsupportedVersion)
    );

    let malformed_v1 = br#"{"version":1,"request":{}}"#;
    let response = NluRuntime::dispatch_at(&runtime, malformed_v1, LogicalTime::from_ticks(1))
        .expect("FIXTURE_TECNICA v1 error");
    assert_eq!(
        v1::decode_outcome(&response, &source).expect("FIXTURE_TECNICA v1 error response"),
        v1::Outcome::ProtocolError(ProtocolError::MalformedJson)
    );

    let substituted = RuntimeSnapshot::new(
        metadata(2, 1),
        NluRuntime::standard(
            metadata(1, 1),
            catalog(1),
            PolicyGeneration::new(1).expect("FIXTURE_TECNICA policy generation"),
            SessionConfig::new(10).expect("FIXTURE_TECNICA session config"),
            ConfirmationConfig::new(10).expect("FIXTURE_TECNICA confirmation config"),
        )
        .expect("FIXTURE_TECNICA substituted runtime"),
    );
    let request = v2::encode_request(&Request::Health).expect("FIXTURE_TECNICA health request");
    assert_eq!(
        NluRuntime::dispatch_at(&substituted, &request, LogicalTime::from_ticks(1))
            .expect_err("FIXTURE_TECNICA generation mismatch")
            .code(),
        ServerErrorCode::RuntimeState
    );
}

#[test]
fn fresh_runtimes_replay_identical_wire_bytes_for_identical_explicit_inputs() {
    let source = source();
    let request = v2::encode_request(&Request::Interpret {
        session_id: session(5),
        text: source,
    })
    .expect("FIXTURE_TECNICA request");
    let first = NluRuntime::dispatch_at(&runtime(1), &request, LogicalTime::from_ticks(7))
        .expect("FIXTURE_TECNICA first");
    let second = NluRuntime::dispatch_at(&runtime(1), &request, LogicalTime::from_ticks(7))
        .expect("FIXTURE_TECNICA second");
    assert_eq!(first, second);
}
