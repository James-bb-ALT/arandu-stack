#![forbid(unsafe_code)]

use std::{
    ffi::OsString,
    io::{self, Write},
    path::PathBuf,
};

use release_eval::{
    ArtifactSizeInput, BenchmarkIdentityInput, ReleaseEvalError, ReleaseEvalErrorCode,
    benchmark_with_identity, canonical_report, evaluate, semantic_preflight, validate_inputs,
};

const MAX_ARGUMENTS: usize = 128;

fn main() {
    if let Err(error) = run(std::env::args_os().skip(1).collect()) {
        eprintln!("RELEASE_EVAL_FAIL {}", error.code().code());
        std::process::exit(1);
    }
}

fn run(arguments: Vec<OsString>) -> Result<(), ReleaseEvalError> {
    if arguments.is_empty() || arguments.len() > MAX_ARGUMENTS {
        return Err(argument_error());
    }
    let command = arguments[0].to_str().ok_or_else(argument_error)?;
    let mut root = None;
    let mut artifacts = Vec::new();
    let mut identity = BenchmarkIdentityInput::default();
    let mut index = 1_usize;
    while index < arguments.len() {
        let flag = arguments[index].to_str().ok_or_else(argument_error)?;
        match flag {
            "--root" => {
                index += 1;
                let value = arguments.get(index).ok_or_else(argument_error)?;
                if root.replace(PathBuf::from(value)).is_some() {
                    return Err(argument_error());
                }
            }
            "--artifact-size" if command == "benchmark" => {
                index += 1;
                let value = arguments
                    .get(index)
                    .and_then(|value| value.to_str())
                    .ok_or_else(argument_error)?;
                artifacts.push(parse_artifact(value)?);
            }
            "--cpu-model" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.cpu_model,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--architecture" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.architecture,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--core-allocation" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.core_allocation,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--operating-system" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.operating_system,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--kernel" if command == "benchmark" => {
                set_identity_string(&mut identity.kernel, next_string(&arguments, &mut index)?)?;
            }
            "--compiler" if command == "benchmark" => {
                set_identity_string(&mut identity.compiler, next_string(&arguments, &mut index)?)?;
            }
            "--build-profile" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.build_profile,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--thread-count" if command == "benchmark" => {
                if identity.thread_count.is_some() {
                    return Err(argument_error());
                }
                identity.thread_count = Some(
                    next_string(&arguments, &mut index)?
                        .parse::<u32>()
                        .map_err(|_| argument_error())?,
                );
            }
            "--runner-executable-sha256" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.runner_executable_sha256,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--source-commit" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.source_commit,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            "--source-tree" if command == "benchmark" => {
                set_identity_string(
                    &mut identity.source_tree,
                    next_string(&arguments, &mut index)?,
                )?;
            }
            _ => return Err(argument_error()),
        }
        index += 1;
    }
    let root = root.ok_or_else(argument_error)?;
    let bytes = match command {
        "validate-inputs" if artifacts.is_empty() => canonical_report(&validate_inputs(&root)?)?,
        "evaluate" if artifacts.is_empty() => canonical_report(&evaluate(&root)?)?,
        "preflight" if artifacts.is_empty() => canonical_report(&semantic_preflight(&root)?)?,
        "benchmark" => canonical_report(&benchmark_with_identity(&root, &artifacts, &identity)?)?,
        _ => return Err(argument_error()),
    };
    io::stdout()
        .lock()
        .write_all(&bytes)
        .map_err(|_| ReleaseEvalError::new(ReleaseEvalErrorCode::Output, "stdout"))?;
    Ok(())
}

fn parse_artifact(value: &str) -> Result<ArtifactSizeInput, ReleaseEvalError> {
    let (label, bytes) = value.split_once('=').ok_or_else(argument_error)?;
    let bytes = bytes.parse::<u64>().map_err(|_| argument_error())?;
    Ok(ArtifactSizeInput {
        label: label.to_owned(),
        bytes,
    })
}

fn next_string<'a>(
    arguments: &'a [OsString],
    index: &mut usize,
) -> Result<&'a str, ReleaseEvalError> {
    *index += 1;
    arguments
        .get(*index)
        .and_then(|value| value.to_str())
        .ok_or_else(argument_error)
}

fn set_identity_string(
    destination: &mut Option<String>,
    value: &str,
) -> Result<(), ReleaseEvalError> {
    if destination.replace(value.to_owned()).is_some() {
        return Err(argument_error());
    }
    Ok(())
}

fn argument_error() -> ReleaseEvalError {
    ReleaseEvalError::new(ReleaseEvalErrorCode::InvalidArguments, "command line")
}
