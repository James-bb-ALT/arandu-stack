#![forbid(unsafe_code)]

mod common;
mod trainer;

#[cfg(feature = "evaluator")]
mod evaluator;

pub use common::{PosEvaluationError, PosEvaluationErrorCode, Result};
pub use trainer::{
    MODEL_ALGORITHM_ID, MODEL_COMPILER_ID, MODEL_CONFIG_ID, MODEL_ID, TrainingArtifacts,
    TrainingSummary, compile_training, train_cli, train_to_directory,
};

#[cfg(feature = "evaluator")]
pub use evaluator::{EvaluationReport, evaluate, evaluate_cli, evaluate_report_bytes};
