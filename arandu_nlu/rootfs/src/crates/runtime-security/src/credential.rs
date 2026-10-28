use std::{
    fmt,
    io::{self, Read},
    ptr,
    sync::atomic::{Ordering, compiler_fence},
};

use crate::SecurityError;

/// Exact byte length of the inherited pairing credential.
pub const CREDENTIAL_LENGTH: usize = 32;

/// A fixed-size pairing credential held only by a guarded process.
///
/// The value is intentionally non-cloneable. Its `Debug` implementation is
/// redacted, and its backing array is overwritten with volatile writes on
/// explicit destruction and drop.
#[must_use = "dropping a credential destroys its storage"]
pub struct Credential {
    bytes: [u8; CREDENTIAL_LENGTH],
    destroyed: bool,
}

impl Credential {
    pub(crate) fn from_bytes(bytes: [u8; CREDENTIAL_LENGTH]) -> Self {
        Self {
            bytes,
            destroyed: false,
        }
    }

    /// Borrows the secret bytes while the credential remains live.
    ///
    /// Callers must not log, format, clone, or retain the returned bytes.
    pub fn expose_secret(&self) -> Option<&[u8; CREDENTIAL_LENGTH]> {
        if self.destroyed {
            None
        } else {
            Some(&self.bytes)
        }
    }

    /// Best-effort explicitly destroys the in-process credential bytes.
    ///
    /// Volatile writes and a compiler fence prevent ordinary dead-store
    /// removal. Rust and the operating system cannot guarantee erasure of
    /// prior register, ABI, kernel-buffer, or compiler-created copies.
    pub fn destroy(&mut self) {
        wipe(&mut self.bytes);
        self.destroyed = true;
    }

    /// Reports whether explicit destruction has already occurred.
    pub fn is_destroyed(&self) -> bool {
        self.destroyed
    }
}

impl fmt::Debug for Credential {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Credential([REDACTED])")
    }
}

impl Drop for Credential {
    fn drop(&mut self) {
        self.destroy();
    }
}

pub(crate) fn read_from<R: Read>(source: &mut R) -> Result<Credential, SecurityError> {
    let mut credential = Credential {
        bytes: [0; CREDENTIAL_LENGTH],
        destroyed: false,
    };
    let mut offset = 0;

    while offset < CREDENTIAL_LENGTH {
        let destination = &mut credential.bytes[offset..];
        match source.read(destination) {
            Ok(0) => return Err(SecurityError::CredentialTooShort),
            Ok(count) if count <= destination.len() => offset += count,
            Ok(_) => return Err(SecurityError::CredentialRead),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => return Err(SecurityError::CredentialRead),
        }
    }

    let mut trailing = [0_u8; 1];
    loop {
        match source.read(&mut trailing) {
            Ok(0) => {
                wipe(&mut trailing);
                return Ok(credential);
            }
            Ok(_) => {
                wipe(&mut trailing);
                return Err(SecurityError::CredentialTrailingData);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => {
                wipe(&mut trailing);
                return Err(SecurityError::CredentialRead);
            }
        }
    }
}

pub(crate) fn wipe(bytes: &mut [u8]) {
    for byte in bytes {
        // SAFETY: `byte` is a valid, uniquely borrowed byte for this write.
        unsafe { ptr::write_volatile(byte, 0) };
    }
    compiler_fence(Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use std::{
        io::{Cursor, Error},
        vec::Vec,
    };

    use super::*;

    const FIXTURE_TECNICA_CREDENTIAL: [u8; CREDENTIAL_LENGTH] =
        *b"FIXTURE_TECNICA_0123456789ABCDEF";

    struct InterruptedOnce<R> {
        inner: R,
        interrupted: bool,
    }

    impl<R: Read> Read for InterruptedOnce<R> {
        fn read(&mut self, destination: &mut [u8]) -> io::Result<usize> {
            if self.interrupted {
                self.inner.read(destination)
            } else {
                self.interrupted = true;
                Err(Error::from(io::ErrorKind::Interrupted))
            }
        }
    }

    struct FailingReader;

    impl Read for FailingReader {
        fn read(&mut self, _destination: &mut [u8]) -> io::Result<usize> {
            Err(Error::from(io::ErrorKind::Other))
        }
    }

    struct InvalidCountReader;

    impl Read for InvalidCountReader {
        fn read(&mut self, destination: &mut [u8]) -> io::Result<usize> {
            Ok(destination.len().saturating_add(1))
        }
    }

    #[test]
    fn exact_input_is_accepted_and_debug_is_redacted() {
        let mut source = Cursor::new(FIXTURE_TECNICA_CREDENTIAL);
        let credential = read_from(&mut source).expect("FIXTURE_TECNICA exact credential");

        assert!(credential.expose_secret() == Some(&FIXTURE_TECNICA_CREDENTIAL));
        assert!(format!("{credential:?}") == "Credential([REDACTED])");
    }

    #[test]
    fn short_input_is_rejected() {
        let mut source = Cursor::new(&FIXTURE_TECNICA_CREDENTIAL[..CREDENTIAL_LENGTH - 1]);
        let result = read_from(&mut source);

        assert!(matches!(result, Err(SecurityError::CredentialTooShort)));
    }

    #[test]
    fn long_input_is_rejected() {
        let mut bytes = Vec::from(FIXTURE_TECNICA_CREDENTIAL);
        bytes.push(b'X');
        let mut source = Cursor::new(bytes);
        let result = read_from(&mut source);

        assert!(matches!(result, Err(SecurityError::CredentialTrailingData)));
    }

    #[test]
    fn interrupted_reads_are_retried() {
        let cursor = Cursor::new(FIXTURE_TECNICA_CREDENTIAL);
        let mut source = InterruptedOnce {
            inner: cursor,
            interrupted: false,
        };
        let credential = read_from(&mut source).expect("FIXTURE_TECNICA interrupted credential");

        assert!(credential.expose_secret() == Some(&FIXTURE_TECNICA_CREDENTIAL));
    }

    #[test]
    fn read_errors_are_closed() {
        let result = read_from(&mut FailingReader);

        assert!(matches!(result, Err(SecurityError::CredentialRead)));
    }

    #[test]
    fn invalid_reader_counts_are_closed() {
        let result = read_from(&mut InvalidCountReader);

        assert!(matches!(result, Err(SecurityError::CredentialRead)));
    }

    #[test]
    fn explicit_destroy_removes_access_and_overwrites_storage() {
        let mut source = Cursor::new(FIXTURE_TECNICA_CREDENTIAL);
        let mut credential = read_from(&mut source).expect("FIXTURE_TECNICA destroy credential");

        credential.destroy();

        assert!(credential.is_destroyed());
        assert!(credential.expose_secret().is_none());
        assert!(credential.bytes.iter().all(|byte| *byte == 0));
    }

    #[test]
    fn admitted_bytes_are_owned_and_redacted() {
        let credential = Credential::from_bytes(FIXTURE_TECNICA_CREDENTIAL);

        assert_eq!(
            credential.expose_secret(),
            Some(&FIXTURE_TECNICA_CREDENTIAL)
        );
        assert_eq!(format!("{credential:?}"), "Credential([REDACTED])");
    }
}
