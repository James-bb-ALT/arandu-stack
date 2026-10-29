use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fs::{self, OpenOptions},
    io::Write as _,
    path::{Path, PathBuf},
};

use nlu_data::pos::{
    PosCompileInput, PosEndpoint, PosModelIdentity, PosSourceIdentity, PosTrainingIdentity,
    PosTransition, compile_pos_transition_package, decode_pos_transition_package,
};
use serde::Serialize;

use crate::common::{
    CORPUS_VERSION, GENERATOR_ID, GENERATOR_SHA256, LOCALE, ORIGINAL_POS_SHA256, ORIGINS_SHA256,
    PosDataset, PosEvaluationError, PosEvaluationErrorCode, Result, SOURCE_ID, SOURCE_LICENSE,
    SOURCE_MANIFEST_SHA256, SPECIFICATION_SHA256, SPLIT_MANIFEST_PATH, TRAIN_CASE_IDS_SHA256,
    TRAIN_DOCUMENT_IDS_SHA256, TRAIN_PATH, TRAIN_SHA256, TRAIN_TEXT_SHA256S_SHA256,
    canonical_bytes, collection_digest, collection_digest_strings, load_split_manifest,
    map_data_error, parse_pos_slice, read_split_slice, sha256,
};

pub const MODEL_ID: &str = "p08-pos-transition-model-v1";
pub const MODEL_ALGORITHM_ID: &str = "adjacent-singleton-presence-noncascading-v1";
pub const MODEL_COMPILER_ID: &str = "nlu-data-pos-transition-compiler-v1";
pub const MODEL_CONFIG_ID: &str = "p08-pos-config-v1";

const PACKAGE_FILENAME: &str = "package.bin";
const MANIFEST_FILENAME: &str = "package-manifest.json";
const EXPECTED_LABEL_COUNTS: [(&str, u64); 5] = [
    ("ADP", 80),
    ("DET", 80),
    ("NOUN", 160),
    ("NUM", 160),
    ("VERB", 80),
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TrainingArtifacts {
    package: Vec<u8>,
    manifest: Vec<u8>,
}

impl TrainingArtifacts {
    #[must_use]
    pub fn package(&self) -> &[u8] {
        &self.package
    }

    #[must_use]
    pub fn manifest(&self) -> &[u8] {
        &self.manifest
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct TrainingSummary {
    schema_version: u32,
    operation: &'static str,
    model_id: &'static str,
    algorithm_id: &'static str,
    compiler_id: &'static str,
    config_id: &'static str,
    split_manifest_sha256: &'static str,
    train_sha256: &'static str,
    package_file: &'static str,
    package_bytes: u64,
    package_sha256: String,
    manifest_file: &'static str,
    manifest_bytes: u64,
    manifest_sha256: String,
}

impl TrainingSummary {
    #[must_use]
    pub fn package_sha256(&self) -> &str {
        &self.package_sha256
    }

    #[must_use]
    pub fn manifest_sha256(&self) -> &str {
        &self.manifest_sha256
    }
}

pub fn compile_training(
    root: &Path,
    split_manifest_relative_path: &str,
) -> Result<TrainingArtifacts> {
    let split_manifest = load_split_manifest(root, split_manifest_relative_path)?;
    let train_entry = split_manifest.entry("train")?;
    let train_bytes = read_split_slice(root, train_entry, TRAIN_PATH, TRAIN_SHA256)?;
    compile_verified_training_bytes(&train_bytes)
}

pub fn train_to_directory(
    root: &Path,
    split_manifest_relative_path: &str,
    output_directory: &Path,
) -> Result<TrainingSummary> {
    let artifacts = compile_training(root, split_manifest_relative_path)?;
    let summary = build_summary(&artifacts)?;
    write_artifacts(output_directory, &artifacts)?;
    Ok(summary)
}

pub fn train_cli<I, T>(arguments: I) -> Result<Vec<u8>>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let arguments = arguments.into_iter().map(Into::into).collect::<Vec<_>>();
    let (root, split_manifest, output) = parse_cli_arguments(&arguments)?;
    let summary = train_to_directory(&root, &split_manifest, &output)?;
    canonical_bytes(
        &summary,
        "P08 POS training summary",
        PosEvaluationErrorCode::OutputFailure,
    )
}

fn compile_verified_training_bytes(bytes: &[u8]) -> Result<TrainingArtifacts> {
    if sha256(bytes, PosEvaluationErrorCode::InvalidDataset)? != TRAIN_SHA256 {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "physical train slice SHA-256",
        ));
    }
    let dataset = parse_pos_slice(bytes, "train")?;
    validate_train_inventory(&dataset)?;
    compile_dataset(&dataset)
}

fn compile_dataset(dataset: &PosDataset) -> Result<TrainingArtifacts> {
    let labels = dataset.label_counts.keys().cloned().collect::<Vec<_>>();
    let mut transitions = BTreeSet::new();
    for row in dataset.rows.values() {
        let tags = row
            .tokens
            .iter()
            .map(|token| token.allowed_pos[0].clone())
            .collect::<Vec<_>>();
        let first = tags.first().ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidDataset,
                "empty training sentence",
            )
        })?;
        transitions.insert(PosTransition::new(
            PosEndpoint::Bos,
            PosEndpoint::label(first.clone()),
        ));
        for pair in tags.windows(2) {
            transitions.insert(PosTransition::new(
                PosEndpoint::label(pair[0].clone()),
                PosEndpoint::label(pair[1].clone()),
            ));
        }
        let last = tags.last().ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidDataset,
                "empty training sentence",
            )
        })?;
        transitions.insert(PosTransition::new(
            PosEndpoint::label(last.clone()),
            PosEndpoint::Eos,
        ));
    }

    let identity = model_identity();
    let input = PosCompileInput::new(
        identity.clone(),
        PosSourceIdentity::new(
            SOURCE_ID,
            CORPUS_VERSION,
            GENERATOR_ID,
            SOURCE_LICENSE,
            LOCALE,
            SOURCE_MANIFEST_SHA256,
            SPECIFICATION_SHA256,
            GENERATOR_SHA256,
            ORIGINAL_POS_SHA256,
        ),
        PosTrainingIdentity::new(
            TRAIN_SHA256,
            TRAIN_CASE_IDS_SHA256,
            TRAIN_DOCUMENT_IDS_SHA256,
            ORIGINS_SHA256,
            80,
            8,
            560,
        ),
        labels,
        transitions.into_iter().collect(),
    );
    let compiled = compile_pos_transition_package(&input)
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidDataset, error))?;
    decode_pos_transition_package(
        compiled.package_bytes(),
        compiled.manifest_bytes(),
        &identity,
    )
    .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidDataset, error))?;
    let (package, manifest) = compiled.into_parts();
    Ok(TrainingArtifacts { package, manifest })
}

fn validate_train_inventory(dataset: &PosDataset) -> Result<()> {
    let case_ids = dataset.rows.keys().map(String::as_str).collect::<Vec<_>>();
    let expected_counts = EXPECTED_LABEL_COUNTS
        .into_iter()
        .map(|(label, count)| (label.to_owned(), count))
        .collect::<BTreeMap<_, _>>();
    if dataset.rows.len() != 80
        || dataset.documents.len() != 8
        || dataset.token_count != 560
        || dataset.origins.len() != 1
        || dataset
            .families
            .iter()
            .map(String::as_str)
            .ne(["pos-command-train-v1"])
        || dataset.label_counts != expected_counts
        || dataset
            .rows
            .values()
            .any(|row| row.ambiguity_preserved.is_some())
        || collection_digest_strings(case_ids)? != TRAIN_CASE_IDS_SHA256
        || collection_digest(&dataset.documents)? != TRAIN_DOCUMENT_IDS_SHA256
        || collection_digest(&dataset.text_sha256s)? != TRAIN_TEXT_SHA256S_SHA256
        || collection_digest(&dataset.origins)? != ORIGINS_SHA256
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "train slice inventory",
        ));
    }
    Ok(())
}

fn model_identity() -> PosModelIdentity {
    PosModelIdentity::new(
        MODEL_ID,
        MODEL_ALGORITHM_ID,
        MODEL_COMPILER_ID,
        MODEL_CONFIG_ID,
    )
}

fn build_summary(artifacts: &TrainingArtifacts) -> Result<TrainingSummary> {
    Ok(TrainingSummary {
        schema_version: 1,
        operation: "train",
        model_id: MODEL_ID,
        algorithm_id: MODEL_ALGORITHM_ID,
        compiler_id: MODEL_COMPILER_ID,
        config_id: MODEL_CONFIG_ID,
        split_manifest_sha256: crate::common::SPLIT_MANIFEST_SHA256,
        train_sha256: TRAIN_SHA256,
        package_file: PACKAGE_FILENAME,
        package_bytes: u64::try_from(artifacts.package.len()).map_err(|_| {
            PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, "package byte count")
        })?,
        package_sha256: sha256(&artifacts.package, PosEvaluationErrorCode::OutputFailure)?,
        manifest_file: MANIFEST_FILENAME,
        manifest_bytes: u64::try_from(artifacts.manifest.len()).map_err(|_| {
            PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, "manifest byte count")
        })?,
        manifest_sha256: sha256(&artifacts.manifest, PosEvaluationErrorCode::OutputFailure)?,
    })
}

fn write_artifacts(output: &Path, artifacts: &TrainingArtifacts) -> Result<()> {
    let created_directory = match fs::symlink_metadata(output) {
        Ok(metadata) => {
            if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
                return Err(PosEvaluationError::new(
                    PosEvaluationErrorCode::InvalidArguments,
                    "output path must be a real directory",
                ));
            }
            if fs::read_dir(output)
                .map_err(|error| output_error("inspect output directory", error))?
                .next()
                .is_some()
            {
                return Err(PosEvaluationError::new(
                    PosEvaluationErrorCode::InvalidArguments,
                    "output directory must be empty",
                ));
            }
            false
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(output)
                .map_err(|error| output_error("create output directory", error))?;
            true
        }
        Err(error) => return Err(output_error("inspect output directory", error)),
    };

    let package_path = output.join(PACKAGE_FILENAME);
    let manifest_path = output.join(MANIFEST_FILENAME);
    let result = (|| {
        write_new_file(&package_path, &artifacts.package)?;
        write_new_file(&manifest_path, &artifacts.manifest)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&package_path);
        let _ = fs::remove_file(&manifest_path);
        if created_directory {
            let _ = fs::remove_dir(output);
        }
    }
    result
}

fn write_new_file(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| output_error("create artifact", error))?;
    file.write_all(bytes)
        .map_err(|error| output_error("write artifact", error))?;
    file.sync_all()
        .map_err(|error| output_error("sync artifact", error))
}

fn parse_cli_arguments(arguments: &[OsString]) -> Result<(PathBuf, String, PathBuf)> {
    if arguments.is_empty() {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "missing program name",
        ));
    }
    let mut root = None;
    let mut split_manifest = None;
    let mut output = None;
    let mut index = 1;
    while index < arguments.len() {
        let option = arguments[index].to_str().ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidArguments,
                "command-line option is not UTF-8",
            )
        })?;
        index += 1;
        let value = arguments.get(index).ok_or_else(|| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidArguments,
                format!("missing value for {option}"),
            )
        })?;
        index += 1;
        match option {
            "--root" if root.is_none() => root = Some(PathBuf::from(value)),
            "--split-manifest" if split_manifest.is_none() => {
                split_manifest = Some(
                    value
                        .to_str()
                        .ok_or_else(|| {
                            PosEvaluationError::new(
                                PosEvaluationErrorCode::InvalidArguments,
                                "split manifest path is not UTF-8",
                            )
                        })?
                        .to_owned(),
                );
            }
            "--output" if output.is_none() => output = Some(PathBuf::from(value)),
            "--root" | "--split-manifest" | "--output" => {
                return Err(PosEvaluationError::new(
                    PosEvaluationErrorCode::InvalidArguments,
                    format!("duplicate option {option}"),
                ));
            }
            _ => {
                return Err(PosEvaluationError::new(
                    PosEvaluationErrorCode::InvalidArguments,
                    format!("unknown option {option}"),
                ));
            }
        }
    }
    let root = root.ok_or_else(|| {
        PosEvaluationError::new(PosEvaluationErrorCode::InvalidArguments, "missing --root")
    })?;
    let split_manifest = split_manifest.ok_or_else(|| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "missing --split-manifest",
        )
    })?;
    if split_manifest != SPLIT_MANIFEST_PATH {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "split manifest path is not the frozen P08 path",
        ));
    }
    let output = output.ok_or_else(|| {
        PosEvaluationError::new(PosEvaluationErrorCode::InvalidArguments, "missing --output")
    })?;
    Ok((root, split_manifest, output))
}

fn output_error(context: &str, error: std::io::Error) -> PosEvaluationError {
    PosEvaluationError::new(
        PosEvaluationErrorCode::OutputFailure,
        format!("{context}: {}", error.kind()),
    )
}

#[cfg(test)]
mod tests {
    use super::{compile_dataset, compile_verified_training_bytes};
    use crate::common::{TRAIN_SHA256, parse_pos_slice};

    const TRAIN: &[u8] = include_bytes!("../../../data/evaluation/p08/pos-v1/splits/train.jsonl");

    #[test]
    fn frozen_training_compiles_and_rejects_substitution() {
        let artifacts = compile_verified_training_bytes(TRAIN).expect("compile frozen train");
        assert!(artifacts.package().starts_with(b"NLUPOS\0"));
        assert!(artifacts.manifest().ends_with(b"\n"));

        let mut changed = TRAIN.to_vec();
        changed[0] ^= 1;
        assert!(compile_verified_training_bytes(&changed).is_err());
        assert_eq!(
            nlu_data::sha256_hex(TRAIN).expect("train digest"),
            TRAIN_SHA256
        );
    }

    #[test]
    fn model_payload_is_invariant_to_record_order() {
        let original = parse_pos_slice(TRAIN, "train").expect("original rows");
        let mut lines = TRAIN
            .strip_suffix(b"\n")
            .expect("newline")
            .split(|byte| *byte == b'\n')
            .collect::<Vec<_>>();
        lines.reverse();
        let mut permuted = lines.join(&b'\n');
        permuted.push(b'\n');
        let permuted = parse_pos_slice(&permuted, "train").expect("permuted rows");

        let first = compile_dataset(&original).expect("first package");
        let second = compile_dataset(&permuted).expect("second package");
        assert_eq!(first, second);
    }
}
