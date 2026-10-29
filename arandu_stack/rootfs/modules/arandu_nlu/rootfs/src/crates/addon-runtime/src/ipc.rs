use std::io::{Read, Write};

use protocol::v2::{self, Request};
use serde_json::{Map, Value, json};

use crate::{Result, RuntimeError};

const IPC_VERSION: u16 = 1;
const PROTOCOL_V2_REQUEST_KIND: &str = "protocol_v2_request";
pub const MAX_SUBMISSION_TEXT_BYTES: usize = 16_384;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CompanionSubmissionKind {
    Interpret,
    Continue,
    Confirm,
    Cancel,
}

#[derive(Clone, Eq, PartialEq)]
enum CompanionSubmissionRequest {
    Interpret,
    ProtocolV2(Box<Request>),
}

#[derive(Clone, Eq, PartialEq)]
pub struct CompanionSubmission {
    original_source: String,
    context_id: String,
    caller_id: String,
    conversation_id: Option<String>,
    request: CompanionSubmissionRequest,
}

impl CompanionSubmission {
    pub fn new(
        text: String,
        context_id: String,
        caller_id: String,
        conversation_id: Option<String>,
    ) -> Result<Self> {
        Self::build(
            text,
            context_id,
            caller_id,
            conversation_id,
            CompanionSubmissionRequest::Interpret,
        )
    }

    pub fn from_protocol_v2(
        original_source: String,
        context_id: String,
        caller_id: String,
        conversation_id: Option<String>,
        protocol_request: &[u8],
    ) -> Result<Self> {
        let request =
            v2::decode_request(protocol_request).map_err(|_| RuntimeError::InvalidSubmission)?;
        if !matches!(
            &request,
            Request::Continue { .. } | Request::Confirm { .. } | Request::Cancel { .. }
        ) {
            return Err(RuntimeError::InvalidSubmission);
        }
        if matches!(&request, Request::Confirm { plan, .. } if plan.source().as_str() != original_source.as_str())
        {
            return Err(RuntimeError::InvalidSubmission);
        }
        Self::build(
            original_source,
            context_id,
            caller_id,
            conversation_id,
            CompanionSubmissionRequest::ProtocolV2(Box::new(request)),
        )
    }

    fn build(
        original_source: String,
        context_id: String,
        caller_id: String,
        conversation_id: Option<String>,
        request: CompanionSubmissionRequest,
    ) -> Result<Self> {
        if original_source.is_empty()
            || original_source.len() > MAX_SUBMISSION_TEXT_BYTES
            || original_source.chars().all(char::is_whitespace)
            || original_source.chars().any(char::is_control)
            || !valid_context_id(&context_id)
            || !valid_context_id(&caller_id)
            || conversation_id
                .as_deref()
                .is_some_and(|value| !valid_context_id(value))
        {
            return Err(RuntimeError::InvalidSubmission);
        }
        Ok(Self {
            original_source,
            context_id,
            caller_id,
            conversation_id,
            request,
        })
    }

    #[must_use]
    pub fn text(&self) -> &str {
        self.original_source()
    }

    #[must_use]
    pub fn original_source(&self) -> &str {
        &self.original_source
    }

    #[must_use]
    pub fn context_id(&self) -> &str {
        &self.context_id
    }

    #[must_use]
    pub fn caller_id(&self) -> &str {
        &self.caller_id
    }

    #[must_use]
    pub fn conversation_id(&self) -> Option<&str> {
        self.conversation_id.as_deref()
    }

    #[must_use]
    pub fn kind(&self) -> CompanionSubmissionKind {
        match &self.request {
            CompanionSubmissionRequest::Interpret => CompanionSubmissionKind::Interpret,
            CompanionSubmissionRequest::ProtocolV2(request) => match request.as_ref() {
                Request::Continue { .. } => CompanionSubmissionKind::Continue,
                Request::Confirm { .. } => CompanionSubmissionKind::Confirm,
                Request::Cancel { .. } => CompanionSubmissionKind::Cancel,
                Request::Interpret { .. } | Request::Health => unreachable!(),
            },
        }
    }

    #[must_use]
    pub fn protocol_request(&self) -> Option<&Request> {
        match &self.request {
            CompanionSubmissionRequest::Interpret => None,
            CompanionSubmissionRequest::ProtocolV2(request) => Some(request.as_ref()),
        }
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        let document = match &self.request {
            CompanionSubmissionRequest::Interpret => json!({
                "caller_id": self.caller_id,
                "context_id": self.context_id,
                "conversation_id": self.conversation_id,
                "language": "pt-BR",
                "text": self.original_source,
                "version": IPC_VERSION,
            }),
            CompanionSubmissionRequest::ProtocolV2(request) => {
                let encoded =
                    v2::encode_request(request).map_err(|_| RuntimeError::InvalidSubmission)?;
                let request = nlu_data::parse_strict_json(&encoded, "protocol-v2 request")
                    .map_err(|_| RuntimeError::InvalidSubmission)?;
                if !request.is_object() {
                    return Err(RuntimeError::InvalidSubmission);
                }
                json!({
                    "caller_id": self.caller_id,
                    "context_id": self.context_id,
                    "conversation_id": self.conversation_id,
                    "kind": PROTOCOL_V2_REQUEST_KIND,
                    "language": "pt-BR",
                    "original_source": self.original_source,
                    "protocol_request": request,
                    "version": IPC_VERSION,
                })
            }
        };
        let encoded = canonical(&document).map_err(|_| RuntimeError::InvalidSubmission)?;
        if encoded.is_empty() || encoded.len() > nlu_server::MAX_FRAME_BYTES {
            return Err(RuntimeError::InvalidSubmission);
        }
        Ok(encoded)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > nlu_server::MAX_FRAME_BYTES {
            return Err(RuntimeError::InvalidSubmission);
        }
        let value = nlu_data::parse_strict_json(bytes, "companion submission")
            .map_err(|_| RuntimeError::InvalidSubmission)?;
        let object = value.as_object().ok_or(RuntimeError::InvalidSubmission)?;
        if object.get("version").and_then(Value::as_u64) != Some(u64::from(IPC_VERSION))
            || object.get("language").and_then(Value::as_str) != Some("pt-BR")
        {
            return Err(RuntimeError::InvalidSubmission);
        }
        if has_exact_fields(
            object,
            &[
                "caller_id",
                "context_id",
                "conversation_id",
                "language",
                "text",
                "version",
            ],
        ) {
            let text = required_string(object, "text", RuntimeError::InvalidSubmission)?.to_owned();
            let (context_id, caller_id, conversation_id) = decode_submission_bindings(object)?;
            return Self::new(text, context_id, caller_id, conversation_id);
        }
        if !has_exact_fields(
            object,
            &[
                "caller_id",
                "context_id",
                "conversation_id",
                "kind",
                "language",
                "original_source",
                "protocol_request",
                "version",
            ],
        ) || object.get("kind").and_then(Value::as_str) != Some(PROTOCOL_V2_REQUEST_KIND)
        {
            return Err(RuntimeError::InvalidSubmission);
        }
        let original_source =
            required_string(object, "original_source", RuntimeError::InvalidSubmission)?.to_owned();
        let protocol_request = object
            .get("protocol_request")
            .filter(|request| request.is_object())
            .ok_or(RuntimeError::InvalidSubmission)?;
        let protocol_request =
            canonical(protocol_request).map_err(|_| RuntimeError::InvalidSubmission)?;
        let (context_id, caller_id, conversation_id) = decode_submission_bindings(object)?;
        Self::from_protocol_v2(
            original_source,
            context_id,
            caller_id,
            conversation_id,
            &protocol_request,
        )
    }
}

impl core::fmt::Debug for CompanionSubmission {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("CompanionSubmission(content=redacted)")
    }
}

#[derive(Clone, Eq, PartialEq)]
pub enum HelperReply {
    Authenticated {
        wire: Value,
        opened_connection: bool,
    },
    Rejected {
        code: Box<str>,
    },
}

impl core::fmt::Debug for HelperReply {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("HelperReply(content=redacted)")
    }
}

impl HelperReply {
    pub fn authenticated(wire: &[u8], opened_connection: bool) -> Result<Self> {
        let wire = nlu_data::parse_strict_json(wire, "authenticated helper reply")
            .map_err(|_| RuntimeError::InvalidHelperReply)?;
        if !wire.is_object() {
            return Err(RuntimeError::InvalidHelperReply);
        }
        Ok(Self::Authenticated {
            wire,
            opened_connection,
        })
    }

    pub fn rejected(code: &str) -> Result<Self> {
        if !valid_error_code(code) {
            return Err(RuntimeError::InvalidHelperReply);
        }
        Ok(Self::Rejected { code: code.into() })
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        let value = match self {
            Self::Authenticated {
                wire,
                opened_connection,
            } => json!({
                "kind": "authenticated_request",
                "opened_connection": opened_connection,
                "version": IPC_VERSION,
                "wire": wire,
            }),
            Self::Rejected { code } => json!({
                "code": code,
                "kind": "rejected",
                "version": IPC_VERSION,
            }),
        };
        canonical(&value).map_err(|_| RuntimeError::InvalidHelperReply)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self> {
        let value = nlu_data::parse_strict_json(bytes, "helper reply")
            .map_err(|_| RuntimeError::InvalidHelperReply)?;
        let object = value.as_object().ok_or(RuntimeError::InvalidHelperReply)?;
        if object.get("version").and_then(Value::as_u64) != Some(u64::from(IPC_VERSION)) {
            return Err(RuntimeError::InvalidHelperReply);
        }
        match object.get("kind").and_then(Value::as_str) {
            Some("authenticated_request") => {
                require_exact_fields(
                    object,
                    &["kind", "opened_connection", "version", "wire"],
                    RuntimeError::InvalidHelperReply,
                )?;
                let opened_connection = object
                    .get("opened_connection")
                    .and_then(Value::as_bool)
                    .ok_or(RuntimeError::InvalidHelperReply)?;
                let wire = object
                    .get("wire")
                    .filter(|wire| wire.is_object())
                    .cloned()
                    .ok_or(RuntimeError::InvalidHelperReply)?;
                Ok(Self::Authenticated {
                    wire,
                    opened_connection,
                })
            }
            Some("rejected") => {
                require_exact_fields(
                    object,
                    &["code", "kind", "version"],
                    RuntimeError::InvalidHelperReply,
                )?;
                let code = required_string(object, "code", RuntimeError::InvalidHelperReply)?;
                Self::rejected(code)
            }
            _ => Err(RuntimeError::InvalidHelperReply),
        }
    }

    pub fn authenticated_wire(&self) -> Result<Option<Vec<u8>>> {
        match self {
            Self::Authenticated { wire, .. } => canonical(wire)
                .map(Some)
                .map_err(|_| RuntimeError::InvalidHelperReply),
            Self::Rejected { .. } => Ok(None),
        }
    }
}

pub fn read_submission(reader: &mut impl Read) -> Result<CompanionSubmission> {
    let frame = nlu_server::read_frame(reader).map_err(|_| RuntimeError::IpcIo)?;
    CompanionSubmission::decode(&frame)
}

pub fn write_submission(writer: &mut impl Write, submission: &CompanionSubmission) -> Result<()> {
    let frame = submission.encode()?;
    nlu_server::write_frame(writer, &frame).map_err(|_| RuntimeError::IpcIo)
}

pub fn read_helper_reply(reader: &mut impl Read) -> Result<HelperReply> {
    let frame = nlu_server::read_frame(reader).map_err(|_| RuntimeError::IpcIo)?;
    HelperReply::decode(&frame)
}

pub fn write_helper_reply(writer: &mut impl Write, reply: &HelperReply) -> Result<()> {
    let frame = reply.encode()?;
    nlu_server::write_frame(writer, &frame).map_err(|_| RuntimeError::IpcIo)
}

fn canonical(value: &Value) -> nlu_data::Result<Vec<u8>> {
    nlu_data::canonical_json(value, "addon runtime IPC")
}

fn require_exact_fields(
    object: &Map<String, Value>,
    fields: &[&str],
    error: RuntimeError,
) -> Result<()> {
    if object.len() != fields.len() || object.keys().any(|field| !fields.contains(&field.as_str()))
    {
        return Err(error);
    }
    Ok(())
}

fn has_exact_fields(object: &Map<String, Value>, fields: &[&str]) -> bool {
    object.len() == fields.len() && object.keys().all(|field| fields.contains(&field.as_str()))
}

fn decode_submission_bindings(
    object: &Map<String, Value>,
) -> Result<(String, String, Option<String>)> {
    let context_id =
        required_string(object, "context_id", RuntimeError::InvalidSubmission)?.to_owned();
    let caller_id =
        required_string(object, "caller_id", RuntimeError::InvalidSubmission)?.to_owned();
    let conversation_id = match object.get("conversation_id") {
        Some(Value::Null) => None,
        Some(Value::String(value)) => Some(value.clone()),
        _ => return Err(RuntimeError::InvalidSubmission),
    };
    Ok((context_id, caller_id, conversation_id))
}

fn required_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
    error: RuntimeError,
) -> Result<&'a str> {
    object.get(field).and_then(Value::as_str).ok_or(error)
}

fn valid_context_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.is_ascii()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn valid_error_code(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
}
