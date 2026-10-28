use core::fmt;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path},
};

use nlu_data::{
    DataError, DataErrorCode, MAX_RECORD_BYTES, canonical_json, parse_strict_json,
    read_bounded_root_file, sha256_hex,
};
use serde::{Deserialize, Serialize};

pub(crate) const SPLIT_MANIFEST_PATH: &str =
    "data/evaluation/p08/pos-v1/splits/split-manifest.json";
pub(crate) const SPLIT_MANIFEST_SHA256: &str =
    "bd6ef5cf79e188d43eb6b6ecddd4de7e5f767aab14316a3a4b677a04d9c2fb5c";
pub(crate) const TRAIN_PATH: &str = "data/evaluation/p08/pos-v1/splits/train.jsonl";
pub(crate) const TRAIN_SHA256: &str =
    "b78d4b79350638da0cacf2c6c027d07b37919a9e3bc82de654b4f1c0f0984461";
pub(crate) const HELDOUT_PATH: &str = "data/evaluation/p08/pos-v1/splits/heldout.jsonl";
pub(crate) const HELDOUT_SHA256: &str =
    "876e85049fb391b5171641e206b7bc2b4ae2bb4e1d58f36858bf1b5c91812dd7";

pub(crate) const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
pub(crate) const CORPUS_VERSION: &str = "1.0.0";
pub(crate) const GENERATOR_ID: &str = "p02-generator-v1";
pub(crate) const SOURCE_LICENSE: &str = "Apache-2.0";
pub(crate) const LOCALE: &str = "pt-BR";
pub(crate) const CLAIM_SCOPE: &str = "internal_conformance_only";
pub(crate) const SOURCE_MANIFEST_SHA256: &str =
    "d9e1ca32ec0f92aa6b5fc6c1d4f232f93389e0bf8031267d23daddf7287006c5";
pub(crate) const CORPUS_MANIFEST_SHA256: &str =
    "a251485ba2f8d5032603200b7e171954213383aeceac9a8f394edb09a265e72a";
pub(crate) const SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
pub(crate) const GENERATOR_SHA256: &str =
    "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
pub(crate) const ORIGINAL_POS_SHA256: &str =
    "85ad18caf3ae749d3ec0135c3c01ce6d754f831d395abdc44ff1c3643a18fac8";

pub(crate) const TRAIN_CASE_IDS_SHA256: &str =
    "2d19927df47be5f79b98ee6efffa290b512633682b2635dbe7de265653a4f481";
pub(crate) const TRAIN_DOCUMENT_IDS_SHA256: &str =
    "31548f0c55c3c66d2560a46b1567a4a197c1cf443ddecbc8d1f502fd3a8e3ce8";
pub(crate) const TRAIN_TEXT_SHA256S_SHA256: &str =
    "af7d475417506b366b6eb226524f25b29bafb84d25f4dd9e39adfe8c6e1b7e2a";
pub(crate) const ORIGINS_SHA256: &str =
    "7c9b3c0943f43dcc2327fcaec2edc61ff2eedc8e5a2c62f714c1c07cb388b757";

pub(crate) const HELDOUT_CASE_IDS_SHA256: &str =
    "273c254d3dd18c2f296330f6affbd8e4f33bbc0ed35bb2c2de38480a9c5bf93f";
pub(crate) const HELDOUT_DOCUMENT_IDS_SHA256: &str =
    "26ed164850cb01339215158824eb5c2f2f832b9516abad1cbe13326aeae1926b";
pub(crate) const HELDOUT_TEXT_SHA256S_SHA256: &str =
    "b1dc1f1aa9598155c783b43871136ba64b9959996d0972e464840f8975c970c4";

pub(crate) const MAX_SPLIT_MANIFEST_BYTES: usize = 64 * 1024;
pub(crate) const MAX_SLICE_BYTES: usize = 256 * 1024;
pub(crate) const MAX_ROWS: usize = 512;
pub(crate) const MAX_TOKENS_PER_ROW: usize = 256;
pub(crate) const MAX_TEXT_BYTES: usize = 32 * 1024;
pub(crate) const MAX_IDENTIFIER_BYTES: usize = 256;

const POS_LABELS: [&str; 7] = ["ADJ", "ADP", "DET", "NOUN", "NUM", "VERB", "X"];

pub type Result<T> = core::result::Result<T, PosEvaluationError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PosEvaluationErrorCode {
    InvalidArguments,
    InvalidManifest,
    InvalidDataset,
    IntegrityMismatch,
    ResourceLimit,
    RuntimeUnavailable,
    OutputFailure,
    ReportEncoding,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PosEvaluationError {
    code: PosEvaluationErrorCode,
    context: String,
}

impl PosEvaluationError {
    pub(crate) fn new(code: PosEvaluationErrorCode, context: impl Into<String>) -> Self {
        let mut context = context.into();
        context.truncate(512);
        Self { code, context }
    }

    #[must_use]
    pub const fn code(&self) -> PosEvaluationErrorCode {
        self.code
    }

    #[must_use]
    pub fn context(&self) -> &str {
        &self.context
    }
}

impl fmt::Display for PosEvaluationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.context)
    }
}

impl std::error::Error for PosEvaluationError {}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PosRow {
    pub(crate) schema_version: u32,
    pub(crate) case_id: String,
    pub(crate) source_id: String,
    pub(crate) corpus_version: String,
    pub(crate) generator_id: String,
    pub(crate) license: String,
    pub(crate) locale: String,
    pub(crate) split: String,
    pub(crate) family: String,
    pub(crate) document_id: String,
    pub(crate) text: String,
    pub(crate) text_sha256: String,
    pub(crate) tokens: Vec<PosToken>,
    pub(crate) ambiguity_preserved: Option<bool>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PosToken {
    pub(crate) text: String,
    pub(crate) begin_byte: u64,
    pub(crate) end_byte: u64,
    pub(crate) allowed_pos: Vec<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct PosDataset {
    pub(crate) rows: BTreeMap<String, PosRow>,
    pub(crate) documents: BTreeSet<String>,
    pub(crate) families: BTreeSet<String>,
    pub(crate) text_sha256s: BTreeSet<String>,
    pub(crate) origins: BTreeSet<Origin>,
    pub(crate) label_counts: BTreeMap<String, u64>,
    pub(crate) token_count: u64,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
pub(crate) struct Origin {
    generator_id: String,
    corpus_version: String,
    source_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SplitManifest {
    schema_version: u32,
    compiler_id: String,
    dataset_id: String,
    dataset_version: String,
    digest_contract: DigestContract,
    source: SplitSource,
    partition: Partition,
    splits: Vec<SplitEntry>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DigestContract {
    algorithm: String,
    collection_encoding: String,
    origin_fields: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SplitSource {
    source_id: String,
    corpus_version: String,
    generator_id: String,
    license: String,
    locale: String,
    claim_scope: String,
    source_manifest_path: String,
    source_manifest_sha256: String,
    corpus_manifest_path: String,
    corpus_manifest_sha256: String,
    specification_path: String,
    specification_sha256: String,
    generator_path: String,
    generator_sha256: String,
    artifact_path: String,
    artifact_bytes: u64,
    artifact_sha256: String,
    artifact_records: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Partition {
    split_order: Vec<String>,
    records: u64,
    documents: u64,
    tokens: u64,
    case_ids_sha256: String,
    document_ids_sha256: String,
    text_sha256s_sha256: String,
    origins_sha256: String,
    origin_count: u64,
    families: Vec<String>,
    sentence_disjoint: bool,
    document_disjoint: bool,
    family_disjoint: bool,
    complete: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SplitEntry {
    pub(crate) split: String,
    pub(crate) path: String,
    pub(crate) bytes: u64,
    pub(crate) sha256: String,
    pub(crate) records: u64,
    pub(crate) documents: u64,
    pub(crate) tokens: u64,
    pub(crate) case_ids_sha256: String,
    pub(crate) document_ids_sha256: String,
    pub(crate) text_sha256s_sha256: String,
    pub(crate) origins_sha256: String,
    pub(crate) origin_count: u64,
    pub(crate) families: Vec<String>,
}

impl SplitManifest {
    pub(crate) fn entry(&self, split: &str) -> Result<&SplitEntry> {
        let mut matches = self.splits.iter().filter(|entry| entry.split == split);
        let entry = matches
            .next()
            .ok_or_else(|| invalid_manifest("missing split entry"))?;
        if matches.next().is_some() {
            return Err(invalid_manifest("duplicate split entry"));
        }
        Ok(entry)
    }

    fn validate(&self) -> Result<()> {
        if self.schema_version != 1
            || self.compiler_id != "p08-pos-split-compiler-v1"
            || self.dataset_id != "p08-pos-context-splits-v1"
            || self.dataset_version != CORPUS_VERSION
            || self.digest_contract.algorithm != "sha256"
            || self.digest_contract.collection_encoding != "canonical-json-sorted-unique-array-v1"
            || self.digest_contract.origin_fields != ["source_id", "corpus_version", "generator_id"]
        {
            return Err(invalid_manifest("split manifest identity"));
        }

        let source = &self.source;
        if source.source_id != SOURCE_ID
            || source.corpus_version != CORPUS_VERSION
            || source.generator_id != GENERATOR_ID
            || source.license != SOURCE_LICENSE
            || source.locale != LOCALE
            || source.claim_scope != CLAIM_SCOPE
            || source.source_manifest_path
                != "data/manifests/project-authored-synthetic-ptbr-v1.json"
            || source.source_manifest_sha256 != SOURCE_MANIFEST_SHA256
            || source.corpus_manifest_path != "data/project-authored/p02-v1/manifest.json"
            || source.corpus_manifest_sha256 != CORPUS_MANIFEST_SHA256
            || source.specification_path != "data/project-authored/p02-v1/specification.yaml"
            || source.specification_sha256 != SPECIFICATION_SHA256
            || source.generator_path != "tools/generate-p02-corpus.rb"
            || source.generator_sha256 != GENERATOR_SHA256
            || source.artifact_path != "data/project-authored/p02-v1/pos-context.jsonl"
            || source.artifact_bytes != 222_999
            || source.artifact_sha256 != ORIGINAL_POS_SHA256
            || source.artifact_records != 241
        {
            return Err(invalid_manifest("split manifest source"));
        }

        let partition = &self.partition;
        if partition.split_order != ["train", "development", "heldout"]
            || partition.records != 241
            || partition.documents != 25
            || partition.tokens != 1_763
            || partition.case_ids_sha256
                != "2bd856c701426893fc99cec421ef23c335ce62f390eb7c7fa47c45d3ef1dfa7c"
            || partition.document_ids_sha256
                != "03fe393410fe4de1d4011d267c244bff82f93919d9af867f5e280ccf084938d7"
            || partition.text_sha256s_sha256
                != "ebf82a790d2bfe50def2b0a6f69cd484fdbd46f1ace816344d577ba3f87213b4"
            || partition.origins_sha256 != ORIGINS_SHA256
            || partition.origin_count != 1
            || partition.families
                != [
                    "pos-ambiguity-heldout-v1",
                    "pos-command-train-v1",
                    "pos-query-development-v1",
                    "pos-state-heldout-v1",
                ]
            || !partition.sentence_disjoint
            || !partition.document_disjoint
            || !partition.family_disjoint
            || !partition.complete
            || self.splits.len() != 3
        {
            return Err(invalid_manifest("split manifest partition"));
        }

        validate_split_entry(
            self.entry("train")?,
            ExpectedSplitEntry {
                path: TRAIN_PATH,
                bytes: 70_382,
                sha256: TRAIN_SHA256,
                records: 80,
                documents: 8,
                tokens: 560,
                case_ids_sha256: TRAIN_CASE_IDS_SHA256,
                document_ids_sha256: TRAIN_DOCUMENT_IDS_SHA256,
                text_sha256s_sha256: TRAIN_TEXT_SHA256S_SHA256,
                families: &["pos-command-train-v1"],
            },
        )?;
        validate_split_entry(
            self.entry("development")?,
            ExpectedSplitEntry {
                path: "data/evaluation/p08/pos-v1/splits/development.jsonl",
                bytes: 73_262,
                sha256: "50df080f1d0f5e4d22dab1fd1221eb3aac61a3152afbfb759f0a728da1072880",
                records: 80,
                documents: 8,
                tokens: 560,
                case_ids_sha256: "829b57ba407eda545d6643f7c3299eaef1d54908e16e76bafea9f80556c1d730",
                document_ids_sha256: "fb7a90200770594266dec6b3ba0ca69ec07841bf2dfadad246313182a5e6c8a4",
                text_sha256s_sha256: "1988b9ae8328899203310ab7c25bba24bca66a2ca5acedfb6222cff5a8f17db4",
                families: &["pos-query-development-v1"],
            },
        )?;
        validate_split_entry(
            self.entry("heldout")?,
            ExpectedSplitEntry {
                path: HELDOUT_PATH,
                bytes: 79_355,
                sha256: HELDOUT_SHA256,
                records: 81,
                documents: 9,
                tokens: 643,
                case_ids_sha256: HELDOUT_CASE_IDS_SHA256,
                document_ids_sha256: HELDOUT_DOCUMENT_IDS_SHA256,
                text_sha256s_sha256: HELDOUT_TEXT_SHA256S_SHA256,
                families: &["pos-ambiguity-heldout-v1", "pos-state-heldout-v1"],
            },
        )
    }
}

struct ExpectedSplitEntry<'a> {
    path: &'a str,
    bytes: u64,
    sha256: &'a str,
    records: u64,
    documents: u64,
    tokens: u64,
    case_ids_sha256: &'a str,
    document_ids_sha256: &'a str,
    text_sha256s_sha256: &'a str,
    families: &'a [&'a str],
}

fn validate_split_entry(entry: &SplitEntry, expected: ExpectedSplitEntry<'_>) -> Result<()> {
    if entry.path != expected.path
        || entry.bytes != expected.bytes
        || entry.sha256 != expected.sha256
        || entry.records != expected.records
        || entry.documents != expected.documents
        || entry.tokens != expected.tokens
        || entry.case_ids_sha256 != expected.case_ids_sha256
        || entry.document_ids_sha256 != expected.document_ids_sha256
        || entry.text_sha256s_sha256 != expected.text_sha256s_sha256
        || entry.origins_sha256 != ORIGINS_SHA256
        || entry.origin_count != 1
        || entry
            .families
            .iter()
            .map(String::as_str)
            .ne(expected.families.iter().copied())
    {
        return Err(invalid_manifest("split entry inventory"));
    }
    Ok(())
}

pub(crate) fn load_split_manifest(root: &Path, relative_path: &str) -> Result<SplitManifest> {
    if relative_path != SPLIT_MANIFEST_PATH {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "split manifest path is not the frozen P08 path",
        ));
    }
    validate_relative_path(relative_path)?;
    let bytes = read_bounded_root_file(root, relative_path, MAX_SPLIT_MANIFEST_BYTES)
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    if sha256(&bytes, PosEvaluationErrorCode::InvalidManifest)? != SPLIT_MANIFEST_SHA256 {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "split manifest SHA-256",
        ));
    }
    if bytes.last() != Some(&b'\n') {
        return Err(invalid_manifest("split manifest final newline"));
    }
    let value = parse_strict_json(&bytes, "P08 split manifest")
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    let mut canonical = canonical_json(&value, "P08 split manifest")
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidManifest, error))?;
    canonical.push(b'\n');
    if canonical != bytes {
        return Err(invalid_manifest("noncanonical split manifest"));
    }
    let manifest: SplitManifest = serde_json::from_value(value).map_err(|error| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidManifest,
            format!("closed split manifest shape: {error}"),
        )
    })?;
    manifest.validate()?;
    Ok(manifest)
}

pub(crate) fn read_split_slice(
    root: &Path,
    entry: &SplitEntry,
    expected_path: &str,
    expected_sha256: &str,
) -> Result<Vec<u8>> {
    if entry.path != expected_path || entry.sha256 != expected_sha256 {
        return Err(invalid_manifest("requested split entry"));
    }
    validate_relative_path(&entry.path)?;
    let bytes = read_bounded_root_file(root, &entry.path, MAX_SLICE_BYTES)
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidDataset, error))?;
    let actual_bytes = u64::try_from(bytes.len()).map_err(|_| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::ResourceLimit,
            "split slice byte count",
        )
    })?;
    if actual_bytes != entry.bytes
        || sha256(&bytes, PosEvaluationErrorCode::InvalidDataset)? != expected_sha256
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::IntegrityMismatch,
            "split slice bytes or SHA-256",
        ));
    }
    Ok(bytes)
}

pub(crate) fn parse_pos_slice(bytes: &[u8], expected_split: &str) -> Result<PosDataset> {
    if bytes.is_empty() || bytes.len() > MAX_SLICE_BYTES {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::ResourceLimit,
            "POS slice bytes",
        ));
    }
    if bytes.last() != Some(&b'\n') {
        return Err(invalid_dataset("POS slice final newline"));
    }
    core::str::from_utf8(bytes).map_err(|_| invalid_dataset("POS slice UTF-8"))?;

    let content = bytes
        .strip_suffix(b"\n")
        .ok_or_else(|| invalid_dataset("POS slice newline"))?;
    let lines = content.split(|byte| *byte == b'\n').collect::<Vec<_>>();
    if lines.len() > MAX_ROWS {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::ResourceLimit,
            "POS row count",
        ));
    }

    let mut dataset = PosDataset {
        rows: BTreeMap::new(),
        documents: BTreeSet::new(),
        families: BTreeSet::new(),
        text_sha256s: BTreeSet::new(),
        origins: BTreeSet::new(),
        label_counts: BTreeMap::new(),
        token_count: 0,
    };
    for (index, line) in lines.into_iter().enumerate() {
        if line.is_empty() {
            return Err(invalid_dataset("empty POS row"));
        }
        if line.len() > MAX_RECORD_BYTES {
            return Err(PosEvaluationError::new(
                PosEvaluationErrorCode::ResourceLimit,
                "POS row bytes",
            ));
        }
        let value = parse_strict_json(line, &format!("P08 POS row {}", index + 1))
            .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidDataset, error))?;
        let row: PosRow = serde_json::from_value(value).map_err(|error| {
            PosEvaluationError::new(
                PosEvaluationErrorCode::InvalidDataset,
                format!("closed POS row {}: {error}", index + 1),
            )
        })?;
        validate_and_add_row(&mut dataset, row, expected_split)?;
    }
    Ok(dataset)
}

fn validate_and_add_row(dataset: &mut PosDataset, row: PosRow, expected_split: &str) -> Result<()> {
    if row.schema_version != 1
        || row.source_id != SOURCE_ID
        || row.corpus_version != CORPUS_VERSION
        || row.generator_id != GENERATOR_ID
        || row.license != SOURCE_LICENSE
        || row.locale != LOCALE
        || row.split != expected_split
    {
        return Err(invalid_dataset("POS row lineage"));
    }
    if !valid_identifier(&row.case_id)
        || !valid_identifier(&row.family)
        || !valid_identifier(&row.document_id)
        || row.text.is_empty()
        || row.text.len() > MAX_TEXT_BYTES
        || row.text.contains("FIXTURE_TECNICA")
        || !valid_sha256(&row.text_sha256)
        || sha256(row.text.as_bytes(), PosEvaluationErrorCode::InvalidDataset)? != row.text_sha256
    {
        return Err(invalid_dataset("POS row identity or text"));
    }
    if row.tokens.is_empty() || row.tokens.len() > MAX_TOKENS_PER_ROW {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::ResourceLimit,
            "tokens per POS row",
        ));
    }
    if dataset.rows.contains_key(&row.case_id) {
        return Err(invalid_dataset("duplicate POS case ID"));
    }
    if !dataset.text_sha256s.insert(row.text_sha256.clone()) {
        return Err(invalid_dataset("duplicate POS sentence"));
    }

    let mut previous_end = 0_usize;
    for token in &row.tokens {
        let begin = usize::try_from(token.begin_byte)
            .map_err(|_| invalid_dataset("POS token begin offset"))?;
        let end =
            usize::try_from(token.end_byte).map_err(|_| invalid_dataset("POS token end offset"))?;
        if begin >= end
            || end > row.text.len()
            || !row.text.is_char_boundary(begin)
            || !row.text.is_char_boundary(end)
            || begin < previous_end
            || !row
                .text
                .get(previous_end..begin)
                .is_some_and(|gap| gap.chars().all(char::is_whitespace))
            || row.text.get(begin..end) != Some(token.text.as_str())
            || token.text.is_empty()
            || token.text.contains("FIXTURE_TECNICA")
            || token.allowed_pos.len() != 1
            || !POS_LABELS.contains(&token.allowed_pos[0].as_str())
        {
            return Err(invalid_dataset("POS token span or singleton label"));
        }
        previous_end = end;
        let count = dataset
            .label_counts
            .entry(token.allowed_pos[0].clone())
            .or_default();
        *count = count.checked_add(1).ok_or_else(|| {
            PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, "POS label count")
        })?;
        dataset.token_count = dataset.token_count.checked_add(1).ok_or_else(|| {
            PosEvaluationError::new(PosEvaluationErrorCode::ResourceLimit, "POS token count")
        })?;
    }
    if !row
        .text
        .get(previous_end..)
        .is_some_and(|gap| gap.chars().all(char::is_whitespace))
    {
        return Err(invalid_dataset("POS token terminal span"));
    }

    dataset.documents.insert(row.document_id.clone());
    dataset.families.insert(row.family.clone());
    dataset.origins.insert(Origin {
        generator_id: row.generator_id.clone(),
        corpus_version: row.corpus_version.clone(),
        source_id: row.source_id.clone(),
    });
    dataset.rows.insert(row.case_id.clone(), row);
    Ok(())
}

pub(crate) fn collection_digest_strings<'a>(
    values: impl IntoIterator<Item = &'a str>,
) -> Result<String> {
    let values = values
        .into_iter()
        .map(str::to_owned)
        .collect::<BTreeSet<_>>();
    collection_digest(&values)
}

pub(crate) fn collection_digest<T: Serialize + Ord>(values: &BTreeSet<T>) -> Result<String> {
    let value = serde_json::to_value(values).map_err(|error| {
        PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidDataset,
            format!("collection digest value: {error}"),
        )
    })?;
    let bytes = canonical_json(&value, "P08 collection digest")
        .map_err(|error| map_data_error(PosEvaluationErrorCode::InvalidDataset, error))?;
    sha256(&bytes, PosEvaluationErrorCode::InvalidDataset)
}

pub(crate) fn canonical_bytes<T: Serialize>(
    value: &T,
    context: &str,
    error_code: PosEvaluationErrorCode,
) -> Result<Vec<u8>> {
    let value = serde_json::to_value(value).map_err(|error| {
        PosEvaluationError::new(error_code, format!("{context} value: {error}"))
    })?;
    let mut bytes =
        canonical_json(&value, context).map_err(|error| map_data_error(error_code, error))?;
    bytes.push(b'\n');
    Ok(bytes)
}

pub(crate) fn sha256(bytes: &[u8], code: PosEvaluationErrorCode) -> Result<String> {
    sha256_hex(bytes).map_err(|error| map_data_error(code, error))
}

pub(crate) fn validate_relative_path(path: &str) -> Result<()> {
    if path.is_empty()
        || path.len() > 512
        || path.as_bytes().contains(&0)
        || Path::new(path).is_absolute()
        || Path::new(path)
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(PosEvaluationError::new(
            PosEvaluationErrorCode::InvalidArguments,
            "path must be a bounded safe relative path",
        ));
    }
    Ok(())
}

pub(crate) fn map_data_error(code: PosEvaluationErrorCode, error: DataError) -> PosEvaluationError {
    let mapped = if error.code() == DataErrorCode::ResourceLimit {
        PosEvaluationErrorCode::ResourceLimit
    } else {
        code
    };
    PosEvaluationError::new(mapped, error.context())
}

pub(crate) fn invalid_manifest(context: &str) -> PosEvaluationError {
    PosEvaluationError::new(PosEvaluationErrorCode::InvalidManifest, context)
}

pub(crate) fn invalid_dataset(context: &str) -> PosEvaluationError {
    PosEvaluationError::new(PosEvaluationErrorCode::InvalidDataset, context)
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_IDENTIFIER_BYTES
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'.' | b'_' | b'-'))
        })
        && value.as_bytes()[0].is_ascii_alphanumeric()
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::{MAX_ROWS, MAX_SLICE_BYTES, PosEvaluationErrorCode, parse_pos_slice};

    const TRAIN: &[u8] = include_bytes!("../../../data/evaluation/p08/pos-v1/splits/train.jsonl");

    #[test]
    fn rejects_duplicate_keys_bad_spans_and_open_rows() {
        let first = TRAIN
            .split(|byte| *byte == b'\n')
            .next()
            .expect("first train row");
        let first = core::str::from_utf8(first).expect("UTF-8 row");

        let duplicate = format!(
            "{{\"schema_version\":1,{}\n",
            first.strip_prefix('{').expect("object")
        );
        assert!(parse_pos_slice(duplicate.as_bytes(), "train").is_err());

        let mut bad_span: serde_json::Value =
            serde_json::from_str(first).expect("bad-span source row");
        bad_span["tokens"][0]["begin_byte"] = serde_json::json!(1);
        assert!(parse_pos_slice(&jsonl(bad_span), "train").is_err());

        let mut open: serde_json::Value = serde_json::from_str(first).expect("open source row");
        open["unexpected"] = serde_json::json!(true);
        assert!(parse_pos_slice(&jsonl(open), "train").is_err());
    }

    #[test]
    fn rejects_non_whitespace_between_valid_token_spans() {
        let first = TRAIN
            .split(|byte| *byte == b'\n')
            .next()
            .expect("first train row");
        let mut row: serde_json::Value = serde_json::from_slice(first).expect("gap source row");
        let original = row["text"].as_str().expect("row text");
        let changed = original.replacen(' ', ".", 1);
        assert_eq!(changed.len(), original.len());
        row["text"] = serde_json::Value::String(changed.clone());
        row["text_sha256"] = serde_json::Value::String(
            nlu_data::sha256_hex(changed.as_bytes()).expect("changed text digest"),
        );

        let error = parse_pos_slice(&jsonl(row), "train").expect_err("non-whitespace gap");
        assert_eq!(error.code(), PosEvaluationErrorCode::InvalidDataset);
        assert!(error.context().contains("span"));
    }

    #[test]
    fn enforces_slice_byte_and_row_limits_before_row_parsing() {
        let one_over_bytes = vec![b'x'; MAX_SLICE_BYTES + 1];
        assert_eq!(
            parse_pos_slice(&one_over_bytes, "train")
                .expect_err("one-over bytes")
                .code(),
            PosEvaluationErrorCode::ResourceLimit
        );

        let one_over_rows = b"{}\n".repeat(MAX_ROWS + 1);
        assert_eq!(
            parse_pos_slice(&one_over_rows, "train")
                .expect_err("one-over rows")
                .code(),
            PosEvaluationErrorCode::ResourceLimit
        );
    }

    fn jsonl(value: serde_json::Value) -> Vec<u8> {
        let mut bytes = serde_json::to_vec(&value).expect("row encoding");
        bytes.push(b'\n');
        bytes
    }
}
