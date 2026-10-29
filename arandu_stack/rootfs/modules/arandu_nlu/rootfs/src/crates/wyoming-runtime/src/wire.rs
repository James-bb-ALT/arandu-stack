use core::fmt;

use serde::Serialize;
use serde_json::{Map, Value};

use crate::json::{JsonLimits, parse_strict_json};
use crate::{Result, RuntimeError};

pub const WYOMING_VERSION: &str = "1.10.0";
pub const MAX_HEADER_BYTES: usize = 1_024;
pub const MAX_DATA_BYTES: usize = 8_192;
pub const MAX_FRAME_BYTES: usize = MAX_HEADER_BYTES + MAX_DATA_BYTES;
pub const MAX_BUFFERED_BYTES: usize = MAX_FRAME_BYTES * 2;
pub const MAX_JSON_DEPTH: usize = 8;
pub const MAX_JSON_COLLECTION_ITEMS: usize = 64;
pub const MAX_JSON_STRING_BYTES: usize = 4_096;
pub const MAX_RECOGNITION_TEXT_BYTES: usize = 4_096;
pub const MAX_CONTEXT_VALUE_BYTES: usize = 256;

const MAX_JSON_TOTAL_ITEMS: usize = 128;
const PROGRAM_NAME: &str = "local-nlu";
const PROGRAM_DESCRIPTION: &str = "Local deterministic PT-BR intent recognition";
const MODEL_NAME: &str = "local-nlu-pt-BR";
const MODEL_DESCRIPTION: &str = "Brazilian Portuguese intent recognition";
const ATTRIBUTION_NAME: &str = "Local NLU";
const ATTRIBUTION_URL: &str = "https://local-nlu.invalid/";
const PACKAGE_VERSION: &str = env!("CARGO_PKG_VERSION");
const STATE_QUERY_INTENT: &str = "HassGetState";
const STATE_QUERY_SLOT: &str = "name";

#[derive(Clone, Eq, PartialEq)]
pub struct RecognitionContext {
    conversation_id: String,
    device_id: Option<String>,
    satellite_id: Option<String>,
}

impl RecognitionContext {
    #[must_use]
    pub fn conversation_id(&self) -> &str {
        &self.conversation_id
    }

    #[must_use]
    pub fn device_id(&self) -> Option<&str> {
        self.device_id.as_deref()
    }

    #[must_use]
    pub fn satellite_id(&self) -> Option<&str> {
        self.satellite_id.as_deref()
    }
}

impl fmt::Debug for RecognitionContext {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RecognitionContext")
            .field("conversation_id_bytes", &self.conversation_id.len())
            .field("has_device_id", &self.device_id.is_some())
            .field("has_satellite_id", &self.satellite_id.is_some())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct RecognitionInput {
    text: String,
    context: RecognitionContext,
}

impl RecognitionInput {
    #[must_use]
    pub fn text(&self) -> &str {
        &self.text
    }

    #[must_use]
    pub const fn context(&self) -> &RecognitionContext {
        &self.context
    }
}

impl fmt::Debug for RecognitionInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RecognitionInput")
            .field("text_bytes", &self.text.len())
            .field("context", &self.context)
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RequestEvent {
    Describe,
    Transcript(RecognitionInput),
}

#[derive(Clone, Copy)]
enum HeaderEvent {
    Describe,
    Transcript { data_length: usize },
}

#[derive(Clone, Copy)]
enum DecoderState {
    Header,
    TranscriptData { data_length: usize },
}

pub struct WireDecoder {
    buffer: Vec<u8>,
    state: DecoderState,
    failed: bool,
}

impl WireDecoder {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            buffer: Vec::new(),
            state: DecoderState::Header,
            failed: false,
        }
    }

    pub fn push(&mut self, bytes: &[u8]) -> Result<Vec<RequestEvent>> {
        if self.failed {
            return Err(RuntimeError::DecoderFailed);
        }

        let buffered = self
            .buffer
            .len()
            .checked_add(bytes.len())
            .ok_or(RuntimeError::BufferLimit)?;
        if buffered > MAX_BUFFERED_BYTES {
            return self.fail(RuntimeError::BufferLimit);
        }
        self.buffer.extend_from_slice(bytes);

        match self.decode_available() {
            Ok(events) => Ok(events),
            Err(error) => self.fail(error),
        }
    }

    pub fn finish(&mut self) -> Result<()> {
        if self.failed {
            return Err(RuntimeError::DecoderFailed);
        }
        if self.is_idle() {
            return Ok(());
        }
        self.fail(RuntimeError::TruncatedFrame)
    }

    #[must_use]
    pub fn is_idle(&self) -> bool {
        matches!(self.state, DecoderState::Header) && self.buffer.is_empty()
    }

    fn decode_available(&mut self) -> Result<Vec<RequestEvent>> {
        let mut events = Vec::new();
        loop {
            match self.state {
                DecoderState::Header => {
                    let Some(newline) = self.buffer.iter().position(|byte| *byte == b'\n') else {
                        if self.buffer.len() >= MAX_HEADER_BYTES {
                            return Err(RuntimeError::HeaderTooLarge);
                        }
                        break;
                    };
                    let header_bytes =
                        newline.checked_add(1).ok_or(RuntimeError::HeaderTooLarge)?;
                    if header_bytes > MAX_HEADER_BYTES {
                        return Err(RuntimeError::HeaderTooLarge);
                    }

                    let mut line = take_prefix(&mut self.buffer, header_bytes);
                    let terminator = line.pop();
                    if terminator != Some(b'\n') || line.contains(&b'\r') || line.is_empty() {
                        return Err(RuntimeError::InvalidHeader);
                    }

                    match parse_header(&line)? {
                        HeaderEvent::Describe => events.push(RequestEvent::Describe),
                        HeaderEvent::Transcript { data_length } => {
                            self.state = DecoderState::TranscriptData { data_length };
                        }
                    }
                }
                DecoderState::TranscriptData { data_length } => {
                    if self.buffer.len() < data_length {
                        break;
                    }
                    let data = take_prefix(&mut self.buffer, data_length);
                    let input = parse_recognition_data(&data)?;
                    self.state = DecoderState::Header;
                    events.push(RequestEvent::Transcript(input));
                }
            }
        }
        Ok(events)
    }

    fn fail<T>(&mut self, error: RuntimeError) -> Result<T> {
        self.failed = true;
        self.buffer.clear();
        self.state = DecoderState::Header;
        Err(error)
    }
}

impl Default for WireDecoder {
    fn default() -> Self {
        Self::new()
    }
}

fn take_prefix(bytes: &mut Vec<u8>, length: usize) -> Vec<u8> {
    bytes.drain(..length).collect()
}

fn json_limits() -> JsonLimits {
    JsonLimits {
        max_depth: MAX_JSON_DEPTH,
        max_collection_items: MAX_JSON_COLLECTION_ITEMS,
        max_total_items: MAX_JSON_TOTAL_ITEMS,
        max_string_bytes: MAX_JSON_STRING_BYTES,
    }
}

fn parse_header(bytes: &[u8]) -> Result<HeaderEvent> {
    let value = parse_strict_json(bytes, json_limits())?;
    let object = value.as_object().ok_or(RuntimeError::InvalidHeader)?;

    if object.contains_key("payload_length") {
        return Err(RuntimeError::PayloadNotAllowed);
    }
    if object.contains_key("data") {
        return Err(RuntimeError::InlineData);
    }

    let event_type = required_string(object, "type", RuntimeError::InvalidHeader)?;
    let version = required_string(object, "version", RuntimeError::InvalidHeader)?;
    if version != WYOMING_VERSION {
        return Err(RuntimeError::UnsupportedVersion);
    }

    match event_type {
        "describe" => {
            require_exact_fields(object, &["type", "version"])?;
            Ok(HeaderEvent::Describe)
        }
        "transcript" => {
            require_exact_fields(object, &["type", "version", "data_length"])?;
            let length = object
                .get("data_length")
                .and_then(Value::as_u64)
                .and_then(|value| usize::try_from(value).ok())
                .ok_or(RuntimeError::InvalidDataLength)?;
            if length == 0 || length > MAX_DATA_BYTES {
                return Err(RuntimeError::InvalidDataLength);
            }
            let frame_length = MAX_HEADER_BYTES
                .checked_add(length)
                .ok_or(RuntimeError::FrameTooLarge)?;
            if frame_length > MAX_FRAME_BYTES {
                return Err(RuntimeError::FrameTooLarge);
            }
            Ok(HeaderEvent::Transcript {
                data_length: length,
            })
        }
        _ => Err(RuntimeError::UnsupportedEvent),
    }
}

fn parse_recognition_data(bytes: &[u8]) -> Result<RecognitionInput> {
    let value = parse_strict_json(bytes, json_limits())?;
    let object = value
        .as_object()
        .ok_or(RuntimeError::InvalidRecognitionData)?;
    require_exact_fields(object, &["text", "language", "context"])?;

    let text = required_string(object, "text", RuntimeError::InvalidRecognitionData)?;
    let language = required_string(object, "language", RuntimeError::InvalidRecognitionData)?;
    if language != "pt-BR" {
        return Err(RuntimeError::InvalidRecognitionData);
    }
    if text.len() > MAX_RECOGNITION_TEXT_BYTES {
        return Err(RuntimeError::JsonStringLimit);
    }
    if text.chars().all(char::is_whitespace) {
        return Err(RuntimeError::EmptyRecognitionText);
    }
    if text.chars().any(char::is_control) {
        return Err(RuntimeError::InvalidRecognitionData);
    }

    let context_object = object
        .get("context")
        .and_then(Value::as_object)
        .ok_or(RuntimeError::InvalidContext)?;
    if context_object.is_empty() || context_object.len() > 3 {
        return Err(RuntimeError::OpenFields);
    }
    for field in context_object.keys() {
        if !matches!(
            field.as_str(),
            "conversation_id" | "device_id" | "satellite_id"
        ) {
            return Err(RuntimeError::OpenFields);
        }
    }

    let conversation_id = required_context_value(context_object, "conversation_id")?;
    let device_id = optional_context_value(context_object, "device_id")?;
    let satellite_id = optional_context_value(context_object, "satellite_id")?;

    Ok(RecognitionInput {
        text: text.to_owned(),
        context: RecognitionContext {
            conversation_id: conversation_id.to_owned(),
            device_id: device_id.map(str::to_owned),
            satellite_id: satellite_id.map(str::to_owned),
        },
    })
}

fn required_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
    error: RuntimeError,
) -> Result<&'a str> {
    object.get(field).and_then(Value::as_str).ok_or(error)
}

fn require_exact_fields(object: &Map<String, Value>, expected: &[&str]) -> Result<()> {
    if object.len() != expected.len()
        || object
            .keys()
            .any(|field| !expected.contains(&field.as_str()))
    {
        return Err(RuntimeError::OpenFields);
    }
    Ok(())
}

fn required_context_value<'a>(object: &'a Map<String, Value>, field: &str) -> Result<&'a str> {
    let value = object
        .get(field)
        .and_then(Value::as_str)
        .ok_or(RuntimeError::InvalidContext)?;
    validate_context_value(value)?;
    Ok(value)
}

fn optional_context_value<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a str>> {
    object
        .get(field)
        .map(|value| {
            let value = value.as_str().ok_or(RuntimeError::InvalidContext)?;
            validate_context_value(value)?;
            Ok(value)
        })
        .transpose()
}

fn validate_context_value(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > MAX_CONTEXT_VALUE_BYTES
        || !value.is_ascii()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b':'))
    {
        return Err(RuntimeError::InvalidContext);
    }
    Ok(())
}

#[derive(Serialize)]
struct EmptyHeader<'a> {
    #[serde(rename = "type")]
    event_type: &'a str,
    version: &'static str,
}

#[derive(Serialize)]
struct DataHeader<'a> {
    #[serde(rename = "type")]
    event_type: &'a str,
    version: &'static str,
    data_length: usize,
}

#[derive(Clone, Copy, Serialize)]
struct Attribution<'a> {
    name: &'a str,
    url: &'a str,
}

#[derive(Serialize)]
struct IntentModel<'a> {
    name: &'a str,
    attribution: Attribution<'a>,
    installed: bool,
    description: &'a str,
    version: &'a str,
    languages: [&'a str; 1],
}

#[derive(Serialize)]
struct IntentProgram<'a> {
    name: &'a str,
    attribution: Attribution<'a>,
    installed: bool,
    description: &'a str,
    version: &'a str,
    models: [IntentModel<'a>; 1],
}

#[derive(Serialize)]
struct InfoData<'a> {
    asr: &'a [(); 0],
    tts: &'a [(); 0],
    handle: &'a [(); 0],
    intent: [IntentProgram<'a>; 1],
    wake: &'a [(); 0],
    mic: &'a [(); 0],
    snd: &'a [(); 0],
}

#[derive(Serialize)]
struct IntentEntity<'a> {
    name: &'static str,
    value: &'a str,
}

#[derive(Serialize)]
struct IntentData<'a> {
    name: &'static str,
    entities: [IntentEntity<'a>; 1],
}

pub(crate) fn encode_info() -> Result<Vec<u8>> {
    let attribution = Attribution {
        name: ATTRIBUTION_NAME,
        url: ATTRIBUTION_URL,
    };
    let info = InfoData {
        asr: &[],
        tts: &[],
        handle: &[],
        intent: [IntentProgram {
            name: PROGRAM_NAME,
            attribution,
            installed: true,
            description: PROGRAM_DESCRIPTION,
            version: PACKAGE_VERSION,
            models: [IntentModel {
                name: MODEL_NAME,
                attribution,
                installed: true,
                description: MODEL_DESCRIPTION,
                version: PACKAGE_VERSION,
                languages: ["pt-BR"],
            }],
        }],
        wake: &[],
        mic: &[],
        snd: &[],
    };
    encode_data_event("info", &info)
}

pub(crate) fn encode_not_recognized() -> Result<Vec<u8>> {
    encode_empty_event("not-recognized")
}

pub(crate) fn encode_recognized(names: &[String]) -> Result<Vec<u8>> {
    if names.is_empty() {
        return Err(RuntimeError::InvalidStateQuery);
    }

    let mut output = Vec::new();
    if names.len() > 1 {
        append_frame(&mut output, &encode_empty_event("intents-start")?)?;
    }
    for name in names {
        let intent = IntentData {
            name: STATE_QUERY_INTENT,
            entities: [IntentEntity {
                name: STATE_QUERY_SLOT,
                value: name,
            }],
        };
        append_frame(&mut output, &encode_data_event("intent", &intent)?)?;
    }
    if names.len() > 1 {
        append_frame(&mut output, &encode_empty_event("intents-stop")?)?;
    }
    Ok(output)
}

fn encode_empty_event(event_type: &str) -> Result<Vec<u8>> {
    let header = EmptyHeader {
        event_type,
        version: WYOMING_VERSION,
    };
    let mut output = serde_json::to_vec(&header).map_err(|_| RuntimeError::Encoding)?;
    output.push(b'\n');
    if output.len() > MAX_FRAME_BYTES {
        return Err(RuntimeError::FrameTooLarge);
    }
    Ok(output)
}

fn encode_data_event<T: Serialize>(event_type: &str, data: &T) -> Result<Vec<u8>> {
    let data = serde_json::to_vec(data).map_err(|_| RuntimeError::Encoding)?;
    if data.is_empty() || data.len() > MAX_DATA_BYTES {
        return Err(RuntimeError::FrameTooLarge);
    }
    let header = DataHeader {
        event_type,
        version: WYOMING_VERSION,
        data_length: data.len(),
    };
    let mut output = serde_json::to_vec(&header).map_err(|_| RuntimeError::Encoding)?;
    output.push(b'\n');
    output.extend_from_slice(&data);
    if output.len() > MAX_FRAME_BYTES {
        return Err(RuntimeError::FrameTooLarge);
    }
    Ok(output)
}

fn append_frame(output: &mut Vec<u8>, frame: &[u8]) -> Result<()> {
    output
        .len()
        .checked_add(frame.len())
        .ok_or(RuntimeError::FrameTooLarge)?;
    output.extend_from_slice(frame);
    Ok(())
}
