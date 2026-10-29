use core::fmt;
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};

use ha_catalog::{ExternalEntityId, RegistryEntryId};
use serde_json::{Map, Value, json};

pub const READ_ONLY_COMMAND_ALLOWLIST: [&str; 8] = [
    "config/area_registry/list",
    "config/device_registry/list",
    "config/entity_registry/list",
    "config/entity_registry/get_entries",
    "config/floor_registry/list",
    "get_services",
    "get_states",
    "homeassistant/expose_entity/list",
];
pub const MAX_HANDSHAKE_BYTES: usize = 16_384;
pub const MAX_SERVER_FRAME_BYTES: usize = 4_194_304;
pub const MAX_SYNCHRONIZATION_BYTES: usize =
    MAX_SERVER_FRAME_BYTES * READ_ONLY_COMMAND_ALLOWLIST.len();

const SUPERVISOR_HOST: &str = "supervisor";
const SUPERVISOR_WEBSOCKET_PATH: &str = "/core/websocket";
const WEBSOCKET_GUID: &[u8] = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const MAX_AUTH_TOKEN_BYTES: usize = 16_384;
const MAX_CLIENT_FRAME_BYTES: usize = 131_072;
const MAX_HTTP_HEADERS: usize = 64;
const MAX_JSON_DEPTH: usize = 64;
const MAX_JSON_ITEMS: usize = 100_000;
const MAX_JSON_STRING_BYTES: usize = 65_536;
const MAX_CONTROL_FRAMES_PER_MESSAGE: usize = 16;
const MAX_REQUEST_ID: u64 = i64::MAX as u64;

const OPCODE_TEXT: u8 = 0x1;
const OPCODE_CLOSE: u8 = 0x8;
const OPCODE_PING: u8 = 0x9;
const OPCODE_PONG: u8 = 0xa;

const CLOSE_NORMAL: u16 = 1000;
const CLOSE_PROTOCOL: u16 = 1002;
const CLOSE_UNSUPPORTED_DATA: u16 = 1003;
const CLOSE_INVALID_PAYLOAD: u16 = 1007;
const CLOSE_POLICY: u16 = 1008;
const CLOSE_TOO_LARGE: u16 = 1009;

pub type SupervisorResult<T> = core::result::Result<T, SupervisorError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SupervisorError {
    InvalidConfiguration,
    Io,
    MaskingKeyUnavailable,
    HandshakeTooLarge,
    HandshakeRejected,
    AuthenticationRejected,
    ProtocolViolation,
    InvalidMessage,
    FrameTooLarge,
    CommandRejected,
    PeerClosed,
    RequestIdExhausted,
    Closed,
    SynchronizationTooLarge,
}

impl SupervisorError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidConfiguration => "invalid_configuration",
            Self::Io => "io",
            Self::MaskingKeyUnavailable => "masking_key_unavailable",
            Self::HandshakeTooLarge => "handshake_too_large",
            Self::HandshakeRejected => "handshake_rejected",
            Self::AuthenticationRejected => "authentication_rejected",
            Self::ProtocolViolation => "protocol_violation",
            Self::InvalidMessage => "invalid_message",
            Self::FrameTooLarge => "frame_too_large",
            Self::CommandRejected => "command_rejected",
            Self::PeerClosed => "peer_closed",
            Self::RequestIdExhausted => "request_id_exhausted",
            Self::Closed => "closed",
            Self::SynchronizationTooLarge => "synchronization_too_large",
        }
    }
}

impl fmt::Display for SupervisorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for SupervisorError {}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum SupervisorCommand {
    AreaRegistryList,
    DeviceRegistryList,
    EntityRegistryList,
    FloorRegistryList,
    GetServices,
    GetStates,
    ExposeEntityList,
}

impl SupervisorCommand {
    pub const ALL: [Self; 7] = [
        Self::AreaRegistryList,
        Self::DeviceRegistryList,
        Self::EntityRegistryList,
        Self::FloorRegistryList,
        Self::GetServices,
        Self::GetStates,
        Self::ExposeEntityList,
    ];

    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::AreaRegistryList => READ_ONLY_COMMAND_ALLOWLIST[0],
            Self::DeviceRegistryList => READ_ONLY_COMMAND_ALLOWLIST[1],
            Self::EntityRegistryList => READ_ONLY_COMMAND_ALLOWLIST[2],
            Self::FloorRegistryList => READ_ONLY_COMMAND_ALLOWLIST[4],
            Self::GetServices => READ_ONLY_COMMAND_ALLOWLIST[5],
            Self::GetStates => READ_ONLY_COMMAND_ALLOWLIST[6],
            Self::ExposeEntityList => READ_ONLY_COMMAND_ALLOWLIST[7],
        }
    }
}

pub struct SupervisorEntry {
    command: SupervisorCommand,
    result: Value,
}

impl SupervisorEntry {
    #[must_use]
    pub const fn command(&self) -> SupervisorCommand {
        self.command
    }

    #[must_use]
    pub const fn result(&self) -> &Value {
        &self.result
    }
}

impl fmt::Debug for SupervisorEntry {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SupervisorEntry")
            .field("command", &self.command)
            .field("result", &"redacted")
            .finish()
    }
}

pub struct SupervisorSnapshot {
    entries: Vec<SupervisorEntry>,
    wire_bytes: usize,
}

impl SupervisorSnapshot {
    pub fn from_results(
        results: impl IntoIterator<Item = (SupervisorCommand, Value)>,
    ) -> SupervisorResult<Self> {
        let mut by_command = std::collections::BTreeMap::new();
        let mut wire_bytes = 0_usize;
        for (command, result) in results {
            if by_command.insert(command, result).is_some() {
                return Err(SupervisorError::InvalidMessage);
            }
        }
        if by_command.len() != SupervisorCommand::ALL.len() {
            return Err(SupervisorError::InvalidMessage);
        }
        let mut entries = Vec::with_capacity(SupervisorCommand::ALL.len());
        for command in SupervisorCommand::ALL {
            let result = by_command
                .remove(&command)
                .ok_or(SupervisorError::InvalidMessage)?;
            let encoded = nlu_data::canonical_json(&result, "Supervisor snapshot")
                .map_err(|_| SupervisorError::InvalidMessage)?;
            wire_bytes = wire_bytes
                .checked_add(encoded.len())
                .ok_or(SupervisorError::SynchronizationTooLarge)?;
            if encoded.len() > MAX_SERVER_FRAME_BYTES || wire_bytes > MAX_SYNCHRONIZATION_BYTES {
                return Err(SupervisorError::SynchronizationTooLarge);
            }
            entries.push(SupervisorEntry { command, result });
        }
        Ok(Self {
            entries,
            wire_bytes,
        })
    }

    #[must_use]
    pub fn entries(&self) -> &[SupervisorEntry] {
        &self.entries
    }

    #[must_use]
    pub fn get(&self, command: SupervisorCommand) -> Option<&Value> {
        self.entries
            .iter()
            .find(|entry| entry.command == command)
            .map(|entry| &entry.result)
    }

    #[must_use]
    pub const fn wire_bytes(&self) -> usize {
        self.wire_bytes
    }
}

impl fmt::Debug for SupervisorSnapshot {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SupervisorSnapshot")
            .field("entry_count", &self.entries.len())
            .field("wire_bytes", &self.wire_bytes)
            .field("residential_data", &"redacted")
            .finish()
    }
}

pub struct SupervisorClient<S, M> {
    io: S,
    masking_source: M,
    next_request_id: u64,
    closed: bool,
}

impl<S, M> fmt::Debug for SupervisorClient<S, M> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SupervisorClient")
            .field("next_request_id", &self.next_request_id)
            .field("closed", &self.closed)
            .field("transport", &"redacted")
            .finish()
    }
}

impl<S, M> SupervisorClient<S, M>
where
    S: Read + Write,
    M: Read,
{
    /// `websocket_nonce` and every four bytes read from `masking_source` must
    /// come from a cryptographically secure operating-system entropy source.
    /// The supplied I/O object must enforce the product's read/write deadlines.
    pub fn connect(
        mut io: S,
        masking_source: M,
        supervisor_token: &str,
        websocket_nonce: [u8; 16],
    ) -> SupervisorResult<Self> {
        validate_connect_inputs(supervisor_token, &websocket_nonce)?;
        perform_handshake(&mut io, websocket_nonce)?;

        let mut client = Self {
            io,
            masking_source,
            next_request_id: 1,
            closed: false,
        };
        let (required, _) = client.read_json_message()?;
        if !valid_auth_message(&required, "auth_required") {
            return client.protocol_failure(CLOSE_POLICY, SupervisorError::ProtocolViolation);
        }
        client.send_auth(supervisor_token)?;
        let (reply, _) = client.read_json_message()?;
        match reply.get("type").and_then(Value::as_str) {
            Some("auth_ok") if valid_auth_message(&reply, "auth_ok") => Ok(client),
            Some("auth_invalid") if valid_auth_invalid(&reply) => {
                client.protocol_failure(CLOSE_POLICY, SupervisorError::AuthenticationRejected)
            }
            _ => client.protocol_failure(CLOSE_POLICY, SupervisorError::ProtocolViolation),
        }
    }

    pub fn request(&mut self, command: SupervisorCommand) -> SupervisorResult<Value> {
        self.request_with_size(command).map(|(value, _)| value)
    }

    pub fn synchronize_read_only(&mut self) -> SupervisorResult<SupervisorSnapshot> {
        let mut entries = Vec::with_capacity(SupervisorCommand::ALL.len());
        let mut wire_bytes = 0_usize;
        for command in SupervisorCommand::ALL {
            let (mut result, response_bytes) = self.request_with_size(command)?;
            wire_bytes = wire_bytes
                .checked_add(response_bytes)
                .ok_or(SupervisorError::SynchronizationTooLarge)?;
            if wire_bytes > MAX_SYNCHRONIZATION_BYTES {
                return self
                    .protocol_failure(CLOSE_TOO_LARGE, SupervisorError::SynchronizationTooLarge);
            }
            if command == SupervisorCommand::EntityRegistryList {
                let entity_ids = match entity_registry_request_ids(&result) {
                    Ok(entity_ids) => entity_ids,
                    Err(error) => return self.protocol_failure(CLOSE_POLICY, error),
                };
                let (extended, extended_bytes) =
                    self.request_entity_registry_entries(&entity_ids)?;
                wire_bytes = wire_bytes
                    .checked_add(extended_bytes)
                    .ok_or(SupervisorError::SynchronizationTooLarge)?;
                if wire_bytes > MAX_SYNCHRONIZATION_BYTES {
                    return self.protocol_failure(
                        CLOSE_TOO_LARGE,
                        SupervisorError::SynchronizationTooLarge,
                    );
                }
                result = match merge_entity_registry_entries(&result, &extended) {
                    Ok(merged) => merged,
                    Err(error) => return self.protocol_failure(CLOSE_POLICY, error),
                };
            }
            entries.push(SupervisorEntry { command, result });
        }
        Ok(SupervisorSnapshot {
            entries,
            wire_bytes,
        })
    }

    pub fn close(&mut self) -> SupervisorResult<()> {
        if self.closed {
            return Err(SupervisorError::Closed);
        }
        let payload = CLOSE_NORMAL.to_be_bytes();
        let result = self.send_frame(OPCODE_CLOSE, &payload);
        self.closed = true;
        result
    }

    #[must_use]
    pub const fn is_closed(&self) -> bool {
        self.closed
    }

    fn request_with_size(
        &mut self,
        command: SupervisorCommand,
    ) -> SupervisorResult<(Value, usize)> {
        self.request_message_with_size(command.as_str(), None)
    }

    fn request_entity_registry_entries(
        &mut self,
        entity_ids: &[Box<str>],
    ) -> SupervisorResult<(Value, usize)> {
        self.request_message_with_size(READ_ONLY_COMMAND_ALLOWLIST[3], Some(entity_ids))
    }

    fn request_message_with_size(
        &mut self,
        command: &str,
        entity_ids: Option<&[Box<str>]>,
    ) -> SupervisorResult<(Value, usize)> {
        if self.closed {
            return Err(SupervisorError::Closed);
        }
        let request_id = self.next_request_id;
        if request_id > MAX_REQUEST_ID {
            return Err(SupervisorError::RequestIdExhausted);
        }
        self.next_request_id = request_id + 1;
        let request_value = if let Some(entity_ids) = entity_ids {
            json!({
                "entity_ids": entity_ids,
                "id": request_id,
                "type": command,
            })
        } else {
            json!({
                "id": request_id,
                "type": command,
            })
        };
        let request = nlu_data::canonical_json(&request_value, "Supervisor WebSocket request")
            .map_err(|_| SupervisorError::InvalidMessage)?;
        self.send_frame(OPCODE_TEXT, &request)?;

        let (response, response_bytes) = self.read_json_message()?;
        let Some(object) = response.as_object() else {
            return self.protocol_failure(CLOSE_POLICY, SupervisorError::InvalidMessage);
        };
        if object.get("id").and_then(Value::as_u64) != Some(request_id)
            || object.get("type").and_then(Value::as_str) != Some("result")
        {
            return self.protocol_failure(CLOSE_POLICY, SupervisorError::InvalidMessage);
        }
        match object.get("success").and_then(Value::as_bool) {
            Some(true) => {
                if !has_exact_fields(object, &["id", "result", "success", "type"]) {
                    return self.protocol_failure(CLOSE_POLICY, SupervisorError::InvalidMessage);
                }
                let result = object
                    .get("result")
                    .cloned()
                    .ok_or(SupervisorError::InvalidMessage)?;
                Ok((result, response_bytes))
            }
            Some(false) => {
                if !has_exact_fields(object, &["error", "id", "success", "type"])
                    || !valid_command_error(object.get("error"))
                {
                    return self.protocol_failure(CLOSE_POLICY, SupervisorError::InvalidMessage);
                }
                Err(SupervisorError::CommandRejected)
            }
            _ => self.protocol_failure(CLOSE_POLICY, SupervisorError::InvalidMessage),
        }
    }

    fn send_auth(&mut self, supervisor_token: &str) -> SupervisorResult<()> {
        let mut encoded_token =
            serde_json::to_vec(supervisor_token).map_err(|_| SupervisorError::InvalidMessage)?;
        let capacity = encoded_token
            .len()
            .checked_add(32)
            .ok_or(SupervisorError::InvalidMessage)?;
        if capacity > MAX_CLIENT_FRAME_BYTES {
            encoded_token.fill(0);
            return Err(SupervisorError::InvalidConfiguration);
        }
        let mut message = Vec::with_capacity(capacity);
        message.extend_from_slice(b"{\"access_token\":");
        message.extend_from_slice(&encoded_token);
        message.extend_from_slice(b",\"type\":\"auth\"}");
        encoded_token.fill(0);
        let result = self.send_frame(OPCODE_TEXT, &message);
        message.fill(0);
        result
    }

    fn read_json_message(&mut self) -> SupervisorResult<(Value, usize)> {
        let mut controls = 0_usize;
        loop {
            let frame = self.read_frame()?;
            match frame.opcode {
                OPCODE_TEXT => {
                    let mut payload = frame.payload;
                    let payload_len = payload.len();
                    let parsed = parse_strict_json(&payload);
                    payload.fill(0);
                    return match parsed {
                        Ok(value) => Ok((value, payload_len)),
                        Err(()) => self.protocol_failure(
                            CLOSE_INVALID_PAYLOAD,
                            SupervisorError::InvalidMessage,
                        ),
                    };
                }
                OPCODE_PING => {
                    controls = controls
                        .checked_add(1)
                        .ok_or(SupervisorError::ProtocolViolation)?;
                    if controls > MAX_CONTROL_FRAMES_PER_MESSAGE {
                        return self
                            .protocol_failure(CLOSE_POLICY, SupervisorError::ProtocolViolation);
                    }
                    self.send_frame(OPCODE_PONG, &frame.payload)?;
                }
                OPCODE_PONG => {
                    controls = controls
                        .checked_add(1)
                        .ok_or(SupervisorError::ProtocolViolation)?;
                    if controls > MAX_CONTROL_FRAMES_PER_MESSAGE {
                        return self
                            .protocol_failure(CLOSE_POLICY, SupervisorError::ProtocolViolation);
                    }
                }
                OPCODE_CLOSE => {
                    let reply = self.send_frame(OPCODE_CLOSE, &frame.payload);
                    self.closed = true;
                    reply?;
                    return Err(SupervisorError::PeerClosed);
                }
                _ => {
                    return self
                        .protocol_failure(CLOSE_PROTOCOL, SupervisorError::ProtocolViolation);
                }
            }
        }
    }

    fn read_frame(&mut self) -> SupervisorResult<ServerFrame> {
        match read_server_frame(&mut self.io) {
            Ok(frame) => Ok(frame),
            Err(FrameReadError::Io) => {
                self.closed = true;
                Err(SupervisorError::Io)
            }
            Err(FrameReadError::Violation { close_code, error }) => {
                self.protocol_failure(close_code, error)
            }
        }
    }

    fn send_frame(&mut self, opcode: u8, payload: &[u8]) -> SupervisorResult<()> {
        if self.closed {
            return Err(SupervisorError::Closed);
        }
        if payload.len() > MAX_CLIENT_FRAME_BYTES || (opcode & 0x08 != 0 && payload.len() > 125) {
            self.closed = true;
            return Err(SupervisorError::FrameTooLarge);
        }
        let mut mask = [0_u8; 4];
        if self.masking_source.read_exact(&mut mask).is_err() {
            self.closed = true;
            return Err(SupervisorError::MaskingKeyUnavailable);
        }
        if write_client_frame(&mut self.io, opcode, payload, mask).is_err() {
            self.closed = true;
            return Err(SupervisorError::Io);
        }
        Ok(())
    }

    fn protocol_failure<T>(
        &mut self,
        close_code: u16,
        error: SupervisorError,
    ) -> SupervisorResult<T> {
        if !self.closed {
            let payload = close_code.to_be_bytes();
            let _ = self.send_frame(OPCODE_CLOSE, &payload);
            self.closed = true;
        }
        Err(error)
    }
}

struct SlimRegistryEntry<'a> {
    entity_id: Box<str>,
    object: &'a Map<String, Value>,
}

struct ExtendedRegistryEntry<'a> {
    entity_id: Box<str>,
    object: &'a Map<String, Value>,
    aliases: Vec<String>,
}

fn entity_registry_request_ids(value: &Value) -> SupervisorResult<Vec<Box<str>>> {
    Ok(index_slim_entity_registry(value)?
        .into_values()
        .map(|entry| entry.entity_id)
        .collect())
}

fn index_slim_entity_registry(
    value: &Value,
) -> SupervisorResult<BTreeMap<Box<str>, SlimRegistryEntry<'_>>> {
    let values = value.as_array().ok_or(SupervisorError::InvalidMessage)?;
    if values.len() > ha_catalog::snapshot::MAX_ENTITIES {
        return Err(SupervisorError::InvalidMessage);
    }
    let mut external_ids = BTreeSet::<Box<str>>::new();
    let mut entries = BTreeMap::new();
    for value in values {
        let object = value.as_object().ok_or(SupervisorError::InvalidMessage)?;
        let registry_id = required_registry_string(object, "id")?;
        RegistryEntryId::new(registry_id).map_err(|_| SupervisorError::InvalidMessage)?;
        let entity_id = required_registry_string(object, "entity_id")?;
        ExternalEntityId::new(entity_id).map_err(|_| SupervisorError::InvalidMessage)?;
        if !external_ids.insert(entity_id.into())
            || entries
                .insert(
                    registry_id.into(),
                    SlimRegistryEntry {
                        entity_id: entity_id.into(),
                        object,
                    },
                )
                .is_some()
        {
            return Err(SupervisorError::InvalidMessage);
        }
    }
    Ok(entries)
}

fn merge_entity_registry_entries(slim: &Value, extended: &Value) -> SupervisorResult<Value> {
    let slim_entries = index_slim_entity_registry(slim)?;
    let extended_values = extended
        .as_object()
        .ok_or(SupervisorError::InvalidMessage)?;
    if extended_values.len() != slim_entries.len()
        || extended_values.len() > ha_catalog::snapshot::MAX_ENTITIES
    {
        return Err(SupervisorError::InvalidMessage);
    }

    let mut extended_external_ids = BTreeSet::<Box<str>>::new();
    let mut extended_entries = BTreeMap::<Box<str>, ExtendedRegistryEntry<'_>>::new();
    for (response_entity_id, value) in extended_values {
        ExternalEntityId::new(response_entity_id).map_err(|_| SupervisorError::InvalidMessage)?;
        let object = value.as_object().ok_or(SupervisorError::InvalidMessage)?;
        let registry_id = required_registry_string(object, "id")?;
        RegistryEntryId::new(registry_id).map_err(|_| SupervisorError::InvalidMessage)?;
        let entity_id = required_registry_string(object, "entity_id")?;
        ExternalEntityId::new(entity_id).map_err(|_| SupervisorError::InvalidMessage)?;
        if entity_id != response_entity_id
            || !extended_external_ids.insert(entity_id.into())
            || extended_entries
                .insert(
                    registry_id.into(),
                    ExtendedRegistryEntry {
                        entity_id: entity_id.into(),
                        object,
                        aliases: canonical_explicit_aliases(object)?,
                    },
                )
                .is_some()
        {
            return Err(SupervisorError::InvalidMessage);
        }
    }

    let mut merged = Vec::with_capacity(slim_entries.len());
    for (registry_id, slim_entry) in slim_entries {
        let extended_entry = extended_entries
            .remove(&registry_id)
            .ok_or(SupervisorError::InvalidMessage)?;
        if slim_entry.entity_id != extended_entry.entity_id
            || slim_entry
                .object
                .iter()
                .any(|(field, value)| extended_entry.object.get(field) != Some(value))
        {
            return Err(SupervisorError::InvalidMessage);
        }
        let mut object = slim_entry.object.clone();
        object.insert(
            "aliases".to_owned(),
            Value::Array(
                extended_entry
                    .aliases
                    .into_iter()
                    .map(Value::String)
                    .collect(),
            ),
        );
        merged.push(Value::Object(object));
    }
    if !extended_entries.is_empty() {
        return Err(SupervisorError::InvalidMessage);
    }
    Ok(Value::Array(merged))
}

fn required_registry_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> SupervisorResult<&'a str> {
    object
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or(SupervisorError::InvalidMessage)
}

fn canonical_explicit_aliases(object: &Map<String, Value>) -> SupervisorResult<Vec<String>> {
    let aliases = object
        .get("aliases")
        .and_then(Value::as_array)
        .ok_or(SupervisorError::InvalidMessage)?;
    if aliases.len() > ha_catalog::snapshot::MAX_ALIASES_PER_RECORD + 1 {
        return Err(SupervisorError::InvalidMessage);
    }
    let mut computed_name = false;
    let mut explicit = BTreeSet::new();
    for alias in aliases {
        match alias {
            Value::Null if !computed_name => computed_name = true,
            Value::String(value) if !value.is_empty() && explicit.insert(value.clone()) => {}
            _ => return Err(SupervisorError::InvalidMessage),
        }
    }
    if explicit.len() > ha_catalog::snapshot::MAX_ALIASES_PER_RECORD {
        return Err(SupervisorError::InvalidMessage);
    }
    Ok(explicit.into_iter().collect())
}

fn validate_connect_inputs(token: &str, nonce: &[u8; 16]) -> SupervisorResult<()> {
    if token.is_empty()
        || token.len() > MAX_AUTH_TOKEN_BYTES
        || !token.bytes().all(|byte| (0x21..=0x7e).contains(&byte))
        || nonce.iter().all(|byte| *byte == 0)
    {
        return Err(SupervisorError::InvalidConfiguration);
    }
    Ok(())
}

fn perform_handshake(io: &mut (impl Read + Write), nonce: [u8; 16]) -> SupervisorResult<()> {
    let websocket_key = base64_encode(&nonce);
    let request = format!(
        "GET {SUPERVISOR_WEBSOCKET_PATH} HTTP/1.1\r\n\
         Host: {SUPERVISOR_HOST}\r\n\
         Upgrade: websocket\r\n\
         Connection: Upgrade\r\n\
         Sec-WebSocket-Key: {websocket_key}\r\n\
         Sec-WebSocket-Version: 13\r\n\
         \r\n"
    );
    io.write_all(request.as_bytes())
        .and_then(|()| io.flush())
        .map_err(|_| SupervisorError::Io)?;

    let response = read_http_head(io)?;
    validate_handshake_response(&response, &websocket_key)
}

fn read_http_head(reader: &mut impl Read) -> SupervisorResult<Vec<u8>> {
    let mut response = Vec::with_capacity(512);
    let mut byte = [0_u8; 1];
    while response.len() < MAX_HANDSHAKE_BYTES {
        reader
            .read_exact(&mut byte)
            .map_err(|_| SupervisorError::Io)?;
        response.push(byte[0]);
        if response.ends_with(b"\r\n\r\n") {
            return Ok(response);
        }
    }
    Err(SupervisorError::HandshakeTooLarge)
}

fn validate_handshake_response(response: &[u8], websocket_key: &str) -> SupervisorResult<()> {
    let text = std::str::from_utf8(response).map_err(|_| SupervisorError::HandshakeRejected)?;
    let head = text
        .strip_suffix("\r\n\r\n")
        .ok_or(SupervisorError::HandshakeRejected)?;
    let mut lines = head.split("\r\n");
    let status = lines.next().ok_or(SupervisorError::HandshakeRejected)?;
    let mut status_parts = status.splitn(3, ' ');
    if status_parts.next() != Some("HTTP/1.1")
        || status_parts.next() != Some("101")
        || status_parts.next().is_none()
    {
        return Err(SupervisorError::HandshakeRejected);
    }

    let mut upgrade = None;
    let mut connection = None;
    let mut accept = None;
    let mut header_count = 0_usize;
    for line in lines {
        header_count = header_count
            .checked_add(1)
            .ok_or(SupervisorError::HandshakeRejected)?;
        if header_count > MAX_HTTP_HEADERS
            || line.starts_with([' ', '\t'])
            || line.bytes().any(invalid_header_byte)
        {
            return Err(SupervisorError::HandshakeRejected);
        }
        let (name, raw_value) = line
            .split_once(':')
            .ok_or(SupervisorError::HandshakeRejected)?;
        if name.is_empty() || !name.bytes().all(is_http_token_byte) {
            return Err(SupervisorError::HandshakeRejected);
        }
        let value = raw_value.trim_matches([' ', '\t']);
        match name.to_ascii_lowercase().as_str() {
            "upgrade" => set_once(&mut upgrade, value)?,
            "connection" => set_once(&mut connection, value)?,
            "sec-websocket-accept" => set_once(&mut accept, value)?,
            "sec-websocket-extensions" | "sec-websocket-protocol" => {
                return Err(SupervisorError::HandshakeRejected);
            }
            _ => {}
        }
    }

    let upgrade = upgrade.ok_or(SupervisorError::HandshakeRejected)?;
    let connection = connection.ok_or(SupervisorError::HandshakeRejected)?;
    let accept = accept.ok_or(SupervisorError::HandshakeRejected)?;
    if !single_header_token(upgrade, "websocket")
        || !header_has_token(connection, "upgrade")
        || accept != expected_websocket_accept(websocket_key)
    {
        return Err(SupervisorError::HandshakeRejected);
    }
    Ok(())
}

fn set_once<'a>(slot: &mut Option<&'a str>, value: &'a str) -> SupervisorResult<()> {
    if slot.replace(value).is_some() || value.is_empty() {
        return Err(SupervisorError::HandshakeRejected);
    }
    Ok(())
}

fn single_header_token(value: &str, expected: &str) -> bool {
    !value.contains(',')
        && value.bytes().all(is_http_token_byte)
        && value.eq_ignore_ascii_case(expected)
}

fn header_has_token(value: &str, expected: &str) -> bool {
    let mut found = false;
    for token in value.split(',') {
        let token = token.trim_matches([' ', '\t']);
        if token.is_empty() || !token.bytes().all(is_http_token_byte) {
            return false;
        }
        found |= token.eq_ignore_ascii_case(expected);
    }
    found
}

fn invalid_header_byte(byte: u8) -> bool {
    byte < 0x20 && byte != b'\t' || byte == 0x7f
}

fn is_http_token_byte(byte: u8) -> bool {
    matches!(
        byte,
        b'!' | b'#'..=b'\'' | b'*' | b'+' | b'-' | b'.' | b'0'..=b'9' | b'A'..=b'Z'
            | b'^'..=b'z' | b'|'
    ) && byte != b'`'
}

fn expected_websocket_accept(websocket_key: &str) -> String {
    let mut challenge = Vec::with_capacity(websocket_key.len() + WEBSOCKET_GUID.len());
    challenge.extend_from_slice(websocket_key.as_bytes());
    challenge.extend_from_slice(WEBSOCKET_GUID);
    base64_encode(&sha1(&challenge))
}

fn write_client_frame(
    writer: &mut impl Write,
    opcode: u8,
    payload: &[u8],
    mask: [u8; 4],
) -> std::io::Result<()> {
    let mut header = Vec::with_capacity(14);
    header.push(0x80 | opcode);
    match payload.len() {
        0..=125 => header.push(0x80 | u8::try_from(payload.len()).unwrap_or(125)),
        126..=65_535 => {
            header.push(0x80 | 126);
            header.extend_from_slice(
                &u16::try_from(payload.len())
                    .unwrap_or(u16::MAX)
                    .to_be_bytes(),
            );
        }
        _ => {
            header.push(0x80 | 127);
            header.extend_from_slice(
                &u64::try_from(payload.len())
                    .unwrap_or(u64::MAX)
                    .to_be_bytes(),
            );
        }
    }
    header.extend_from_slice(&mask);
    let mut masked = Vec::with_capacity(payload.len());
    masked.extend(
        payload
            .iter()
            .enumerate()
            .map(|(index, byte)| byte ^ mask[index % mask.len()]),
    );
    let result = writer
        .write_all(&header)
        .and_then(|()| writer.write_all(&masked))
        .and_then(|()| writer.flush());
    masked.fill(0);
    result
}

struct ServerFrame {
    opcode: u8,
    payload: Vec<u8>,
}

enum FrameReadError {
    Io,
    Violation {
        close_code: u16,
        error: SupervisorError,
    },
}

fn read_server_frame(reader: &mut impl Read) -> core::result::Result<ServerFrame, FrameReadError> {
    let mut header = [0_u8; 2];
    reader
        .read_exact(&mut header)
        .map_err(|_| FrameReadError::Io)?;
    let fin = header[0] & 0x80 != 0;
    let reserved = header[0] & 0x70;
    let opcode = header[0] & 0x0f;
    let masked = header[1] & 0x80 != 0;
    if !fin || reserved != 0 || masked {
        return Err(frame_violation(
            CLOSE_PROTOCOL,
            SupervisorError::ProtocolViolation,
        ));
    }
    if opcode == 0
        || !matches!(
            opcode,
            OPCODE_TEXT | 0x2 | OPCODE_CLOSE | OPCODE_PING | OPCODE_PONG
        )
    {
        return Err(frame_violation(
            CLOSE_PROTOCOL,
            SupervisorError::ProtocolViolation,
        ));
    }
    if opcode == 0x2 {
        return Err(frame_violation(
            CLOSE_UNSUPPORTED_DATA,
            SupervisorError::ProtocolViolation,
        ));
    }

    let short_length = header[1] & 0x7f;
    let payload_length = match short_length {
        0..=125 => u64::from(short_length),
        126 => {
            let mut bytes = [0_u8; 2];
            reader
                .read_exact(&mut bytes)
                .map_err(|_| FrameReadError::Io)?;
            let length = u64::from(u16::from_be_bytes(bytes));
            if length < 126 {
                return Err(frame_violation(
                    CLOSE_PROTOCOL,
                    SupervisorError::ProtocolViolation,
                ));
            }
            length
        }
        127 => {
            let mut bytes = [0_u8; 8];
            reader
                .read_exact(&mut bytes)
                .map_err(|_| FrameReadError::Io)?;
            let length = u64::from_be_bytes(bytes);
            if bytes[0] & 0x80 != 0 || length < 65_536 {
                return Err(frame_violation(
                    CLOSE_PROTOCOL,
                    SupervisorError::ProtocolViolation,
                ));
            }
            length
        }
        _ => unreachable!(),
    };
    if opcode & 0x08 != 0 && payload_length > 125 {
        return Err(frame_violation(
            CLOSE_PROTOCOL,
            SupervisorError::ProtocolViolation,
        ));
    }
    if payload_length > MAX_SERVER_FRAME_BYTES as u64 {
        return Err(frame_violation(
            CLOSE_TOO_LARGE,
            SupervisorError::FrameTooLarge,
        ));
    }
    let length = usize::try_from(payload_length)
        .map_err(|_| frame_violation(CLOSE_TOO_LARGE, SupervisorError::FrameTooLarge))?;
    let mut payload = vec![0_u8; length];
    reader
        .read_exact(&mut payload)
        .map_err(|_| FrameReadError::Io)?;
    if opcode == OPCODE_CLOSE && !valid_close_payload(&payload) {
        payload.fill(0);
        return Err(frame_violation(
            CLOSE_PROTOCOL,
            SupervisorError::ProtocolViolation,
        ));
    }
    Ok(ServerFrame { opcode, payload })
}

const fn frame_violation(close_code: u16, error: SupervisorError) -> FrameReadError {
    FrameReadError::Violation { close_code, error }
}

fn valid_close_payload(payload: &[u8]) -> bool {
    if payload.is_empty() {
        return true;
    }
    if payload.len() == 1 {
        return false;
    }
    let code = u16::from_be_bytes([payload[0], payload[1]]);
    valid_close_code(code) && std::str::from_utf8(&payload[2..]).is_ok()
}

const fn valid_close_code(code: u16) -> bool {
    matches!(code, 1000..=1003 | 1007..=1014 | 3000..=4999) && !matches!(code, 1004..=1006)
}

fn valid_auth_message(value: &Value, expected_type: &str) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    has_exact_fields(object, &["ha_version", "type"])
        && object.get("type").and_then(Value::as_str) == Some(expected_type)
        && object
            .get("ha_version")
            .and_then(Value::as_str)
            .is_some_and(valid_version)
}

fn valid_auth_invalid(value: &Value) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    has_exact_fields(object, &["message", "type"])
        && object.get("type").and_then(Value::as_str) == Some("auth_invalid")
        && object
            .get("message")
            .and_then(Value::as_str)
            .is_some_and(|message| !message.is_empty())
}

fn valid_version(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_graphic() && byte != b'\\' && byte != b'"')
}

fn valid_command_error(value: Option<&Value>) -> bool {
    let Some(object) = value.and_then(Value::as_object) else {
        return false;
    };
    has_exact_fields(object, &["code", "message"])
        && ["code", "message"].iter().all(|field| {
            object
                .get(*field)
                .and_then(Value::as_str)
                .is_some_and(|text| !text.is_empty())
        })
}

fn has_exact_fields(object: &Map<String, Value>, fields: &[&str]) -> bool {
    object.len() == fields.len() && object.keys().all(|field| fields.contains(&field.as_str()))
}

fn parse_strict_json(bytes: &[u8]) -> core::result::Result<Value, ()> {
    if bytes.is_empty() || bytes.len() > MAX_SERVER_FRAME_BYTES {
        return Err(());
    }
    JsonScanner::new(bytes).scan_document()?;
    let value = serde_json::from_slice(bytes).map_err(|_| ())?;
    let mut item_count = 0_usize;
    validate_json_value(&value, 1, &mut item_count)?;
    Ok(value)
}

fn validate_json_value(
    value: &Value,
    depth: usize,
    item_count: &mut usize,
) -> core::result::Result<(), ()> {
    if depth > MAX_JSON_DEPTH {
        return Err(());
    }
    *item_count = item_count.checked_add(1).ok_or(())?;
    if *item_count > MAX_JSON_ITEMS {
        return Err(());
    }
    match value {
        Value::String(text) => {
            if text.len() > MAX_JSON_STRING_BYTES {
                return Err(());
            }
        }
        Value::Array(values) => {
            if values.len() > MAX_JSON_ITEMS {
                return Err(());
            }
            for child in values {
                validate_json_value(child, depth + 1, item_count)?;
            }
        }
        Value::Object(values) => {
            if values.len() > MAX_JSON_ITEMS {
                return Err(());
            }
            for (key, child) in values {
                if key.len() > MAX_JSON_STRING_BYTES {
                    return Err(());
                }
                validate_json_value(child, depth + 1, item_count)?;
            }
        }
        _ => {}
    }
    Ok(())
}

struct JsonScanner<'a> {
    bytes: &'a [u8],
    position: usize,
    item_count: usize,
}

impl<'a> JsonScanner<'a> {
    const fn new(bytes: &'a [u8]) -> Self {
        Self {
            bytes,
            position: 0,
            item_count: 0,
        }
    }

    fn scan_document(mut self) -> core::result::Result<(), ()> {
        self.skip_whitespace();
        self.scan_value(1)?;
        self.skip_whitespace();
        if self.position == self.bytes.len() {
            Ok(())
        } else {
            Err(())
        }
    }

    fn scan_value(&mut self, depth: usize) -> core::result::Result<(), ()> {
        if depth > MAX_JSON_DEPTH {
            return Err(());
        }
        self.item_count = self.item_count.checked_add(1).ok_or(())?;
        if self.item_count > MAX_JSON_ITEMS {
            return Err(());
        }
        self.skip_whitespace();
        match self.peek().ok_or(())? {
            b'{' => self.scan_object(depth),
            b'[' => self.scan_array(depth),
            b'"' => self.scan_string().map(|_| ()),
            b't' => self.consume_literal(b"true"),
            b'f' => self.consume_literal(b"false"),
            b'n' => self.consume_literal(b"null"),
            b'-' | b'0'..=b'9' => self.scan_number(),
            _ => Err(()),
        }
    }

    fn scan_object(&mut self, depth: usize) -> core::result::Result<(), ()> {
        self.consume_byte(b'{')?;
        self.skip_whitespace();
        if self.consume_if(b'}') {
            return Ok(());
        }
        let mut keys = BTreeSet::new();
        let mut fields = 0_usize;
        loop {
            self.skip_whitespace();
            let key_bytes = self.scan_string()?;
            let key: String = serde_json::from_slice(key_bytes).map_err(|_| ())?;
            if key.len() > MAX_JSON_STRING_BYTES || !keys.insert(key) {
                return Err(());
            }
            fields = fields.checked_add(1).ok_or(())?;
            if fields > MAX_JSON_ITEMS {
                return Err(());
            }
            self.skip_whitespace();
            self.consume_byte(b':')?;
            self.scan_value(depth + 1)?;
            self.skip_whitespace();
            if self.consume_if(b'}') {
                return Ok(());
            }
            self.consume_byte(b',')?;
        }
    }

    fn scan_array(&mut self, depth: usize) -> core::result::Result<(), ()> {
        self.consume_byte(b'[')?;
        self.skip_whitespace();
        if self.consume_if(b']') {
            return Ok(());
        }
        let mut values = 0_usize;
        loop {
            values = values.checked_add(1).ok_or(())?;
            if values > MAX_JSON_ITEMS {
                return Err(());
            }
            self.scan_value(depth + 1)?;
            self.skip_whitespace();
            if self.consume_if(b']') {
                return Ok(());
            }
            self.consume_byte(b',')?;
        }
    }

    fn scan_string(&mut self) -> core::result::Result<&'a [u8], ()> {
        let start = self.position;
        self.consume_byte(b'"')?;
        while let Some(byte) = self.peek() {
            match byte {
                b'"' => {
                    self.position += 1;
                    return Ok(&self.bytes[start..self.position]);
                }
                b'\\' => {
                    self.position += 1;
                    let escaped = self.peek().ok_or(())?;
                    self.position += 1;
                    if escaped == b'u' {
                        let end = self.position.checked_add(4).ok_or(())?;
                        let digits = self.bytes.get(self.position..end).ok_or(())?;
                        if !digits.iter().all(u8::is_ascii_hexdigit) {
                            return Err(());
                        }
                        self.position = end;
                    }
                }
                _ => self.position += 1,
            }
        }
        Err(())
    }

    fn scan_number(&mut self) -> core::result::Result<(), ()> {
        let start = self.position;
        while self
            .peek()
            .is_some_and(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n' | b',' | b']' | b'}'))
        {
            self.position += 1;
        }
        if self.position == start {
            Err(())
        } else {
            Ok(())
        }
    }

    fn consume_literal(&mut self, literal: &[u8]) -> core::result::Result<(), ()> {
        let end = self.position.checked_add(literal.len()).ok_or(())?;
        if self.bytes.get(self.position..end) != Some(literal) {
            return Err(());
        }
        self.position = end;
        Ok(())
    }

    fn consume_byte(&mut self, expected: u8) -> core::result::Result<(), ()> {
        if self.consume_if(expected) {
            Ok(())
        } else {
            Err(())
        }
    }

    fn consume_if(&mut self, expected: u8) -> bool {
        if self.peek() == Some(expected) {
            self.position += 1;
            true
        } else {
            false
        }
    }

    fn skip_whitespace(&mut self) {
        while self
            .peek()
            .is_some_and(|byte| matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        {
            self.position += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.position).copied()
    }
}

fn base64_encode(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let output_len = bytes.len().div_ceil(3) * 4;
    let mut output = String::with_capacity(output_len);
    let mut index = 0_usize;
    while index < bytes.len() {
        let first = bytes[index];
        let second = bytes.get(index + 1).copied();
        let third = bytes.get(index + 2).copied();
        output.push(char::from(ALPHABET[usize::from(first >> 2)]));
        output.push(char::from(
            ALPHABET[usize::from((first & 0x03) << 4 | second.unwrap_or(0) >> 4)],
        ));
        if let Some(second) = second {
            output.push(char::from(
                ALPHABET[usize::from((second & 0x0f) << 2 | third.unwrap_or(0) >> 6)],
            ));
        } else {
            output.push('=');
        }
        if let Some(third) = third {
            output.push(char::from(ALPHABET[usize::from(third & 0x3f)]));
        } else {
            output.push('=');
        }
        index += 3;
    }
    output
}

fn sha1(bytes: &[u8]) -> [u8; 20] {
    let bit_length = u64::try_from(bytes.len()).unwrap_or(u64::MAX) * 8;
    let mut padded = Vec::with_capacity(bytes.len() + 72);
    padded.extend_from_slice(bytes);
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_length.to_be_bytes());

    let mut state = [
        0x6745_2301_u32,
        0xefcd_ab89,
        0x98ba_dcfe,
        0x1032_5476,
        0xc3d2_e1f0,
    ];
    let (chunks, remainder) = padded.as_chunks::<64>();
    debug_assert!(remainder.is_empty());
    for chunk in chunks {
        let mut words = [0_u32; 80];
        let mut index = 0_usize;
        while index < 16 {
            let offset = index * 4;
            words[index] = u32::from_be_bytes([
                chunk[offset],
                chunk[offset + 1],
                chunk[offset + 2],
                chunk[offset + 3],
            ]);
            index += 1;
        }
        while index < words.len() {
            words[index] =
                (words[index - 3] ^ words[index - 8] ^ words[index - 14] ^ words[index - 16])
                    .rotate_left(1);
            index += 1;
        }

        let [mut a, mut b, mut c, mut d, mut e] = state;
        index = 0;
        while index < words.len() {
            let (function, constant) = match index {
                0..=19 => ((b & c) | ((!b) & d), 0x5a82_7999),
                20..=39 => (b ^ c ^ d, 0x6ed9_eba1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8f1b_bcdc),
                _ => (b ^ c ^ d, 0xca62_c1d6),
            };
            let next = a
                .rotate_left(5)
                .wrapping_add(function)
                .wrapping_add(e)
                .wrapping_add(constant)
                .wrapping_add(words[index]);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = next;
            index += 1;
        }
        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
    }

    let mut digest = [0_u8; 20];
    for (index, word) in state.into_iter().enumerate() {
        digest[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    digest
}
