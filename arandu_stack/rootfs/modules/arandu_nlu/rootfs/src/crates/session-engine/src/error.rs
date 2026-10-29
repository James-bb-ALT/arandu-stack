use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessionErrorCode {
    InvalidTtl,
    DeadlineOverflow,
    CapacityExceeded,
    DuplicateSession,
    InvalidPendingState,
    InvalidBinding,
    ClockRollback,
    LockPoisoned,
    PlanContract,
}

impl SessionErrorCode {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidTtl => "session_invalid_ttl",
            Self::DeadlineOverflow => "session_deadline_overflow",
            Self::CapacityExceeded => "session_capacity_exceeded",
            Self::DuplicateSession => "session_duplicate",
            Self::InvalidPendingState => "session_invalid_pending_state",
            Self::InvalidBinding => "session_invalid_binding",
            Self::ClockRollback => "session_clock_rollback",
            Self::LockPoisoned => "session_lock_poisoned",
            Self::PlanContract => "session_plan_contract",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionError {
    code: SessionErrorCode,
}

impl SessionError {
    pub(crate) const fn new(code: SessionErrorCode) -> Self {
        Self { code }
    }

    #[must_use]
    pub const fn code(self) -> SessionErrorCode {
        self.code
    }
}

impl From<SessionErrorCode> for SessionError {
    fn from(code: SessionErrorCode) -> Self {
        Self::new(code)
    }
}

impl fmt::Display for SessionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code.code())
    }
}

impl std::error::Error for SessionError {}

pub type Result<T> = core::result::Result<T, SessionError>;
