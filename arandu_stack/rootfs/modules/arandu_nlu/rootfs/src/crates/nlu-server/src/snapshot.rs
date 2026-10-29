use core::fmt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};

use nlu_core::LogicalTime;

use crate::{Result, ServerError, ServerErrorCode};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SnapshotMetadata {
    runtime_generation: u64,
    catalog_generation: u64,
    policy_generation: u64,
    configuration_generation: u64,
    language_generation: u64,
}

impl SnapshotMetadata {
    pub fn new(
        runtime_generation: u64,
        catalog_generation: u64,
        policy_generation: u64,
        configuration_generation: u64,
        language_generation: u64,
    ) -> Result<Self> {
        if [
            runtime_generation,
            catalog_generation,
            policy_generation,
            configuration_generation,
            language_generation,
        ]
        .contains(&0)
        {
            return Err(ServerError::new(ServerErrorCode::SnapshotGeneration));
        }
        Ok(Self {
            runtime_generation,
            catalog_generation,
            policy_generation,
            configuration_generation,
            language_generation,
        })
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

    #[must_use]
    pub const fn configuration_generation(self) -> u64 {
        self.configuration_generation
    }

    #[must_use]
    pub const fn language_generation(self) -> u64 {
        self.language_generation
    }
}

pub struct RuntimeSnapshot<T> {
    metadata: SnapshotMetadata,
    state: T,
    active: AtomicBool,
}

impl<T> RuntimeSnapshot<T> {
    #[must_use]
    pub const fn new(metadata: SnapshotMetadata, state: T) -> Self {
        Self {
            metadata,
            state,
            active: AtomicBool::new(true),
        }
    }

    #[must_use]
    pub const fn metadata(&self) -> SnapshotMetadata {
        self.metadata
    }

    #[must_use]
    pub const fn state(&self) -> &T {
        &self.state
    }

    pub(crate) fn ensure_active(&self) -> Result<()> {
        if !self.active.load(Ordering::Acquire) {
            return Err(ServerError::new(ServerErrorCode::RuntimeState));
        }
        Ok(())
    }

    fn retire(&self) {
        self.active.store(false, Ordering::Release);
    }
}

impl<T> fmt::Debug for RuntimeSnapshot<T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RuntimeSnapshot")
            .field("metadata", &self.metadata)
            .field("runtime_state", &"redacted")
            .finish()
    }
}

pub trait SnapshotState {
    fn validate_snapshot_metadata(&self, metadata: SnapshotMetadata) -> Result<()>;
    fn prepare_successor(&self, _successor: &Self) -> Result<()> {
        Ok(())
    }
    fn invalidate_for_reload(&self, now: LogicalTime) -> Result<()>;
}

pub struct SnapshotStore<T: SnapshotState> {
    current: RwLock<Arc<RuntimeSnapshot<T>>>,
}

impl<T: SnapshotState> SnapshotStore<T> {
    pub fn new(initial: Arc<RuntimeSnapshot<T>>) -> Result<Self> {
        initial
            .state()
            .validate_snapshot_metadata(initial.metadata())?;
        Ok(Self {
            current: RwLock::new(initial),
        })
    }

    pub fn lease(&self) -> Result<Arc<RuntimeSnapshot<T>>> {
        self.current
            .read()
            .map(|snapshot| Arc::clone(&snapshot))
            .map_err(|_| ServerError::new(ServerErrorCode::LockPoisoned))
    }

    pub fn replace_at(&self, next: Arc<RuntimeSnapshot<T>>, now: LogicalTime) -> Result<()> {
        next.state().validate_snapshot_metadata(next.metadata())?;
        next.ensure_active()?;
        let mut current = self
            .current
            .write()
            .map_err(|_| ServerError::new(ServerErrorCode::LockPoisoned))?;
        validate_successor(current.metadata(), next.metadata())?;
        current.state().prepare_successor(next.state())?;
        current.state().invalidate_for_reload(now)?;
        current.retire();
        *current = next;
        Ok(())
    }
}

impl<T: SnapshotState> fmt::Debug for SnapshotStore<T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SnapshotStore { runtime_state: redacted }")
    }
}

fn validate_successor(current: SnapshotMetadata, next: SnapshotMetadata) -> Result<()> {
    let expected_runtime = current
        .runtime_generation
        .checked_add(1)
        .ok_or_else(|| ServerError::new(ServerErrorCode::SnapshotGeneration))?;
    let component_generations = [
        (current.catalog_generation, next.catalog_generation),
        (current.policy_generation, next.policy_generation),
        (
            current.configuration_generation,
            next.configuration_generation,
        ),
        (current.language_generation, next.language_generation),
    ];
    if next.runtime_generation != expected_runtime
        || component_generations.iter().any(|(old, new)| new < old)
        || component_generations.iter().all(|(old, new)| new == old)
    {
        return Err(ServerError::new(ServerErrorCode::SnapshotGeneration));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Barrier};
    use std::thread;

    use super::*;

    fn metadata(runtime: u64, catalog: u64, policy: u64) -> SnapshotMetadata {
        SnapshotMetadata::new(runtime, catalog, policy, 1, 1).expect("metadata")
    }

    #[derive(Debug)]
    struct TestState {
        catalog_generation: u64,
        policy_generation: u64,
        value: u64,
        invalidated: AtomicBool,
    }

    impl SnapshotState for TestState {
        fn validate_snapshot_metadata(&self, metadata: SnapshotMetadata) -> Result<()> {
            if self.invalidated.load(Ordering::Acquire)
                || self.catalog_generation != metadata.catalog_generation()
                || self.policy_generation != metadata.policy_generation()
                || metadata.configuration_generation() != 1
                || metadata.language_generation() != 1
            {
                return Err(ServerError::new(ServerErrorCode::RuntimeState));
            }
            Ok(())
        }

        fn invalidate_for_reload(&self, _now: LogicalTime) -> Result<()> {
            self.invalidated.store(true, Ordering::Release);
            Ok(())
        }
    }

    fn state(catalog_generation: u64, policy_generation: u64, value: u64) -> TestState {
        TestState {
            catalog_generation,
            policy_generation,
            value,
            invalidated: AtomicBool::new(false),
        }
    }

    #[test]
    fn invalid_replacement_retains_the_prior_snapshot() {
        let initial = Arc::new(RuntimeSnapshot::new(metadata(1, 1, 1), state(1, 1, 11)));
        let store = SnapshotStore::new(Arc::clone(&initial)).expect("valid initial snapshot");
        let invalid = Arc::new(RuntimeSnapshot::new(metadata(2, 2, 1), state(1, 1, 22)));
        assert_eq!(
            store
                .replace_at(invalid, LogicalTime::from_ticks(1))
                .expect_err("state and metadata mismatch")
                .code(),
            ServerErrorCode::RuntimeState
        );
        assert!(Arc::ptr_eq(&store.lease().expect("retained"), &initial));

        let skipped = Arc::new(RuntimeSnapshot::new(metadata(3, 2, 1), state(2, 1, 33)));
        assert_eq!(
            store
                .replace_at(skipped, LogicalTime::from_ticks(1))
                .expect_err("skipped generation")
                .code(),
            ServerErrorCode::SnapshotGeneration
        );
        assert!(Arc::ptr_eq(&store.lease().expect("retained"), &initial));
    }

    #[test]
    fn retained_lease_is_invalidated_before_complete_generation_exchange() {
        let initial = Arc::new(RuntimeSnapshot::new(metadata(1, 1, 1), state(1, 1, 11)));
        let store = SnapshotStore::new(Arc::clone(&initial)).expect("valid initial snapshot");
        let retained = store.lease().expect("retained");
        let next = Arc::new(RuntimeSnapshot::new(metadata(2, 2, 1), state(2, 1, 22)));
        store
            .replace_at(Arc::clone(&next), LogicalTime::from_ticks(1))
            .expect("replace");
        assert_eq!(retained.state().value, 11);
        assert!(retained.state().invalidated.load(Ordering::Acquire));
        assert_eq!(
            retained
                .ensure_active()
                .expect_err("retained generation is retired")
                .code(),
            ServerErrorCode::RuntimeState
        );
        assert_eq!(store.lease().expect("current").state().value, 22);
    }

    #[test]
    fn competing_reload_is_atomic_under_real_concurrency() {
        let store = Arc::new(
            SnapshotStore::new(Arc::new(RuntimeSnapshot::new(
                metadata(1, 1, 1),
                state(1, 1, 1),
            )))
            .expect("valid initial snapshot"),
        );
        let barrier = Arc::new(Barrier::new(3));
        let mut threads = Vec::new();
        for value in [2_u64, 3_u64] {
            let store = Arc::clone(&store);
            let barrier = Arc::clone(&barrier);
            threads.push(thread::spawn(move || {
                let next = Arc::new(RuntimeSnapshot::new(
                    metadata(2, value, 1),
                    state(value, 1, value),
                ));
                barrier.wait();
                store.replace_at(next, LogicalTime::from_ticks(1))
            }));
        }
        barrier.wait();
        let results = threads
            .into_iter()
            .map(|thread| thread.join().expect("thread"))
            .collect::<Vec<_>>();
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(
            results
                .iter()
                .filter(|result| {
                    result
                        .as_ref()
                        .is_err_and(|error| error.code() == ServerErrorCode::SnapshotGeneration)
                })
                .count(),
            1
        );
        let current = store.lease().expect("current");
        assert_eq!(current.metadata().runtime_generation(), 2);
        assert_eq!(
            current.metadata().catalog_generation(),
            current.state().catalog_generation
        );
    }
}
