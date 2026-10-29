mod frozen;
mod gold;
mod templates;

use std::path::Path;

pub(crate) use gold::{
    GoldCase, GoldCatalogEntity, GoldOutcome, GoldSource, core_canonical_bytes, source_counts,
};

use crate::{Result, evaluator::EvaluationSplit};

pub(crate) fn load(root: &Path, split: EvaluationSplit) -> Result<Vec<GoldCase>> {
    gold::project(frozen::load(root, split)?, split)
}
