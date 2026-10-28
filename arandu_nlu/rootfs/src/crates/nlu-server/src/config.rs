use std::ffi::{OsStr, OsString};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};

use crate::peer;
use crate::{Result, ServerError, ServerErrorCode};

pub const MAX_WORKERS: u16 = 8;
pub const MAX_CONNECTION_QUEUE: u16 = 64;
pub const MAX_FRAMES_PER_CONNECTION: u16 = 8;
pub const MAX_SOCKET_PATH_BYTES: usize = 96;
pub const MIN_FRAME_TIMEOUT_MILLIS: u64 = 10;
pub const MAX_FRAME_TIMEOUT_MILLIS: u64 = 30_000;

const ALLOWED_ENVIRONMENT: [(&str, &str); 7] = [
    ("HOME", "/nonexistent"),
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("PATH", "/usr/bin:/bin"),
    ("RUST_BACKTRACE", "0"),
    ("TMPDIR", "/tmp"),
    ("TZ", "UTC"),
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct SanitizedEnvironment {
    _private: (),
}

impl SanitizedEnvironment {
    fn validate<I, K, V>(variables: I) -> Result<Self>
    where
        I: IntoIterator<Item = (K, V)>,
        K: Into<OsString>,
        V: Into<OsString>,
    {
        let mut seen = Vec::<OsString>::new();
        for (key, value) in variables {
            let key = key.into();
            let value = value.into();
            let Some((_, expected)) = ALLOWED_ENVIRONMENT
                .iter()
                .find(|(allowed, _)| key == OsStr::new(allowed))
            else {
                return Err(ServerError::new(ServerErrorCode::UnsanitizedEnvironment));
            };
            if value != OsStr::new(expected) || seen.contains(&key) {
                return Err(ServerError::new(ServerErrorCode::UnsanitizedEnvironment));
            }
            seen.push(key);
        }
        Ok(Self { _private: () })
    }

    pub(crate) fn validate_current() -> Result<Self> {
        Self::validate(std::env::vars_os())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PeerIdentity {
    uid: u32,
    gid: u32,
}

impl PeerIdentity {
    #[must_use]
    pub const fn new(uid: u32, gid: u32) -> Self {
        Self { uid, gid }
    }

    pub fn current() -> Result<Self> {
        peer::current_identity()
    }

    pub fn from_unix_stream(stream: &UnixStream) -> Result<Self> {
        peer::stream_identity(stream)
    }

    #[must_use]
    pub const fn uid(self) -> u32 {
        self.uid
    }

    #[must_use]
    pub const fn gid(self) -> u32 {
        self.gid
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ServerConfig {
    socket_path: PathBuf,
    expected_peer: PeerIdentity,
    ipc_group: u32,
    workers: u16,
    queue_capacity: u16,
    frames_per_connection: u16,
    frame_timeout_millis: u64,
}

impl ServerConfig {
    pub fn new(socket_path: PathBuf) -> Result<Self> {
        Self::with_limits(socket_path, 2, 16, MAX_FRAMES_PER_CONNECTION, 5_000)
    }

    pub fn with_limits(
        socket_path: PathBuf,
        workers: u16,
        queue_capacity: u16,
        frames_per_connection: u16,
        frame_timeout_millis: u64,
    ) -> Result<Self> {
        validate_socket_path(&socket_path)?;
        let expected_peer = PeerIdentity::current()?;
        let parent = socket_path
            .parent()
            .ok_or_else(|| ServerError::new(ServerErrorCode::InvalidSocketPath))?;
        let ipc_group = std::fs::symlink_metadata(parent)
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?
            .gid();
        Self::with_limits_for_peer(
            socket_path,
            expected_peer,
            ipc_group,
            workers,
            queue_capacity,
            frames_per_connection,
            frame_timeout_millis,
        )
    }

    pub fn with_limits_for_peer(
        socket_path: PathBuf,
        expected_peer: PeerIdentity,
        ipc_group: u32,
        workers: u16,
        queue_capacity: u16,
        frames_per_connection: u16,
        frame_timeout_millis: u64,
    ) -> Result<Self> {
        validate_socket_path(&socket_path)?;
        if workers == 0
            || workers > MAX_WORKERS
            || queue_capacity == 0
            || queue_capacity > MAX_CONNECTION_QUEUE
            || frames_per_connection == 0
            || frames_per_connection > MAX_FRAMES_PER_CONNECTION
            || !(MIN_FRAME_TIMEOUT_MILLIS..=MAX_FRAME_TIMEOUT_MILLIS)
                .contains(&frame_timeout_millis)
        {
            return Err(ServerError::new(ServerErrorCode::InvalidConfiguration));
        }
        Ok(Self {
            socket_path,
            expected_peer,
            ipc_group,
            workers,
            queue_capacity,
            frames_per_connection,
            frame_timeout_millis,
        })
    }

    #[must_use]
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    #[must_use]
    pub const fn expected_peer(&self) -> PeerIdentity {
        self.expected_peer
    }

    #[must_use]
    pub const fn ipc_group(&self) -> u32 {
        self.ipc_group
    }

    #[must_use]
    pub const fn workers(&self) -> u16 {
        self.workers
    }

    #[must_use]
    pub const fn queue_capacity(&self) -> u16 {
        self.queue_capacity
    }

    #[must_use]
    pub const fn frames_per_connection(&self) -> u16 {
        self.frames_per_connection
    }

    #[must_use]
    pub const fn frame_timeout_millis(&self) -> u64 {
        self.frame_timeout_millis
    }
}

fn validate_socket_path(path: &Path) -> Result<()> {
    let bytes = path.as_os_str().as_bytes();
    if !path.is_absolute()
        || bytes.is_empty()
        || bytes.len() > MAX_SOCKET_PATH_BYTES
        || bytes.contains(&0)
        || path.file_name().is_none()
    {
        return Err(ServerError::new(ServerErrorCode::InvalidSocketPath));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn socket_path() -> PathBuf {
        PathBuf::from("/tmp/FIXTURE_TECNICA_nlu.sock")
    }

    #[test]
    fn environment_is_an_exact_value_allowlist_and_retains_no_values() {
        let exact = ALLOWED_ENVIRONMENT
            .iter()
            .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()));
        assert_eq!(
            SanitizedEnvironment::validate(exact),
            Ok(SanitizedEnvironment { _private: () })
        );
        for variables in [
            vec![(
                "FIXTURE_TECNICA_TOKEN".to_owned(),
                "FIXTURE_TECNICA_PRIVATE_CANARY".to_owned(),
            )],
            vec![(
                "PATH".to_owned(),
                "FIXTURE_TECNICA_PRIVATE_CANARY".to_owned(),
            )],
            vec![
                ("TZ".to_owned(), "UTC".to_owned()),
                ("TZ".to_owned(), "UTC".to_owned()),
            ],
        ] {
            let error = SanitizedEnvironment::validate(variables).expect_err("must reject");
            assert_eq!(error.code(), ServerErrorCode::UnsanitizedEnvironment);
            assert!(!format!("{error:?}").contains("PRIVATE_CANARY"));
        }
        assert_eq!(
            core::mem::size_of::<SanitizedEnvironment>(),
            0,
            "validated environment values are not retained"
        );
    }

    #[test]
    fn configuration_limits_are_exact() {
        assert!(
            ServerConfig::with_limits(
                socket_path(),
                MAX_WORKERS,
                MAX_CONNECTION_QUEUE,
                MAX_FRAMES_PER_CONNECTION,
                MAX_FRAME_TIMEOUT_MILLIS,
            )
            .is_ok()
        );
        for result in [
            ServerConfig::with_limits(socket_path(), 0, 1, 1, 10),
            ServerConfig::with_limits(socket_path(), MAX_WORKERS + 1, 1, 1, 10),
            ServerConfig::with_limits(socket_path(), 1, 0, 1, 10),
            ServerConfig::with_limits(socket_path(), 1, MAX_CONNECTION_QUEUE + 1, 1, 10),
            ServerConfig::with_limits(socket_path(), 1, 1, 0, 10),
            ServerConfig::with_limits(socket_path(), 1, 1, MAX_FRAMES_PER_CONNECTION + 1, 10),
            ServerConfig::with_limits(socket_path(), 1, 1, 1, MIN_FRAME_TIMEOUT_MILLIS - 1),
            ServerConfig::with_limits(socket_path(), 1, 1, 1, MAX_FRAME_TIMEOUT_MILLIS + 1),
        ] {
            assert_eq!(
                result.expect_err("one-over or zero must reject").code(),
                ServerErrorCode::InvalidConfiguration
            );
        }
    }

    #[test]
    fn explicit_peer_and_ipc_group_are_retained_without_environment_data() {
        let config = ServerConfig::with_limits_for_peer(
            socket_path(),
            PeerIdentity::new(42, 43),
            44,
            1,
            1,
            1,
            10,
        )
        .expect("FIXTURE_TECNICA explicit peer");
        assert_eq!(config.expected_peer(), PeerIdentity::new(42, 43));
        assert_eq!(config.ipc_group(), 44);
    }

    #[test]
    fn socket_path_is_absolute_and_bounded() {
        assert!(ServerConfig::new(socket_path()).is_ok());
        assert_eq!(
            ServerConfig::new(PathBuf::from("relative.sock"))
                .expect_err("relative path")
                .code(),
            ServerErrorCode::InvalidSocketPath
        );
        let oversized = format!("/tmp/{}", "A".repeat(MAX_SOCKET_PATH_BYTES));
        assert_eq!(
            ServerConfig::new(PathBuf::from(oversized))
                .expect_err("oversized path")
                .code(),
            ServerErrorCode::InvalidSocketPath
        );
    }
}
