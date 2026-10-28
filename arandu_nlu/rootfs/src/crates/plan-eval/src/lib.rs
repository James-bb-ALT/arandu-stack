#![forbid(unsafe_code)]

mod catalog;
mod engine_adapter;
mod error;
mod evaluator;
mod oracle;
mod schema;

pub use error::{PlanEvaluationError, PlanEvaluationErrorCode};
pub use evaluator::{
    EvaluationSplit, FractionMetric, PlanEvaluationReport, Reconciliation, evaluate, evaluate_cli,
    evaluate_to_path,
};

pub type Result<T> = core::result::Result<T, PlanEvaluationError>;
