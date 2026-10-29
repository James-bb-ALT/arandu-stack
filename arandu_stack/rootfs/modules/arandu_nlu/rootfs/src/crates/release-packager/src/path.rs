use crate::{PackagerError, PackagerErrorCode, Result};

pub(crate) const MAX_RELEASE_PATH_BYTES: usize = 255;

pub(crate) fn validate_release_path(path: &str) -> Result<()> {
    if path.is_empty() {
        return Err(unsafe_path(path, "path is empty"));
    }
    if path.len() > MAX_RELEASE_PATH_BYTES {
        return Err(PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("release path exceeds {MAX_RELEASE_PATH_BYTES} bytes"),
        ));
    }
    if path.starts_with('/') || path.ends_with('/') {
        return Err(unsafe_path(path, "leading or trailing slash"));
    }
    if path.contains('\\') {
        return Err(unsafe_path(path, "backslash is prohibited"));
    }
    if path.chars().any(char::is_control) {
        return Err(unsafe_path(path, "control character is prohibited"));
    }
    for component in path.split('/') {
        if component.is_empty() || component == "." || component == ".." {
            return Err(unsafe_path(path, "non-canonical path component"));
        }
    }
    Ok(())
}

pub(crate) fn validate_oci_root_path(path: &str) -> Result<()> {
    validate_release_path(path)?;
    if path
        .split('/')
        .any(|component| component == ".wh..wh..opq" || component.starts_with(".wh."))
    {
        return Err(unsafe_path(path, "OCI whiteout names are prohibited"));
    }
    Ok(())
}

pub(crate) fn validate_identifier(value: &str, context: &str) -> Result<()> {
    if value.is_empty() || value.len() > 256 {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidIdentifier,
            format!("{context} must contain 1 through 256 bytes"),
        ));
    }
    if value.chars().any(char::is_control) {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidIdentifier,
            format!("{context} contains a control character"),
        ));
    }
    Ok(())
}

pub(crate) fn validate_attestation_id(value: &str, context: &str) -> Result<()> {
    validate_identifier(value, context)?;
    if !value.bytes().all(|byte| {
        byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':' | b'/')
    }) {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidIdentifier,
            format!("{context} contains a non-canonical byte"),
        ));
    }
    Ok(())
}

fn unsafe_path(path: &str, reason: &str) -> PackagerError {
    PackagerError::new(
        PackagerErrorCode::UnsafePath,
        format!("unsafe release path {path:?}: {reason}"),
    )
}
