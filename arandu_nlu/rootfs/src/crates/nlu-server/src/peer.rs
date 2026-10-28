use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::Path;

use crate::{PeerIdentity, Result, ServerError, ServerErrorCode};

pub(crate) fn current_identity() -> Result<PeerIdentity> {
    platform::current_identity()
}

pub(crate) fn stream_identity(stream: &UnixStream) -> Result<PeerIdentity> {
    platform::stream_identity(stream.as_raw_fd())
}

pub(crate) fn set_socket_group(path: &Path, gid: u32) -> Result<()> {
    platform::set_socket_group(path, gid)
}

#[cfg(target_os = "linux")]
mod platform {
    use core::ffi::{c_int, c_void};
    use std::ffi::CString;
    use std::mem::{size_of, zeroed};
    use std::os::unix::ffi::OsStrExt;

    use super::*;

    const SOL_SOCKET: c_int = 1;
    const SO_PEERCRED: c_int = 17;

    #[repr(C)]
    struct UCred {
        pid: c_int,
        uid: u32,
        gid: u32,
    }

    unsafe extern "C" {
        fn chown(path: *const i8, owner: u32, group: u32) -> c_int;
        fn geteuid() -> u32;
        fn getegid() -> u32;
        fn getsockopt(
            socket: c_int,
            level: c_int,
            option: c_int,
            value: *mut c_void,
            length: *mut u32,
        ) -> c_int;
    }

    pub(super) fn current_identity() -> Result<PeerIdentity> {
        // SAFETY: these process credential functions take no pointers and have
        // no preconditions.
        Ok(PeerIdentity::new(unsafe { geteuid() }, unsafe {
            getegid()
        }))
    }

    pub(super) fn stream_identity(socket: RawFd) -> Result<PeerIdentity> {
        // SAFETY: `credential` and `length` are initialized writable objects,
        // their lifetimes cover this call, and `socket` comes from a live
        // `UnixStream`. The kernel writes at most the supplied structure size.
        let mut credential: UCred = unsafe { zeroed() };
        let mut length = u32::try_from(size_of::<UCred>())
            .map_err(|_| ServerError::new(ServerErrorCode::PeerCredentials))?;
        let result = unsafe {
            getsockopt(
                socket,
                SOL_SOCKET,
                SO_PEERCRED,
                (&raw mut credential).cast::<c_void>(),
                &raw mut length,
            )
        };
        if result != 0 || usize::try_from(length).ok() != Some(size_of::<UCred>()) {
            return Err(ServerError::new(ServerErrorCode::PeerCredentials));
        }
        Ok(PeerIdentity::new(credential.uid, credential.gid))
    }

    pub(super) fn set_socket_group(path: &Path, gid: u32) -> Result<()> {
        let path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        // SAFETY: `path` is a live NUL-terminated path and `u32::MAX` is the
        // POSIX sentinel that leaves the owner unchanged.
        if unsafe { chown(path.as_ptr(), u32::MAX, gid) } != 0 {
            return Err(ServerError::new(ServerErrorCode::SocketConfiguration));
        }
        Ok(())
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use core::ffi::c_int;
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    use super::*;

    unsafe extern "C" {
        fn chown(path: *const i8, owner: u32, group: u32) -> c_int;
        fn geteuid() -> u32;
        fn getegid() -> u32;
        fn getpeereid(socket: c_int, uid: *mut u32, gid: *mut u32) -> c_int;
    }

    pub(super) fn current_identity() -> Result<PeerIdentity> {
        // SAFETY: these process credential functions take no pointers and have
        // no preconditions.
        Ok(PeerIdentity::new(unsafe { geteuid() }, unsafe {
            getegid()
        }))
    }

    pub(super) fn stream_identity(socket: RawFd) -> Result<PeerIdentity> {
        let mut uid = 0_u32;
        let mut gid = 0_u32;
        // SAFETY: both output pointers reference initialized writable values
        // for the duration of the call, and `socket` is a live Unix stream.
        if unsafe { getpeereid(socket, &raw mut uid, &raw mut gid) } != 0 {
            return Err(ServerError::new(ServerErrorCode::PeerCredentials));
        }
        Ok(PeerIdentity::new(uid, gid))
    }

    pub(super) fn set_socket_group(path: &Path, gid: u32) -> Result<()> {
        let path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        // SAFETY: `path` is a live NUL-terminated path and `u32::MAX` is the
        // POSIX sentinel that leaves the owner unchanged.
        if unsafe { chown(path.as_ptr(), u32::MAX, gid) } != 0 {
            return Err(ServerError::new(ServerErrorCode::SocketConfiguration));
        }
        Ok(())
    }
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
mod platform {
    use super::*;

    pub(super) fn current_identity() -> Result<PeerIdentity> {
        Err(ServerError::new(ServerErrorCode::PeerCredentials))
    }

    pub(super) fn stream_identity(_socket: RawFd) -> Result<PeerIdentity> {
        Err(ServerError::new(ServerErrorCode::PeerCredentials))
    }

    pub(super) fn set_socket_group(_path: &Path, _gid: u32) -> Result<()> {
        Err(ServerError::new(ServerErrorCode::SocketConfiguration))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_pair_reports_the_kernel_peer_identity() {
        let (left, right) = UnixStream::pair().expect("FIXTURE_TECNICA Unix pair");
        let current = PeerIdentity::current().expect("FIXTURE_TECNICA current identity");
        assert_eq!(
            PeerIdentity::from_unix_stream(&left).expect("FIXTURE_TECNICA left peer"),
            current
        );
        assert_eq!(
            PeerIdentity::from_unix_stream(&right).expect("FIXTURE_TECNICA right peer"),
            current
        );
    }
}
