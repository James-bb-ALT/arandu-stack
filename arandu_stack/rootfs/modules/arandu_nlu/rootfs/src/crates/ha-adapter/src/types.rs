use core::fmt;
use core::num::{NonZeroU16, NonZeroU64};

use crate::{AdapterError, Result};

pub const OPAQUE_ID_BYTES: usize = 32;
pub const MAX_HA_IDENTIFIER_BYTES: usize = 64;
pub const MAX_CAPABILITY_BYTES: usize = 128;
pub const REGISTRY_ENTRY_ID_BYTES: usize = 32;

macro_rules! opaque_identifier {
    ($name:ident) => {
        #[derive(Clone, Copy, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name([u8; OPAQUE_ID_BYTES]);

        impl $name {
            pub fn new(bytes: [u8; OPAQUE_ID_BYTES]) -> Result<Self> {
                if bytes.iter().all(|byte| *byte == 0) {
                    return Err(AdapterError::InvalidOpaqueIdentifier);
                }
                Ok(Self(bytes))
            }

            #[must_use]
            pub const fn as_bytes(&self) -> &[u8; OPAQUE_ID_BYTES] {
                &self.0
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(concat!(stringify!($name), " { bytes: redacted }"))
            }
        }
    };
}

opaque_identifier!(PairingEpoch);
opaque_identifier!(ConnectionNonce);
opaque_identifier!(PlanDigest);
opaque_identifier!(SessionId);

#[derive(Clone, Copy, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct OperationId(NonZeroU64);

impl OperationId {
    pub fn new(value: u64) -> Result<Self> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or(AdapterError::InvalidOperationId)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

impl fmt::Debug for OperationId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("OperationId { value: redacted }")
    }
}

#[derive(Clone, Copy, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct NodeAttemptId(NonZeroU16);

impl NodeAttemptId {
    pub fn new(value: u16) -> Result<Self> {
        NonZeroU16::new(value)
            .map(Self)
            .ok_or(AdapterError::InvalidNodeAttemptId)
    }

    #[must_use]
    pub const fn get(self) -> u16 {
        self.0.get()
    }
}

impl fmt::Debug for NodeAttemptId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NodeAttemptId { value: redacted }")
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct NodeOrdinal(NonZeroU16);

impl NodeOrdinal {
    pub fn new(value: u16) -> Result<Self> {
        NonZeroU16::new(value)
            .map(Self)
            .ok_or(AdapterError::InvalidNodeOrdinal)
    }

    #[must_use]
    pub const fn get(self) -> u16 {
        self.0.get()
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct CatalogGeneration(NonZeroU64);

impl CatalogGeneration {
    pub fn new(value: u64) -> Result<Self> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or(AdapterError::InvalidCatalogGeneration)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct LogicalTime(u64);

impl LogicalTime {
    #[must_use]
    pub const fn from_ticks(ticks: u64) -> Self {
        Self(ticks)
    }

    #[must_use]
    pub const fn ticks(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct PositionPercent(u8);

impl PositionPercent {
    pub fn new(value: u8) -> Result<Self> {
        if value > 100 {
            return Err(AdapterError::InvalidPosition);
        }
        Ok(Self(value))
    }

    #[must_use]
    pub const fn get(self) -> u8 {
        self.0
    }
}

#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct CapabilityId(Box<str>);

impl CapabilityId {
    pub fn new(value: &str) -> Result<Self> {
        if value.is_empty() || value.len() > MAX_CAPABILITY_BYTES || !value.is_ascii() {
            return Err(AdapterError::InvalidCapability);
        }
        let mut parts = value.split(':');
        let namespace = parts.next().ok_or(AdapterError::InvalidCapability)?;
        let local = parts.next().ok_or(AdapterError::InvalidCapability)?;
        if parts.next().is_some()
            || !valid_stable_component(namespace)
            || !valid_stable_component(local)
        {
            return Err(AdapterError::InvalidCapability);
        }
        Ok(Self(value.into()))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for CapabilityId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CapabilityId")
            .field("bytes", &self.0.len())
            .finish()
    }
}

fn valid_stable_component(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes[0].is_ascii_lowercase()
        && bytes
            .last()
            .is_some_and(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        && bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-' || *byte == b'_'
        })
}

#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct RegistryEntryId(Box<str>);

impl RegistryEntryId {
    pub fn new(value: &str) -> Result<Self> {
        if value.len() != REGISTRY_ENTRY_ID_BYTES
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(AdapterError::InvalidRegistryEntryId);
        }
        Ok(Self(value.into()))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for RegistryEntryId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RegistryEntryId { value: redacted }")
    }
}

macro_rules! ha_identifier {
    ($name:ident) => {
        #[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name(Box<str>);

        impl $name {
            pub fn new(value: &str) -> Result<Self> {
                if !valid_ha_identifier(value) {
                    return Err(AdapterError::InvalidIdentifier);
                }
                Ok(Self(value.into()))
            }

            #[must_use]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct(stringify!($name))
                    .field("bytes", &self.0.len())
                    .finish()
            }
        }
    };
}

ha_identifier!(ContextId);
ha_identifier!(CallerId);

fn valid_ha_identifier(value: &str) -> bool {
    let bytes = value.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= MAX_HA_IDENTIFIER_BYTES
        && bytes
            .first()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes
            .last()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'-' || *byte == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiers_are_strict_and_debug_is_redacted() {
        assert_eq!(
            PairingEpoch::new([0; OPAQUE_ID_BYTES]),
            Err(AdapterError::InvalidOpaqueIdentifier)
        );
        assert!(CapabilityId::new("ha:state_query").is_ok());
        assert!(CapabilityId::new("HA:state_query").is_err());
        assert!(RegistryEntryId::new("a".repeat(32).as_str()).is_ok());
        assert!(RegistryEntryId::new("A".repeat(32).as_str()).is_err());

        let marker = "fixture_tecnica_context_canary";
        let context = ContextId::new(marker).expect("technical context ID");
        assert!(!format!("{context:?}").contains(marker));
    }
}
