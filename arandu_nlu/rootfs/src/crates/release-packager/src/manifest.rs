use std::collections::BTreeMap;

use crate::authorization::{
    Architecture, ArchitectureEnablement, ArchitectureStage, EmissionMode, EmissionPolicy,
};
use crate::hash::sha256_hex;
use crate::json::{JsonValue, canonical_json, object};
use crate::path::{validate_attestation_id, validate_identifier};
use crate::reconcile::{
    ByteClaim, ByteOrigin, DeclaredInput, ReconciliationReport, ShippedFile, reconcile,
};
use crate::{PackagerError, PackagerErrorCode, Result};

const MANIFEST_PATH: &str = "release-manifest.json";
const CHECKSUMS_PATH: &str = "SHA256SUMS";
const GENERATED_TRANSFORMATION: &str = "release-packager-v1";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReleaseMetadataRequest {
    pub release_id: String,
    pub release_version: String,
    pub inputs: Vec<DeclaredInput>,
    pub files: Vec<ShippedFile>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReleaseMetadata {
    manifest_bytes: Vec<u8>,
    manifest_sha256: String,
    checksums_bytes: Vec<u8>,
    checksums_sha256: String,
    reconciliation: ReconciliationReport,
    mode: EmissionMode,
}

impl ReleaseMetadata {
    pub fn manifest_bytes(&self) -> &[u8] {
        &self.manifest_bytes
    }

    pub fn manifest_sha256(&self) -> &str {
        &self.manifest_sha256
    }

    pub fn checksums_bytes(&self) -> &[u8] {
        &self.checksums_bytes
    }

    pub fn checksums_sha256(&self) -> &str {
        &self.checksums_sha256
    }

    pub fn reconciliation(&self) -> &ReconciliationReport {
        &self.reconciliation
    }

    pub const fn mode(&self) -> EmissionMode {
        self.mode
    }
}

pub fn emit_release_metadata(
    request: &ReleaseMetadataRequest,
    policy: &EmissionPolicy,
) -> Result<ReleaseMetadata> {
    let targets = policy.validate()?;
    validate_attestation_id(&request.release_id, "release ID")?;
    validate_identifier(&request.release_version, "release version")?;
    validate_reserved_paths(&request.files)?;

    let payload_reconciliation = reconcile(&request.inputs, &request.files, policy.mode())?;
    let manifest_bytes = release_manifest(request, policy, &targets, &payload_reconciliation)?;
    let manifest_sha256 = sha256_hex(&manifest_bytes)?;
    let checksums_bytes = checksums(&request.files, &manifest_bytes)?;
    let checksums_sha256 = sha256_hex(&checksums_bytes)?;

    let all_input_ids: Vec<_> = payload_reconciliation
        .inputs
        .iter()
        .map(|input| input.id.clone())
        .collect();
    let mut reconciled_shipped_files = request.files.clone();
    reconciled_shipped_files.push(generated_file(
        MANIFEST_PATH,
        manifest_bytes.clone(),
        &all_input_ids,
    )?);
    reconciled_shipped_files.push(generated_file(
        CHECKSUMS_PATH,
        checksums_bytes.clone(),
        &all_input_ids,
    )?);
    let reconciliation = reconcile(&request.inputs, &reconciled_shipped_files, policy.mode())?;

    Ok(ReleaseMetadata {
        manifest_bytes,
        manifest_sha256,
        checksums_bytes,
        checksums_sha256,
        reconciliation,
        mode: policy.mode(),
    })
}

fn release_manifest(
    request: &ReleaseMetadataRequest,
    policy: &EmissionPolicy,
    targets: &[Architecture],
    reconciliation: &ReconciliationReport,
) -> Result<Vec<u8>> {
    let files = reconciliation
        .files
        .iter()
        .map(|file| {
            object([
                ("mode", JsonValue::String(format!("{:04o}", file.mode))),
                ("path", JsonValue::String(file.path.clone())),
                ("sha256", JsonValue::String(file.sha256.clone())),
                ("size", JsonValue::Number(file.size)),
            ])
        })
        .collect();
    let inputs = reconciliation
        .inputs
        .iter()
        .map(|input| {
            object([
                ("id", JsonValue::String(input.id.clone())),
                ("license", JsonValue::String(input.license.clone())),
                ("purpose", JsonValue::String(input.purpose.clone())),
                ("sha256", JsonValue::String(input.sha256.clone())),
                ("size", JsonValue::Number(input.size)),
            ])
        })
        .collect();
    let authorization = policy.authorization();
    let architecture_states: BTreeMap<_, _> = authorization
        .architectures
        .iter()
        .map(|state| (state.architecture, state))
        .collect();
    let architecture_values = targets
        .iter()
        .map(|architecture| {
            architecture_state_json(
                *architecture,
                architecture_states.get(architecture).copied(),
            )
        })
        .collect();

    let value = object([
        (
            "architectures",
            JsonValue::Array(
                targets
                    .iter()
                    .map(|architecture| JsonValue::String(architecture.as_oci_str().to_owned()))
                    .collect(),
            ),
        ),
        (
            "authorization",
            object([
                ("architectureStates", JsonValue::Array(architecture_values)),
                (
                    "artifactBuildAllowed",
                    JsonValue::Bool(authorization.artifact_build_allowed),
                ),
                (
                    "inheritedP14Gate",
                    optional_string(&authorization.inherited_p14_gate),
                ),
                (
                    "licenseReconciliation",
                    optional_string(&authorization.license_reconciliation),
                ),
                (
                    "p15ReleaseGate",
                    optional_string(&authorization.p15_release_gate),
                ),
                (
                    "sourceAdmission",
                    optional_string(&authorization.source_admission),
                ),
            ]),
        ),
        (
            "emissionMode",
            JsonValue::String(match policy.mode() {
                EmissionMode::DryRun => "dry-run".to_owned(),
                EmissionMode::Production => "production".to_owned(),
            }),
        ),
        ("files", JsonValue::Array(files)),
        ("inputs", JsonValue::Array(inputs)),
        (
            "payloadReconciliation",
            object([
                (
                    "sha256",
                    JsonValue::String(reconciliation.sha256().to_owned()),
                ),
                (
                    "size",
                    JsonValue::Number(byte_len(
                        reconciliation.bytes().len(),
                        "payload reconciliation",
                    )?),
                ),
            ]),
        ),
        ("releaseId", JsonValue::String(request.release_id.clone())),
        (
            "releaseVersion",
            JsonValue::String(request.release_version.clone()),
        ),
        ("schemaVersion", JsonValue::Number(1)),
    ]);
    let mut bytes = canonical_json(&value);
    bytes.push(b'\n');
    Ok(bytes)
}

fn architecture_state_json(
    architecture: Architecture,
    state: Option<&ArchitectureEnablement>,
) -> JsonValue {
    let stage = state
        .map(|state| state.stage)
        .unwrap_or(ArchitectureStage::Disabled);
    object([
        (
            "architecture",
            JsonValue::String(architecture.as_oci_str().to_owned()),
        ),
        (
            "buildAdmission",
            state
                .map(|state| optional_string(&state.build_admission))
                .unwrap_or(JsonValue::Null),
        ),
        (
            "enablement",
            state
                .map(|state| optional_string(&state.enablement))
                .unwrap_or(JsonValue::Null),
        ),
        (
            "haLifecycleValidation",
            state
                .map(|state| optional_string(&state.ha_lifecycle_validation))
                .unwrap_or(JsonValue::Null),
        ),
        (
            "nativeValidation",
            state
                .map(|state| optional_string(&state.native_validation))
                .unwrap_or(JsonValue::Null),
        ),
        (
            "reproducibility",
            state
                .map(|state| optional_string(&state.reproducibility))
                .unwrap_or(JsonValue::Null),
        ),
        ("stage", JsonValue::String(stage.as_str().to_owned())),
    ])
}

fn optional_string(value: &Option<String>) -> JsonValue {
    value
        .as_ref()
        .map(|value| JsonValue::String(value.clone()))
        .unwrap_or(JsonValue::Null)
}

fn checksums(files: &[ShippedFile], manifest: &[u8]) -> Result<Vec<u8>> {
    let mut checksums = BTreeMap::new();
    for file in files {
        if file.path.bytes().any(|byte| byte.is_ascii_whitespace()) {
            return Err(PackagerError::new(
                PackagerErrorCode::UnsafePath,
                format!(
                    "checksum manifest cannot represent whitespace path {:?}",
                    file.path
                ),
            ));
        }
        if checksums
            .insert(file.path.as_str(), sha256_hex(&file.bytes)?)
            .is_some()
        {
            return Err(PackagerError::new(
                PackagerErrorCode::DuplicatePath,
                format!("duplicate checksum path {:?}", file.path),
            ));
        }
    }
    checksums.insert(MANIFEST_PATH, sha256_hex(manifest)?);
    let mut output = Vec::new();
    for (path, digest) in checksums {
        output.extend_from_slice(digest.as_bytes());
        output.extend_from_slice(b"  ");
        output.extend_from_slice(path.as_bytes());
        output.push(b'\n');
    }
    Ok(output)
}

fn generated_file(path: &str, bytes: Vec<u8>, input_ids: &[String]) -> Result<ShippedFile> {
    let length = byte_len(bytes.len(), "generated release metadata")?;
    if length == 0 {
        return Err(PackagerError::new(
            PackagerErrorCode::CoverageGap,
            "generated release metadata is unexpectedly empty",
        ));
    }
    Ok(ShippedFile {
        path: path.to_owned(),
        mode: 0o644,
        bytes,
        claims: vec![ByteClaim {
            output_offset: 0,
            length,
            origin: ByteOrigin::Derived {
                input_ids: input_ids.to_vec(),
                transformation: GENERATED_TRANSFORMATION.to_owned(),
            },
        }],
    })
}

fn validate_reserved_paths(files: &[ShippedFile]) -> Result<()> {
    for file in files {
        if matches!(file.path.as_str(), MANIFEST_PATH | CHECKSUMS_PATH) {
            return Err(PackagerError::new(
                PackagerErrorCode::DuplicatePath,
                format!(
                    "payload path {:?} is reserved for release metadata",
                    file.path
                ),
            ));
        }
    }
    Ok(())
}

fn byte_len(length: usize, context: &str) -> Result<u64> {
    u64::try_from(length).map_err(|_| {
        PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("{context} length does not fit u64"),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::{ReleaseMetadataRequest, emit_release_metadata};
    use crate::{
        Architecture, ByteClaim, ByteOrigin, DeclaredInput, EmissionMode, EmissionPolicy,
        ReleaseAuthorization, ShippedFile,
    };

    #[test]
    fn fixture_tecnica_metadata_covers_generated_shipped_bytes() {
        let input = DeclaredInput::from_bytes(
            "FIXTURE_TECNICA/input",
            b"payload",
            "Apache-2.0",
            "FIXTURE_TECNICA",
        )
        .expect("input");
        let request = ReleaseMetadataRequest {
            release_id: "FIXTURE_TECNICA/release".to_owned(),
            release_version: "1.0.0".to_owned(),
            inputs: vec![input],
            files: vec![ShippedFile {
                path: "artifact.bin".to_owned(),
                mode: 0o644,
                bytes: b"payload".to_vec(),
                claims: vec![ByteClaim {
                    output_offset: 0,
                    length: 7,
                    origin: ByteOrigin::Copied {
                        input_id: "FIXTURE_TECNICA/input".to_owned(),
                        input_offset: 0,
                    },
                }],
            }],
        };
        let metadata = emit_release_metadata(&request, &policy()).expect("metadata");
        assert_eq!(metadata.reconciliation().file_count(), 3);
        assert!(
            metadata
                .checksums_bytes()
                .windows(b"release-manifest.json".len())
                .any(|window| window == b"release-manifest.json")
        );
        assert!(
            metadata
                .manifest_bytes()
                .windows(b"\"emissionMode\":\"dry-run\"".len())
                .any(|window| window == b"\"emissionMode\":\"dry-run\"")
        );
    }

    fn policy() -> EmissionPolicy {
        EmissionPolicy::new(
            EmissionMode::DryRun,
            vec![Architecture::Amd64, Architecture::Arm64],
            ReleaseAuthorization::default(),
        )
    }
}
