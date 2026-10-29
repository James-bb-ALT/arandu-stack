use unicode_segmentation::UnicodeSegmentation;

use crate::{Result, error::reconciliation};

pub(crate) const WORD_COUNTER_ID: &str =
    "uax29-default-word-v1/unicode-17.0.0/unicode-segmentation-1.13.3";
pub(crate) const UNICODE_VERSION: &str = "17.0.0";

pub(crate) fn count(value: &str) -> Result<u64> {
    if unicode_segmentation::UNICODE_VERSION != (17, 0, 0) {
        return Err(reconciliation("Unicode word counter version"));
    }
    u64::try_from(value.unicode_words().count()).map_err(|_| reconciliation("Unicode word count"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counter_is_the_frozen_unicode_default_word_algorithm() {
        assert_eq!(
            WORD_COUNTER_ID,
            "uax29-default-word-v1/unicode-17.0.0/unicode-segmentation-1.13.3"
        );
        assert_eq!(UNICODE_VERSION, "17.0.0");
        assert_eq!(
            count("FIXTURE_TECNICA alpha beta").expect("FIXTURE_TECNICA word count"),
            3
        );
        assert_eq!(
            count("FIXTURE_TECNICA ação 123").expect("FIXTURE_TECNICA Unicode word count"),
            3
        );
    }
}
