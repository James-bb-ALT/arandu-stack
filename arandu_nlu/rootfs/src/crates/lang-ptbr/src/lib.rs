#![forbid(unsafe_code)]

mod error;
mod lexicon;
mod limits;
mod morphology;
mod normalized;
mod pos;
mod tokenizer;

#[cfg(test)]
mod conformance_tests;

pub use error::TextError;
pub use lexicon::{LexicalAnalysis, LexicalCategory, LexicalLookup, Lexicon, LexiconError};
pub use limits::{
    MAX_GRAPHEME_CLUSTERS, MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN, MAX_TOKENS, MAX_UNICODE_SCALARS,
    NORMALIZATION_FORM, UNICODE_VERSION, WORD_COUNT_ALGORITHM,
};
pub use morphology::{
    LexicalEvidenceAnalysis, LexicalEvidenceSet, MORPHOLOGY_ANALYZER_ID, Morphology,
    MorphologyError, MorphologyLookup,
};
pub use normalized::{NormalizedSpan, NormalizedText};
pub use pos::{
    POS_BASELINE_ID, POS_MODEL_ID, POS_MODEL_PACKAGE_SHA256, POS_TAGGER_ID, PosCandidate, PosError,
    PosEvidenceKind, PosLookup, PosTag, PosTagger, PosTagging,
};
pub use tokenizer::{BoundaryOperation, LogicalPart, Token, TokenClass, TokenRule, Tokenization};
