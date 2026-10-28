use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    path::Path,
};

use crate::{
    DataError, DataErrorCode, Result, compile, compile_lexicon, fetch, import, normalize,
    remove_lexicon_source, remove_source, split, validate_stage, verify,
};

pub fn run(arguments: impl IntoIterator<Item = OsString>) -> Result<&'static str> {
    let mut arguments = arguments.into_iter();
    let command = arguments
        .next()
        .ok_or_else(|| DataError::new(DataErrorCode::InvalidArguments, "missing command"))?
        .into_string()
        .map_err(|_| DataError::new(DataErrorCode::InvalidArguments, "non-UTF-8 command"))?;
    let options = Options::parse(arguments)?;
    match command.as_str() {
        "fetch" => {
            options.exact(&["manifest", "root", "output"], &["allow-fetch"])?;
            fetch(
                Path::new(options.value("manifest")?),
                Path::new(options.value("root")?),
                Path::new(options.value("output")?),
                options.flag("allow-fetch"),
            )?;
            Ok("NLU_DATA_FETCH_PASS")
        }
        "verify" => {
            options.exact(&["manifest", "root"], &[])?;
            verify(
                Path::new(options.value("manifest")?),
                Path::new(options.value("root")?),
            )?;
            Ok("NLU_DATA_VERIFY_PASS")
        }
        "import" => {
            options.exact(&["manifest", "root", "output"], &[])?;
            import(
                Path::new(options.value("manifest")?),
                Path::new(options.value("root")?),
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_IMPORT_PASS")
        }
        "normalize" => {
            options.exact(&["input", "output"], &[])?;
            normalize(
                Path::new(options.value("input")?),
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_NORMALIZE_PASS")
        }
        "validate" => {
            options.exact(&["input"], &[])?;
            validate_stage(Path::new(options.value("input")?))?;
            Ok("NLU_DATA_VALIDATE_PASS")
        }
        "split" => {
            options.exact(&["input", "output"], &[])?;
            split(
                Path::new(options.value("input")?),
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_SPLIT_PASS")
        }
        "compile" => {
            options.exact(&["input", "output"], &[])?;
            compile(
                Path::new(options.value("input")?),
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_COMPILE_PASS")
        }
        "remove-source" => {
            options.exact(&["input", "source-id", "output"], &[])?;
            remove_source(
                Path::new(options.value("input")?),
                options.value("source-id")?,
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_REMOVE_SOURCE_PASS")
        }
        "compile-lexicon" => {
            options.exact(&["manifest", "root", "output"], &[])?;
            compile_lexicon(
                Path::new(options.value("manifest")?),
                Path::new(options.value("root")?),
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_COMPILE_LEXICON_PASS")
        }
        "remove-lexicon-source" => {
            options.exact(&["input", "source-id", "output"], &[])?;
            remove_lexicon_source(
                Path::new(options.value("input")?),
                options.value("source-id")?,
                Path::new(options.value("output")?),
            )?;
            Ok("NLU_DATA_REMOVE_LEXICON_SOURCE_PASS")
        }
        _ => Err(DataError::new(
            DataErrorCode::InvalidArguments,
            "unknown command",
        )),
    }
}

struct Options {
    values: BTreeMap<String, String>,
    flags: BTreeSet<String>,
}

impl Options {
    fn parse(arguments: impl IntoIterator<Item = OsString>) -> Result<Self> {
        let arguments = arguments
            .into_iter()
            .map(|argument| {
                argument.into_string().map_err(|_| {
                    DataError::new(DataErrorCode::InvalidArguments, "non-UTF-8 argument")
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let mut values = BTreeMap::new();
        let mut flags = BTreeSet::new();
        let mut index = 0;
        while index < arguments.len() {
            let option = arguments[index].strip_prefix("--").ok_or_else(|| {
                DataError::new(DataErrorCode::InvalidArguments, "expected named option")
            })?;
            if option.is_empty() || option.contains('=') {
                return Err(DataError::new(
                    DataErrorCode::InvalidArguments,
                    "invalid option syntax",
                ));
            }
            if option == "allow-fetch" {
                if !flags.insert(option.to_owned()) || values.contains_key(option) {
                    return Err(DataError::new(
                        DataErrorCode::InvalidArguments,
                        "duplicate option",
                    ));
                }
                index += 1;
                continue;
            }
            let value = arguments.get(index + 1).ok_or_else(|| {
                DataError::new(DataErrorCode::InvalidArguments, "missing option value")
            })?;
            if value.starts_with("--")
                || values.insert(option.to_owned(), value.clone()).is_some()
                || flags.contains(option)
            {
                return Err(DataError::new(
                    DataErrorCode::InvalidArguments,
                    "duplicate or missing option value",
                ));
            }
            index += 2;
        }
        Ok(Self { values, flags })
    }

    fn exact(&self, values: &[&str], flags: &[&str]) -> Result<()> {
        let expected_values = values.iter().copied().collect::<BTreeSet<_>>();
        let expected_flags = flags.iter().copied().collect::<BTreeSet<_>>();
        if self
            .values
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>()
            != expected_values
            || self
                .flags
                .iter()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
                != expected_flags
        {
            return Err(DataError::new(
                DataErrorCode::InvalidArguments,
                "command option set differs",
            ));
        }
        Ok(())
    }

    fn value(&self, name: &str) -> Result<&str> {
        self.values.get(name).map(String::as_str).ok_or_else(|| {
            DataError::new(DataErrorCode::InvalidArguments, format!("missing --{name}"))
        })
    }

    fn flag(&self, name: &str) -> bool {
        self.flags.contains(name)
    }
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;

    use super::run;
    use crate::DataErrorCode;

    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn fetch_requires_the_explicit_flag() {
        let error = run(args(&[
            "fetch",
            "--manifest",
            "manifest",
            "--root",
            "root",
            "--output",
            "output",
        ]))
        .expect_err("missing flag");
        assert_eq!(error.code(), DataErrorCode::InvalidArguments);
    }

    #[test]
    fn rejects_duplicate_and_unknown_options() {
        assert!(
            run(args(&[
                "verify",
                "--manifest",
                "one",
                "--manifest",
                "two",
                "--root",
                "root",
            ]))
            .is_err()
        );
        assert!(
            run(args(&[
                "validate",
                "--input",
                "stage",
                "--unknown",
                "value",
            ]))
            .is_err()
        );
    }
}
