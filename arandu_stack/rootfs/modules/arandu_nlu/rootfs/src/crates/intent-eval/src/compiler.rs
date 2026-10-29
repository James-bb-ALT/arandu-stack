use std::{
    ffi::OsString,
    fs::{self, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
};

use nlu_data::{DataError, MAX_RECORD_BYTES, compile_intent_package, read_bounded_root_file};

use crate::{IntentEvaluationError, IntentEvaluationErrorCode, error::Result};

pub fn compile_to_directory(root: &Path, schema_relative_path: &str, output: &Path) -> Result<()> {
    validate_relative_path(schema_relative_path)?;
    let schema = read_bounded_root_file(root, schema_relative_path, MAX_RECORD_BYTES)
        .map_err(map_data_error)?;
    let compiled = compile_intent_package(&schema).map_err(map_data_error)?;

    fs::create_dir(output).map_err(|error| output_error("create output directory", error))?;
    write_new_file(&output.join("package.bin"), compiled.package_bytes())?;
    write_new_file(
        &output.join("package-manifest.json"),
        compiled.manifest_bytes(),
    )?;
    Ok(())
}

pub fn compile_cli<I, T>(arguments: I) -> Result<Vec<u8>>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let arguments = arguments.into_iter().map(Into::into).collect::<Vec<_>>();
    let (root, schema, output) = parse_arguments(&arguments)?;
    compile_to_directory(&root, &schema, &output)?;
    Ok(b"INTENT_COMPILE_PASS\n".to_vec())
}

fn parse_arguments(arguments: &[OsString]) -> Result<(PathBuf, String, PathBuf)> {
    let mut root = None;
    let mut schema = None;
    let mut output = None;
    let mut index = 1_usize;
    while index < arguments.len() {
        let option = arguments[index]
            .to_str()
            .ok_or_else(|| invalid_arguments("non-UTF-8 option"))?;
        index = index
            .checked_add(1)
            .ok_or_else(|| invalid_arguments("argument index"))?;
        let value = arguments
            .get(index)
            .ok_or_else(|| invalid_arguments(format!("missing value for {option}")))?;
        match option {
            "--root" if root.is_none() => root = Some(PathBuf::from(value)),
            "--schema" if schema.is_none() => {
                schema = Some(
                    value
                        .to_str()
                        .ok_or_else(|| invalid_arguments("non-UTF-8 schema path"))?
                        .to_owned(),
                );
            }
            "--output" if output.is_none() => output = Some(PathBuf::from(value)),
            "--root" | "--schema" | "--output" => {
                return Err(invalid_arguments(format!("duplicate option {option}")));
            }
            _ => return Err(invalid_arguments(format!("unknown option {option}"))),
        }
        index = index
            .checked_add(1)
            .ok_or_else(|| invalid_arguments("argument index"))?;
    }
    Ok((
        root.ok_or_else(|| invalid_arguments("missing --root"))?,
        schema.ok_or_else(|| invalid_arguments("missing --schema"))?,
        output.ok_or_else(|| invalid_arguments("missing --output"))?,
    ))
}

fn validate_relative_path(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 256
        || Path::new(value)
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(invalid_arguments("invalid relative schema path"));
    }
    Ok(())
}

fn write_new_file(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| output_error("create output file", error))?;
    file.write_all(bytes)
        .map_err(|error| output_error("write output file", error))?;
    file.sync_all()
        .map_err(|error| output_error("sync output file", error))
}

fn map_data_error(error: DataError) -> IntentEvaluationError {
    IntentEvaluationError::new(
        IntentEvaluationErrorCode::InvalidSchema,
        format!("{:?}", error.code()),
    )
}

fn invalid_arguments(context: impl Into<String>) -> IntentEvaluationError {
    IntentEvaluationError::new(IntentEvaluationErrorCode::InvalidArguments, context)
}

fn output_error(context: &str, error: std::io::Error) -> IntentEvaluationError {
    IntentEvaluationError::new(
        IntentEvaluationErrorCode::OutputFailure,
        format!("{context}: {:?}", error.kind()),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_rejects_missing_duplicate_and_unknown_options() {
        assert_eq!(
            compile_cli(["intent-compile"]).expect_err("missing").code(),
            IntentEvaluationErrorCode::InvalidArguments
        );
        assert_eq!(
            compile_cli([
                "intent-compile",
                "--root",
                ".",
                "--root",
                ".",
                "--schema",
                "schema.json",
                "--output",
                "out",
            ])
            .expect_err("duplicate")
            .code(),
            IntentEvaluationErrorCode::InvalidArguments
        );
        assert_eq!(
            compile_cli(["intent-compile", "--unknown", "x"])
                .expect_err("unknown")
                .code(),
            IntentEvaluationErrorCode::InvalidArguments
        );
    }
}
