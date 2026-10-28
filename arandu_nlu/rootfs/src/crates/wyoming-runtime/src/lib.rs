#![forbid(unsafe_code)]

mod error;
mod json;
mod service;
mod wire;

pub use error::{Result, RuntimeError};
pub use service::{
    CONNECTION_LIFETIME_MILLIS, MAX_REQUESTS_PER_CONNECTION, MAX_REVALIDATED_INTENTS,
    MAX_STATE_QUERY_NAME_BYTES, MonotonicMillis, Outbound, RECOGNITION_DEADLINE_MILLIS,
    RecognitionCompletion, RecognitionRequest, RecognitionRequestId, RevalidatedRecognition,
    RevalidatedStateQuery, ServiceAction, ServiceConnection,
};
pub use wire::{
    MAX_BUFFERED_BYTES, MAX_CONTEXT_VALUE_BYTES, MAX_DATA_BYTES, MAX_FRAME_BYTES, MAX_HEADER_BYTES,
    MAX_JSON_COLLECTION_ITEMS, MAX_JSON_DEPTH, MAX_JSON_STRING_BYTES, MAX_RECOGNITION_TEXT_BYTES,
    RecognitionContext, RecognitionInput, RequestEvent, WYOMING_VERSION, WireDecoder,
};
