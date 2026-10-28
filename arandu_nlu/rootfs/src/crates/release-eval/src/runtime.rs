use std::{
    collections::{BTreeMap, BTreeSet},
    hint::black_box,
    time::{Duration, Instant},
};

use ha_catalog::{
    AliasProvenance, CapabilityDescriptorInput, CatalogSnapshot, CatalogSnapshotInput, Domain,
    EntityInput, EntityInputParts, EntityVisibility, ExplicitAlias, ExternalEntityId,
    RegistryEntryId, SensitiveText,
};
use nlu_core::{
    CapabilityId, CatalogGeneration, ComposedPlan, GraphExecutionClass, LogicalTime, Polarity,
    RelationKind, RequestText, SlotValue,
};
use nlu_server::{NluRuntime, RuntimeSnapshot, SnapshotMetadata};
use policy_engine::{ConfirmationConfig, PolicyGeneration};
use protocol::v2::{self, Outcome, Request, SessionId};
use serde::Serialize;
use session_engine::SessionConfig;

use crate::{
    Result,
    error::{protocol_error, reconciliation, resource_limit, runtime_error},
    schema::{
        CatalogEntity, EvaluationCase, ExpectedGraph, ExpectedValue, NegativeCase, P02Dimensions,
    },
};

const MAX_CASES_PER_RUNTIME: usize = 48;
const SESSION_TTL_TICKS: u64 = 300_000;
const CONFIRMATION_TTL_TICKS: u64 = 300_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ObservedOutcome {
    Plan,
    Clarification,
    Abstention,
    Error,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct SemanticFlags {
    pub(crate) intent_exact: bool,
    pub(crate) slot_exact: bool,
    pub(crate) entity_eligible: bool,
    pub(crate) entity_exact: bool,
    pub(crate) graph_exact: bool,
    pub(crate) final_outcome_exact: bool,
}

impl SemanticFlags {
    pub(crate) const fn exact_semantic_success(self) -> bool {
        self.intent_exact
            && self.slot_exact
            && (!self.entity_eligible || self.entity_exact)
            && self.graph_exact
            && self.final_outcome_exact
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
pub(crate) struct StratumKey {
    pub(crate) dimension: String,
    pub(crate) value: String,
}

impl StratumKey {
    fn new(dimension: &str, value: &str) -> Self {
        Self {
            dimension: dimension.to_owned(),
            value: value.to_owned(),
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct EvaluatedCase {
    pub(crate) word_count: u64,
    pub(crate) dimensions: P02Dimensions,
    pub(crate) outcome: ObservedOutcome,
    pub(crate) flags: SemanticFlags,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
pub(crate) struct RunSignature {
    pub(crate) records: u64,
    pub(crate) plans: u64,
    pub(crate) clarifications: u64,
    pub(crate) abstentions: u64,
    pub(crate) errors: u64,
    pub(crate) intent_exact: u64,
    pub(crate) slot_exact: u64,
    pub(crate) entity_eligible: u64,
    pub(crate) entity_exact: u64,
    pub(crate) graph_exact: u64,
    pub(crate) final_outcome_exact: u64,
    pub(crate) exact_semantic_success: u64,
}

impl RunSignature {
    fn observe(&mut self, observation: &CaseObservation) {
        self.records += 1;
        match observation.outcome {
            ObservedOutcome::Plan => self.plans += 1,
            ObservedOutcome::Clarification => self.clarifications += 1,
            ObservedOutcome::Abstention => self.abstentions += 1,
            ObservedOutcome::Error => self.errors += 1,
        }
        self.intent_exact += u64::from(observation.flags.intent_exact);
        self.slot_exact += u64::from(observation.flags.slot_exact);
        self.entity_eligible += u64::from(observation.flags.entity_eligible);
        self.entity_exact += u64::from(observation.flags.entity_exact);
        self.graph_exact += u64::from(observation.flags.graph_exact);
        self.final_outcome_exact += u64::from(observation.flags.final_outcome_exact);
        self.exact_semantic_success += u64::from(observation.flags.exact_semantic_success());
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
pub(crate) struct WorkloadSignature {
    pub(crate) semantic: RunSignature,
    pub(crate) words: u64,
    pub(crate) eligible_utterances: u64,
    pub(crate) eligible_words: u64,
}

impl WorkloadSignature {
    fn observe(&mut self, observation: &CaseObservation, word_count: u64) -> Result<()> {
        self.semantic.observe(observation);
        self.words = self
            .words
            .checked_add(word_count)
            .ok_or_else(|| resource_limit("workload word count"))?;
        if observation.flags.exact_semantic_success() {
            self.eligible_utterances = self
                .eligible_utterances
                .checked_add(1)
                .ok_or_else(|| resource_limit("eligible utterance count"))?;
            self.eligible_words = self
                .eligible_words
                .checked_add(word_count)
                .ok_or_else(|| resource_limit("eligible word count"))?;
        }
        Ok(())
    }

    pub(crate) const fn exact_semantic_pass(self) -> bool {
        self.semantic.records != 0
            && self.semantic.exact_semantic_success == self.semantic.records
            && self.eligible_utterances == self.semantic.records
            && self.eligible_words == self.words
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct PreflightProfile {
    pub(crate) global: WorkloadSignature,
    pub(crate) strata: BTreeMap<StratumKey, WorkloadSignature>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct NegativeSuiteObservation {
    pub(crate) records: u64,
    pub(crate) false_plans: u64,
    pub(crate) clarification_expected: u64,
    pub(crate) clarification_exact: u64,
    pub(crate) abstention_expected: u64,
    pub(crate) abstention_exact: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct NegativeEvaluation {
    pub(crate) suites: BTreeMap<String, NegativeSuiteObservation>,
}

impl NegativeEvaluation {
    pub(crate) fn total_false_plans(&self) -> u64 {
        self.suites.values().map(|suite| suite.false_plans).sum()
    }

    pub(crate) fn total_records(&self) -> u64 {
        self.suites.values().map(|suite| suite.records).sum()
    }

    pub(crate) fn clarification_totals(&self) -> (u64, u64) {
        self.suites
            .values()
            .fold((0, 0), |(exact, expected), suite| {
                (
                    exact + suite.clarification_exact,
                    expected + suite.clarification_expected,
                )
            })
    }

    pub(crate) fn abstention_totals(&self) -> (u64, u64) {
        self.suites
            .values()
            .fold((0, 0), |(exact, expected), suite| {
                (
                    exact + suite.abstention_exact,
                    expected + suite.abstention_expected,
                )
            })
    }
}

pub(crate) struct PositiveEvaluation {
    pub(crate) cases: Vec<EvaluatedCase>,
    pub(crate) signature: RunSignature,
}

struct PreparedCase {
    case: EvaluationCase,
    source: RequestText,
    encoded_request: Vec<u8>,
}

struct PreparedGroup {
    runtime: RuntimeSnapshot<NluRuntime>,
    cases: Vec<PreparedCase>,
}

pub(crate) struct PreparedCorpus {
    groups: Vec<PreparedGroup>,
    records: u64,
}

#[derive(Clone, Copy)]
pub(crate) enum BenchmarkBoundary {
    Core,
    ProtocolRuntime,
}

struct CaseObservation {
    outcome: ObservedOutcome,
    flags: SemanticFlags,
}

pub(crate) struct TimedStratumCycle {
    pub(crate) elapsed: Duration,
    pub(crate) signature: WorkloadSignature,
}

pub(crate) struct TimedCycle {
    pub(crate) elapsed: Duration,
    pub(crate) signature: WorkloadSignature,
    pub(crate) strata: BTreeMap<StratumKey, TimedStratumCycle>,
}

pub(crate) fn evaluate_positive(cases: &[EvaluationCase]) -> Result<PositiveEvaluation> {
    let prepared = prepare_corpus(cases)?;
    let mut evaluated = Vec::with_capacity(cases.len());
    let mut signature = RunSignature::default();
    for group in &prepared.groups {
        for prepared_case in &group.cases {
            let observation =
                dispatch_preencoded(&group.runtime, prepared_case, prepared_case.case.ordinal)?;
            signature.observe(&observation);
            evaluated.push(EvaluatedCase {
                word_count: prepared_case.case.word_count,
                dimensions: prepared_case.case.dimensions.clone(),
                outcome: observation.outcome,
                flags: observation.flags,
            });
        }
    }
    if signature.records != prepared.records {
        return Err(reconciliation("positive runtime denominator"));
    }
    Ok(PositiveEvaluation {
        cases: evaluated,
        signature,
    })
}

pub(crate) fn preflight_profile(evaluation: &PositiveEvaluation) -> Result<PreflightProfile> {
    let mut global = WorkloadSignature::default();
    let mut strata = BTreeMap::<StratumKey, WorkloadSignature>::new();
    for case in &evaluation.cases {
        let observation = CaseObservation {
            outcome: case.outcome,
            flags: case.flags,
        };
        global.observe(&observation, case.word_count)?;
        for (dimension, value) in case.dimensions.frozen_values() {
            strata
                .entry(StratumKey::new(dimension, value))
                .or_default()
                .observe(&observation, case.word_count)?;
        }
    }
    if global.semantic != evaluation.signature {
        return Err(reconciliation("preflight signature"));
    }
    Ok(PreflightProfile { global, strata })
}

pub(crate) fn evaluate_negatives(cases: &[NegativeCase]) -> Result<NegativeEvaluation> {
    let mut result = NegativeEvaluation::default();
    let mut by_generation = BTreeMap::<u64, Vec<&NegativeCase>>::new();
    for case in cases {
        by_generation
            .entry(case.catalog_generation)
            .or_default()
            .push(case);
    }
    for (generation, generation_cases) in by_generation {
        let runtime = runtime_snapshot(generation, &[])?;
        for case in generation_cases {
            let source = RequestText::new(case.utterance.clone())
                .map_err(|_| protocol_error("negative request text"))?;
            let request = Request::Interpret {
                session_id: session_id(case.ordinal),
                text: source.clone(),
            };
            let encoded =
                v2::encode_request(&request).map_err(|_| protocol_error("negative request"))?;
            let response =
                NluRuntime::dispatch_at(&runtime, &encoded, LogicalTime::from_ticks(case.ordinal))
                    .map_err(|_| runtime_error("negative dispatch"))?;
            let decoded = v2::decode_response(&response, &source)
                .map_err(|_| protocol_error("negative response"))?;
            let outcome = observed_outcome(decoded.outcome());
            let suite = result.suites.entry(case.suite_id.clone()).or_default();
            suite.records += 1;
            suite.false_plans += u64::from(outcome == ObservedOutcome::Plan);
            match case.expected_outcome.as_str() {
                "clarification" => {
                    suite.clarification_expected += 1;
                    suite.clarification_exact +=
                        u64::from(outcome == ObservedOutcome::Clarification);
                }
                "abstention" => {
                    suite.abstention_expected += 1;
                    suite.abstention_exact += u64::from(outcome == ObservedOutcome::Abstention);
                }
                _ => return Err(reconciliation("negative expected outcome")),
            }
        }
    }
    if result.total_records()
        != u64::try_from(cases.len()).map_err(|_| resource_limit("negative record count"))?
    {
        return Err(reconciliation("negative runtime denominator"));
    }
    Ok(result)
}

pub(crate) fn prepare_corpus(cases: &[EvaluationCase]) -> Result<PreparedCorpus> {
    let mut groups = Vec::new();
    let mut start = 0_usize;
    while start < cases.len() {
        let intent = cases[start].dimensions.intent.as_str();
        let mut end = start;
        while end < cases.len()
            && end - start < MAX_CASES_PER_RUNTIME
            && cases[end].dimensions.intent == intent
        {
            end += 1;
        }
        if end == start {
            return Err(reconciliation("runtime grouping"));
        }
        groups.push(prepare_group(&cases[start..end])?);
        start = end;
    }
    Ok(PreparedCorpus {
        groups,
        records: u64::try_from(cases.len()).map_err(|_| resource_limit("prepared record count"))?,
    })
}

fn prepare_group(cases: &[EvaluationCase]) -> Result<PreparedGroup> {
    let mut entities = BTreeMap::<String, CatalogEntity>::new();
    for case in cases {
        for entity in &case.catalog_entities {
            let entry = entities
                .entry(entity.external_id.clone())
                .or_insert_with(|| entity.clone());
            if entry.registry_id != entity.registry_id
                || entry.domain != entity.domain
                || entry.mention != entity.mention
            {
                return Err(reconciliation("group catalog collision"));
            }
            for capability in &entity.capabilities {
                if !entry.capabilities.contains(capability) {
                    entry.capabilities.push(capability.clone());
                }
            }
            entry.capabilities.sort();
        }
    }
    let catalog_entities = entities.into_values().collect::<Vec<_>>();
    let runtime = runtime_snapshot(1, &catalog_entities)?;
    let prepared_cases = cases
        .iter()
        .map(|case| {
            let source = RequestText::new(case.utterance.clone())
                .map_err(|_| protocol_error("positive request text"))?;
            let request = Request::Interpret {
                session_id: session_id(case.ordinal),
                text: source.clone(),
            };
            let encoded_request =
                v2::encode_request(&request).map_err(|_| protocol_error("positive request"))?;
            Ok(PreparedCase {
                case: case.clone(),
                source,
                encoded_request,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(PreparedGroup {
        runtime,
        cases: prepared_cases,
    })
}

fn runtime_snapshot(
    catalog_generation: u64,
    entities: &[CatalogEntity],
) -> Result<RuntimeSnapshot<NluRuntime>> {
    let metadata = SnapshotMetadata::new(1, catalog_generation, 1, 1, 1)
        .map_err(|_| runtime_error("snapshot metadata"))?;
    let catalog = build_catalog(catalog_generation, entities)?;
    let runtime = NluRuntime::standard(
        metadata,
        catalog,
        PolicyGeneration::new(1).map_err(|_| runtime_error("policy generation"))?,
        SessionConfig::new(SESSION_TTL_TICKS)
            .map_err(|_| runtime_error("session configuration"))?,
        ConfirmationConfig::new(CONFIRMATION_TTL_TICKS)
            .map_err(|_| runtime_error("confirmation configuration"))?,
    )
    .map_err(|_| runtime_error("runtime construction"))?;
    Ok(RuntimeSnapshot::new(metadata, runtime))
}

fn build_catalog(generation: u64, entities: &[CatalogEntity]) -> Result<CatalogSnapshot> {
    let generation =
        CatalogGeneration::new(generation).map_err(|_| runtime_error("catalog generation"))?;
    let mut descriptor_domains = BTreeMap::<String, BTreeSet<String>>::new();
    let entity_inputs = entities
        .iter()
        .map(|entity| {
            let capabilities = entity
                .capabilities
                .iter()
                .map(|capability| {
                    descriptor_domains
                        .entry(capability.clone())
                        .or_default()
                        .insert(entity.domain.clone());
                    CapabilityId::new(capability).map_err(|_| runtime_error("catalog capability"))
                })
                .collect::<Result<Vec<_>>>()?;
            let registry_id = RegistryEntryId::new(&entity.registry_id)
                .map_err(|_| runtime_error("catalog registry ID"))?;
            let external_id = ExternalEntityId::new(&entity.external_id)
                .map_err(|_| runtime_error("catalog external ID"))?;
            let domain =
                Domain::new(&entity.domain).map_err(|_| runtime_error("catalog domain"))?;
            Ok(EntityInput::new(EntityInputParts {
                generation,
                registry_id,
                external_id,
                domain,
                display_name: SensitiveText::new(format!(
                    "FIXTURE_TECNICA_RELEASE_{}",
                    entity.registry_id
                ))
                .map_err(|_| runtime_error("catalog display"))?,
                aliases: vec![ExplicitAlias::new(
                    SensitiveText::new(entity.mention.clone())
                        .map_err(|_| runtime_error("catalog alias"))?,
                    AliasProvenance::EntityRegistry,
                )],
                capabilities,
                area_id: None,
                floor_id: None,
                device_id: None,
                visibility: EntityVisibility::exposed(),
            }))
        })
        .collect::<Result<Vec<_>>>()?;
    let capability_descriptors = descriptor_domains
        .into_iter()
        .map(|(capability, domains)| {
            Ok(CapabilityDescriptorInput {
                id: CapabilityId::new(&capability)
                    .map_err(|_| runtime_error("catalog descriptor"))?,
                enabled: true,
                state_query_domains: domains
                    .into_iter()
                    .map(|domain| {
                        Domain::new(&domain).map_err(|_| runtime_error("catalog descriptor domain"))
                    })
                    .collect::<Result<Vec<_>>>()?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    CatalogSnapshot::build(CatalogSnapshotInput {
        generation,
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors,
        entities: entity_inputs,
    })
    .map_err(|_| runtime_error("catalog construction"))
}

fn session_id(ordinal: u64) -> SessionId {
    let mut bytes = [0_u8; v2::SESSION_ID_BYTES];
    bytes[..8].copy_from_slice(&ordinal.to_be_bytes());
    bytes[8..24].copy_from_slice(b"FIXTURE_TECNICA_");
    SessionId::from_bytes(bytes)
}

fn dispatch_preencoded(
    runtime: &RuntimeSnapshot<NluRuntime>,
    prepared: &PreparedCase,
    logical_tick: u64,
) -> Result<CaseObservation> {
    let response = NluRuntime::dispatch_at(
        runtime,
        &prepared.encoded_request,
        LogicalTime::from_ticks(logical_tick),
    )
    .map_err(|_| runtime_error("positive dispatch"))?;
    let decoded = v2::decode_response(&response, &prepared.source)
        .map_err(|_| protocol_error("positive response"))?;
    compare_response(&prepared.case.expected, decoded.outcome())
}

fn timed_dispatch_preencoded(
    runtime: &RuntimeSnapshot<NluRuntime>,
    prepared: &PreparedCase,
    logical_tick: u64,
) -> Result<(Duration, CaseObservation)> {
    let started = Instant::now();
    let response = NluRuntime::dispatch_at(
        runtime,
        &prepared.encoded_request,
        LogicalTime::from_ticks(logical_tick),
    )
    .map_err(|_| runtime_error("positive dispatch"))?;
    let decoded = v2::decode_response(&response, &prepared.source)
        .map_err(|_| protocol_error("positive response"))?;
    let elapsed = started.elapsed();
    let observation = compare_response(&prepared.case.expected, decoded.outcome())?;
    Ok((elapsed, observation))
}

fn timed_dispatch_protocol_runtime(
    runtime: &RuntimeSnapshot<NluRuntime>,
    prepared: &PreparedCase,
    logical_tick: u64,
) -> Result<(Duration, CaseObservation)> {
    let started = Instant::now();
    let source = RequestText::new(prepared.case.utterance.clone())
        .map_err(|_| protocol_error("protocol runtime request text"))?;
    let request = Request::Interpret {
        session_id: session_id(prepared.case.ordinal),
        text: source.clone(),
    };
    let encoded = v2::encode_request(&request)
        .map_err(|_| protocol_error("protocol runtime request encoding"))?;
    let response =
        NluRuntime::dispatch_at(runtime, &encoded, LogicalTime::from_ticks(logical_tick))
            .map_err(|_| runtime_error("protocol runtime dispatch"))?;
    let decoded = v2::decode_response(&response, &source)
        .map_err(|_| protocol_error("protocol runtime response decoding"))?;
    let elapsed = started.elapsed();
    let observation = compare_response(&prepared.case.expected, decoded.outcome())?;
    Ok((elapsed, observation))
}

pub(crate) fn timed_complete_cycle(
    prepared: &PreparedCorpus,
    boundary: BenchmarkBoundary,
    cycle_index: u32,
) -> Result<TimedCycle> {
    let mut elapsed = Duration::ZERO;
    let mut signature = WorkloadSignature::default();
    let mut strata = BTreeMap::<StratumKey, TimedStratumCycle>::new();
    let stride = prepared
        .records
        .checked_add(1)
        .ok_or_else(|| resource_limit("benchmark cycle stride"))?;
    let cycle_base = u64::from(cycle_index)
        .checked_mul(stride)
        .ok_or_else(|| resource_limit("benchmark cycle tick"))?;
    for group in &prepared.groups {
        for case in &group.cases {
            let logical_tick = cycle_base
                .checked_add(case.case.ordinal)
                .ok_or_else(|| resource_limit("benchmark logical tick"))?;
            let (case_elapsed, observation) = match boundary {
                BenchmarkBoundary::Core => {
                    timed_dispatch_preencoded(&group.runtime, case, logical_tick)?
                }
                BenchmarkBoundary::ProtocolRuntime => {
                    timed_dispatch_protocol_runtime(&group.runtime, case, logical_tick)?
                }
            };
            let observation = black_box(observation);
            elapsed = elapsed
                .checked_add(case_elapsed)
                .ok_or_else(|| resource_limit("benchmark elapsed duration"))?;
            signature.observe(&observation, case.case.word_count)?;
            for (dimension, value) in case.case.dimensions.frozen_values() {
                let entry =
                    strata
                        .entry(StratumKey::new(dimension, value))
                        .or_insert(TimedStratumCycle {
                            elapsed: Duration::ZERO,
                            signature: WorkloadSignature::default(),
                        });
                entry.elapsed = entry
                    .elapsed
                    .checked_add(case_elapsed)
                    .ok_or_else(|| resource_limit("stratum elapsed duration"))?;
                entry
                    .signature
                    .observe(&observation, case.case.word_count)?;
            }
        }
    }
    if signature.semantic.records != prepared.records {
        return Err(reconciliation("timed runtime denominator"));
    }
    Ok(TimedCycle {
        elapsed,
        signature,
        strata,
    })
}

fn observed_outcome(outcome: &Outcome) -> ObservedOutcome {
    match outcome {
        Outcome::CompletePlan(_)
        | Outcome::ConfirmationRequired(_)
        | Outcome::PolicyAccepted(_) => ObservedOutcome::Plan,
        Outcome::EntityClarification(_) => ObservedOutcome::Clarification,
        Outcome::Abstention(_) | Outcome::PolicyDenial(_) => ObservedOutcome::Abstention,
        Outcome::Cancellation(_) | Outcome::Health(_) | Outcome::ProtocolError(_) => {
            ObservedOutcome::Error
        }
    }
}

fn compare_response(expected: &ExpectedGraph, outcome: &Outcome) -> Result<CaseObservation> {
    let plan = match outcome {
        Outcome::CompletePlan(plan) => Some(plan),
        Outcome::ConfirmationRequired(required) => Some(required.plan()),
        Outcome::PolicyAccepted(accepted) => Some(accepted.plan()),
        Outcome::EntityClarification(_)
        | Outcome::Abstention(_)
        | Outcome::PolicyDenial(_)
        | Outcome::Cancellation(_)
        | Outcome::Health(_)
        | Outcome::ProtocolError(_) => None,
    };
    let observed = observed_outcome(outcome);
    let entity_eligible = expected
        .nodes
        .iter()
        .flat_map(|node| &node.slots)
        .any(|slot| matches!(slot.value, ExpectedValue::Entity { .. }));
    let flags = if let Some(plan) = plan {
        let first = plan
            .canonical_bytes()
            .map_err(|_| reconciliation("canonical plan"))?;
        let second = plan
            .canonical_bytes()
            .map_err(|_| reconciliation("canonical plan replay"))?;
        if first != second {
            return Err(reconciliation("canonical plan determinism"));
        }
        SemanticFlags {
            intent_exact: intent_exact(expected, plan),
            slot_exact: slot_exact(expected, plan)?,
            entity_eligible,
            entity_exact: !entity_eligible || entity_exact(expected, plan),
            graph_exact: graph_exact(expected, plan),
            final_outcome_exact: true,
        }
    } else {
        SemanticFlags {
            entity_eligible,
            ..SemanticFlags::default()
        }
    };
    Ok(CaseObservation {
        outcome: observed,
        flags,
    })
}

fn intent_exact(expected: &ExpectedGraph, actual: &ComposedPlan) -> bool {
    if expected.nodes.len() != actual.clauses().len() {
        return false;
    }
    expected.nodes.iter().all(|expected_node| {
        actual.clauses().iter().any(|clause| {
            clause.node().as_str() == expected_node.id
                && clause.intent().as_str() == expected_node.intent
        })
    })
}

fn slot_exact(expected: &ExpectedGraph, actual: &ComposedPlan) -> Result<bool> {
    if expected.nodes.len() != actual.plan().nodes().len() {
        return Ok(false);
    }
    for expected_node in &expected.nodes {
        let Some(actual_node) = actual
            .plan()
            .nodes()
            .iter()
            .find(|node| node.id().as_str() == expected_node.id)
        else {
            return Ok(false);
        };
        if expected_node.slots.len() != actual_node.slots().len() {
            return Ok(false);
        }
        for expected_slot in &expected_node.slots {
            let Some(actual_slot) = actual_node
                .slots()
                .iter()
                .find(|slot| slot.id().as_str() == expected_slot.id)
            else {
                return Ok(false);
            };
            if !slot_value_exact(&expected_slot.value, actual_slot.value(), actual.source())? {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

fn entity_exact(expected: &ExpectedGraph, actual: &ComposedPlan) -> bool {
    let expected_entities = expected
        .nodes
        .iter()
        .flat_map(|node| {
            node.slots.iter().filter_map(move |slot| {
                let ExpectedValue::Entity { id, generation } = &slot.value else {
                    return None;
                };
                Some((node.id.as_str(), slot.id.as_str(), id.as_str(), *generation))
            })
        })
        .collect::<Vec<_>>();
    let actual_entities = actual
        .plan()
        .nodes()
        .iter()
        .flat_map(|node| {
            node.slots().iter().filter_map(move |slot| {
                let SlotValue::Entity(entity) = slot.value() else {
                    return None;
                };
                Some((
                    node.id().as_str(),
                    slot.id().as_str(),
                    entity.id().as_str(),
                    entity.generation().get(),
                ))
            })
        })
        .collect::<Vec<_>>();
    expected_entities == actual_entities
}

fn slot_value_exact(
    expected: &ExpectedValue,
    actual: &SlotValue,
    source: &RequestText,
) -> Result<bool> {
    match (expected, actual) {
        (ExpectedValue::Entity { id, generation }, SlotValue::Entity(entity)) => {
            Ok(entity.id().as_str() == id && entity.generation().get() == *generation)
        }
        (ExpectedValue::Integer(expected), SlotValue::Integer(actual)) => Ok(expected == actual),
        (ExpectedValue::Text(expected), SlotValue::EvidenceText(span)) => span
            .slice(source)
            .map(|actual| actual == expected)
            .map_err(|_| reconciliation("text slot source")),
        _ => Ok(false),
    }
}

fn graph_exact(expected: &ExpectedGraph, actual: &ComposedPlan) -> bool {
    if actual.plan().catalog_generation().get() != expected.catalog_generation
        || execution_class(actual.execution_class()) != expected.execution_class
        || actual.plan().nodes().len() != expected.nodes.len()
        || actual.plan().relations().len() != expected.relations.len()
        || actual.independent_pairs().len() != expected.independent_pairs.len()
        || actual.argument_shares().len() != expected.argument_shares.len()
    {
        return false;
    }
    for expected_node in &expected.nodes {
        let Some(actual_node) = actual
            .plan()
            .nodes()
            .iter()
            .find(|node| node.id().as_str() == expected_node.id)
        else {
            return false;
        };
        let Some(clause) = actual
            .clauses()
            .iter()
            .find(|clause| clause.node().as_str() == expected_node.id)
        else {
            return false;
        };
        if actual_node.capability().as_str() != expected_node.capability
            || actual_node.operation().as_str() != expected_node.operation
            || polarity(clause.polarity()) != expected_node.polarity
        {
            return false;
        }
    }
    let relations = actual
        .plan()
        .relations()
        .iter()
        .map(|relation| {
            (
                relation.from().as_str(),
                relation.to().as_str(),
                match relation.kind() {
                    RelationKind::Precedes => "precedes",
                    RelationKind::Requires => "requires",
                },
            )
        })
        .collect::<Vec<_>>();
    if relations
        != expected
            .relations
            .iter()
            .map(|(from, to, kind)| (from.as_str(), to.as_str(), kind.as_str()))
            .collect::<Vec<_>>()
    {
        return false;
    }
    let pairs = actual
        .independent_pairs()
        .iter()
        .map(|pair| (pair.left().as_str(), pair.right().as_str()))
        .collect::<Vec<_>>();
    if pairs
        != expected
            .independent_pairs
            .iter()
            .map(|(left, right)| (left.as_str(), right.as_str()))
            .collect::<Vec<_>>()
    {
        return false;
    }
    let shares = actual
        .argument_shares()
        .iter()
        .map(|share| {
            (
                share.from().node().as_str(),
                share.from().slot().as_str(),
                share.to().node().as_str(),
                share.to().slot().as_str(),
            )
        })
        .collect::<Vec<_>>();
    shares
        == expected
            .argument_shares
            .iter()
            .map(|(from_node, from_slot, to_node, to_slot)| {
                (
                    from_node.as_str(),
                    from_slot.as_str(),
                    to_node.as_str(),
                    to_slot.as_str(),
                )
            })
            .collect::<Vec<_>>()
}

const fn execution_class(value: GraphExecutionClass) -> &'static str {
    match value {
        GraphExecutionClass::PartialSafe => "partial_safe",
        GraphExecutionClass::AtomicOnly => "atomic_only",
        GraphExecutionClass::NonExecutable => "non_executable",
    }
}

const fn polarity(value: Polarity) -> &'static str {
    match value {
        Polarity::Affirmed => "affirmed",
        Polarity::Negated => "negated",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{EvaluationCase, ExpectedGraph, P02Dimensions};

    #[test]
    fn protocol_v2_dispatch_uses_the_production_runtime() {
        let utterance = "FIXTURE_TECNICA_RUNTIME_REQUEST".to_owned();
        let cases = vec![EvaluationCase {
            ordinal: 1,
            word_count: crate::word_count::count(&utterance).expect("FIXTURE_TECNICA word count"),
            utterance,
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
            expected: ExpectedGraph {
                catalog_generation: 1,
                execution_class: "FIXTURE_TECNICA_EXECUTION".to_owned(),
                nodes: Vec::new(),
                relations: Vec::new(),
                independent_pairs: Vec::new(),
                argument_shares: Vec::new(),
            },
            catalog_entities: Vec::new(),
        }];
        let result =
            evaluate_positive(&cases).expect("FIXTURE_TECNICA production dispatch evaluation");
        assert_eq!(result.signature.records, 1);
        assert_eq!(
            result.signature.plans
                + result.signature.clarifications
                + result.signature.abstentions
                + result.signature.errors,
            1
        );
    }
}
