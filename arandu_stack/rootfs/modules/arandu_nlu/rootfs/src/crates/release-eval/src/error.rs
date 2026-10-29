use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReleaseEvalErrorCode {
    InvalidArguments,
    InputIo,
    InputTooLarge,
    HashMismatch,
    InvalidManifest,
    InvalidProjection,
    InvalidDataset,
    ResourceLimit,
    Runtime,
    Protocol,
    Reconciliation,
    Output,
}

impl ReleaseEvalErrorCode {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidArguments => "invalid_arguments",
            Self::InputIo => "input_io",
            Self::InputTooLarge => "input_too_large",
            Self::HashMismatch => "hash_mismatch",
            Self::InvalidManifest => "invalid_manifest",
            Self::InvalidProjection => "invalid_projection",
            Self::InvalidDataset => "invalid_dataset",
            Self::ResourceLimit => "resource_limit",
            Self::Runtime => "runtime",
            Self::Protocol => "protocol",
            Self::Reconciliation => "reconciliation",
            Self::Output => "output",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReleaseEvalError {
    code: ReleaseEvalErrorCode,
    context: &'static str,
}

impl ReleaseEvalError {
    #[must_use]
    pub const fn new(code: ReleaseEvalErrorCode, context: &'static str) -> Self {
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> ReleaseEvalErrorCode {
        self.code
    }

    #[must_use]
    pub const fn context(&self) -> &'static str {
        self.context
    }
}

impl fmt::Display for ReleaseEvalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code.code(), self.context)
    }
}

impl std::error::Error for ReleaseEvalError {}

pub(crate) fn invalid_arguments(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InvalidArguments, context)
}

pub(crate) fn input_io(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InputIo, context)
}

pub(crate) fn input_too_large(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InputTooLarge, context)
}

pub(crate) fn hash_mismatch(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::HashMismatch, context)
}

pub(crate) fn invalid_manifest(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InvalidManifest, context)
}

pub(crate) fn invalid_projection(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InvalidProjection, context)
}

pub(crate) fn invalid_dataset(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InvalidDataset, context)
}

pub(crate) fn resource_limit(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::ResourceLimit, context)
}

pub(crate) fn runtime_error(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::Runtime, context)
}

pub(crate) fn protocol_error(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::Protocol, context)
}

pub(crate) fn reconciliation(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::Reconciliation, context)
}

pub(crate) fn output_error(context: &'static str) -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::Output, context)
}
