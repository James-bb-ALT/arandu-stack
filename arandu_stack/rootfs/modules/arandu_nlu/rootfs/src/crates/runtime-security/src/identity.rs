use std::{fmt, marker::PhantomData, rc::Rc};

use crate::{SecurityError, platform};

/// One unprivileged process identity with one distinct shared IPC group.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ServiceIdentity {
    uid: u32,
    primary_gid: u32,
    ipc_gid: u32,
}

impl ServiceIdentity {
    /// Constructs a non-root identity with a distinct supplementary IPC group.
    pub fn new(uid: u32, primary_gid: u32, ipc_gid: u32) -> Result<Self, SecurityError> {
        if uid == 0 || primary_gid == 0 || ipc_gid == 0 || primary_gid == ipc_gid {
            return Err(SecurityError::InvalidServiceIdentity);
        }
        Ok(Self {
            uid,
            primary_gid,
            ipc_gid,
        })
    }

    /// Returns the service user ID.
    #[must_use]
    pub const fn uid(self) -> u32 {
        self.uid
    }

    /// Returns the service primary group ID.
    #[must_use]
    pub const fn primary_gid(self) -> u32 {
        self.primary_gid
    }

    /// Returns the sole supplementary IPC group ID.
    #[must_use]
    pub const fn ipc_gid(self) -> u32 {
        self.ipc_gid
    }
}

/// Proof that the current process irreversibly dropped to a service identity.
///
/// The proof is intentionally neither cloneable nor transferable across
/// threads. Call this before creating worker threads or accepting traffic.
#[must_use = "the privilege-drop proof must remain live for process startup"]
pub struct DroppedPrivileges {
    identity: ServiceIdentity,
    not_send_or_sync: PhantomData<Rc<()>>,
}

impl DroppedPrivileges {
    /// Sets one supplementary group, drops all real/effective/saved IDs, and
    /// verifies no-new-privileges plus an empty effective capability set.
    pub fn establish(identity: ServiceIdentity) -> Result<Self, SecurityError> {
        platform::drop_privileges(identity)?;
        Ok(Self {
            identity,
            not_send_or_sync: PhantomData,
        })
    }

    /// Re-verifies the effective identity and irreversible controls.
    pub fn verify(&self) -> Result<(), SecurityError> {
        platform::verify_privileges(self.identity)
    }

    /// Returns the verified service identity without consulting environment.
    #[must_use]
    pub const fn identity(&self) -> ServiceIdentity {
        self.identity
    }
}

impl fmt::Debug for DroppedPrivileges {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DroppedPrivileges")
            .field("uid", &self.identity.uid)
            .field("primary_gid", &self.identity.primary_gid)
            .field("ipc_gid", &self.identity.ipc_gid)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_identity_is_non_root_and_keeps_groups_distinct() {
        assert!(ServiceIdentity::new(21_001, 21_001, 21_002).is_ok());
        for result in [
            ServiceIdentity::new(0, 21_001, 21_002),
            ServiceIdentity::new(21_001, 0, 21_002),
            ServiceIdentity::new(21_001, 21_001, 0),
            ServiceIdentity::new(21_001, 21_002, 21_002),
        ] {
            assert_eq!(result, Err(SecurityError::InvalidServiceIdentity));
        }
    }
}
