use crate::SnapshotMetadata;

pub const SUPPORTED_PROTOCOL_VERSIONS: [u16; 2] = [1, 2];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Readiness {
    Ready,
    Reloading,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct HealthSnapshot {
    readiness: Readiness,
    protocol_versions: [u16; 2],
    runtime_generation: u64,
    catalog_generation: u64,
    policy_generation: u64,
}

impl HealthSnapshot {
    #[must_use]
    pub const fn new(readiness: Readiness, metadata: SnapshotMetadata) -> Self {
        Self {
            readiness,
            protocol_versions: SUPPORTED_PROTOCOL_VERSIONS,
            runtime_generation: metadata.runtime_generation(),
            catalog_generation: metadata.catalog_generation(),
            policy_generation: metadata.policy_generation(),
        }
    }

    #[must_use]
    pub const fn readiness(self) -> Readiness {
        self.readiness
    }

    #[must_use]
    pub const fn protocol_versions(self) -> [u16; 2] {
        self.protocol_versions
    }

    #[must_use]
    pub const fn runtime_generation(self) -> u64 {
        self.runtime_generation
    }

    #[must_use]
    pub const fn catalog_generation(self) -> u64 {
        self.catalog_generation
    }

    #[must_use]
    pub const fn policy_generation(self) -> u64 {
        self.policy_generation
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_contains_only_closed_bounded_metadata() {
        let metadata = SnapshotMetadata::new(5, 4, 3, 2, 1).expect("metadata");
        let health = HealthSnapshot::new(Readiness::Ready, metadata);
        assert_eq!(health.readiness(), Readiness::Ready);
        assert_eq!(health.protocol_versions(), [1, 2]);
        assert_eq!(health.runtime_generation(), 5);
        assert_eq!(health.catalog_generation(), 4);
        assert_eq!(health.policy_generation(), 3);
        assert!(
            core::mem::size_of::<HealthSnapshot>() <= 48,
            "health remains fixed-size"
        );
    }
}
