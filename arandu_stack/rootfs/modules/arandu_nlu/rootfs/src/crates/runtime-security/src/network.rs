use std::{fmt, marker::PhantomData, rc::Rc};

use crate::{SecurityError, platform};

/// Proof that the current process denies network creation and outbound sends.
///
/// Install this after binding the server's inherited-purpose Unix listener and
/// before creating any worker thread. The Linux filter is irreversible and is
/// inherited by subsequently created threads.
#[must_use = "the outbound-network guard must remain live with the server"]
pub struct OutboundNetworkGuard {
    not_send_or_sync: PhantomData<Rc<()>>,
}

impl OutboundNetworkGuard {
    /// Installs and verifies the product's Linux seccomp network filter.
    pub fn install() -> Result<Self, SecurityError> {
        platform::deny_outbound_network()?;
        Ok(Self {
            not_send_or_sync: PhantomData,
        })
    }

    /// Re-verifies that seccomp filter mode and no-new-privileges remain active.
    pub fn verify(&self) -> Result<(), SecurityError> {
        platform::verify_outbound_network_denial()
    }
}

impl fmt::Debug for OutboundNetworkGuard {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("OutboundNetworkGuard([ENFORCED])")
    }
}
