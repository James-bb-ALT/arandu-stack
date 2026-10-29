//! Process hardening and fixed-size credential receipt for dedicated secret
//! processes.
//!
//! The product path is intentionally narrow: construct [`GuardedProcess`] at
//! process startup, then use that value to receive one credential from an
//! inherited descriptor. No secret bytes are logged or formatted by this
//! crate.

#![warn(missing_docs)]

mod credential;
mod environment;
mod identity;
mod network;
mod platform;

use std::{fmt, marker::PhantomData, rc::Rc};

#[cfg(unix)]
use std::{
    fs::File,
    os::fd::{AsFd, OwnedFd},
};

pub use credential::{CREDENTIAL_LENGTH, Credential};
pub use environment::{SecretEnvironmentValue, take_supervisor_token};
pub use identity::{DroppedPrivileges, ServiceIdentity};
pub use network::OutboundNetworkGuard;

/// A failure that leaves the caller unauthorized to receive secret bytes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum SecurityError {
    /// The operating system is not an implemented product or development path.
    UnsupportedPlatform,
    /// The core-file hard limit and soft limit could not both be set to zero.
    CoreLimitSetup,
    /// The effective core-file limits could not be verified as zero.
    CoreLimitVerification,
    /// Process dumpability or debugger attachment could not be disabled.
    DumpabilitySetup,
    /// The effective no-dump state could not be verified.
    DumpabilityVerification,
    /// The helper had no live, stable parent process to bind to.
    ParentProcessUnavailable,
    /// The parent-death signal could not be installed.
    ParentDeathSignalSetup,
    /// The installed parent-death signal could not be verified.
    ParentDeathSignalVerification,
    /// Current and future process mappings could not be locked.
    AddressSpaceLockSetup,
    /// Effective locking of current and future mappings could not be verified.
    AddressSpaceLockVerification,
    /// The inherited descriptor could not be read.
    CredentialRead,
    /// The inherited descriptor ended before the fixed credential length.
    CredentialTooShort,
    /// The inherited descriptor contained data after the fixed credential.
    CredentialTrailingData,
    /// The required Supervisor token was absent or could not be read.
    EnvironmentSecretRead,
    /// The Supervisor token could not be removed from the process environment.
    EnvironmentSecretRemoval,
    /// A service identity used zero IDs or did not separate its primary and IPC groups.
    InvalidServiceIdentity,
    /// Irreversible no-new-privileges mode could not be established.
    NoNewPrivilegesSetup,
    /// Irreversible no-new-privileges mode could not be verified.
    NoNewPrivilegesVerification,
    /// The exact supplementary IPC group could not be installed.
    SupplementaryGroupsSetup,
    /// The exact supplementary IPC group could not be verified.
    SupplementaryGroupsVerification,
    /// Real, effective, and saved group IDs could not be dropped.
    GroupDropSetup,
    /// Real, effective, and saved group IDs did not match the service identity.
    GroupDropVerification,
    /// Real, effective, and saved user IDs could not be dropped.
    UserDropSetup,
    /// Real, effective, and saved user IDs did not match the service identity.
    UserDropVerification,
    /// Effective Linux capabilities remained after the identity drop.
    CapabilityVerification,
    /// The outbound-network syscall filter could not be installed.
    OutboundNetworkFilterSetup,
    /// The effective outbound-network syscall filter could not be verified.
    OutboundNetworkFilterVerification,
}

impl SecurityError {
    fn label(self) -> &'static str {
        match self {
            Self::UnsupportedPlatform => "unsupported security platform",
            Self::CoreLimitSetup => "core limit setup failed",
            Self::CoreLimitVerification => "core limit verification failed",
            Self::DumpabilitySetup => "dumpability setup failed",
            Self::DumpabilityVerification => "dumpability verification failed",
            Self::ParentProcessUnavailable => "parent process unavailable",
            Self::ParentDeathSignalSetup => "parent-death signal setup failed",
            Self::ParentDeathSignalVerification => "parent-death signal verification failed",
            Self::AddressSpaceLockSetup => "address-space lock setup failed",
            Self::AddressSpaceLockVerification => "address-space lock verification failed",
            Self::CredentialRead => "credential read failed",
            Self::CredentialTooShort => "credential input was too short",
            Self::CredentialTrailingData => "credential input had trailing data",
            Self::EnvironmentSecretRead => "environment secret read failed",
            Self::EnvironmentSecretRemoval => "environment secret removal failed",
            Self::InvalidServiceIdentity => "invalid service identity",
            Self::NoNewPrivilegesSetup => "no-new-privileges setup failed",
            Self::NoNewPrivilegesVerification => "no-new-privileges verification failed",
            Self::SupplementaryGroupsSetup => "supplementary-group setup failed",
            Self::SupplementaryGroupsVerification => "supplementary-group verification failed",
            Self::GroupDropSetup => "group drop failed",
            Self::GroupDropVerification => "group drop verification failed",
            Self::UserDropSetup => "user drop failed",
            Self::UserDropVerification => "user drop verification failed",
            Self::CapabilityVerification => "capability verification failed",
            Self::OutboundNetworkFilterSetup => "outbound-network filter setup failed",
            Self::OutboundNetworkFilterVerification => {
                "outbound-network filter verification failed"
            }
        }
    }
}

impl fmt::Display for SecurityError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.label())
    }
}

impl std::error::Error for SecurityError {}

/// Proof that Linux will terminate this process if its current parent exits.
///
/// Construction rejects an already orphaned process and a parent change that
/// races with signal installation. The binding uses an uncatchable termination
/// signal and is re-verifiable immediately before sensitive input is read.
#[must_use = "the parent-death guard must remain live while the helper runs"]
pub struct ParentDeathGuard {
    parent_process_id: u32,
    not_send_or_sync: PhantomData<Rc<()>>,
}

impl ParentDeathGuard {
    /// Binds this process to its current live parent.
    ///
    /// Linux on a supported 64-bit architecture is the product path. Other
    /// platforms fail closed.
    pub fn establish() -> Result<Self, SecurityError> {
        let parent_process_id = platform::bind_parent_death()?;
        Ok(Self {
            parent_process_id,
            not_send_or_sync: PhantomData,
        })
    }

    /// Re-verifies both the parent identity and the installed death signal.
    pub fn verify(&self) -> Result<(), SecurityError> {
        platform::verify_parent_death(self.parent_process_id)
    }
}

impl fmt::Debug for ParentDeathGuard {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentDeathGuard([BOUND])")
    }
}

/// Proof that the dedicated process established and verified its security
/// controls.
///
/// This value is neither cloneable nor transferable across threads. The
/// controls are process-global, so the dedicated process must not call APIs
/// that weaken dump or memory-lock state after construction. State is
/// re-verified immediately before each credential read.
#[must_use = "a hardened process guard is required to receive credentials"]
pub struct GuardedProcess {
    not_send_or_sync: PhantomData<Rc<()>>,
}

impl GuardedProcess {
    /// Applies and verifies all mandatory controls for the current platform.
    ///
    /// Linux on a 64-bit target is the product path. Other platforms,
    /// including macOS and 32-bit Linux, fail closed.
    pub fn harden() -> Result<Self, SecurityError> {
        platform::establish()?;
        Ok(Self {
            not_send_or_sync: PhantomData,
        })
    }

    /// Admits one fixed credential already received by the hardened process.
    ///
    /// The caller must move the only application-owned byte array into this
    /// method and must clear any transport decoding buffers independently.
    /// Process security state is re-verified before the value becomes
    /// available through [`Credential`].
    pub fn admit_credential(
        &self,
        bytes: [u8; CREDENTIAL_LENGTH],
    ) -> Result<Credential, SecurityError> {
        platform::verify()?;
        Ok(Credential::from_bytes(bytes))
    }

    /// Reads exactly one fixed-size credential from an inherited descriptor.
    ///
    /// The descriptor is consumed and closed on every return path. The sender
    /// must close its end after writing: end-of-file is required to prove that
    /// no trailing data exists. Process security state is re-verified before
    /// any byte is read.
    #[cfg(unix)]
    pub fn read_credential(
        &self,
        inherited_descriptor: OwnedFd,
    ) -> Result<Credential, SecurityError> {
        platform::verify()?;
        let mut source = File::from(inherited_descriptor);
        credential::read_from(&mut source)
    }

    /// Reads exactly one fixed-size credential from piped standard input.
    ///
    /// This safely duplicates the inherited stdin descriptor and performs an
    /// unbuffered file read, avoiding both raw-fd ownership hazards and the
    /// process-global buffered stdin reader. The sender must close the pipe
    /// after writing so EOF can prove that no trailing data exists.
    #[cfg(unix)]
    pub fn read_credential_from_stdin(&self) -> Result<Credential, SecurityError> {
        let descriptor = std::io::stdin()
            .as_fd()
            .try_clone_to_owned()
            .map_err(|_| SecurityError::CredentialRead)?;
        self.read_credential(descriptor)
    }
}

impl fmt::Debug for GuardedProcess {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("GuardedProcess([HARDENED])")
    }
}
