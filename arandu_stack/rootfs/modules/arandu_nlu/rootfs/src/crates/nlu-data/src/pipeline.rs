use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    DataError, DataErrorCode, MAX_RECORD_BYTES, MAX_RECORDS, Result,
    hash::sha256_hex,
    json::{canonical_json, parse_strict_json},
    manifest::{
        MAX_ARTIFACT_BYTES, VerifiedSource, read_root_file, validate_relative_path, verify_source,
    },
};

const STAGE_MANIFEST: &str = "stage-manifest.json";
const STAGE_RECORDS: &str = "records.jsonl";
const PACKAGE_MANIFEST: &str = "package-manifest.json";
const PACKAGE_BYTES: &str = "package.bin";
const FETCHED_MANIFEST: &str = "source-manifest.json";
const MAX_STAGE_BYTES: usize = 32 * 1024 * 1024;
const PACKAGE_HEADER: &[u8; 9] = b"NLUDATA\0\x01";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PipelineRecord {
    schema_version: u32,
    source_id: String,
    artifact_path: String,
    record_id: String,
    assigned_partition: Option<String>,
    record: Value,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StageManifest {
    schema_version: u32,
    stage: String,
    transform_id: String,
    source_manifest_sha256: String,
    input_records_sha256: Option<String>,
    records_sha256: String,
    record_count: u64,
    source_ids: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PackageManifest {
    schema_version: u32,
    format: String,
    source_manifest_sha256: String,
    input_records_sha256: String,
    package_sha256: String,
    package_bytes: u64,
    record_count: u64,
    source_ids: Vec<String>,
    partition_counts: BTreeMap<String, u64>,
}

struct Stage {
    manifest: StageManifest,
    records: Vec<PipelineRecord>,
}

pub fn verify(manifest_path: &Path, root: &Path) -> Result<usize> {
    let source = verify_source(manifest_path, root)?;
    Ok(source.artifacts.len())
}

pub fn fetch(manifest_path: &Path, root: &Path, output: &Path, allowed: bool) -> Result<()> {
    if !allowed {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "fetch requires explicit authorization",
        ));
    }
    let source = verify_source(manifest_path, root)?;
    let destination = AtomicDirectory::create(output)?;
    for artifact in &source.artifacts {
        write_relative_file(
            destination.path(),
            &artifact.descriptor.path,
            &artifact.bytes,
        )?;
    }
    write_relative_file(
        destination.path(),
        &source.manifest.source.license_path,
        &source.license_bytes,
    )?;
    write_relative_file(
        destination.path(),
        &source.manifest.source_decision.adr,
        &source.decision_bytes,
    )?;
    write_new_file(
        &destination.path().join(FETCHED_MANIFEST),
        &source.manifest_bytes,
    )?;
    destination.commit()
}

pub fn import(manifest_path: &Path, root: &Path, output: &Path) -> Result<usize> {
    let source = verify_source(manifest_path, root)?;
    let mut records = import_records(&source)?;
    sort_records(&mut records);
    let count = records.len();
    write_stage(
        output,
        "imported",
        "verified-jsonl-import-v1",
        &source.manifest_sha256,
        None,
        &records,
    )?;
    Ok(count)
}

pub fn normalize(input: &Path, output: &Path) -> Result<usize> {
    let mut stage = read_stage(input)?;
    if stage.manifest.stage != "imported" {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "normalize requires an imported stage",
        ));
    }
    sort_records(&mut stage.records);
    let count = stage.records.len();
    write_stage(
        output,
        "normalized",
        "canonical-json-v1",
        &stage.manifest.source_manifest_sha256,
        Some(&stage.manifest.records_sha256),
        &stage.records,
    )?;
    Ok(count)
}

pub fn validate_stage(input: &Path) -> Result<usize> {
    Ok(read_stage(input)?.records.len())
}

pub fn split(input: &Path, output: &Path) -> Result<usize> {
    let mut stage = read_stage(input)?;
    if stage.manifest.stage != "normalized" {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "split requires a normalized stage",
        ));
    }
    assign_partitions(&mut stage.records)?;
    sort_records(&mut stage.records);
    let count = stage.records.len();
    write_stage(
        output,
        "split",
        "frozen-record-split-v1",
        &stage.manifest.source_manifest_sha256,
        Some(&stage.manifest.records_sha256),
        &stage.records,
    )?;
    Ok(count)
}

pub fn compile(input: &Path, output: &Path) -> Result<usize> {
    let mut stage = read_stage(input)?;
    if stage.manifest.stage != "split" && stage.manifest.stage != "filtered" {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "compile requires a split or filtered stage",
        ));
    }
    if stage
        .records
        .iter()
        .any(|record| record.assigned_partition.is_none())
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "compiled record lacks a partition",
        ));
    }
    sort_records(&mut stage.records);
    let package = encode_package(&stage.records)?;
    let package_sha256 = sha256_hex(&package)?;
    let package_size = u64::try_from(package.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "package size"))?;
    let record_count = u64::try_from(stage.records.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "package record count"))?;
    let mut partition_counts = BTreeMap::new();
    for record in &stage.records {
        let partition = record
            .assigned_partition
            .as_ref()
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "missing partition"))?;
        let count = partition_counts.entry(partition.clone()).or_insert(0_u64);
        *count = count.checked_add(1).ok_or_else(|| {
            DataError::new(DataErrorCode::ResourceLimit, "partition record count")
        })?;
    }
    let manifest = PackageManifest {
        schema_version: 1,
        format: "nlu-data-package-v1".to_owned(),
        source_manifest_sha256: stage.manifest.source_manifest_sha256,
        input_records_sha256: stage.manifest.records_sha256,
        package_sha256,
        package_bytes: package_size,
        record_count,
        source_ids: source_ids(&stage.records),
        partition_counts,
    };
    let manifest_value = serde_json::to_value(manifest).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("package manifest encoding: {error}"),
        )
    })?;
    let mut manifest_bytes = canonical_json(&manifest_value, "package manifest")?;
    manifest_bytes.push(b'\n');

    let destination = AtomicDirectory::create(output)?;
    write_new_file(&destination.path().join(PACKAGE_BYTES), &package)?;
    write_new_file(&destination.path().join(PACKAGE_MANIFEST), &manifest_bytes)?;
    destination.commit()?;
    Ok(stage.records.len())
}

pub fn remove_source(input: &Path, source_id: &str, output: &Path) -> Result<usize> {
    if !valid_identifier(source_id) {
        return Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "invalid removal source ID",
        ));
    }
    let mut stage = read_stage(input)?;
    let input_hash = stage.manifest.records_sha256.clone();
    stage.records.retain(|record| record.source_id != source_id);
    if stage
        .records
        .iter()
        .any(|record| record.source_id == source_id)
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "source removal was incomplete",
        ));
    }
    sort_records(&mut stage.records);
    let count = stage.records.len();
    write_stage(
        output,
        "filtered",
        "source-id-filter-v1",
        &stage.manifest.source_manifest_sha256,
        Some(&input_hash),
        &stage.records,
    )?;
    Ok(count)
}

fn import_records(source: &VerifiedSource) -> Result<Vec<PipelineRecord>> {
    let mut output = Vec::new();
    let mut identities = BTreeSet::new();
    for artifact in &source.artifacts {
        if artifact.descriptor.content_type != "application/x-ndjson" {
            continue;
        }
        for (index, line) in jsonl_lines(&artifact.bytes, false)?.into_iter().enumerate() {
            if line.is_empty() || line.len() > MAX_RECORD_BYTES {
                return Err(DataError::new(
                    DataErrorCode::ResourceLimit,
                    format!("JSONL line size: {}", artifact.descriptor.path),
                ));
            }
            let value =
                parse_strict_json(line, &format!("{}:{}", artifact.descriptor.path, index + 1))?;
            let source_id = required_string(&value, "source_id", &artifact.descriptor.path)?;
            if source_id != source.manifest.source.id {
                return Err(DataError::new(
                    DataErrorCode::InvalidRecord,
                    "record source differs from source manifest",
                ));
            }
            let record_id = record_identity(&value, &artifact.descriptor.path)?;
            if !identities.insert(record_id.clone()) {
                return Err(DataError::new(
                    DataErrorCode::InvalidRecord,
                    "duplicate record identity",
                ));
            }
            let record = PipelineRecord {
                schema_version: 1,
                source_id,
                artifact_path: artifact.descriptor.path.clone(),
                record_id,
                assigned_partition: None,
                record: value,
            };
            validate_pipeline_record(&record)?;
            output.push(record);
            if output.len() > MAX_RECORDS {
                return Err(DataError::new(
                    DataErrorCode::ResourceLimit,
                    "pipeline record count",
                ));
            }
        }
    }
    Ok(output)
}

fn write_stage(
    output: &Path,
    stage: &str,
    transform_id: &str,
    source_manifest_sha256: &str,
    input_records_sha256: Option<&str>,
    records: &[PipelineRecord],
) -> Result<()> {
    let records_bytes = encode_records(records)?;
    let records_sha256 = sha256_hex(&records_bytes)?;
    let record_count = u64::try_from(records.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "stage record count"))?;
    let manifest = StageManifest {
        schema_version: 1,
        stage: stage.to_owned(),
        transform_id: transform_id.to_owned(),
        source_manifest_sha256: source_manifest_sha256.to_owned(),
        input_records_sha256: input_records_sha256.map(str::to_owned),
        records_sha256,
        record_count,
        source_ids: source_ids(records),
    };
    validate_stage_manifest(&manifest)?;
    let value = serde_json::to_value(manifest).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("stage manifest encoding: {error}"),
        )
    })?;
    let mut manifest_bytes = canonical_json(&value, "stage manifest")?;
    manifest_bytes.push(b'\n');

    let destination = AtomicDirectory::create(output)?;
    write_new_file(&destination.path().join(STAGE_RECORDS), &records_bytes)?;
    write_new_file(&destination.path().join(STAGE_MANIFEST), &manifest_bytes)?;
    destination.commit()
}

fn read_stage(input: &Path) -> Result<Stage> {
    let manifest_bytes = read_root_file(input, STAGE_MANIFEST, MAX_ARTIFACT_BYTES)?;
    let manifest_value = parse_strict_json(&manifest_bytes, "stage manifest")?;
    let manifest: StageManifest = serde_json::from_value(manifest_value).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidRecord,
            format!("stage manifest shape: {error}"),
        )
    })?;
    validate_stage_manifest(&manifest)?;

    let records_bytes = read_root_file(input, STAGE_RECORDS, MAX_STAGE_BYTES)?;
    if sha256_hex(&records_bytes)? != manifest.records_sha256 {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "stage records SHA-256",
        ));
    }
    let mut records = Vec::new();
    let mut identities = BTreeSet::new();
    for (index, line) in jsonl_lines(&records_bytes, true)?.into_iter().enumerate() {
        if line.is_empty() || line.len() > MAX_RECORD_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "stage record size",
            ));
        }
        let value = parse_strict_json(line, &format!("stage record {}", index + 1))?;
        let record: PipelineRecord = serde_json::from_value(value.clone()).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("stage record shape: {error}"),
            )
        })?;
        if canonical_json(&value, "stage record")? != line {
            return Err(DataError::new(
                DataErrorCode::InvalidRecord,
                "noncanonical stage record",
            ));
        }
        validate_pipeline_record(&record)?;
        if !identities.insert(record.record_id.clone()) {
            return Err(DataError::new(
                DataErrorCode::InvalidRecord,
                "duplicate stage record identity",
            ));
        }
        records.push(record);
        if records.len() > MAX_RECORDS {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "stage record count",
            ));
        }
    }
    let expected_count = u64::try_from(records.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "stage record count"))?;
    if expected_count != manifest.record_count || source_ids(&records) != manifest.source_ids {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "stage manifest inventory",
        ));
    }
    Ok(Stage { manifest, records })
}

fn validate_stage_manifest(manifest: &StageManifest) -> Result<()> {
    let stages = ["imported", "normalized", "split", "filtered"];
    if manifest.schema_version != 1
        || !stages.contains(&manifest.stage.as_str())
        || manifest.transform_id.is_empty()
        || !is_sha256(&manifest.source_manifest_sha256)
        || !is_sha256(&manifest.records_sha256)
        || manifest
            .input_records_sha256
            .as_deref()
            .is_some_and(|value| !is_sha256(value))
        || (manifest.stage == "imported" && manifest.input_records_sha256.is_some())
        || (manifest.stage != "imported" && manifest.input_records_sha256.is_none())
        || manifest.source_ids.iter().any(|id| !valid_identifier(id))
        || !manifest.source_ids.windows(2).all(|pair| pair[0] < pair[1])
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "invalid stage manifest",
        ));
    }
    Ok(())
}

fn validate_pipeline_record(record: &PipelineRecord) -> Result<()> {
    if record.schema_version != 1
        || !valid_identifier(&record.source_id)
        || !valid_identifier(&record.record_id)
        || !record.artifact_path.ends_with(".jsonl")
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "invalid pipeline record identity",
        ));
    }
    validate_relative_path(&record.artifact_path)?;
    let object = record.record.as_object().ok_or_else(|| {
        DataError::new(DataErrorCode::InvalidRecord, "record root is not an object")
    })?;
    for (field, expected) in [
        ("source_id", record.source_id.as_str()),
        ("corpus_version", "1.0.0"),
        ("generator_id", "p02-generator-v1"),
        ("license", "Apache-2.0"),
        ("locale", "pt-BR"),
    ] {
        if object.get(field).and_then(Value::as_str) != Some(expected) {
            return Err(DataError::new(
                DataErrorCode::InvalidRecord,
                format!("record provenance field: {field}"),
            ));
        }
    }
    if object
        .get("oracle_origin")
        .is_some_and(|value| value.as_str() != Some("pre_engine_generator_specification"))
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "unauthorized oracle origin",
        ));
    }
    if contains_forbidden_fixture(&record.record) {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "technical fixture in linguistic data",
        ));
    }
    if record_identity(&record.record, &record.artifact_path)? != record.record_id {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "record identity differs",
        ));
    }
    if record
        .assigned_partition
        .as_deref()
        .is_some_and(|partition| !valid_partition(partition))
    {
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "invalid assigned partition",
        ));
    }
    Ok(())
}

fn assign_partitions(records: &mut [PipelineRecord]) -> Result<()> {
    let mut families = BTreeMap::<String, String>::new();
    for record in records {
        let partition = record_partition(record)?;
        if let Some(family) = record_family(&record.record)
            && let Some(previous) = families.insert(family.clone(), partition.clone())
            && previous != partition
        {
            return Err(DataError::new(
                DataErrorCode::SplitLeakage,
                "family crosses frozen partitions",
            ));
        }
        record.assigned_partition = Some(partition);
    }
    Ok(())
}

fn record_partition(record: &PipelineRecord) -> Result<String> {
    if let Some(split) = record.record.get("split").and_then(Value::as_str) {
        if ["train", "development", "heldout", "performance"].contains(&split) {
            return Ok(split.to_owned());
        }
        return Err(DataError::new(
            DataErrorCode::InvalidRecord,
            "unknown frozen split",
        ));
    }
    if let Some((_, suffix)) = record.artifact_path.split_once("/suites/") {
        let name = suffix
            .strip_suffix(".jsonl")
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "suite artifact suffix"))?;
        let partition = format!("suite_{}", name.replace('-', "_"));
        if valid_partition(&partition) {
            return Ok(partition);
        }
    }
    Ok("shared".to_owned())
}

fn record_family(value: &Value) -> Option<String> {
    value
        .get("dimensions")
        .and_then(|dimensions| dimensions.get("family"))
        .and_then(Value::as_str)
        .or_else(|| value.get("family").and_then(Value::as_str))
        .map(str::to_owned)
}

fn encode_records(records: &[PipelineRecord]) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    for record in records {
        let value = serde_json::to_value(record).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("pipeline record encoding: {error}"),
            )
        })?;
        let bytes = canonical_json(&value, "pipeline record")?;
        if bytes.len() > MAX_RECORD_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "encoded pipeline record size",
            ));
        }
        output.extend_from_slice(&bytes);
        output.push(b'\n');
        if output.len() > MAX_STAGE_BYTES {
            return Err(DataError::new(
                DataErrorCode::ResourceLimit,
                "encoded stage size",
            ));
        }
    }
    Ok(output)
}

fn encode_package(records: &[PipelineRecord]) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    output.extend_from_slice(PACKAGE_HEADER);
    let count = u64::try_from(records.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "package record count"))?;
    output.extend_from_slice(&count.to_be_bytes());
    for record in records {
        let value = serde_json::to_value(record).map_err(|error| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("package record encoding: {error}"),
            )
        })?;
        let bytes = canonical_json(&value, "package record")?;
        let length = u32::try_from(bytes.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "package record size"))?;
        output.extend_from_slice(&length.to_be_bytes());
        output.extend_from_slice(&bytes);
        if output.len() > MAX_STAGE_BYTES {
            return Err(DataError::new(DataErrorCode::ResourceLimit, "package size"));
        }
    }
    Ok(output)
}

fn sort_records(records: &mut [PipelineRecord]) {
    records.sort_by(|left, right| {
        (
            left.source_id.as_str(),
            left.assigned_partition.as_deref().unwrap_or(""),
            left.artifact_path.as_str(),
            left.record_id.as_str(),
        )
            .cmp(&(
                right.source_id.as_str(),
                right.assigned_partition.as_deref().unwrap_or(""),
                right.artifact_path.as_str(),
                right.record_id.as_str(),
            ))
    });
}

fn source_ids(records: &[PipelineRecord]) -> Vec<String> {
    records
        .iter()
        .map(|record| record.source_id.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn record_identity(value: &Value, context: &str) -> Result<String> {
    for field in ["generator_record_id", "analysis_id", "case_id"] {
        if let Some(identifier) = value.get(field).and_then(Value::as_str) {
            if valid_identifier(identifier) {
                return Ok(identifier.to_owned());
            }
            return Err(DataError::new(
                DataErrorCode::InvalidRecord,
                format!("invalid {field}: {context}"),
            ));
        }
    }
    Err(DataError::new(
        DataErrorCode::InvalidRecord,
        format!("missing record identity: {context}"),
    ))
}

fn required_string(value: &Value, field: &str, context: &str) -> Result<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| {
            DataError::new(
                DataErrorCode::InvalidRecord,
                format!("missing {field}: {context}"),
            )
        })
}

fn contains_forbidden_fixture(value: &Value) -> bool {
    match value {
        Value::String(text) => text.contains("FIXTURE_TECNICA"),
        Value::Array(values) => values.iter().any(contains_forbidden_fixture),
        Value::Object(values) => values.iter().any(|(key, child)| {
            key.contains("FIXTURE_TECNICA") || contains_forbidden_fixture(child)
        }),
        _ => false,
    }
}

fn jsonl_lines(bytes: &[u8], allow_empty: bool) -> Result<Vec<&[u8]>> {
    if bytes.is_empty() {
        return if allow_empty {
            Ok(Vec::new())
        } else {
            Err(DataError::new(
                DataErrorCode::InvalidRecord,
                "empty JSONL artifact",
            ))
        };
    }
    let content = bytes
        .strip_suffix(b"\n")
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidRecord, "JSONL lacks final newline"))?;
    Ok(content.split(|byte| *byte == b'\n').collect())
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
}

fn valid_partition(value: &str) -> bool {
    valid_identifier(value) && !value.contains(':')
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn write_relative_file(root: &Path, relative: &str, bytes: &[u8]) -> Result<()> {
    validate_relative_path(relative)?;
    let path = root.join(relative);
    let parent = path
        .parent()
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidPath, "output path has no parent"))?;
    fs::create_dir_all(parent)
        .map_err(|_| DataError::new(DataErrorCode::Io, "create output directory"))?;
    write_new_file(&path, bytes)
}

pub(crate) fn write_new_file(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::io::Write as _;

    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| DataError::new(DataErrorCode::Io, "create output file"))?;
    file.write_all(bytes)
        .map_err(|_| DataError::new(DataErrorCode::Io, "write output file"))?;
    file.sync_all()
        .map_err(|_| DataError::new(DataErrorCode::Io, "sync output file"))
}

pub(crate) struct AtomicDirectory {
    temporary: PathBuf,
    destination: PathBuf,
    committed: bool,
}

impl AtomicDirectory {
    pub(crate) fn create(destination: &Path) -> Result<Self> {
        if fs::symlink_metadata(destination).is_ok() {
            return Err(DataError::new(
                DataErrorCode::OutputExists,
                "output destination already exists",
            ));
        }
        let parent = destination
            .parent()
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidPath, "output has no parent"))?;
        let parent_metadata = fs::symlink_metadata(parent).map_err(|_| {
            DataError::new(DataErrorCode::InvalidPath, "output parent does not exist")
        })?;
        if parent_metadata.file_type().is_symlink() || !parent_metadata.is_dir() {
            return Err(DataError::new(
                DataErrorCode::InvalidPath,
                "unsafe output parent",
            ));
        }
        let name = destination
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty() && *name != "." && *name != "..")
            .ok_or_else(|| DataError::new(DataErrorCode::InvalidPath, "output name"))?;
        let temporary = parent.join(format!(".{name}.nlu-data-tmp"));
        if fs::symlink_metadata(&temporary).is_ok() {
            return Err(DataError::new(
                DataErrorCode::OutputExists,
                "temporary output already exists",
            ));
        }
        fs::create_dir(&temporary)
            .map_err(|_| DataError::new(DataErrorCode::Io, "create temporary output"))?;
        Ok(Self {
            temporary,
            destination: destination.to_path_buf(),
            committed: false,
        })
    }

    pub(crate) fn path(&self) -> &Path {
        &self.temporary
    }

    pub(crate) fn commit(mut self) -> Result<()> {
        fs::rename(&self.temporary, &self.destination)
            .map_err(|_| DataError::new(DataErrorCode::Io, "promote output"))?;
        self.committed = true;
        Ok(())
    }
}

impl Drop for AtomicDirectory {
    fn drop(&mut self) {
        if !self.committed {
            let _ = fs::remove_dir_all(&self.temporary);
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{PipelineRecord, assign_partitions, encode_package, sort_records};
    use crate::DataErrorCode;

    fn record(id: &str, split: &str, family: &str) -> PipelineRecord {
        PipelineRecord {
            schema_version: 1,
            source_id: "project-authored-synthetic-ptbr-v1".to_owned(),
            artifact_path: format!("data/{split}.jsonl"),
            record_id: id.to_owned(),
            assigned_partition: None,
            record: json!({
                "case_id": id,
                "corpus_version": "1.0.0",
                "dimensions": {"family": family},
                "generator_id": "p02-generator-v1",
                "license": "Apache-2.0",
                "locale": "pt-BR",
                "source_id": "project-authored-synthetic-ptbr-v1",
                "split": split
            }),
        }
    }

    #[test]
    fn rejects_family_leakage() {
        let mut records = vec![
            record("p03:case-1", "train", "shared-family"),
            record("p03:case-2", "heldout", "shared-family"),
        ];
        let error = assign_partitions(&mut records).expect_err("family leakage");
        assert_eq!(error.code(), DataErrorCode::SplitLeakage);
    }

    #[test]
    fn package_is_independent_of_input_order_after_sort() {
        let mut first = vec![
            record("p03:case-2", "train", "family-2"),
            record("p03:case-1", "train", "family-1"),
        ];
        assign_partitions(&mut first).expect("partitions");
        let mut second = first.iter().cloned().rev().collect::<Vec<_>>();
        sort_records(&mut first);
        sort_records(&mut second);
        assert_eq!(
            encode_package(&first).expect("first package"),
            encode_package(&second).expect("second package")
        );
    }

    #[test]
    fn rejects_missing_provenance_and_technical_fixture_content() {
        let mut missing = record("p03:case-1", "train", "family-1");
        missing
            .record
            .as_object_mut()
            .expect("record object")
            .remove("license");
        assert!(super::validate_pipeline_record(&missing).is_err());

        let mut fixture = record("p03:case-2", "train", "family-2");
        fixture
            .record
            .as_object_mut()
            .expect("record object")
            .insert(
                "utterance".to_owned(),
                serde_json::Value::String("FIXTURE_TECNICA".to_owned()),
            );
        assert!(super::validate_pipeline_record(&fixture).is_err());
    }
}
