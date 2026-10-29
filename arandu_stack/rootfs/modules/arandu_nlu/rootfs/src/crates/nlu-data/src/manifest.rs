use std::{
    collections::BTreeSet,
    fs,
    path::{Component, Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    DataError, DataErrorCode, Result,
    hash::{Sha256, sha256_hex},
    json::parse_strict_json,
};

pub(crate) const MAX_MANIFEST_BYTES: usize = 1024 * 1024;
pub(crate) const MAX_ARTIFACT_BYTES: usize = 20 * 1024 * 1024;
pub(crate) const MAX_ARTIFACTS: usize = 128;
pub(crate) const MAX_RELATIVE_PATH_BYTES: usize = 512;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceManifest {
    pub schema_version: u32,
    pub source: SourceDescriptor,
    pub recipes: Recipes,
    pub source_reviews: Vec<SourceReview>,
    pub source_decision: SourceDecision,
    pub per_entry_provenance_fields: Vec<String>,
    pub artifacts: Vec<ArtifactDescriptor>,
    pub aggregate: AggregateDescriptor,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceDescriptor {
    pub id: String,
    pub admission_status: String,
    pub admission_basis: String,
    pub authorization: String,
    pub claim_scope: String,
    pub canonical_source_url: String,
    pub upstream_owner: String,
    pub immutable_version: String,
    pub license: String,
    pub license_path: String,
    pub license_url: String,
    pub license_sha256: String,
    pub intended_use: String,
    pub redistribution: String,
    pub external_evidence_disposition: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Recipes {
    pub fetch: String,
    pub extract: String,
    pub normalize: String,
    pub split: String,
    pub compile: String,
    pub remove: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceReview {
    pub role: String,
    pub review_id: String,
    pub disposition: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceDecision {
    pub adr: String,
    pub status: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactDescriptor {
    pub path: String,
    pub role: String,
    pub content_type: String,
    pub bytes: u64,
    pub sha256: String,
    pub records: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AggregateDescriptor {
    pub bytes: u64,
    pub sha256: String,
}

pub(crate) struct VerifiedArtifact {
    pub descriptor: ArtifactDescriptor,
    pub bytes: Vec<u8>,
}

pub(crate) struct VerifiedSource {
    pub manifest: SourceManifest,
    pub manifest_bytes: Vec<u8>,
    pub manifest_sha256: String,
    pub artifacts: Vec<VerifiedArtifact>,
    pub license_bytes: Vec<u8>,
    pub decision_bytes: Vec<u8>,
}

pub(crate) fn load_manifest(path: &Path) -> Result<(SourceManifest, Vec<u8>, String)> {
    let bytes = read_direct_regular_file(path, MAX_MANIFEST_BYTES, "source manifest")?;
    let value = parse_strict_json(&bytes, "source manifest")?;
    let manifest: SourceManifest = serde_json::from_value(value).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidManifest,
            format!("source manifest shape: {error}"),
        )
    })?;
    let digest = sha256_hex(&bytes)?;
    Ok((manifest, bytes, digest))
}

pub(crate) fn verify_source(manifest_path: &Path, root: &Path) -> Result<VerifiedSource> {
    verify_source_with_manifest_pin(manifest_path, root, None)
}

pub(crate) fn verify_source_pinned(
    manifest_path: &Path,
    root: &Path,
    expected_manifest_sha256: &str,
) -> Result<VerifiedSource> {
    verify_source_with_manifest_pin(manifest_path, root, Some(expected_manifest_sha256))
}

fn verify_source_with_manifest_pin(
    manifest_path: &Path,
    root: &Path,
    expected_manifest_sha256: Option<&str>,
) -> Result<VerifiedSource> {
    validate_root(root)?;
    let (manifest, manifest_bytes, manifest_sha256) = load_manifest(manifest_path)?;
    if expected_manifest_sha256.is_some_and(|expected| manifest_sha256 != expected) {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "pinned source manifest SHA-256",
        ));
    }
    let decision_bytes = validate_manifest_contract(&manifest, root)?;

    let mut aggregate = Sha256::new();
    let mut total_bytes = 0_u64;
    let mut verified = Vec::with_capacity(manifest.artifacts.len());
    for artifact in &manifest.artifacts {
        let bytes = read_root_file(root, &artifact.path, MAX_ARTIFACT_BYTES)?;
        let byte_count = u64::try_from(bytes.len())
            .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "artifact byte count"))?;
        if byte_count != artifact.bytes {
            return Err(DataError::new(
                DataErrorCode::IntegrityMismatch,
                format!("artifact size: {}", artifact.path),
            ));
        }
        if sha256_hex(&bytes)? != artifact.sha256 {
            return Err(DataError::new(
                DataErrorCode::IntegrityMismatch,
                format!("artifact SHA-256: {}", artifact.path),
            ));
        }
        validate_artifact_content(artifact, &bytes)?;
        total_bytes = total_bytes.checked_add(byte_count).ok_or_else(|| {
            DataError::new(DataErrorCode::ResourceLimit, "aggregate artifact bytes")
        })?;
        aggregate.update(artifact.path.as_bytes())?;
        aggregate.update(&[0])?;
        aggregate.update(&bytes)?;
        aggregate.update(&[0])?;
        verified.push(VerifiedArtifact {
            descriptor: artifact.clone(),
            bytes,
        });
    }
    if total_bytes != manifest.aggregate.bytes
        || crate::hash::hex(&aggregate.finalize()) != manifest.aggregate.sha256
    {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "aggregate source identity",
        ));
    }

    let license_bytes = read_root_file(root, &manifest.source.license_path, MAX_ARTIFACT_BYTES)?;
    if sha256_hex(&license_bytes)? != manifest.source.license_sha256 {
        return Err(DataError::new(
            DataErrorCode::IntegrityMismatch,
            "license SHA-256",
        ));
    }

    Ok(VerifiedSource {
        manifest,
        manifest_bytes,
        manifest_sha256,
        artifacts: verified,
        license_bytes,
        decision_bytes,
    })
}

fn validate_manifest_contract(manifest: &SourceManifest, root: &Path) -> Result<Vec<u8>> {
    if manifest.schema_version != 1 {
        return invalid_manifest("unsupported source manifest version");
    }
    let source = &manifest.source;
    let expected_source = [
        (&source.admission_status, "PROJECT_AUTHORED_SYNTHETIC"),
        (&source.admission_basis, "USER_DELEGATED_AUTONOMY"),
        (&source.authorization, "USR-016"),
        (&source.claim_scope, "internal_conformance_only"),
        (&source.upstream_owner, "project_contributors"),
        (&source.license, "Apache-2.0"),
        (
            &source.external_evidence_disposition,
            "not_applicable_project_authored",
        ),
    ];
    if source.id != "project-authored-synthetic-ptbr-v1"
        || source.immutable_version != "1.0.0"
        || source.canonical_source_url != "repository://self/data/project-authored/p02-v1"
        || source.license_path != "LICENSE"
        || source.license_url != "repository://self/LICENSE"
        || source.intended_use != "deterministic_internal_conformance_and_language_package"
        || source.redistribution != "apache_2_0_source_and_generated_outputs"
        || expected_source
            .iter()
            .any(|(actual, expected)| actual != expected)
        || !is_sha256(&source.license_sha256)
    {
        return invalid_manifest("project-authored source contract");
    }

    let recipes = &manifest.recipes;
    if recipes.fetch != "local-manifest-copy-v1"
        || recipes.extract != "identity-v1"
        || recipes.normalize != "canonical-json-v1"
        || recipes.split != "frozen-record-split-v1"
        || recipes.compile != "nlu-data-package-v1"
        || recipes.remove != "source-id-filter-v1"
    {
        return invalid_manifest("recipe contract");
    }

    let roles: Vec<&str> = manifest
        .source_reviews
        .iter()
        .map(|review| review.role.as_str())
        .collect();
    if roles != ["adversary", "discovery", "license", "provenance", "quality"]
        || manifest.source_reviews.iter().any(|review| {
            review.review_id.is_empty() || review.disposition != "USER_WAIVED_PROJECT_AUTHORED"
        })
    {
        return invalid_manifest("source review dispositions");
    }

    if manifest.source_decision.adr != "docs/adr/ADR-0009-deterministic-data-pipeline.md"
        || manifest.source_decision.status != "ACCEPTED_AUTONOMOUS"
    {
        return invalid_manifest("source decision");
    }
    let adr = read_root_file(root, &manifest.source_decision.adr, MAX_MANIFEST_BYTES)?;
    if !adr
        .windows(b"- Status: `ACCEPTED_AUTONOMOUS`".len())
        .any(|window| window == b"- Status: `ACCEPTED_AUTONOMOUS`")
    {
        return invalid_manifest("source decision status");
    }

    let expected_provenance = [
        "source_id",
        "corpus_version",
        "generator_id",
        "license",
        "locale",
    ];
    if manifest
        .per_entry_provenance_fields
        .iter()
        .map(String::as_str)
        .ne(expected_provenance)
    {
        return invalid_manifest("per-entry provenance fields");
    }

    if manifest.artifacts.is_empty() || manifest.artifacts.len() > MAX_ARTIFACTS {
        return invalid_manifest("artifact count");
    }
    let mut paths = BTreeSet::new();
    let mut roles = BTreeSet::new();
    let mut previous = None::<&str>;
    for artifact in &manifest.artifacts {
        validate_relative_path(&artifact.path)?;
        if previous.is_some_and(|path| path >= artifact.path.as_str())
            || !paths.insert(artifact.path.as_str())
            || !roles.insert(artifact.role.as_str())
            || artifact.bytes == 0
            || !is_sha256(&artifact.sha256)
        {
            return invalid_manifest("artifact identities or ordering");
        }
        previous = Some(&artifact.path);
        match artifact.content_type.as_str() {
            "application/x-ndjson" if artifact.records.is_some() => {}
            "application/json" | "application/yaml" if artifact.records.is_none() => {}
            _ => return invalid_manifest("artifact content type or record count"),
        }
    }
    if manifest.aggregate.bytes == 0 || !is_sha256(&manifest.aggregate.sha256) {
        return invalid_manifest("aggregate identity");
    }
    Ok(adr)
}

fn validate_artifact_content(artifact: &ArtifactDescriptor, bytes: &[u8]) -> Result<()> {
    match artifact.content_type.as_str() {
        "application/x-ndjson" => {
            if bytes.last() != Some(&b'\n') || core::str::from_utf8(bytes).is_err() {
                return Err(DataError::new(
                    DataErrorCode::InvalidRecord,
                    format!("JSONL encoding: {}", artifact.path),
                ));
            }
            let records = u64::try_from(bytes.iter().filter(|byte| **byte == b'\n').count())
                .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, "record count"))?;
            if Some(records) != artifact.records {
                return Err(DataError::new(
                    DataErrorCode::IntegrityMismatch,
                    format!("record count: {}", artifact.path),
                ));
            }
        }
        "application/json" => {
            parse_strict_json(bytes, &artifact.path)?;
        }
        "application/yaml" => {
            if core::str::from_utf8(bytes).is_err() || bytes.is_empty() {
                return Err(DataError::new(
                    DataErrorCode::InvalidRecord,
                    format!("YAML encoding: {}", artifact.path),
                ));
            }
        }
        _ => return invalid_manifest("unsupported artifact content type"),
    }
    Ok(())
}

pub(crate) fn validate_relative_path(path: &str) -> Result<()> {
    if path.is_empty()
        || path.len() > MAX_RELATIVE_PATH_BYTES
        || path.contains('\\')
        || !Path::new(path)
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err(DataError::new(
            DataErrorCode::InvalidPath,
            format!("noncanonical relative path: {path}"),
        ));
    }
    Ok(())
}

pub(crate) fn read_root_file(root: &Path, relative: &str, limit: usize) -> Result<Vec<u8>> {
    validate_relative_path(relative)?;
    validate_root(root)?;
    let mut current = PathBuf::from(root);
    let components: Vec<_> = Path::new(relative).components().collect();
    for (index, component) in components.iter().enumerate() {
        let Component::Normal(name) = component else {
            return Err(DataError::new(DataErrorCode::InvalidPath, relative));
        };
        current.push(name);
        let metadata = fs::symlink_metadata(&current).map_err(|_| {
            DataError::new(
                DataErrorCode::InvalidPath,
                format!("missing source path: {relative}"),
            )
        })?;
        if metadata.file_type().is_symlink()
            || (index + 1 == components.len() && !metadata.is_file())
            || (index + 1 != components.len() && !metadata.is_dir())
        {
            return Err(DataError::new(
                DataErrorCode::InvalidPath,
                format!("unsafe source path: {relative}"),
            ));
        }
    }
    read_direct_regular_file(&current, limit, relative)
}

pub(crate) fn read_direct_regular_file(
    path: &Path,
    limit: usize,
    context: &str,
) -> Result<Vec<u8>> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| DataError::new(DataErrorCode::Io, format!("missing {context}")))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(DataError::new(
            DataErrorCode::InvalidPath,
            format!("not a regular file: {context}"),
        ));
    }
    let size = usize::try_from(metadata.len())
        .map_err(|_| DataError::new(DataErrorCode::ResourceLimit, context))?;
    if size > limit {
        return Err(DataError::new(
            DataErrorCode::ResourceLimit,
            format!("oversized {context}"),
        ));
    }
    fs::read(path).map_err(|_| DataError::new(DataErrorCode::Io, format!("read {context}")))
}

fn validate_root(root: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(root)
        .map_err(|_| DataError::new(DataErrorCode::InvalidPath, "missing source root"))?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(DataError::new(
            DataErrorCode::InvalidPath,
            "source root is not a regular directory",
        ));
    }
    Ok(())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn invalid_manifest<T>(context: &str) -> Result<T> {
    Err(DataError::new(DataErrorCode::InvalidManifest, context))
}
