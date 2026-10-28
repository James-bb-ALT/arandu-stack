use crate::{
    MAX_DECODED_STRING_BYTES, MAX_NESTING_DEPTH, MAX_NUMERIC_TOKEN_BYTES, MAX_STRUCTURAL_ITEMS,
    MAX_WIRE_BYTES,
};
use core::fmt;
use nlu_core::CoreError;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProtocolErrorCode {
    InvalidUtf8,
    InputTooLarge,
    StringTooLarge,
    NestingTooDeep,
    StructuralLimitExceeded,
    NumericTokenTooLong,
    NonIntegerNumber,
    MalformedJson,
    UnsupportedVersion,
    InvalidIdentifier,
    InvalidSpan,
    InvalidGraph,
    EmptyPlan,
    InvalidOutcome,
    EncodingFailure,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProtocolError {
    InvalidUtf8,
    InputTooLarge,
    StringTooLarge,
    NestingTooDeep,
    StructuralLimitExceeded,
    NumericTokenTooLong,
    NonIntegerNumber,
    MalformedJson,
    UnsupportedVersion,
    InvalidIdentifier,
    InvalidSpan,
    InvalidGraph,
    EmptyPlan,
    InvalidOutcome,
    EncodingFailure,
}

impl ProtocolError {
    #[must_use]
    pub const fn code(self) -> ProtocolErrorCode {
        match self {
            Self::InvalidUtf8 => ProtocolErrorCode::InvalidUtf8,
            Self::InputTooLarge => ProtocolErrorCode::InputTooLarge,
            Self::StringTooLarge => ProtocolErrorCode::StringTooLarge,
            Self::NestingTooDeep => ProtocolErrorCode::NestingTooDeep,
            Self::StructuralLimitExceeded => ProtocolErrorCode::StructuralLimitExceeded,
            Self::NumericTokenTooLong => ProtocolErrorCode::NumericTokenTooLong,
            Self::NonIntegerNumber => ProtocolErrorCode::NonIntegerNumber,
            Self::MalformedJson => ProtocolErrorCode::MalformedJson,
            Self::UnsupportedVersion => ProtocolErrorCode::UnsupportedVersion,
            Self::InvalidIdentifier => ProtocolErrorCode::InvalidIdentifier,
            Self::InvalidSpan => ProtocolErrorCode::InvalidSpan,
            Self::InvalidGraph => ProtocolErrorCode::InvalidGraph,
            Self::EmptyPlan => ProtocolErrorCode::EmptyPlan,
            Self::InvalidOutcome => ProtocolErrorCode::InvalidOutcome,
            Self::EncodingFailure => ProtocolErrorCode::EncodingFailure,
        }
    }

    #[must_use]
    pub const fn limit(self) -> Option<u32> {
        match self {
            Self::InputTooLarge => Some(MAX_WIRE_BYTES as u32),
            Self::StringTooLarge => Some(MAX_DECODED_STRING_BYTES as u32),
            Self::NestingTooDeep => Some(MAX_NESTING_DEPTH as u32),
            Self::StructuralLimitExceeded => Some(MAX_STRUCTURAL_ITEMS as u32),
            Self::NumericTokenTooLong => Some(MAX_NUMERIC_TOKEN_BYTES as u32),
            _ => None,
        }
    }

    pub(crate) fn from_wire(code: ProtocolErrorCode, limit: Option<u32>) -> Result<Self, Self> {
        let error = match code {
            ProtocolErrorCode::InvalidUtf8 => Self::InvalidUtf8,
            ProtocolErrorCode::InputTooLarge => Self::InputTooLarge,
            ProtocolErrorCode::StringTooLarge => Self::StringTooLarge,
            ProtocolErrorCode::NestingTooDeep => Self::NestingTooDeep,
            ProtocolErrorCode::StructuralLimitExceeded => Self::StructuralLimitExceeded,
            ProtocolErrorCode::NumericTokenTooLong => Self::NumericTokenTooLong,
            ProtocolErrorCode::NonIntegerNumber => Self::NonIntegerNumber,
            ProtocolErrorCode::MalformedJson => Self::MalformedJson,
            ProtocolErrorCode::UnsupportedVersion => Self::UnsupportedVersion,
            ProtocolErrorCode::InvalidIdentifier => Self::InvalidIdentifier,
            ProtocolErrorCode::InvalidSpan => Self::InvalidSpan,
            ProtocolErrorCode::InvalidGraph => Self::InvalidGraph,
            ProtocolErrorCode::EmptyPlan => Self::EmptyPlan,
            ProtocolErrorCode::InvalidOutcome => Self::InvalidOutcome,
            ProtocolErrorCode::EncodingFailure => Self::EncodingFailure,
        };
        if error.limit() == limit {
            Ok(error)
        } else {
            Err(Self::InvalidOutcome)
        }
    }
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let code = match self.code() {
            ProtocolErrorCode::InvalidUtf8 => "invalid_utf8",
            ProtocolErrorCode::InputTooLarge => "input_too_large",
            ProtocolErrorCode::StringTooLarge => "string_too_large",
            ProtocolErrorCode::NestingTooDeep => "nesting_too_deep",
            ProtocolErrorCode::StructuralLimitExceeded => "structural_limit_exceeded",
            ProtocolErrorCode::NumericTokenTooLong => "numeric_token_too_long",
            ProtocolErrorCode::NonIntegerNumber => "non_integer_number",
            ProtocolErrorCode::MalformedJson => "malformed_json",
            ProtocolErrorCode::UnsupportedVersion => "unsupported_version",
            ProtocolErrorCode::InvalidIdentifier => "invalid_identifier",
            ProtocolErrorCode::InvalidSpan => "invalid_span",
            ProtocolErrorCode::InvalidGraph => "invalid_graph",
            ProtocolErrorCode::EmptyPlan => "empty_plan",
            ProtocolErrorCode::InvalidOutcome => "invalid_outcome",
            ProtocolErrorCode::EncodingFailure => "encoding_failure",
        };
        formatter.write_str(code)
    }
}

impl std::error::Error for ProtocolError {}

impl From<CoreError> for ProtocolError {
    fn from(error: CoreError) -> Self {
        match error {
            CoreError::InvalidIdentifier => Self::InvalidIdentifier,
            CoreError::OffsetOverflow
            | CoreError::SpanReversed
            | CoreError::SpanOutOfRange
            | CoreError::SpanNotCharBoundary
            | CoreError::EmptySpan
            | CoreError::SpanSourceMismatch => Self::InvalidSpan,
            CoreError::EmptyCollection {
                kind: nlu_core::CollectionKind::PlanNodes,
            } => Self::EmptyPlan,
            CoreError::RequestTooLarge { .. } => Self::InputTooLarge,
            CoreError::CollectionTooLarge { .. } => Self::StructuralLimitExceeded,
            CoreError::Duplicate { .. }
            | CoreError::DanglingRelation
            | CoreError::SelfRelation
            | CoreError::RelationCycle
            | CoreError::SemanticPlan(_)
            | CoreError::InvalidCatalogGeneration
            | CoreError::StaleCatalogGeneration => Self::InvalidGraph,
            CoreError::InvalidUtf8
            | CoreError::ScoreOutOfRange
            | CoreError::EmptyCollection { .. } => Self::InvalidOutcome,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nlu_core::SemanticPlanErrorKind;

    #[test]
    fn semantic_plan_errors_map_to_invalid_graph() {
        assert_eq!(
            ProtocolError::from(CoreError::SemanticPlan(
                SemanticPlanErrorKind::ExecutionClassMismatch,
            )),
            ProtocolError::InvalidGraph,
        );
    }
}
