use crate::SecurityError;
use crate::ServiceIdentity;

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
mod linux;
#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
mod procfs;

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
trait ProcessControls {
    fn set_core_limit_zero(&self) -> bool;
    fn disable_dumpability(&self) -> bool;
    fn lock_current_and_future(&self) -> bool;
    fn core_limit_is_zero(&self) -> bool;
    fn dumpability_is_disabled(&self) -> bool;
    fn current_and_future_are_locked(&self) -> bool;
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
trait ParentDeathControls {
    fn parent_process_id(&self) -> Option<u32>;
    fn set_parent_death_signal(&self) -> bool;
    fn parent_death_signal_is_set(&self) -> bool;
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
fn establish_with(controls: &impl ProcessControls) -> Result<(), SecurityError> {
    if !controls.set_core_limit_zero() {
        return Err(SecurityError::CoreLimitSetup);
    }
    if !controls.disable_dumpability() {
        return Err(SecurityError::DumpabilitySetup);
    }
    if !controls.lock_current_and_future() {
        return Err(SecurityError::AddressSpaceLockSetup);
    }
    verify_with(controls)
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
fn verify_with(controls: &impl ProcessControls) -> Result<(), SecurityError> {
    if !controls.core_limit_is_zero() {
        return Err(SecurityError::CoreLimitVerification);
    }
    verify_dumpability_with(controls)?;
    if !controls.current_and_future_are_locked() {
        return Err(SecurityError::AddressSpaceLockVerification);
    }
    Ok(())
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
fn restore_dumpability_with(controls: &impl ProcessControls) -> Result<(), SecurityError> {
    if !controls.disable_dumpability() {
        return Err(SecurityError::DumpabilitySetup);
    }
    verify_dumpability_with(controls)
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
fn verify_dumpability_with(controls: &impl ProcessControls) -> Result<(), SecurityError> {
    if !controls.dumpability_is_disabled() {
        return Err(SecurityError::DumpabilityVerification);
    }
    Ok(())
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
fn bind_parent_death_with(controls: &impl ParentDeathControls) -> Result<u32, SecurityError> {
    let parent_process_id = controls
        .parent_process_id()
        .filter(|process_id| *process_id > 1)
        .ok_or(SecurityError::ParentProcessUnavailable)?;
    if !controls.set_parent_death_signal() {
        return Err(SecurityError::ParentDeathSignalSetup);
    }
    verify_parent_death_with(controls, parent_process_id)?;
    Ok(parent_process_id)
}

#[cfg(any(
    test,
    all(
        target_os = "linux",
        target_pointer_width = "64",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )
))]
fn verify_parent_death_with(
    controls: &impl ParentDeathControls,
    expected_parent_process_id: u32,
) -> Result<(), SecurityError> {
    if controls.parent_process_id() != Some(expected_parent_process_id)
        || expected_parent_process_id <= 1
    {
        return Err(SecurityError::ParentProcessUnavailable);
    }
    if !controls.parent_death_signal_is_set() {
        return Err(SecurityError::ParentDeathSignalVerification);
    }
    Ok(())
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn establish() -> Result<(), SecurityError> {
    establish_with(&linux::LinuxControls)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn verify() -> Result<(), SecurityError> {
    verify_with(&linux::LinuxControls)
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn establish() -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn verify() -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn bind_parent_death() -> Result<u32, SecurityError> {
    bind_parent_death_with(&linux::LinuxControls)
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn bind_parent_death() -> Result<u32, SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn verify_parent_death(expected_parent_process_id: u32) -> Result<(), SecurityError> {
    verify_parent_death_with(&linux::LinuxControls, expected_parent_process_id)
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn verify_parent_death(_expected_parent_process_id: u32) -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn drop_privileges(identity: ServiceIdentity) -> Result<(), SecurityError> {
    linux::drop_privileges(identity)
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn drop_privileges(_identity: ServiceIdentity) -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn verify_privileges(identity: ServiceIdentity) -> Result<(), SecurityError> {
    linux::verify_privileges(identity)
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn verify_privileges(_identity: ServiceIdentity) -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn deny_outbound_network() -> Result<(), SecurityError> {
    linux::deny_outbound_network()
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn deny_outbound_network() -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
pub(crate) fn verify_outbound_network_denial() -> Result<(), SecurityError> {
    linux::verify_outbound_network_denial()
}

#[cfg(not(all(
    target_os = "linux",
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
)))]
pub(crate) fn verify_outbound_network_denial() -> Result<(), SecurityError> {
    Err(SecurityError::UnsupportedPlatform)
}

#[cfg(test)]
mod tests {
    use std::{cell::RefCell, collections::VecDeque};

    use super::*;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum Call {
        SetCore,
        DisableDump,
        LockMemory,
        VerifyCore,
        VerifyDump,
        VerifyMemory,
    }

    struct InjectedControls {
        calls: RefCell<Vec<Call>>,
        fail_at: Option<Call>,
    }

    impl InjectedControls {
        fn passing() -> Self {
            Self {
                calls: RefCell::new(Vec::new()),
                fail_at: None,
            }
        }

        fn failing(fail_at: Call) -> Self {
            Self {
                calls: RefCell::new(Vec::new()),
                fail_at: Some(fail_at),
            }
        }

        fn result(&self, call: Call) -> bool {
            self.calls.borrow_mut().push(call);
            self.fail_at != Some(call)
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum ParentCall {
        ReadParent,
        SetSignal,
        VerifySignal,
    }

    struct InjectedParentControls {
        calls: RefCell<Vec<ParentCall>>,
        parent_process_ids: RefCell<VecDeque<Option<u32>>>,
        set_succeeds: bool,
        signal_is_set: bool,
    }

    impl InjectedParentControls {
        fn new(
            parent_process_ids: impl IntoIterator<Item = Option<u32>>,
            set_succeeds: bool,
            signal_is_set: bool,
        ) -> Self {
            Self {
                calls: RefCell::new(Vec::new()),
                parent_process_ids: RefCell::new(parent_process_ids.into_iter().collect()),
                set_succeeds,
                signal_is_set,
            }
        }
    }

    impl ParentDeathControls for InjectedParentControls {
        fn parent_process_id(&self) -> Option<u32> {
            self.calls.borrow_mut().push(ParentCall::ReadParent);
            self.parent_process_ids.borrow_mut().pop_front().flatten()
        }

        fn set_parent_death_signal(&self) -> bool {
            self.calls.borrow_mut().push(ParentCall::SetSignal);
            self.set_succeeds
        }

        fn parent_death_signal_is_set(&self) -> bool {
            self.calls.borrow_mut().push(ParentCall::VerifySignal);
            self.signal_is_set
        }
    }

    impl ProcessControls for InjectedControls {
        fn set_core_limit_zero(&self) -> bool {
            self.result(Call::SetCore)
        }

        fn disable_dumpability(&self) -> bool {
            self.result(Call::DisableDump)
        }

        fn lock_current_and_future(&self) -> bool {
            self.result(Call::LockMemory)
        }

        fn core_limit_is_zero(&self) -> bool {
            self.result(Call::VerifyCore)
        }

        fn dumpability_is_disabled(&self) -> bool {
            self.result(Call::VerifyDump)
        }

        fn current_and_future_are_locked(&self) -> bool {
            self.result(Call::VerifyMemory)
        }
    }

    #[test]
    fn establishment_applies_then_verifies_every_control() {
        let controls = InjectedControls::passing();

        establish_with(&controls).expect("FIXTURE_TECNICA controls");

        assert!(
            *controls.calls.borrow()
                == [
                    Call::SetCore,
                    Call::DisableDump,
                    Call::LockMemory,
                    Call::VerifyCore,
                    Call::VerifyDump,
                    Call::VerifyMemory,
                ]
        );
    }

    #[test]
    fn each_setup_failure_stops_closed() {
        let cases = [
            (Call::SetCore, SecurityError::CoreLimitSetup),
            (Call::DisableDump, SecurityError::DumpabilitySetup),
            (Call::LockMemory, SecurityError::AddressSpaceLockSetup),
        ];

        for (call, expected) in cases {
            let controls = InjectedControls::failing(call);
            assert!(establish_with(&controls) == Err(expected));
            assert!(controls.calls.borrow().last() == Some(&call));
        }
    }

    #[test]
    fn each_verification_failure_stops_closed() {
        let cases = [
            (Call::VerifyCore, SecurityError::CoreLimitVerification),
            (Call::VerifyDump, SecurityError::DumpabilityVerification),
            (
                Call::VerifyMemory,
                SecurityError::AddressSpaceLockVerification,
            ),
        ];

        for (call, expected) in cases {
            let controls = InjectedControls::failing(call);
            assert!(establish_with(&controls) == Err(expected));
            assert!(controls.calls.borrow().last() == Some(&call));
        }
    }

    #[test]
    fn pre_read_verification_does_not_repeat_setup() {
        let controls = InjectedControls::passing();

        verify_with(&controls).expect("FIXTURE_TECNICA verification");

        assert!(
            *controls.calls.borrow() == [Call::VerifyCore, Call::VerifyDump, Call::VerifyMemory]
        );
    }

    #[test]
    fn dumpability_restoration_reapplies_then_verifies() {
        let controls = InjectedControls::passing();

        restore_dumpability_with(&controls).expect("FIXTURE_TECNICA dumpability");

        assert!(*controls.calls.borrow() == [Call::DisableDump, Call::VerifyDump]);
    }

    #[test]
    fn each_dumpability_restoration_failure_stops_closed() {
        for (call, expected) in [
            (Call::DisableDump, SecurityError::DumpabilitySetup),
            (Call::VerifyDump, SecurityError::DumpabilityVerification),
        ] {
            let controls = InjectedControls::failing(call);
            assert_eq!(restore_dumpability_with(&controls), Err(expected));
            assert_eq!(controls.calls.borrow().last(), Some(&call));
        }
    }

    #[test]
    fn parent_death_binding_checks_parent_before_and_after_setup() {
        let controls = InjectedParentControls::new([Some(42), Some(42)], true, true);

        assert_eq!(
            bind_parent_death_with(&controls),
            Ok(42),
            "FIXTURE_TECNICA live parent"
        );
        assert_eq!(
            *controls.calls.borrow(),
            [
                ParentCall::ReadParent,
                ParentCall::SetSignal,
                ParentCall::ReadParent,
                ParentCall::VerifySignal,
            ]
        );
    }

    #[test]
    fn parent_death_binding_rejects_an_initial_or_racing_orphan() {
        for parent_process_ids in [
            VecDeque::from([Some(1)]),
            VecDeque::from([None]),
            VecDeque::from([Some(42), Some(1)]),
            VecDeque::from([Some(42), Some(43)]),
        ] {
            let controls = InjectedParentControls::new(parent_process_ids, true, true);
            assert_eq!(
                bind_parent_death_with(&controls),
                Err(SecurityError::ParentProcessUnavailable)
            );
        }
    }

    #[test]
    fn parent_death_binding_rejects_setup_and_verification_failures() {
        let setup_failure = InjectedParentControls::new([Some(42)], false, true);
        assert_eq!(
            bind_parent_death_with(&setup_failure),
            Err(SecurityError::ParentDeathSignalSetup)
        );
        assert_eq!(
            *setup_failure.calls.borrow(),
            [ParentCall::ReadParent, ParentCall::SetSignal]
        );

        let verification_failure = InjectedParentControls::new([Some(42), Some(42)], true, false);
        assert_eq!(
            bind_parent_death_with(&verification_failure),
            Err(SecurityError::ParentDeathSignalVerification)
        );
        assert_eq!(
            verification_failure.calls.borrow().last(),
            Some(&ParentCall::VerifySignal)
        );
    }
}
