use std::fmt;

use crate::{SecurityError, credential::wipe};

const SUPERVISOR_TOKEN: &str = "SUPERVISOR_TOKEN";
const MAX_SUPERVISOR_TOKEN_BYTES: usize = 16_384;

/// A process-environment secret whose owned storage is erased on drop.
#[must_use = "the environment secret must be consumed or explicitly destroyed"]
pub struct SecretEnvironmentValue {
    bytes: Vec<u8>,
    destroyed: bool,
}

impl SecretEnvironmentValue {
    /// Borrows the validated ASCII token while the value remains live.
    pub fn expose_secret(&self) -> Option<&str> {
        if self.destroyed {
            None
        } else {
            std::str::from_utf8(&self.bytes).ok()
        }
    }

    /// Overwrites the owned token storage.
    pub fn destroy(&mut self) {
        wipe(&mut self.bytes);
        self.bytes.clear();
        self.destroyed = true;
    }

    /// Reports whether explicit destruction has occurred.
    #[must_use]
    pub const fn is_destroyed(&self) -> bool {
        self.destroyed
    }
}

impl fmt::Debug for SecretEnvironmentValue {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretEnvironmentValue([REDACTED])")
    }
}

impl Drop for SecretEnvironmentValue {
    fn drop(&mut self) {
        self.destroy();
    }
}

/// Takes the Home Assistant Supervisor token and removes its environment entry.
///
/// This must run during single-threaded process startup. The returned value
/// accepts only printable ASCII and is erased on drop.
pub fn take_supervisor_token() -> Result<SecretEnvironmentValue, SecurityError> {
    let value =
        std::env::var(SUPERVISOR_TOKEN).map_err(|_| SecurityError::EnvironmentSecretRead)?;
    let bytes = value.into_bytes();
    if bytes.is_empty()
        || bytes.len() > MAX_SUPERVISOR_TOKEN_BYTES
        || !bytes.iter().all(|byte| (0x21..=0x7e).contains(byte))
    {
        let mut rejected = bytes;
        wipe(&mut rejected);
        return Err(SecurityError::EnvironmentSecretRead);
    }

    // SAFETY: product startup is still single-threaded and has not invoked
    // any API that can create a thread. No concurrent environment access is
    // therefore possible.
    unsafe {
        std::env::remove_var(SUPERVISOR_TOKEN);
    }
    if std::env::var_os(SUPERVISOR_TOKEN).is_some() {
        let mut rejected = bytes;
        wipe(&mut rejected);
        return Err(SecurityError::EnvironmentSecretRemoval);
    }
    Ok(SecretEnvironmentValue {
        bytes,
        destroyed: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_representation_and_explicit_destruction_are_closed() {
        let mut value = SecretEnvironmentValue {
            bytes: b"FIXTURE_TECNICA_TOKEN".to_vec(),
            destroyed: false,
        };

        assert_eq!(value.expose_secret(), Some("FIXTURE_TECNICA_TOKEN"));
        assert_eq!(format!("{value:?}"), "SecretEnvironmentValue([REDACTED])");
        value.destroy();
        assert!(value.is_destroyed());
        assert!(value.expose_secret().is_none());
        assert!(value.bytes.is_empty());
    }
}
