use std::{
    collections::{BTreeMap, BTreeSet},
    time::{Duration, Instant},
};

#[cfg(target_os = "linux")]
use std::fs;

use nlu_data::{canonical_json, sha256_hex};
use serde::Serialize;

use crate::{
    Result,
    error::{invalid_arguments, reconciliation, resource_limit},
    frozen::{
        CLAIM_SCOPE, CORPUS_VERSION, DIMENSIONS, MANIFEST_SHA256, P09_SHA256, P11_SHA256,
        PERFORMANCE_SHA256, SOURCE_ID, SPECIFICATION_SHA256,
    },
    runtime::{
        BenchmarkBoundary, PreflightProfile, StratumKey, TimedCycle, WorkloadSignature,
        prepare_corpus, timed_complete_cycle,
    },
    schema::{
        ArtifactSizeInput, BenchmarkCorpusIdentity, BenchmarkIdentityInput,
        BenchmarkIdentityReport, BenchmarkPercentiles, BenchmarkReport, BenchmarkSummary,
        BoundaryBenchmark, EvaluationCase, ExternalE2EStatus, RawBenchmarkSample, RunnerBinding,
        StratumBenchmark, StratumPreflight,
    },
    word_count::{UNICODE_VERSION, WORD_COUNTER_ID},
};

pub const WARMUP_RUNS: u32 = 3;
pub const MEASURED_RUNS: u32 = 5;

const RUNNER_ID: &str = "p15-release-benchmark-v2";
const RUNNER_SCHEMA_ID: &str = "p15-release-benchmark-input-v2";
const REPORT_SCHEMA_ID: &str = "p15-release-benchmark-report-v2";
const BENCHMARK_SPECIFICATION: &str = "p15-complete-performance-corpus-unicode-words-strata-v2";
const MINIMUM_SAMPLE_ELAPSED_NS: u64 = 1_000_000_000;
const MAXIMUM_COMPLETE_CORPUS_CYCLES: u32 = 64;
const CORE_WORDS_PER_SECOND_THRESHOLD_MILLI: u64 = 20_000_000;
const EXTERNAL_E2E_UTTERANCES_PER_SECOND_THRESHOLD_MILLI: u64 = 400_000;
const MAX_ARTIFACT_INPUTS: usize = 64;
const MAX_ARTIFACT_LABEL_BYTES: usize = 64;
const MAX_IDENTITY_BYTES: usize = 256;

#[derive(Clone, Copy)]
struct BoundaryContract {
    boundary: BenchmarkBoundary,
    boundary_id: &'static str,
    definition: &'static str,
    release_gate: &'static str,
    threshold_metric: Option<&'static str>,
    threshold_value_milli: Option<u64>,
}

struct MeasuredSample {
    global: RawBenchmarkSample,
    strata: BTreeMap<StratumKey, RawBenchmarkSample>,
}

#[derive(Default)]
struct StratumSampleAccumulator {
    elapsed: Duration,
    cycle_signatures: Vec<String>,
}

pub(crate) fn run(
    cases: &[EvaluationCase],
    preflight: &PreflightProfile,
    semantic_preflight_sha256: String,
    semantic_preflight_reconciled: bool,
    artifacts: &[ArtifactSizeInput],
    identity_input: &BenchmarkIdentityInput,
) -> Result<BenchmarkReport> {
    validate_preflight(cases, preflight)?;
    let artifact_sizes = validate_artifacts(artifacts)?;
    let artifact_size_total = artifact_sizes.values().try_fold(0_u64, |sum, value| {
        sum.checked_add(*value)
            .ok_or_else(|| resource_limit("artifact size total"))
    })?;
    let (identity, runner) = validate_identity(identity_input)?;
    let single_thread_asserted = identity.single_thread_asserted;
    let identity_gate_eligible = identity.complete
        && single_thread_asserted
        && runner.complete
        && semantic_preflight_reconciled;

    let startup_started = Instant::now();
    let startup = prepare_corpus(cases)?;
    let startup_ns = duration_ns(startup_started.elapsed())?;
    drop(startup);

    let core = measure_boundary(
        cases,
        preflight,
        core_boundary_contract(),
        identity_gate_eligible,
        single_thread_asserted,
    )?;
    let protocol_runtime = measure_boundary(
        cases,
        preflight,
        protocol_runtime_boundary_contract(),
        false,
        single_thread_asserted,
    )?;
    let external_e2e = external_e2e_status();

    let mut projection_sha256 = BTreeMap::new();
    projection_sha256.insert("p09_pre_resolution", P09_SHA256);
    projection_sha256.insert("p11_semantic_plan", P11_SHA256);

    Ok(BenchmarkReport {
        schema_version: 2,
        benchmark_specification: BENCHMARK_SPECIFICATION,
        semantic_preflight_sha256,
        semantic_preflight_reconciled,
        identity,
        runner,
        corpus: BenchmarkCorpusIdentity {
            source_id: SOURCE_ID,
            corpus_version: CORPUS_VERSION,
            claim_scope: CLAIM_SCOPE,
            split: "performance",
            split_sha256: PERFORMANCE_SHA256,
            manifest_sha256: MANIFEST_SHA256,
            specification_sha256: SPECIFICATION_SHA256,
            projection_sha256,
            utterances_per_cycle: preflight.global.semantic.records,
            words_per_cycle: preflight.global.words,
            word_counter_id: WORD_COUNTER_ID,
            unicode_version: UNICODE_VERSION,
            frozen_dimensions: DIMENSIONS.to_vec(),
        },
        complete_release_gate_eligible: core.release_gate_eligible
            && external_e2e.release_gate_eligible,
        startup_ns,
        peak_rss_bytes: peak_rss_bytes(),
        artifact_sizes,
        artifact_size_total,
        boundaries: vec![core, protocol_runtime],
        external_e2e,
    })
}

fn core_boundary_contract() -> BoundaryContract {
    BoundaryContract {
        boundary: BenchmarkBoundary::Core,
        boundary_id: "core",
        definition: "preencoded_protocol_v2_request_dispatch_at_response_decode",
        release_gate: "nfr_core_unicode_words_per_second",
        threshold_metric: Some("median_eligible_unicode_words_per_second_milli"),
        threshold_value_milli: Some(CORE_WORDS_PER_SECOND_THRESHOLD_MILLI),
    }
}

fn protocol_runtime_boundary_contract() -> BoundaryContract {
    BoundaryContract {
        boundary: BenchmarkBoundary::ProtocolRuntime,
        boundary_id: "protocol_runtime",
        definition: "request_text_protocol_v2_encode_dispatch_at_response_decode",
        release_gate: "none_diagnostic_only_not_external_e2e",
        threshold_metric: None,
        threshold_value_milli: None,
    }
}

fn external_e2e_status() -> ExternalE2EStatus {
    ExternalE2EStatus {
        boundary_id: "rust_adapter_python_companion_deterministic_ha_fixture",
        required_components: vec![
            "rust_adapter",
            "python_companion",
            "deterministic_home_assistant_fixture",
        ],
        status: "unmeasured_insufficient",
        release_gate_eligible: false,
        reason: "external_harness_samples_not_supplied",
        required_warmups: WARMUP_RUNS,
        required_measured_runs: MEASURED_RUNS,
        required_metric: "median_eligible_utterances_per_second_milli",
        required_threshold_value_milli: EXTERNAL_E2E_UTTERANCES_PER_SECOND_THRESHOLD_MILLI,
    }
}

fn measure_boundary(
    cases: &[EvaluationCase],
    preflight: &PreflightProfile,
    contract: BoundaryContract,
    identity_gate_eligible: bool,
    single_thread_asserted: bool,
) -> Result<BoundaryBenchmark> {
    for _ in 0..WARMUP_RUNS {
        measure_sample(cases, preflight, contract.boundary, 0)?;
    }

    let mut raw_samples = Vec::with_capacity(MEASURED_RUNS as usize);
    let mut stratum_samples = preflight
        .strata
        .keys()
        .cloned()
        .map(|key| (key, Vec::with_capacity(MEASURED_RUNS as usize)))
        .collect::<BTreeMap<_, _>>();
    for run in 1..=MEASURED_RUNS {
        let sample = measure_sample(cases, preflight, contract.boundary, run)?;
        raw_samples.push(sample.global);
        if sample.strata.keys().ne(stratum_samples.keys()) {
            return Err(reconciliation("benchmark measured strata"));
        }
        for (key, raw) in sample.strata {
            stratum_samples
                .get_mut(&key)
                .ok_or_else(|| reconciliation("benchmark stratum sample"))?
                .push(raw);
        }
    }
    if raw_samples.len() != MEASURED_RUNS as usize {
        return Err(reconciliation("benchmark sample count"));
    }

    let global_summary = summarize(&raw_samples)?;
    let samples_meet_duration_floor = raw_samples
        .iter()
        .all(|sample| sample.elapsed_ns >= MINIMUM_SAMPLE_ELAPSED_NS);
    let all_preflight_exact = preflight.global.exact_semantic_pass()
        && preflight
            .strata
            .values()
            .all(|signature| signature.exact_semantic_pass());
    let is_release_gate = contract.threshold_value_milli.is_some();
    let release_gate_eligible = is_release_gate
        && identity_gate_eligible
        && samples_meet_duration_floor
        && all_preflight_exact;
    let threshold_met = if release_gate_eligible {
        Some(
            global_summary.median_words_per_second_milli
                >= contract
                    .threshold_value_milli
                    .ok_or_else(|| reconciliation("benchmark threshold"))?,
        )
    } else {
        None
    };

    let mut strata = BTreeMap::<String, Vec<StratumBenchmark>>::new();
    let mut every_stratum_eligible = true;
    let mut every_stratum_threshold_met = true;
    for (key, signature) in &preflight.strata {
        let samples = stratum_samples
            .remove(key)
            .ok_or_else(|| reconciliation("benchmark missing stratum samples"))?;
        let summary = summarize(&samples)?;
        let stratum_eligible = is_release_gate
            && identity_gate_eligible
            && samples_meet_duration_floor
            && signature.exact_semantic_pass();
        let stratum_threshold_met = if stratum_eligible {
            Some(
                summary.median_words_per_second_milli
                    >= contract
                        .threshold_value_milli
                        .ok_or_else(|| reconciliation("benchmark stratum threshold"))?,
            )
        } else {
            None
        };
        every_stratum_eligible &= stratum_eligible;
        every_stratum_threshold_met &= stratum_threshold_met.unwrap_or(false);
        strata
            .entry(key.dimension.clone())
            .or_default()
            .push(StratumBenchmark {
                value: key.value.clone(),
                preflight: StratumPreflight {
                    records: signature.semantic.records,
                    words: signature.words,
                    eligible_utterances: signature.eligible_utterances,
                    eligible_words: signature.eligible_words,
                    semantic_signature_sha256: semantic_signature_sha256(signature)?,
                    exact_semantic_pass: signature.exact_semantic_pass(),
                },
                release_gate_eligible: stratum_eligible,
                threshold_met: stratum_threshold_met,
                raw_samples: samples,
                summary,
            });
    }
    if !stratum_samples.is_empty() {
        return Err(reconciliation("benchmark extra stratum samples"));
    }
    for values in strata.values_mut() {
        values.sort_by(|left, right| left.value.cmp(&right.value));
    }
    let all_strata_threshold_met = if is_release_gate && every_stratum_eligible {
        Some(every_stratum_threshold_met)
    } else {
        None
    };

    Ok(BoundaryBenchmark {
        boundary_id: contract.boundary_id,
        definition: contract.definition,
        release_gate: contract.release_gate,
        release_gate_eligible,
        threshold_metric: contract.threshold_metric,
        threshold_value_milli: contract.threshold_value_milli,
        threshold_met,
        all_strata_threshold_met,
        single_thread_asserted,
        warmups: WARMUP_RUNS,
        measured_runs: MEASURED_RUNS,
        minimum_sample_elapsed_ns: MINIMUM_SAMPLE_ELAPSED_NS,
        maximum_complete_corpus_cycles: MAXIMUM_COMPLETE_CORPUS_CYCLES,
        preflight_semantic_signature_sha256: semantic_signature_sha256(&preflight.global)?,
        preflight_exact_semantic_pass: preflight.global.exact_semantic_pass(),
        raw_samples,
        summary: global_summary,
        strata,
    })
}

fn measure_sample(
    cases: &[EvaluationCase],
    preflight: &PreflightProfile,
    boundary: BenchmarkBoundary,
    run: u32,
) -> Result<MeasuredSample> {
    let prepared = prepare_corpus(cases)?;
    let mut elapsed = Duration::ZERO;
    let mut completed_cycles = 0_u32;
    let mut cycle_signatures = Vec::new();
    let mut strata = preflight
        .strata
        .keys()
        .cloned()
        .map(|key| (key, StratumSampleAccumulator::default()))
        .collect::<BTreeMap<_, _>>();

    loop {
        let timed = timed_complete_cycle(&prepared, boundary, completed_cycles)?;
        reconcile_timed_cycle(preflight, &timed)?;
        completed_cycles = completed_cycles
            .checked_add(1)
            .ok_or_else(|| resource_limit("benchmark cycle count"))?;
        elapsed = elapsed
            .checked_add(timed.elapsed)
            .ok_or_else(|| resource_limit("benchmark elapsed duration"))?;
        cycle_signatures.push(semantic_signature_sha256(&timed.signature)?);
        for (key, timed_stratum) in timed.strata {
            let accumulator = strata
                .get_mut(&key)
                .ok_or_else(|| reconciliation("benchmark timed stratum"))?;
            accumulator.elapsed = accumulator
                .elapsed
                .checked_add(timed_stratum.elapsed)
                .ok_or_else(|| resource_limit("benchmark stratum elapsed duration"))?;
            accumulator
                .cycle_signatures
                .push(semantic_signature_sha256(&timed_stratum.signature)?);
        }
        if !requires_another_cycle(duration_ns(elapsed)?, completed_cycles)? {
            break;
        }
    }

    let global = raw_sample(
        run,
        elapsed,
        completed_cycles,
        preflight.global,
        cycle_signatures,
    )?;
    let strata = strata
        .into_iter()
        .map(|(key, accumulator)| {
            let signature = preflight
                .strata
                .get(&key)
                .copied()
                .ok_or_else(|| reconciliation("benchmark preflight stratum"))?;
            let raw = raw_sample(
                run,
                accumulator.elapsed,
                completed_cycles,
                signature,
                accumulator.cycle_signatures,
            )?;
            Ok((key, raw))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    Ok(MeasuredSample { global, strata })
}

fn reconcile_timed_cycle(preflight: &PreflightProfile, timed: &TimedCycle) -> Result<()> {
    if timed.signature != preflight.global
        || timed.strata.len() != preflight.strata.len()
        || timed.strata.keys().ne(preflight.strata.keys())
        || preflight.strata.iter().any(|(key, expected)| {
            timed
                .strata
                .get(key)
                .is_none_or(|observed| observed.signature != *expected)
        })
    {
        return Err(reconciliation("benchmark cycle semantic signature"));
    }
    Ok(())
}

fn raw_sample(
    run: u32,
    elapsed: Duration,
    complete_corpus_cycles: u32,
    signature: WorkloadSignature,
    cycle_semantic_signature_sha256: Vec<String>,
) -> Result<RawBenchmarkSample> {
    if complete_corpus_cycles == 0
        || cycle_semantic_signature_sha256.len() != complete_corpus_cycles as usize
    {
        return Err(reconciliation("benchmark raw cycle count"));
    }
    let elapsed_ns = duration_ns(elapsed)?;
    let cycles = u64::from(complete_corpus_cycles);
    let total_utterances = checked_product(
        signature.semantic.records,
        cycles,
        "benchmark total utterances",
    )?;
    let total_words = checked_product(signature.words, cycles, "benchmark total words")?;
    let eligible_utterances = checked_product(
        signature.eligible_utterances,
        cycles,
        "benchmark eligible utterances",
    )?;
    let eligible_words =
        checked_product(signature.eligible_words, cycles, "benchmark eligible words")?;
    Ok(RawBenchmarkSample {
        run,
        elapsed_ns,
        complete_corpus_cycles,
        utterances_per_cycle: signature.semantic.records,
        words_per_cycle: signature.words,
        total_utterances,
        total_words,
        eligible_utterances,
        eligible_words,
        words_per_second_milli: throughput_milli(eligible_words, elapsed_ns)?,
        utterances_per_second_milli: throughput_milli(eligible_utterances, elapsed_ns)?,
        cycle_semantic_signature_sha256,
    })
}

fn validate_preflight(cases: &[EvaluationCase], preflight: &PreflightProfile) -> Result<()> {
    let records =
        u64::try_from(cases.len()).map_err(|_| resource_limit("benchmark record count"))?;
    validate_workload_signature(preflight.global)?;
    if preflight.global.semantic.records != records || preflight.global.words == 0 {
        return Err(reconciliation("benchmark preflight denominator"));
    }
    let dimensions = preflight
        .strata
        .keys()
        .map(|key| key.dimension.as_str())
        .collect::<BTreeSet<_>>();
    if dimensions != DIMENSIONS.into_iter().collect::<BTreeSet<_>>() {
        return Err(reconciliation("benchmark preflight dimensions"));
    }
    for dimension in DIMENSIONS {
        let matching = preflight
            .strata
            .iter()
            .filter(|(key, _)| key.dimension == dimension)
            .map(|(_, signature)| *signature)
            .collect::<Vec<_>>();
        if matching.is_empty() {
            return Err(reconciliation("benchmark preflight stratum"));
        }
        let mut dimension_records = 0_u64;
        let mut dimension_words = 0_u64;
        for signature in matching {
            validate_workload_signature(signature)?;
            dimension_records = dimension_records
                .checked_add(signature.semantic.records)
                .ok_or_else(|| resource_limit("benchmark dimension records"))?;
            dimension_words = dimension_words
                .checked_add(signature.words)
                .ok_or_else(|| resource_limit("benchmark dimension words"))?;
        }
        if dimension_records != records || dimension_words != preflight.global.words {
            return Err(reconciliation("benchmark stratum denominator"));
        }
    }
    Ok(())
}

fn validate_workload_signature(signature: WorkloadSignature) -> Result<()> {
    if signature.semantic.records == 0
        || signature.words == 0
        || signature.eligible_utterances > signature.semantic.records
        || signature.eligible_words > signature.words
        || signature.semantic.exact_semantic_success != signature.eligible_utterances
    {
        return Err(reconciliation("benchmark workload signature"));
    }
    Ok(())
}

fn validate_identity(
    input: &BenchmarkIdentityInput,
) -> Result<(BenchmarkIdentityReport, RunnerBinding)> {
    let cpu_model = validate_identity_text(input.cpu_model.as_deref(), "benchmark CPU model")?;
    let architecture =
        validate_identity_text(input.architecture.as_deref(), "benchmark architecture")?;
    let core_allocation = validate_identity_text(
        input.core_allocation.as_deref(),
        "benchmark core allocation",
    )?;
    let operating_system = validate_identity_text(
        input.operating_system.as_deref(),
        "benchmark operating system",
    )?;
    let kernel = validate_identity_text(input.kernel.as_deref(), "benchmark kernel")?;
    let compiler = validate_identity_text(input.compiler.as_deref(), "benchmark compiler")?;
    let build_profile =
        validate_identity_text(input.build_profile.as_deref(), "benchmark build profile")?;
    if input.thread_count == Some(0) {
        return Err(invalid_arguments("benchmark thread count"));
    }

    let mut missing_fields = Vec::new();
    for (name, missing) in [
        ("cpu_model", cpu_model.is_none()),
        ("architecture", architecture.is_none()),
        ("core_allocation", core_allocation.is_none()),
        ("operating_system", operating_system.is_none()),
        ("kernel", kernel.is_none()),
        ("compiler", compiler.is_none()),
        ("build_profile", build_profile.is_none()),
        ("thread_count", input.thread_count.is_none()),
    ] {
        if missing {
            missing_fields.push(name);
        }
    }
    let single_thread_asserted = input.thread_count == Some(1);
    let identity = BenchmarkIdentityReport {
        cpu_model,
        architecture,
        core_allocation,
        operating_system,
        kernel,
        compiler,
        build_profile,
        thread_count: input.thread_count,
        single_thread_asserted,
        complete: missing_fields.is_empty(),
        missing_fields,
    };

    let executable_sha256 = validate_digest(
        input.runner_executable_sha256.as_deref(),
        &[64],
        "benchmark runner executable digest",
    )?;
    let source_commit = validate_digest(
        input.source_commit.as_deref(),
        &[40, 64],
        "benchmark source commit",
    )?;
    let source_tree = validate_digest(
        input.source_tree.as_deref(),
        &[40, 64],
        "benchmark source tree",
    )?;
    let mut runner_missing_fields = Vec::new();
    for (name, missing) in [
        ("executable_sha256", executable_sha256.is_none()),
        ("source_commit", source_commit.is_none()),
        ("source_tree", source_tree.is_none()),
    ] {
        if missing {
            runner_missing_fields.push(name);
        }
    }
    let runner = RunnerBinding {
        runner_id: RUNNER_ID,
        runner_schema_id: RUNNER_SCHEMA_ID,
        report_schema_id: REPORT_SCHEMA_ID,
        package_version: env!("CARGO_PKG_VERSION"),
        executable_sha256,
        source_commit,
        source_tree,
        complete: runner_missing_fields.is_empty(),
        missing_fields: runner_missing_fields,
    };
    Ok((identity, runner))
}

fn validate_identity_text(value: Option<&str>, context: &'static str) -> Result<Option<String>> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_empty()
        || value.len() > MAX_IDENTITY_BYTES
        || value.trim() != value
        || value.contains(['/', '\\'])
        || value.chars().any(char::is_control)
    {
        return Err(invalid_arguments(context));
    }
    Ok(Some(value.to_owned()))
}

fn validate_digest(
    value: Option<&str>,
    allowed_lengths: &[usize],
    context: &'static str,
) -> Result<Option<String>> {
    let Some(value) = value else {
        return Ok(None);
    };
    if !allowed_lengths.contains(&value.len())
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid_arguments(context));
    }
    Ok(Some(value.to_owned()))
}

fn validate_artifacts(inputs: &[ArtifactSizeInput]) -> Result<BTreeMap<String, u64>> {
    if inputs.len() > MAX_ARTIFACT_INPUTS {
        return Err(resource_limit("artifact input count"));
    }
    let mut values = BTreeMap::new();
    for input in inputs {
        if input.label.is_empty()
            || input.label.len() > MAX_ARTIFACT_LABEL_BYTES
            || !input.label.bytes().all(|byte| {
                byte.is_ascii_lowercase()
                    || byte.is_ascii_digit()
                    || matches!(byte, b'.' | b'_' | b'-')
            })
            || values.insert(input.label.clone(), input.bytes).is_some()
        {
            return Err(invalid_arguments("artifact size input"));
        }
    }
    Ok(values)
}

fn summarize(samples: &[RawBenchmarkSample]) -> Result<BenchmarkSummary> {
    if samples.len() != MEASURED_RUNS as usize {
        return Err(reconciliation("benchmark summary sample count"));
    }
    let elapsed = samples
        .iter()
        .map(|sample| sample.elapsed_ns)
        .collect::<Vec<_>>();
    let words_per_second = samples
        .iter()
        .map(|sample| sample.words_per_second_milli)
        .collect::<Vec<_>>();
    let utterances_per_second = samples
        .iter()
        .map(|sample| sample.utterances_per_second_milli)
        .collect::<Vec<_>>();
    Ok(BenchmarkSummary {
        elapsed_ns: BenchmarkPercentiles {
            p50_ns: nearest_rank(&elapsed, 50)?,
            p95_ns: nearest_rank(&elapsed, 95)?,
            p99_ns: nearest_rank(&elapsed, 99)?,
        },
        median_words_per_second_milli: nearest_rank(&words_per_second, 50)?,
        median_utterances_per_second_milli: nearest_rank(&utterances_per_second, 50)?,
    })
}

fn nearest_rank(values: &[u64], percentile: u64) -> Result<u64> {
    if values.is_empty() || percentile == 0 || percentile > 100 {
        return Err(reconciliation("benchmark percentile"));
    }
    let mut values = values.to_vec();
    values.sort_unstable();
    let rank = percentile
        .checked_mul(values.len() as u64)
        .ok_or_else(|| resource_limit("benchmark percentile rank"))?
        .saturating_add(99)
        / 100;
    let index = usize::try_from(rank.saturating_sub(1))
        .map_err(|_| resource_limit("benchmark percentile index"))?;
    values
        .get(index)
        .copied()
        .ok_or_else(|| reconciliation("benchmark percentile index"))
}

fn requires_another_cycle(elapsed_ns: u64, completed_cycles: u32) -> Result<bool> {
    if elapsed_ns >= MINIMUM_SAMPLE_ELAPSED_NS {
        return Ok(false);
    }
    if completed_cycles >= MAXIMUM_COMPLETE_CORPUS_CYCLES {
        return Err(resource_limit("benchmark duration floor cycles"));
    }
    Ok(true)
}

fn duration_ns(duration: Duration) -> Result<u64> {
    u64::try_from(duration.as_nanos()).map_err(|_| resource_limit("benchmark duration"))
}

fn checked_product(left: u64, right: u64, context: &'static str) -> Result<u64> {
    left.checked_mul(right)
        .ok_or_else(|| resource_limit(context))
}

fn throughput_milli(units: u64, elapsed_ns: u64) -> Result<u64> {
    if elapsed_ns == 0 {
        return Err(reconciliation("benchmark zero duration"));
    }
    let scaled = u128::from(units)
        .checked_mul(1_000_000_000_000)
        .ok_or_else(|| resource_limit("benchmark throughput"))?;
    u64::try_from(scaled.saturating_add(u128::from(elapsed_ns / 2)) / u128::from(elapsed_ns))
        .map_err(|_| resource_limit("benchmark throughput"))
}

fn semantic_signature_sha256<T: Serialize>(signature: &T) -> Result<String> {
    let value = serde_json::to_value(signature)
        .map_err(|_| reconciliation("benchmark semantic signature value"))?;
    let bytes = canonical_json(&value, "benchmark semantic signature")
        .map_err(|_| reconciliation("benchmark semantic signature encoding"))?;
    sha256_hex(&bytes).map_err(|_| reconciliation("benchmark semantic signature digest"))
}

#[cfg(target_os = "linux")]
fn peak_rss_bytes() -> Option<u64> {
    let contents = fs::read_to_string("/proc/self/status").ok()?;
    let kibibytes = contents.lines().find_map(|line| {
        let value = line.strip_prefix("VmHWM:")?.trim();
        let number = value.strip_suffix("kB")?.trim().parse::<u64>().ok()?;
        Some(number)
    })?;
    kibibytes.checked_mul(1_024)
}

#[cfg(not(target_os = "linux"))]
fn peak_rss_bytes() -> Option<u64> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::{RunSignature, TimedStratumCycle};

    fn complete_identity() -> BenchmarkIdentityInput {
        BenchmarkIdentityInput {
            cpu_model: Some("FIXTURE_TECNICA_CPU".to_owned()),
            architecture: Some("FIXTURE_TECNICA_ARCH".to_owned()),
            core_allocation: Some("FIXTURE_TECNICA_CORE_0".to_owned()),
            operating_system: Some("FIXTURE_TECNICA_OS".to_owned()),
            kernel: Some("FIXTURE_TECNICA_KERNEL".to_owned()),
            compiler: Some("FIXTURE_TECNICA_RUSTC".to_owned()),
            build_profile: Some("FIXTURE_TECNICA_RELEASE".to_owned()),
            thread_count: Some(1),
            runner_executable_sha256: Some("0".repeat(64)),
            source_commit: Some("1".repeat(40)),
            source_tree: Some("2".repeat(40)),
        }
    }

    fn exact_signature(records: u64, words: u64) -> WorkloadSignature {
        WorkloadSignature {
            semantic: RunSignature {
                records,
                plans: records,
                intent_exact: records,
                slot_exact: records,
                graph_exact: records,
                final_outcome_exact: records,
                exact_semantic_success: records,
                ..RunSignature::default()
            },
            words,
            eligible_utterances: records,
            eligible_words: words,
        }
    }

    fn sample(run: u32, elapsed_ns: u64, words_per_second_milli: u64) -> RawBenchmarkSample {
        RawBenchmarkSample {
            run,
            elapsed_ns,
            complete_corpus_cycles: 1,
            utterances_per_cycle: 1,
            words_per_cycle: 1,
            total_utterances: 1,
            total_words: 1,
            eligible_utterances: 1,
            eligible_words: 1,
            words_per_second_milli,
            utterances_per_second_milli: 1,
            cycle_semantic_signature_sha256: vec!["0".repeat(64)],
        }
    }

    #[test]
    fn benchmark_contract_is_exactly_three_plus_five() {
        assert_eq!(WARMUP_RUNS, 3);
        assert_eq!(MEASURED_RUNS, 5);
    }

    #[test]
    fn protocol_runtime_is_not_external_e2e_evidence() {
        let protocol = protocol_runtime_boundary_contract();
        let external = external_e2e_status();
        assert_eq!(protocol.boundary_id, "protocol_runtime");
        assert_eq!(
            protocol.release_gate,
            "none_diagnostic_only_not_external_e2e"
        );
        assert!(protocol.threshold_metric.is_none());
        assert_eq!(external.status, "unmeasured_insufficient");
        assert!(!external.release_gate_eligible);
        assert_ne!(protocol.boundary_id, external.boundary_id);
    }

    #[test]
    fn duration_floor_repeats_only_complete_cycles_and_is_bounded() {
        assert!(
            requires_another_cycle(MINIMUM_SAMPLE_ELAPSED_NS - 1, 1)
                .expect("FIXTURE_TECNICA repeat")
        );
        assert!(
            !requires_another_cycle(MINIMUM_SAMPLE_ELAPSED_NS, 1).expect("FIXTURE_TECNICA stop")
        );
        assert!(
            requires_another_cycle(
                MINIMUM_SAMPLE_ELAPSED_NS - 1,
                MAXIMUM_COMPLETE_CORPUS_CYCLES
            )
            .is_err()
        );
    }

    #[test]
    fn words_and_utterances_have_independent_integer_rates() {
        assert_eq!(
            throughput_milli(20_000, 1_000_000_000).expect("FIXTURE_TECNICA word throughput"),
            20_000_000
        );
        assert_eq!(
            throughput_milli(400, 1_000_000_000).expect("FIXTURE_TECNICA utterance throughput"),
            400_000
        );
    }

    #[test]
    fn summaries_use_five_raw_sample_medians_and_nearest_rank_tails() {
        let samples = [10_u64, 20, 30, 40, 50]
            .into_iter()
            .enumerate()
            .map(|(index, elapsed_ns)| sample(index as u32 + 1, elapsed_ns, elapsed_ns * 10))
            .collect::<Vec<_>>();
        let values = summarize(&samples).expect("FIXTURE_TECNICA summary");
        assert_eq!(values.elapsed_ns.p50_ns, 30);
        assert_eq!(values.elapsed_ns.p95_ns, 50);
        assert_eq!(values.elapsed_ns.p99_ns, 50);
        assert_eq!(values.median_words_per_second_milli, 300);
    }

    #[test]
    fn missing_or_non_single_thread_identity_is_ineligible() {
        let (missing, runner) =
            validate_identity(&BenchmarkIdentityInput::default()).expect("FIXTURE_TECNICA missing");
        assert!(!missing.complete);
        assert!(!missing.single_thread_asserted);
        assert!(!runner.complete);

        let mut multi = complete_identity();
        multi.thread_count = Some(2);
        let (multi, runner) =
            validate_identity(&multi).expect("FIXTURE_TECNICA multi thread identity");
        assert!(multi.complete);
        assert!(!multi.single_thread_asserted);
        assert!(runner.complete);
    }

    #[test]
    fn runner_bindings_require_exact_lowercase_hashes() {
        let (identity, runner) =
            validate_identity(&complete_identity()).expect("FIXTURE_TECNICA complete identity");
        assert!(identity.complete);
        assert!(identity.single_thread_asserted);
        assert!(runner.complete);

        let mut invalid = complete_identity();
        invalid.runner_executable_sha256 = Some("A".repeat(64));
        assert!(validate_identity(&invalid).is_err());
        invalid = complete_identity();
        invalid.source_tree = Some("0".repeat(39));
        assert!(validate_identity(&invalid).is_err());
    }

    #[test]
    fn identity_text_rejects_paths() {
        let mut invalid = complete_identity();
        invalid.kernel = Some("FIXTURE_TECNICA/path".to_owned());
        assert!(validate_identity(&invalid).is_err());
    }

    #[test]
    fn artifact_labels_cannot_encode_paths() {
        assert!(
            validate_artifacts(&[ArtifactSizeInput {
                label: "addon-amd64.oci".to_owned(),
                bytes: 1,
            }])
            .is_ok()
        );
        assert!(
            validate_artifacts(&[ArtifactSizeInput {
                label: "../FIXTURE_TECNICA".to_owned(),
                bytes: 1,
            }])
            .is_err()
        );
    }

    #[test]
    fn cycle_reconciliation_rejects_missing_strata() {
        let key = StratumKey {
            dimension: "FIXTURE_TECNICA_DIMENSION".to_owned(),
            value: "FIXTURE_TECNICA_VALUE".to_owned(),
        };
        let signature = exact_signature(1, 1);
        let preflight = PreflightProfile {
            global: signature,
            strata: BTreeMap::from([(key.clone(), signature)]),
        };
        let missing = TimedCycle {
            elapsed: Duration::from_secs(1),
            signature,
            strata: BTreeMap::new(),
        };
        assert!(reconcile_timed_cycle(&preflight, &missing).is_err());

        let complete = TimedCycle {
            elapsed: Duration::from_secs(1),
            signature,
            strata: BTreeMap::from([(
                key,
                TimedStratumCycle {
                    elapsed: Duration::from_secs(1),
                    signature,
                },
            )]),
        };
        assert!(reconcile_timed_cycle(&preflight, &complete).is_ok());
    }
}
