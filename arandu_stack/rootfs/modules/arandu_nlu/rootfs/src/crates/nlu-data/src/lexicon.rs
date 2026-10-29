use std::{collections::BTreeSet, path::Path};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    DataError, DataErrorCode, MAX_RECORD_BYTES, MAX_RECORDS, Result,
    hash::sha256_hex,
    json::{canonical_json, parse_strict_json},
    manifest::{
        MAX_MANIFEST_BYTES, VerifiedArtifact, VerifiedSource, read_root_file, verify_source_pinned,
    },
    pipeline::{AtomicDirectory, write_new_file},
};

pub const LEXICON_PACKAGE_FILE: &str = "package.bin";
pub const LEXICON_MANIFEST_FILE: &str = "package-manifest.json";
pub const LEXICON_FORMAT: &str = "nlu-lexicon-package-v1";

const PACKAGE_HEADER: &[u8; 8] = b"NLULEX\0\x01";
const MAX_PACKAGE_BYTES: usize = 32 * 1024 * 1024;
const MAX_FEATURES: usize = 128;
const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const SOURCE_VERSION: &str = "1.0.0";
const SOURCE_STATUS: &str = "PROJECT_AUTHORED_SYNTHETIC";
const SOURCE_LICENSE: &str = "Apache-2.0";
const SOURCE_LOCALE: &str = "pt-BR";
const SOURCE_PARTITION: &str = "shared";
const SOURCE_MANIFEST_SHA256: &str =
    "d9e1ca32ec0f92aa6b5fc6c1d4f232f93389e0bf8031267d23daddf7287006c5";
const SOURCE_ARTIFACT_PATH: &str = "data/project-authored/p02-v1/lexicon.jsonl";
const SOURCE_ARTIFACT_SHA256: &str =
    "727e89195fc2ed16489c24b17841d58a2a9bb68c828769ece83ab70489316e4e";
const SPECIFICATION_PATH: &str = "data/project-authored/p02-v1/specification.yaml";
const SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
const GENERATOR_ID: &str = "p02-generator-v1";
const GENERATOR_SHA256: &str = "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
const GENERATION_FAMILY: &str = "p02-generator-v1/build_lexicon_records";
const COMPILE_TRANSFORM_ID: &str = "ptbr-lexicon-compile-v1";
const REMOVE_TRANSFORM_ID: &str = "ptbr-lexicon-source-filter-v1";
const GENERATOR_SOURCE: &[u8] = include_bytes!("../../../tools/generate-p02-corpus.rb");
const COMPILER_SOURCE: &[u8] = include_bytes!("lexicon.rs");

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LexiconSource {
    source_id: String,
    admission_status: String,
    corpus_version: String,
    locale: String,
    source_license: String,
    partition: String,
    source_manifest_sha256: String,
    artifact_path: String,
    artifact_sha256: String,
    record_id: String,
    record_sha256: String,
}

impl LexiconSource {
    #[must_use]
    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    #[must_use]
    pub fn admission_status(&self) -> &str {
        &self.admission_status
    }

    #[must_use]
    pub fn corpus_version(&self) -> &str {
        &self.corpus_version
    }

    #[must_use]
    pub fn locale(&self) -> &str {
        &self.locale
    }

    #[must_use]
    pub fn source_license(&self) -> &str {
        &self.source_license
    }

    #[must_use]
    pub fn partition(&self) -> &str {
        &self.partition
    }

    #[must_use]
    pub fn source_manifest_sha256(&self) -> &str {
        &self.source_manifest_sha256
    }

    #[must_use]
    pub fn artifact_path(&self) -> &str {
        &self.artifact_path
    }

    #[must_use]
    pub fn artifact_sha256(&self) -> &str {
        &self.artifact_sha256
    }

    #[must_use]
    pub fn record_id(&self) -> &str {
        &self.record_id
    }

    #[must_use]
    pub fn record_sha256(&self) -> &str {
        &self.record_sha256
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GenerationParameter {
    name: String,
    values: Vec<String>,
}

impl GenerationParameter {
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    #[must_use]
    pub fn values(&self) -> &[String] {
        &self.values
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GenerationLineage {
    generator_id: String,
    generator_implementation_sha256: String,
    specification_path: String,
    specification_sha256: String,
    family: String,
    parameters: Vec<GenerationParameter>,
    canonical_identity_sha256: String,
}

impl GenerationLineage {
    #[must_use]
    pub fn generator_id(&self) -> &str {
        &self.generator_id
    }

    #[must_use]
    pub fn generator_implementation_sha256(&self) -> &str {
        &self.generator_implementation_sha256
    }

    #[must_use]
    pub fn specification_path(&self) -> &str {
        &self.specification_path
    }

    #[must_use]
    pub fn specification_sha256(&self) -> &str {
        &self.specification_sha256
    }

    #[must_use]
    pub fn family(&self) -> &str {
        &self.family
    }

    #[must_use]
    pub fn parameters(&self) -> &[GenerationParameter] {
        &self.parameters
    }

    #[must_use]
    pub fn canonical_identity_sha256(&self) -> &str {
        &self.canonical_identity_sha256
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransformationStep {
    transform_id: String,
    implementation_sha256: String,
    input_sha256: String,
    output_sha256: String,
}

impl TransformationStep {
    #[must_use]
    pub fn transform_id(&self) -> &str {
        &self.transform_id
    }

    #[must_use]
    pub fn implementation_sha256(&self) -> &str {
        &self.implementation_sha256
    }

    #[must_use]
    pub fn input_sha256(&self) -> &str {
        &self.input_sha256
    }

    #[must_use]
    pub fn output_sha256(&self) -> &str {
        &self.output_sha256
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LexiconEntry {
    schema_version: u32,
    analysis_id: String,
    surface: String,
    lemma: String,
    pos: String,
    features: Vec<String>,
    source: LexiconSource,
    generation: GenerationLineage,
    transformations: Vec<TransformationStep>,
    derivative_license: String,
}

impl LexiconEntry {
    #[must_use]
    pub fn analysis_id(&self) -> &str {
        &self.analysis_id
    }

    #[must_use]
    pub fn surface(&self) -> &str {
        &self.surface
    }

    #[must_use]
    pub fn lemma(&self) -> &str {
        &self.lemma
    }

    #[must_use]
    pub fn pos(&self) -> &str {
        &self.pos
    }

    #[must_use]
    pub fn features(&self) -> &[String] {
        &self.features
    }

    #[must_use]
    pub const fn source(&self) -> &LexiconSource {
        &self.source
    }

    #[must_use]
    pub const fn generation(&self) -> &GenerationLineage {
        &self.generation
    }

    #[must_use]
    pub fn transformations(&self) -> &[TransformationStep] {
        &self.transformations
    }

    #[must_use]
    pub fn derivative_license(&self) -> &str {
        &self.derivative_license
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LexiconManifest {
    schema_version: u32,
    format: String,
    transform_id: String,
    input_sha256: String,
    package_sha256: String,
    package_bytes: u64,
    entry_count: u64,
    source_ids: Vec<String>,
    derivative_licenses: Vec<String>,
}

impl LexiconManifest {
    #[must_use]
    pub fn transform_id(&self) -> &str {
        &self.transform_id
    }

    #[must_use]
    pub fn input_sha256(&self) -> &str {
        &self.input_sha256
    }

    #[must_use]
    pub fn package_sha256(&self) -> &str {
        &self.package_sha256
    }

    #[must_use]
    pub const fn package_bytes(&self) -> u64 {
        self.package_bytes
    }

    #[must_use]
    pub const fn entry_count(&self) -> u64 {
        self.entry_count
    }

    #[must_use]
    pub fn source_ids(&self) -> &[String] {
        &self.source_ids
    }

    #[must_use]
    pub fn derivative_licenses(&self) -> &[String] {
        &self.derivative_licenses
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LexiconPackage {
    manifest: LexiconManifest,
    entries: Box<[LexiconEntry]>,
}

impl LexiconPackage {
    #[must_use]
    pub const fn manifest(&self) -> &LexiconManifest {
        &self.manifest
    }

    #[must_use]
    pub fn entries(&self) -> &[LexiconEntry] {
        &self.entries
    }
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceLexiconRecord {
    schema_version: u32,
    analysis_id: String,
    source_id: String,
    corpus_version: String,
    generator_id: String,
    license: String,
    locale: String,
    surface: String,
    lemma: String,
    pos: String,
    features: Vec<String>,
}

#[derive(Serialize)]
struct EntryPayload<'a> {
    schema_version: u32,
    analysis_id: &'a str,
    surface: &'a str,
    lemma: &'a str,
    pos: &'a str,
    features: &'a [String],
    source: &'a LexiconSource,
    generation: &'a GenerationLineage,
    derivative_license: &'a str,
}

struct SourceRow {
    bytes: Vec<u8>,
    record: SourceLexiconRecord,
}

struct CompileContext {
    compiler_sha256: String,
}

pub fn compile_lexicon(manifest_path: &Path, root: &Path, output: &Path) -> Result<usize> {
    let source = verify_source_pinned(manifest_path, root, SOURCE_MANIFEST_SHA256)?;
    let entries = compile_verified_source(&source)?;
    let count = entries.len();
    let package = encode_package(entries)?;
    let manifest = build_manifest(
        COMPILE_TRANSFORM_ID,
        SOURCE_ARTIFACT_SHA256,
        &package,
        &decode_entries(&package)?,
    )?;
    write_artifact(output, &package, &manifest)?;
    Ok(count)
}

pub fn remove_lexicon_source(input: &Path, source_id: &str, output: &Path) -> Result<usize> {
    if !valid_identifier(source_id) {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "invalid lexicon removal source ID",
        ));
    }
    let package_bytes = read_root_file(input, LEXICON_PACKAGE_FILE, MAX_PACKAGE_BYTES)?;
    let manifest_bytes = read_root_file(input, LEXICON_MANIFEST_FILE, MAX_MANIFEST_BYTES)?;
    let decoded = decode_lexicon(&package_bytes, &manifest_bytes)?;
    if !decoded
        .manifest
        .source_ids
        .iter()
        .any(|candidate| candidate == source_id)
    {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "lexicon removal source is absent",
        ));
    }
    let entries = remove_entries(decoded.entries.into_vec(), source_id);
    let count = entries.len();
    let package = encode_package(entries)?;
    let input_sha256 = sha256_hex(&package_bytes)?;
    let manifest = build_manifest(
        REMOVE_TRANSFORM_ID,
        &input_sha256,
        &package,
        &decode_entries(&package)?,
    )?;
    write_artifact(output, &package, &manifest)?;
    Ok(count)
}

pub fn decode_lexicon(package: &[u8], manifest: &[u8]) -> Result<LexiconPackage> {
    if package.len() > MAX_PACKAGE_BYTES {
        return Err(DataError::new(
            DataErrorCode::ResourceLimit,
            "lexicon package bytes",
        ));
    }
    let manifest_value = parse_strict_json(manifest, "lexicon package manifest")?;
    let decoded_manifest: LexiconManifest = serde_json::from_value(manifest_value.clone())
        .map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidManifest,
                format!("lexicon package manifest shape: {error}"),
            )
        })?;
    let mut canonical_manifest = canonical_json(&manifest_value, "lexicon package manifest")?;
    canonical_manifest.push(b'\n');
    if canonical_manifest != manifest {
        return Err(DataError::new(
            DataErrorCode::InvalidManifest,
            "noncanonical lexicon package manifest",
        ));
    }
    validate_manifest(&decoded_manifest, package)?;
    let entries = decode_entries(package)?;
    validate_inventory(&decoded_manifest, &entries)?;
    Ok(LexiconPackage {
        manifest: decoded_manifest,
        entries: entries.into_boxed_slice(),
    })
}

fn compile_verified_source(source: &VerifiedSource) -> Result<Vec<LexiconEntry>> {
    let lexicon = exactly_one_artifact(source, "lexicon")?;
    let specification = exactly_one_artifact(source, "generator_specification")?;
    let p02_manifest = exactly_one_artifact(source, "p02_manifest")?;
    if lexicon.descriptor.path != SOURCE_ARTIFACT_PATH
        || lexicon.descriptor.sha256 != SOURCE_ARTIFACT_SHA256
        || lexicon.descriptor.records != Some(33)
        || specification.descriptor.path != SPECIFICATION_PATH
        || specification.descriptor.sha256 != SPECIFICATION_SHA256
        || sha256_hex(GENERATOR_SOURCE)? != GENERATOR_SHA256
    {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "P06 lexical source identities",
        ));
    }
    validate_p02_manifest(&p02_manifest.bytes)?;
    let context = CompileContext {
        compiler_sha256: sha256_hex(COMPILER_SOURCE)?,
    };
    let rows = parse_source_rows(&lexicon.bytes)?;
    compile_rows(rows, &context)
}

fn exactly_one_artifact<'a>(
    source: &'a VerifiedSource,
    role: &str,
) -> Result<&'a VerifiedArtifact> {
    let mut matches = source
        .artifacts
        .iter()
        .filter(|artifact| artifact.descriptor.role == role);
    let artifact = matches.next().ok_or_else(|| {
        DataError::new(
            DataErrorCode::InvalidManifest,
            format!("missing P06 source artifact role: {role}"),
        )
    })?;
    if matches.next().is_some() {
        return Err(DataError::new(
            DataErrorCode::InvalidManifest,
            format!("duplicate P06 source artifact role: {role}"),
        ));
    }
    Ok(artifact)
}

fn validate_p02_manifest(bytes: &[u8]) -> Result<()> {
    let value = parse_strict_json(bytes, "P02 corpus manifest")?;
    let corpus = value
        .get("corpus")
        .and_then(Value::as_object)
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "P02 corpus identity"))?;
    let expected = [
        ("id", SOURCE_ID),
        ("version", SOURCE_VERSION),
        ("status", SOURCE_STATUS),
        ("authorization", "USR-016"),
        ("locale", SOURCE_LOCALE),
        ("license", SOURCE_LICENSE),
        ("claim_scope", "internal_conformance_only"),
        ("generator_id", GENERATOR_ID),
        ("oracle_origin", "pre_engine_generator_specification"),
        ("specification_sha256", SPECIFICATION_SHA256),
        ("generator_sha256", GENERATOR_SHA256),
    ];
    if expected
        .iter()
        .any(|(field, expected)| corpus.get(*field).and_then(Value::as_str) != Some(*expected))
    {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "P02 corpus lineage identity",
        ));
    }
    Ok(())
}

fn parse_source_rows(bytes: &[u8]) -> Result<Vec<SourceRow>> {
    let content = bytes.strip_suffix(b"\n").ok_or_else(|| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            "P06 lexicon source lacks final newline",
        )
    })?;
    let mut rows = Vec::new();
    for (index, line) in content.split(|byte| *byte == b'\n').enumerate() {
        if line.is_empty() || line.len() > MAX_RECORD_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "P06 lexical source row size",
            ));
        }
        let value = parse_strict_json(line, &format!("P06 lexical source row {}", index + 1))?;
        let record: SourceLexiconRecord = serde_json::from_value(value).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("P06 lexical source row shape: {error}"),
            )
        })?;
        rows.push(SourceRow {
            bytes: line.to_vec(),
            record,
        });
        if rows.len() > MAX_RECORDS {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "P06 lexical source row count",
            ));
        }
    }
    if rows.len() != 33 {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "P06 lexical source row count",
        ));
    }
    Ok(rows)
}

fn compile_rows(rows: Vec<SourceRow>, context: &CompileContext) -> Result<Vec<LexiconEntry>> {
    let mut entries = Vec::with_capacity(rows.len());
    let mut identities = BTreeSet::new();
    for row in rows {
        validate_source_record(&row.record)?;
        if !identities.insert(row.record.analysis_id.clone()) {
            return Err(DataError::new(
                DataErrorCode::InvalidRecord,
                "duplicate P06 lexical source identity",
            ));
        }
        entries.push(compile_entry(row, context)?);
    }
    sort_entries(&mut entries);
    validate_entry_order(&entries)?;
    Ok(entries)
}

fn validate_source_record(record: &SourceLexiconRecord) -> Result<()> {
    if record.schema_version != 1
        || !valid_identifier(&record.analysis_id)
        || record.source_id != SOURCE_ID
        || record.corpus_version != SOURCE_VERSION
        || record.generator_id != GENERATOR_ID
        || record.license != SOURCE_LICENSE
        || record.locale != SOURCE_LOCALE
        || !valid_text(&record.surface)
        || !valid_text(&record.lemma)
        || !valid_identifier(&record.pos)
        || record.features.len() > MAX_FEATURES
        || record
            .features
            .iter()
            .any(|feature| !valid_feature(feature))
        || !strictly_sorted(&record.features)
        || contains_fixture(record)
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "invalid P06 lexical source record",
        ));
    }
    Ok(())
}

fn compile_entry(row: SourceRow, context: &CompileContext) -> Result<LexiconEntry> {
    let record_sha256 = sha256_hex(&row.bytes)?;
    let semantic_value = serde_json::json!([
        &row.record.surface,
        &row.record.lemma,
        &row.record.pos,
        &row.record.features
    ]);
    let semantic_bytes = canonical_json(&semantic_value, "P06 lexical identity")?;
    let source = LexiconSource {
        source_id: row.record.source_id.clone(),
        admission_status: SOURCE_STATUS.to_owned(),
        corpus_version: row.record.corpus_version.clone(),
        locale: row.record.locale.clone(),
        source_license: row.record.license.clone(),
        partition: SOURCE_PARTITION.to_owned(),
        source_manifest_sha256: SOURCE_MANIFEST_SHA256.to_owned(),
        artifact_path: SOURCE_ARTIFACT_PATH.to_owned(),
        artifact_sha256: SOURCE_ARTIFACT_SHA256.to_owned(),
        record_id: row.record.analysis_id.clone(),
        record_sha256: record_sha256.clone(),
    };
    let generation = GenerationLineage {
        generator_id: row.record.generator_id.clone(),
        generator_implementation_sha256: GENERATOR_SHA256.to_owned(),
        specification_path: SPECIFICATION_PATH.to_owned(),
        specification_sha256: SPECIFICATION_SHA256.to_owned(),
        family: GENERATION_FAMILY.to_owned(),
        parameters: vec![
            GenerationParameter {
                name: "surface".to_owned(),
                values: vec![row.record.surface.clone()],
            },
            GenerationParameter {
                name: "lemma".to_owned(),
                values: vec![row.record.lemma.clone()],
            },
            GenerationParameter {
                name: "pos".to_owned(),
                values: vec![row.record.pos.clone()],
            },
            GenerationParameter {
                name: "features".to_owned(),
                values: row.record.features.clone(),
            },
        ],
        canonical_identity_sha256: sha256_hex(&semantic_bytes)?,
    };
    let payload = EntryPayload {
        schema_version: 1,
        analysis_id: &row.record.analysis_id,
        surface: &row.record.surface,
        lemma: &row.record.lemma,
        pos: &row.record.pos,
        features: &row.record.features,
        source: &source,
        generation: &generation,
        derivative_license: SOURCE_LICENSE,
    };
    let payload_value = serde_json::to_value(payload).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("P06 lexical payload encoding: {error}"),
        )
    })?;
    let payload_bytes = canonical_json(&payload_value, "P06 lexical payload")?;
    let entry = LexiconEntry {
        schema_version: 1,
        analysis_id: row.record.analysis_id,
        surface: row.record.surface,
        lemma: row.record.lemma,
        pos: row.record.pos,
        features: row.record.features,
        source,
        generation,
        transformations: vec![
            TransformationStep {
                transform_id: GENERATOR_ID.to_owned(),
                implementation_sha256: GENERATOR_SHA256.to_owned(),
                input_sha256: SPECIFICATION_SHA256.to_owned(),
                output_sha256: record_sha256.clone(),
            },
            TransformationStep {
                transform_id: COMPILE_TRANSFORM_ID.to_owned(),
                implementation_sha256: context.compiler_sha256.clone(),
                input_sha256: record_sha256,
                output_sha256: sha256_hex(&payload_bytes)?,
            },
        ],
        derivative_license: SOURCE_LICENSE.to_owned(),
    };
    validate_entry(&entry)?;
    Ok(entry)
}

fn validate_entry(entry: &LexiconEntry) -> Result<()> {
    if entry.schema_version != 1
        || !valid_identifier(&entry.analysis_id)
        || !valid_text(&entry.surface)
        || !valid_text(&entry.lemma)
        || !valid_identifier(&entry.pos)
        || entry.features.len() > MAX_FEATURES
        || entry.features.iter().any(|feature| !valid_feature(feature))
        || !strictly_sorted(&entry.features)
        || entry.derivative_license != SOURCE_LICENSE
    {
        return invalid_entry();
    }
    let source = &entry.source;
    if source.source_id != SOURCE_ID
        || source.admission_status != SOURCE_STATUS
        || source.corpus_version != SOURCE_VERSION
        || source.locale != SOURCE_LOCALE
        || source.source_license != SOURCE_LICENSE
        || source.partition != SOURCE_PARTITION
        || source.source_manifest_sha256 != SOURCE_MANIFEST_SHA256
        || source.artifact_path != SOURCE_ARTIFACT_PATH
        || source.artifact_sha256 != SOURCE_ARTIFACT_SHA256
        || source.record_id != entry.analysis_id
        || !is_sha256(&source.record_sha256)
    {
        return invalid_entry();
    }
    let generation = &entry.generation;
    let expected_parameters = [
        ("surface", core::slice::from_ref(&entry.surface)),
        ("lemma", core::slice::from_ref(&entry.lemma)),
        ("pos", core::slice::from_ref(&entry.pos)),
        ("features", entry.features.as_slice()),
    ];
    if generation.generator_id != GENERATOR_ID
        || generation.generator_implementation_sha256 != GENERATOR_SHA256
        || generation.specification_path != SPECIFICATION_PATH
        || generation.specification_sha256 != SPECIFICATION_SHA256
        || generation.family != GENERATION_FAMILY
        || !is_sha256(&generation.canonical_identity_sha256)
        || generation.parameters.len() != expected_parameters.len()
        || generation
            .parameters
            .iter()
            .zip(expected_parameters)
            .any(|(actual, (name, values))| actual.name != name || actual.values != values)
    {
        return invalid_entry();
    }
    let semantic_value =
        serde_json::json!([&entry.surface, &entry.lemma, &entry.pos, &entry.features]);
    let semantic_bytes = canonical_json(&semantic_value, "P06 lexical identity")?;
    if sha256_hex(&semantic_bytes)? != generation.canonical_identity_sha256 {
        return invalid_entry();
    }
    if entry.transformations.len() != 2 {
        return invalid_entry();
    }
    let generator = &entry.transformations[0];
    let compiler = &entry.transformations[1];
    if generator.transform_id != GENERATOR_ID
        || generator.implementation_sha256 != GENERATOR_SHA256
        || generator.input_sha256 != SPECIFICATION_SHA256
        || generator.output_sha256 != source.record_sha256
        || compiler.transform_id != COMPILE_TRANSFORM_ID
        || compiler.implementation_sha256 != sha256_hex(COMPILER_SOURCE)?
        || compiler.input_sha256 != source.record_sha256
        || !is_sha256(&compiler.output_sha256)
    {
        return invalid_entry();
    }
    let payload = EntryPayload {
        schema_version: entry.schema_version,
        analysis_id: &entry.analysis_id,
        surface: &entry.surface,
        lemma: &entry.lemma,
        pos: &entry.pos,
        features: &entry.features,
        source,
        generation,
        derivative_license: &entry.derivative_license,
    };
    let payload_value = serde_json::to_value(payload).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("P06 lexical payload validation: {error}"),
        )
    })?;
    let payload_bytes = canonical_json(&payload_value, "P06 lexical payload")?;
    if sha256_hex(&payload_bytes)? != compiler.output_sha256 {
        return invalid_entry();
    }
    Ok(())
}

fn invalid_entry<T>() -> Result<T> {
    Err(DataError::new(
        DataErrorCode::InvalidRecord,
        "invalid P06 lexical entry",
    ))
}

fn encode_package(mut entries: Vec<LexiconEntry>) -> Result<Vec<u8>> {
    sort_entries(&mut entries);
    validate_entry_order(&entries)?;
    let mut output = Vec::new();
    output.extend_from_slice(PACKAGE_HEADER);
    let count = u64::try_from(entries.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 lexicon entry count"))?;
    output.extend_from_slice(&count.to_be_bytes());
    for entry in &entries {
        validate_entry(entry)?;
        let value = serde_json::to_value(entry).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("P06 lexical entry encoding: {error}"),
            )
        })?;
        let bytes = canonical_json(&value, "P06 lexical entry")?;
        if bytes.len() > MAX_RECORD_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "P06 lexical entry bytes",
            ));
        }
        let length = u32::try_from(bytes.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 entry length"))?;
        output.extend_from_slice(&length.to_be_bytes());
        output.extend_from_slice(&bytes);
        if output.len() > MAX_PACKAGE_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "P06 lexicon package bytes",
            ));
        }
    }
    Ok(output)
}

fn decode_entries(package: &[u8]) -> Result<Vec<LexiconEntry>> {
    if package.len() < 16 || package.get(..8) != Some(PACKAGE_HEADER) {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "invalid P06 lexicon package header",
        ));
    }
    let count_bytes: [u8; 8] = package[8..16]
        .try_into()
        .map_err(|_| DataError::new(DataErrorCode::InvalidRecord, "P06 entry count bytes"))?;
    let count = usize::try_from(u64::from_be_bytes(count_bytes))
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 entry count"))?;
    if count > MAX_RECORDS {
        return Err(DataError::new(
            DataErrorCode::ResourceLimit,
            "P06 entry count",
        ));
    }
    let mut entries = Vec::with_capacity(count);
    let mut offset = 16_usize;
    for index in 0..count {
        let length_end = offset.checked_add(4).ok_or_else(|| {
            DataError::new(DataErrorCode::ResourceLimit, "P06 entry length offset")
        })?;
        let length_bytes: [u8; 4] = package
            .get(offset..length_end)
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "truncated P06 length"))?
            .try_into()
            .map_err(|_| DataError::new(DataErrorCode::InvalidRecord, "P06 entry length bytes"))?;
        let length = usize::try_from(u32::from_be_bytes(length_bytes))
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 entry length"))?;
        if length == 0 || length > MAX_RECORD_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "P06 entry length",
            ));
        }
        let entry_end = length_end
            .checked_add(length)
            .ok_or_else(|| DataError::new(DataErrorCode::ResourceLimit, "P06 entry byte offset"))?;
        let bytes = package
            .get(length_end..entry_end)
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "truncated P06 entry"))?;
        let value = parse_strict_json(bytes, &format!("P06 package entry {}", index + 1))?;
        if canonical_json(&value, "P06 package entry")? != bytes {
            return Err(DataError::new(
                DataErrorCode::InvalidRecord,
                "noncanonical P06 package entry",
            ));
        }
        let entry: LexiconEntry = serde_json::from_value(value).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("P06 package entry shape: {error}"),
            )
        })?;
        validate_entry(&entry)?;
        entries.push(entry);
        offset = entry_end;
    }
    if offset != package.len() {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "trailing P06 package bytes",
        ));
    }
    validate_entry_order(&entries)?;
    Ok(entries)
}

fn build_manifest(
    transform_id: &str,
    input_sha256: &str,
    package: &[u8],
    entries: &[LexiconEntry],
) -> Result<LexiconManifest> {
    let package_bytes = u64::try_from(package.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 package size"))?;
    let entry_count = u64::try_from(entries.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 entry count"))?;
    Ok(LexiconManifest {
        schema_version: 1,
        format: LEXICON_FORMAT.to_owned(),
        transform_id: transform_id.to_owned(),
        input_sha256: input_sha256.to_owned(),
        package_sha256: sha256_hex(package)?,
        package_bytes,
        entry_count,
        source_ids: source_ids(entries),
        derivative_licenses: derivative_licenses(entries),
    })
}

fn validate_manifest(manifest: &LexiconManifest, package: &[u8]) -> Result<()> {
    let package_bytes = u64::try_from(package.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 package size"))?;
    if manifest.schema_version != 1
        || manifest.format != LEXICON_FORMAT
        || ![COMPILE_TRANSFORM_ID, REMOVE_TRANSFORM_ID].contains(&manifest.transform_id.as_str())
        || !is_sha256(&manifest.input_sha256)
        || !is_sha256(&manifest.package_sha256)
        || manifest.package_sha256 != sha256_hex(package)?
        || manifest.package_bytes != package_bytes
        || manifest.entry_count > MAX_RECORDS as u64
        || !strictly_sorted(&manifest.source_ids)
        || !strictly_sorted(&manifest.derivative_licenses)
    {
        return Err(DataError::new(
            DataErrorCode::InvalidManifest,
            "invalid P06 lexicon package manifest",
        ));
    }
    Ok(())
}

fn validate_inventory(manifest: &LexiconManifest, entries: &[LexiconEntry]) -> Result<()> {
    let count = u64::try_from(entries.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "P06 entry count"))?;
    if manifest.entry_count != count
        || manifest.source_ids != source_ids(entries)
        || manifest.derivative_licenses != derivative_licenses(entries)
    {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "P06 lexicon package inventory",
        ));
    }
    Ok(())
}

fn write_artifact(output: &Path, package: &[u8], manifest: &LexiconManifest) -> Result<()> {
    let value = serde_json::to_value(manifest).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidManifest,
            format!("P06 lexicon manifest encoding: {error}"),
        )
    })?;
    let mut manifest_bytes = canonical_json(&value, "P06 lexicon manifest")?;
    manifest_bytes.push(b'\n');
    let destination = AtomicDirectory::create(output)?;
    write_new_file(&destination.path().join(LEXICON_PACKAGE_FILE), package)?;
    write_new_file(
        &destination.path().join(LEXICON_MANIFEST_FILE),
        &manifest_bytes,
    )?;
    destination.commit()
}

fn sort_entries(entries: &mut [LexiconEntry]) {
    entries.sort_by(|left, right| entry_key(left).cmp(&entry_key(right)));
}

fn validate_entry_order(entries: &[LexiconEntry]) -> Result<()> {
    if entries
        .windows(2)
        .any(|pair| entry_key(&pair[0]) >= entry_key(&pair[1]))
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "P06 lexicon entry order or identity",
        ));
    }
    let mut identities = BTreeSet::new();
    if entries.iter().any(|entry| {
        !identities.insert((entry.source.source_id.as_str(), entry.analysis_id.as_str()))
    }) {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "duplicate P06 lexicon entry identity",
        ));
    }
    Ok(())
}

fn entry_key(entry: &LexiconEntry) -> (&str, &str, &str, &[String], &str, &str) {
    (
        &entry.surface,
        &entry.pos,
        &entry.lemma,
        &entry.features,
        &entry.source.source_id,
        &entry.analysis_id,
    )
}

fn source_ids(entries: &[LexiconEntry]) -> Vec<String> {
    entries
        .iter()
        .map(|entry| entry.source.source_id.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn derivative_licenses(entries: &[LexiconEntry]) -> Vec<String> {
    entries
        .iter()
        .map(|entry| entry.derivative_license.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn remove_entries(entries: Vec<LexiconEntry>, source_id: &str) -> Vec<LexiconEntry> {
    entries
        .into_iter()
        .filter(|entry| entry.source.source_id != source_id)
        .collect()
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn valid_text(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64 * 1024
        && !value.chars().any(char::is_control)
        && !value.contains("FIXTURE_TECNICA")
}

fn valid_feature(value: &str) -> bool {
    valid_text(value) && value.contains('=')
}

fn strictly_sorted(values: &[String]) -> bool {
    values.windows(2).all(|pair| pair[0] < pair[1])
}

fn contains_fixture(record: &SourceLexiconRecord) -> bool {
    [
        record.analysis_id.as_str(),
        record.source_id.as_str(),
        record.corpus_version.as_str(),
        record.generator_id.as_str(),
        record.license.as_str(),
        record.locale.as_str(),
        record.surface.as_str(),
        record.lemma.as_str(),
        record.pos.as_str(),
    ]
    .into_iter()
    .any(|value| value.contains("FIXTURE_TECNICA"))
        || record
            .features
            .iter()
            .any(|value| value.contains("FIXTURE_TECNICA"))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::{
        CompileContext, LexiconEntry, SOURCE_ARTIFACT_SHA256, SourceRow, compile_rows,
        decode_entries, encode_package, parse_source_rows, remove_entries,
    };

    const SOURCE: &[u8] = include_bytes!("../../../data/project-authored/p02-v1/lexicon.jsonl");

    #[test]
    fn package_bytes_ignore_source_row_order() {
        let context = CompileContext {
            compiler_sha256: super::sha256_hex(super::COMPILER_SOURCE)
                .expect("compiler source hash"),
        };
        let first_rows = parse_source_rows(SOURCE).expect("source rows");
        let mut second_rows = parse_source_rows(SOURCE).expect("source rows");
        second_rows.reverse();
        let first = compile_rows(first_rows, &context).expect("first entries");
        let second = compile_rows(second_rows, &context).expect("second entries");
        assert_eq!(
            encode_package(first).expect("first package"),
            encode_package(second).expect("second package")
        );
    }

    #[test]
    fn source_filter_retains_unrelated_structural_entries() {
        let context = CompileContext {
            compiler_sha256: super::sha256_hex(super::COMPILER_SOURCE)
                .expect("compiler source hash"),
        };
        let rows = parse_source_rows(SOURCE).expect("source rows");
        let entries = compile_rows(rows, &context).expect("entries");
        let mut source_a = entries[0].clone();
        source_a.source.source_id = "FIXTURE_TECNICA.source-a".to_owned();
        source_a.analysis_id = "FIXTURE_TECNICA.analysis-a".to_owned();
        let mut source_b = entries[1].clone();
        source_b.source.source_id = "FIXTURE_TECNICA.source-b".to_owned();
        source_b.analysis_id = "FIXTURE_TECNICA.analysis-b".to_owned();
        let filtered = remove_entries(vec![source_a, source_b], "FIXTURE_TECNICA.source-a");
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].source.source_id, "FIXTURE_TECNICA.source-b");
    }

    #[test]
    fn framing_rejects_truncation_and_trailing_bytes() {
        let context = CompileContext {
            compiler_sha256: super::sha256_hex(super::COMPILER_SOURCE)
                .expect("compiler source hash"),
        };
        let rows = parse_source_rows(SOURCE).expect("source rows");
        let package =
            encode_package(compile_rows(rows, &context).expect("entries")).expect("package");
        assert_eq!(
            super::sha256_hex(SOURCE).expect("source hash"),
            SOURCE_ARTIFACT_SHA256
        );
        for end in [0, 7, 8, 15, package.len() - 1] {
            assert!(decode_entries(&package[..end]).is_err(), "accepted {end}");
        }
        let mut trailing = package;
        trailing.push(0);
        assert!(decode_entries(&trailing).is_err());
    }

    #[test]
    fn framing_rejects_version_count_and_length_limit_substitutions() {
        let (entries, package) = compiled_package();

        let mut version = package.clone();
        version[7] = 2;
        assert!(decode_entries(&version).is_err());

        let mut excessive_count = Vec::from(super::PACKAGE_HEADER.as_slice());
        excessive_count.extend_from_slice(
            &u64::try_from(super::MAX_RECORDS + 1)
                .expect("one-over count")
                .to_be_bytes(),
        );
        let error = decode_entries(&excessive_count).expect_err("one-over count");
        assert_eq!(error.code(), super::DataErrorCode::ResourceLimit);

        let mut zero_length = package.clone();
        zero_length[16..20].copy_from_slice(&0_u32.to_be_bytes());
        let error = decode_entries(&zero_length).expect_err("zero length");
        assert_eq!(error.code(), super::DataErrorCode::ResourceLimit);

        let mut excessive_length = package;
        excessive_length[16..20].copy_from_slice(
            &u32::try_from(super::MAX_RECORD_BYTES + 1)
                .expect("one-over length")
                .to_be_bytes(),
        );
        let error = decode_entries(&excessive_length).expect_err("one-over length");
        assert_eq!(error.code(), super::DataErrorCode::ResourceLimit);

        assert_eq!(entries.len(), 33);
    }

    #[test]
    fn inner_substitutions_fail_even_with_a_recomputed_outer_manifest() {
        let (entries, package) = compiled_package();

        assert_tamper_rejected(&entries, &package, |entry| {
            entry
                .as_object_mut()
                .expect("entry object")
                .remove("derivative_license");
        });
        assert_tamper_rejected(&entries, &package, |entry| {
            entry["source"]["source_license"] = serde_json::json!("MIT");
        });
        assert_tamper_rejected(&entries, &package, |entry| {
            entry["source"]["source_manifest_sha256"] = serde_json::json!("0".repeat(64));
        });
        assert_tamper_rejected(&entries, &package, |entry| {
            entry["generation"]["specification_sha256"] = serde_json::json!("0".repeat(64));
        });
        assert_tamper_rejected(&entries, &package, |entry| {
            entry["transformations"]
                .as_array_mut()
                .expect("transformations")
                .reverse();
        });
        assert_tamper_rejected(&entries, &package, |entry| {
            entry["transformations"][1]["output_sha256"] = serde_json::json!("0".repeat(64));
        });
        assert_tamper_rejected(&entries, &package, |entry| {
            entry
                .as_object_mut()
                .expect("entry object")
                .insert("unknown".to_owned(), serde_json::json!(true));
        });
    }

    #[test]
    fn package_order_and_manifest_inventory_substitutions_fail_closed() {
        let (entries, package) = compiled_package();
        let reordered = swap_first_two_entries(&package);
        let manifest = manifest_bytes(&reordered, &entries);
        assert!(super::decode_lexicon(&reordered, &manifest).is_err());

        let manifest = manifest_bytes(&package, &entries);
        let mut value: serde_json::Value =
            serde_json::from_slice(&manifest).expect("parse package manifest");
        value["entry_count"] = serde_json::json!(32);
        let mut changed =
            super::canonical_json(&value, "changed manifest").expect("canonical manifest");
        changed.push(b'\n');
        let error = super::decode_lexicon(&package, &changed).expect_err("manifest count mismatch");
        assert_eq!(error.code(), super::DataErrorCode::IntegrityMismatch);
    }

    #[test]
    fn package_limit_is_checked_before_manifest_or_entry_parsing() {
        let package = vec![0; super::MAX_PACKAGE_BYTES + 1];
        let error = super::decode_lexicon(&package, b"not-json")
            .expect_err("one-over package must fail first");
        assert_eq!(error.code(), super::DataErrorCode::ResourceLimit);
    }

    #[test]
    fn strict_row_parser_rejects_duplicate_keys() {
        let row = br#"{"schema_version":1,"schema_version":1}"#;
        let mut bytes = row.to_vec();
        bytes.push(b'\n');
        assert!(parse_source_rows(&bytes).is_err());
    }

    fn _assert_source_row_is_send(_: SourceRow) {}

    fn compiled_package() -> (Vec<LexiconEntry>, Vec<u8>) {
        let context = CompileContext {
            compiler_sha256: super::sha256_hex(super::COMPILER_SOURCE)
                .expect("compiler source hash"),
        };
        let rows = parse_source_rows(SOURCE).expect("source rows");
        let entries = compile_rows(rows, &context).expect("entries");
        let package = encode_package(entries.clone()).expect("package");
        (entries, package)
    }

    fn assert_tamper_rejected(
        entries: &[LexiconEntry],
        package: &[u8],
        mutate: impl FnOnce(&mut serde_json::Value),
    ) {
        let changed = rewrite_first_entry(package, mutate);
        let manifest = manifest_bytes(&changed, entries);
        assert!(
            super::decode_lexicon(&changed, &manifest).is_err(),
            "accepted recomputed inner substitution"
        );
    }

    fn rewrite_first_entry(package: &[u8], mutate: impl FnOnce(&mut serde_json::Value)) -> Vec<u8> {
        let length = u32::from_be_bytes(package[16..20].try_into().expect("entry length")) as usize;
        let entry_end = 20 + length;
        let mut value: serde_json::Value =
            serde_json::from_slice(&package[20..entry_end]).expect("parse entry");
        mutate(&mut value);
        let bytes = super::canonical_json(&value, "tampered entry").expect("canonical entry");
        let mut changed = package[..16].to_vec();
        changed.extend_from_slice(
            &u32::try_from(bytes.len())
                .expect("tampered entry length")
                .to_be_bytes(),
        );
        changed.extend_from_slice(&bytes);
        changed.extend_from_slice(&package[entry_end..]);
        changed
    }

    fn swap_first_two_entries(package: &[u8]) -> Vec<u8> {
        let first_length =
            u32::from_be_bytes(package[16..20].try_into().expect("first length")) as usize;
        let first_end = 20 + first_length;
        let second_length = u32::from_be_bytes(
            package[first_end..first_end + 4]
                .try_into()
                .expect("second length"),
        ) as usize;
        let second_end = first_end + 4 + second_length;
        let mut changed = package[..16].to_vec();
        changed.extend_from_slice(&package[first_end..second_end]);
        changed.extend_from_slice(&package[16..first_end]);
        changed.extend_from_slice(&package[second_end..]);
        changed
    }

    fn manifest_bytes(package: &[u8], entries: &[LexiconEntry]) -> Vec<u8> {
        let manifest = super::build_manifest(
            super::COMPILE_TRANSFORM_ID,
            super::SOURCE_ARTIFACT_SHA256,
            package,
            entries,
        )
        .expect("build manifest");
        let value = serde_json::to_value(manifest).expect("manifest value");
        let mut bytes =
            super::canonical_json(&value, "package manifest").expect("canonical manifest");
        bytes.push(b'\n');
        bytes
    }
}
