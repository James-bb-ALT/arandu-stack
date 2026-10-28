use std::collections::BTreeMap;

use crate::{
    Result,
    error::{reconciliation, resource_limit},
    frozen::{
        CLAIM_SCOPE, CORPUS_VERSION, DIMENSIONS, MANIFEST_SHA256, MINIMUM_SUPPORTED_STRATUM,
        P09_SHA256, P11_SHA256, SOURCE_ID, SPECIFICATION_SHA256,
    },
    runtime::{EvaluatedCase, NegativeEvaluation, PositiveEvaluation, RunSignature, SemanticFlags},
    schema::{
        AggregateReport, InsufficientStratum, MacroMetric, MacroMetricSet, Metric,
        NegativeSuiteReport, OutcomeMetrics, Reconciliation, SemanticMetricSet, SourceIdentity,
        StratumReport, WilsonInterval,
    },
};

const RUNNER_ID: &str = "p15-release-eval-v1";
const METRIC_SPECIFICATION: &str = "p15-aggregate-intent-slot-entity-graph-outcome-wilson-v1";
const LIMITATIONS: [&str; 6] = [
    "project_authored_internal_conformance_only",
    "not_independent_language_accuracy",
    "aggregate_only_no_case_level_output",
    "asr_noise_has_no_frozen_release_quota",
    "clarification_and_abstention_below_frozen_supported_quota",
    "policy_carried_complete_plans_are_semantically_scored_but_never_execution_authority",
];

#[derive(Clone, Copy, Debug, Default)]
struct MetricAccumulator {
    records: u64,
    intent_exact: u64,
    slot_exact: u64,
    entity_eligible: u64,
    entity_exact: u64,
    graph_exact: u64,
    final_outcome_exact: u64,
}

impl MetricAccumulator {
    fn observe(&mut self, flags: SemanticFlags) {
        self.records += 1;
        self.intent_exact += u64::from(flags.intent_exact);
        self.slot_exact += u64::from(flags.slot_exact);
        self.entity_eligible += u64::from(flags.entity_eligible);
        self.entity_exact += u64::from(flags.entity_eligible && flags.entity_exact);
        self.graph_exact += u64::from(flags.graph_exact);
        self.final_outcome_exact += u64::from(flags.final_outcome_exact);
    }

    fn metrics(self) -> SemanticMetricSet {
        SemanticMetricSet {
            intent_exact: correctness_metric(self.intent_exact, self.records),
            slot_exact: correctness_metric(self.slot_exact, self.records),
            entity_exact: correctness_metric(self.entity_exact, self.entity_eligible),
            graph_exact: correctness_metric(self.graph_exact, self.records),
            final_outcome_exact: correctness_metric(self.final_outcome_exact, self.records),
        }
    }
}

pub(crate) fn build_report(
    inputs: &crate::frozen::FrozenInputs,
    positive: &PositiveEvaluation,
    negatives: &NegativeEvaluation,
) -> Result<AggregateReport> {
    let expected_records =
        u64::try_from(inputs.rows.len()).map_err(|_| resource_limit("aggregate record count"))?;
    build_report_for_split(inputs.split, expected_records, positive, negatives)
}

fn build_report_for_split(
    split: crate::schema::EvaluationSplit,
    expected_records: u64,
    positive: &PositiveEvaluation,
    negatives: &NegativeEvaluation,
) -> Result<AggregateReport> {
    if positive.signature.records != expected_records
        || u64::try_from(positive.cases.len())
            .map_err(|_| resource_limit("aggregate observed record count"))?
            != expected_records
    {
        return Err(reconciliation("aggregate positive denominator"));
    }

    let overall = signature_metrics(positive.signature);
    let outcomes = outcome_metrics(positive.signature, negatives);
    let (dimensions, dimension_complete) = dimension_reports(&positive.cases, expected_records)?;
    let macro_by_dimension = dimensions
        .iter()
        .map(|(dimension, strata)| (dimension.clone(), macro_metrics(strata)))
        .collect();
    let negative_suites = negative_suite_reports(negatives);
    let negative_complete = negatives.total_records() == 27
        && negative_suites.len() == 5
        && negative_suites
            .iter()
            .all(|suite| suite.expected_records == suite.observed_records);
    let reconciliation = Reconciliation {
        expected_records,
        observed_records: positive.signature.records,
        dimension_denominators_complete: dimension_complete,
        negative_suite_denominators_complete: negative_complete,
        complete: positive.signature.records == expected_records
            && dimension_complete
            && negative_complete,
    };
    let mut projection_sha256 = BTreeMap::new();
    projection_sha256.insert("p09_pre_resolution", P09_SHA256);
    projection_sha256.insert("p11_semantic_plan", P11_SHA256);
    Ok(AggregateReport {
        schema_version: 1,
        runner_id: RUNNER_ID,
        metric_specification: METRIC_SPECIFICATION,
        split: split.code(),
        source: SourceIdentity {
            source_id: SOURCE_ID,
            corpus_version: CORPUS_VERSION,
            claim_scope: CLAIM_SCOPE,
            manifest_sha256: MANIFEST_SHA256,
            specification_sha256: SPECIFICATION_SHA256,
            projection_sha256,
            split_sha256: split.sha256(),
            records: expected_records,
        },
        reconciliation,
        overall,
        outcomes,
        dimensions,
        macro_by_dimension,
        negative_suites,
        insufficiently_evaluated: insufficient_strata(negatives),
        limitations: LIMITATIONS.to_vec(),
    })
}

fn signature_metrics(signature: RunSignature) -> SemanticMetricSet {
    SemanticMetricSet {
        intent_exact: correctness_metric(signature.intent_exact, signature.records),
        slot_exact: correctness_metric(signature.slot_exact, signature.records),
        entity_exact: correctness_metric(signature.entity_exact, signature.entity_eligible),
        graph_exact: correctness_metric(signature.graph_exact, signature.records),
        final_outcome_exact: correctness_metric(signature.final_outcome_exact, signature.records),
    }
}

fn outcome_metrics(signature: RunSignature, negatives: &NegativeEvaluation) -> OutcomeMetrics {
    let (clarification_exact, clarification_expected) = negatives.clarification_totals();
    let (abstention_exact, abstention_expected) = negatives.abstention_totals();
    OutcomeMetrics {
        plans: signature.plans,
        clarifications: signature.clarifications,
        abstentions_or_denials: signature.abstentions,
        errors: signature.errors,
        clarification_exact: correctness_metric(clarification_exact, clarification_expected),
        abstention_exact: correctness_metric(abstention_exact, abstention_expected),
        false_plan_rate: rate_metric(
            negatives.total_false_plans(),
            negatives.total_records(),
            MINIMUM_SUPPORTED_STRATUM,
        ),
    }
}

fn dimension_reports(
    cases: &[EvaluatedCase],
    expected_records: u64,
) -> Result<(BTreeMap<String, Vec<StratumReport>>, bool)> {
    let mut accumulators = BTreeMap::<String, BTreeMap<String, MetricAccumulator>>::new();
    for case in cases {
        for (dimension, value) in case.dimensions.frozen_values() {
            accumulators
                .entry(dimension.to_owned())
                .or_default()
                .entry(value.to_owned())
                .or_default()
                .observe(case.flags);
        }
    }
    let complete = accumulators.len() == DIMENSIONS.len()
        && DIMENSIONS.iter().all(|dimension| {
            accumulators.get(*dimension).is_some_and(|strata| {
                strata
                    .values()
                    .map(|accumulator| accumulator.records)
                    .sum::<u64>()
                    == expected_records
            })
        });
    let reports = accumulators
        .into_iter()
        .map(|(dimension, strata)| {
            let reports = strata
                .into_iter()
                .map(|(value, accumulator)| StratumReport {
                    value,
                    records: accumulator.records,
                    metrics: accumulator.metrics(),
                })
                .collect();
            (dimension, reports)
        })
        .collect();
    Ok((reports, complete))
}

fn macro_metrics(strata: &[StratumReport]) -> MacroMetricSet {
    MacroMetricSet {
        intent_exact: macro_metric(strata, |metrics| &metrics.intent_exact),
        slot_exact: macro_metric(strata, |metrics| &metrics.slot_exact),
        entity_exact: macro_metric(strata, |metrics| &metrics.entity_exact),
        graph_exact: macro_metric(strata, |metrics| &metrics.graph_exact),
        final_outcome_exact: macro_metric(strata, |metrics| &metrics.final_outcome_exact),
    }
}

fn macro_metric(
    strata: &[StratumReport],
    select: impl Fn(&SemanticMetricSet) -> &Metric,
) -> MacroMetric {
    let rates = strata
        .iter()
        .filter_map(|stratum| select(&stratum.metrics).rate_ppm)
        .collect::<Vec<_>>();
    let mean_rate_ppm = if rates.is_empty() {
        None
    } else {
        let total = rates.iter().map(|value| u128::from(*value)).sum::<u128>();
        let rounded = total.saturating_add(u128::try_from(rates.len() / 2).unwrap_or(0))
            / u128::try_from(rates.len()).unwrap_or(1);
        u64::try_from(rounded).ok()
    };
    MacroMetric {
        strata_included: rates.len() as u64,
        mean_rate_ppm,
    }
}

fn negative_suite_reports(negatives: &NegativeEvaluation) -> Vec<NegativeSuiteReport> {
    negatives
        .suites
        .iter()
        .map(|(suite, observation)| NegativeSuiteReport {
            suite: suite.clone(),
            expected_records: match suite.as_str() {
                "explicit_negative" => 7,
                "ambiguity" | "contradiction" | "safety_sensitive" | "stale_state" => 5,
                _ => 0,
            },
            observed_records: observation.records,
            false_plans: observation.false_plans,
            zero_false_plan_gate: if observation.false_plans == 0 {
                "pass"
            } else {
                "fail"
            },
        })
        .collect()
}

fn insufficient_strata(negatives: &NegativeEvaluation) -> Vec<InsufficientStratum> {
    let (_, clarification) = negatives.clarification_totals();
    let (_, abstention) = negatives.abstention_totals();
    let mut values = vec![
        InsufficientStratum {
            metric: "semantic_accuracy",
            dimension: "noise",
            value: "asr_noise",
            eligible_records: 0,
            required_records: MINIMUM_SUPPORTED_STRATUM,
            disposition: "insufficient_no_frozen_cases",
        },
        InsufficientStratum {
            metric: "clarification_exact",
            dimension: "outcome",
            value: "clarification",
            eligible_records: clarification,
            required_records: MINIMUM_SUPPORTED_STRATUM,
            disposition: "insufficient_below_frozen_quota",
        },
        InsufficientStratum {
            metric: "abstention_exact",
            dimension: "outcome",
            value: "abstention",
            eligible_records: abstention,
            required_records: MINIMUM_SUPPORTED_STRATUM,
            disposition: "insufficient_below_frozen_quota",
        },
    ];
    values.sort_by(|left, right| {
        (left.dimension, left.value, left.metric).cmp(&(right.dimension, right.value, right.metric))
    });
    values
}

fn correctness_metric(numerator: u64, denominator: u64) -> Metric {
    rate_metric(numerator, denominator, MINIMUM_SUPPORTED_STRATUM)
}

fn rate_metric(numerator: u64, denominator: u64, minimum: u64) -> Metric {
    let rate_ppm = ratio_ppm(numerator, denominator);
    Metric {
        numerator,
        denominator,
        rate_ppm,
        wilson_95: wilson_95(numerator, denominator),
        support: if denominator >= minimum {
            "sufficient"
        } else {
            "insufficient"
        },
        minimum_supported_denominator: minimum,
    }
}

fn ratio_ppm(numerator: u64, denominator: u64) -> Option<u64> {
    if denominator == 0 || numerator > denominator {
        return None;
    }
    let scaled = u128::from(numerator)
        .saturating_mul(1_000_000)
        .saturating_add(u128::from(denominator / 2))
        / u128::from(denominator);
    u64::try_from(scaled).ok()
}

fn wilson_95(successes: u64, total: u64) -> Option<WilsonInterval> {
    if total == 0 || successes > total {
        return None;
    }
    const Z: f64 = 1.959_963_984_540_054;
    let n = total as f64;
    let p = successes as f64 / n;
    let z2 = Z * Z;
    let denominator = 1.0 + z2 / n;
    let center = (p + z2 / (2.0 * n)) / denominator;
    let margin = Z * ((p * (1.0 - p) / n + z2 / (4.0 * n * n)).sqrt()) / denominator;
    Some(WilsonInterval {
        lower_ppm: probability_ppm((center - margin).clamp(0.0, 1.0)),
        upper_ppm: probability_ppm((center + margin).clamp(0.0, 1.0)),
    })
}

fn probability_ppm(value: f64) -> u64 {
    (value * 1_000_000.0).round() as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        runtime::{NegativeSuiteObservation, PositiveEvaluation},
        schema::{EvaluationSplit, P02Dimensions},
    };

    #[test]
    fn wilson_and_integer_rate_are_bounded() {
        assert_eq!(ratio_ppm(1, 2), Some(500_000));
        assert_eq!(ratio_ppm(0, 0), None);
        let all = wilson_95(240, 240).expect("FIXTURE_TECNICA interval");
        assert!(all.lower_ppm < 1_000_000);
        assert_eq!(all.upper_ppm, 1_000_000);
        let none = wilson_95(0, 240).expect("FIXTURE_TECNICA interval");
        assert_eq!(none.lower_ppm, 0);
        assert!(none.upper_ppm > 0);
    }

    #[test]
    fn required_missing_strata_are_always_explicit() {
        let mut negatives = NegativeEvaluation::default();
        negatives.suites.insert(
            "ambiguity".to_owned(),
            NegativeSuiteObservation {
                records: 5,
                clarification_expected: 5,
                ..NegativeSuiteObservation::default()
            },
        );
        let values = insufficient_strata(&negatives);
        assert_eq!(values.len(), 3);
        assert!(values.iter().any(|value| value.value == "asr_noise"));
        assert!(
            values
                .iter()
                .any(|value| value.value == "clarification" && value.eligible_records == 5)
        );
    }

    #[test]
    fn aggregate_output_contains_no_case_or_utterance_bytes() {
        let cases = vec![EvaluatedCase {
            word_count: 2,
            dimensions: P02Dimensions {
                source: "FIXTURE_TECNICA_SOURCE".to_owned(),
                family: "FIXTURE_TECNICA_FAMILY".to_owned(),
                intent: "FIXTURE_TECNICA_INTENT".to_owned(),
                domain: "FIXTURE_TECNICA_DOMAIN".to_owned(),
                slot_kind: "FIXTURE_TECNICA_SLOT".to_owned(),
                graph_shape: "FIXTURE_TECNICA_GRAPH".to_owned(),
                outcome: "FIXTURE_TECNICA_OUTCOME".to_owned(),
                ambiguity: "FIXTURE_TECNICA_AMBIGUITY".to_owned(),
                noise: "FIXTURE_TECNICA_NOISE".to_owned(),
                target_cardinality: "FIXTURE_TECNICA_CARDINALITY".to_owned(),
            },
            outcome: crate::runtime::ObservedOutcome::Plan,
            flags: SemanticFlags {
                intent_exact: true,
                slot_exact: true,
                entity_eligible: false,
                entity_exact: true,
                graph_exact: true,
                final_outcome_exact: true,
            },
        }];
        let positive = PositiveEvaluation {
            signature: RunSignature {
                records: 1,
                plans: 1,
                intent_exact: 1,
                slot_exact: 1,
                graph_exact: 1,
                final_outcome_exact: 1,
                exact_semantic_success: 1,
                ..RunSignature::default()
            },
            cases,
        };
        let mut negatives = NegativeEvaluation::default();
        for (suite, records) in [
            ("ambiguity", 5),
            ("contradiction", 5),
            ("explicit_negative", 7),
            ("safety_sensitive", 5),
            ("stale_state", 5),
        ] {
            negatives.suites.insert(
                suite.to_owned(),
                NegativeSuiteObservation {
                    records,
                    clarification_expected: if suite == "ambiguity" { records } else { 0 },
                    abstention_expected: if suite == "ambiguity" { 0 } else { records },
                    ..NegativeSuiteObservation::default()
                },
            );
        }
        let report = build_report_for_split(EvaluationSplit::Heldout, 1, &positive, &negatives)
            .expect("FIXTURE_TECNICA report");
        let bytes = crate::canonical_report(&report).expect("FIXTURE_TECNICA canonical report");
        for forbidden in [
            b"FIXTURE_TECNICA_CASE_SECRET".as_slice(),
            b"FIXTURE_TECNICA_UTTERANCE_SECRET".as_slice(),
            b"case_id".as_slice(),
            b"utterance".as_slice(),
        ] {
            assert!(!contains_subslice(&bytes, forbidden));
        }
    }

    fn contains_subslice(haystack: &[u8], needle: &[u8]) -> bool {
        !needle.is_empty()
            && haystack
                .windows(needle.len())
                .any(|window| window == needle)
    }
}
