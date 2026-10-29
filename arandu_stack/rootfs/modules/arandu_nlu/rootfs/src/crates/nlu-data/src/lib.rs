#![forbid(unsafe_code)]

mod error;
mod hash;
mod json;
mod manifest;
mod pipeline;

pub mod cli;
pub mod intent;
pub mod lexicon;
pub mod pos;

pub use error::{DataError, DataErrorCode, Result};
pub use hash::sha256_hex;
pub use intent::{compile_intent_package, decode_intent_package};
pub use json::{canonical_json, parse_strict_json};
pub use lexicon::{compile_lexicon, remove_lexicon_source};
pub use pipeline::{
    compile, fetch, import, normalize, remove_source, split, validate_stage, verify,
};
pub use pos::{compile_pos_transition_package, decode_pos_transition_package};

pub const MAX_RECORD_BYTES: usize = 256 * 1024;
pub const MAX_RECORDS: usize = 20_000;

pub fn read_bounded_root_file(
    root: &std::path::Path,
    relative: &str,
    limit: usize,
) -> Result<Vec<u8>> {
    manifest::read_root_file(root, relative, limit)
}
