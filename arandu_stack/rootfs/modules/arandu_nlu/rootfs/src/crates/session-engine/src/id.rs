use core::fmt;

pub const SESSION_ID_BYTES: usize = 32;

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct SessionId([u8; SESSION_ID_BYTES]);

impl SessionId {
    #[must_use]
    pub const fn from_bytes(bytes: [u8; SESSION_ID_BYTES]) -> Self {
        Self(bytes)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; SESSION_ID_BYTES] {
        &self.0
    }
}

impl fmt::Debug for SessionId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SessionId { bytes: redacted }")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifier_is_fixed_size_opaque_and_debug_redacted() {
        let canary = [0xa5; SESSION_ID_BYTES];
        let id = SessionId::from_bytes(canary);
        assert_eq!(id.as_bytes(), &canary);
        assert_eq!(core::mem::size_of::<SessionId>(), SESSION_ID_BYTES);
        assert!(!format!("{id:?}").contains("a5"));
    }
}
