use std::env;
use std::io::Read;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use std::time::Instant;

use addon_runtime::{RuntimeError, read_catalog_seed};
use nlu_core::{LogicalClock, LogicalTime};
use nlu_server::{
    NluRequestHandler, NluRuntime, PeerIdentity, RuntimeSnapshot, ServerConfig, SnapshotMetadata,
    SnapshotStore, UnixServer,
};
use policy_engine::{ConfirmationConfig, PolicyGeneration};
use runtime_security::{DroppedPrivileges, GuardedProcess, OutboundNetworkGuard, ServiceIdentity};
use session_engine::SessionConfig;

const STATE_TTL_TICKS: u64 = 300_000;

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => ExitCode::FAILURE,
    }
}

fn run() -> Result<(), RuntimeError> {
    let arguments = Arguments::parse(env::args_os().skip(1))?;
    let _hardening = GuardedProcess::harden().map_err(|_| RuntimeError::ServerStartup)?;
    let identity = ServiceIdentity::new(
        arguments.server_uid,
        arguments.server_gid,
        arguments.ipc_gid,
    )
    .map_err(|_| RuntimeError::InvalidProcessConfiguration)?;
    let privileges =
        DroppedPrivileges::establish(identity).map_err(|_| RuntimeError::ServerStartup)?;
    privileges
        .verify()
        .map_err(|_| RuntimeError::ServerStartup)?;

    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let seed = read_catalog_seed(&mut input)?;
    let mut trailing = [0_u8; 1];
    if input
        .read(&mut trailing)
        .map_err(|_| RuntimeError::CatalogFrame)?
        != 0
    {
        return Err(RuntimeError::CatalogFrame);
    }
    drop(input);
    let catalog_generation = seed.generation();
    let catalog = seed.into_snapshot()?;
    let metadata = SnapshotMetadata::new(1, catalog_generation, 1, 1, 1)
        .map_err(|_| RuntimeError::ServerStartup)?;
    let runtime = NluRuntime::standard(
        metadata,
        catalog,
        PolicyGeneration::new(1).map_err(|_| RuntimeError::ServerStartup)?,
        SessionConfig::new(STATE_TTL_TICKS).map_err(|_| RuntimeError::ServerStartup)?,
        ConfirmationConfig::new(STATE_TTL_TICKS).map_err(|_| RuntimeError::ServerStartup)?,
    )
    .map_err(|_| RuntimeError::ServerStartup)?;
    let snapshots = Arc::new(
        SnapshotStore::new(Arc::new(RuntimeSnapshot::new(metadata, runtime)))
            .map_err(|_| RuntimeError::ServerStartup)?,
    );
    let config = ServerConfig::with_limits_for_peer(
        arguments.socket,
        PeerIdentity::new(arguments.adapter_uid, arguments.adapter_gid),
        arguments.ipc_gid,
        2,
        16,
        nlu_server::MAX_FRAMES_PER_CONNECTION,
        5_000,
    )
    .map_err(|_| RuntimeError::ServerStartup)?;
    let server = UnixServer::bind(
        config,
        snapshots,
        Arc::new(NluRequestHandler::new(ProcessClock::new())),
    )
    .map_err(|_| RuntimeError::ServerStartup)?;
    let network = OutboundNetworkGuard::install().map_err(|_| RuntimeError::ServerStartup)?;
    network.verify().map_err(|_| RuntimeError::ServerStartup)?;
    let stop = AtomicBool::new(false);
    server
        .run_until(&stop)
        .map_err(|_| RuntimeError::ServerStartup)
}

struct ProcessClock {
    start: Instant,
}

impl ProcessClock {
    fn new() -> Self {
        Self {
            start: Instant::now(),
        }
    }
}

impl LogicalClock for ProcessClock {
    fn now(&self) -> LogicalTime {
        let ticks = u64::try_from(self.start.elapsed().as_millis()).unwrap_or(u64::MAX);
        LogicalTime::from_ticks(ticks)
    }
}

struct Arguments {
    socket: PathBuf,
    adapter_uid: u32,
    adapter_gid: u32,
    server_uid: u32,
    server_gid: u32,
    ipc_gid: u32,
}

impl Arguments {
    fn parse(
        arguments: impl IntoIterator<Item = std::ffi::OsString>,
    ) -> Result<Self, RuntimeError> {
        let mut values = arguments.into_iter();
        let mut socket = None;
        let mut adapter_uid = None;
        let mut adapter_gid = None;
        let mut server_uid = None;
        let mut server_gid = None;
        let mut ipc_gid = None;
        while let Some(flag) = values.next() {
            let value = values
                .next()
                .ok_or(RuntimeError::InvalidProcessConfiguration)?;
            match flag.to_str() {
                Some("--socket") if socket.is_none() => socket = Some(PathBuf::from(value)),
                Some("--adapter-uid") if adapter_uid.is_none() => adapter_uid = parse_u32(&value),
                Some("--adapter-gid") if adapter_gid.is_none() => adapter_gid = parse_u32(&value),
                Some("--server-uid") if server_uid.is_none() => server_uid = parse_u32(&value),
                Some("--server-gid") if server_gid.is_none() => server_gid = parse_u32(&value),
                Some("--ipc-gid") if ipc_gid.is_none() => ipc_gid = parse_u32(&value),
                _ => return Err(RuntimeError::InvalidProcessConfiguration),
            }
        }
        let parsed = Self {
            socket: socket.ok_or(RuntimeError::InvalidProcessConfiguration)?,
            adapter_uid: adapter_uid.ok_or(RuntimeError::InvalidProcessConfiguration)?,
            adapter_gid: adapter_gid.ok_or(RuntimeError::InvalidProcessConfiguration)?,
            server_uid: server_uid.ok_or(RuntimeError::InvalidProcessConfiguration)?,
            server_gid: server_gid.ok_or(RuntimeError::InvalidProcessConfiguration)?,
            ipc_gid: ipc_gid.ok_or(RuntimeError::InvalidProcessConfiguration)?,
        };
        RuntimeIdentitiesCheck::validate(&parsed)?;
        Ok(parsed)
    }
}

struct RuntimeIdentitiesCheck;

impl RuntimeIdentitiesCheck {
    fn validate(arguments: &Arguments) -> Result<(), RuntimeError> {
        addon_runtime::RuntimeIdentities::new(
            arguments.adapter_uid,
            arguments.adapter_gid,
            arguments.server_uid,
            arguments.server_gid,
            arguments.ipc_gid,
        )?;
        if !arguments.socket.is_absolute() || arguments.socket.file_name().is_none() {
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        Ok(())
    }
}

fn parse_u32(value: &std::ffi::OsStr) -> Option<u32> {
    value.to_str()?.parse().ok()
}
