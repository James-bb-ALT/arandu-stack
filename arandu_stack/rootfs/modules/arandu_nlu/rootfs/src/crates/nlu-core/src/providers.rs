use crate::{
    BinaryId, CatalogGeneration, ConfigurationId, InvocationId, LanguagePackageId, RequestText,
    SessionSnapshotId,
};

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

pub trait LogicalClock {
    fn now(&self) -> LogicalTime;
}

pub trait IdentifierProvider {
    fn next_invocation_id(&mut self) -> InvocationId;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SemanticEnvelope {
    binary: BinaryId,
    configuration: ConfigurationId,
    language_package: LanguagePackageId,
    catalog_generation: CatalogGeneration,
    session_snapshot: SessionSnapshotId,
    logical_time: LogicalTime,
    request: RequestText,
}

pub struct SemanticEnvelopeParts {
    pub binary: BinaryId,
    pub configuration: ConfigurationId,
    pub language_package: LanguagePackageId,
    pub catalog_generation: CatalogGeneration,
    pub session_snapshot: SessionSnapshotId,
    pub logical_time: LogicalTime,
    pub request: RequestText,
}

impl SemanticEnvelope {
    #[must_use]
    pub fn new(parts: SemanticEnvelopeParts) -> Self {
        Self {
            binary: parts.binary,
            configuration: parts.configuration,
            language_package: parts.language_package,
            catalog_generation: parts.catalog_generation,
            session_snapshot: parts.session_snapshot,
            logical_time: parts.logical_time,
            request: parts.request,
        }
    }

    #[must_use]
    pub const fn binary(&self) -> &BinaryId {
        &self.binary
    }

    #[must_use]
    pub const fn configuration(&self) -> &ConfigurationId {
        &self.configuration
    }

    #[must_use]
    pub const fn language_package(&self) -> &LanguagePackageId {
        &self.language_package
    }

    #[must_use]
    pub const fn catalog_generation(&self) -> CatalogGeneration {
        self.catalog_generation
    }

    #[must_use]
    pub const fn session_snapshot(&self) -> &SessionSnapshotId {
        &self.session_snapshot
    }

    #[must_use]
    pub const fn logical_time(&self) -> LogicalTime {
        self.logical_time
    }

    #[must_use]
    pub const fn request(&self) -> &RequestText {
        &self.request
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FixedClock(LogicalTime);

    impl LogicalClock for FixedClock {
        fn now(&self) -> LogicalTime {
            self.0
        }
    }

    struct FixedIds(u8);

    impl IdentifierProvider for FixedIds {
        fn next_invocation_id(&mut self) -> InvocationId {
            let id = InvocationId::new(&format!("fixture_tecnica:invocation_{}", self.0))
                .expect("valid fixture ID");
            self.0 += 1;
            id
        }
    }

    #[test]
    fn logical_time_and_identifier_creation_are_explicit() {
        let clock = FixedClock(LogicalTime::from_ticks(17));
        assert_eq!(clock.now().ticks(), 17);

        let mut ids = FixedIds(1);
        assert_eq!(
            ids.next_invocation_id().as_str(),
            "fixture_tecnica:invocation_1"
        );
        assert_eq!(
            ids.next_invocation_id().as_str(),
            "fixture_tecnica:invocation_2"
        );
    }

    #[test]
    fn semantic_envelope_names_every_explicit_input() {
        let envelope = SemanticEnvelope::new(SemanticEnvelopeParts {
            binary: BinaryId::new("fixture_tecnica:binary").expect("ID"),
            configuration: ConfigurationId::new("fixture_tecnica:configuration").expect("ID"),
            language_package: LanguagePackageId::new("fixture_tecnica:language").expect("ID"),
            catalog_generation: CatalogGeneration::new(3).expect("generation"),
            session_snapshot: SessionSnapshotId::new("fixture_tecnica:session").expect("ID"),
            logical_time: LogicalTime::from_ticks(4),
            request: RequestText::new("FIXTURE_TECNICA_A".into()).expect("request"),
        });
        assert_eq!(envelope.catalog_generation().get(), 3);
        assert_eq!(envelope.logical_time().ticks(), 4);
        assert_eq!(envelope.request().as_bytes(), b"FIXTURE_TECNICA_A");
    }
}
