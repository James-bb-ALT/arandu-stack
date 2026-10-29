use core::fmt;
use std::sync::{Arc, RwLock};

use nlu_core::CatalogGeneration;

use crate::{CatalogError, CatalogSnapshot};

pub struct CatalogStore {
    current: RwLock<Arc<CatalogSnapshot>>,
}

impl CatalogStore {
    #[must_use]
    pub fn new(initial: Arc<CatalogSnapshot>) -> Self {
        Self {
            current: RwLock::new(initial),
        }
    }

    pub fn snapshot(&self) -> Result<Arc<CatalogSnapshot>, CatalogError> {
        self.current
            .read()
            .map(|snapshot| Arc::clone(&snapshot))
            .map_err(|_| CatalogError::LockPoisoned)
    }

    pub fn publish(
        &self,
        expected: CatalogGeneration,
        next: Arc<CatalogSnapshot>,
    ) -> Result<(), CatalogError> {
        let successor = expected
            .get()
            .checked_add(1)
            .ok_or(CatalogError::CatalogGenerationOverflow)?;
        if next.generation().get() != successor {
            return Err(CatalogError::NonSequentialCatalogGeneration);
        }

        let mut current = self
            .current
            .write()
            .map_err(|_| CatalogError::LockPoisoned)?;
        if current.generation() != expected {
            return Err(CatalogError::StaleCatalogGeneration);
        }
        *current = next;
        Ok(())
    }
}

impl fmt::Debug for CatalogStore {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CatalogStore { residential_data: redacted }")
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Barrier};
    use std::thread;

    use nlu_core::CatalogGeneration;

    use super::*;
    use crate::CatalogSnapshotInput;

    fn snapshot(generation: u64) -> Arc<CatalogSnapshot> {
        let generation =
            CatalogGeneration::new(generation).expect("FIXTURE_TECNICA valid generation");
        Arc::new(
            CatalogSnapshot::build(CatalogSnapshotInput::empty(generation))
                .expect("FIXTURE_TECNICA empty snapshot"),
        )
    }

    #[test]
    fn stale_skipped_and_overflowing_publications_fail_closed() {
        let store = CatalogStore::new(snapshot(1));
        assert_eq!(
            store.publish(CatalogGeneration::new(1).expect("generation"), snapshot(3)),
            Err(CatalogError::NonSequentialCatalogGeneration)
        );
        assert_eq!(
            store.publish(
                CatalogGeneration::new(2).expect("stale expected"),
                snapshot(3)
            ),
            Err(CatalogError::StaleCatalogGeneration)
        );

        let max = CatalogGeneration::new(u64::MAX).expect("maximum generation");
        let max_store = CatalogStore::new(snapshot(u64::MAX));
        assert_eq!(
            max_store.publish(max, snapshot(1)),
            Err(CatalogError::CatalogGenerationOverflow)
        );
    }

    #[test]
    fn publication_is_atomic_and_retained_arcs_remain_coherent() {
        let store = Arc::new(CatalogStore::new(snapshot(1)));
        let retained = store.snapshot().expect("retained generation one");
        let barrier = Arc::new(Barrier::new(3));
        let mut publishers = Vec::new();
        for _ in 0..2 {
            let store = Arc::clone(&store);
            let barrier = Arc::clone(&barrier);
            publishers.push(thread::spawn(move || {
                barrier.wait();
                store.publish(
                    CatalogGeneration::new(1).expect("expected generation"),
                    snapshot(2),
                )
            }));
        }
        barrier.wait();
        let results = publishers
            .into_iter()
            .map(|publisher| publisher.join().expect("FIXTURE_TECNICA publisher"))
            .collect::<Vec<_>>();
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(
            results
                .iter()
                .filter(|result| **result == Err(CatalogError::StaleCatalogGeneration))
                .count(),
            1
        );
        assert_eq!(store.snapshot().expect("current").generation().get(), 2);
        assert_eq!(retained.generation().get(), 1);
    }

    #[test]
    fn poisoned_lock_returns_an_error_instead_of_panicking() {
        let store = Arc::new(CatalogStore::new(snapshot(1)));
        let poisoner = Arc::clone(&store);
        let _ = thread::spawn(move || {
            let _guard = poisoner
                .current
                .write()
                .expect("FIXTURE_TECNICA unpoisoned lock");
            panic!("FIXTURE_TECNICA deliberate lock poison");
        })
        .join();

        assert!(matches!(store.snapshot(), Err(CatalogError::LockPoisoned)));
        assert_eq!(
            store.publish(CatalogGeneration::new(1).expect("generation"), snapshot(2)),
            Err(CatalogError::LockPoisoned)
        );
    }
}
