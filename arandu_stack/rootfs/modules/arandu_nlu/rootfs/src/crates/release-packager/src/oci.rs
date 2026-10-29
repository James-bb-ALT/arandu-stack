use std::collections::BTreeMap;

use crate::authorization::{Architecture, EmissionMode, EmissionPolicy};
use crate::hash::sha256_hex;
use crate::json::{JsonValue, canonical_json, object};
use crate::path::{validate_identifier, validate_oci_root_path};
use crate::ustar::{TarEntry, TarEntryKind, build_posix_ustar};
use crate::{PackagerError, PackagerErrorCode, Result};

const OCI_LAYOUT_MEDIA_TYPE: &str = "application/vnd.oci.image.manifest.v1+json";
const OCI_CONFIG_MEDIA_TYPE: &str = "application/vnd.oci.image.config.v1+json";
const OCI_LAYER_MEDIA_TYPE: &str = "application/vnd.oci.image.layer.v1.tar";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArchitectureBinding {
    Independent,
    Native(Architecture),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OciRootEntry {
    pub entry: TarEntry,
    pub architecture: ArchitectureBinding,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OciImageRequest {
    pub reference: String,
    pub architecture: Architecture,
    pub entries: Vec<OciRootEntry>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OciImageLayout {
    files: BTreeMap<String, Vec<u8>>,
    architecture: Architecture,
    manifest_digest: String,
    config_digest: String,
    layer_digest: String,
    mode: EmissionMode,
}

impl OciImageLayout {
    pub fn files(&self) -> &BTreeMap<String, Vec<u8>> {
        &self.files
    }

    pub fn file(&self, path: &str) -> Option<&[u8]> {
        self.files.get(path).map(Vec::as_slice)
    }

    pub const fn architecture(&self) -> Architecture {
        self.architecture
    }

    pub fn manifest_digest(&self) -> &str {
        &self.manifest_digest
    }

    pub fn config_digest(&self) -> &str {
        &self.config_digest
    }

    pub fn layer_digest(&self) -> &str {
        &self.layer_digest
    }

    pub const fn mode(&self) -> EmissionMode {
        self.mode
    }
}

pub fn emit_oci_image_layout(
    request: &OciImageRequest,
    policy: &EmissionPolicy,
) -> Result<OciImageLayout> {
    let targets = policy.validate()?;
    if targets.as_slice() != [request.architecture] {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidArchitecture,
            format!(
                "single-platform OCI request for {} requires exactly that policy target",
                request.architecture.as_oci_str()
            ),
        ));
    }
    validate_reference(&request.reference)?;
    validate_root_entries(request)?;

    let tar_entries: Vec<_> = request
        .entries
        .iter()
        .map(|entry| entry.entry.clone())
        .collect();
    let layer = build_posix_ustar(&tar_entries)?;
    let layer_hash = sha256_hex(&layer)?;
    let layer_digest = format!("sha256:{layer_hash}");

    let config = canonical_json(&object([
        (
            "architecture",
            JsonValue::String(request.architecture.as_oci_str().to_owned()),
        ),
        ("config", JsonValue::Object(BTreeMap::new())),
        ("os", JsonValue::String("linux".to_owned())),
        (
            "rootfs",
            object([
                (
                    "diff_ids",
                    JsonValue::Array(vec![JsonValue::String(layer_digest.clone())]),
                ),
                ("type", JsonValue::String("layers".to_owned())),
            ]),
        ),
    ]));
    let config_hash = sha256_hex(&config)?;
    let config_digest = format!("sha256:{config_hash}");

    let manifest = canonical_json(&object([
        (
            "config",
            descriptor(OCI_CONFIG_MEDIA_TYPE, &config_digest, config.len())?,
        ),
        (
            "layers",
            JsonValue::Array(vec![descriptor(
                OCI_LAYER_MEDIA_TYPE,
                &layer_digest,
                layer.len(),
            )?]),
        ),
        (
            "mediaType",
            JsonValue::String(OCI_LAYOUT_MEDIA_TYPE.to_owned()),
        ),
        ("schemaVersion", JsonValue::Number(2)),
    ]));
    let manifest_hash = sha256_hex(&manifest)?;
    let manifest_digest = format!("sha256:{manifest_hash}");

    let mut annotations = BTreeMap::new();
    annotations.insert(
        "org.opencontainers.image.ref.name".to_owned(),
        JsonValue::String(request.reference.clone()),
    );
    let index = canonical_json(&object([
        (
            "manifests",
            JsonValue::Array(vec![object([
                ("annotations", JsonValue::Object(annotations)),
                ("digest", JsonValue::String(manifest_digest.clone())),
                (
                    "mediaType",
                    JsonValue::String(OCI_LAYOUT_MEDIA_TYPE.to_owned()),
                ),
                (
                    "platform",
                    object([
                        (
                            "architecture",
                            JsonValue::String(request.architecture.as_oci_str().to_owned()),
                        ),
                        ("os", JsonValue::String("linux".to_owned())),
                    ]),
                ),
                (
                    "size",
                    JsonValue::Number(byte_len(manifest.len(), "OCI manifest")?),
                ),
            ])]),
        ),
        (
            "mediaType",
            JsonValue::String("application/vnd.oci.image.index.v1+json".to_owned()),
        ),
        ("schemaVersion", JsonValue::Number(2)),
    ]));
    let layout = canonical_json(&object([(
        "imageLayoutVersion",
        JsonValue::String("1.0.0".to_owned()),
    )]));

    let mut files = BTreeMap::new();
    insert_unique(&mut files, "oci-layout".to_owned(), layout)?;
    insert_unique(&mut files, "index.json".to_owned(), index)?;
    insert_unique(&mut files, format!("blobs/sha256/{layer_hash}"), layer)?;
    insert_unique(&mut files, format!("blobs/sha256/{config_hash}"), config)?;
    insert_unique(
        &mut files,
        format!("blobs/sha256/{manifest_hash}"),
        manifest,
    )?;

    Ok(OciImageLayout {
        files,
        architecture: request.architecture,
        manifest_digest,
        config_digest,
        layer_digest,
        mode: policy.mode(),
    })
}

fn validate_reference(reference: &str) -> Result<()> {
    validate_identifier(reference, "OCI reference")?;
    if !reference
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
    {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidIdentifier,
            "OCI reference contains a non-canonical byte",
        ));
    }
    Ok(())
}

fn validate_root_entries(request: &OciImageRequest) -> Result<()> {
    let mut native_entries = 0_usize;
    for root_entry in &request.entries {
        validate_oci_root_path(&root_entry.entry.path)?;
        match (&root_entry.entry.kind, root_entry.architecture) {
            (TarEntryKind::Directory, ArchitectureBinding::Independent) => {}
            (TarEntryKind::Directory, ArchitectureBinding::Native(_)) => {
                return Err(PackagerError::new(
                    PackagerErrorCode::InvalidArchitecture,
                    format!(
                        "directory {:?} cannot carry a native architecture",
                        root_entry.entry.path
                    ),
                ));
            }
            (TarEntryKind::RegularFile, ArchitectureBinding::Independent) => {
                if root_entry.entry.bytes.starts_with(b"\x7fELF") {
                    return Err(PackagerError::new(
                        PackagerErrorCode::InvalidArchitecture,
                        format!(
                            "ELF file {:?} must carry an inspected architecture binding",
                            root_entry.entry.path
                        ),
                    ));
                }
            }
            (TarEntryKind::RegularFile, ArchitectureBinding::Native(declared)) => {
                let inspected =
                    inspect_elf_architecture(&root_entry.entry.bytes, &root_entry.entry.path)?;
                if declared != inspected || declared != request.architecture {
                    return Err(PackagerError::new(
                        PackagerErrorCode::ArchitectureMismatch,
                        format!(
                            "ELF file {:?} is {}, declared {}, requested {}",
                            root_entry.entry.path,
                            inspected.as_oci_str(),
                            declared.as_oci_str(),
                            request.architecture.as_oci_str()
                        ),
                    ));
                }
                if root_entry.entry.metadata.mode != 0o555
                    && root_entry.entry.metadata.mode != 0o755
                {
                    return Err(PackagerError::new(
                        PackagerErrorCode::InvalidMode,
                        format!(
                            "native ELF file {:?} must be executable",
                            root_entry.entry.path
                        ),
                    ));
                }
                native_entries = native_entries.checked_add(1).ok_or_else(|| {
                    PackagerError::new(
                        PackagerErrorCode::ResourceLimit,
                        "native OCI entry count overflow",
                    )
                })?;
            }
            (
                TarEntryKind::SymbolicLink { .. }
                | TarEntryKind::HardLink { .. }
                | TarEntryKind::CharacterDevice { .. }
                | TarEntryKind::BlockDevice { .. }
                | TarEntryKind::Fifo,
                _,
            ) => {
                return Err(PackagerError::new(
                    PackagerErrorCode::UnsupportedEntryKind,
                    format!(
                        "OCI root entry {:?} is not a regular file or directory",
                        root_entry.entry.path
                    ),
                ));
            }
        }
    }
    if native_entries == 0 {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidArchitecture,
            "scratch OCI root has no architecture-bound native ELF",
        ));
    }
    Ok(())
}

fn inspect_elf_architecture(bytes: &[u8], path: &str) -> Result<Architecture> {
    if bytes.len() < 20 || !bytes.starts_with(b"\x7fELF") {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidElf,
            format!("native entry {path:?} is not an ELF file"),
        ));
    }
    if bytes[4] != 2 || bytes[5] != 1 || bytes[6] != 1 {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidElf,
            format!("native entry {path:?} is not ELF64 little-endian version 1"),
        ));
    }
    let machine = u16::from_le_bytes([bytes[18], bytes[19]]);
    for architecture in [Architecture::Amd64, Architecture::Arm64] {
        if machine == architecture.elf_machine() {
            return Ok(architecture);
        }
    }
    Err(PackagerError::new(
        PackagerErrorCode::InvalidArchitecture,
        format!("native entry {path:?} has unsupported ELF machine {machine}"),
    ))
}

fn descriptor(media_type: &str, digest: &str, size: usize) -> Result<JsonValue> {
    Ok(object([
        ("digest", JsonValue::String(digest.to_owned())),
        ("mediaType", JsonValue::String(media_type.to_owned())),
        ("size", JsonValue::Number(byte_len(size, "OCI blob")?)),
    ]))
}

fn byte_len(length: usize, context: &str) -> Result<u64> {
    u64::try_from(length).map_err(|_| {
        PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("{context} length does not fit u64"),
        )
    })
}

fn insert_unique(
    files: &mut BTreeMap<String, Vec<u8>>,
    path: String,
    bytes: Vec<u8>,
) -> Result<()> {
    if files.insert(path.clone(), bytes).is_some() {
        return Err(PackagerError::new(
            PackagerErrorCode::DuplicatePath,
            format!("duplicate OCI layout path {path:?}"),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{ArchitectureBinding, OciImageRequest, OciRootEntry, emit_oci_image_layout};
    use crate::{
        Architecture, EmissionMode, EmissionPolicy, PackagerErrorCode, ReleaseAuthorization,
        TarEntry,
    };

    #[test]
    fn fixture_tecnica_layout_is_single_platform_and_deterministic() {
        let request = request(Architecture::Amd64, Architecture::Amd64);
        let policy = dry_run(Architecture::Amd64);
        let first = emit_oci_image_layout(&request, &policy).expect("first layout");
        let second = emit_oci_image_layout(&request, &policy).expect("second layout");
        assert_eq!(first, second);
        assert_eq!(first.files().len(), 5);
        assert_eq!(first.architecture(), Architecture::Amd64);
        assert!(
            first
                .file("index.json")
                .expect("index")
                .windows(b"\"architecture\":\"amd64\"".len())
                .any(|window| window == b"\"architecture\":\"amd64\"")
        );
    }

    #[test]
    fn fixture_tecnica_relabeling_is_rejected() {
        let request = request(Architecture::Arm64, Architecture::Amd64);
        let error = emit_oci_image_layout(&request, &dry_run(Architecture::Arm64))
            .expect_err("relabel must fail");
        assert_eq!(error.code(), PackagerErrorCode::ArchitectureMismatch);
    }

    fn request(requested: Architecture, binary_architecture: Architecture) -> OciImageRequest {
        OciImageRequest {
            reference: "FIXTURE_TECNICA".to_owned(),
            architecture: requested,
            entries: vec![
                OciRootEntry {
                    entry: TarEntry::directory("bin", 0o755),
                    architecture: ArchitectureBinding::Independent,
                },
                OciRootEntry {
                    entry: TarEntry::file(
                        "bin/FIXTURE_TECNICA",
                        0o755,
                        fixture_elf(binary_architecture),
                    ),
                    architecture: ArchitectureBinding::Native(binary_architecture),
                },
            ],
        }
    }

    fn fixture_elf(architecture: Architecture) -> Vec<u8> {
        let mut bytes = vec![0_u8; 64];
        bytes[..4].copy_from_slice(b"\x7fELF");
        bytes[4] = 2;
        bytes[5] = 1;
        bytes[6] = 1;
        bytes[18..20].copy_from_slice(&architecture.elf_machine().to_le_bytes());
        bytes
    }

    fn dry_run(architecture: Architecture) -> EmissionPolicy {
        EmissionPolicy::new(
            EmissionMode::DryRun,
            vec![architecture],
            ReleaseAuthorization::default(),
        )
    }
}
