use crate::{CoreError, MAX_IDENTIFIER_BYTES};
use core::fmt;

#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
struct StableIdentifier(Box<str>);

impl StableIdentifier {
    fn new(value: &str) -> Result<Self, CoreError> {
        if value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES || !value.is_ascii() {
            return Err(CoreError::InvalidIdentifier);
        }

        let mut components = value.split(':');
        let namespace = components.next().ok_or(CoreError::InvalidIdentifier)?;
        let local = components.next().ok_or(CoreError::InvalidIdentifier)?;
        if components.next().is_some()
            || !Self::valid_component(namespace)
            || !Self::valid_component(local)
        {
            return Err(CoreError::InvalidIdentifier);
        }

        Ok(Self(value.into()))
    }

    fn valid_component(component: &str) -> bool {
        let bytes = component.as_bytes();
        if bytes.is_empty()
            || !bytes[0].is_ascii_lowercase()
            || matches!(bytes.last(), Some(b'-' | b'_'))
        {
            return false;
        }

        bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-' || *byte == b'_'
        })
    }

    fn as_str(&self) -> &str {
        &self.0
    }
}

macro_rules! identifier_type {
    ($name:ident) => {
        #[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name(StableIdentifier);

        impl $name {
            pub fn new(value: &str) -> Result<Self, CoreError> {
                StableIdentifier::new(value).map(Self)
            }

            #[must_use]
            pub fn as_str(&self) -> &str {
                self.0.as_str()
            }
        }

        impl TryFrom<&str> for $name {
            type Error = CoreError;

            fn try_from(value: &str) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter
                    .debug_struct(stringify!($name))
                    .field("bytes", &self.0.as_str().len())
                    .finish()
            }
        }
    };
}

identifier_type!(BinaryId);
identifier_type!(CapabilityId);
identifier_type!(ConfigurationId);
identifier_type!(EntityId);
identifier_type!(IntentId);
identifier_type!(InvocationId);
identifier_type!(LanguagePackageId);
identifier_type!(NodeId);
identifier_type!(OperationId);
identifier_type!(OptionId);
identifier_type!(SessionSnapshotId);
identifier_type!(SlotId);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_one_explicit_ascii_namespace() {
        let id = IntentId::new("fixture_tecnica:intent_a").expect("valid technical ID");
        assert_eq!(id.as_str(), "fixture_tecnica:intent_a");
    }

    #[test]
    fn rejects_alias_prone_or_unbounded_identifiers() {
        let invalid = [
            "",
            "fixture_tecnica",
            ":fixture_tecnica",
            "fixture_tecnica:",
            "fixture_tecnica:a:b",
            "Fixture_tecnica:a",
            "fixture_tecnica:a.",
            "fixture_tecnica:a-",
            "fixture_tecnica:\u{ff41}",
        ];
        for value in invalid {
            assert_eq!(IntentId::new(value), Err(CoreError::InvalidIdentifier));
        }

        let oversized = format!("fixture_tecnica:{}", "a".repeat(MAX_IDENTIFIER_BYTES));
        assert_eq!(IntentId::new(&oversized), Err(CoreError::InvalidIdentifier));
    }

    #[test]
    fn debug_does_not_disclose_identifier_bytes() {
        let marker = "fixture_tecnica:private_canary";
        let rendered = format!("{:?}", EntityId::new(marker).expect("valid ID"));
        assert!(!rendered.contains(marker));
    }
}
