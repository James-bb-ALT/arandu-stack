use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PlanEngineErrorCode {
    InvalidStaticConfiguration,
    MatchSourceMismatch,
    TextProcessing,
    CatalogContract,
    CoreContract,
}

impl PlanEngineErrorCode {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidStaticConfiguration => "plan_invalid_static_configuration",
            Self::MatchSourceMismatch => "plan_match_source_mismatch",
            Self::TextProcessing => "plan_text_processing",
            Self::CatalogContract => "plan_catalog_contract",
            Self::CoreContract => "plan_core_contract",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PlanEngineError {
    code: PlanEngineErrorCode,
}

impl PlanEngineError {
    pub(crate) const fn new(code: PlanEngineErrorCode) -> Self {
        Self { code }
    }

    #[must_use]
    pub const fn code(self) -> PlanEngineErrorCode {
        self.code
    }
}

impl fmt::Display for PlanEngineError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code.code())
    }
}

impl std::error::Error for PlanEngineError {}

pub type Result<T> = core::result::Result<T, PlanEngineError>;
