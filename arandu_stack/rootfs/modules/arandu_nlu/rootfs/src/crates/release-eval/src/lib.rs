#![forbid(unsafe_code)]

mod benchmark;
mod error;
mod frozen;
mod metrics;
mod oracle;
mod runtime;
mod schema;
mod word_count;

use std::path::Path;

use nlu_data::{canonical_json, sha256_hex};
use serde::Serialize;

pub use benchmark::{MEASURED_RUNS, WARMUP_RUNS};
pub use error::{ReleaseEvalError, ReleaseEvalErrorCode};
pub use schema::{
    AggregateReport, ArtifactSizeInput, BenchmarkIdentityInput, BenchmarkReport, InputSummary,
    Metric, NegativeSuiteReport, Reconciliation, SemanticMetricSet, WilsonInterval,
};

pub type Result<T> = core::result::Result<T, ReleaseEvalError>;

const MAX_REPORT_BYTES: usize = 2 * 1024 * 1024;

pub fn validate_inputs(root: &Path) -> Result<InputSummary> {
    let heldout = frozen::load(root, schema::EvaluationSplit::Heldout)?;
    let performance = frozen::load(root, schema::EvaluationSplit::Performance)?;
    if heldout.rows.len() != frozen::RECORDS_PER_RELEASE_SPLIT
        || performance.rows.len() != frozen::RECORDS_PER_RELEASE_SPLIT
        || heldout.negatives.len() != 27
        || performance.negatives.len() != 27
    {
        return Err(error::reconciliation("input summary denominator"));
    }
    Ok(InputSummary {
        schema_version: 1,
        heldout_records: heldout.rows.len() as u64,
        performance_records: performance.rows.len() as u64,
        negative_records: heldout.negatives.len() as u64,
        frozen_dimensions: frozen::DIMENSIONS.to_vec(),
        hash_admission: "exact_known_sha256",
    })
}

pub fn evaluate(root: &Path) -> Result<AggregateReport> {
    evaluate_split(root, schema::EvaluationSplit::Heldout).map(|product| product.report)
}

pub fn semantic_preflight(root: &Path) -> Result<AggregateReport> {
    evaluate_split(root, schema::EvaluationSplit::Performance).map(|product| product.report)
}

pub fn benchmark(root: &Path, artifacts: &[ArtifactSizeInput]) -> Result<BenchmarkReport> {
    benchmark_with_identity(root, artifacts, &BenchmarkIdentityInput::default())
}

pub fn benchmark_with_identity(
    root: &Path,
    artifacts: &[ArtifactSizeInput],
    identity: &BenchmarkIdentityInput,
) -> Result<BenchmarkReport> {
    let inputs = frozen::load(root, schema::EvaluationSplit::Performance)?;
    let cases = oracle::project(
        &inputs.rows,
        schema::EvaluationSplit::Performance,
        &inputs.p09,
        &inputs.p11,
    )?;
    let positive = runtime::evaluate_positive(&cases)?;
    let negative_cases = frozen::negative_cases(&inputs.negatives)?;
    let negatives = runtime::evaluate_negatives(&negative_cases)?;
    let report = metrics::build_report(&inputs, &positive, &negatives)?;
    let preflight_bytes = canonical_report(&report)?;
    ensure_no_case_material(&preflight_bytes, &inputs.rows)?;
    let preflight_sha256 = sha256_hex(&preflight_bytes)
        .map_err(|_| error::output_error("semantic preflight digest"))?;
    let preflight = runtime::preflight_profile(&positive)?;
    let report = benchmark::run(
        &cases,
        &preflight,
        preflight_sha256,
        report.reconciliation.complete(),
        artifacts,
        identity,
    )?;
    let report_bytes = canonical_report(&report)?;
    ensure_no_case_material(&report_bytes, &inputs.rows)?;
    Ok(report)
}

struct EvaluationProduct {
    report: AggregateReport,
}

fn evaluate_split(root: &Path, split: schema::EvaluationSplit) -> Result<EvaluationProduct> {
    let inputs = frozen::load(root, split)?;
    let cases = oracle::project(&inputs.rows, split, &inputs.p09, &inputs.p11)?;
    let positive = runtime::evaluate_positive(&cases)?;
    let negative_cases = frozen::negative_cases(&inputs.negatives)?;
    let negatives = runtime::evaluate_negatives(&negative_cases)?;
    let report = metrics::build_report(&inputs, &positive, &negatives)?;
    let report_bytes = canonical_report(&report)?;
    ensure_no_case_material(&report_bytes, &inputs.rows)?;
    Ok(EvaluationProduct { report })
}

fn ensure_no_case_material(bytes: &[u8], rows: &[schema::P02Row]) -> Result<()> {
    for row in rows {
        for value in [&row.case_id, &row.utterance] {
            let encoded = serde_json::to_vec(value)
                .map_err(|_| error::output_error("case material encoding"))?;
            if contains_subslice(bytes, &encoded) {
                return Err(error::output_error("aggregate output leakage"));
            }
        }
    }
    Ok(())
}

fn contains_subslice(haystack: &[u8], needle: &[u8]) -> bool {
    !needle.is_empty()
        && haystack
            .windows(needle.len())
            .any(|window| window == needle)
}

pub fn canonical_report<T: Serialize>(report: &T) -> Result<Vec<u8>> {
    let value = serde_json::to_value(report).map_err(|_| error::output_error("report value"))?;
    let bytes = canonical_json(&value, "release aggregate report")
        .map_err(|_| error::output_error("report encoding"))?;
    if bytes.len() > MAX_REPORT_BYTES {
        return Err(error::resource_limit("report bytes"));
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use serde::Serialize;

    use super::*;

    #[derive(Serialize)]
    struct LeakageFixture<'a> {
        aggregate_label: &'a str,
    }

    #[test]
    fn serialized_value_leakage_detection_is_exact() {
        let secret = "FIXTURE_TECNICA_UTTERANCE_SECRET";
        let safe = canonical_report(&LeakageFixture {
            aggregate_label: "FIXTURE_TECNICA_AGGREGATE",
        })
        .expect("FIXTURE_TECNICA safe report");
        assert!(!contains_subslice(
            &safe,
            &serde_json::to_vec(secret).expect("FIXTURE_TECNICA secret encoding")
        ));

        let leaked = canonical_report(&LeakageFixture {
            aggregate_label: secret,
        })
        .expect("FIXTURE_TECNICA leaked report");
        assert!(contains_subslice(
            &leaked,
            &serde_json::to_vec(secret).expect("FIXTURE_TECNICA secret encoding")
        ));
    }
}
