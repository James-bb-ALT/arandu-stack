#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::io::Write;
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use addon_runtime::{
    DEFAULT_HELPER_PAIRING_PORT, HelperReply, MAX_PAIRING_WIRE_BYTES, PairingBindings, Result,
    RuntimeError, accept_pairing, relay_companion_submission, write_helper_reply,
};
use nlu_server::PeerIdentity;
use runtime_security::{Credential, GuardedProcess, ParentDeathGuard};
use serde_json::json;

const PRODUCT_LOCAL_ROOT: &str = "/tmp";
const PRODUCT_CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const PRODUCT_IO_TIMEOUT: Duration = Duration::from_secs(10);
const PAIRING_IO_TIMEOUT: Duration = Duration::from_secs(300);
const DIRECTORY_MODE: u32 = 0o700;
const SOCKET_MODE: u32 = 0o600;

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => ExitCode::FAILURE,
    }
}

fn run() -> Result<()> {
    if env::args_os().count() != 1 {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }
    let parent_guard = ParentDeathGuard::establish().map_err(|_| RuntimeError::HelperStartup)?;
    let guard = GuardedProcess::harden().map_err(|_| RuntimeError::HelperStartup)?;
    parent_guard
        .verify()
        .map_err(|_| RuntimeError::HelperStartup)?;
    let mut credential = guard
        .read_credential_from_stdin()
        .map_err(|_| RuntimeError::HelperStartup)?;
    let result = run_hardened(&credential);
    credential.destroy();
    result
}

fn run_hardened(credential: &Credential) -> Result<()> {
    let pairing_credential = credential
        .expose_secret()
        .ok_or(RuntimeError::HelperStartup)?;
    let bindings = PairingBindings::derive(pairing_credential)?;
    let identity = PeerIdentity::current().map_err(|_| RuntimeError::HelperStartup)?;
    let endpoint = LocalEndpoint::bind(Path::new(PRODUCT_LOCAL_ROOT), bindings, identity)?;

    let pairing_listener = TcpListener::bind(SocketAddr::new(
        IpAddr::V4(Ipv4Addr::UNSPECIFIED),
        DEFAULT_HELPER_PAIRING_PORT,
    ))
    .map_err(|_| RuntimeError::HelperStartup)?;
    let (pairing_stream, peer_address) = pairing_listener
        .accept()
        .map_err(|_| RuntimeError::HelperStartup)?;
    configure_stream(&pairing_stream, PAIRING_IO_TIMEOUT)?;
    let offer = accept_pairing(pairing_stream, bindings, pairing_credential)?;
    let relay_address = relay_address(peer_address, offer.relay_port())?;
    drop(pairing_listener);

    write_proof(&endpoint, bindings, offer.peer_id())?;
    serve_local(
        endpoint,
        identity,
        relay_address,
        bindings,
        pairing_credential,
    )
}

fn serve_local(
    endpoint: LocalEndpoint,
    expected_peer: PeerIdentity,
    relay_address: SocketAddr,
    bindings: PairingBindings,
    pairing_credential: &[u8; 32],
) -> Result<()> {
    loop {
        let (mut local_stream, _) = endpoint
            .listener
            .accept()
            .map_err(|_| RuntimeError::IpcIo)?;
        let authenticated = PeerIdentity::from_unix_stream(&local_stream)
            .is_ok_and(|identity| identity == expected_peer);
        if !authenticated || configure_unix_stream(&local_stream).is_err() {
            let _ = local_stream.shutdown(Shutdown::Both);
            continue;
        }

        let result = relay_companion_submission(
            &mut local_stream,
            || connect_adapter(relay_address),
            bindings.epoch(),
            pairing_credential,
        );
        if let Err(error) = result
            && let Ok(rejection) = HelperReply::rejected(error.code())
        {
            let _ = write_helper_reply(&mut local_stream, &rejection);
        }
        let _ = local_stream.shutdown(Shutdown::Both);
    }
}

fn connect_adapter(address: SocketAddr) -> Result<TcpStream> {
    let stream = TcpStream::connect_timeout(&address, PRODUCT_CONNECT_TIMEOUT)
        .map_err(|_| RuntimeError::ChannelIo)?;
    configure_stream(&stream, PRODUCT_IO_TIMEOUT)?;
    Ok(stream)
}

fn configure_stream(stream: &TcpStream, timeout: Duration) -> Result<()> {
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|()| stream.set_write_timeout(Some(timeout)))
        .and_then(|()| stream.set_nodelay(true))
        .map_err(|_| RuntimeError::ChannelIo)
}

fn configure_unix_stream(stream: &UnixStream) -> Result<()> {
    stream
        .set_read_timeout(Some(PRODUCT_IO_TIMEOUT))
        .and_then(|()| stream.set_write_timeout(Some(PRODUCT_IO_TIMEOUT)))
        .map_err(|_| RuntimeError::IpcIo)
}

fn relay_address(peer_address: SocketAddr, relay_port: u16) -> Result<SocketAddr> {
    let invalid_broadcast =
        matches!(peer_address.ip(), IpAddr::V4(address) if address.is_broadcast());
    if peer_address.ip().is_unspecified()
        || peer_address.ip().is_multicast()
        || invalid_broadcast
        || relay_port == 0
    {
        return Err(RuntimeError::PairingRejected);
    }
    Ok(SocketAddr::new(peer_address.ip(), relay_port))
}

fn write_proof(endpoint: &LocalEndpoint, bindings: PairingBindings, peer_id: &str) -> Result<()> {
    let encoded = encode_proof(endpoint, bindings, peer_id)?;
    let stdout = std::io::stdout();
    let mut output = stdout.lock();
    nlu_server::write_frame(&mut output, &encoded).map_err(|_| RuntimeError::HelperStartup)?;
    output.flush().map_err(|_| RuntimeError::HelperStartup)
}

fn encode_proof(
    endpoint: &LocalEndpoint,
    bindings: PairingBindings,
    peer_id: &str,
) -> Result<Vec<u8>> {
    let endpoint = endpoint
        .socket_path
        .to_str()
        .ok_or(RuntimeError::HelperStartup)?;
    let encoded = nlu_data::canonical_json(
        &json!({
            "endpoint": format!("unix:{endpoint}"),
            "epoch": bindings.epoch_hex(),
            "kind": "pairing_ready",
            "pairing_id": bindings.pairing_id_hex(),
            "peer_id": peer_id,
            "version": 1,
        }),
        "helper proof",
    )
    .map_err(|_| RuntimeError::HelperStartup)?;
    if encoded.is_empty() || encoded.len() > MAX_PAIRING_WIRE_BYTES {
        return Err(RuntimeError::HelperStartup);
    }
    Ok(encoded)
}

struct LocalEndpoint {
    listener: UnixListener,
    directory: PathBuf,
    socket_path: PathBuf,
}

impl LocalEndpoint {
    fn bind(root: &Path, bindings: PairingBindings, identity: PeerIdentity) -> Result<Self> {
        if !root.is_absolute() || root.file_name().is_none() {
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        validate_local_root(root)?;

        let directory = root.join(format!("ptbr-nlu-companion-{}", bindings.pairing_id_hex()));
        fs::DirBuilder::new()
            .mode(DIRECTORY_MODE)
            .create(&directory)
            .map_err(|_| RuntimeError::RuntimeDirectory)?;
        if validate_directory(&directory, identity).is_err() {
            let _ = fs::remove_dir(&directory);
            return Err(RuntimeError::RuntimeDirectorySecurity);
        }

        let socket_path = directory.join("helper.sock");
        if socket_path.as_os_str().as_encoded_bytes().len() > nlu_server::MAX_SOCKET_PATH_BYTES {
            let _ = fs::remove_dir(&directory);
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        let listener = match UnixListener::bind(&socket_path) {
            Ok(listener) => listener,
            Err(_) => {
                let _ = fs::remove_dir(&directory);
                return Err(RuntimeError::IpcIo);
            }
        };
        let configured = fs::set_permissions(&socket_path, fs::Permissions::from_mode(SOCKET_MODE))
            .map_err(|_| RuntimeError::RuntimeDirectorySecurity)
            .and_then(|()| validate_socket(&socket_path, identity));
        if configured.is_err() {
            drop(listener);
            let _ = fs::remove_file(&socket_path);
            let _ = fs::remove_dir(&directory);
            return Err(RuntimeError::RuntimeDirectorySecurity);
        }
        Ok(Self {
            listener,
            directory,
            socket_path,
        })
    }
}

impl core::fmt::Debug for LocalEndpoint {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("LocalEndpoint(path=redacted)")
    }
}

impl Drop for LocalEndpoint {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.socket_path);
        let _ = fs::remove_dir(&self.directory);
    }
}

fn validate_directory(path: &Path, identity: PeerIdentity) -> Result<()> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.permissions().mode() & 0o7777 != DIRECTORY_MODE
        || metadata.uid() != identity.uid()
    {
        return Err(RuntimeError::RuntimeDirectorySecurity);
    }
    Ok(())
}

fn validate_local_root(root: &Path) -> Result<()> {
    let metadata =
        fs::symlink_metadata(root).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    if root == Path::new("/tmp")
        && metadata.file_type().is_symlink()
        && fs::canonicalize(root).ok().as_deref() == Some(Path::new("/private/tmp"))
    {
        return Ok(());
    }
    Err(RuntimeError::RuntimeDirectorySecurity)
}

fn validate_socket(path: &Path, identity: PeerIdentity) -> Result<()> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| RuntimeError::RuntimeDirectorySecurity)?;
    if !metadata.file_type().is_socket()
        || metadata.permissions().mode() & 0o7777 != SOCKET_MODE
        || metadata.uid() != identity.uid()
    {
        return Err(RuntimeError::RuntimeDirectorySecurity);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicU64, Ordering};

    use serde_json::Value;

    use super::*;

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(1);
    const FIXTURE_TECNICA_CREDENTIAL: [u8; 32] = *b"FIXTURE_TECNICA_0123456789ABCDEF";

    fn fixture_bindings() -> PairingBindings {
        let serial = NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed);
        let mut credential = FIXTURE_TECNICA_CREDENTIAL;
        credential[..4].copy_from_slice(&std::process::id().to_be_bytes());
        credential[4..12].copy_from_slice(&serial.to_be_bytes());
        PairingBindings::derive(&credential).expect("FIXTURE_TECNICA bindings")
    }

    #[test]
    fn local_endpoint_has_exact_private_metadata_and_cleans_up() {
        let identity = PeerIdentity::current().expect("FIXTURE_TECNICA identity");
        let endpoint = LocalEndpoint::bind(Path::new("/tmp"), fixture_bindings(), identity)
            .expect("FIXTURE_TECNICA endpoint");
        let directory = endpoint.directory.clone();
        let socket = endpoint.socket_path.clone();

        validate_directory(&directory, identity).expect("FIXTURE_TECNICA directory");
        validate_socket(&socket, identity).expect("FIXTURE_TECNICA socket");
        assert!(!format!("{endpoint:?}").contains(socket.to_string_lossy().as_ref()));
        drop(endpoint);
        assert!(!directory.exists());
        assert!(!socket.exists());
    }

    #[test]
    fn helper_proof_is_bounded_closed_and_redacted_by_type() {
        let identity = PeerIdentity::current().expect("FIXTURE_TECNICA identity");
        let bindings = fixture_bindings();
        let endpoint = LocalEndpoint::bind(Path::new("/tmp"), bindings, identity)
            .expect("FIXTURE_TECNICA endpoint");
        let proof = encode_proof(&endpoint, bindings, "FIXTURE_TECNICA_ADDON")
            .expect("FIXTURE_TECNICA proof");
        let value: Value = serde_json::from_slice(&proof).expect("FIXTURE_TECNICA proof JSON");

        assert_eq!(value["kind"], "pairing_ready");
        assert_eq!(value["pairing_id"], bindings.pairing_id_hex());
        assert_eq!(value["epoch"], bindings.epoch_hex());
        assert_eq!(value["peer_id"], "FIXTURE_TECNICA_ADDON");
        assert!(
            value["endpoint"]
                .as_str()
                .is_some_and(|value| value.starts_with("unix:/"))
        );
        assert!(proof.len() <= MAX_PAIRING_WIRE_BYTES);
    }

    #[test]
    fn relay_address_preserves_authenticated_peer_ip_only() {
        let source: SocketAddr = "192.0.2.10:54321".parse().expect("FIXTURE_TECNICA source");
        assert_eq!(
            relay_address(source, 10_702).expect("FIXTURE_TECNICA relay"),
            "192.0.2.10:10702"
                .parse()
                .expect("FIXTURE_TECNICA expected")
        );
        assert_eq!(
            relay_address(
                "0.0.0.0:1".parse().expect("FIXTURE_TECNICA unspecified"),
                10_702,
            )
            .expect_err("FIXTURE_TECNICA unspecified rejected"),
            RuntimeError::PairingRejected
        );
    }
}
