use core::fmt;

pub type Result<T> = core::result::Result<T, PackagerError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PackagerErrorCode {
    UnsafePath,
    DuplicatePath,
    PathConflict,
    UnsupportedEntryKind,
    InvalidMode,
    NondeterministicMetadata,
    ResourceLimit,
    InvalidIdentifier,
    InvalidArchitecture,
    ArchitectureMismatch,
    InvalidElf,
    ContradictoryAuthorization,
    ProductionGateDenied,
    DuplicateInput,
    InputDigestMismatch,
    UndeclaredInput,
    UnusedInput,
    InvalidByteRange,
    CoverageGap,
    CoverageOverlap,
    CopiedByteMismatch,
    InvalidSpdx,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PackagerError {
    code: PackagerErrorCode,
    message: String,
}

impl PackagerError {
    pub(crate) fn new(code: PackagerErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub const fn code(&self) -> PackagerErrorCode {
        self.code
    }

    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for PackagerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.message)
    }
}

impl std::error::Error for PackagerError {}
