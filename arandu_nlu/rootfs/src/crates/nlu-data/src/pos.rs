use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{
    DataError, DataErrorCode, Result,
    hash::sha256_hex,
    json::{canonical_json, parse_strict_json},
};

pub const POS_PACKAGE_MAGIC: &[u8; 7] = b"NLUPOS\0";
pub const POS_PACKAGE_VERSION: u8 = 1;
pub const POS_PACKAGE_FORMAT: &str = "nlu-pos-transition-package-v1";
pub const MAX_POS_PACKAGE_BYTES: usize = 16 * 1024;
pub const MAX_POS_MANIFEST_BYTES: usize = 16 * 1024;
pub const MAX_POS_LABELS: usize = 8;
pub const MAX_POS_TRANSITIONS: usize = 100;
pub const MAX_POS_LABEL_BYTES: usize = 32;

const HEADER_BYTES: usize = 11;
const MAX_ID_BYTES: usize = 128;
const FIXED_RANDOM_SEED: u64 = 0;
const RANDOMNESS_USED: bool = false;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum PosEndpoint {
    Bos,
    Label(String),
    Eos,
}

impl PosEndpoint {
    #[must_use]
    pub fn label(value: impl Into<String>) -> Self {
        Self::Label(value.into())
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct PosTransition {
    from: PosEndpoint,
    to: PosEndpoint,
}

impl PosTransition {
    #[must_use]
    pub const fn new(from: PosEndpoint, to: PosEndpoint) -> Self {
        Self { from, to }
    }

    #[must_use]
    pub const fn from(&self) -> &PosEndpoint {
        &self.from
    }

    #[must_use]
    pub const fn to(&self) -> &PosEndpoint {
        &self.to
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PosModelIdentity {
    model_id: String,
    algorithm_id: String,
    compiler_id: String,
    config_id: String,
}

impl PosModelIdentity {
    #[must_use]
    pub fn new(
        model_id: impl Into<String>,
        algorithm_id: impl Into<String>,
        compiler_id: impl Into<String>,
        config_id: impl Into<String>,
    ) -> Self {
        Self {
            model_id: model_id.into(),
            algorithm_id: algorithm_id.into(),
            compiler_id: compiler_id.into(),
            config_id: config_id.into(),
        }
    }

    #[must_use]
    pub fn model_id(&self) -> &str {
        &self.model_id
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
    pub fn config_id(&self) -> &str {
        &self.config_id
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PosSourceIdentity {
    source_id: String,
    corpus_version: String,
    generator_id: String,
    source_license: String,
    locale: String,
    source_manifest_sha256: String,
    specification_sha256: String,
    generator_sha256: String,
    original_pos_artifact_sha256: String,
}

impl PosSourceIdentity {
    #[allow(clippy::too_many_arguments)]
    #[must_use]
    pub fn new(
        source_id: impl Into<String>,
        corpus_version: impl Into<String>,
        generator_id: impl Into<String>,
        source_license: impl Into<String>,
        locale: impl Into<String>,
        source_manifest_sha256: impl Into<String>,
        specification_sha256: impl Into<String>,
        generator_sha256: impl Into<String>,
        original_pos_artifact_sha256: impl Into<String>,
    ) -> Self {
        Self {
            source_id: source_id.into(),
            corpus_version: corpus_version.into(),
            generator_id: generator_id.into(),
            source_license: source_license.into(),
            locale: locale.into(),
            source_manifest_sha256: source_manifest_sha256.into(),
            specification_sha256: specification_sha256.into(),
            generator_sha256: generator_sha256.into(),
            original_pos_artifact_sha256: original_pos_artifact_sha256.into(),
        }
    }

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
    pub fn source_license(&self) -> &str {
        &self.source_license
    }

    #[must_use]
    pub fn locale(&self) -> &str {
        &self.locale
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
    pub fn original_pos_artifact_sha256(&self) -> &str {
        &self.original_pos_artifact_sha256
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PosTrainingIdentity {
    physical_train_slice_sha256: String,
    train_case_digest_sha256: String,
    train_document_digest_sha256: String,
    train_origin_digest_sha256: String,
    train_sentence_count: u64,
    train_document_count: u64,
    train_token_count: u64,
}

impl PosTrainingIdentity {
    #[allow(clippy::too_many_arguments)]
    #[must_use]
    pub fn new(
        physical_train_slice_sha256: impl Into<String>,
        train_case_digest_sha256: impl Into<String>,
        train_document_digest_sha256: impl Into<String>,
        train_origin_digest_sha256: impl Into<String>,
        train_sentence_count: u64,
        train_document_count: u64,
        train_token_count: u64,
    ) -> Self {
        Self {
            physical_train_slice_sha256: physical_train_slice_sha256.into(),
            train_case_digest_sha256: train_case_digest_sha256.into(),
            train_document_digest_sha256: train_document_digest_sha256.into(),
            train_origin_digest_sha256: train_origin_digest_sha256.into(),
            train_sentence_count,
            train_document_count,
            train_token_count,
        }
    }

    #[must_use]
    pub fn physical_train_slice_sha256(&self) -> &str {
        &self.physical_train_slice_sha256
    }

    #[must_use]
    pub fn train_case_digest_sha256(&self) -> &str {
        &self.train_case_digest_sha256
    }

    #[must_use]
    pub fn train_document_digest_sha256(&self) -> &str {
        &self.train_document_digest_sha256
    }

    #[must_use]
    pub fn train_origin_digest_sha256(&self) -> &str {
        &self.train_origin_digest_sha256
    }

    #[must_use]
    pub const fn train_sentence_count(&self) -> u64 {
        self.train_sentence_count
    }

    #[must_use]
    pub const fn train_document_count(&self) -> u64 {
        self.train_document_count
    }

    #[must_use]
    pub const fn train_token_count(&self) -> u64 {
        self.train_token_count
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PosCompileInput {
    identity: PosModelIdentity,
    source: PosSourceIdentity,
    training: PosTrainingIdentity,
    labels: Vec<String>,
    transitions: Vec<PosTransition>,
}

impl PosCompileInput {
    #[must_use]
    pub fn new(
        identity: PosModelIdentity,
        source: PosSourceIdentity,
        training: PosTrainingIdentity,
        labels: Vec<String>,
        transitions: Vec<PosTransition>,
    ) -> Self {
        Self {
            identity,
            source,
            training,
            labels,
            transitions,
        }
    }

    #[must_use]
    pub const fn identity(&self) -> &PosModelIdentity {
        &self.identity
    }

    #[must_use]
    pub const fn source(&self) -> &PosSourceIdentity {
        &self.source
    }

    #[must_use]
    pub const fn training(&self) -> &PosTrainingIdentity {
        &self.training
    }

    #[must_use]
    pub fn labels(&self) -> &[String] {
        &self.labels
    }

    #[must_use]
    pub fn transitions(&self) -> &[PosTransition] {
        &self.transitions
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PosManifest {
    schema_version: u32,
    format: String,
    identity: PosModelIdentity,
    random_seed: u64,
    randomness_used: bool,
    source: PosSourceIdentity,
    training: PosTrainingIdentity,
    label_count: u64,
    labels_sha256: String,
    transition_count: u64,
    transitions_sha256: String,
    package_bytes: u64,
    package_sha256: String,
}

impl PosManifest {
    #[must_use]
    pub const fn schema_version(&self) -> u32 {
        self.schema_version
    }

    #[must_use]
    pub fn format(&self) -> &str {
        &self.format
    }

    #[must_use]
    pub const fn identity(&self) -> &PosModelIdentity {
        &self.identity
    }

    #[must_use]
    pub const fn random_seed(&self) -> u64 {
        self.random_seed
    }

    #[must_use]
    pub const fn randomness_used(&self) -> bool {
        self.randomness_used
    }

    #[must_use]
    pub const fn source(&self) -> &PosSourceIdentity {
        &self.source
    }

    #[must_use]
    pub const fn training(&self) -> &PosTrainingIdentity {
        &self.training
    }

    #[must_use]
    pub const fn label_count(&self) -> u64 {
        self.label_count
    }

    #[must_use]
    pub fn labels_sha256(&self) -> &str {
        &self.labels_sha256
    }

    #[must_use]
    pub const fn transition_count(&self) -> u64 {
        self.transition_count
    }

    #[must_use]
    pub fn transitions_sha256(&self) -> &str {
        &self.transitions_sha256
    }

    #[must_use]
    pub const fn package_bytes(&self) -> u64 {
        self.package_bytes
    }

    #[must_use]
    pub fn package_sha256(&self) -> &str {
        &self.package_sha256
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompiledPosPackage {
    package_bytes: Vec<u8>,
    manifest_bytes: Vec<u8>,
}

impl CompiledPosPackage {
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
pub struct PosPackage {
    manifest: PosManifest,
    labels: Box<[String]>,
    transitions: Box<[PosTransition]>,
}

impl PosPackage {
    #[must_use]
    pub const fn manifest(&self) -> &PosManifest {
        &self.manifest
    }

    #[must_use]
    pub fn labels(&self) -> &[String] {
        &self.labels
    }

    #[must_use]
    pub fn transitions(&self) -> &[PosTransition] {
        &self.transitions
    }

    #[must_use]
    pub fn contains_transition(&self, transition: &PosTransition) -> bool {
        self.transitions.binary_search(transition).is_ok()
    }
}

pub fn compile_pos_transition_package(input: &PosCompileInput) -> Result<CompiledPosPackage> {
    validate_compile_metadata(input)?;
    if input.labels.len() > MAX_POS_LABELS {
        return resource_limit("POS label count");
    }
    if input.transitions.len() > MAX_POS_TRANSITIONS {
        return resource_limit("POS transition count");
    }
    if input
        .labels
        .iter()
        .any(|label| label.len() > MAX_POS_LABEL_BYTES)
    {
        return resource_limit("POS label bytes");
    }

    let mut labels = input.labels.clone();
    labels.sort();
    let mut transitions = input.transitions.clone();
    transitions.sort();
    validate_model(&labels, &transitions)?;

    let package_bytes = encode_package(&labels, &transitions)?;
    let manifest = build_manifest(input, &labels, &transitions, &package_bytes)?;
    let manifest_bytes = encode_manifest(&manifest)?;
    if manifest_bytes.len() > MAX_POS_MANIFEST_BYTES {
        return resource_limit("POS manifest bytes");
    }

    Ok(CompiledPosPackage {
        package_bytes,
        manifest_bytes,
    })
}

pub fn decode_pos_transition_package(
    package: &[u8],
    manifest: &[u8],
    expected_identity: &PosModelIdentity,
) -> Result<PosPackage> {
    if package.len() > MAX_POS_PACKAGE_BYTES {
        return resource_limit("POS package bytes");
    }
    if manifest.len() > MAX_POS_MANIFEST_BYTES {
        return resource_limit("POS manifest bytes");
    }
    if !valid_identity(expected_identity) {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "invalid expected POS model identity",
        ));
    }

    let manifest_value = parse_strict_json(manifest, "POS package manifest")?;
    let decoded_manifest: PosManifest =
        serde_json::from_value(manifest_value.clone()).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidManifest,
                format!("POS package manifest shape: {error}"),
            )
        })?;
    let mut canonical_manifest = canonical_json(&manifest_value, "POS package manifest")?;
    canonical_manifest.push(b'\n');
    if canonical_manifest != manifest {
        return invalid_manifest("noncanonical POS package manifest");
    }
    validate_manifest(&decoded_manifest, package, expected_identity)?;

    let (labels, transitions) = decode_package(package)?;
    validate_manifest_inventory(&decoded_manifest, &labels, &transitions)?;
    Ok(PosPackage {
        manifest: decoded_manifest,
        labels: labels.into_boxed_slice(),
        transitions: transitions.into_boxed_slice(),
    })
}

fn validate_compile_metadata(input: &PosCompileInput) -> Result<()> {
    if !valid_identity(&input.identity)
        || !valid_source(&input.source)
        || !valid_training(&input.training)
    {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "invalid POS compile metadata",
        ));
    }
    Ok(())
}

fn validate_manifest(
    manifest: &PosManifest,
    package: &[u8],
    expected_identity: &PosModelIdentity,
) -> Result<()> {
    if manifest.schema_version != 1
        || manifest.format != POS_PACKAGE_FORMAT
        || manifest.random_seed != FIXED_RANDOM_SEED
        || manifest.randomness_used != RANDOMNESS_USED
        || !valid_identity(&manifest.identity)
        || !valid_source(&manifest.source)
        || !valid_training(&manifest.training)
        || manifest.label_count == 0
        || manifest.label_count > MAX_POS_LABELS as u64
        || manifest.transition_count == 0
        || manifest.transition_count > MAX_POS_TRANSITIONS as u64
        || !is_sha256(&manifest.labels_sha256)
        || !is_sha256(&manifest.transitions_sha256)
        || !is_sha256(&manifest.package_sha256)
    {
        return invalid_manifest("invalid POS package manifest");
    }
    if manifest.identity != *expected_identity {
        return integrity_mismatch("POS model identity or configuration");
    }
    let package_bytes = u64::try_from(package.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS package size"))?;
    if manifest.package_bytes != package_bytes {
        return integrity_mismatch("POS package size");
    }
    if manifest.package_sha256 != sha256_hex(package)? {
        return integrity_mismatch("POS package SHA-256");
    }
    Ok(())
}

fn build_manifest(
    input: &PosCompileInput,
    labels: &[String],
    transitions: &[PosTransition],
    package: &[u8],
) -> Result<PosManifest> {
    Ok(PosManifest {
        schema_version: 1,
        format: POS_PACKAGE_FORMAT.to_owned(),
        identity: input.identity.clone(),
        random_seed: FIXED_RANDOM_SEED,
        randomness_used: RANDOMNESS_USED,
        source: input.source.clone(),
        training: input.training.clone(),
        label_count: u64::try_from(labels.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS label count"))?,
        labels_sha256: labels_sha256(labels)?,
        transition_count: u64::try_from(transitions.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS transition count"))?,
        transitions_sha256: transitions_sha256(labels, transitions)?,
        package_bytes: u64::try_from(package.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS package size"))?,
        package_sha256: sha256_hex(package)?,
    })
}

fn encode_manifest(manifest: &PosManifest) -> Result<Vec<u8>> {
    let value = serde_json::to_value(manifest).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidManifest,
            format!("POS package manifest encoding: {error}"),
        )
    })?;
    let mut bytes = canonical_json(&value, "POS package manifest")?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn validate_manifest_inventory(
    manifest: &PosManifest,
    labels: &[String],
    transitions: &[PosTransition],
) -> Result<()> {
    let label_count = u64::try_from(labels.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS label count"))?;
    let transition_count = u64::try_from(transitions.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS transition count"))?;
    if manifest.label_count != label_count
        || manifest.transition_count != transition_count
        || manifest.labels_sha256 != labels_sha256(labels)?
        || manifest.transitions_sha256 != transitions_sha256(labels, transitions)?
    {
        return integrity_mismatch("POS package inventory");
    }
    Ok(())
}

fn encode_package(labels: &[String], transitions: &[PosTransition]) -> Result<Vec<u8>> {
    validate_model(labels, transitions)?;
    let mut output = Vec::with_capacity(
        HEADER_BYTES
            .checked_add(labels.iter().map(|label| label.len() + 1).sum::<usize>())
            .and_then(|bytes| bytes.checked_add(transitions.len() * 2))
            .ok_or_else(|| DataError::new(DataErrorCode::ResourceLimit, "POS package capacity"))?,
    );
    output.extend_from_slice(POS_PACKAGE_MAGIC);
    output.push(POS_PACKAGE_VERSION);
    output.push(
        u8::try_from(labels.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS label count"))?,
    );
    output.extend_from_slice(
        &u16::try_from(transitions.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS transition count"))?
            .to_be_bytes(),
    );
    for label in labels {
        output.push(
            u8::try_from(label.len())
                .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS label bytes"))?,
        );
        output.extend_from_slice(label.as_bytes());
    }
    for transition in transitions {
        output.push(endpoint_code(transition.from(), labels)?);
        output.push(endpoint_code(transition.to(), labels)?);
    }
    if output.len() > MAX_POS_PACKAGE_BYTES {
        return resource_limit("POS package bytes");
    }
    Ok(output)
}

fn decode_package(package: &[u8]) -> Result<(Vec<String>, Vec<PosTransition>)> {
    if package.len() < HEADER_BYTES {
        return invalid_record("truncated POS package header");
    }
    if package.get(..POS_PACKAGE_MAGIC.len()) != Some(POS_PACKAGE_MAGIC) {
        return invalid_record("invalid POS package magic");
    }
    if package[POS_PACKAGE_MAGIC.len()] != POS_PACKAGE_VERSION {
        return invalid_record("unsupported POS package version");
    }

    let label_count = usize::from(package[8]);
    if label_count == 0 {
        return invalid_record("empty POS label inventory");
    }
    if label_count > MAX_POS_LABELS {
        return resource_limit("POS label count");
    }
    let transition_count =
        usize::from(u16::from_be_bytes(package[9..11].try_into().map_err(
            |_| DataError::new(DataErrorCode::InvalidRecord, "POS transition count"),
        )?));
    if transition_count == 0 {
        return invalid_record("empty POS transition inventory");
    }
    if transition_count > MAX_POS_TRANSITIONS {
        return resource_limit("POS transition count");
    }

    let mut offset = HEADER_BYTES;
    let mut labels: Vec<String> = Vec::with_capacity(label_count);
    for _ in 0..label_count {
        let length = usize::from(*package.get(offset).ok_or_else(|| {
            DataError::new(DataErrorCode::InvalidRecord, "truncated POS label length")
        })?);
        offset = offset.checked_add(1).ok_or_else(|| {
            DataError::new(DataErrorCode::ResourceLimit, "POS label length offset")
        })?;
        if length == 0 {
            return invalid_record("empty POS label");
        }
        if length > MAX_POS_LABEL_BYTES {
            return resource_limit("POS label bytes");
        }
        let end = offset
            .checked_add(length)
            .ok_or_else(|| DataError::new(DataErrorCode::ResourceLimit, "POS label byte offset"))?;
        let bytes = package
            .get(offset..end)
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "truncated POS label"))?;
        let label = std::str::from_utf8(bytes)
            .map_err(|_| DataError::new(DataErrorCode::InvalidRecord, "invalid UTF-8 POS label"))?;
        if !valid_label(label) {
            return invalid_record("invalid POS label");
        }
        if labels
            .last()
            .is_some_and(|previous| previous.as_str() >= label)
        {
            return invalid_record("noncanonical or duplicate POS labels");
        }
        labels.push(label.to_owned());
        offset = end;
    }

    let mut transitions = Vec::with_capacity(transition_count);
    for _ in 0..transition_count {
        let end = offset.checked_add(2).ok_or_else(|| {
            DataError::new(DataErrorCode::ResourceLimit, "POS transition byte offset")
        })?;
        let bytes = package.get(offset..end).ok_or_else(|| {
            DataError::new(DataErrorCode::InvalidRecord, "truncated POS transition")
        })?;
        let transition = PosTransition::new(
            decode_endpoint(bytes[0], &labels)?,
            decode_endpoint(bytes[1], &labels)?,
        );
        if transitions
            .last()
            .is_some_and(|previous| previous >= &transition)
        {
            return invalid_record("noncanonical or duplicate POS transitions");
        }
        transitions.push(transition);
        offset = end;
    }
    if offset != package.len() {
        return invalid_record("trailing POS package bytes");
    }
    validate_model(&labels, &transitions)?;
    Ok((labels, transitions))
}

fn validate_model(labels: &[String], transitions: &[PosTransition]) -> Result<()> {
    if labels.is_empty() {
        return invalid_record("empty POS label inventory");
    }
    if labels.len() > MAX_POS_LABELS {
        return resource_limit("POS label count");
    }
    if transitions.is_empty() {
        return invalid_record("empty POS transition inventory");
    }
    if transitions.len() > MAX_POS_TRANSITIONS {
        return resource_limit("POS transition count");
    }
    if labels.iter().any(|label| !valid_label(label))
        || labels.windows(2).any(|pair| pair[0] >= pair[1])
    {
        return invalid_record("invalid, duplicate, or noncanonical POS labels");
    }
    if transitions.windows(2).any(|pair| pair[0] >= pair[1]) {
        return invalid_record("duplicate or noncanonical POS transitions");
    }

    let mut has_bos = false;
    let mut has_eos = false;
    let mut used_labels = BTreeSet::new();
    for transition in transitions {
        validate_transition(transition, labels)?;
        if matches!(transition.from(), PosEndpoint::Bos) {
            has_bos = true;
        }
        if matches!(transition.to(), PosEndpoint::Eos) {
            has_eos = true;
        }
        for endpoint in [transition.from(), transition.to()] {
            if let PosEndpoint::Label(label) = endpoint {
                used_labels.insert(label.as_str());
            }
        }
    }
    if !has_bos || !has_eos || used_labels.len() != labels.len() {
        return invalid_record("incomplete POS transition boundaries or label inventory");
    }
    Ok(())
}

fn validate_transition(transition: &PosTransition, labels: &[String]) -> Result<()> {
    if matches!(transition.from(), PosEndpoint::Eos)
        || matches!(transition.to(), PosEndpoint::Bos)
        || matches!(
            (transition.from(), transition.to()),
            (PosEndpoint::Bos, PosEndpoint::Eos)
        )
    {
        return invalid_record("invalid POS boundary transition");
    }
    for endpoint in [transition.from(), transition.to()] {
        if let PosEndpoint::Label(label) = endpoint
            && labels
                .binary_search_by(|candidate| candidate.as_str().cmp(label))
                .is_err()
        {
            return invalid_record("POS transition references an unknown label");
        }
    }
    Ok(())
}

fn endpoint_code(endpoint: &PosEndpoint, labels: &[String]) -> Result<u8> {
    match endpoint {
        PosEndpoint::Bos => Ok(0),
        PosEndpoint::Label(label) => {
            let index = labels
                .binary_search_by(|candidate| candidate.as_str().cmp(label))
                .map_err(|_| {
                    DataError::new(
                        DataErrorCode::InvalidRecord,
                        "POS transition references an unknown label",
                    )
                })?;
            u8::try_from(index + 1)
                .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS endpoint index"))
        }
        PosEndpoint::Eos => u8::try_from(labels.len() + 1)
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS endpoint index")),
    }
}

fn decode_endpoint(code: u8, labels: &[String]) -> Result<PosEndpoint> {
    if code == 0 {
        return Ok(PosEndpoint::Bos);
    }
    let eos = u8::try_from(labels.len() + 1)
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS endpoint index"))?;
    if code == eos {
        return Ok(PosEndpoint::Eos);
    }
    let index = usize::from(code)
        .checked_sub(1)
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "invalid POS endpoint"))?;
    labels
        .get(index)
        .cloned()
        .map(PosEndpoint::Label)
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "invalid POS endpoint"))
}

fn labels_sha256(labels: &[String]) -> Result<String> {
    let mut bytes = Vec::new();
    bytes.push(
        u8::try_from(labels.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS label count"))?,
    );
    for label in labels {
        bytes.push(
            u8::try_from(label.len())
                .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS label bytes"))?,
        );
        bytes.extend_from_slice(label.as_bytes());
    }
    sha256_hex(&bytes)
}

fn transitions_sha256(labels: &[String], transitions: &[PosTransition]) -> Result<String> {
    let mut bytes = Vec::with_capacity(2 + transitions.len() * 2);
    bytes.extend_from_slice(
        &u16::try_from(transitions.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "POS transition count"))?
            .to_be_bytes(),
    );
    for transition in transitions {
        bytes.push(endpoint_code(transition.from(), labels)?);
        bytes.push(endpoint_code(transition.to(), labels)?);
    }
    sha256_hex(&bytes)
}

fn valid_identity(identity: &PosModelIdentity) -> bool {
    [
        identity.model_id.as_str(),
        identity.algorithm_id.as_str(),
        identity.compiler_id.as_str(),
        identity.config_id.as_str(),
    ]
    .into_iter()
    .all(valid_identifier)
}

fn valid_source(source: &PosSourceIdentity) -> bool {
    [
        source.source_id.as_str(),
        source.corpus_version.as_str(),
        source.generator_id.as_str(),
        source.source_license.as_str(),
        source.locale.as_str(),
    ]
    .into_iter()
    .all(valid_identifier)
        && [
            source.source_manifest_sha256.as_str(),
            source.specification_sha256.as_str(),
            source.generator_sha256.as_str(),
            source.original_pos_artifact_sha256.as_str(),
        ]
        .into_iter()
        .all(is_sha256)
}

fn valid_training(training: &PosTrainingIdentity) -> bool {
    [
        training.physical_train_slice_sha256.as_str(),
        training.train_case_digest_sha256.as_str(),
        training.train_document_digest_sha256.as_str(),
        training.train_origin_digest_sha256.as_str(),
    ]
    .into_iter()
    .all(is_sha256)
        && training.train_sentence_count > 0
        && training.train_document_count > 0
        && training.train_document_count <= training.train_sentence_count
        && training.train_token_count >= training.train_sentence_count
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_ID_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn valid_label(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_POS_LABEL_BYTES
        && value != "BOS"
        && value != "EOS"
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_uppercase() || (index > 0 && (byte.is_ascii_digit() || byte == b'_'))
        })
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
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
