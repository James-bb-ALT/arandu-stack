use core::fmt;

pub type Result<T> = core::result::Result<T, RuntimeError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeError {
    BufferLimit,
    ConnectionClosed,
    ConnectionDeadline,
    DecoderFailed,
    DuplicateJsonKey,
    EmptyRecognitionText,
    Encoding,
    FrameTooLarge,
    HeaderTooLarge,
    InlineData,
    InvalidContext,
    InvalidDataLength,
    InvalidHeader,
    InvalidJson,
    InvalidRecognitionData,
    InvalidStateQuery,
    JsonCollectionLimit,
    JsonDepthLimit,
    JsonStringLimit,
    NoPendingRecognition,
    OpenFields,
    PayloadNotAllowed,
    RequestInFlight,
    RequestLimit,
    TimeOverflow,
    TimeRegressed,
    TooManyRecognizedIntents,
    TruncatedFrame,
    UnsupportedEvent,
    UnsupportedVersion,
    WrongRecognitionRequest,
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::BufferLimit => "wyoming buffered-byte limit exceeded",
            Self::ConnectionClosed => "wyoming connection is closed",
            Self::ConnectionDeadline => "wyoming connection deadline exceeded",
            Self::DecoderFailed => "wyoming decoder is in a failed state",
            Self::DuplicateJsonKey => "wyoming JSON contains a duplicate key",
            Self::EmptyRecognitionText => "wyoming recognition text is empty",
            Self::Encoding => "wyoming response encoding failed",
            Self::FrameTooLarge => "wyoming frame-byte limit exceeded",
            Self::HeaderTooLarge => "wyoming header-byte limit exceeded",
            Self::InlineData => "wyoming inline data is prohibited",
            Self::InvalidContext => "wyoming recognition context is invalid",
            Self::InvalidDataLength => "wyoming data length is invalid",
            Self::InvalidHeader => "wyoming event header is invalid",
            Self::InvalidJson => "wyoming JSON is invalid",
            Self::InvalidRecognitionData => "wyoming recognition data is invalid",
            Self::InvalidStateQuery => "wyoming revalidated state query is invalid",
            Self::JsonCollectionLimit => "wyoming JSON collection limit exceeded",
            Self::JsonDepthLimit => "wyoming JSON depth limit exceeded",
            Self::JsonStringLimit => "wyoming JSON string-byte limit exceeded",
            Self::NoPendingRecognition => "wyoming connection has no pending recognition",
            Self::OpenFields => "wyoming event contains an unknown field",
            Self::PayloadNotAllowed => "wyoming binary payloads are prohibited",
            Self::RequestInFlight => "wyoming recognition request is already in flight",
            Self::RequestLimit => "wyoming request-per-connection limit exceeded",
            Self::TimeOverflow => "wyoming monotonic deadline overflow",
            Self::TimeRegressed => "wyoming monotonic time regressed",
            Self::TooManyRecognizedIntents => "wyoming recognized-intent limit exceeded",
            Self::TruncatedFrame => "wyoming stream ended in a partial frame",
            Self::UnsupportedEvent => "wyoming event type is unsupported",
            Self::UnsupportedVersion => "wyoming protocol version is unsupported",
            Self::WrongRecognitionRequest => "wyoming completion is for another request",
        })
    }
}

impl std::error::Error for RuntimeError {}
