use std::collections::{BTreeMap, BTreeSet};

use crate::authorization::{EmissionMode, EmissionPolicy};
use crate::hash::sha256_hex;
use crate::path::validate_release_path;
use crate::{PackagerError, PackagerErrorCode, Result};

const TAR_BLOCK_BYTES: usize = 512;
const TAR_END_BYTES: usize = TAR_BLOCK_BYTES * 2;
const MAX_TAR_NUMBER: u64 = 0o77_777_777_777;
const ALLOWED_FILE_MODES: [u32; 4] = [0o444, 0o555, 0o644, 0o755];
const ALLOWED_DIRECTORY_MODES: [u32; 2] = [0o555, 0o755];

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TarEntryKind {
    RegularFile,
    Directory,
    SymbolicLink { target: String },
    HardLink { target: String },
    CharacterDevice { major: u32, minor: u32 },
    BlockDevice { major: u32, minor: u32 },
    Fifo,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TarMetadata {
    pub mode: u32,
    pub uid: u32,
    pub gid: u32,
    pub mtime: u64,
    pub user_name: String,
    pub group_name: String,
}

impl TarMetadata {
    pub fn canonical(mode: u32) -> Self {
        Self {
            mode,
            uid: 0,
            gid: 0,
            mtime: 0,
            user_name: String::new(),
            group_name: String::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TarEntry {
    pub path: String,
    pub kind: TarEntryKind,
    pub metadata: TarMetadata,
    pub bytes: Vec<u8>,
}

impl TarEntry {
    pub fn file(path: impl Into<String>, mode: u32, bytes: impl Into<Vec<u8>>) -> Self {
        Self {
            path: path.into(),
            kind: TarEntryKind::RegularFile,
            metadata: TarMetadata::canonical(mode),
            bytes: bytes.into(),
        }
    }

    pub fn directory(path: impl Into<String>, mode: u32) -> Self {
        Self {
            path: path.into(),
            kind: TarEntryKind::Directory,
            metadata: TarMetadata::canonical(mode),
            bytes: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EmittedArchive {
    bytes: Vec<u8>,
    sha256: String,
    mode: EmissionMode,
}

impl EmittedArchive {
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }

    pub const fn mode(&self) -> EmissionMode {
        self.mode
    }
}

pub fn emit_posix_ustar(entries: &[TarEntry], policy: &EmissionPolicy) -> Result<EmittedArchive> {
    policy.validate()?;
    let bytes = build_posix_ustar(entries)?;
    Ok(EmittedArchive {
        sha256: sha256_hex(&bytes)?,
        bytes,
        mode: policy.mode(),
    })
}

pub(crate) fn build_posix_ustar(entries: &[TarEntry]) -> Result<Vec<u8>> {
    let entries = validate_and_sort(entries)?;
    let mut output = Vec::new();
    for entry in entries {
        let header = header(entry)?;
        output.extend_from_slice(&header);
        if matches!(entry.kind, TarEntryKind::RegularFile) {
            output.extend_from_slice(&entry.bytes);
            let remainder = entry.bytes.len() % TAR_BLOCK_BYTES;
            if remainder != 0 {
                output.resize(output.len() + TAR_BLOCK_BYTES - remainder, 0);
            }
        }
    }
    output.resize(output.len() + TAR_END_BYTES, 0);
    Ok(output)
}

fn validate_and_sort(entries: &[TarEntry]) -> Result<Vec<&TarEntry>> {
    let mut by_path = BTreeMap::new();
    let mut regular_files = BTreeSet::new();
    for entry in entries {
        validate_release_path(&entry.path)?;
        validate_kind_and_metadata(entry)?;
        if by_path.insert(entry.path.as_str(), entry).is_some() {
            return Err(PackagerError::new(
                PackagerErrorCode::DuplicatePath,
                format!("duplicate archive path {:?}", entry.path),
            ));
        }
        if matches!(entry.kind, TarEntryKind::RegularFile) {
            regular_files.insert(entry.path.as_str());
        }
    }
    for path in by_path.keys() {
        for (index, byte) in path.bytes().enumerate() {
            if byte == b'/' && regular_files.contains(&path[..index]) {
                return Err(PackagerError::new(
                    PackagerErrorCode::PathConflict,
                    format!(
                        "regular file {:?} is an ancestor of {path:?}",
                        &path[..index]
                    ),
                ));
            }
        }
    }
    Ok(by_path.into_values().collect())
}

fn validate_kind_and_metadata(entry: &TarEntry) -> Result<()> {
    match entry.kind {
        TarEntryKind::RegularFile => {
            if !ALLOWED_FILE_MODES.contains(&entry.metadata.mode) {
                return Err(invalid_mode(entry));
            }
        }
        TarEntryKind::Directory => {
            if !entry.bytes.is_empty() {
                return Err(PackagerError::new(
                    PackagerErrorCode::UnsupportedEntryKind,
                    format!("directory {:?} contains payload bytes", entry.path),
                ));
            }
            if !ALLOWED_DIRECTORY_MODES.contains(&entry.metadata.mode) {
                return Err(invalid_mode(entry));
            }
        }
        TarEntryKind::SymbolicLink { .. }
        | TarEntryKind::HardLink { .. }
        | TarEntryKind::CharacterDevice { .. }
        | TarEntryKind::BlockDevice { .. }
        | TarEntryKind::Fifo => {
            return Err(PackagerError::new(
                PackagerErrorCode::UnsupportedEntryKind,
                format!("links, devices, and FIFOs are prohibited: {:?}", entry.path),
            ));
        }
    }
    if entry.metadata.uid != 0
        || entry.metadata.gid != 0
        || entry.metadata.mtime != 0
        || !entry.metadata.user_name.is_empty()
        || !entry.metadata.group_name.is_empty()
    {
        return Err(PackagerError::new(
            PackagerErrorCode::NondeterministicMetadata,
            format!(
                "archive metadata for {:?} must use zero ownership/time and empty names",
                entry.path
            ),
        ));
    }
    Ok(())
}

fn invalid_mode(entry: &TarEntry) -> PackagerError {
    PackagerError::new(
        PackagerErrorCode::InvalidMode,
        format!(
            "mode {:04o} is not allowed for {:?}",
            entry.metadata.mode, entry.path
        ),
    )
}

fn header(entry: &TarEntry) -> Result<[u8; TAR_BLOCK_BYTES]> {
    let archive_path = if matches!(entry.kind, TarEntryKind::Directory) {
        format!("{}/", entry.path)
    } else {
        entry.path.clone()
    };
    let (prefix, name) = split_ustar_path(&archive_path)?;
    let mut header = [0_u8; TAR_BLOCK_BYTES];
    write_text(&mut header[0..100], name, "ustar name")?;
    write_octal(
        &mut header[100..108],
        u64::from(entry.metadata.mode),
        "ustar mode",
    )?;
    write_octal(&mut header[108..116], 0, "ustar uid")?;
    write_octal(&mut header[116..124], 0, "ustar gid")?;
    let size = if matches!(entry.kind, TarEntryKind::RegularFile) {
        u64::try_from(entry.bytes.len()).map_err(|_| {
            PackagerError::new(
                PackagerErrorCode::ResourceLimit,
                format!("archive entry {:?} size does not fit u64", entry.path),
            )
        })?
    } else {
        0
    };
    if size > MAX_TAR_NUMBER {
        return Err(PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("archive entry {:?} exceeds ustar size limit", entry.path),
        ));
    }
    write_octal(&mut header[124..136], size, "ustar size")?;
    write_octal(&mut header[136..148], 0, "ustar mtime")?;
    header[148..156].fill(b' ');
    header[156] = if matches!(entry.kind, TarEntryKind::Directory) {
        b'5'
    } else {
        b'0'
    };
    header[257..263].copy_from_slice(b"ustar\0");
    header[263..265].copy_from_slice(b"00");
    write_octal(&mut header[329..337], 0, "ustar device major")?;
    write_octal(&mut header[337..345], 0, "ustar device minor")?;
    write_text(&mut header[345..500], prefix, "ustar prefix")?;
    let checksum: u64 = header.iter().map(|byte| u64::from(*byte)).sum();
    if checksum > 0o777_777 {
        return Err(PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            "ustar header checksum overflow",
        ));
    }
    let checksum = format!("{checksum:06o}\0 ");
    header[148..156].copy_from_slice(checksum.as_bytes());
    Ok(header)
}

fn split_ustar_path(path: &str) -> Result<(&str, &str)> {
    if path.len() <= 100 {
        return Ok(("", path));
    }
    for (index, byte) in path.bytes().enumerate().rev() {
        if byte == b'/' && index != path.len() - 1 && index <= 155 && path.len() - index - 1 <= 100
        {
            return Ok((&path[..index], &path[index + 1..]));
        }
    }
    Err(PackagerError::new(
        PackagerErrorCode::ResourceLimit,
        format!("path cannot be represented by POSIX ustar: {path:?}"),
    ))
}

fn write_text(field: &mut [u8], value: &str, context: &str) -> Result<()> {
    if value.len() > field.len() {
        return Err(PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("{context} exceeds {} bytes", field.len()),
        ));
    }
    field[..value.len()].copy_from_slice(value.as_bytes());
    Ok(())
}

fn write_octal(field: &mut [u8], value: u64, context: &str) -> Result<()> {
    let digits = field
        .len()
        .checked_sub(1)
        .ok_or_else(|| PackagerError::new(PackagerErrorCode::ResourceLimit, "empty octal field"))?;
    let encoded = format!("{value:0digits$o}");
    if encoded.len() > digits {
        return Err(PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("{context} does not fit its ustar field"),
        ));
    }
    field[..digits].copy_from_slice(encoded.as_bytes());
    field[digits] = 0;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{TarEntry, TarEntryKind, TarMetadata, build_posix_ustar};
    use crate::PackagerErrorCode;

    #[test]
    fn fixture_tecnica_preserves_allowed_mode_and_canonical_header() {
        let bytes = build_posix_ustar(&[TarEntry::file("bin/FIXTURE_TECNICA", 0o755, b"x")])
            .expect("FIXTURE_TECNICA archive");
        assert_eq!(&bytes[100..108], b"0000755\0");
        assert_eq!(&bytes[108..116], b"0000000\0");
        assert_eq!(&bytes[136..148], b"00000000000\0");
        assert_eq!(&bytes[257..265], b"ustar\x0000");
        assert_eq!(bytes.len(), 2_048);
    }

    #[test]
    fn fixture_tecnica_rejects_link_and_nondeterministic_metadata() {
        let link = TarEntry {
            path: "FIXTURE_TECNICA-link".to_owned(),
            kind: TarEntryKind::SymbolicLink {
                target: "FIXTURE_TECNICA-target".to_owned(),
            },
            metadata: TarMetadata::canonical(0o755),
            bytes: Vec::new(),
        };
        assert_eq!(
            build_posix_ustar(&[link])
                .expect_err("link must fail")
                .code(),
            PackagerErrorCode::UnsupportedEntryKind
        );

        let mut file = TarEntry::file("FIXTURE_TECNICA", 0o644, b"x");
        file.metadata.mtime = 1;
        assert_eq!(
            build_posix_ustar(&[file])
                .expect_err("mtime must fail")
                .code(),
            PackagerErrorCode::NondeterministicMetadata
        );
    }
}
