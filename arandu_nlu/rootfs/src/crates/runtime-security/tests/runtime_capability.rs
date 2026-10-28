use std::process::ExitCode;

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
use std::{
    env,
    io::Write,
    process::{Command, Stdio},
};

use runtime_security::{GuardedProcess, ParentDeathGuard, SecurityError};

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
use runtime_security::CREDENTIAL_LENGTH;

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const FIXTURE_TECNICA_CHILD_ENV: &str = "RUNTIME_SECURITY_FIXTURE_TECNICA_CHILD";
#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const FIXTURE_TECNICA_EXACT_MODE: &str = "FIXTURE_TECNICA_EXACT";
#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const FIXTURE_TECNICA_SHORT_MODE: &str = "FIXTURE_TECNICA_SHORT";
#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const FIXTURE_TECNICA_TRAILING_MODE: &str = "FIXTURE_TECNICA_TRAILING";
#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const FIXTURE_TECNICA_CREDENTIAL: [u8; CREDENTIAL_LENGTH] = *b"FIXTURE_TECNICA_0123456789ABCDEF";

fn main() -> ExitCode {
    run().map_or_else(
        |message| {
            eprintln!("{message}");
            ExitCode::FAILURE
        },
        |()| ExitCode::SUCCESS,
    )
}

fn run() -> Result<(), &'static str> {
    #[cfg(not(all(target_os = "linux", target_pointer_width = "64")))]
    {
        if !matches!(
            GuardedProcess::harden(),
            Err(SecurityError::UnsupportedPlatform)
        ) {
            return Err("FIXTURE_TECNICA unsupported platform did not fail closed");
        }
        if !matches!(
            ParentDeathGuard::establish(),
            Err(SecurityError::UnsupportedPlatform)
        ) {
            return Err("FIXTURE_TECNICA unsupported parent binding did not fail closed");
        }
        Ok(())
    }

    #[cfg(all(target_os = "linux", target_pointer_width = "64"))]
    {
        if env::var_os(FIXTURE_TECNICA_CHILD_ENV).is_some() {
            return run_hardened_child();
        }
        run_parent()
    }
}

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
fn run_parent() -> Result<(), &'static str> {
    run_child_case(FIXTURE_TECNICA_EXACT_MODE, &FIXTURE_TECNICA_CREDENTIAL)?;
    run_child_case(
        FIXTURE_TECNICA_SHORT_MODE,
        &FIXTURE_TECNICA_CREDENTIAL[..CREDENTIAL_LENGTH - 1],
    )?;
    let mut trailing = FIXTURE_TECNICA_CREDENTIAL.to_vec();
    trailing.push(b'X');
    run_child_case(FIXTURE_TECNICA_TRAILING_MODE, &trailing)
}

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
fn run_child_case(mode: &'static str, input_bytes: &[u8]) -> Result<(), &'static str> {
    let executable =
        env::current_exe().map_err(|_| "FIXTURE_TECNICA current executable unavailable")?;
    let mut child = Command::new(executable)
        .env(FIXTURE_TECNICA_CHILD_ENV, mode)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| "FIXTURE_TECNICA child spawn failed")?;
    let mut input = child
        .stdin
        .take()
        .ok_or("FIXTURE_TECNICA child input unavailable")?;
    input
        .write_all(input_bytes)
        .map_err(|_| "FIXTURE_TECNICA child input write failed")?;
    drop(input);

    let output = child
        .wait_with_output()
        .map_err(|_| "FIXTURE_TECNICA child wait failed")?;
    if !output.status.success() {
        return Err("FIXTURE_TECNICA hardened child failed");
    }
    Ok(())
}

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
fn run_hardened_child() -> Result<(), &'static str> {
    let mode = env::var(FIXTURE_TECNICA_CHILD_ENV)
        .map_err(|_| "FIXTURE_TECNICA child mode unavailable")?;
    let parent_guard = ParentDeathGuard::establish().map_err(|error| match error {
        SecurityError::ParentProcessUnavailable => "FIXTURE_TECNICA parent unavailable",
        SecurityError::ParentDeathSignalSetup => "FIXTURE_TECNICA parent signal setup",
        SecurityError::ParentDeathSignalVerification => {
            "FIXTURE_TECNICA parent signal verification"
        }
        _ => "FIXTURE_TECNICA unexpected parent binding failure",
    })?;
    let guard = GuardedProcess::harden().map_err(|error| match error {
        SecurityError::CoreLimitSetup => "FIXTURE_TECNICA core setup",
        SecurityError::CoreLimitVerification => "FIXTURE_TECNICA core verification",
        SecurityError::DumpabilitySetup => "FIXTURE_TECNICA dump setup",
        SecurityError::DumpabilityVerification => "FIXTURE_TECNICA dump verification",
        SecurityError::AddressSpaceLockSetup => "FIXTURE_TECNICA memory setup",
        SecurityError::AddressSpaceLockVerification => "FIXTURE_TECNICA memory verification",
        _ => "FIXTURE_TECNICA unexpected hardening failure",
    })?;
    parent_guard
        .verify()
        .map_err(|_| "FIXTURE_TECNICA parent binding changed before credential read")?;
    let result = guard.read_credential_from_stdin();

    if mode == FIXTURE_TECNICA_SHORT_MODE {
        return matches!(result, Err(SecurityError::CredentialTooShort))
            .then_some(())
            .ok_or("FIXTURE_TECNICA short stdin was not rejected");
    }
    if mode == FIXTURE_TECNICA_TRAILING_MODE {
        return matches!(result, Err(SecurityError::CredentialTrailingData))
            .then_some(())
            .ok_or("FIXTURE_TECNICA trailing stdin was not rejected");
    }
    if mode != FIXTURE_TECNICA_EXACT_MODE {
        return Err("FIXTURE_TECNICA unknown child mode");
    }

    let mut credential = result.map_err(|_| "FIXTURE_TECNICA credential receipt failed")?;
    if credential.expose_secret() != Some(&FIXTURE_TECNICA_CREDENTIAL) {
        return Err("FIXTURE_TECNICA credential mismatch");
    }
    credential.destroy();
    if !credential.is_destroyed() || credential.expose_secret().is_some() {
        return Err("FIXTURE_TECNICA credential destruction failed");
    }
    Ok(())
}
