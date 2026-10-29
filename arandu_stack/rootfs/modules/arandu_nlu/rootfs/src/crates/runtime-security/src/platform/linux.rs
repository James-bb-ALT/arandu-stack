use std::{
    ffi::{c_int, c_ulong, c_void},
    fs,
    io::ErrorKind,
    net::TcpStream,
    ptr,
};

use crate::{SecurityError, ServiceIdentity};

use super::{ParentDeathControls, ProcessControls, procfs::smaps_reports_locked};

const RLIMIT_CORE: c_int = 4;
const PR_SET_PDEATHSIG: c_int = 1;
const PR_GET_PDEATHSIG: c_int = 2;
const PR_GET_DUMPABLE: c_int = 3;
const PR_SET_DUMPABLE: c_int = 4;
const SIGKILL: c_int = 9;
const MCL_CURRENT: c_int = 1;
const MCL_FUTURE: c_int = 2;
const PROT_READ: c_int = 1;
const PROT_WRITE: c_int = 2;
const MAP_PRIVATE: c_int = 2;
const MAP_ANONYMOUS: c_int = 0x20;
const PR_GET_SECCOMP: c_int = 21;
const PR_SET_SECCOMP: c_int = 22;
const PR_SET_NO_NEW_PRIVS: c_int = 38;
const PR_GET_NO_NEW_PRIVS: c_int = 39;
const SECCOMP_MODE_FILTER: c_ulong = 2;
const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
const SECCOMP_RET_ERRNO: u32 = 0x0005_0000;
const SECCOMP_RET_ALLOW: u32 = 0x7fff_0000;
const EACCES: u32 = 13;
const BPF_LD_W_ABS: u16 = 0x20;
const BPF_JMP_JEQ_K: u16 = 0x15;
const BPF_RET_K: u16 = 0x06;

#[cfg(target_arch = "x86_64")]
const AUDIT_ARCH: u32 = 0xc000_003e;
#[cfg(target_arch = "x86_64")]
const SYS_SOCKET: u32 = 41;
#[cfg(target_arch = "x86_64")]
const SYS_CONNECT: u32 = 42;
#[cfg(target_arch = "x86_64")]
const SYS_SENDTO: u32 = 44;
#[cfg(target_arch = "x86_64")]
const SYS_SENDMSG: u32 = 46;
#[cfg(target_arch = "x86_64")]
const SYS_SENDMMSG: u32 = 307;

#[cfg(target_arch = "aarch64")]
const AUDIT_ARCH: u32 = 0xc000_00b7;
#[cfg(target_arch = "aarch64")]
const SYS_SOCKET: u32 = 198;
#[cfg(target_arch = "aarch64")]
const SYS_CONNECT: u32 = 203;
#[cfg(target_arch = "aarch64")]
const SYS_SENDTO: u32 = 206;
#[cfg(target_arch = "aarch64")]
const SYS_SENDMSG: u32 = 211;
#[cfg(target_arch = "aarch64")]
const SYS_SENDMMSG: u32 = 269;

#[repr(C)]
struct RLimit {
    current: c_ulong,
    maximum: c_ulong,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct SockFilter {
    code: u16,
    jump_true: u8,
    jump_false: u8,
    value: u32,
}

#[repr(C)]
struct SockFilterProgram {
    length: u16,
    filters: *const SockFilter,
}

unsafe extern "C" {
    fn getppid() -> c_int;
    fn getrlimit(resource: c_int, limit: *mut RLimit) -> c_int;
    fn setrlimit(resource: c_int, limit: *const RLimit) -> c_int;
    fn prctl(option: c_int, ...) -> c_int;
    fn mlockall(flags: c_int) -> c_int;
    fn getpagesize() -> c_int;
    fn mmap(
        address: *mut c_void,
        length: usize,
        protection: c_int,
        flags: c_int,
        descriptor: c_int,
        offset: i64,
    ) -> *mut c_void;
    fn munmap(address: *mut c_void, length: usize) -> c_int;
    fn setgroups(count: usize, groups: *const u32) -> c_int;
    fn getgroups(count: c_int, groups: *mut u32) -> c_int;
    fn setresgid(real: u32, effective: u32, saved: u32) -> c_int;
    fn getresgid(real: *mut u32, effective: *mut u32, saved: *mut u32) -> c_int;
    fn setresuid(real: u32, effective: u32, saved: u32) -> c_int;
    fn getresuid(real: *mut u32, effective: *mut u32, saved: *mut u32) -> c_int;
}

pub(super) struct LinuxControls;

impl ProcessControls for LinuxControls {
    fn set_core_limit_zero(&self) -> bool {
        let limit = RLimit {
            current: 0,
            maximum: 0,
        };
        // SAFETY: `limit` is a valid immutable `rlimit` object for this call.
        unsafe { setrlimit(RLIMIT_CORE, &raw const limit) == 0 }
    }

    fn disable_dumpability(&self) -> bool {
        let zero: c_ulong = 0;
        // SAFETY: `prctl` accepts these scalar arguments for
        // `PR_SET_DUMPABLE`; all unused variadic slots are zero.
        unsafe { prctl(PR_SET_DUMPABLE, zero, zero, zero, zero) == 0 }
    }

    fn lock_current_and_future(&self) -> bool {
        // SAFETY: `mlockall` takes only the documented scalar flag mask.
        unsafe { mlockall(MCL_CURRENT | MCL_FUTURE) == 0 }
    }

    fn core_limit_is_zero(&self) -> bool {
        let mut limit = RLimit {
            current: c_ulong::MAX,
            maximum: c_ulong::MAX,
        };
        // SAFETY: `limit` is writable and valid for the duration of the call.
        let result = unsafe { getrlimit(RLIMIT_CORE, &raw mut limit) };
        result == 0 && limit.current == 0 && limit.maximum == 0
    }

    fn dumpability_is_disabled(&self) -> bool {
        let zero: c_ulong = 0;
        // SAFETY: `prctl` accepts these scalar arguments for
        // `PR_GET_DUMPABLE`; all unused variadic slots are zero.
        unsafe { prctl(PR_GET_DUMPABLE, zero, zero, zero, zero) == 0 }
    }

    fn current_and_future_are_locked(&self) -> bool {
        status_reports_locked_memory() && locked_current_and_future_mapping_probe()
    }
}

impl ParentDeathControls for LinuxControls {
    fn parent_process_id(&self) -> Option<u32> {
        // SAFETY: `getppid` has no arguments or preconditions.
        u32::try_from(unsafe { getppid() })
            .ok()
            .filter(|process_id| *process_id > 0)
    }

    fn set_parent_death_signal(&self) -> bool {
        let signal = SIGKILL as c_ulong;
        let zero: c_ulong = 0;
        // SAFETY: `PR_SET_PDEATHSIG` accepts one scalar signal number and
        // zeroed unused variadic slots.
        unsafe { prctl(PR_SET_PDEATHSIG, signal, zero, zero, zero) == 0 }
    }

    fn parent_death_signal_is_set(&self) -> bool {
        let mut signal: c_int = 0;
        let zero: c_ulong = 0;
        // SAFETY: `signal` is writable for the getter and all unused variadic
        // slots are zero.
        unsafe {
            prctl(PR_GET_PDEATHSIG, &raw mut signal, zero, zero, zero) == 0 && signal == SIGKILL
        }
    }
}

pub(super) fn drop_privileges(identity: ServiceIdentity) -> Result<(), SecurityError> {
    if !set_no_new_privileges() {
        return Err(SecurityError::NoNewPrivilegesSetup);
    }
    let ipc_gid = identity.ipc_gid();
    // SAFETY: one initialized group ID is readable for the supplied count.
    if unsafe { setgroups(1, &raw const ipc_gid) } != 0 {
        return Err(SecurityError::SupplementaryGroupsSetup);
    }
    // SAFETY: scalar group IDs have no pointer or lifetime preconditions.
    if unsafe {
        setresgid(
            identity.primary_gid(),
            identity.primary_gid(),
            identity.primary_gid(),
        )
    } != 0
    {
        return Err(SecurityError::GroupDropSetup);
    }
    super::restore_dumpability_with(&LinuxControls)?;
    // SAFETY: scalar user IDs have no pointer or lifetime preconditions.
    if unsafe { setresuid(identity.uid(), identity.uid(), identity.uid()) } != 0 {
        return Err(SecurityError::UserDropSetup);
    }
    super::restore_dumpability_with(&LinuxControls)?;
    verify_privileges(identity)
}

pub(super) fn verify_privileges(identity: ServiceIdentity) -> Result<(), SecurityError> {
    if !no_new_privileges_enabled() {
        return Err(SecurityError::NoNewPrivilegesVerification);
    }
    super::verify_dumpability_with(&LinuxControls)?;

    let mut real_gid = u32::MAX;
    let mut effective_gid = u32::MAX;
    let mut saved_gid = u32::MAX;
    // SAFETY: all three initialized output values are writable for this call.
    if unsafe {
        getresgid(
            &raw mut real_gid,
            &raw mut effective_gid,
            &raw mut saved_gid,
        )
    } != 0
        || [real_gid, effective_gid, saved_gid]
            != [
                identity.primary_gid(),
                identity.primary_gid(),
                identity.primary_gid(),
            ]
    {
        return Err(SecurityError::GroupDropVerification);
    }

    let mut real_uid = u32::MAX;
    let mut effective_uid = u32::MAX;
    let mut saved_uid = u32::MAX;
    // SAFETY: all three initialized output values are writable for this call.
    if unsafe {
        getresuid(
            &raw mut real_uid,
            &raw mut effective_uid,
            &raw mut saved_uid,
        )
    } != 0
        || [real_uid, effective_uid, saved_uid] != [identity.uid(), identity.uid(), identity.uid()]
    {
        return Err(SecurityError::UserDropVerification);
    }

    let mut group = u32::MAX;
    // SAFETY: `group` is one writable group-ID slot matching the count.
    if unsafe { getgroups(1, &raw mut group) } != 1 || group != identity.ipc_gid() {
        return Err(SecurityError::SupplementaryGroupsVerification);
    }
    if effective_capabilities().is_none_or(|capabilities| capabilities != 0) {
        return Err(SecurityError::CapabilityVerification);
    }
    Ok(())
}

pub(super) fn deny_outbound_network() -> Result<(), SecurityError> {
    if !set_no_new_privileges() {
        return Err(SecurityError::NoNewPrivilegesSetup);
    }
    let filters = outbound_network_filters();
    let program = SockFilterProgram {
        length: u16::try_from(filters.len())
            .map_err(|_| SecurityError::OutboundNetworkFilterSetup)?,
        filters: filters.as_ptr(),
    };
    let zero: c_ulong = 0;
    // SAFETY: `program` and its fixed filter array remain live and immutable
    // for the entire `prctl` call. All unused variadic slots are zero.
    if unsafe {
        prctl(
            PR_SET_SECCOMP,
            SECCOMP_MODE_FILTER,
            &raw const program,
            zero,
            zero,
        )
    } != 0
    {
        return Err(SecurityError::OutboundNetworkFilterSetup);
    }
    verify_outbound_network_denial()
}

pub(super) fn verify_outbound_network_denial() -> Result<(), SecurityError> {
    let zero: c_ulong = 0;
    // SAFETY: both getter operations use scalar arguments only.
    let no_new_privileges = unsafe { prctl(PR_GET_NO_NEW_PRIVS, zero, zero, zero, zero) };
    // SAFETY: both getter operations use scalar arguments only.
    let seccomp_mode = unsafe { prctl(PR_GET_SECCOMP, zero, zero, zero, zero) };
    if no_new_privileges != 1 || seccomp_mode != SECCOMP_MODE_FILTER as c_int {
        return Err(SecurityError::OutboundNetworkFilterVerification);
    }
    if TcpStream::connect(("127.0.0.1", 9))
        .expect_err("seccomp must deny socket creation before connection")
        .kind()
        != ErrorKind::PermissionDenied
    {
        return Err(SecurityError::OutboundNetworkFilterVerification);
    }
    Ok(())
}

fn set_no_new_privileges() -> bool {
    let one: c_ulong = 1;
    let zero: c_ulong = 0;
    // SAFETY: `PR_SET_NO_NEW_PRIVS` accepts one scalar flag and zeroed unused
    // variadic slots.
    unsafe { prctl(PR_SET_NO_NEW_PRIVS, one, zero, zero, zero) == 0 }
}

fn no_new_privileges_enabled() -> bool {
    let zero: c_ulong = 0;
    // SAFETY: `PR_GET_NO_NEW_PRIVS` accepts only zeroed scalar arguments.
    unsafe { prctl(PR_GET_NO_NEW_PRIVS, zero, zero, zero, zero) == 1 }
}

fn effective_capabilities() -> Option<u64> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    status.lines().find_map(|line| {
        line.strip_prefix("CapEff:")
            .and_then(|value| u64::from_str_radix(value.trim(), 16).ok())
    })
}

const fn statement(code: u16, value: u32) -> SockFilter {
    SockFilter {
        code,
        jump_true: 0,
        jump_false: 0,
        value,
    }
}

const fn jump(value: u32, jump_true: u8, jump_false: u8) -> SockFilter {
    SockFilter {
        code: BPF_JMP_JEQ_K,
        jump_true,
        jump_false,
        value,
    }
}

fn outbound_network_filters() -> [SockFilter; 11] {
    [
        statement(BPF_LD_W_ABS, 4),
        jump(AUDIT_ARCH, 1, 0),
        statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS),
        statement(BPF_LD_W_ABS, 0),
        jump(SYS_SOCKET, 4, 0),
        jump(SYS_CONNECT, 3, 0),
        jump(SYS_SENDTO, 2, 0),
        jump(SYS_SENDMSG, 1, 0),
        jump(SYS_SENDMMSG, 0, 1),
        statement(BPF_RET_K, SECCOMP_RET_ERRNO | EACCES),
        statement(BPF_RET_K, SECCOMP_RET_ALLOW),
    ]
}

fn status_reports_locked_memory() -> bool {
    let Ok(status) = fs::read_to_string("/proc/self/status") else {
        return false;
    };
    status.lines().any(|line| {
        let mut fields = line.split_ascii_whitespace();
        fields.next() == Some("VmLck:")
            && fields
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .is_some_and(|value| value > 0)
            && fields.next() == Some("kB")
            && fields.next().is_none()
    })
}

fn locked_current_and_future_mapping_probe() -> bool {
    let stack_marker = 0_u8;
    let stack_address = (&raw const stack_marker) as usize;
    let Some(future_probe) = MappedProbe::new() else {
        return false;
    };
    let Ok(smaps) = fs::read_to_string("/proc/self/smaps") else {
        return false;
    };
    smaps_reports_locked(&smaps, stack_address, 1)
        && smaps_reports_locked(&smaps, future_probe.address as usize, future_probe.length)
}

struct MappedProbe {
    address: *mut c_void,
    length: usize,
}

impl MappedProbe {
    fn new() -> Option<Self> {
        // SAFETY: `getpagesize` has no arguments or preconditions.
        let page_size = unsafe { getpagesize() };
        let length = usize::try_from(page_size).ok().filter(|value| *value > 0)?;
        // SAFETY: this requests a new anonymous private mapping with no input
        // pointer or descriptor. `length` is a positive kernel page size.
        let address = unsafe {
            mmap(
                ptr::null_mut(),
                length,
                PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        if address as isize == -1 {
            return None;
        }
        if address.is_null() {
            // SAFETY: a non-`MAP_FAILED` result owns this exact mapping.
            let _ = unsafe { munmap(address, length) };
            return None;
        }
        // SAFETY: a successful writable mapping spans at least one byte.
        unsafe { ptr::write_volatile(address.cast::<u8>(), 0) };
        Some(Self { address, length })
    }
}

impl Drop for MappedProbe {
    fn drop(&mut self) {
        // SAFETY: this exact mapping is live and uniquely owned until drop.
        let _ = unsafe { munmap(self.address, self.length) };
    }
}
