use std::ffi::OsStr;
use std::fs;
use std::io::ErrorKind;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt, chown};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

use runtime_security::ServiceIdentity;

use crate::{Result, RuntimeError};

pub const DEFAULT_ADAPTER_UID: u32 = 21_000;
pub const DEFAULT_ADAPTER_GID: u32 = 21_000;
pub const DEFAULT_SERVER_UID: u32 = 21_001;
pub const DEFAULT_SERVER_GID: u32 = 21_001;
pub const DEFAULT_IPC_GID: u32 = 21_002;
pub const DEFAULT_GENERATION_ROOT: &str = "/run/ptbr-nlu/adapter-server/generations";

const PRODUCT_RUNTIME_PARENT: &str = "/run";
const PRODUCT_DIRECTORY_COMPONENTS: [&str; 3] = ["ptbr-nlu", "adapter-server", "generations"];
const GENERATION_PREFIX: &str = "generation-";
const GENERATION_DIGITS: usize = 16;
const SOCKET_BASENAME: &str = "adapter-server.sock";
const GENERATION_MODE: u32 = 0o770;
const DIRECTORY_CREATION_MODE: u32 = 0o700;
const SERVER_ENVIRONMENT: [(&str, &str); 7] = [
    ("HOME", "/nonexistent"),
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("PATH", "/usr/bin:/bin"),
    ("RUST_BACKTRACE", "0"),
    ("TMPDIR", "/tmp"),
    ("TZ", "UTC"),
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RuntimeIdentities {
    adapter: ServiceIdentity,
    server: ServiceIdentity,
}

impl RuntimeIdentities {
    pub fn new(
        adapter_uid: u32,
        adapter_gid: u32,
        server_uid: u32,
        server_gid: u32,
        ipc_gid: u32,
    ) -> Result<Self> {
        if adapter_uid == server_uid {
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        let adapter = ServiceIdentity::new(adapter_uid, adapter_gid, ipc_gid)
            .map_err(|_| RuntimeError::InvalidProcessConfiguration)?;
        let server = ServiceIdentity::new(server_uid, server_gid, ipc_gid)
            .map_err(|_| RuntimeError::InvalidProcessConfiguration)?;
        Ok(Self { adapter, server })
    }

    pub fn product() -> Result<Self> {
        Self::new(
            DEFAULT_ADAPTER_UID,
            DEFAULT_ADAPTER_GID,
            DEFAULT_SERVER_UID,
            DEFAULT_SERVER_GID,
            DEFAULT_IPC_GID,
        )
    }

    #[must_use]
    pub const fn adapter(self) -> ServiceIdentity {
        self.adapter
    }

    #[must_use]
    pub const fn server(self) -> ServiceIdentity {
        self.server
    }

    #[must_use]
    pub const fn ipc_gid(self) -> u32 {
        self.adapter.ipc_gid()
    }
}

pub struct GenerationDirectory {
    path: PathBuf,
    socket_path: PathBuf,
}

impl GenerationDirectory {
    pub fn create(root: &Path, identities: RuntimeIdentities) -> Result<Self> {
        validate_root(root, identities)?;
        let next = next_generation(root, identities)?;
        let name = format!("{GENERATION_PREFIX}{next:0GENERATION_DIGITS$x}");
        let path = root.join(name);
        fs::create_dir(&path).map_err(|_| RuntimeError::RuntimeDirectory)?;
        if configure_directory(&path, identities.adapter().uid(), identities.ipc_gid()).is_err() {
            return Err(RuntimeError::RuntimeDirectorySecurity);
        }
        let socket_path = path.join(SOCKET_BASENAME);
        if socket_path.as_os_str().as_encoded_bytes().len() > nlu_server::MAX_SOCKET_PATH_BYTES {
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        Ok(Self { path, socket_path })
    }

    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    #[must_use]
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }
}

impl core::fmt::Debug for GenerationDirectory {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("GenerationDirectory")
            .field("path", &self.path)
            .finish_non_exhaustive()
    }
}

/// Creates and verifies the fixed product generation-root hierarchy.
///
/// Existing directories must already have the exact product ownership, mode,
/// shape, and entry set. The function never repairs or removes an existing
/// entry. The returned path is therefore safe to pass to
/// [`GenerationDirectory::create`].
pub fn prepare_product_generation_root(identities: RuntimeIdentities) -> Result<PathBuf> {
    let runtime_parent = Path::new(PRODUCT_RUNTIME_PARENT);
    let expected = product_generation_root(runtime_parent);
    if expected != Path::new(DEFAULT_GENERATION_ROOT) || !is_exact_absolute(&expected) {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }
    prepare_generation_root_under(runtime_parent, identities)
}

pub fn configure_server_command(
    executable: &Path,
    generation: &GenerationDirectory,
    identities: RuntimeIdentities,
) -> Result<Command> {
    if !executable.is_absolute() || executable.file_name().is_none() {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }
    let mut command = Command::new(executable);
    command
        .arg("--socket")
        .arg(generation.socket_path())
        .arg("--adapter-uid")
        .arg(identities.adapter().uid().to_string())
        .arg("--adapter-gid")
        .arg(identities.adapter().primary_gid().to_string())
        .arg("--server-uid")
        .arg(identities.server().uid().to_string())
        .arg("--server-gid")
        .arg(identities.server().primary_gid().to_string())
        .arg("--ipc-gid")
        .arg(identities.ipc_gid().to_string())
        .env_clear()
        .envs(SERVER_ENVIRONMENT)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    Ok(command)
}

pub(crate) fn prepare_generation_root_under(
    runtime_parent: &Path,
    identities: RuntimeIdentities,
) -> Result<PathBuf> {
    if !is_exact_absolute(runtime_parent) {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }
    validate_ancestor_directories(runtime_parent)?;

    let existing_depth = existing_managed_depth(runtime_parent, identities)?;
    for index in existing_depth..PRODUCT_DIRECTORY_COMPONENTS.len() {
        create_managed_directory(runtime_parent, index, identities)?;
    }

    verify_managed_prefix(
        runtime_parent,
        PRODUCT_DIRECTORY_COMPONENTS.len(),
        GENERATION_MODE,
        true,
        identities,
    )?;
    Ok(product_generation_root(runtime_parent))
}

fn product_generation_root(runtime_parent: &Path) -> PathBuf {
    PRODUCT_DIRECTORY_COMPONENTS
        .iter()
        .fold(runtime_parent.to_path_buf(), |path, component| {
            path.join(component)
        })
}

fn is_exact_absolute(path: &Path) -> bool {
    path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::RootDir | Component::Normal(_)))
}

fn validate_ancestor_directories(path: &Path) -> Result<()> {
    for ancestor in path.ancestors() {
        let metadata =
            fs::symlink_metadata(ancestor).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err(RuntimeError::RuntimeDirectorySecurity);
        }
    }
    Ok(())
}

fn existing_managed_depth(runtime_parent: &Path, identities: RuntimeIdentities) -> Result<usize> {
    let mut parent = runtime_parent.to_path_buf();
    for (index, component) in PRODUCT_DIRECTORY_COMPONENTS.iter().enumerate() {
        if index != 0 {
            validate_only_expected_entry(&parent, OsStr::new(component))?;
        }
        let path = parent.join(component);
        match fs::symlink_metadata(&path) {
            Ok(_) => validate_managed_directory(&path, identities)?,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(index),
            Err(_) => return Err(RuntimeError::RuntimeDirectorySecurity),
        }
        parent = path;
    }
    validate_generation_entries(&parent, identities)?;
    Ok(PRODUCT_DIRECTORY_COMPONENTS.len())
}

fn create_managed_directory(
    runtime_parent: &Path,
    index: usize,
    identities: RuntimeIdentities,
) -> Result<()> {
    let path = PRODUCT_DIRECTORY_COMPONENTS
        .iter()
        .take(index + 1)
        .fold(runtime_parent.to_path_buf(), |path, component| {
            path.join(component)
        });
    fs::DirBuilder::new()
        .mode(DIRECTORY_CREATION_MODE)
        .create(&path)
        .map_err(|_| RuntimeError::RuntimeDirectory)?;
    verify_managed_prefix(
        runtime_parent,
        index + 1,
        DIRECTORY_CREATION_MODE,
        false,
        identities,
    )?;

    let metadata =
        fs::symlink_metadata(&path).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if metadata.uid() != identities.adapter().uid() || metadata.gid() != identities.ipc_gid() {
        chown(
            &path,
            Some(identities.adapter().uid()),
            Some(identities.ipc_gid()),
        )
        .map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    }
    verify_managed_prefix(
        runtime_parent,
        index + 1,
        DIRECTORY_CREATION_MODE,
        true,
        identities,
    )?;

    fs::set_permissions(&path, fs::Permissions::from_mode(GENERATION_MODE))
        .map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    verify_managed_prefix(runtime_parent, index + 1, GENERATION_MODE, true, identities)
}

fn verify_managed_prefix(
    runtime_parent: &Path,
    depth: usize,
    last_mode: u32,
    last_owner_is_exact: bool,
    identities: RuntimeIdentities,
) -> Result<()> {
    validate_ancestor_directories(runtime_parent)?;
    if depth == 0 || depth > PRODUCT_DIRECTORY_COMPONENTS.len() {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }

    let mut path = runtime_parent.to_path_buf();
    for (index, component) in PRODUCT_DIRECTORY_COMPONENTS.iter().take(depth).enumerate() {
        path.push(component);
        let metadata =
            fs::symlink_metadata(&path).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
        let is_last = index + 1 == depth;
        let expected_mode = if is_last { last_mode } else { GENERATION_MODE };
        let owner_is_exact =
            metadata.uid() == identities.adapter().uid() && metadata.gid() == identities.ipc_gid();
        if metadata.file_type().is_symlink()
            || !metadata.is_dir()
            || metadata.permissions().mode() & 0o7777 != expected_mode
            || ((!is_last || last_owner_is_exact) && !owner_is_exact)
        {
            return Err(RuntimeError::RuntimeDirectorySecurity);
        }

        if !is_last {
            validate_only_expected_entry(
                &path,
                OsStr::new(PRODUCT_DIRECTORY_COMPONENTS[index + 1]),
            )?;
        } else if depth == PRODUCT_DIRECTORY_COMPONENTS.len() {
            validate_generation_entries(&path, identities)?;
        } else {
            validate_empty_directory(&path)?;
        }
    }
    Ok(())
}

fn validate_managed_directory(path: &Path, identities: RuntimeIdentities) -> Result<()> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.permissions().mode() & 0o7777 != GENERATION_MODE
        || metadata.uid() != identities.adapter().uid()
        || metadata.gid() != identities.ipc_gid()
    {
        return Err(RuntimeError::RuntimeDirectorySecurity);
    }
    Ok(())
}

fn validate_only_expected_entry(directory: &Path, expected: &OsStr) -> Result<()> {
    let entries = fs::read_dir(directory).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    for entry in entries {
        let entry = entry.map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
        if entry.file_name() != expected {
            return Err(RuntimeError::RuntimeDirectorySecurity);
        }
    }
    Ok(())
}

fn validate_empty_directory(directory: &Path) -> Result<()> {
    let mut entries =
        fs::read_dir(directory).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if entries.next().is_none() {
        Ok(())
    } else {
        Err(RuntimeError::RuntimeDirectorySecurity)
    }
}

fn validate_generation_entries(root: &Path, identities: RuntimeIdentities) -> Result<()> {
    let entries = fs::read_dir(root).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    for entry in entries {
        let entry = entry.map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
        parse_generation(&entry.file_name()).ok_or(RuntimeError::RuntimeDirectorySecurity)?;
        validate_generation(&entry.path(), identities)?;
    }
    Ok(())
}

fn validate_root(root: &Path, identities: RuntimeIdentities) -> Result<()> {
    if !root.is_absolute() || root.file_name().is_none() {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }
    let metadata =
        fs::symlink_metadata(root).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.permissions().mode() & 0o7777 != GENERATION_MODE
        || metadata.uid() != identities.adapter().uid()
        || metadata.gid() != identities.ipc_gid()
    {
        return Err(RuntimeError::RuntimeDirectorySecurity);
    }
    Ok(())
}

fn next_generation(root: &Path, identities: RuntimeIdentities) -> Result<u64> {
    let mut maximum = 0_u64;
    let entries = fs::read_dir(root).map_err(|_| RuntimeError::RuntimeDirectory)?;
    for entry in entries {
        let entry = entry.map_err(|_| RuntimeError::RuntimeDirectory)?;
        let name = entry.file_name();
        let value = parse_generation(&name).ok_or(RuntimeError::RuntimeDirectorySecurity)?;
        validate_generation(&entry.path(), identities)?;
        maximum = maximum.max(value);
    }
    maximum.checked_add(1).ok_or(RuntimeError::RuntimeDirectory)
}

fn parse_generation(name: &OsStr) -> Option<u64> {
    let text = name.to_str()?;
    let digits = text.strip_prefix(GENERATION_PREFIX)?;
    if digits.len() != GENERATION_DIGITS
        || !digits.bytes().all(|byte| byte.is_ascii_hexdigit())
        || digits.bytes().any(|byte| byte.is_ascii_uppercase())
    {
        return None;
    }
    u64::from_str_radix(digits, 16).ok()
}

fn validate_generation(path: &Path, identities: RuntimeIdentities) -> Result<()> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.permissions().mode() & 0o7777 != GENERATION_MODE
        || metadata.uid() != identities.adapter().uid()
        || metadata.gid() != identities.ipc_gid()
    {
        return Err(RuntimeError::RuntimeDirectorySecurity);
    }
    Ok(())
}

fn configure_directory(path: &Path, uid: u32, gid: u32) -> std::io::Result<()> {
    fs::set_permissions(path, fs::Permissions::from_mode(GENERATION_MODE))?;
    let metadata = fs::symlink_metadata(path)?;
    if metadata.uid() != uid || metadata.gid() != gid {
        chown(path, Some(uid), Some(gid))?;
    }
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.permissions().mode() & 0o7777 != GENERATION_MODE
        || metadata.uid() != uid
        || metadata.gid() != gid
    {
        return Err(std::io::Error::other("runtime directory verification"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;
    use std::sync::atomic::{AtomicU64, Ordering};

    use nlu_server::PeerIdentity;

    use super::*;

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(1);

    fn fixture_root() -> (PathBuf, RuntimeIdentities) {
        let current = PeerIdentity::current().expect("FIXTURE_TECNICA current identity");
        let suffix = NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed);
        let path = PathBuf::from(format!(
            "/tmp/FIXTURE_TECNICA_generation_{}_{}",
            std::process::id(),
            suffix
        ));
        fs::create_dir(&path).expect("FIXTURE_TECNICA create root");
        fs::set_permissions(&path, fs::Permissions::from_mode(GENERATION_MODE))
            .expect("FIXTURE_TECNICA root mode");
        chown(&path, Some(current.uid()), Some(current.gid()))
            .expect("FIXTURE_TECNICA root ownership");
        let adapter_gid = current.gid().checked_add(1).expect("FIXTURE_TECNICA gid");
        let server_gid = current.gid().checked_add(2).expect("FIXTURE_TECNICA gid");
        let server_uid = current.uid().checked_add(1).expect("FIXTURE_TECNICA uid");
        let identities = RuntimeIdentities::new(
            current.uid(),
            adapter_gid,
            server_uid,
            server_gid,
            current.gid(),
        )
        .expect("FIXTURE_TECNICA identities");
        (path, identities)
    }

    #[test]
    fn generations_are_fresh_monotonic_private_directories() {
        let (root, identities) = fixture_root();
        let first =
            GenerationDirectory::create(&root, identities).expect("FIXTURE_TECNICA generation");
        let second =
            GenerationDirectory::create(&root, identities).expect("FIXTURE_TECNICA generation");

        assert!(first.path().ends_with("generation-0000000000000001"));
        assert!(second.path().ends_with("generation-0000000000000002"));
        assert_ne!(first.socket_path(), second.socket_path());
        for path in [first.path(), second.path()] {
            let metadata = fs::symlink_metadata(path).expect("FIXTURE_TECNICA metadata");
            assert_eq!(metadata.permissions().mode() & 0o7777, GENERATION_MODE);
            assert_eq!(metadata.uid(), identities.adapter().uid());
            assert_eq!(metadata.gid(), identities.ipc_gid());
        }
        fs::remove_dir_all(root).expect("FIXTURE_TECNICA cleanup");
    }

    #[test]
    fn unknown_or_insecure_stale_entries_fail_closed() {
        let (root, identities) = fixture_root();
        fs::write(root.join("foreign"), b"FIXTURE_TECNICA").expect("FIXTURE_TECNICA foreign entry");

        assert_eq!(
            GenerationDirectory::create(&root, identities)
                .expect_err("foreign entry must fail")
                .code(),
            RuntimeError::RuntimeDirectorySecurity.code()
        );
        fs::remove_dir_all(root).expect("FIXTURE_TECNICA cleanup");
    }

    #[test]
    fn server_command_has_only_fixed_non_secret_environment() {
        let (root, identities) = fixture_root();
        let generation =
            GenerationDirectory::create(&root, identities).expect("FIXTURE_TECNICA generation");
        let command = configure_server_command(
            Path::new("/opt/ptbr-nlu/bin/nlu-server"),
            &generation,
            identities,
        )
        .expect("FIXTURE_TECNICA command");
        let environment = command
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_owned(),
                    value.expect("FIXTURE_TECNICA fixed value").to_owned(),
                )
            })
            .collect::<Vec<(OsString, OsString)>>();
        let expected = SERVER_ENVIRONMENT
            .iter()
            .map(|(key, value)| (OsString::from(key), OsString::from(value)))
            .collect::<Vec<_>>();

        assert_eq!(environment, expected);
        assert!(environment.iter().all(|(key, _)| key != "SUPERVISOR_TOKEN"));
        fs::remove_dir_all(root).expect("FIXTURE_TECNICA cleanup");
    }
}
