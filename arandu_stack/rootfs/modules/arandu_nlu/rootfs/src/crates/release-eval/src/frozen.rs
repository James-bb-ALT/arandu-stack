use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

use nlu_core::{CapabilityId, IntentId, OperationId, SlotId};
use nlu_data::{DataErrorCode, parse_strict_json, read_bounded_root_file, sha256_hex};
use serde::de::DeserializeOwned;

use crate::{
    Result,
    error::{
        hash_mismatch, input_io, input_too_large, invalid_dataset, invalid_manifest,
        invalid_projection, resource_limit,
    },
    oracle,
    schema::{
        EvaluationSplit, GraphShapeProjection, IntentProjection, Manifest, ManifestArtifact,
        NegativeCase, NegativeRow, P02ExpectedPlan, P02ExpectedSlot, P02Row, P09Projection,
        P11Projection, SlotProjection,
    },
};

pub(crate) const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
pub(crate) const CORPUS_VERSION: &str = "1.0.0";
pub(crate) const GENERATOR_ID: &str = "p02-generator-v1";
pub(crate) const ORACLE_ORIGIN: &str = "pre_engine_generator_specification";
pub(crate) const CLAIM_SCOPE: &str = "internal_conformance_only";
pub(crate) const SOURCE_LICENSE: &str = "Apache-2.0";
pub(crate) const LOCALE: &str = "pt-BR";
pub(crate) const MANIFEST_PATH: &str = "data/project-authored/p02-v1/manifest.json";
pub(crate) const MANIFEST_SHA256: &str =
    "a251485ba2f8d5032603200b7e171954213383aeceac9a8f394edb09a265e72a";
pub(crate) const SPECIFICATION_PATH: &str = "data/project-authored/p02-v1/specification.yaml";
pub(crate) const SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
pub(crate) const GENERATOR_SHA256: &str =
    "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
pub(crate) const P09_PATH: &str = "data/evaluation/p09/intent-v1/projection.json";
pub(crate) const P09_SHA256: &str =
    "5f661bb96b85667a1d0c9ec4d85cb61c443e1b24ab4dfc6c1ab849c6df936ee4";
pub(crate) const P11_PATH: &str = "data/evaluation/p11/plan-v1/projection.json";
pub(crate) const P11_SHA256: &str =
    "581b852b8cd2ec24b4c02b8c68914b27a928d953470a10665357e50425783f75";
pub(crate) const HELDOUT_PATH: &str = "data/project-authored/p02-v1/heldout.jsonl";
pub(crate) const HELDOUT_SHA256: &str =
    "1b3e3669ba3e64193b769bccf90368f571daae5b4f3d9ed88724c99bec12c6da";
pub(crate) const PERFORMANCE_PATH: &str = "data/project-authored/p02-v1/performance.jsonl";
pub(crate) const PERFORMANCE_SHA256: &str =
    "1c9fedff6a3bc7aa36e5f343eb85ce2f197ce0d5c44cd514fe1c5b08a03ba55b";
pub(crate) const RECORDS_PER_RELEASE_SPLIT: usize = 4_800;
pub(crate) const MINIMUM_SUPPORTED_STRATUM: u64 = 237;

const MANIFEST_MAX_BYTES: usize = 16 * 1024;
const SPECIFICATION_MAX_BYTES: usize = 32 * 1024;
const PROJECTION_MAX_BYTES: usize = 128 * 1024;
const RELEASE_SPLIT_MAX_BYTES: usize = 8 * 1024 * 1024;
const SUITE_MAX_BYTES: usize = 16 * 1024;
const ROW_MAX_BYTES: usize = 8 * 1024;
const NEGATIVE_ROW_MAX_BYTES: usize = 4 * 1024;

pub(crate) const DIMENSIONS: [&str; 9] = [
    "source",
    "family",
    "intent",
    "domain",
    "slot_kind",
    "graph_shape",
    "outcome",
    "ambiguity",
    "noise",
];

pub(crate) const OFFICIAL_INTENTS: [&str; 20] = [
    "HassTurnOff",
    "HassTurnOn",
    "HassToggle",
    "HassGetState",
    "HassNevermind",
    "HassSetPosition",
    "HassStopMoving",
    "HassStartTimer",
    "HassCancelTimer",
    "HassCancelAllTimers",
    "HassIncreaseTimer",
    "HassDecreaseTimer",
    "HassPauseTimer",
    "HassUnpauseTimer",
    "HassTimerStatus",
    "HassGetCurrentDate",
    "HassGetCurrentTime",
    "HassRespond",
    "HassBroadcast",
    "HassClimateGetTemperature",
];

#[derive(Clone, Copy)]
struct SuiteSpec {
    id: &'static str,
    path: &'static str,
    sha256: &'static str,
    records: usize,
    expected_outcome: &'static str,
    catalog_generation: u64,
}

const SUITES: [SuiteSpec; 5] = [
    SuiteSpec {
        id: "ambiguity",
        path: "data/project-authored/p02-v1/suites/ambiguity.jsonl",
        sha256: "098051c102bfdf9ede0781d8c1069546cb2db1d9265b4d1e2b3d2123ea1c7721",
        records: 5,
        expected_outcome: "clarification",
        catalog_generation: 1,
    },
    SuiteSpec {
        id: "contradiction",
        path: "data/project-authored/p02-v1/suites/contradiction.jsonl",
        sha256: "9a2c53626b74ad0479fd81bd91d0a41a027db2eb53eb37b6efcc2066a67689a8",
        records: 5,
        expected_outcome: "abstention",
        catalog_generation: 1,
    },
    SuiteSpec {
        id: "explicit_negative",
        path: "data/project-authored/p02-v1/suites/explicit-negative.jsonl",
        sha256: "9c90a3f399a988d3714a611cf04fda1d60cd5be2b311d06cf957915bb3f53ebb",
        records: 7,
        expected_outcome: "abstention",
        catalog_generation: 1,
    },
    SuiteSpec {
        id: "safety_sensitive",
        path: "data/project-authored/p02-v1/suites/safety-sensitive.jsonl",
        sha256: "8d06bbfbcaf48cf729fd24fb7330c4b6a50a5d016addbf8517ae0356c5b5f261",
        records: 5,
        expected_outcome: "abstention",
        catalog_generation: 1,
    },
    SuiteSpec {
        id: "stale_state",
        path: "data/project-authored/p02-v1/suites/stale-state.jsonl",
        sha256: "1d5d8c8f490ee546ee6fd7d8e165bbb6d6d926ccaedaa8484a47546aa6b96e27",
        records: 5,
        expected_outcome: "abstention",
        catalog_generation: 2,
    },
];

pub(crate) struct FrozenInputs {
    pub(crate) p09: P09Projection,
    pub(crate) p11: P11Projection,
    pub(crate) rows: Vec<P02Row>,
    pub(crate) negatives: Vec<NegativeRow>,
    pub(crate) split: EvaluationSplit,
}

impl EvaluationSplit {
    pub(crate) const fn path(self) -> &'static str {
        match self {
            Self::Heldout => HELDOUT_PATH,
            Self::Performance => PERFORMANCE_PATH,
        }
    }

    pub(crate) const fn sha256(self) -> &'static str {
        match self {
            Self::Heldout => HELDOUT_SHA256,
            Self::Performance => PERFORMANCE_SHA256,
        }
    }
}

pub(crate) fn load(root: &Path, split: EvaluationSplit) -> Result<FrozenInputs> {
    let manifest_bytes = read_verified(
        root,
        MANIFEST_PATH,
        MANIFEST_MAX_BYTES,
        MANIFEST_SHA256,
        "manifest bytes",
    )?;
    let manifest: Manifest = parse_json(&manifest_bytes, "manifest JSON", invalid_manifest)?;
    validate_manifest(&manifest)?;

    let specification = read_verified(
        root,
        SPECIFICATION_PATH,
        SPECIFICATION_MAX_BYTES,
        SPECIFICATION_SHA256,
        "specification bytes",
    )?;
    if specification.is_empty() {
        return Err(invalid_manifest("specification identity"));
    }

    let p09_bytes = read_verified(
        root,
        P09_PATH,
        PROJECTION_MAX_BYTES,
        P09_SHA256,
        "P09 projection bytes",
    )?;
    let p09: P09Projection = parse_json(&p09_bytes, "P09 projection JSON", invalid_projection)?;
    validate_p09(&p09)?;

    let p11_bytes = read_verified(
        root,
        P11_PATH,
        PROJECTION_MAX_BYTES,
        P11_SHA256,
        "P11 projection bytes",
    )?;
    let p11: P11Projection = parse_json(&p11_bytes, "P11 projection JSON", invalid_projection)?;
    validate_p11(&p11)?;

    let split_bytes = read_verified(
        root,
        split.path(),
        RELEASE_SPLIT_MAX_BYTES,
        split.sha256(),
        "release split bytes",
    )?;
    let rows = parse_jsonl::<P02Row>(
        &split_bytes,
        RECORDS_PER_RELEASE_SPLIT,
        ROW_MAX_BYTES,
        "release split rows",
    )?;
    validate_rows(&rows, split, &manifest, &p09)?;

    let mut negatives = Vec::with_capacity(27);
    for suite in SUITES {
        let bytes = read_verified(
            root,
            suite.path,
            SUITE_MAX_BYTES,
            suite.sha256,
            "negative suite bytes",
        )?;
        let rows = parse_jsonl::<NegativeRow>(
            &bytes,
            suite.records,
            NEGATIVE_ROW_MAX_BYTES,
            "negative suite rows",
        )?;
        validate_negative_rows(&rows, suite, &manifest)?;
        negatives.extend(rows);
    }
    if negatives.len() != 27 {
        return Err(invalid_dataset("negative suite total"));
    }

    Ok(FrozenInputs {
        p09,
        p11,
        rows,
        negatives,
        split,
    })
}

pub(crate) fn negative_cases(rows: &[NegativeRow]) -> Result<Vec<NegativeCase>> {
    rows.iter()
        .enumerate()
        .map(|(index, row)| {
            Ok(NegativeCase {
                ordinal: u64::try_from(index)
                    .map_err(|_| resource_limit("negative ordinal"))?
                    .saturating_add(1),
                suite_id: row.suite_id.clone(),
                expected_outcome: row.expected.outcome.clone(),
                catalog_generation: row.context.catalog_generation,
                utterance: row.utterance.clone(),
            })
        })
        .collect()
}

fn read_verified(
    root: &Path,
    relative: &str,
    maximum_bytes: usize,
    expected_sha256: &str,
    context: &'static str,
) -> Result<Vec<u8>> {
    let bytes = read_bounded_root_file(root, relative, maximum_bytes).map_err(|error| {
        if error.code() == DataErrorCode::ResourceLimit {
            input_too_large(context)
        } else {
            input_io(context)
        }
    })?;
    verify_hash(&bytes, expected_sha256, context)?;
    Ok(bytes)
}

fn verify_hash(bytes: &[u8], expected_sha256: &str, context: &'static str) -> Result<()> {
    let actual = sha256_hex(bytes).map_err(|_| hash_mismatch(context))?;
    if actual != expected_sha256 {
        return Err(hash_mismatch(context));
    }
    Ok(())
}

fn parse_json<T>(
    bytes: &[u8],
    context: &'static str,
    error: fn(&'static str) -> crate::ReleaseEvalError,
) -> Result<T>
where
    T: DeserializeOwned,
{
    let value = parse_strict_json(bytes, context).map_err(|failure| {
        if failure.code() == DataErrorCode::ResourceLimit {
            resource_limit(context)
        } else {
            error(context)
        }
    })?;
    serde_json::from_value(value).map_err(|_| error(context))
}

fn parse_jsonl<T>(
    bytes: &[u8],
    expected_records: usize,
    maximum_row_bytes: usize,
    context: &'static str,
) -> Result<Vec<T>>
where
    T: DeserializeOwned,
{
    let mut rows = Vec::with_capacity(expected_records);
    let mut lines = bytes.split(|byte| *byte == b'\n').peekable();
    while let Some(line) = lines.next() {
        if line.is_empty() {
            if lines.peek().is_none() {
                break;
            }
            return Err(invalid_dataset(context));
        }
        if line.len() > maximum_row_bytes || rows.len() >= expected_records {
            return Err(resource_limit(context));
        }
        let value = parse_strict_json(line, context).map_err(|failure| {
            if failure.code() == DataErrorCode::ResourceLimit {
                resource_limit(context)
            } else {
                invalid_dataset(context)
            }
        })?;
        let row = serde_json::from_value(value).map_err(|_| invalid_dataset(context))?;
        rows.push(row);
    }
    if rows.len() != expected_records || !bytes.ends_with(b"\n") {
        return Err(invalid_dataset(context));
    }
    Ok(rows)
}

fn validate_manifest(manifest: &Manifest) -> Result<()> {
    if manifest.schema_version != 1
        || manifest.corpus.id != SOURCE_ID
        || manifest.corpus.version != CORPUS_VERSION
        || manifest.corpus.status != "PROJECT_AUTHORED_SYNTHETIC"
        || manifest.corpus.authorization != "USR-016"
        || manifest.corpus.locale != LOCALE
        || manifest.corpus.license != SOURCE_LICENSE
        || manifest.corpus.claim_scope != CLAIM_SCOPE
        || manifest.corpus.generator_id != GENERATOR_ID
        || manifest.corpus.oracle_origin != ORACLE_ORIGIN
        || manifest.corpus.specification_sha256 != SPECIFICATION_SHA256
        || manifest.corpus.generator_sha256 != GENERATOR_SHA256
        || manifest.contract.source_id != "home-assistant-core-2026.8.3"
        || manifest.contract.commit != "759e4658f40b3ccb671d418b8a0ed95224bf4561"
        || manifest.contract.path != "homeassistant/helpers/intent.py"
        || manifest.contract.sha256
            != "8a62d1ab08d66a60a6c397bbb4d0b0ef12770c924ef8fd4ecffd690143703f9f"
        || manifest.contract.allowed_use != "public_intent_contract_only"
        || manifest.freeze.state != "FROZEN_PRE_IMPLEMENTATION"
        || manifest.freeze.sequence != "P02_CANDIDATE_1_PRE_ENGINE"
        || !manifest.freeze.before_nlu_implementation
        || !manifest.freeze.family_disjoint
        || !manifest.freeze.text_disjoint
        || !manifest.freeze.semantic_identity_disjoint
        || manifest.freeze.heldout_access_after_freeze != "aggregate_runner_only"
        || manifest.freeze.self_oracle_allowed
        || manifest.quotas.minimum_scored_cases != 3_715
        || manifest.quotas.minimum_per_supported_stratum != MINIMUM_SUPPORTED_STRATUM
        || manifest.quotas.train_per_intent != 48
        || manifest.quotas.development_per_intent != 48
        || manifest.quotas.heldout_per_intent != 240
        || manifest.quotas.performance_per_intent != 240
        || manifest.quotas.pos_per_split != 80
        || manifest.quotas.weighting != "unweighted"
        || manifest
            .taxonomies
            .dimensions
            .iter()
            .map(String::as_str)
            .ne(DIMENSIONS)
        || manifest
            .taxonomies
            .intents
            .iter()
            .map(String::as_str)
            .ne(OFFICIAL_INTENTS)
    {
        return Err(invalid_manifest("manifest identity"));
    }

    let expected_artifacts = expected_artifacts();
    let actual = manifest
        .artifacts
        .iter()
        .map(|artifact| (artifact.path.as_str(), artifact))
        .collect::<BTreeMap<_, _>>();
    if actual.len() != expected_artifacts.len() {
        return Err(invalid_manifest("manifest artifact inventory"));
    }
    for expected in expected_artifacts {
        let observed = actual
            .get(expected.path.as_str())
            .ok_or_else(|| invalid_manifest("manifest artifact inventory"))?;
        if observed.bytes != expected.bytes
            || observed.sha256 != expected.sha256
            || observed.records != expected.records
        {
            return Err(invalid_manifest("manifest artifact identity"));
        }
    }

    let expected_suite_counts = BTreeMap::from([
        ("ambiguity", 5_usize),
        ("contradiction", 5),
        ("explicit_negative", 7),
        ("safety_sensitive", 5),
        ("stale_state", 5),
    ]);
    if manifest.taxonomies.suite_classes.len() != expected_suite_counts.len()
        || expected_suite_counts.iter().any(|(suite, count)| {
            manifest
                .taxonomies
                .suite_classes
                .get(*suite)
                .is_none_or(|classes| {
                    classes.len() != *count
                        || classes.iter().collect::<BTreeSet<_>>().len() != *count
                })
        })
    {
        return Err(invalid_manifest("manifest suite taxonomy"));
    }
    Ok(())
}

fn expected_artifacts() -> Vec<ManifestArtifact> {
    vec![
        artifact(
            "development.jsonl",
            1_300_259,
            "75400570ddfc7196ed982da98dcf49c4e6bda5820200a2fefd93a8ea6f049161",
            960,
        ),
        artifact(
            "heldout.jsonl",
            6_389_947,
            HELDOUT_SHA256,
            RECORDS_PER_RELEASE_SPLIT as u64,
        ),
        artifact(
            "lexicon.jsonl",
            9_835,
            "727e89195fc2ed16489c24b17841d58a2a9bb68c828769ece83ab70489316e4e",
            33,
        ),
        artifact(
            "morphology.jsonl",
            9_468,
            "ebee221611e4cbf6206a755022d163e4c96f7eb1a42626d7032773bfb8c793dc",
            29,
        ),
        artifact(
            "performance.jsonl",
            6_461_467,
            PERFORMANCE_SHA256,
            RECORDS_PER_RELEASE_SPLIT as u64,
        ),
        artifact(
            "pos-context.jsonl",
            222_999,
            "85ad18caf3ae749d3ec0135c3c01ce6d754f831d395abdc44ff1c3643a18fac8",
            241,
        ),
        artifact("suites/ambiguity.jsonl", 3_663, SUITES[0].sha256, 5),
        artifact("suites/contradiction.jsonl", 3_792, SUITES[1].sha256, 5),
        artifact("suites/explicit-negative.jsonl", 5_350, SUITES[2].sha256, 7),
        artifact("suites/safety-sensitive.jsonl", 3_751, SUITES[3].sha256, 5),
        artifact("suites/stale-state.jsonl", 3_749, SUITES[4].sha256, 5),
        artifact(
            "train.jsonl",
            1_257_443,
            "23d2bc8c8fcde80d1a9560d42219484bc34e9198c791ccadf5d4b56413a81b64",
            960,
        ),
    ]
}

fn artifact(path: &str, bytes: u64, sha256: &str, records: u64) -> ManifestArtifact {
    ManifestArtifact {
        path: path.to_owned(),
        bytes,
        sha256: sha256.to_owned(),
        records,
    }
}

fn validate_p09(projection: &P09Projection) -> Result<()> {
    if projection.schema_version != 1
        || projection.projection_id != "p09-pre-resolution-oracle-projection-v1"
        || projection.schema_id != "p09-intent-schema-v1"
        || projection.source.source_id != SOURCE_ID
        || projection.source.corpus_version != CORPUS_VERSION
        || projection.source.generator_id != GENERATOR_ID
        || projection.source.oracle_origin != ORACLE_ORIGIN
        || projection.source.specification_sha256 != SPECIFICATION_SHA256
        || projection.source.generator_sha256 != GENERATOR_SHA256
        || projection.source.development_sha256
            != "75400570ddfc7196ed982da98dcf49c4e6bda5820200a2fefd93a8ea6f049161"
        || projection.source.development_records != 960
        || projection.source.claim_scope != CLAIM_SCOPE
        || projection.span_derivation.algorithm != "p02-generator-parameter-exact-utf8-substring-v1"
        || !projection.span_derivation.require_unique_match
        || projection.span_derivation.source_output_allowed
        || projection.span_derivation.normalization_allowed
        || projection.intent_projections.len() != OFFICIAL_INTENTS.len()
    {
        return Err(invalid_projection("P09 projection identity"));
    }

    let mut external = BTreeSet::new();
    let mut internal = BTreeSet::new();
    for intent in &projection.intent_projections {
        IntentId::new(&intent.intent_id)
            .map_err(|_| invalid_projection("P09 intent projection"))?;
        if !OFFICIAL_INTENTS.contains(&intent.external_intent.as_str())
            || !external.insert(intent.external_intent.as_str())
            || !internal.insert(intent.intent_id.as_str())
            || intent.slots.is_empty()
        {
            return Err(invalid_projection("P09 intent projection"));
        }
        validate_slot_projections(intent)?;
    }
    Ok(())
}

fn validate_slot_projections(intent: &IntentProjection) -> Result<()> {
    let mut keys = BTreeSet::new();
    for slot in &intent.slots {
        SlotId::new(&slot.slot_id).map_err(|_| invalid_projection("P09 slot projection"))?;
        if slot.expected_slot_id != slot.slot_id
            || !matches!(slot.expected_kind.as_str(), "entity" | "integer" | "text")
            || slot.role.is_empty()
            || !keys.insert((
                slot.expected_slot_id.as_str(),
                slot.expected_kind.as_str(),
                slot.occurrence,
            ))
            || !matches!(
                slot.parameter.as_str(),
                "target" | "target2" | "position" | "minutes" | "scope" | "message"
            )
            || !matches!(
                (slot.expected_kind.as_str(), slot.transform.as_str()),
                ("entity" | "text", "evidence_text")
                    | ("integer", "integer" | "minutes_to_seconds")
            )
        {
            return Err(invalid_projection("P09 slot projection"));
        }
    }
    Ok(())
}

fn validate_p11(projection: &P11Projection) -> Result<()> {
    const LIMITATIONS: [&str; 5] = [
        "project_authored_internal_conformance_not_independent_accuracy",
        "typed_evidence_is_a_pre_composer_mechanical_projection",
        "development_results_do_not_authorize_runtime_rule_changes",
        "heldout_not_accessed",
        "graph_class_does_not_authorize_execution",
    ];
    if projection.schema_version != 1
        || projection.projection_id != "p11-semantic-plan-oracle-projection-v1"
        || projection.semantic_plan_schema != "p11-semantic-plan-v1"
        || projection.source.source_id != SOURCE_ID
        || projection.source.corpus_version != CORPUS_VERSION
        || projection.source.generator_id != GENERATOR_ID
        || projection.source.oracle_origin != ORACLE_ORIGIN
        || projection.source.specification_sha256 != SPECIFICATION_SHA256
        || projection.source.generator_sha256 != GENERATOR_SHA256
        || projection.source.train_sha256
            != "23d2bc8c8fcde80d1a9560d42219484bc34e9198c791ccadf5d4b56413a81b64"
        || projection.source.development_sha256
            != "75400570ddfc7196ed982da98dcf49c4e6bda5820200a2fefd93a8ea6f049161"
        || projection.source.records_per_split != 960
        || projection.source.claim_scope != CLAIM_SCOPE
        || projection.pre_resolution_projection.path != P09_PATH
        || projection.pre_resolution_projection.sha256 != P09_SHA256
        || projection.catalog_projection.generation != 1
        || projection.catalog_projection.registry_id_algorithm
            != "sha256(p11-catalog-v1-nul || external_entity_id_utf8)[0:32]"
        || projection.catalog_projection.core_entity_id_prefix != "ha_entity:id_"
        || projection.catalog_projection.mention_source
            != "pre_resolution_parameter_exact_utf8_span"
        || projection.catalog_projection.display_matching_allowed
        || projection.catalog_projection.nlu_output_allowed
        || projection.evidence_projection.argument != "pre_resolution_parameter_exact_utf8_span"
        || projection.evidence_projection.single_predicate
            != "trimmed_initial_template_literal_before_first_parameter"
        || projection.evidence_projection.parallel_predicate
            != "shared_trimmed_initial_template_literal_before_first_parameter"
        || projection.evidence_projection.normalization_allowed
        || projection.evidence_projection.nlu_output_allowed
        || projection
            .limitations
            .iter()
            .map(String::as_str)
            .ne(LIMITATIONS)
        || projection.graph_shapes.len() != 3
    {
        return Err(invalid_projection("P11 projection identity"));
    }
    let shapes = projection
        .graph_shapes
        .iter()
        .map(|shape| (shape.shape.as_str(), shape))
        .collect::<BTreeMap<_, _>>();
    if shapes.len() != 3 {
        return Err(invalid_projection("P11 graph shape inventory"));
    }
    validate_graph_shape(
        shapes
            .get("single")
            .ok_or_else(|| invalid_projection("P11 single shape"))?,
        1,
        "partial_safe",
    )?;
    validate_graph_shape(
        shapes
            .get("parallel_pair")
            .ok_or_else(|| invalid_projection("P11 parallel shape"))?,
        2,
        "atomic_only",
    )?;
    validate_graph_shape(
        shapes
            .get("ordered_pair")
            .ok_or_else(|| invalid_projection("P11 ordered shape"))?,
        2,
        "partial_safe",
    )?;
    Ok(())
}

fn validate_graph_shape(
    shape: &GraphShapeProjection,
    node_count: usize,
    execution_class: &str,
) -> Result<()> {
    if shape.node_ids.len() != node_count
        || shape.execution_class != execution_class
        || shape
            .node_ids
            .iter()
            .enumerate()
            .any(|(index, id)| id != &format!("p11:node_{}", index + 1))
    {
        return Err(invalid_projection("P11 graph shape"));
    }
    match shape.shape.as_str() {
        "single" => {
            if shape.required_external_intent.is_some()
                || shape.slot_occurrences.is_some()
                || !shape.relations.is_empty()
                || !shape.independent_pairs.is_empty()
                || !shape.argument_shares.is_empty()
                || shape.secondary_capability.is_some()
                || shape.secondary_operation.is_some()
                || shape.secondary_predicate_cues.is_some()
                || shape.relation_cues.is_some()
            {
                return Err(invalid_projection("P11 single shape"));
            }
        }
        "parallel_pair" => {
            if shape.required_external_intent.as_deref() != Some("HassTurnOn")
                || shape.slot_occurrences.as_deref() != Some(&[0, 1])
                || !shape.relations.is_empty()
                || shape.independent_pairs.len() != 1
                || !shape.argument_shares.is_empty()
            {
                return Err(invalid_projection("P11 parallel shape"));
            }
        }
        "ordered_pair" => {
            if shape.required_external_intent.as_deref() != Some("HassStartTimer")
                || shape.slot_occurrences.is_some()
                || shape.secondary_capability.as_deref() != Some("ha:timer_control")
                || shape.secondary_operation.as_deref() != Some("ha:timer_status")
                || shape.secondary_predicate_cues.as_ref().is_none_or(|cues| {
                    cues.train != "consulte o estado" || cues.development != "verifique o estado"
                })
                || shape.relation_cues.as_ref().is_none_or(|cues| {
                    cues.train != "e consulte o estado depois"
                        || cues.development != "e então verifique o estado"
                })
                || shape.relations.len() != 1
                || !shape.independent_pairs.is_empty()
                || shape.argument_shares.len() != 1
                || shape.argument_shares[0].evidence != "source_argument"
            {
                return Err(invalid_projection("P11 ordered shape"));
            }
        }
        _ => return Err(invalid_projection("P11 graph shape")),
    }
    Ok(())
}

fn validate_rows(
    rows: &[P02Row],
    split: EvaluationSplit,
    manifest: &Manifest,
    p09: &P09Projection,
) -> Result<()> {
    let mut case_ids = BTreeSet::new();
    let mut semantic_ids = BTreeSet::new();
    for (offset, row) in rows.iter().enumerate() {
        let intent_index = offset / 240;
        let index = (offset % 240) + 1;
        let external_intent = OFFICIAL_INTENTS
            .get(intent_index)
            .ok_or_else(|| invalid_dataset("release row order"))?;
        let contract = oracle::contract(external_intent)
            .ok_or_else(|| invalid_dataset("release generator contract"))?;
        let rendered = oracle::render(split, contract, index as u64)?;
        let expected_case_id = format!("p02-v1-{}-{}-{index:03}", split.code(), contract.slug);
        let expected_session = format!("p02:{}_{}_{index:03}", split.code(), contract.slug);
        let expected_family = format!("{}-{}-family-v1", split.code(), contract.slug);
        if row.schema_version != 1
            || row.case_id != expected_case_id
            || row.generator_record_id != format!("generator-{}", row.case_id)
            || !is_sha256(&row.canonical_semantic_id)
            || !case_ids.insert(row.case_id.as_str())
            || !semantic_ids.insert(row.canonical_semantic_id.as_str())
            || row.source_id != SOURCE_ID
            || row.corpus_version != CORPUS_VERSION
            || row.generator_id != GENERATOR_ID
            || row.oracle_origin != ORACLE_ORIGIN
            || row.license != SOURCE_LICENSE
            || row.locale != LOCALE
            || row.split != split.code()
            || row.utterance.as_bytes() != rendered.utterance.as_bytes()
            || hash_text(&row.utterance)? != row.utterance_sha256
            || row.context.catalog_generation != 1
            || row.context.session_snapshot_id != expected_session
            || row.dimensions.source != SOURCE_ID
            || row.dimensions.family != expected_family
            || row.dimensions.intent != *external_intent
            || row.dimensions.intent != row.expected.intent
            || row.dimensions.domain != contract.domain
            || row.dimensions.graph_shape != contract.graph_shape
            || row.dimensions.outcome != "plan"
            || row.dimensions.ambiguity != "unambiguous"
            || row.dimensions.noise != "clean_text"
            || row.expected.outcome != "plan"
            || row.expected.catalog_generation != 1
            || find_intent(p09, external_intent).is_none()
        {
            return Err(invalid_dataset("release row identity"));
        }
        validate_expected_plan(&row.expected, &row.dimensions.graph_shape)?;
    }

    let observed = dimension_counts(rows);
    let expected = match split {
        EvaluationSplit::Heldout => &manifest.taxonomies.heldout_counts,
        EvaluationSplit::Performance => &manifest.taxonomies.performance_counts,
    };
    reconcile_dimension_denominators(&observed, expected)
}

fn reconcile_dimension_denominators(
    observed: &BTreeMap<String, BTreeMap<String, u64>>,
    expected: &BTreeMap<String, BTreeMap<String, u64>>,
) -> Result<()> {
    if observed != expected {
        return Err(invalid_dataset("release dimension denominators"));
    }
    Ok(())
}

fn validate_expected_plan(plan: &P02ExpectedPlan, shape: &str) -> Result<()> {
    let expected_nodes = match shape {
        "single" => 1,
        "parallel_pair" | "ordered_pair" => 2,
        _ => return Err(invalid_dataset("expected graph shape")),
    };
    if plan.nodes.len() != expected_nodes {
        return Err(invalid_dataset("expected node count"));
    }
    for (index, node) in plan.nodes.iter().enumerate() {
        if node.id != format!("p02:node_{}", index + 1)
            || node.slots.is_empty()
            || CapabilityId::new(&node.capability).is_err()
            || OperationId::new(&node.operation).is_err()
        {
            return Err(invalid_dataset("expected node"));
        }
        let mut slots = BTreeSet::new();
        for slot in &node.slots {
            validate_expected_slot(slot)?;
            if !slots.insert((slot.id.as_str(), slot.kind.as_str())) {
                return Err(invalid_dataset("expected slot inventory"));
            }
        }
    }
    match shape {
        "ordered_pair" => {
            if plan.relations.len() != 1
                || plan.relations[0].from != "p02:node_1"
                || plan.relations[0].to != "p02:node_2"
                || plan.relations[0].kind != "precedes"
            {
                return Err(invalid_dataset("expected relation"));
            }
        }
        _ if !plan.relations.is_empty() => {
            return Err(invalid_dataset("expected relation inventory"));
        }
        _ => {}
    }
    Ok(())
}

fn validate_expected_slot(slot: &P02ExpectedSlot) -> Result<()> {
    SlotId::new(&slot.id).map_err(|_| invalid_dataset("expected slot"))?;
    match slot.kind.as_str() {
        "integer" if slot.value.as_i64().is_some() => Ok(()),
        "text" if slot.value.as_str().is_some() => Ok(()),
        "entity" => {
            let entity = slot
                .value
                .as_object()
                .ok_or_else(|| invalid_dataset("expected entity slot"))?;
            if entity.len() != 2
                || entity
                    .get("id")
                    .and_then(serde_json::Value::as_str)
                    .is_none()
                || entity
                    .get("catalog_generation")
                    .and_then(serde_json::Value::as_u64)
                    != Some(1)
            {
                return Err(invalid_dataset("expected entity slot"));
            }
            Ok(())
        }
        _ => Err(invalid_dataset("expected slot value")),
    }
}

fn validate_negative_rows(
    rows: &[NegativeRow],
    suite: SuiteSpec,
    manifest: &Manifest,
) -> Result<()> {
    let expected_classes = manifest
        .taxonomies
        .suite_classes
        .get(suite.id)
        .ok_or_else(|| invalid_manifest("negative suite taxonomy"))?;
    let mut classes = BTreeSet::new();
    let mut case_ids = BTreeSet::new();
    let mut semantic_ids = BTreeSet::new();
    for (offset, row) in rows.iter().enumerate() {
        let index = offset + 1;
        let expected_case = format!("p02-v1-suite-{}-{index:03}", suite.id.replace('_', "-"));
        if row.schema_version != 1
            || row.suite_id != suite.id
            || row.case_id != expected_case
            || row.generator_record_id != format!("generator-{}", row.case_id)
            || !is_sha256(&row.canonical_semantic_id)
            || !case_ids.insert(row.case_id.as_str())
            || !semantic_ids.insert(row.canonical_semantic_id.as_str())
            || !classes.insert(row.coverage_class.as_str())
            || row.source_id != SOURCE_ID
            || row.corpus_version != CORPUS_VERSION
            || row.generator_id != GENERATOR_ID
            || row.oracle_origin != ORACLE_ORIGIN
            || row.license != SOURCE_LICENSE
            || row.locale != LOCALE
            || hash_text(&row.utterance)? != row.utterance_sha256
            || row.context.condition != row.coverage_class
            || row.context.catalog_generation != suite.catalog_generation
            || row.expected.outcome != suite.expected_outcome
            || row.expected.reason.is_empty()
        {
            return Err(invalid_dataset("negative suite identity"));
        }
    }
    if classes
        != expected_classes
            .iter()
            .map(String::as_str)
            .collect::<BTreeSet<_>>()
    {
        return Err(invalid_dataset("negative suite denominator"));
    }
    Ok(())
}

fn dimension_counts(rows: &[P02Row]) -> BTreeMap<String, BTreeMap<String, u64>> {
    let mut result = BTreeMap::<String, BTreeMap<String, u64>>::new();
    for row in rows {
        for (dimension, value) in row.dimensions.frozen_values().into_iter().chain([(
            "target_cardinality",
            row.dimensions.target_cardinality.as_str(),
        )]) {
            *result
                .entry(dimension.to_owned())
                .or_default()
                .entry(value.to_owned())
                .or_default() += 1;
        }
    }
    result
}

pub(crate) fn find_intent<'a>(
    projection: &'a P09Projection,
    external_intent: &str,
) -> Option<&'a IntentProjection> {
    projection
        .intent_projections
        .iter()
        .find(|intent| intent.external_intent == external_intent)
}

pub(crate) fn find_shape<'a>(
    projection: &'a P11Projection,
    shape: &str,
) -> Option<&'a GraphShapeProjection> {
    projection
        .graph_shapes
        .iter()
        .find(|candidate| candidate.shape == shape)
}

pub(crate) fn projected_slot<'a>(
    projection: &'a IntentProjection,
    slot_id: &str,
    kind: &str,
    occurrence: Option<u16>,
) -> Result<&'a SlotProjection> {
    let mut candidates = projection.slots.iter().filter(|slot| {
        slot.expected_slot_id == slot_id
            && slot.expected_kind == kind
            && occurrence.is_none_or(|value| slot.occurrence == value)
    });
    let first = candidates
        .next()
        .ok_or_else(|| invalid_projection("missing slot projection"))?;
    if candidates.next().is_some() {
        return Err(invalid_projection("ambiguous slot projection"));
    }
    Ok(first)
}

fn hash_text(value: &str) -> Result<String> {
    sha256_hex(value.as_bytes()).map_err(|_| invalid_dataset("text digest"))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_mutation_is_rejected_without_disclosing_input() {
        let mut bytes = b"FIXTURE_TECNICA_HASH_BOUND_INPUT".to_vec();
        let expected =
            sha256_hex(&bytes).expect("FIXTURE_TECNICA expected mechanical fixture digest");
        bytes[0] ^= 1;
        let error = verify_hash(&bytes, &expected, "FIXTURE_TECNICA hash-bound bytes")
            .expect_err("FIXTURE_TECNICA mutation rejection");
        assert_eq!(error.code(), crate::ReleaseEvalErrorCode::HashMismatch);
        assert!(!error.to_string().contains("HASH_BOUND_INPUT"));
    }

    #[test]
    fn denominator_mutation_is_rejected() {
        let observed = BTreeMap::from([(
            "FIXTURE_TECNICA_DIMENSION".to_owned(),
            BTreeMap::from([("FIXTURE_TECNICA_VALUE".to_owned(), 2)]),
        )]);
        let mut expected = observed.clone();
        *expected
            .get_mut("FIXTURE_TECNICA_DIMENSION")
            .expect("FIXTURE_TECNICA dimension")
            .get_mut("FIXTURE_TECNICA_VALUE")
            .expect("FIXTURE_TECNICA denominator") += 1;
        assert_eq!(
            reconcile_dimension_denominators(&observed, &expected)
                .expect_err("FIXTURE_TECNICA denominator rejection")
                .code(),
            crate::ReleaseEvalErrorCode::InvalidDataset
        );
    }
}
