use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{
    DataError, DataErrorCode, Result,
    hash::sha256_hex,
    json::{canonical_json, parse_strict_json},
};

pub const INTENT_PACKAGE_MAGIC: &[u8; 7] = b"NLUINT\0";
pub const INTENT_PACKAGE_VERSION: u8 = 1;
pub const INTENT_PACKAGE_FORMAT: &str = "nlu-intent-schema-package-v1";
pub const INTENT_COMPILER_ID: &str = "nlu-data-intent-compiler-v1";
pub const MAX_INTENT_SCHEMA_BYTES: usize = 128 * 1024;
pub const MAX_INTENT_PACKAGE_BYTES: usize = 128 * 1024;
pub const MAX_INTENT_MANIFEST_BYTES: usize = 32 * 1024;
pub const MAX_INTENTS: usize = 32;
pub const MAX_MARKERS_PER_INTENT: usize = 16;
pub const MAX_SLOTS_PER_INTENT: usize = 8;
pub const MAX_MARKER_BYTES: usize = 64;
pub const MAX_ROLE_BYTES: usize = 64;

const HEADER_BYTES: usize = 12;
const FIXED_RANDOM_SEED: u64 = 0;
const RANDOMNESS_USED: bool = false;
const MAX_ID_BYTES: usize = 128;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentSource {
    source_id: String,
    corpus_version: String,
    generator_id: String,
    license: String,
    locale: String,
    claim_scope: String,
    source_manifest_sha256: String,
    specification_sha256: String,
    generator_sha256: String,
    physical_train_sha256: String,
    physical_train_records: u64,
    linguistic_input: String,
}

impl IntentSource {
    #[must_use]
    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    #[must_use]
    pub fn corpus_version(&self) -> &str {
        &self.corpus_version
    }

    #[must_use]
    pub fn generator_id(&self) -> &str {
        &self.generator_id
    }

    #[must_use]
    pub fn license(&self) -> &str {
        &self.license
    }

    #[must_use]
    pub fn locale(&self) -> &str {
        &self.locale
    }

    #[must_use]
    pub fn claim_scope(&self) -> &str {
        &self.claim_scope
    }

    #[must_use]
    pub fn source_manifest_sha256(&self) -> &str {
        &self.source_manifest_sha256
    }

    #[must_use]
    pub fn specification_sha256(&self) -> &str {
        &self.specification_sha256
    }

    #[must_use]
    pub fn generator_sha256(&self) -> &str {
        &self.generator_sha256
    }

    #[must_use]
    pub fn physical_train_sha256(&self) -> &str {
        &self.physical_train_sha256
    }

    #[must_use]
    pub const fn physical_train_records(&self) -> u64 {
        self.physical_train_records
    }

    #[must_use]
    pub fn linguistic_input(&self) -> &str {
        &self.linguistic_input
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentRanking {
    score_maximum: u16,
    ambiguity_margin: u16,
    maximum_candidates: u16,
    maximum_slots_per_candidate: u16,
}

impl IntentRanking {
    #[must_use]
    pub const fn score_maximum(&self) -> u16 {
        self.score_maximum
    }

    #[must_use]
    pub const fn ambiguity_margin(&self) -> u16 {
        self.ambiguity_margin
    }

    #[must_use]
    pub const fn maximum_candidates(&self) -> u16 {
        self.maximum_candidates
    }

    #[must_use]
    pub const fn maximum_slots_per_candidate(&self) -> u16 {
        self.maximum_slots_per_candidate
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentMarker {
    text: String,
    weight: u16,
}

impl IntentMarker {
    #[must_use]
    pub fn text(&self) -> &str {
        &self.text
    }

    #[must_use]
    pub const fn weight(&self) -> u16 {
        self.weight
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum IntentValueKind {
    Mention,
    Integer,
    Text,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum SlotExtractor {
    AnchoredTokens {
        anchor: String,
        anchor_occurrence: u16,
        token_count: u16,
    },
    TokensBeforeAnchor {
        anchor: String,
        anchor_occurrence: u16,
        skip_tokens: u16,
        token_count: u16,
    },
    LastToken,
    LastTokens {
        token_count: u16,
    },
    NumberAfterCapture {
        after_role: String,
        after_occurrence: u16,
        scale: i64,
    },
}

impl SlotExtractor {
    #[must_use]
    pub fn anchor(&self) -> Option<&str> {
        match self {
            Self::AnchoredTokens { anchor, .. } | Self::TokensBeforeAnchor { anchor, .. } => {
                Some(anchor)
            }
            Self::LastToken | Self::LastTokens { .. } | Self::NumberAfterCapture { .. } => None,
        }
    }

    #[must_use]
    pub const fn anchor_occurrence(&self) -> Option<u16> {
        match self {
            Self::AnchoredTokens {
                anchor_occurrence, ..
            }
            | Self::TokensBeforeAnchor {
                anchor_occurrence, ..
            } => Some(*anchor_occurrence),
            Self::LastToken | Self::LastTokens { .. } | Self::NumberAfterCapture { .. } => None,
        }
    }

    #[must_use]
    pub const fn token_count(&self) -> Option<u16> {
        match self {
            Self::AnchoredTokens { token_count, .. }
            | Self::TokensBeforeAnchor { token_count, .. }
            | Self::LastTokens { token_count } => Some(*token_count),
            Self::LastToken | Self::NumberAfterCapture { .. } => None,
        }
    }

    #[must_use]
    pub const fn skip_tokens(&self) -> Option<u16> {
        match self {
            Self::TokensBeforeAnchor { skip_tokens, .. } => Some(*skip_tokens),
            _ => None,
        }
    }

    #[must_use]
    pub fn capture_reference(&self) -> Option<(&str, u16)> {
        match self {
            Self::NumberAfterCapture {
                after_role,
                after_occurrence,
                ..
            } => Some((after_role, *after_occurrence)),
            _ => None,
        }
    }

    #[must_use]
    pub const fn scale(&self) -> Option<i64> {
        match self {
            Self::NumberAfterCapture { scale, .. } => Some(*scale),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentSlotSchema {
    slot_id: String,
    role: String,
    occurrence: u16,
    value_kind: IntentValueKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    minimum: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    maximum: Option<i64>,
    extractor: SlotExtractor,
}

impl IntentSlotSchema {
    #[must_use]
    pub fn slot_id(&self) -> &str {
        &self.slot_id
    }

    #[must_use]
    pub fn role(&self) -> &str {
        &self.role
    }

    #[must_use]
    pub const fn occurrence(&self) -> u16 {
        self.occurrence
    }

    #[must_use]
    pub const fn value_kind(&self) -> IntentValueKind {
        self.value_kind
    }

    #[must_use]
    pub const fn minimum(&self) -> Option<i64> {
        self.minimum
    }

    #[must_use]
    pub const fn maximum(&self) -> Option<i64> {
        self.maximum
    }

    #[must_use]
    pub const fn extractor(&self) -> &SlotExtractor {
        &self.extractor
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentDefinition {
    external_intent: String,
    intent_id: String,
    evidence_threshold: u16,
    markers: Vec<IntentMarker>,
    slots: Vec<IntentSlotSchema>,
}

impl IntentDefinition {
    #[must_use]
    pub fn external_intent(&self) -> &str {
        &self.external_intent
    }

    #[must_use]
    pub fn intent_id(&self) -> &str {
        &self.intent_id
    }

    #[must_use]
    pub const fn evidence_threshold(&self) -> u16 {
        self.evidence_threshold
    }

    #[must_use]
    pub fn markers(&self) -> &[IntentMarker] {
        &self.markers
    }

    #[must_use]
    pub fn slots(&self) -> &[IntentSlotSchema] {
        &self.slots
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentSchema {
    schema_version: u32,
    schema_id: String,
    algorithm_id: String,
    configuration_id: String,
    source: IntentSource,
    ranking: IntentRanking,
    intents: Vec<IntentDefinition>,
}

impl IntentSchema {
    #[must_use]
    pub const fn schema_version(&self) -> u32 {
        self.schema_version
    }

    #[must_use]
    pub fn schema_id(&self) -> &str {
        &self.schema_id
    }

    #[must_use]
    pub fn algorithm_id(&self) -> &str {
        &self.algorithm_id
    }

    #[must_use]
    pub fn configuration_id(&self) -> &str {
        &self.configuration_id
    }

    #[must_use]
    pub const fn source(&self) -> &IntentSource {
        &self.source
    }

    #[must_use]
    pub const fn ranking(&self) -> &IntentRanking {
        &self.ranking
    }

    #[must_use]
    pub fn intents(&self) -> &[IntentDefinition] {
        &self.intents
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntentPackageIdentity {
    schema_id: String,
    algorithm_id: String,
    compiler_id: String,
    configuration_id: String,
}

impl IntentPackageIdentity {
    #[must_use]
    pub fn new(
        schema_id: impl Into<String>,
        algorithm_id: impl Into<String>,
        compiler_id: impl Into<String>,
        configuration_id: impl Into<String>,
    ) -> Self {
        Self {
            schema_id: schema_id.into(),
            algorithm_id: algorithm_id.into(),
            compiler_id: compiler_id.into(),
            configuration_id: configuration_id.into(),
        }
    }

    #[must_use]
    pub fn schema_id(&self) -> &str {
        &self.schema_id
    }

    #[must_use]
    pub fn algorithm_id(&self) -> &str {
        &self.algorithm_id
    }

    #[must_use]
    pub fn compiler_id(&self) -> &str {
        &self.compiler_id
    }

    #[must_use]
    pub fn configuration_id(&self) -> &str {
        &self.configuration_id
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntentPackageManifest {
    schema_version: u32,
    format: String,
    compiler_id: String,
    schema_id: String,
    algorithm_id: String,
    configuration_id: String,
    random_seed: u64,
    randomness_used: bool,
    source: IntentSource,
    schema_source_sha256: String,
    canonical_schema_sha256: String,
    intent_count: u64,
    marker_count: u64,
    slot_count: u64,
    package_bytes: u64,
    package_sha256: String,
}

impl IntentPackageManifest {
    #[must_use]
    pub fn compiler_id(&self) -> &str {
        &self.compiler_id
    }

    #[must_use]
    pub fn schema_source_sha256(&self) -> &str {
        &self.schema_source_sha256
    }

    #[must_use]
    pub fn canonical_schema_sha256(&self) -> &str {
        &self.canonical_schema_sha256
    }

    #[must_use]
    pub const fn intent_count(&self) -> u64 {
        self.intent_count
    }

    #[must_use]
    pub const fn marker_count(&self) -> u64 {
        self.marker_count
    }

    #[must_use]
    pub const fn slot_count(&self) -> u64 {
        self.slot_count
    }

    #[must_use]
    pub const fn source(&self) -> &IntentSource {
        &self.source
    }

    #[must_use]
    pub fn package_sha256(&self) -> &str {
        &self.package_sha256
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompiledIntentPackage {
    package_bytes: Vec<u8>,
    manifest_bytes: Vec<u8>,
}

impl CompiledIntentPackage {
    #[must_use]
    pub fn package_bytes(&self) -> &[u8] {
        &self.package_bytes
    }

    #[must_use]
    pub fn manifest_bytes(&self) -> &[u8] {
        &self.manifest_bytes
    }

    #[must_use]
    pub fn into_parts(self) -> (Vec<u8>, Vec<u8>) {
        (self.package_bytes, self.manifest_bytes)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DecodedIntentPackage {
    manifest: IntentPackageManifest,
    schema: IntentSchema,
}

impl DecodedIntentPackage {
    #[must_use]
    pub const fn manifest(&self) -> &IntentPackageManifest {
        &self.manifest
    }

    #[must_use]
    pub const fn schema(&self) -> &IntentSchema {
        &self.schema
    }
}

pub fn compile_intent_package(schema_source: &[u8]) -> Result<CompiledIntentPackage> {
    if schema_source.len() > MAX_INTENT_SCHEMA_BYTES {
        return resource_limit("intent schema source bytes");
    }
    let value = parse_strict_json(schema_source, "intent schema source")?;
    let mut schema: IntentSchema = serde_json::from_value(value).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("intent schema source shape: {error}"),
        )
    })?;
    canonicalize_schema(&mut schema);
    validate_schema(&schema)?;

    let canonical_schema = encode_schema(&schema)?;
    let package_bytes = encode_package(&canonical_schema)?;
    let manifest = build_manifest(&schema, schema_source, &canonical_schema, &package_bytes)?;
    let manifest_bytes = encode_manifest(&manifest)?;
    if manifest_bytes.len() > MAX_INTENT_MANIFEST_BYTES {
        return resource_limit("intent package manifest bytes");
    }
    Ok(CompiledIntentPackage {
        package_bytes,
        manifest_bytes,
    })
}

pub fn decode_intent_package(
    package: &[u8],
    manifest: &[u8],
    expected_identity: &IntentPackageIdentity,
) -> Result<DecodedIntentPackage> {
    if package.len() > MAX_INTENT_PACKAGE_BYTES {
        return resource_limit("intent package bytes");
    }
    if manifest.len() > MAX_INTENT_MANIFEST_BYTES {
        return resource_limit("intent package manifest bytes");
    }
    if !valid_identity(expected_identity) {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "invalid expected intent package identity",
        ));
    }

    let manifest_value = parse_strict_json(manifest, "intent package manifest")?;
    let decoded_manifest: IntentPackageManifest = serde_json::from_value(manifest_value.clone())
        .map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidManifest,
                format!("intent package manifest shape: {error}"),
            )
        })?;
    let mut canonical_manifest = canonical_json(&manifest_value, "intent package manifest")?;
    canonical_manifest.push(b'\n');
    if canonical_manifest != manifest {
        return invalid_manifest("noncanonical intent package manifest");
    }
    validate_manifest(&decoded_manifest, package, expected_identity)?;

    let canonical_schema = decode_package(package)?;
    let value = parse_strict_json(canonical_schema, "packaged intent schema")?;
    let schema: IntentSchema = serde_json::from_value(value.clone()).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("packaged intent schema shape: {error}"),
        )
    })?;
    let encoded = canonical_json(&value, "packaged intent schema")?;
    if encoded != canonical_schema {
        return invalid_record("noncanonical packaged intent schema");
    }
    validate_schema(&schema)?;
    let mut canonicalized = schema.clone();
    canonicalize_schema(&mut canonicalized);
    if canonicalized != schema {
        return invalid_record("noncanonical intent schema inventory");
    }
    validate_inventory(&decoded_manifest, &schema, canonical_schema)?;
    Ok(DecodedIntentPackage {
        manifest: decoded_manifest,
        schema,
    })
}

fn canonicalize_schema(schema: &mut IntentSchema) {
    for intent in &mut schema.intents {
        intent
            .markers
            .sort_by(|left, right| left.text.cmp(&right.text));
        intent.slots.sort_by(|left, right| {
            (&left.slot_id, &left.role, left.occurrence).cmp(&(
                &right.slot_id,
                &right.role,
                right.occurrence,
            ))
        });
    }
    schema
        .intents
        .sort_by(|left, right| left.intent_id.cmp(&right.intent_id));
}

fn validate_schema(schema: &IntentSchema) -> Result<()> {
    if schema.schema_version != 1
        || !valid_metadata_id(&schema.schema_id)
        || !valid_metadata_id(&schema.algorithm_id)
        || !valid_metadata_id(&schema.configuration_id)
        || !valid_source(&schema.source)
        || schema.ranking.score_maximum == 0
        || schema.ranking.score_maximum > 10_000
        || schema.ranking.ambiguity_margin > schema.ranking.score_maximum
        || schema.ranking.maximum_candidates == 0
        || usize::from(schema.ranking.maximum_candidates) > MAX_INTENTS
        || schema.ranking.maximum_slots_per_candidate == 0
        || usize::from(schema.ranking.maximum_slots_per_candidate) > MAX_SLOTS_PER_INTENT
        || schema.intents.is_empty()
        || schema.intents.len() > usize::from(schema.ranking.maximum_candidates)
    {
        return invalid_record("invalid intent schema metadata");
    }

    let mut external = BTreeSet::new();
    for intent in &schema.intents {
        if !external.insert(intent.external_intent.as_str())
            || !valid_external_intent(&intent.external_intent)
            || !valid_stable_id(&intent.intent_id)
            || intent.markers.is_empty()
            || intent.markers.len() > MAX_MARKERS_PER_INTENT
            || intent.slots.is_empty()
            || intent.slots.len() > usize::from(schema.ranking.maximum_slots_per_candidate)
        {
            return invalid_record("invalid intent definition");
        }
        validate_markers(intent, schema.ranking.score_maximum)?;
        validate_slots(intent)?;
    }
    if schema
        .intents
        .windows(2)
        .any(|pair| pair[0].intent_id >= pair[1].intent_id)
    {
        return invalid_record("duplicate or noncanonical intent identifiers");
    }
    Ok(())
}

fn validate_markers(intent: &IntentDefinition, score_maximum: u16) -> Result<()> {
    let mut total = 0_u32;
    for marker in &intent.markers {
        if !valid_marker(&marker.text) || marker.weight == 0 {
            return invalid_record("invalid intent marker");
        }
        total = total.checked_add(u32::from(marker.weight)).ok_or_else(|| {
            DataError::new(DataErrorCode::ResourceLimit, "intent marker score sum")
        })?;
    }
    if intent
        .markers
        .windows(2)
        .any(|pair| pair[0].text >= pair[1].text)
        || total > u32::from(score_maximum)
        || intent.evidence_threshold == 0
        || u32::from(intent.evidence_threshold) > total
    {
        return invalid_record("invalid marker order, score, or threshold");
    }
    Ok(())
}

fn validate_slots(intent: &IntentDefinition) -> Result<()> {
    let mut role_occurrences = BTreeSet::new();
    for slot in &intent.slots {
        if !valid_stable_id(&slot.slot_id)
            || !valid_role(&slot.role)
            || usize::from(slot.occurrence) >= MAX_SLOTS_PER_INTENT
            || !role_occurrences.insert((slot.role.as_str(), slot.occurrence))
        {
            return invalid_record("invalid intent slot identity");
        }
        match slot.value_kind {
            IntentValueKind::Integer => {
                let (Some(minimum), Some(maximum)) = (slot.minimum, slot.maximum) else {
                    return invalid_record("integer slot lacks bounds");
                };
                if minimum > maximum
                    || !matches!(slot.extractor, SlotExtractor::NumberAfterCapture { .. })
                {
                    return invalid_record("invalid integer slot constraint");
                }
            }
            IntentValueKind::Mention | IntentValueKind::Text => {
                if slot.minimum.is_some()
                    || slot.maximum.is_some()
                    || matches!(slot.extractor, SlotExtractor::NumberAfterCapture { .. })
                {
                    return invalid_record("invalid evidence-text slot constraint");
                }
            }
        }
        validate_extractor(intent, slot)?;
    }
    if intent.slots.windows(2).any(|pair| {
        (&pair[0].slot_id, &pair[0].role, pair[0].occurrence)
            >= (&pair[1].slot_id, &pair[1].role, pair[1].occurrence)
    }) {
        return invalid_record("duplicate or noncanonical intent slots");
    }
    Ok(())
}

fn validate_extractor(intent: &IntentDefinition, slot: &IntentSlotSchema) -> Result<()> {
    match &slot.extractor {
        SlotExtractor::AnchoredTokens {
            anchor,
            anchor_occurrence,
            token_count,
        } => {
            validate_anchor(intent, anchor)?;
            if usize::from(*anchor_occurrence) >= MAX_SLOTS_PER_INTENT
                || *token_count == 0
                || usize::from(*token_count) > MAX_SLOTS_PER_INTENT
            {
                return invalid_record("invalid anchored-token extractor");
            }
        }
        SlotExtractor::TokensBeforeAnchor {
            anchor,
            anchor_occurrence,
            skip_tokens,
            token_count,
        } => {
            validate_anchor(intent, anchor)?;
            if usize::from(*anchor_occurrence) >= MAX_SLOTS_PER_INTENT
                || *token_count == 0
                || usize::from(*token_count) > MAX_SLOTS_PER_INTENT
                || usize::from(*skip_tokens) > MAX_SLOTS_PER_INTENT
            {
                return invalid_record("invalid before-anchor extractor");
            }
        }
        SlotExtractor::LastToken => {}
        SlotExtractor::LastTokens { token_count } => {
            if *token_count == 0 || usize::from(*token_count) > MAX_SLOTS_PER_INTENT {
                return invalid_record("invalid trailing-token extractor");
            }
        }
        SlotExtractor::NumberAfterCapture {
            after_role,
            after_occurrence,
            scale,
        } => {
            if !valid_role(after_role) || *scale <= 0 {
                return invalid_record("invalid number extractor");
            }
            let referenced = intent.slots.iter().find(|candidate| {
                candidate.role == *after_role && candidate.occurrence == *after_occurrence
            });
            if !referenced.is_some_and(|candidate| {
                candidate.value_kind != IntentValueKind::Integer
                    && (candidate.role != slot.role || candidate.occurrence != slot.occurrence)
            }) {
                return invalid_record("dangling number extractor reference");
            }
        }
    }
    Ok(())
}

fn validate_anchor(intent: &IntentDefinition, anchor: &str) -> Result<()> {
    if !valid_marker(anchor)
        || intent
            .markers
            .binary_search_by(|marker| marker.text.as_str().cmp(anchor))
            .is_err()
    {
        return invalid_record("extractor anchor is not an intent marker");
    }
    Ok(())
}

fn build_manifest(
    schema: &IntentSchema,
    schema_source: &[u8],
    canonical_schema: &[u8],
    package: &[u8],
) -> Result<IntentPackageManifest> {
    let (intent_count, marker_count, slot_count) = inventory_counts(schema)?;
    Ok(IntentPackageManifest {
        schema_version: 1,
        format: INTENT_PACKAGE_FORMAT.to_owned(),
        compiler_id: INTENT_COMPILER_ID.to_owned(),
        schema_id: schema.schema_id.clone(),
        algorithm_id: schema.algorithm_id.clone(),
        configuration_id: schema.configuration_id.clone(),
        random_seed: FIXED_RANDOM_SEED,
        randomness_used: RANDOMNESS_USED,
        source: schema.source.clone(),
        schema_source_sha256: sha256_hex(schema_source)?,
        canonical_schema_sha256: sha256_hex(canonical_schema)?,
        intent_count,
        marker_count,
        slot_count,
        package_bytes: usize_to_u64(package.len(), "intent package bytes")?,
        package_sha256: sha256_hex(package)?,
    })
}

fn validate_manifest(
    manifest: &IntentPackageManifest,
    package: &[u8],
    expected: &IntentPackageIdentity,
) -> Result<()> {
    if manifest.schema_version != 1
        || manifest.format != INTENT_PACKAGE_FORMAT
        || manifest.compiler_id != INTENT_COMPILER_ID
        || manifest.random_seed != FIXED_RANDOM_SEED
        || manifest.randomness_used != RANDOMNESS_USED
        || !valid_source(&manifest.source)
        || !is_sha256(&manifest.schema_source_sha256)
        || !is_sha256(&manifest.canonical_schema_sha256)
        || !is_sha256(&manifest.package_sha256)
        || manifest.intent_count == 0
        || manifest.intent_count > MAX_INTENTS as u64
        || manifest.marker_count == 0
        || manifest.slot_count == 0
    {
        return invalid_manifest("invalid intent package manifest");
    }
    if manifest.schema_id != expected.schema_id
        || manifest.algorithm_id != expected.algorithm_id
        || manifest.compiler_id != expected.compiler_id
        || manifest.configuration_id != expected.configuration_id
    {
        return integrity_mismatch("intent package identity or configuration");
    }
    if manifest.package_bytes != usize_to_u64(package.len(), "intent package bytes")?
        || manifest.package_sha256 != sha256_hex(package)?
    {
        return integrity_mismatch("intent package size or SHA-256");
    }
    Ok(())
}

fn validate_inventory(
    manifest: &IntentPackageManifest,
    schema: &IntentSchema,
    canonical_schema: &[u8],
) -> Result<()> {
    let (intent_count, marker_count, slot_count) = inventory_counts(schema)?;
    if manifest.schema_id != schema.schema_id
        || manifest.algorithm_id != schema.algorithm_id
        || manifest.configuration_id != schema.configuration_id
        || manifest.source != schema.source
        || manifest.canonical_schema_sha256 != sha256_hex(canonical_schema)?
        || manifest.intent_count != intent_count
        || manifest.marker_count != marker_count
        || manifest.slot_count != slot_count
    {
        return integrity_mismatch("intent package schema inventory");
    }
    Ok(())
}

fn inventory_counts(schema: &IntentSchema) -> Result<(u64, u64, u64)> {
    let markers = schema
        .intents
        .iter()
        .try_fold(0_usize, |total, intent| {
            total.checked_add(intent.markers.len())
        })
        .ok_or_else(|| DataError::new(DataErrorCode::ResourceLimit, "intent marker count"))?;
    let slots = schema
        .intents
        .iter()
        .try_fold(0_usize, |total, intent| {
            total.checked_add(intent.slots.len())
        })
        .ok_or_else(|| DataError::new(DataErrorCode::ResourceLimit, "intent slot count"))?;
    Ok((
        usize_to_u64(schema.intents.len(), "intent count")?,
        usize_to_u64(markers, "intent marker count")?,
        usize_to_u64(slots, "intent slot count")?,
    ))
}

fn encode_schema(schema: &IntentSchema) -> Result<Vec<u8>> {
    let value = serde_json::to_value(schema).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("intent schema encoding: {error}"),
        )
    })?;
    canonical_json(&value, "intent schema")
}

fn encode_manifest(manifest: &IntentPackageManifest) -> Result<Vec<u8>> {
    let value = serde_json::to_value(manifest).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidManifest,
            format!("intent package manifest encoding: {error}"),
        )
    })?;
    let mut bytes = canonical_json(&value, "intent package manifest")?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn encode_package(canonical_schema: &[u8]) -> Result<Vec<u8>> {
    let mut output = Vec::with_capacity(
        HEADER_BYTES
            .checked_add(canonical_schema.len())
            .ok_or_else(|| {
                DataError::new(DataErrorCode::ResourceLimit, "intent package capacity")
            })?,
    );
    output.extend_from_slice(INTENT_PACKAGE_MAGIC);
    output.push(INTENT_PACKAGE_VERSION);
    output.extend_from_slice(
        &u32::try_from(canonical_schema.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "intent schema bytes"))?
            .to_be_bytes(),
    );
    output.extend_from_slice(canonical_schema);
    if output.len() > MAX_INTENT_PACKAGE_BYTES {
        return resource_limit("intent package bytes");
    }
    Ok(output)
}

fn decode_package(package: &[u8]) -> Result<&[u8]> {
    if package.len() < HEADER_BYTES {
        return invalid_record("truncated intent package header");
    }
    if package.get(..INTENT_PACKAGE_MAGIC.len()) != Some(INTENT_PACKAGE_MAGIC) {
        return invalid_record("invalid intent package magic");
    }
    if package[INTENT_PACKAGE_MAGIC.len()] != INTENT_PACKAGE_VERSION {
        return invalid_record("unsupported intent package version");
    }
    let length =
        usize::try_from(u32::from_be_bytes(package[8..12].try_into().map_err(
            |_| DataError::new(DataErrorCode::InvalidRecord, "intent schema length"),
        )?))
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "intent schema length"))?;
    if length == 0 || length > MAX_INTENT_SCHEMA_BYTES {
        return resource_limit("packaged intent schema bytes");
    }
    let end = HEADER_BYTES
        .checked_add(length)
        .ok_or_else(|| DataError::new(DataErrorCode::ResourceLimit, "intent schema end"))?;
    if end != package.len() {
        return invalid_record("truncated or trailing intent package bytes");
    }
    package
        .get(HEADER_BYTES..end)
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "truncated intent schema"))
}

fn valid_identity(identity: &IntentPackageIdentity) -> bool {
    valid_metadata_id(&identity.schema_id)
        && valid_metadata_id(&identity.algorithm_id)
        && identity.compiler_id == INTENT_COMPILER_ID
        && valid_metadata_id(&identity.configuration_id)
}

fn valid_source(source: &IntentSource) -> bool {
    [
        source.source_id.as_str(),
        source.corpus_version.as_str(),
        source.generator_id.as_str(),
        source.license.as_str(),
        source.locale.as_str(),
        source.claim_scope.as_str(),
        source.linguistic_input.as_str(),
    ]
    .into_iter()
    .all(valid_metadata_id)
        && [
            source.source_manifest_sha256.as_str(),
            source.specification_sha256.as_str(),
            source.generator_sha256.as_str(),
            source.physical_train_sha256.as_str(),
        ]
        .into_iter()
        .all(is_sha256)
        && source.physical_train_records > 0
}

fn valid_metadata_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_ID_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn valid_stable_id(value: &str) -> bool {
    let mut parts = value.split(':');
    let namespace = parts.next();
    let local = parts.next();
    parts.next().is_none()
        && namespace.is_some_and(valid_stable_component)
        && local.is_some_and(valid_stable_component)
}

fn valid_stable_component(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes[0].is_ascii_lowercase()
        && !matches!(bytes.last(), Some(b'-' | b'_'))
        && bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-' || *byte == b'_'
        })
}

fn valid_external_intent(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_ID_BYTES
        && value.as_bytes()[0].is_ascii_uppercase()
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

fn valid_role(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= MAX_ROLE_BYTES
        && bytes[0].is_ascii_lowercase()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
}

fn valid_marker(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_MARKER_BYTES
        && value.chars().all(|character| {
            !character.is_whitespace() && !character.is_control() && character != '\0'
        })
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn usize_to_u64(value: usize, context: &str) -> Result<u64> {
    u64::try_from(value)
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, context.to_owned()))
}

fn resource_limit<T>(context: &str) -> Result<T> {
    Err(DataError::new(DataErrorCode::ResourceLimit, context))
}

fn invalid_manifest<T>(context: &str) -> Result<T> {
    Err(DataError::new(DataErrorCode::InvalidManifest, context))
}

fn invalid_record<T>(context: &str) -> Result<T> {
    Err(DataError::new(DataErrorCode::InvalidRecord, context))
}

fn integrity_mismatch<T>(context: &str) -> Result<T> {
    Err(DataError::new(DataErrorCode::IntegrityMismatch, context))
}
