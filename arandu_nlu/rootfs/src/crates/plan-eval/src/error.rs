use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PlanEvaluationErrorCode {
    InvalidArguments,
    InvalidProjection,
    InvalidDataset,
    InvalidCatalog,
    EngineFailure,
    Reconciliation,
    ResourceLimit,
    OutputFailure,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PlanEvaluationError {
    code: PlanEvaluationErrorCode,
    context: String,
}

impl PlanEvaluationError {
    pub(crate) fn new(code: PlanEvaluationErrorCode, context: impl Into<String>) -> Self {
        let mut context = context.into();
        context.truncate(512);
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> PlanEvaluationErrorCode {
        self.code
    }

    #[must_use]
    pub fn context(&self) -> &str {
        &self.context
    }
}

impl fmt::Display for PlanEvaluationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.context)
    }
}

impl std::error::Error for PlanEvaluationError {}

pub(crate) fn invalid_arguments(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::InvalidArguments, context)
}

pub(crate) fn invalid_projection(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::InvalidProjection, context)
}

pub(crate) fn invalid_dataset(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::InvalidDataset, context)
}

pub(crate) fn invalid_catalog(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::InvalidCatalog, context)
}

pub(crate) fn engine_failure(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::EngineFailure, context)
}

pub(crate) fn reconciliation_error(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::Reconciliation, context)
}

pub(crate) fn resource_limit(context: impl Into<String>) -> PlanEvaluationError {
    PlanEvaluationError::new(PlanEvaluationErrorCode::ResourceLimit, context)
}

pub(crate) fn output_error(context: &str, error: std::io::Error) -> PlanEvaluationError {
    PlanEvaluationError::new(
        PlanEvaluationErrorCode::OutputFailure,
        format!("{context}: {:?}", error.kind()),
    )
}
