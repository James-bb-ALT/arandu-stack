use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DataErrorCode {
    InvalidArguments,
    InvalidManifest,
    InvalidPath,
    InvalidJson,
    DuplicateJsonKey,
    ResourceLimit,
    IntegrityMismatch,
    InvalidRecord,
    SplitLeakage,
    OutputExists,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DataError {
    code: DataErrorCode,
    context: String,
}

impl DataError {
    pub(crate) fn new(code: DataErrorCode, context: impl Into<String>) -> Self {
        let mut context = context.into();
        context.truncate(512);
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> DataErrorCode {
        self.code
    }

    #[must_use]
    pub fn context(&self) -> &str {
        &self.context
    }
}

impl fmt::Display for DataError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.context)
    }
}

impl std::error::Error for DataError {}

pub type Result<T> = core::result::Result<T, DataError>;
