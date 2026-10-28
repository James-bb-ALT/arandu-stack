use core::fmt;

pub type Result<T> = core::result::Result<T, IntentEvaluationError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IntentEvaluationErrorCode {
    InvalidArguments,
    InvalidSchema,
    InvalidDataset,
    InvalidProjection,
    Reconciliation,
    ResourceLimit,
    OutputFailure,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntentEvaluationError {
    code: IntentEvaluationErrorCode,
    context: String,
}

impl IntentEvaluationError {
    pub(crate) fn new(code: IntentEvaluationErrorCode, context: impl Into<String>) -> Self {
        let mut context = context.into();
        context.truncate(512);
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> IntentEvaluationErrorCode {
        self.code
    }

    #[must_use]
    pub fn context(&self) -> &str {
        &self.context
    }
}

impl fmt::Display for IntentEvaluationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.context)
    }
}

impl std::error::Error for IntentEvaluationError {}
