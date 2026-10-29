#![forbid(unsafe_code)]

mod engine;
mod error;
mod outcome;

pub use engine::{
    INTENT_ALGORITHM_ID, INTENT_CONFIGURATION_ID, INTENT_MANIFEST_SHA256, INTENT_PACKAGE_SHA256,
    INTENT_SCHEMA_ID, IntentEngine,
};
pub use error::{IntentEngineError, IntentEngineErrorCode, Result};
pub use outcome::{
    INTENT_OUTPUT_SCHEMA_VERSION, IntentClarification, IntentMatch, IntentSlotValue,
    RecognitionAbstentionReason, RecognitionOutcome, SlotBinding,
};

pub(crate) const fn invalid_artifact(context: &'static str) -> IntentEngineError {
    IntentEngineError::new(IntentEngineErrorCode::InvalidArtifact, context)
}

pub(crate) const fn invalid_configuration(context: &'static str) -> IntentEngineError {
    IntentEngineError::new(IntentEngineErrorCode::InvalidConfiguration, context)
}

pub(crate) const fn invalid_text(context: &'static str) -> IntentEngineError {
    IntentEngineError::new(IntentEngineErrorCode::InvalidText, context)
}

pub(crate) const fn invalid_output(context: &'static str) -> IntentEngineError {
    IntentEngineError::new(IntentEngineErrorCode::InvalidOutput, context)
}

pub(crate) const fn resource_limit(context: &'static str) -> IntentEngineError {
    IntentEngineError::new(IntentEngineErrorCode::ResourceLimit, context)
}

pub(crate) const fn span_source_mismatch() -> IntentEngineError {
    IntentEngineError::new(
        IntentEngineErrorCode::SpanSourceMismatch,
        "output span source",
    )
}
