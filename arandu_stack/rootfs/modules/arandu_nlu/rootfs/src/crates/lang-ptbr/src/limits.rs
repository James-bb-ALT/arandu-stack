pub const MAX_UNICODE_SCALARS: usize = 32_768;
pub const MAX_GRAPHEME_CLUSTERS: usize = 16_384;
pub const MAX_TOKENS: usize = 4_096;
pub const MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN: usize = 32;

pub const UNICODE_VERSION: &str = "17.0.0";
pub const NORMALIZATION_FORM: &str = "NFC";
pub const WORD_COUNT_ALGORITHM: &str =
    "uax29-default-word-v1/unicode-17.0.0/unicode-segmentation-1.13.3";
