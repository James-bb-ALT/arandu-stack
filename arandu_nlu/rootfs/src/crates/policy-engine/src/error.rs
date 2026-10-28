use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PolicyErrorCode {
    InvalidGeneration,
    InvalidTtl,
    InvalidGraphClasses,
    InvalidSlotSchema,
    DuplicateDescriptor,
    IncompleteConfiguration,
    UnknownDescriptor,
    DescriptorContract,
    DeadlineOverflow,
    CapacityExceeded,
    ClockRollback,
    LockPoisoned,
    PlanContract,
}

impl PolicyErrorCode {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidGeneration => "policy_invalid_generation",
            Self::InvalidTtl => "policy_invalid_ttl",
            Self::InvalidGraphClasses => "policy_invalid_graph_classes",
            Self::InvalidSlotSchema => "policy_invalid_slot_schema",
            Self::DuplicateDescriptor => "policy_duplicate_descriptor",
            Self::IncompleteConfiguration => "policy_incomplete_configuration",
            Self::UnknownDescriptor => "policy_unknown_descriptor",
            Self::DescriptorContract => "policy_descriptor_contract",
            Self::DeadlineOverflow => "policy_deadline_overflow",
            Self::CapacityExceeded => "policy_capacity_exceeded",
            Self::ClockRollback => "policy_clock_rollback",
            Self::LockPoisoned => "policy_lock_poisoned",
            Self::PlanContract => "policy_plan_contract",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PolicyError {
    code: PolicyErrorCode,
}

impl PolicyError {
    pub(crate) const fn new(code: PolicyErrorCode) -> Self {
        Self { code }
    }

    #[must_use]
    pub const fn code(self) -> PolicyErrorCode {
        self.code
    }
}

impl From<PolicyErrorCode> for PolicyError {
    fn from(code: PolicyErrorCode) -> Self {
        Self::new(code)
    }
}

impl fmt::Display for PolicyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code.code())
    }
}

impl std::error::Error for PolicyError {}

pub type Result<T> = core::result::Result<T, PolicyError>;
