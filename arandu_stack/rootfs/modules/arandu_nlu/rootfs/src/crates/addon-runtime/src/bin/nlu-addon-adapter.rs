#![forbid(unsafe_code)]

use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, ExitCode};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use addon_runtime::{
    CatalogBindings, CatalogSeed, DEFAULT_ADAPTER_RELAY_PORT, DEFAULT_HELPER_PAIRING_PORT,
    GenerationDirectory, PairingBindings, RecognitionProvider, Result, RuntimeError,
    RuntimeIdentities, SupervisorClient, WyomingTcpConfig, configure_server_command,
    drive_wyoming_tcp, initiate_pairing, interpret_submission, prepare_product_generation_root,
    recognize_wyoming, serve_companion_once, write_catalog_seed,
};
use runtime_security::{
    Credential, DroppedPrivileges, GuardedProcess, SecretEnvironmentValue, take_supervisor_token,
};
use wyoming_runtime::{
    MonotonicMillis, RecognitionCompletion, RecognitionRequest, RevalidatedRecognition,
    RevalidatedStateQuery,
};

const RUNTIME_CONTRACT_PATH: &str = "/etc/ptbr-nlu/runtime-contract.json";
const OPTIONS_PATH: &str = "/data/options.json";
const SERVER_EXECUTABLE: &str = "/opt/ptbr-nlu/bin/nlu-server";
const SUPERVISOR_HOST: &str = "supervisor";
const SUPERVISOR_PORT: u16 = 80;
const COMPANION_HOST: &str = "homeassistant";
const INGRESS_PORT: u16 = 8_099;
const WYOMING_PORT: u16 = 10_700;
const ADDON_PEER_ID: &str = "ptbr_nlu";
const MAX_OPTIONS_BYTES: usize = 4_096;
const MAX_HTTP_HEAD_BYTES: usize = 8_192;
const MAX_HTTP_BODY_BYTES: usize = 128;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(10);
const NETWORK_TIMEOUT: Duration = Duration::from_secs(10);
const CONTROL_POLL_INTERVAL: Duration = Duration::from_millis(100);
const PAIRING_CREDENTIAL_TEXT_BYTES: usize = 43;
const EXPECTED_RUNTIME_CONTRACT: &[u8] = include_bytes!("../../../../addon/runtime-contract.json");

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => ExitCode::FAILURE,
    }
}

fn run() -> Result<()> {
    validate_arguments(std::env::args_os().skip(1))?;
    validate_runtime_contract(Path::new(RUNTIME_CONTRACT_PATH))?;
    let options = RuntimeOptions::read(Path::new(OPTIONS_PATH))?;
    let guard = GuardedProcess::harden().map_err(|_| RuntimeError::ServerStartup)?;
    let mut supervisor_token = take_supervisor_token().map_err(|_| RuntimeError::ServerStartup)?;
    let seed = synchronize_catalog(&supervisor_token)?;
    supervisor_token.destroy();

    let identities = RuntimeIdentities::product()?;
    let generation_root = prepare_product_generation_root(identities)?;
    let generation = GenerationDirectory::create(&generation_root, identities)?;
    let mut server = spawn_server(&generation, identities, &seed)?;

    let catalog = Arc::new(CatalogBindings::from_seed(&seed)?);
    let ingress = options
        .companion_execution
        .then(|| bind_listener(INGRESS_PORT))
        .transpose()?;
    let relay = options
        .companion_execution
        .then(|| bind_listener(DEFAULT_ADAPTER_RELAY_PORT))
        .transpose()?;
    let wyoming = options
        .wyoming
        .then(|| bind_listener(WYOMING_PORT))
        .transpose()?;

    let privileges = DroppedPrivileges::establish(identities.adapter())
        .map_err(|_| RuntimeError::ServerStartup)?;
    privileges
        .verify()
        .map_err(|_| RuntimeError::ServerStartup)?;

    if let Some(listener) = wyoming {
        spawn_wyoming(
            listener,
            generation.socket_path().to_owned(),
            Arc::clone(&catalog),
        );
    }

    let result = match (ingress, relay) {
        (Some(ingress), Some(relay)) => serve_companion_runtime(
            &guard,
            ingress,
            relay,
            &mut server,
            generation.socket_path(),
            catalog.as_ref(),
        ),
        (None, None) => wait_for_server(&mut server),
        _ => Err(RuntimeError::InvalidProcessConfiguration),
    };
    if result.is_err() {
        terminate_child(&mut server);
    }
    result
}

fn validate_arguments(arguments: impl IntoIterator<Item = std::ffi::OsString>) -> Result<()> {
    let values = arguments.into_iter().collect::<Vec<_>>();
    if values.len() != 2 || values[0] != "--runtime-contract" || values[1] != RUNTIME_CONTRACT_PATH
    {
        return Err(RuntimeError::InvalidProcessConfiguration);
    }
    Ok(())
}

fn validate_runtime_contract(path: &Path) -> Result<()> {
    let bytes = fs::read(path).map_err(|_| RuntimeError::InvalidProcessConfiguration)?;
    if bytes == EXPECTED_RUNTIME_CONTRACT {
        Ok(())
    } else {
        Err(RuntimeError::InvalidProcessConfiguration)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RuntimeOptions {
    companion_execution: bool,
    wyoming: bool,
}

impl RuntimeOptions {
    fn read(path: &Path) -> Result<Self> {
        let bytes = fs::read(path).map_err(|_| RuntimeError::InvalidProcessConfiguration)?;
        if bytes.is_empty() || bytes.len() > MAX_OPTIONS_BYTES {
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        Self::decode(&bytes)
    }

    fn decode(bytes: &[u8]) -> Result<Self> {
        let value = nlu_data::parse_strict_json(bytes, "add-on options")
            .map_err(|_| RuntimeError::InvalidProcessConfiguration)?;
        let object = value
            .as_object()
            .ok_or(RuntimeError::InvalidProcessConfiguration)?;
        if object.len() != 2
            || !object.contains_key("companion_execution_enabled")
            || !object.contains_key("wyoming_enabled")
        {
            return Err(RuntimeError::InvalidProcessConfiguration);
        }
        Ok(Self {
            companion_execution: object
                .get("companion_execution_enabled")
                .and_then(serde_json::Value::as_bool)
                .ok_or(RuntimeError::InvalidProcessConfiguration)?,
            wyoming: object
                .get("wyoming_enabled")
                .and_then(serde_json::Value::as_bool)
                .ok_or(RuntimeError::InvalidProcessConfiguration)?,
        })
    }
}

fn synchronize_catalog(token: &SecretEnvironmentValue) -> Result<CatalogSeed> {
    let address = resolve_one(SUPERVISOR_HOST, SUPERVISOR_PORT)?;
    let stream = TcpStream::connect_timeout(&address, STARTUP_TIMEOUT)
        .map_err(|_| RuntimeError::ServerStartup)?;
    configure_tcp(&stream, NETWORK_TIMEOUT)?;
    let mut entropy = File::open("/dev/urandom").map_err(|_| RuntimeError::ServerStartup)?;
    let mut nonce = [0_u8; 16];
    entropy
        .read_exact(&mut nonce)
        .map_err(|_| RuntimeError::ServerStartup)?;
    let secret = token.expose_secret().ok_or(RuntimeError::ServerStartup)?;
    let mut client = SupervisorClient::connect(stream, entropy, secret, nonce)
        .map_err(|_| RuntimeError::ServerStartup)?;
    let snapshot = client
        .synchronize_read_only()
        .map_err(|_| RuntimeError::ServerStartup)?;
    client.close().map_err(|_| RuntimeError::ServerStartup)?;
    CatalogSeed::from_supervisor_snapshot(&snapshot)
}

fn spawn_server(
    generation: &GenerationDirectory,
    identities: RuntimeIdentities,
    seed: &CatalogSeed,
) -> Result<Child> {
    let mut command =
        configure_server_command(Path::new(SERVER_EXECUTABLE), generation, identities)?;
    let mut child = command.spawn().map_err(|_| RuntimeError::ServerSpawn)?;
    let result = child
        .stdin
        .take()
        .ok_or(RuntimeError::ServerSpawn)
        .and_then(|mut input| {
            write_catalog_seed(&mut input, seed)?;
            drop(input);
            wait_for_server_socket(&mut child, generation.socket_path(), identities)
        });
    if result.is_err() {
        terminate_child(&mut child);
        return Err(RuntimeError::ServerStartup);
    }
    Ok(child)
}

fn wait_for_server_socket(
    child: &mut Child,
    socket_path: &Path,
    identities: RuntimeIdentities,
) -> Result<()> {
    let deadline = Instant::now()
        .checked_add(STARTUP_TIMEOUT)
        .ok_or(RuntimeError::ServerStartup)?;
    loop {
        if child
            .try_wait()
            .map_err(|_| RuntimeError::ServerStartup)?
            .is_some()
        {
            return Err(RuntimeError::ServerStartup);
        }
        if let Ok(metadata) = fs::symlink_metadata(socket_path) {
            if metadata.file_type().is_socket()
                && metadata.permissions().mode() & 0o7777 == 0o660
                && metadata.uid() == identities.server().uid()
                && metadata.gid() == identities.ipc_gid()
            {
                return Ok(());
            }
            return Err(RuntimeError::ServerStartup);
        }
        if Instant::now() >= deadline {
            return Err(RuntimeError::ServerStartup);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn terminate_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn wait_for_server(server: &mut Child) -> Result<()> {
    match server.wait() {
        Ok(status) if status.success() => Ok(()),
        _ => Err(RuntimeError::ServerStartup),
    }
}

fn ensure_server_running(server: &mut Child) -> Result<()> {
    match server.try_wait() {
        Ok(None) => Ok(()),
        Ok(Some(_)) | Err(_) => Err(RuntimeError::ServerStartup),
    }
}

fn bind_listener(port: u16) -> Result<TcpListener> {
    let listener = TcpListener::bind(SocketAddr::new(IpAddr::V4(Ipv4Addr::UNSPECIFIED), port))
        .map_err(|_| RuntimeError::ServerStartup)?;
    listener
        .set_nonblocking(false)
        .map_err(|_| RuntimeError::ServerStartup)?;
    Ok(listener)
}

trait TimeoutTransport: Read + Write {
    fn set_operation_read_timeout(&self, timeout: Duration) -> io::Result<()>;
    fn set_operation_write_timeout(&self, timeout: Duration) -> io::Result<()>;
}

impl TimeoutTransport for TcpStream {
    fn set_operation_read_timeout(&self, timeout: Duration) -> io::Result<()> {
        self.set_read_timeout(Some(timeout))
    }

    fn set_operation_write_timeout(&self, timeout: Duration) -> io::Result<()> {
        self.set_write_timeout(Some(timeout))
    }
}

trait LivenessProbe {
    fn verify_live(&mut self) -> io::Result<()>;
}

impl LivenessProbe for Child {
    fn verify_live(&mut self) -> io::Result<()> {
        match self.try_wait() {
            Ok(None) => Ok(()),
            Ok(Some(_)) => Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "nlu-server exited",
            )),
            Err(error) => Err(error),
        }
    }
}

struct DeadlineStream<'a, Transport, Probe> {
    transport: Transport,
    deadline: Instant,
    probe: &'a mut Probe,
}

impl<'a, Transport, Probe> DeadlineStream<'a, Transport, Probe>
where
    Transport: TimeoutTransport,
    Probe: LivenessProbe,
{
    fn new(transport: Transport, timeout: Duration, probe: &'a mut Probe) -> io::Result<Self> {
        let deadline = Instant::now()
            .checked_add(timeout)
            .ok_or_else(deadline_error)?;
        Ok(Self {
            transport,
            deadline,
            probe,
        })
    }

    fn until(transport: Transport, deadline: Instant, probe: &'a mut Probe) -> Self {
        Self {
            transport,
            deadline,
            probe,
        }
    }

    fn into_parts(self) -> (Transport, Instant) {
        (self.transport, self.deadline)
    }

    fn prepare_io(&mut self) -> io::Result<Duration> {
        self.probe.verify_live()?;
        remaining(self.deadline).map(|value| value.min(CONTROL_POLL_INTERVAL))
    }

    fn finish_io(&mut self) -> io::Result<()> {
        self.probe.verify_live()?;
        remaining(self.deadline).map(|_| ())
    }
}

impl<Transport, Probe> Read for DeadlineStream<'_, Transport, Probe>
where
    Transport: TimeoutTransport,
    Probe: LivenessProbe,
{
    fn read(&mut self, destination: &mut [u8]) -> io::Result<usize> {
        loop {
            let timeout = self.prepare_io()?;
            self.transport.set_operation_read_timeout(timeout)?;
            match self.transport.read(destination) {
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) if is_timeout(&error) && Instant::now() < self.deadline => {}
                result => {
                    self.finish_io()?;
                    return result;
                }
            }
        }
    }
}

impl<Transport, Probe> Write for DeadlineStream<'_, Transport, Probe>
where
    Transport: TimeoutTransport,
    Probe: LivenessProbe,
{
    fn write(&mut self, source: &[u8]) -> io::Result<usize> {
        loop {
            let timeout = self.prepare_io()?;
            self.transport.set_operation_write_timeout(timeout)?;
            match self.transport.write(source) {
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) if is_timeout(&error) && Instant::now() < self.deadline => {}
                result => {
                    self.finish_io()?;
                    return result;
                }
            }
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        loop {
            let timeout = self.prepare_io()?;
            self.transport.set_operation_write_timeout(timeout)?;
            match self.transport.flush() {
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) if is_timeout(&error) && Instant::now() < self.deadline => {}
                result => {
                    self.finish_io()?;
                    return result;
                }
            }
        }
    }
}

impl<Probe> DeadlineStream<'_, TcpStream, Probe> {
    fn shutdown(&self) -> io::Result<()> {
        self.transport.shutdown(Shutdown::Both)
    }
}

fn remaining(deadline: Instant) -> io::Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|value| !value.is_zero())
        .ok_or_else(deadline_error)
}

fn deadline_error() -> io::Error {
    io::Error::new(io::ErrorKind::TimedOut, "operation deadline expired")
}

fn is_timeout(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    )
}

enum PairingUpdate<T> {
    Unchanged,
    Activate(T),
}

fn apply_pairing_update<T>(active: &mut Option<T>, update: PairingUpdate<T>) -> Option<T> {
    match update {
        PairingUpdate::Unchanged => None,
        PairingUpdate::Activate(replacement) => active.replace(replacement),
    }
}

fn serve_companion_runtime(
    guard: &GuardedProcess,
    ingress: TcpListener,
    relay: TcpListener,
    server: &mut Child,
    server_socket: &Path,
    catalog: &CatalogBindings,
) -> Result<()> {
    ingress
        .set_nonblocking(true)
        .and_then(|()| relay.set_nonblocking(true))
        .map_err(|_| RuntimeError::ServerStartup)?;
    let mut active_credential: Option<Credential> = None;

    loop {
        ensure_server_running(server)?;
        let mut handled_connection = false;

        match ingress.accept() {
            Ok((stream, _)) => {
                handled_connection = true;
                let update = handle_ingress_connection(guard, stream, server)?;
                if let PairingUpdate::Activate(credential) = &update {
                    let secret = credential
                        .expose_secret()
                        .ok_or(RuntimeError::PairingRejected)?;
                    let epoch = PairingBindings::derive(secret)?.epoch();
                    catalog.activate_operation_epoch(*epoch.as_bytes())?;
                }
                if let Some(mut previous) = apply_pairing_update(&mut active_credential, update) {
                    previous.destroy();
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}
            Err(_) => return Err(RuntimeError::PairingProtocol),
        }
        ensure_server_running(server)?;

        match relay.accept() {
            Ok((stream, _)) => {
                handled_connection = true;
                if let Some(credential) = active_credential.as_ref() {
                    let _ =
                        serve_relay_connection(stream, server, server_socket, catalog, credential);
                } else {
                    let _ = stream.shutdown(Shutdown::Both);
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}
            Err(_) => return Err(RuntimeError::ChannelIo),
        }
        ensure_server_running(server)?;

        if !handled_connection {
            thread::sleep(CONTROL_POLL_INTERVAL);
        }
    }
}

fn handle_ingress_connection(
    guard: &GuardedProcess,
    stream: TcpStream,
    server: &mut Child,
) -> Result<PairingUpdate<Credential>> {
    if stream.set_nodelay(true).is_err() {
        let _ = stream.shutdown(Shutdown::Both);
        return Ok(PairingUpdate::Unchanged);
    }
    let mut stream = DeadlineStream::new(stream, NETWORK_TIMEOUT, server)
        .map_err(|_| RuntimeError::PairingProtocol)?;
    match read_ingress_request(&mut stream) {
        Ok(IngressRequest::Form) => {
            let _ = write_pairing_form(&mut stream);
            let _ = stream.shutdown();
            Ok(PairingUpdate::Unchanged)
        }
        Ok(IngressRequest::Credential(mut bytes)) => {
            let credential = guard
                .admit_credential(bytes)
                .map_err(|_| RuntimeError::PairingRejected);
            bytes.fill(0);
            let credential = credential?;
            let (response_stream, deadline) = stream.into_parts();
            let paired = initiate_addon_pairing(&credential, deadline, server).is_ok();
            ensure_server_running(server)?;
            let mut response = DeadlineStream::until(response_stream, deadline, server);
            if paired {
                let _ = write_http_status(&mut response, 204, "No Content");
                let _ = response.shutdown();
                Ok(PairingUpdate::Activate(credential))
            } else {
                let _ = write_http_status(&mut response, 400, "Bad Request");
                let _ = response.shutdown();
                Ok(PairingUpdate::Unchanged)
            }
        }
        Err(()) => {
            let _ = write_http_status(&mut stream, 400, "Bad Request");
            let _ = stream.shutdown();
            Ok(PairingUpdate::Unchanged)
        }
    }
}

fn initiate_addon_pairing(
    credential: &Credential,
    deadline: Instant,
    server: &mut Child,
) -> Result<()> {
    let secret = credential
        .expose_secret()
        .ok_or(RuntimeError::PairingRejected)?;
    let bindings = PairingBindings::derive(secret)?;
    let address = resolve_one(COMPANION_HOST, DEFAULT_HELPER_PAIRING_PORT)?;
    ensure_server_running(server)?;
    let connect_timeout = remaining(deadline)
        .map_err(|_| RuntimeError::PairingRejected)?
        .min(STARTUP_TIMEOUT);
    let stream = TcpStream::connect_timeout(&address, connect_timeout)
        .map_err(|_| RuntimeError::PairingRejected)?;
    stream
        .set_nodelay(true)
        .map_err(|_| RuntimeError::PairingRejected)?;
    let stream = DeadlineStream::until(stream, deadline, server);
    initiate_pairing(
        stream,
        bindings,
        secret,
        ADDON_PEER_ID,
        DEFAULT_ADAPTER_RELAY_PORT,
    )
}

enum IngressRequest {
    Form,
    Credential([u8; 32]),
}

fn read_ingress_request(stream: &mut impl Read) -> core::result::Result<IngressRequest, ()> {
    let mut request = Vec::with_capacity(512);
    let head_end = loop {
        if request.len() >= MAX_HTTP_HEAD_BYTES {
            request.fill(0);
            return Err(());
        }
        let mut byte = [0_u8; 1];
        stream.read_exact(&mut byte).map_err(|_| ())?;
        request.push(byte[0]);
        if request.ends_with(b"\r\n\r\n") {
            break request.len();
        }
    };
    let head = std::str::from_utf8(&request[..head_end]).map_err(|_| ())?;
    let mut lines = head.strip_suffix("\r\n\r\n").ok_or(())?.split("\r\n");
    let request_line = lines.next().ok_or(())?;
    if request_line == "GET / HTTP/1.1" {
        request.fill(0);
        return Ok(IngressRequest::Form);
    }
    if request_line != "POST /pair HTTP/1.1" {
        request.fill(0);
        return Err(());
    }
    let mut content_length = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(())?;
        if name.eq_ignore_ascii_case("transfer-encoding") {
            request.fill(0);
            return Err(());
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                request.fill(0);
                return Err(());
            }
            content_length = value.trim().parse::<usize>().ok();
        }
    }
    let length = content_length.ok_or(())?;
    if length == 0 || length > MAX_HTTP_BODY_BYTES {
        request.fill(0);
        return Err(());
    }
    request.resize(head_end + length, 0);
    stream
        .read_exact(&mut request[head_end..])
        .map_err(|_| ())?;
    let decoded = decode_pairing_body(&request[head_end..]);
    request.fill(0);
    decoded.map(IngressRequest::Credential)
}

fn decode_pairing_body(body: &[u8]) -> core::result::Result<[u8; 32], ()> {
    let credential = body.strip_prefix(b"credential=").unwrap_or(body);
    if credential.len() != PAIRING_CREDENTIAL_TEXT_BYTES {
        return Err(());
    }
    let mut output = [0_u8; 32];
    let mut output_index = 0_usize;
    let mut accumulator = 0_u32;
    let mut bits = 0_u8;
    for byte in credential {
        let value = base64url_value(*byte).ok_or(())?;
        accumulator = (accumulator << 6) | u32::from(value);
        bits += 6;
        while bits >= 8 {
            bits -= 8;
            if output_index >= output.len() {
                return Err(());
            }
            output[output_index] = u8::try_from((accumulator >> bits) & 0xff).map_err(|_| ())?;
            output_index += 1;
        }
    }
    let trailing_mask = (1_u32 << bits) - 1;
    if output_index != output.len() || accumulator & trailing_mask != 0 {
        output.fill(0);
        return Err(());
    }
    Ok(output)
}

const fn base64url_value(byte: u8) -> Option<u8> {
    match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'-' => Some(62),
        b'_' => Some(63),
        _ => None,
    }
}

fn write_pairing_form(stream: &mut impl Write) -> io::Result<()> {
    const BODY: &str = "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">\
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\
<title>Local NLU</title><body><main><h1>Local NLU</h1>\
<form method=\"post\" action=\"pair\"><label>Pairing credential\
<input name=\"credential\" type=\"password\" required maxlength=\"43\" minlength=\"43\" \
autocomplete=\"off\"></label><button type=\"submit\">Pair</button></form></main></body></html>";
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\
Content-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\
X-Content-Type-Options: nosniff\r\n\r\n{BODY}",
        BODY.len()
    )
}

fn write_http_status(stream: &mut impl Write, status: u16, reason: &str) -> io::Result<()> {
    write!(
        stream,
        "HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\
Cache-Control: no-store\r\nConnection: close\r\n\r\n"
    )
}

fn serve_relay_connection(
    stream: TcpStream,
    server: &mut Child,
    server_socket: &Path,
    catalog: &CatalogBindings,
    credential: &Credential,
) -> Result<()> {
    let secret = credential
        .expose_secret()
        .ok_or(RuntimeError::PairingRejected)?;
    let epoch = PairingBindings::derive(secret)?.epoch();
    stream
        .set_nodelay(true)
        .map_err(|_| RuntimeError::ChannelIo)?;
    let stream = DeadlineStream::new(stream, NETWORK_TIMEOUT, server)
        .map_err(|_| RuntimeError::ChannelIo)?;
    serve_companion_once(stream, epoch, secret, |binding, submission| {
        interpret_submission(server_socket, catalog, binding, submission)
    })
}

fn spawn_wyoming(listener: TcpListener, server_socket: PathBuf, catalog: Arc<CatalogBindings>) {
    thread::spawn(move || {
        while let Ok((stream, _)) = listener.accept() {
            let mut provider = ProductRecognitionProvider {
                server_socket: &server_socket,
                catalog: catalog.as_ref(),
            };
            let started = Instant::now();
            let mut clock = || {
                let millis = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
                MonotonicMillis::new(millis)
            };
            let _ = drive_wyoming_tcp(
                stream,
                &mut clock,
                &mut provider,
                WyomingTcpConfig::default(),
            );
        }
    });
}

struct ProductRecognitionProvider<'a> {
    server_socket: &'a Path,
    catalog: &'a CatalogBindings,
}

impl RecognitionProvider for ProductRecognitionProvider<'_> {
    type Error = RuntimeError;

    fn recognize(
        &mut self,
        request: &RecognitionRequest,
    ) -> core::result::Result<RecognitionCompletion, Self::Error> {
        let external_ids =
            match recognize_wyoming(self.server_socket, self.catalog, request.input().text()) {
                Ok(value) => value,
                Err(_) => return Ok(RecognitionCompletion::abstain(request.id())),
            };
        let queries = external_ids
            .into_iter()
            .map(|external_id| {
                RevalidatedStateQuery::from_revalidated_exact_name(external_id)
                    .map_err(|_| RuntimeError::InterpretationRejected)
            })
            .collect::<Result<Vec<_>>>()?;
        let recognition = RevalidatedRecognition::independent(request.id(), queries)
            .map_err(|_| RuntimeError::InterpretationRejected)?;
        Ok(RecognitionCompletion::recognized(recognition))
    }
}

fn configure_tcp(stream: &TcpStream, timeout: Duration) -> Result<()> {
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|()| stream.set_write_timeout(Some(timeout)))
        .and_then(|()| stream.set_nodelay(true))
        .map_err(|_| RuntimeError::ChannelIo)
}

fn resolve_one(host: &str, port: u16) -> Result<SocketAddr> {
    (host, port)
        .to_socket_addrs()
        .map_err(|_| RuntimeError::ServerStartup)?
        .find(|address| {
            !address.ip().is_unspecified()
                && !address.ip().is_multicast()
                && !matches!(address.ip(), IpAddr::V4(ip) if ip.is_broadcast())
        })
        .ok_or(RuntimeError::ServerStartup)
}

#[cfg(test)]
mod tests {
    use std::process::{Command, Stdio};

    use super::*;

    const FIXTURE_TECNICA_CREDENTIAL: [u8; 32] = *b"FIXTURE_TECNICA_0123456789ABCDEF";
    const FIXTURE_TECNICA_ENCODED: &[u8] = b"RklYVFVSRV9URUNOSUNBXzAxMjM0NTY3ODlBQkNERUY";

    struct FixtureDripTransport {
        bytes: Vec<u8>,
        offset: usize,
        reads: usize,
        delay: Duration,
    }

    impl Read for FixtureDripTransport {
        fn read(&mut self, destination: &mut [u8]) -> io::Result<usize> {
            if destination.is_empty() || self.offset == self.bytes.len() {
                return Ok(0);
            }
            thread::sleep(self.delay);
            destination[0] = self.bytes[self.offset];
            self.offset += 1;
            self.reads += 1;
            Ok(1)
        }
    }

    impl Write for FixtureDripTransport {
        fn write(&mut self, source: &[u8]) -> io::Result<usize> {
            Ok(source.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl TimeoutTransport for FixtureDripTransport {
        fn set_operation_read_timeout(&self, _timeout: Duration) -> io::Result<()> {
            Ok(())
        }

        fn set_operation_write_timeout(&self, _timeout: Duration) -> io::Result<()> {
            Ok(())
        }
    }

    struct FixtureProbe {
        checks: usize,
        fail_at: Option<usize>,
    }

    impl LivenessProbe for FixtureProbe {
        fn verify_live(&mut self) -> io::Result<()> {
            self.checks += 1;
            if self.fail_at == Some(self.checks) {
                Err(io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "FIXTURE_TECNICA server exit",
                ))
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn options_are_closed_and_boolean_only() {
        assert_eq!(
            RuntimeOptions::decode(
                br#"{"companion_execution_enabled":true,"wyoming_enabled":false}"#
            )
            .expect("FIXTURE_TECNICA options"),
            RuntimeOptions {
                companion_execution: true,
                wyoming: false,
            }
        );
        for bytes in [
            br#"{"companion_execution_enabled":true}"#.as_slice(),
            br#"{"companion_execution_enabled":true,"private":"FIXTURE_TECNICA","wyoming_enabled":false}"#.as_slice(),
            br#"{"companion_execution_enabled":1,"wyoming_enabled":false}"#.as_slice(),
        ] {
            assert_eq!(
                RuntimeOptions::decode(bytes).expect_err("FIXTURE_TECNICA closed options"),
                RuntimeError::InvalidProcessConfiguration
            );
        }
    }

    #[test]
    fn ingress_accepts_only_exact_unpadded_base64url_credential() {
        assert_eq!(
            decode_pairing_body(FIXTURE_TECNICA_ENCODED).expect("FIXTURE_TECNICA raw credential"),
            FIXTURE_TECNICA_CREDENTIAL
        );
        let mut form = b"credential=".to_vec();
        form.extend_from_slice(FIXTURE_TECNICA_ENCODED);
        assert_eq!(
            decode_pairing_body(&form).expect("FIXTURE_TECNICA form credential"),
            FIXTURE_TECNICA_CREDENTIAL
        );
        for rejected in [
            &FIXTURE_TECNICA_ENCODED[..42],
            b"RklYVFVSRV9URUNOSUNBXzAxMjM0NTY3ODlBQkNERU=",
            b"RklYVFVSRV9URUNOSUNBXzAxMjM0NTY3ODlBQkNERU!",
        ] {
            assert!(decode_pairing_body(rejected).is_err());
        }
    }

    #[test]
    fn product_arguments_are_exact_and_credential_free() {
        assert!(
            validate_arguments([
                std::ffi::OsString::from("--runtime-contract"),
                std::ffi::OsString::from(RUNTIME_CONTRACT_PATH),
            ])
            .is_ok()
        );
        assert!(validate_arguments([std::ffi::OsString::from("FIXTURE_TECNICA_SECRET")]).is_err());
    }

    #[test]
    fn pairing_update_preserves_old_value_until_authenticated_replacement() {
        let mut active = Some("FIXTURE_TECNICA_OLD");

        assert_eq!(
            apply_pairing_update(&mut active, PairingUpdate::Unchanged),
            None
        );
        assert_eq!(active, Some("FIXTURE_TECNICA_OLD"));
        assert_eq!(
            apply_pairing_update(&mut active, PairingUpdate::Activate("FIXTURE_TECNICA_NEW"),),
            Some("FIXTURE_TECNICA_OLD")
        );
        assert_eq!(active, Some("FIXTURE_TECNICA_NEW"));
        assert_eq!(
            apply_pairing_update(
                &mut active,
                PairingUpdate::Activate("FIXTURE_TECNICA_NEWER"),
            ),
            Some("FIXTURE_TECNICA_NEW")
        );
        assert_eq!(active, Some("FIXTURE_TECNICA_NEWER"));
    }

    #[test]
    fn absolute_deadline_rejects_continuous_one_byte_drip() {
        let transport = FixtureDripTransport {
            bytes: b"FIXTURE_TECNICA_DRIP".to_vec(),
            offset: 0,
            reads: 0,
            delay: Duration::from_millis(10),
        };
        let mut probe = FixtureProbe {
            checks: 0,
            fail_at: None,
        };
        let mut stream = DeadlineStream::new(transport, Duration::from_millis(25), &mut probe)
            .expect("FIXTURE_TECNICA deadline");
        let mut received = [0_u8; 8];

        let error = stream
            .read_exact(&mut received)
            .expect_err("FIXTURE_TECNICA drip must exceed one fixed deadline");

        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(stream.transport.reads < received.len());
    }

    #[test]
    fn stream_fails_as_soon_as_server_probe_reports_exit() {
        let transport = FixtureDripTransport {
            bytes: b"FIXTURE_TECNICA".to_vec(),
            offset: 0,
            reads: 0,
            delay: Duration::ZERO,
        };
        let mut probe = FixtureProbe {
            checks: 0,
            fail_at: Some(3),
        };
        let mut stream = DeadlineStream::new(transport, Duration::from_secs(1), &mut probe)
            .expect("FIXTURE_TECNICA deadline");
        let mut received = [0_u8; 2];

        let error = stream
            .read_exact(&mut received)
            .expect_err("FIXTURE_TECNICA dead server");

        assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
        assert_eq!(stream.transport.reads, 1);
    }

    #[test]
    fn child_liveness_probe_reaps_an_exited_server() {
        let executable = std::env::current_exe().expect("FIXTURE_TECNICA test executable");
        let mut child = Command::new(executable)
            .args(["--exact", "tests::fixture_tecnica_server_child_exits"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("FIXTURE_TECNICA child");
        let deadline = Instant::now() + Duration::from_secs(2);

        loop {
            match child.verify_live() {
                Ok(()) if Instant::now() < deadline => {
                    thread::sleep(Duration::from_millis(5));
                }
                Ok(()) => panic!("FIXTURE_TECNICA child did not exit"),
                Err(error) => {
                    assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
                    break;
                }
            }
        }
    }

    #[test]
    fn fixture_tecnica_server_child_exits() {}
}
