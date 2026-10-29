use core::fmt;
use std::fs;
use std::io::ErrorKind;
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender, TrySendError, sync_channel};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::config::SanitizedEnvironment;
use crate::framing::{read_frame_until, write_frame_until};
use crate::peer;
use crate::{
    MAX_FRAME_BYTES, Result, RuntimeSnapshot, ServerConfig, ServerError, ServerErrorCode,
    SnapshotState, SnapshotStore,
};

pub trait RequestHandler<T>: Send + Sync + 'static {
    fn handle(&self, snapshot: &RuntimeSnapshot<T>, request: &[u8]) -> Result<Vec<u8>>;
}

pub struct UnixServer<T: SnapshotState, H> {
    config: ServerConfig,
    listener: UnixListener,
    snapshots: Arc<SnapshotStore<T>>,
    handler: Arc<H>,
    started: AtomicBool,
}

struct FatalProcess;

impl FatalProcess {
    fn exit(&self) -> ! {
        std::process::exit(FATAL_HANDLER_EXIT_CODE)
    }
}

impl<T, H> UnixServer<T, H>
where
    T: SnapshotState + Send + Sync + 'static,
    H: RequestHandler<T>,
{
    pub fn bind(
        config: ServerConfig,
        snapshots: Arc<SnapshotStore<T>>,
        handler: Arc<H>,
    ) -> Result<Self> {
        SanitizedEnvironment::validate_current()?;
        validate_socket_parent(&config)?;
        if fs::symlink_metadata(config.socket_path()).is_ok() {
            return Err(ServerError::new(ServerErrorCode::SocketPathOccupied));
        }
        let listener = UnixListener::bind(config.socket_path())
            .map_err(|_| ServerError::new(ServerErrorCode::SocketBind))?;
        fs::set_permissions(config.socket_path(), fs::Permissions::from_mode(0o660))
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        peer::set_socket_group(config.socket_path(), config.ipc_group())?;
        let metadata = fs::symlink_metadata(config.socket_path())
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        if !metadata.file_type().is_socket()
            || metadata.permissions().mode() & 0o777 != 0o660
            || metadata.gid() != config.ipc_group()
        {
            return Err(ServerError::new(ServerErrorCode::SocketConfiguration));
        }
        listener
            .set_nonblocking(true)
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        Ok(Self {
            config,
            listener,
            snapshots,
            handler,
            started: AtomicBool::new(false),
        })
    }

    pub fn run_until(&self, stop: &AtomicBool) -> Result<()> {
        if self.started.swap(true, Ordering::AcqRel) {
            return Err(ServerError::new(ServerErrorCode::InvalidConfiguration));
        }

        let capacity = usize::from(self.config.queue_capacity());
        let shutdown = Arc::new(AtomicBool::new(false));
        let fatal = Arc::new(FatalProcess);
        let (request_sender, request_receiver) = sync_channel(capacity);
        let request_receiver = Arc::new(Mutex::new(request_receiver));
        let (mut request_workers, mut request_monitors) =
            self.spawn_request_workers(request_receiver, Arc::clone(&shutdown), Arc::clone(&fatal));

        let (connection_sender, connection_receiver) = sync_channel(capacity);
        let connection_receiver = Arc::new(Mutex::new(connection_receiver));
        let mut connection_workers = self.spawn_connection_workers(
            connection_receiver,
            request_sender.clone(),
            Arc::clone(&shutdown),
            Arc::clone(&fatal),
        );

        let accept_result = self.accept_loop(stop, &connection_sender);
        shutdown.store(true, Ordering::Release);
        drop(connection_sender);
        drop(request_sender);
        let shutdown_deadline = Instant::now()
            .checked_add(Duration::from_millis(100))
            .ok_or_else(|| ServerError::new(ServerErrorCode::InvalidConfiguration))?;
        connection_workers.append(&mut request_workers);
        connection_workers.append(&mut request_monitors);
        let worker_result =
            join_workers_until(&mut connection_workers, shutdown_deadline, fatal.as_ref());

        accept_result?;
        worker_result
    }

    fn spawn_connection_workers(
        &self,
        receiver: Arc<Mutex<Receiver<UnixStream>>>,
        request_sender: SyncSender<RequestJob<T>>,
        shutdown: Arc<AtomicBool>,
        fatal: Arc<FatalProcess>,
    ) -> Vec<JoinHandle<()>> {
        (0..self.config.workers())
            .map(|_| {
                let receiver = Arc::clone(&receiver);
                let snapshots = Arc::clone(&self.snapshots);
                let request_sender = request_sender.clone();
                let config = self.config.clone();
                let shutdown = Arc::clone(&shutdown);
                let fatal = Arc::clone(&fatal);
                thread::spawn(move || {
                    worker_loop(
                        &config,
                        receiver.as_ref(),
                        snapshots.as_ref(),
                        &request_sender,
                        &shutdown,
                        &fatal,
                    );
                })
            })
            .collect()
    }

    fn spawn_request_workers(
        &self,
        receiver: Arc<Mutex<Receiver<RequestJob<T>>>>,
        shutdown: Arc<AtomicBool>,
        fatal: Arc<FatalProcess>,
    ) -> (Vec<JoinHandle<()>>, Vec<JoinHandle<()>>) {
        let mut workers = Vec::with_capacity(usize::from(self.config.workers()));
        let mut monitors = Vec::with_capacity(usize::from(self.config.workers()));
        for _ in 0..self.config.workers() {
            let active = Arc::new(Mutex::new(WorkerSlot::default()));
            let worker_receiver = Arc::clone(&receiver);
            let worker_handler = Arc::clone(&self.handler);
            let worker_shutdown = Arc::clone(&shutdown);
            let worker_active = Arc::clone(&active);
            let worker_fatal = Arc::clone(&fatal);
            let monitor_fatal = Arc::clone(&fatal);
            workers.push(thread::spawn(move || {
                let _finished = WorkerFinished::new(Arc::clone(&worker_active), worker_fatal);
                request_worker_loop(
                    worker_receiver.as_ref(),
                    worker_handler.as_ref(),
                    worker_shutdown,
                    worker_active.as_ref(),
                );
            }));
            monitors.push(thread::spawn(move || {
                request_monitor_loop(active.as_ref(), monitor_fatal.as_ref());
            }));
        }
        (workers, monitors)
    }

    fn accept_loop(&self, stop: &AtomicBool, sender: &SyncSender<UnixStream>) -> Result<()> {
        while !stop.load(Ordering::Acquire) {
            match self.listener.accept() {
                Ok((stream, _address)) => {
                    if peer::stream_identity(&stream).ok() != Some(self.config.expected_peer()) {
                        continue;
                    }
                    stream
                        .set_nonblocking(false)
                        .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
                    match sender.try_send(stream) {
                        Ok(()) => {}
                        Err(TrySendError::Full(_stream)) => {}
                        Err(TrySendError::Disconnected(_stream)) => {
                            return Err(ServerError::new(ServerErrorCode::WorkerPanic));
                        }
                    }
                }
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(1));
                }
                Err(_) => return Err(ServerError::new(ServerErrorCode::SocketAccept)),
            }
        }
        Ok(())
    }
}

impl<T: SnapshotState, H> fmt::Debug for UnixServer<T, H> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("UnixServer")
            .field("workers", &self.config.workers())
            .field("queue_capacity", &self.config.queue_capacity())
            .field("runtime_state", &"redacted")
            .finish()
    }
}

struct RequestJob<T> {
    snapshot: Arc<RuntimeSnapshot<T>>,
    request: RequestBytes,
    response: SyncSender<Result<Vec<u8>>>,
    admission: Arc<RequestAdmission>,
}

const REQUEST_QUEUED: u8 = 0;
const REQUEST_ADMITTED: u8 = 1;
const REQUEST_CANCELLED: u8 = 2;
const REQUEST_COMPLETED: u8 = 3;
const REQUEST_EXPIRED_ACTIVE: u8 = 4;
const FATAL_HANDLER_EXIT_CODE: i32 = 70;

struct RequestAdmission {
    deadline: Instant,
    state: AtomicU8,
    shutdown: Arc<AtomicBool>,
    fatal: Arc<FatalProcess>,
}

impl RequestAdmission {
    fn new(deadline: Instant, shutdown: Arc<AtomicBool>, fatal: Arc<FatalProcess>) -> Self {
        Self {
            deadline,
            state: AtomicU8::new(REQUEST_QUEUED),
            shutdown,
            fatal,
        }
    }

    fn try_admit(&self) -> bool {
        if self.is_expired() {
            self.cancel();
            return false;
        }
        if self
            .state
            .compare_exchange(
                REQUEST_QUEUED,
                REQUEST_ADMITTED,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_err()
        {
            return false;
        }
        if self.is_expired() {
            self.cancel();
            return false;
        }
        true
    }

    fn try_complete(&self) -> bool {
        if self.deadline_elapsed() {
            self.expire();
            return false;
        }
        self.state
            .compare_exchange(
                REQUEST_ADMITTED,
                REQUEST_COMPLETED,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_ok()
    }

    fn cancel(&self) {
        let mut state = self.state.load(Ordering::Acquire);
        while matches!(state, REQUEST_QUEUED | REQUEST_ADMITTED) {
            match self.state.compare_exchange_weak(
                state,
                REQUEST_CANCELLED,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return,
                Err(current) => state = current,
            }
        }
    }

    fn expire(&self) -> bool {
        let mut state = self.state.load(Ordering::Acquire);
        loop {
            match state {
                REQUEST_QUEUED => match self.state.compare_exchange_weak(
                    REQUEST_QUEUED,
                    REQUEST_CANCELLED,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                ) {
                    Ok(_) => return false,
                    Err(current) => state = current,
                },
                REQUEST_ADMITTED => match self.state.compare_exchange_weak(
                    REQUEST_ADMITTED,
                    REQUEST_EXPIRED_ACTIVE,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                ) {
                    Ok(_) => return true,
                    Err(current) => state = current,
                },
                REQUEST_EXPIRED_ACTIVE => return true,
                REQUEST_CANCELLED | REQUEST_COMPLETED => return false,
                _ => self.fatal.exit(),
            }
        }
    }

    fn is_expired(&self) -> bool {
        self.shutdown.load(Ordering::Acquire) || self.deadline_elapsed()
    }

    fn deadline_elapsed(&self) -> bool {
        Instant::now() >= self.deadline
    }

    fn is_cancelled(&self) -> bool {
        self.state.load(Ordering::Acquire) == REQUEST_CANCELLED
    }
}

struct RequestBytes {
    bytes: Vec<u8>,
    #[cfg(test)]
    erase_audit: Option<Arc<Mutex<Vec<u8>>>>,
}

impl RequestBytes {
    fn new(bytes: Vec<u8>) -> Self {
        Self {
            bytes,
            #[cfg(test)]
            erase_audit: None,
        }
    }

    #[cfg(test)]
    fn with_erase_audit(bytes: Vec<u8>, erase_audit: Arc<Mutex<Vec<u8>>>) -> Self {
        Self {
            bytes,
            erase_audit: Some(erase_audit),
        }
    }

    fn as_slice(&self) -> &[u8] {
        &self.bytes
    }

    fn erase(&mut self) {
        self.bytes.fill(0);
        std::hint::black_box(self.bytes.as_slice());
        #[cfg(test)]
        if let Some(audit) = &self.erase_audit
            && let Ok(mut observed) = audit.lock()
        {
            observed.clone_from(&self.bytes);
        }
    }
}

impl Drop for RequestBytes {
    fn drop(&mut self) {
        self.erase();
    }
}

#[derive(Default)]
struct WorkerSlot {
    admission: Option<Arc<RequestAdmission>>,
    finished: bool,
}

type ActiveRequest = Mutex<WorkerSlot>;

struct WorkerFinished {
    active: Arc<ActiveRequest>,
    fatal: Arc<FatalProcess>,
}

impl WorkerFinished {
    fn new(active: Arc<ActiveRequest>, fatal: Arc<FatalProcess>) -> Self {
        Self { active, fatal }
    }
}

impl Drop for WorkerFinished {
    fn drop(&mut self) {
        let mut slot = self.active.lock().unwrap_or_else(|_| self.fatal.exit());
        slot.finished = true;
    }
}

fn request_worker_loop<T, H>(
    receiver: &Mutex<Receiver<RequestJob<T>>>,
    handler: &H,
    shutdown: Arc<AtomicBool>,
    active: &ActiveRequest,
) where
    H: RequestHandler<T>,
{
    loop {
        let job = {
            let Ok(receiver) = receiver.lock() else {
                return;
            };
            receiver.recv()
        };
        let Ok(mut job) = job else {
            return;
        };
        let admission = Arc::clone(&job.admission);
        if !admission.try_admit() {
            continue;
        }
        install_active_request(active, &admission);
        if shutdown.load(Ordering::Acquire) {
            admission.cancel();
            clear_active_request(active, &admission);
            continue;
        }
        if admission.deadline_elapsed() {
            admission.fatal.exit();
        }
        let response = handler.handle(&job.snapshot, job.request.as_slice());
        job.request.erase();
        let completed = complete_active_request(active, &admission);
        if completed {
            let _ = job.response.send(response);
        } else {
            admission.fatal.exit();
        }
    }
}

fn install_active_request(active: &ActiveRequest, admission: &Arc<RequestAdmission>) {
    let mut slot = active.lock().unwrap_or_else(|_| admission.fatal.exit());
    if slot.admission.is_some() {
        admission.fatal.exit();
    }
    slot.admission = Some(Arc::clone(admission));
}

fn clear_active_request(active: &ActiveRequest, admission: &Arc<RequestAdmission>) {
    let mut slot = active.lock().unwrap_or_else(|_| admission.fatal.exit());
    let Some(current) = slot.admission.take() else {
        admission.fatal.exit();
    };
    if !Arc::ptr_eq(&current, admission) {
        admission.fatal.exit();
    }
}

fn complete_active_request(active: &ActiveRequest, admission: &Arc<RequestAdmission>) -> bool {
    let mut slot = active.lock().unwrap_or_else(|_| admission.fatal.exit());
    let Some(current) = slot.admission.as_ref() else {
        admission.fatal.exit();
    };
    if !Arc::ptr_eq(current, admission) {
        admission.fatal.exit();
    }
    let completed = admission.try_complete();
    if completed {
        slot.admission.take();
    }
    completed
}

fn monitor_requires_fatal(slot: &WorkerSlot, now: Instant) -> bool {
    slot.admission.as_ref().is_some_and(|admission| {
        let state = admission.state.load(Ordering::Acquire);
        state == REQUEST_EXPIRED_ACTIVE
            || (state == REQUEST_ADMITTED && (now >= admission.deadline || slot.finished))
    })
}

fn request_monitor_loop(active: &ActiveRequest, fatal: &FatalProcess) {
    loop {
        let finished = {
            let slot = active.lock().unwrap_or_else(|_| fatal.exit());
            if let Some(admission) = slot.admission.as_ref() {
                if monitor_requires_fatal(&slot, Instant::now()) {
                    admission.fatal.exit();
                }
                if slot.finished {
                    fatal.exit();
                }
            }
            slot.finished
        };
        if finished {
            return;
        }
        thread::sleep(Duration::from_millis(1));
    }
}

fn worker_loop<T>(
    config: &ServerConfig,
    receiver: &Mutex<Receiver<UnixStream>>,
    snapshots: &SnapshotStore<T>,
    request_sender: &SyncSender<RequestJob<T>>,
    shutdown: &Arc<AtomicBool>,
    fatal: &Arc<FatalProcess>,
) where
    T: SnapshotState,
{
    loop {
        if shutdown.load(Ordering::Acquire) {
            return;
        }
        let stream = {
            let Ok(receiver) = receiver.lock() else {
                return;
            };
            receiver.recv()
        };
        let Ok(mut stream) = stream else {
            return;
        };
        let _ = process_connection(
            config,
            &mut stream,
            snapshots,
            request_sender,
            Arc::clone(shutdown),
            Arc::clone(fatal),
        );
    }
}

fn process_connection<T>(
    config: &ServerConfig,
    stream: &mut UnixStream,
    snapshots: &SnapshotStore<T>,
    request_sender: &SyncSender<RequestJob<T>>,
    shutdown: Arc<AtomicBool>,
    fatal: Arc<FatalProcess>,
) -> Result<()>
where
    T: SnapshotState,
{
    let frame_budget = Duration::from_millis(config.frame_timeout_millis());
    for _ in 0..config.frames_per_connection() {
        if shutdown.load(Ordering::Acquire) {
            return Ok(());
        }
        let deadline = Instant::now()
            .checked_add(frame_budget)
            .ok_or_else(|| ServerError::new(ServerErrorCode::InvalidConfiguration))?;
        let request = read_frame_until(stream, deadline)?;
        if shutdown.load(Ordering::Acquire) {
            return Ok(());
        }
        let snapshot = snapshots.lease()?;
        let response = execute_request_until(
            request_sender,
            snapshot,
            request,
            deadline,
            Arc::clone(&shutdown),
            Arc::clone(&fatal),
        )?;
        if response.len() > MAX_FRAME_BYTES {
            return Err(ServerError::new(ServerErrorCode::ResponseTooLarge));
        }
        write_frame_until(stream, &response, deadline)?;
    }
    Ok(())
}

fn execute_request_until<T>(
    request_sender: &SyncSender<RequestJob<T>>,
    snapshot: Arc<RuntimeSnapshot<T>>,
    request: Vec<u8>,
    deadline: Instant,
    shutdown: Arc<AtomicBool>,
    fatal: Arc<FatalProcess>,
) -> Result<Vec<u8>> {
    let (response, receiver) = sync_channel(1);
    let admission = Arc::new(RequestAdmission::new(deadline, shutdown, fatal));
    let job = RequestJob {
        snapshot,
        request: RequestBytes::new(request),
        response,
        admission: Arc::clone(&admission),
    };
    request_sender.try_send(job).map_err(|error| match error {
        TrySendError::Full(_) => ServerError::new(ServerErrorCode::RequestQueueFull),
        TrySendError::Disconnected(_) => ServerError::new(ServerErrorCode::WorkerPanic),
    })?;
    let Some(remaining) = deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| !remaining.is_zero())
    else {
        return settle_expired_request(admission.as_ref());
    };
    match receiver.recv_timeout(remaining) {
        Ok(result) => result,
        Err(RecvTimeoutError::Timeout) => settle_expired_request(admission.as_ref()),
        Err(RecvTimeoutError::Disconnected) if admission.is_cancelled() => {
            Err(ServerError::new(ServerErrorCode::RequestTimeout))
        }
        Err(RecvTimeoutError::Disconnected) => Err(ServerError::new(ServerErrorCode::WorkerPanic)),
    }
}

fn settle_expired_request(admission: &RequestAdmission) -> Result<Vec<u8>> {
    if admission.expire() {
        // Arbitrary in-process code cannot have its shared-memory access revoked.
        admission.fatal.exit();
    }
    Err(ServerError::new(ServerErrorCode::RequestTimeout))
}

fn join_workers_until(
    workers: &mut Vec<JoinHandle<()>>,
    deadline: Instant,
    fatal: &FatalProcess,
) -> Result<()> {
    let mut result = Ok(());
    loop {
        let mut index = 0;
        while index < workers.len() {
            if workers[index].is_finished() {
                let worker = workers.swap_remove(index);
                if worker.join().is_err() {
                    result = Err(ServerError::new(ServerErrorCode::WorkerPanic));
                }
            } else {
                index += 1;
            }
        }
        if workers.is_empty() || Instant::now() >= deadline {
            break;
        }
        thread::sleep(Duration::from_millis(1));
    }
    if !workers.is_empty() {
        fatal.exit();
    }
    result
}

fn validate_socket_parent(config: &ServerConfig) -> Result<()> {
    let parent = config
        .socket_path()
        .parent()
        .ok_or_else(|| ServerError::new(ServerErrorCode::InvalidSocketPath))?;
    let metadata = fs::symlink_metadata(parent)
        .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.permissions().mode() & 0o777 != 0o770
        || metadata.gid() != config.ipc_group()
    {
        return Err(ServerError::new(ServerErrorCode::SocketConfiguration));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::io::Write;
    use std::net::TcpListener;
    use std::os::unix::net::UnixStream;
    use std::process::Command;
    use std::sync::Barrier;
    use std::sync::atomic::{AtomicBool, AtomicU64};
    use std::time::{Duration, Instant};

    use super::*;
    use crate::{RuntimeSnapshot, SnapshotMetadata, SnapshotState, read_frame, write_frame};

    struct Echo;

    #[derive(Debug)]
    struct EchoState(u64);

    impl SnapshotState for EchoState {
        fn validate_snapshot_metadata(&self, metadata: SnapshotMetadata) -> Result<()> {
            if metadata == SnapshotMetadata::new(1, 1, 1, 1, 1)? {
                Ok(())
            } else {
                Err(ServerError::new(ServerErrorCode::RuntimeState))
            }
        }

        fn invalidate_for_reload(&self, _now: nlu_core::LogicalTime) -> Result<()> {
            Ok(())
        }
    }

    impl RequestHandler<EchoState> for Echo {
        fn handle(&self, snapshot: &RuntimeSnapshot<EchoState>, request: &[u8]) -> Result<Vec<u8>> {
            let mut response = snapshot.state().0.to_be_bytes().to_vec();
            response.extend_from_slice(request);
            Ok(response)
        }
    }

    struct Panicking;

    impl RequestHandler<EchoState> for Panicking {
        fn handle(
            &self,
            _snapshot: &RuntimeSnapshot<EchoState>,
            _request: &[u8],
        ) -> Result<Vec<u8>> {
            panic!("FIXTURE_TECNICA handler panic")
        }
    }

    #[derive(Debug)]
    struct MutatingState {
        mutated: AtomicBool,
    }

    impl SnapshotState for MutatingState {
        fn validate_snapshot_metadata(&self, metadata: SnapshotMetadata) -> Result<()> {
            if metadata == SnapshotMetadata::new(1, 1, 1, 1, 1)? {
                Ok(())
            } else {
                Err(ServerError::new(ServerErrorCode::RuntimeState))
            }
        }

        fn invalidate_for_reload(&self, _now: nlu_core::LogicalTime) -> Result<()> {
            Ok(())
        }
    }

    struct BlockingMutation {
        blocker: Arc<Barrier>,
    }

    impl RequestHandler<MutatingState> for BlockingMutation {
        fn handle(
            &self,
            snapshot: &RuntimeSnapshot<MutatingState>,
            request: &[u8],
        ) -> Result<Vec<u8>> {
            match request {
                b"FIXTURE_TECNICA_ADMITTED_MUTATE" => {
                    self.blocker.wait();
                    self.blocker.wait();
                    snapshot.state().mutated.store(true, Ordering::Release);
                    Ok(b"FIXTURE_TECNICA_MUTATED".to_vec())
                }
                b"FIXTURE_TECNICA_BLOCKER" => {
                    self.blocker.wait();
                    self.blocker.wait();
                    Ok(b"FIXTURE_TECNICA_BLOCKER_DONE".to_vec())
                }
                b"FIXTURE_TECNICA_MUTATE" => {
                    snapshot.state().mutated.store(true, Ordering::Release);
                    Ok(b"FIXTURE_TECNICA_MUTATED".to_vec())
                }
                b"FIXTURE_TECNICA_DRAIN" => Ok(b"FIXTURE_TECNICA_DRAINED".to_vec()),
                _ => Err(ServerError::new(ServerErrorCode::RequestProcessing)),
            }
        }
    }

    struct DelayedExternalMutation {
        entered: Arc<Barrier>,
        marker: std::path::PathBuf,
    }

    impl RequestHandler<MutatingState> for DelayedExternalMutation {
        fn handle(
            &self,
            snapshot: &RuntimeSnapshot<MutatingState>,
            _request: &[u8],
        ) -> Result<Vec<u8>> {
            self.entered.wait();
            thread::sleep(Duration::from_millis(500));
            fs::write(&self.marker, b"FIXTURE_TECNICA_LATE_MUTATION")
                .expect("FIXTURE_TECNICA late marker");
            snapshot.state().mutated.store(true, Ordering::Release);
            Ok(b"FIXTURE_TECNICA_LATE".to_vec())
        }
    }

    const SANITIZED_TEST_ENVIRONMENT: [(&str, &str); 7] = [
        ("HOME", "/nonexistent"),
        ("LANG", "C.UTF-8"),
        ("LC_ALL", "C.UTF-8"),
        ("PATH", "/usr/bin:/bin"),
        ("RUST_BACKTRACE", "0"),
        ("TMPDIR", "/tmp"),
        ("TZ", "UTC"),
    ];

    fn run_in_sanitized_child(test_name: &str, body: impl FnOnce()) {
        if SanitizedEnvironment::validate_current().is_ok() {
            body();
            return;
        }
        let status = Command::new(std::env::current_exe().expect("FIXTURE_TECNICA test binary"))
            .arg("--exact")
            .arg(test_name)
            .arg("--nocapture")
            .env_clear()
            .envs(SANITIZED_TEST_ENVIRONMENT)
            .status()
            .expect("FIXTURE_TECNICA sanitized child");
        assert!(status.success(), "sanitized child failed");
    }

    fn fatal_child_directory(process_id: u32) -> std::path::PathBuf {
        std::path::PathBuf::from(format!("/private/tmp/FIXTURE_TECNICA_nlu_{process_id}_0"))
    }

    fn detached_fatal_process() -> Arc<FatalProcess> {
        Arc::new(FatalProcess)
    }

    fn late_mutation_marker(process_id: u32) -> std::path::PathBuf {
        fatal_child_directory(process_id).join("late-mutation.marker")
    }

    struct FatalChildResult {
        exit_code: Option<i32>,
        marker_was_written: bool,
        socket_was_retained: bool,
    }

    fn run_fatal_test_child(test_name: &str) -> FatalChildResult {
        let mut child =
            Command::new(std::env::current_exe().expect("FIXTURE_TECNICA fatal child test binary"))
                .arg("--ignored")
                .arg("--exact")
                .arg(test_name)
                .arg("--nocapture")
                .env_clear()
                .envs(SANITIZED_TEST_ENVIRONMENT)
                .spawn()
                .expect("FIXTURE_TECNICA fatal child");
        let process_id = child.id();
        let status = child.wait().expect("FIXTURE_TECNICA fatal child wait");
        let directory = fatal_child_directory(process_id);
        let marker = late_mutation_marker(process_id);
        let marker_was_written = marker.exists();
        let socket_was_retained = fs::symlink_metadata(directory.join("server.sock"))
            .is_ok_and(|metadata| metadata.file_type().is_socket());
        let _ = fs::remove_file(directory.join("server.sock"));
        let _ = fs::remove_file(&marker);
        let _ = fs::remove_dir(&directory);

        FatalChildResult {
            exit_code: status.code(),
            marker_was_written,
            socket_was_retained,
        }
    }

    fn arm_fatal_test_watchdog() {
        thread::spawn(|| {
            thread::sleep(Duration::from_secs(2));
            std::process::exit(125);
        });
    }

    fn private_directory() -> std::path::PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let unique = NEXT.fetch_add(1, Ordering::Relaxed);
        let path = std::path::PathBuf::from(format!(
            "/private/tmp/FIXTURE_TECNICA_nlu_{}_{unique}",
            std::process::id()
        ));
        fs::create_dir(&path).expect("private socket directory");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o770))
            .expect("private directory mode");
        path
    }

    fn path() -> std::path::PathBuf {
        private_directory().join("server.sock")
    }

    fn snapshot() -> Arc<SnapshotStore<EchoState>> {
        let metadata = SnapshotMetadata::new(1, 1, 1, 1, 1).expect("metadata");
        Arc::new(
            SnapshotStore::new(Arc::new(RuntimeSnapshot::new(metadata, EchoState(17))))
                .expect("FIXTURE_TECNICA valid snapshot"),
        )
    }

    #[test]
    fn dedicated_directory_matches_the_configured_ipc_group() {
        let directory = private_directory();
        let config = ServerConfig::new(directory.join("server.sock")).expect("config");
        let metadata = fs::symlink_metadata(&directory).expect("directory metadata");
        assert_eq!(metadata.permissions().mode() & 0o777, 0o770);
        assert_eq!(
            metadata.gid(),
            config.ipc_group(),
            "FIXTURE_TECNICA directory group must match configured IPC group"
        );
        assert_eq!(validate_socket_parent(&config), Ok(()));
        fs::remove_dir(directory).expect("test directory cleanup");
    }

    #[test]
    fn binds_one_group_restricted_unix_listener_serves_fragmented_frames_and_stays_outbound_free() {
        run_in_sanitized_child(
            "server::tests::binds_one_group_restricted_unix_listener_serves_fragmented_frames_and_stays_outbound_free",
            || {
                let outbound_canary =
                    TcpListener::bind("127.0.0.1:0").expect("FIXTURE_TECNICA outbound canary");
                outbound_canary
                    .set_nonblocking(true)
                    .expect("FIXTURE_TECNICA nonblocking canary");
                let outbound_address = outbound_canary
                    .local_addr()
                    .expect("FIXTURE_TECNICA outbound canary address");

                let path = path();
                let config = ServerConfig::with_limits(path.clone(), 1, 1, 1, 100).expect("config");
                let server =
                    Arc::new(UnixServer::bind(config, snapshot(), Arc::new(Echo)).expect("bind"));
                let mode = fs::metadata(&path).expect("metadata").permissions().mode() & 0o777;
                assert_eq!(mode, 0o660);

                let stop = Arc::new(AtomicBool::new(false));
                let runner = {
                    let server = Arc::clone(&server);
                    let stop = Arc::clone(&stop);
                    thread::spawn(move || server.run_until(&stop))
                };
                let mut client = UnixStream::connect(&path).expect("local client");
                let payload = format!("FIXTURE_TECNICA_A_{outbound_address}");
                let header = u32::try_from(payload.len())
                    .expect("FIXTURE_TECNICA frame length")
                    .to_be_bytes();
                client
                    .write_all(&header[..1])
                    .expect("FIXTURE_TECNICA first header fragment");
                thread::sleep(Duration::from_millis(30));
                client
                    .write_all(&header[1..])
                    .and_then(|()| client.write_all(payload.as_bytes()))
                    .expect("FIXTURE_TECNICA remaining fragments");
                let response = read_frame(&mut client).expect("fragmented response");
                assert_eq!(&response[..8], &17_u64.to_be_bytes());
                assert_eq!(&response[8..], payload.as_bytes());
                assert_eq!(
                    outbound_canary
                        .accept()
                        .expect_err("server must make no outbound connection")
                        .kind(),
                    ErrorKind::WouldBlock
                );

                stop.store(true, Ordering::Release);
                assert_eq!(runner.join().expect("runner"), Ok(()));
                drop(server);
                assert!(
                    fs::symlink_metadata(&path)
                        .is_ok_and(|metadata| metadata.file_type().is_socket()),
                    "owned socket is retained for generation retirement"
                );
                fs::remove_file(&path).expect("test socket retirement");
                fs::remove_dir(path.parent().expect("parent")).expect("test directory cleanup");
            },
        );
    }

    #[test]
    fn admitted_handler_overrun_exits_the_dedicated_process_without_late_mutation() {
        let result = run_fatal_test_child("server::tests::admitted_handler_overrun_fatal_child");
        assert_eq!(result.exit_code, Some(FATAL_HANDLER_EXIT_CODE));
        assert!(
            !result.marker_was_written,
            "FIXTURE_TECNICA admitted handler mutated after fatal containment"
        );
    }

    #[test]
    #[ignore = "FIXTURE_TECNICA fatal child only"]
    fn admitted_handler_overrun_fatal_child() {
        arm_fatal_test_watchdog();
        let directory = private_directory();
        let metadata = SnapshotMetadata::new(1, 1, 1, 1, 1).expect("FIXTURE_TECNICA metadata");
        let snapshot = Arc::new(RuntimeSnapshot::new(
            metadata,
            MutatingState {
                mutated: AtomicBool::new(false),
            },
        ));
        let entered = Arc::new(Barrier::new(2));
        let handler = DelayedExternalMutation {
            entered: Arc::clone(&entered),
            marker: directory.join("late-mutation.marker"),
        };
        let stop = Arc::new(AtomicBool::new(false));
        let (job_sender, job_receiver) = sync_channel(1);
        let job_receiver = Mutex::new(job_receiver);
        let active = Arc::new(Mutex::new(WorkerSlot::default()));
        let fatal = detached_fatal_process();
        let worker_active = Arc::clone(&active);
        let worker_fatal = Arc::clone(&fatal);
        let monitor_fatal = Arc::clone(&fatal);
        let worker_stop = Arc::clone(&stop);
        let _worker = thread::spawn(move || {
            let _finished = WorkerFinished::new(Arc::clone(&worker_active), worker_fatal);
            request_worker_loop(&job_receiver, &handler, worker_stop, &worker_active);
        });
        let _monitor = thread::spawn(move || {
            request_monitor_loop(active.as_ref(), monitor_fatal.as_ref());
        });
        let (response, _response_receiver) = sync_channel(1);
        job_sender
            .send(RequestJob {
                snapshot,
                request: RequestBytes::new(b"FIXTURE_TECNICA_ADMITTED_MUTATE".to_vec()),
                response,
                admission: Arc::new(RequestAdmission::new(
                    Instant::now() + Duration::from_millis(100),
                    stop,
                    fatal,
                )),
            })
            .expect("FIXTURE_TECNICA request submission");
        entered.wait();
        thread::sleep(Duration::from_millis(700));
        std::process::exit(126);
    }

    #[test]
    fn blocked_handler_shutdown_exits_the_dedicated_process_without_late_mutation() {
        let result = run_fatal_test_child("server::tests::blocked_handler_shutdown_fatal_child");
        assert_eq!(result.exit_code, Some(FATAL_HANDLER_EXIT_CODE));
        assert!(
            !result.marker_was_written,
            "FIXTURE_TECNICA blocked handler mutated after fatal shutdown containment"
        );
        assert!(
            result.socket_was_retained,
            "FIXTURE_TECNICA fatal shutdown did not retain the owned socket"
        );
    }

    #[test]
    #[ignore = "FIXTURE_TECNICA fatal child only"]
    fn blocked_handler_shutdown_fatal_child() {
        arm_fatal_test_watchdog();
        let metadata = SnapshotMetadata::new(1, 1, 1, 1, 1).expect("FIXTURE_TECNICA metadata");
        let snapshot = Arc::new(RuntimeSnapshot::new(
            metadata,
            MutatingState {
                mutated: AtomicBool::new(false),
            },
        ));
        let snapshots = Arc::new(SnapshotStore::new(snapshot).expect("FIXTURE_TECNICA store"));
        let entered = Arc::new(Barrier::new(2));
        let handler = Arc::new(DelayedExternalMutation {
            entered: Arc::clone(&entered),
            marker: late_mutation_marker(std::process::id()),
        });
        let path = path();
        let config = ServerConfig::with_limits(path.clone(), 1, 1, 1, 500).expect("config");
        let server = Arc::new(UnixServer::bind(config, snapshots, handler).expect("bind"));
        let stop = Arc::new(AtomicBool::new(false));
        let runner = {
            let server = Arc::clone(&server);
            let stop = Arc::clone(&stop);
            thread::spawn(move || server.run_until(&stop))
        };
        let mut client = UnixStream::connect(&path).expect("FIXTURE_TECNICA client");
        write_frame(&mut client, b"FIXTURE_TECNICA_ADMITTED_MUTATE")
            .expect("FIXTURE_TECNICA request");
        entered.wait();
        stop.store(true, Ordering::Release);
        let _ = runner.join();
        thread::sleep(Duration::from_millis(700));
        std::process::exit(126);
    }

    #[test]
    fn handler_panic_exits_the_dedicated_process_and_retains_its_socket() {
        let result = run_fatal_test_child("server::tests::handler_panic_fatal_child");
        assert_eq!(result.exit_code, Some(FATAL_HANDLER_EXIT_CODE));
        assert!(
            result.socket_was_retained,
            "FIXTURE_TECNICA handler panic did not retain the owned socket"
        );
    }

    #[test]
    #[ignore = "FIXTURE_TECNICA fatal child only"]
    fn handler_panic_fatal_child() {
        arm_fatal_test_watchdog();
        let path = path();
        let config = ServerConfig::with_limits(path.clone(), 1, 1, 1, 500).expect("config");
        let server =
            Arc::new(UnixServer::bind(config, snapshot(), Arc::new(Panicking)).expect("bind"));
        let stop = Arc::new(AtomicBool::new(false));
        let _runner = {
            let server = Arc::clone(&server);
            let stop = Arc::clone(&stop);
            thread::spawn(move || server.run_until(&stop))
        };
        let mut client = UnixStream::connect(&path).expect("FIXTURE_TECNICA client");

        write_frame(&mut client, b"FIXTURE_TECNICA_PANIC").expect("FIXTURE_TECNICA request");
        thread::sleep(Duration::from_millis(700));
        std::process::exit(126);
    }

    #[test]
    fn queued_request_timeout_skips_state_mutation_after_worker_drains() {
        let metadata = SnapshotMetadata::new(1, 1, 1, 1, 1).expect("FIXTURE_TECNICA metadata");
        let snapshot = Arc::new(RuntimeSnapshot::new(
            metadata,
            MutatingState {
                mutated: AtomicBool::new(false),
            },
        ));
        let blocker = Arc::new(Barrier::new(2));
        let handler = BlockingMutation {
            blocker: Arc::clone(&blocker),
        };
        let (job_sender, job_receiver) = sync_channel(2);
        let job_receiver = Mutex::new(job_receiver);
        let shutdown = Arc::new(AtomicBool::new(false));
        let active = Arc::new(Mutex::new(WorkerSlot::default()));
        let fatal = detached_fatal_process();
        let worker_shutdown = Arc::clone(&shutdown);
        let worker_active = Arc::clone(&active);
        let worker = thread::spawn(move || {
            request_worker_loop(&job_receiver, &handler, worker_shutdown, &worker_active)
        });

        let blocker_sender = job_sender.clone();
        let blocker_snapshot = Arc::clone(&snapshot);
        let blocker_shutdown = Arc::clone(&shutdown);
        let blocker_fatal = Arc::clone(&fatal);
        let blocker_request = thread::spawn(move || {
            execute_request_until(
                &blocker_sender,
                blocker_snapshot,
                b"FIXTURE_TECNICA_BLOCKER".to_vec(),
                Instant::now() + Duration::from_secs(1),
                blocker_shutdown,
                blocker_fatal,
            )
        });
        blocker.wait();

        let timeout = execute_request_until(
            &job_sender,
            Arc::clone(&snapshot),
            b"FIXTURE_TECNICA_MUTATE".to_vec(),
            Instant::now() + Duration::from_millis(10),
            Arc::clone(&shutdown),
            Arc::clone(&fatal),
        )
        .expect_err("FIXTURE_TECNICA queued mutation must expire");
        assert_eq!(timeout.code(), ServerErrorCode::RequestTimeout);
        assert!(!snapshot.state().mutated.load(Ordering::Acquire));

        blocker.wait();
        assert_eq!(
            blocker_request
                .join()
                .expect("FIXTURE_TECNICA blocker join")
                .expect("FIXTURE_TECNICA blocker response"),
            b"FIXTURE_TECNICA_BLOCKER_DONE"
        );
        assert_eq!(
            execute_request_until(
                &job_sender,
                Arc::clone(&snapshot),
                b"FIXTURE_TECNICA_DRAIN".to_vec(),
                Instant::now() + Duration::from_secs(1),
                Arc::clone(&shutdown),
                Arc::clone(&fatal),
            )
            .expect("FIXTURE_TECNICA drain response"),
            b"FIXTURE_TECNICA_DRAINED"
        );
        assert!(!snapshot.state().mutated.load(Ordering::Acquire));

        drop(job_sender);
        worker.join().expect("FIXTURE_TECNICA worker join");
    }

    #[test]
    fn request_monitor_expires_only_an_admitted_active_request() {
        let shutdown = Arc::new(AtomicBool::new(false));
        let fatal = detached_fatal_process();
        let completed = Arc::new(RequestAdmission::new(
            Instant::now() + Duration::from_millis(10),
            Arc::clone(&shutdown),
            Arc::clone(&fatal),
        ));
        assert!(completed.try_admit());
        assert!(completed.try_complete());
        let completed_slot = WorkerSlot {
            admission: Some(completed),
            finished: true,
        };
        assert!(
            !monitor_requires_fatal(&completed_slot, Instant::now() + Duration::from_secs(1),),
            "FIXTURE_TECNICA completed request must not trigger fatal containment"
        );

        let admitted = Arc::new(RequestAdmission::new(
            Instant::now() + Duration::from_secs(1),
            shutdown,
            fatal,
        ));
        assert!(admitted.try_admit());
        let mut admitted_slot = WorkerSlot {
            admission: Some(Arc::clone(&admitted)),
            finished: false,
        };
        assert!(!monitor_requires_fatal(&admitted_slot, Instant::now()));
        admitted_slot.finished = true;
        assert!(
            monitor_requires_fatal(&admitted_slot, Instant::now()),
            "FIXTURE_TECNICA retired admitted worker must be fatal"
        );
        assert!(
            monitor_requires_fatal(&admitted_slot, admitted.deadline),
            "FIXTURE_TECNICA expired and retired admission must stay fatal"
        );
    }

    #[test]
    fn expired_active_request_stays_monitor_fatal_across_preempted_settlement() {
        let shutdown = Arc::new(AtomicBool::new(false));
        let fatal = detached_fatal_process();
        let admission = Arc::new(RequestAdmission::new(
            Instant::now() + Duration::from_secs(30),
            shutdown,
            fatal,
        ));
        assert!(admission.try_admit());
        let active = Arc::new(Mutex::new(WorkerSlot {
            admission: Some(Arc::clone(&admission)),
            finished: false,
        }));

        let (settlement_paused, settlement_paused_receiver) = sync_channel(0);
        let (release_settlement, settlement_release_receiver) = sync_channel(0);
        let settlement_admission = Arc::clone(&admission);
        let settlement = thread::spawn(move || {
            assert!(settlement_admission.expire());
            settlement_paused
                .send(())
                .expect("FIXTURE_TECNICA settlement pause");
            settlement_release_receiver
                .recv()
                .expect("FIXTURE_TECNICA settlement release");
        });
        settlement_paused_receiver
            .recv()
            .expect("FIXTURE_TECNICA expired transition");

        let (handler_paused, handler_paused_receiver) = sync_channel(0);
        let (release_handler, handler_release_receiver) = sync_channel(0);
        let handler_active = Arc::clone(&active);
        let handler_admission = Arc::clone(&admission);
        let handler = thread::spawn(move || {
            assert!(!complete_active_request(
                handler_active.as_ref(),
                &handler_admission,
            ));
            handler_paused
                .send(())
                .expect("FIXTURE_TECNICA handler pause");
            handler_release_receiver
                .recv()
                .expect("FIXTURE_TECNICA handler release");
        });
        handler_paused_receiver
            .recv()
            .expect("FIXTURE_TECNICA failed completion");

        {
            let slot = active.lock().expect("FIXTURE_TECNICA active request slot");
            assert!(
                slot.admission
                    .as_ref()
                    .is_some_and(|current| Arc::ptr_eq(current, &admission)),
                "FIXTURE_TECNICA expired handler must remain installed"
            );
            assert!(
                monitor_requires_fatal(&slot, admission.deadline),
                "FIXTURE_TECNICA monitor ignored expired active work"
            );
        }

        release_handler
            .send(())
            .expect("FIXTURE_TECNICA release handler");
        release_settlement
            .send(())
            .expect("FIXTURE_TECNICA release settlement");
        handler.join().expect("FIXTURE_TECNICA handler join");
        settlement.join().expect("FIXTURE_TECNICA settlement join");
    }

    #[test]
    fn slow_fragmented_frame_obeys_one_absolute_deadline() {
        run_in_sanitized_child(
            "server::tests::slow_fragmented_frame_obeys_one_absolute_deadline",
            || {
                let path = path();
                let config = ServerConfig::with_limits(path.clone(), 1, 1, 1, 100).expect("config");
                let server =
                    Arc::new(UnixServer::bind(config, snapshot(), Arc::new(Echo)).expect("bind"));
                let stop = Arc::new(AtomicBool::new(false));
                let runner = {
                    let server = Arc::clone(&server);
                    let stop = Arc::clone(&stop);
                    thread::spawn(move || server.run_until(&stop))
                };
                let mut client = UnixStream::connect(&path).expect("local client");
                client
                    .set_read_timeout(Some(Duration::from_millis(300)))
                    .expect("FIXTURE_TECNICA client timeout");
                let payload = b"FIXTURE_TECNICA_SLOW_FRAGMENT";
                let header = u32::try_from(payload.len())
                    .expect("FIXTURE_TECNICA frame length")
                    .to_be_bytes();
                let started = Instant::now();
                for byte in header {
                    if client.write_all(&[byte]).is_err() {
                        break;
                    }
                    thread::sleep(Duration::from_millis(35));
                }
                let _ = client.write_all(payload);
                assert_eq!(
                    read_frame(&mut client)
                        .expect_err("slow fragments must not renew the frame deadline")
                        .code(),
                    ServerErrorCode::FrameIo
                );
                assert!(
                    started.elapsed() < Duration::from_millis(250),
                    "absolute frame deadline was renewed"
                );

                stop.store(true, Ordering::Release);
                assert_eq!(runner.join().expect("runner"), Ok(()));
                drop(server);
                fs::remove_file(&path).expect("test socket retirement");
                fs::remove_dir(path.parent().expect("parent")).expect("test directory cleanup");
            },
        );
    }

    #[test]
    fn request_storage_is_explicitly_erased_after_handling() {
        let erase_audit = Arc::new(Mutex::new(Vec::new()));
        let (job_sender, job_receiver) = sync_channel(1);
        let job_receiver = Mutex::new(job_receiver);
        let shutdown = Arc::new(AtomicBool::new(false));
        let active = Arc::new(Mutex::new(WorkerSlot::default()));
        let fatal = detached_fatal_process();
        let worker_shutdown = Arc::clone(&shutdown);
        let worker_active = Arc::clone(&active);
        let worker = thread::spawn(move || {
            request_worker_loop(&job_receiver, &Echo, worker_shutdown, &worker_active)
        });
        let (response_sender, response_receiver) = sync_channel(1);
        let canary = b"FIXTURE_TECNICA_REQUEST_STORAGE_CANARY".to_vec();
        job_sender
            .send(RequestJob {
                snapshot: snapshot().lease().expect("FIXTURE_TECNICA lease"),
                request: RequestBytes::with_erase_audit(canary.clone(), Arc::clone(&erase_audit)),
                response: response_sender,
                admission: Arc::new(RequestAdmission::new(
                    Instant::now() + Duration::from_secs(1),
                    Arc::clone(&shutdown),
                    fatal,
                )),
            })
            .expect("FIXTURE_TECNICA request submission");
        assert!(
            response_receiver
                .recv()
                .expect("FIXTURE_TECNICA worker response")
                .is_ok()
        );
        assert_eq!(
            erase_audit
                .lock()
                .expect("FIXTURE_TECNICA erase audit")
                .as_slice(),
            vec![0; canary.len()]
        );
        drop(job_sender);
        worker.join().expect("FIXTURE_TECNICA worker join");
    }

    #[test]
    fn bind_rejects_the_actual_process_environment_when_a_canary_is_present() {
        if std::env::var_os("FIXTURE_TECNICA_TOKEN").is_none() {
            let status =
                Command::new(std::env::current_exe().expect("FIXTURE_TECNICA test binary"))
                    .arg("--exact")
                    .arg("server::tests::bind_rejects_the_actual_process_environment_when_a_canary_is_present")
                    .arg("--nocapture")
                    .env_clear()
                    .envs(SANITIZED_TEST_ENVIRONMENT)
                    .env(
                        "FIXTURE_TECNICA_TOKEN",
                        "FIXTURE_TECNICA_PRIVATE_CANARY",
                    )
                    .status()
                    .expect("FIXTURE_TECNICA canary child");
            assert!(status.success(), "canary child failed");
            return;
        }

        let path = path();
        let config = ServerConfig::new(path.clone()).expect("config");
        let error = UnixServer::bind(config, snapshot(), Arc::new(Echo))
            .expect_err("live canary must reject");
        assert_eq!(error.code(), ServerErrorCode::UnsanitizedEnvironment);
        assert!(!path.exists());
        fs::remove_dir(path.parent().expect("parent")).expect("test directory cleanup");
    }

    #[test]
    fn occupied_paths_are_never_removed_or_replaced() {
        run_in_sanitized_child(
            "server::tests::occupied_paths_are_never_removed_or_replaced",
            || {
                let path = path();
                fs::write(&path, b"FIXTURE_TECNICA_OCCUPIED").expect("occupied path");
                let config = ServerConfig::new(path.clone()).expect("config");
                let error = UnixServer::bind(config, snapshot(), Arc::new(Echo))
                    .expect_err("occupied path");
                assert_eq!(error.code(), ServerErrorCode::SocketPathOccupied);
                assert_eq!(
                    fs::read(&path).expect("retained occupied path"),
                    b"FIXTURE_TECNICA_OCCUPIED"
                );
                fs::remove_file(&path).expect("test cleanup");
                fs::remove_dir(path.parent().expect("parent")).expect("test directory cleanup");
            },
        );
    }

    #[test]
    fn socket_retirement_never_removes_a_replacement_path() {
        run_in_sanitized_child(
            "server::tests::socket_retirement_never_removes_a_replacement_path",
            || {
                let path = path();
                let config = ServerConfig::new(path.clone()).expect("config");
                let server = UnixServer::bind(config, snapshot(), Arc::new(Echo)).expect("bind");
                fs::remove_file(&path).expect("unlink active name");
                fs::write(&path, b"FIXTURE_TECNICA_REPLACEMENT").expect("replacement");
                drop(server);
                assert_eq!(
                    fs::read(&path).expect("replacement retained"),
                    b"FIXTURE_TECNICA_REPLACEMENT"
                );
                fs::remove_file(&path).expect("replacement cleanup");
                fs::remove_dir(path.parent().expect("parent")).expect("test directory cleanup");
            },
        );
    }

    #[test]
    fn shared_socket_parent_is_rejected() {
        run_in_sanitized_child("server::tests::shared_socket_parent_is_rejected", || {
            let path = std::path::PathBuf::from(format!(
                "/private/tmp/FIXTURE_TECNICA_nlu_shared_{}.sock",
                std::process::id()
            ));
            let config = ServerConfig::new(path).expect("config");
            let error =
                UnixServer::bind(config, snapshot(), Arc::new(Echo)).expect_err("shared parent");
            assert_eq!(error.code(), ServerErrorCode::SocketConfiguration);
        });
    }
}
