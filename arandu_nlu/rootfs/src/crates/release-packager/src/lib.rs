#![forbid(unsafe_code)]

mod authorization;
mod error;
mod hash;
mod json;
mod manifest;
mod oci;
mod path;
mod reconcile;
mod spdx;
mod ustar;

pub use authorization::{
    Architecture, ArchitectureEnablement, ArchitectureStage, EmissionMode, EmissionPolicy,
    ReleaseAuthorization,
};
pub use error::{PackagerError, PackagerErrorCode, Result};
pub use hash::{sha1_hex, sha256_hex};
pub use manifest::{ReleaseMetadata, ReleaseMetadataRequest, emit_release_metadata};
pub use oci::{
    ArchitectureBinding, OciImageLayout, OciImageRequest, OciRootEntry, emit_oci_image_layout,
};
pub use reconcile::{
    ByteClaim, ByteOrigin, DeclaredInput, ReconciliationReport, ShippedFile,
    reconcile_release_bytes,
};
pub use spdx::{EmittedSpdx, SpdxDocumentInput, SpdxFile, SpdxPackageInput, emit_spdx_23_json};
pub use ustar::{EmittedArchive, TarEntry, TarEntryKind, TarMetadata, emit_posix_ustar};
