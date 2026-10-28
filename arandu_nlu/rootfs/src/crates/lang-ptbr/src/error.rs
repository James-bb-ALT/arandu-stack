use core::fmt;
use nlu_core::CoreError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TextError {
    Core(CoreError),
    ScalarLimit { limit: u32 },
    GraphemeLimit { limit: u32 },
    TokenLimit { limit: u32 },
    NormalizedByteLimit { limit: u32 },
    ControlCodePoint { offset: u32 },
    ZeroWidthCodePoint { offset: u32 },
    OffsetOverflow,
    SpanReversed,
    SpanOutOfRange,
    SpanNotCharBoundary,
    SpanNotMappingBoundary,
    EmptySpan,
    SpanSourceMismatch,
    NormalizationInvariant,
    TokenizationInvariant,
}

impl TextError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Core(error) => error.code(),
            Self::ScalarLimit { .. } => "unicode_scalar_limit",
            Self::GraphemeLimit { .. } => "grapheme_cluster_limit",
            Self::TokenLimit { .. } => "token_count_limit",
            Self::NormalizedByteLimit { .. } => "normalized_byte_limit",
            Self::ControlCodePoint { .. } => "control_code_point",
            Self::ZeroWidthCodePoint { .. } => "zero_width_code_point",
            Self::OffsetOverflow => "normalized_offset_overflow",
            Self::SpanReversed => "normalized_span_reversed",
            Self::SpanOutOfRange => "normalized_span_out_of_range",
            Self::SpanNotCharBoundary => "normalized_span_not_char_boundary",
            Self::SpanNotMappingBoundary => "span_not_mapping_boundary",
            Self::EmptySpan => "normalized_span_empty",
            Self::SpanSourceMismatch => "normalized_span_source_mismatch",
            Self::NormalizationInvariant => "normalization_invariant",
            Self::TokenizationInvariant => "tokenization_invariant",
        }
    }
}

impl From<CoreError> for TextError {
    fn from(error: CoreError) -> Self {
        Self::Core(error)
    }
}

impl fmt::Display for TextError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for TextError {}
