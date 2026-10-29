use core::fmt;

use crate::{Result, SessionError, SessionErrorCode};

pub const SESSION_BINDING_BYTES: usize = 32;

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
struct OpaqueBinding([u8; SESSION_BINDING_BYTES]);

impl OpaqueBinding {
    fn from_bytes(bytes: [u8; SESSION_BINDING_BYTES]) -> Result<Self> {
        if bytes.iter().all(|byte| *byte == 0) {
            return Err(SessionError::new(SessionErrorCode::InvalidBinding));
        }
        Ok(Self(bytes))
    }

    const fn as_bytes(&self) -> &[u8; SESSION_BINDING_BYTES] {
        &self.0
    }
}

macro_rules! opaque_binding {
    ($name:ident) => {
        #[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
        pub struct $name(OpaqueBinding);

        impl $name {
            pub fn from_bytes(bytes: [u8; SESSION_BINDING_BYTES]) -> Result<Self> {
                OpaqueBinding::from_bytes(bytes).map(Self)
            }

            #[must_use]
            pub const fn as_bytes(&self) -> &[u8; SESSION_BINDING_BYTES] {
                self.0.as_bytes()
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(concat!(stringify!($name), " { bytes: redacted }"))
            }
        }
    };
}

opaque_binding!(CallerBinding);
opaque_binding!(ContextBinding);

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct PairingEpoch(OpaqueBinding);

impl PairingEpoch {
    pub fn new(bytes: [u8; SESSION_BINDING_BYTES]) -> Result<Self> {
        OpaqueBinding::from_bytes(bytes).map(Self)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; SESSION_BINDING_BYTES] {
        self.0.as_bytes()
    }
}

impl fmt::Debug for PairingEpoch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PairingEpoch { bytes: redacted }")
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct SessionBinding {
    epoch: PairingEpoch,
    caller: CallerBinding,
    context: ContextBinding,
}

impl SessionBinding {
    #[must_use]
    pub const fn new(epoch: PairingEpoch, caller: CallerBinding, context: ContextBinding) -> Self {
        Self {
            epoch,
            caller,
            context,
        }
    }

    #[must_use]
    pub const fn epoch(self) -> PairingEpoch {
        self.epoch
    }

    #[must_use]
    pub const fn caller(self) -> CallerBinding {
        self.caller
    }

    #[must_use]
    pub const fn context(self) -> ContextBinding {
        self.context
    }
}

impl fmt::Debug for SessionBinding {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SessionBinding { residential_data: redacted }")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bindings_are_nonzero_fixed_size_and_redacted() {
        assert_eq!(
            CallerBinding::from_bytes([0; SESSION_BINDING_BYTES])
                .expect_err("zero caller must reject")
                .code(),
            SessionErrorCode::InvalidBinding
        );
        assert_eq!(
            PairingEpoch::new([0; SESSION_BINDING_BYTES])
                .expect_err("zero epoch must reject")
                .code(),
            SessionErrorCode::InvalidBinding
        );

        let canary = [0xa5; SESSION_BINDING_BYTES];
        let binding = SessionBinding::new(
            PairingEpoch::new([1; SESSION_BINDING_BYTES]).expect("FIXTURE_TECNICA epoch"),
            CallerBinding::from_bytes(canary).expect("FIXTURE_TECNICA caller"),
            ContextBinding::from_bytes([0xb6; SESSION_BINDING_BYTES])
                .expect("FIXTURE_TECNICA context"),
        );
        assert_eq!(binding.caller().as_bytes(), &canary);
        assert!(!format!("{binding:?}").contains("a5"));
    }
}
