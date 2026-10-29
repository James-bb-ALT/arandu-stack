use std::collections::{BTreeMap, BTreeSet};

use crate::authorization::{EmissionMode, EmissionPolicy};
use crate::hash::sha256_hex;
use crate::json::{JsonValue, canonical_json, object};
use crate::path::{validate_attestation_id, validate_identifier, validate_release_path};
use crate::{PackagerError, PackagerErrorCode, Result};

const ALLOWED_SHIPPED_MODES: [u32; 4] = [0o444, 0o555, 0o644, 0o755];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DeclaredInput {
    pub id: String,
    pub expected_sha256: String,
    pub bytes: Vec<u8>,
    pub license: String,
    pub purpose: String,
}

impl DeclaredInput {
    pub fn from_bytes(
        id: impl Into<String>,
        bytes: impl Into<Vec<u8>>,
        license: impl Into<String>,
        purpose: impl Into<String>,
    ) -> Result<Self> {
        let bytes = bytes.into();
        Ok(Self {
            id: id.into(),
            expected_sha256: sha256_hex(&bytes)?,
            bytes,
            license: license.into(),
            purpose: purpose.into(),
        })
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ByteOrigin {
    Copied {
        input_id: String,
        input_offset: u64,
    },
    Derived {
        input_ids: Vec<String>,
        transformation: String,
    },
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ByteClaim {
    pub output_offset: u64,
    pub length: u64,
    pub origin: ByteOrigin,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ShippedFile {
    pub path: String,
    pub mode: u32,
    pub bytes: Vec<u8>,
    pub claims: Vec<ByteClaim>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReconciliationReport {
    bytes: Vec<u8>,
    sha256: String,
    input_count: usize,
    file_count: usize,
    mode: EmissionMode,
    pub(crate) inputs: Vec<ReconciledInput>,
    pub(crate) files: Vec<ReconciledFile>,
}

impl ReconciliationReport {
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }

    pub const fn input_count(&self) -> usize {
        self.input_count
    }

    pub const fn file_count(&self) -> usize {
        self.file_count
    }

    pub const fn mode(&self) -> EmissionMode {
        self.mode
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ReconciledInput {
    pub id: String,
    pub sha256: String,
    pub size: u64,
    pub license: String,
    pub purpose: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ReconciledFile {
    pub path: String,
    pub mode: u32,
    pub sha256: String,
    pub size: u64,
    pub claims: Vec<ByteClaim>,
}

pub fn reconcile_release_bytes(
    inputs: &[DeclaredInput],
    files: &[ShippedFile],
    policy: &EmissionPolicy,
) -> Result<ReconciliationReport> {
    policy.validate()?;
    reconcile(inputs, files, policy.mode())
}

pub(crate) fn reconcile(
    inputs: &[DeclaredInput],
    files: &[ShippedFile],
    mode: EmissionMode,
) -> Result<ReconciliationReport> {
    if files.is_empty() {
        return Err(PackagerError::new(
            PackagerErrorCode::CoverageGap,
            "release reconciliation requires at least one shipped file",
        ));
    }
    let (declared, reconciled_inputs) = validate_inputs(inputs)?;
    let mut used_inputs = BTreeSet::new();
    let reconciled_files = validate_files(files, &declared, &mut used_inputs)?;

    for input in declared.keys() {
        if !used_inputs.contains(*input) {
            return Err(PackagerError::new(
                PackagerErrorCode::UnusedInput,
                format!("declared input {input:?} is unused"),
            ));
        }
    }

    let value = object([
        (
            "files",
            JsonValue::Array(reconciled_files.iter().map(reconciled_file_json).collect()),
        ),
        (
            "inputs",
            JsonValue::Array(
                reconciled_inputs
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
                    .collect(),
            ),
        ),
        ("schemaVersion", JsonValue::Number(1)),
    ]);
    let mut bytes = canonical_json(&value);
    bytes.push(b'\n');
    Ok(ReconciliationReport {
        sha256: sha256_hex(&bytes)?,
        input_count: reconciled_inputs.len(),
        file_count: reconciled_files.len(),
        bytes,
        mode,
        inputs: reconciled_inputs,
        files: reconciled_files,
    })
}

fn validate_inputs(
    inputs: &[DeclaredInput],
) -> Result<(BTreeMap<&str, &DeclaredInput>, Vec<ReconciledInput>)> {
    let mut declared = BTreeMap::new();
    for input in inputs {
        validate_attestation_id(&input.id, "declared input ID")?;
        validate_identifier(&input.license, "declared input license")?;
        validate_identifier(&input.purpose, "declared input purpose")?;
        validate_sha256(&input.expected_sha256, "declared input digest")?;
        let actual = sha256_hex(&input.bytes)?;
        if actual != input.expected_sha256 {
            return Err(PackagerError::new(
                PackagerErrorCode::InputDigestMismatch,
                format!(
                    "declared input {:?} digest does not match its bytes",
                    input.id
                ),
            ));
        }
        if declared.insert(input.id.as_str(), input).is_some() {
            return Err(PackagerError::new(
                PackagerErrorCode::DuplicateInput,
                format!("duplicate declared input {:?}", input.id),
            ));
        }
    }
    let reconciled = declared
        .values()
        .map(|input| {
            Ok(ReconciledInput {
                id: input.id.clone(),
                sha256: input.expected_sha256.clone(),
                size: byte_len(input.bytes.len(), "declared input")?,
                license: input.license.clone(),
                purpose: input.purpose.clone(),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok((declared, reconciled))
}

fn validate_files(
    files: &[ShippedFile],
    declared: &BTreeMap<&str, &DeclaredInput>,
    used_inputs: &mut BTreeSet<String>,
) -> Result<Vec<ReconciledFile>> {
    let mut by_path = BTreeMap::new();
    for file in files {
        validate_release_path(&file.path)?;
        if !ALLOWED_SHIPPED_MODES.contains(&file.mode) {
            return Err(PackagerError::new(
                PackagerErrorCode::InvalidMode,
                format!("shipped file {:?} has mode {:04o}", file.path, file.mode),
            ));
        }
        if by_path.insert(file.path.as_str(), file).is_some() {
            return Err(PackagerError::new(
                PackagerErrorCode::DuplicatePath,
                format!("duplicate shipped path {:?}", file.path),
            ));
        }
    }

    by_path
        .into_values()
        .map(|file| {
            let mut claims = file.claims.clone();
            claims.sort();
            validate_claims(file, &claims, declared, used_inputs)?;
            Ok(ReconciledFile {
                path: file.path.clone(),
                mode: file.mode,
                sha256: sha256_hex(&file.bytes)?,
                size: byte_len(file.bytes.len(), "shipped file")?,
                claims,
            })
        })
        .collect()
}

fn validate_claims(
    file: &ShippedFile,
    claims: &[ByteClaim],
    declared: &BTreeMap<&str, &DeclaredInput>,
    used_inputs: &mut BTreeSet<String>,
) -> Result<()> {
    if file.bytes.is_empty() {
        if claims.len() != 1
            || claims[0].output_offset != 0
            || claims[0].length != 0
            || !matches!(claims[0].origin, ByteOrigin::Derived { .. })
        {
            return Err(PackagerError::new(
                PackagerErrorCode::CoverageGap,
                format!(
                    "empty shipped file {:?} requires one zero-length derived claim",
                    file.path
                ),
            ));
        }
        validate_origin(file, &claims[0], declared, used_inputs)?;
        return Ok(());
    }

    let file_len = byte_len(file.bytes.len(), "shipped file")?;
    let mut expected_offset = 0_u64;
    for claim in claims {
        if claim.length == 0 {
            return Err(PackagerError::new(
                PackagerErrorCode::InvalidByteRange,
                format!(
                    "non-empty shipped file {:?} has a zero-length claim",
                    file.path
                ),
            ));
        }
        if claim.output_offset > expected_offset {
            return Err(PackagerError::new(
                PackagerErrorCode::CoverageGap,
                format!(
                    "shipped file {:?} has an unrepresented range at byte {expected_offset}",
                    file.path
                ),
            ));
        }
        if claim.output_offset < expected_offset {
            return Err(PackagerError::new(
                PackagerErrorCode::CoverageOverlap,
                format!(
                    "shipped file {:?} has overlapping claims at byte {}",
                    file.path, claim.output_offset
                ),
            ));
        }
        let end = claim
            .output_offset
            .checked_add(claim.length)
            .ok_or_else(|| invalid_range(file, "output range overflow"))?;
        if end > file_len {
            return Err(invalid_range(file, "output range exceeds file bytes"));
        }
        validate_origin(file, claim, declared, used_inputs)?;
        expected_offset = end;
    }
    if expected_offset != file_len {
        return Err(PackagerError::new(
            PackagerErrorCode::CoverageGap,
            format!(
                "shipped file {:?} has unrepresented trailing bytes",
                file.path
            ),
        ));
    }
    Ok(())
}

fn validate_origin(
    file: &ShippedFile,
    claim: &ByteClaim,
    declared: &BTreeMap<&str, &DeclaredInput>,
    used_inputs: &mut BTreeSet<String>,
) -> Result<()> {
    match &claim.origin {
        ByteOrigin::Copied {
            input_id,
            input_offset,
        } => {
            let input = declared.get(input_id.as_str()).ok_or_else(|| {
                PackagerError::new(
                    PackagerErrorCode::UndeclaredInput,
                    format!("copied claim names undeclared input {input_id:?}"),
                )
            })?;
            let input_end = input_offset
                .checked_add(claim.length)
                .ok_or_else(|| invalid_range(file, "input range overflow"))?;
            let input_len = byte_len(input.bytes.len(), "declared input")?;
            if input_end > input_len {
                return Err(invalid_range(file, "input range exceeds declared bytes"));
            }
            let output = checked_slice(
                &file.bytes,
                claim.output_offset,
                claim.length,
                "shipped output",
            )?;
            let source =
                checked_slice(&input.bytes, *input_offset, claim.length, "declared input")?;
            if output != source {
                return Err(PackagerError::new(
                    PackagerErrorCode::CopiedByteMismatch,
                    format!(
                        "copied bytes in {:?} do not match declared input {input_id:?}",
                        file.path
                    ),
                ));
            }
            used_inputs.insert(input_id.clone());
        }
        ByteOrigin::Derived {
            input_ids,
            transformation,
        } => {
            validate_attestation_id(transformation, "byte derivation ID")?;
            if input_ids.is_empty() {
                return Err(PackagerError::new(
                    PackagerErrorCode::UndeclaredInput,
                    format!("derived claim in {:?} has no declared inputs", file.path),
                ));
            }
            let unique: BTreeSet<_> = input_ids.iter().collect();
            if unique.len() != input_ids.len() {
                return Err(PackagerError::new(
                    PackagerErrorCode::DuplicateInput,
                    format!("derived claim in {:?} repeats an input", file.path),
                ));
            }
            for input_id in unique {
                if !declared.contains_key(input_id.as_str()) {
                    return Err(PackagerError::new(
                        PackagerErrorCode::UndeclaredInput,
                        format!("derived claim names undeclared input {input_id:?}"),
                    ));
                }
                used_inputs.insert(input_id.clone());
            }
        }
    }
    Ok(())
}

fn reconciled_file_json(file: &ReconciledFile) -> JsonValue {
    object([
        (
            "claims",
            JsonValue::Array(file.claims.iter().map(claim_json).collect()),
        ),
        ("mode", JsonValue::String(format!("{:04o}", file.mode))),
        ("path", JsonValue::String(file.path.clone())),
        ("sha256", JsonValue::String(file.sha256.clone())),
        ("size", JsonValue::Number(file.size)),
    ])
}

fn claim_json(claim: &ByteClaim) -> JsonValue {
    let origin = match &claim.origin {
        ByteOrigin::Copied {
            input_id,
            input_offset,
        } => object([
            ("inputId", JsonValue::String(input_id.clone())),
            ("inputOffset", JsonValue::Number(*input_offset)),
            ("kind", JsonValue::String("copied".to_owned())),
        ]),
        ByteOrigin::Derived {
            input_ids,
            transformation,
        } => {
            let mut input_ids = input_ids.clone();
            input_ids.sort();
            object([
                (
                    "inputIds",
                    JsonValue::Array(input_ids.into_iter().map(JsonValue::String).collect()),
                ),
                ("kind", JsonValue::String("derived".to_owned())),
                ("transformation", JsonValue::String(transformation.clone())),
            ])
        }
    };
    object([
        ("length", JsonValue::Number(claim.length)),
        ("origin", origin),
        ("outputOffset", JsonValue::Number(claim.output_offset)),
    ])
}

fn checked_slice<'a>(bytes: &'a [u8], offset: u64, length: u64, context: &str) -> Result<&'a [u8]> {
    let end = offset.checked_add(length).ok_or_else(|| {
        PackagerError::new(
            PackagerErrorCode::InvalidByteRange,
            format!("{context} range overflow"),
        )
    })?;
    let start = usize::try_from(offset).map_err(|_| {
        PackagerError::new(
            PackagerErrorCode::InvalidByteRange,
            format!("{context} offset does not fit usize"),
        )
    })?;
    let end = usize::try_from(end).map_err(|_| {
        PackagerError::new(
            PackagerErrorCode::InvalidByteRange,
            format!("{context} end does not fit usize"),
        )
    })?;
    bytes.get(start..end).ok_or_else(|| {
        PackagerError::new(
            PackagerErrorCode::InvalidByteRange,
            format!("{context} range exceeds bytes"),
        )
    })
}

fn validate_sha256(value: &str, context: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(PackagerError::new(
            PackagerErrorCode::InputDigestMismatch,
            format!("{context} is not canonical lowercase SHA-256"),
        ));
    }
    Ok(())
}

fn invalid_range(file: &ShippedFile, reason: &str) -> PackagerError {
    PackagerError::new(
        PackagerErrorCode::InvalidByteRange,
        format!("invalid claim for {:?}: {reason}", file.path),
    )
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
    use super::{ByteClaim, ByteOrigin, DeclaredInput, ShippedFile, reconcile_release_bytes};
    use crate::{
        Architecture, EmissionMode, EmissionPolicy, PackagerErrorCode, ReleaseAuthorization,
    };

    #[test]
    fn fixture_tecnica_reconciliation_is_bidirectional() {
        let input = DeclaredInput::from_bytes(
            "FIXTURE_TECNICA/input",
            b"abcd",
            "Apache-2.0",
            "FIXTURE_TECNICA",
        )
        .expect("input");
        let file = copied_file(b"abcd");
        let report = reconcile_release_bytes(&[input], &[file], &policy()).expect("report");
        assert_eq!(report.input_count(), 1);
        assert_eq!(report.file_count(), 1);
        assert!(report.bytes().ends_with(b"\n"));
    }

    #[test]
    fn fixture_tecnica_rejects_gap_and_unused_input() {
        let first = DeclaredInput::from_bytes(
            "FIXTURE_TECNICA/input",
            b"abcd",
            "Apache-2.0",
            "FIXTURE_TECNICA",
        )
        .expect("first");
        let unused = DeclaredInput::from_bytes(
            "FIXTURE_TECNICA/unused",
            b"x",
            "Apache-2.0",
            "FIXTURE_TECNICA",
        )
        .expect("unused");
        let mut file = copied_file(b"abcd");
        file.claims[0].length = 3;
        assert_eq!(
            reconcile_release_bytes(std::slice::from_ref(&first), &[file], &policy())
                .expect_err("gap")
                .code(),
            PackagerErrorCode::CoverageGap
        );
        assert_eq!(
            reconcile_release_bytes(&[first, unused], &[copied_file(b"abcd")], &policy())
                .expect_err("unused")
                .code(),
            PackagerErrorCode::UnusedInput
        );
    }

    fn copied_file(bytes: &[u8]) -> ShippedFile {
        ShippedFile {
            path: "FIXTURE_TECNICA.bin".to_owned(),
            mode: 0o644,
            bytes: bytes.to_vec(),
            claims: vec![ByteClaim {
                output_offset: 0,
                length: u64::try_from(bytes.len()).expect("FIXTURE_TECNICA length"),
                origin: ByteOrigin::Copied {
                    input_id: "FIXTURE_TECNICA/input".to_owned(),
                    input_offset: 0,
                },
            }],
        }
    }

    fn policy() -> EmissionPolicy {
        EmissionPolicy::new(
            EmissionMode::DryRun,
            vec![Architecture::Amd64],
            ReleaseAuthorization::default(),
        )
    }
}
