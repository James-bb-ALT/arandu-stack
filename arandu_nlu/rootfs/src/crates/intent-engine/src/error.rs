use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IntentEngineErrorCode {
    InvalidArtifact,
    InvalidConfiguration,
    InvalidText,
    InvalidOutput,
    ResourceLimit,
    SpanSourceMismatch,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntentEngineError {
    code: IntentEngineErrorCode,
    context: &'static str,
}

impl IntentEngineError {
    pub(crate) const fn new(code: IntentEngineErrorCode, context: &'static str) -> Self {
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> IntentEngineErrorCode {
        self.code
    }

    #[must_use]
    pub const fn context(&self) -> &'static str {
        self.context
    }
}

impl fmt::Display for IntentEngineError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.context)
    }
}

impl std::error::Error for IntentEngineError {}

pub type Result<T> = core::result::Result<T, IntentEngineError>;
