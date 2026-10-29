use core::{fmt, slice};

use crate::{
    LexicalAnalysis, LexicalLookup, Lexicon, LexiconError, MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN,
    NormalizedText, TextError, Token,
};

pub const MORPHOLOGY_ANALYZER_ID: &str = "lang-ptbr-lexical-evidence-morphology-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MorphologyError {
    InvalidLexicon(LexiconError),
    TooManyAnalyses,
}

impl MorphologyError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidLexicon(_) => "morphology_invalid_lexicon",
            Self::TooManyAnalyses => "morphology_too_many_analyses",
        }
    }
}

impl fmt::Display for MorphologyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for MorphologyError {}

impl From<LexiconError> for MorphologyError {
    fn from(error: LexiconError) -> Self {
        Self::InvalidLexicon(error)
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct LexicalEvidenceAnalysis<'a> {
    analysis: &'a LexicalAnalysis,
}

impl<'a> LexicalEvidenceAnalysis<'a> {
    #[must_use]
    pub const fn analysis(self) -> &'a LexicalAnalysis {
        self.analysis
    }
}

impl fmt::Debug for LexicalEvidenceAnalysis<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LexicalEvidenceAnalysis")
            .field("analysis_id", &self.analysis.analysis_id())
            .field("category", &self.analysis.category())
            .field("feature_count", &self.analysis.features().len())
            .field("source_id", &self.analysis.source().source_id())
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct LexicalEvidenceSet<'a> {
    analyses: &'a [LexicalAnalysis],
}

impl<'a> LexicalEvidenceSet<'a> {
    #[must_use]
    pub const fn analyses(self) -> &'a [LexicalAnalysis] {
        self.analyses
    }

    #[must_use]
    pub const fn len(self) -> usize {
        self.analyses.len()
    }

    #[must_use]
    pub const fn is_empty(self) -> bool {
        self.analyses.is_empty()
    }
}

impl fmt::Debug for LexicalEvidenceSet<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LexicalEvidenceSet")
            .field("analysis_count", &self.analyses.len())
            .finish()
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum MorphologyLookup<'a> {
    Unknown,
    Unique(LexicalEvidenceAnalysis<'a>),
    Ambiguous(LexicalEvidenceSet<'a>),
}

impl<'a> MorphologyLookup<'a> {
    #[must_use]
    pub const fn is_unknown(self) -> bool {
        matches!(self, Self::Unknown)
    }

    #[must_use]
    pub const fn len(self) -> usize {
        match self {
            Self::Unknown => 0,
            Self::Unique(_) => 1,
            Self::Ambiguous(values) => values.len(),
        }
    }

    #[must_use]
    pub const fn is_empty(self) -> bool {
        matches!(self, Self::Unknown)
    }

    #[must_use]
    pub fn analyses(self) -> &'a [LexicalAnalysis] {
        match self {
            Self::Unknown => &[],
            Self::Unique(value) => slice::from_ref(value.analysis()),
            Self::Ambiguous(values) => values.analyses(),
        }
    }
}

impl fmt::Debug for MorphologyLookup<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown => formatter.write_str("Unknown"),
            Self::Unique(_) => formatter.write_str("UniqueLexicalEvidence"),
            Self::Ambiguous(values) => formatter
                .debug_tuple("AmbiguousLexicalEvidence")
                .field(&values.len())
                .finish(),
        }
    }
}

pub struct Morphology {
    lexicon: Lexicon,
}

impl Morphology {
    pub fn bundled() -> Result<Self, MorphologyError> {
        let lexicon = Lexicon::bundled()?;
        validate_surface_runs(lexicon.entries().iter().map(LexicalAnalysis::surface))?;
        Ok(Self { lexicon })
    }

    #[must_use]
    pub fn analyze(&self, surface: &str) -> MorphologyLookup<'_> {
        match self.lexicon.lookup(surface) {
            LexicalLookup::Unknown => MorphologyLookup::Unknown,
            LexicalLookup::Unique(analysis) => {
                MorphologyLookup::Unique(LexicalEvidenceAnalysis { analysis })
            }
            LexicalLookup::Conflict(analyses) => {
                MorphologyLookup::Ambiguous(LexicalEvidenceSet { analyses })
            }
        }
    }

    pub fn analyze_token<'a>(
        &'a self,
        token: &Token,
        text: &NormalizedText,
    ) -> Result<MorphologyLookup<'a>, TextError> {
        Ok(self.analyze(token.normalized_slice(text)?))
    }
}

impl fmt::Debug for Morphology {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Morphology")
            .field("analysis_count", &self.lexicon.len())
            .finish()
    }
}

fn validate_surface_runs<'a>(
    surfaces: impl IntoIterator<Item = &'a str>,
) -> Result<(), MorphologyError> {
    let mut previous = None::<&str>;
    let mut run = 0_usize;
    for surface in surfaces {
        if previous == Some(surface) {
            run = run.checked_add(1).ok_or(MorphologyError::TooManyAnalyses)?;
        } else {
            previous = Some(surface);
            run = 1;
        }
        if run > MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN {
            return Err(MorphologyError::TooManyAnalyses);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::{
        LexicalEvidenceAnalysis, MORPHOLOGY_ANALYZER_ID, Morphology, MorphologyError,
        MorphologyLookup, validate_surface_runs,
    };
    use crate::{
        LexicalAnalysis, LexicalCategory, MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN, NormalizedText,
    };

    #[test]
    fn every_bundled_entry_is_returned_as_lexical_evidence() {
        let morphology = Morphology::bundled().expect("bundled morphology");
        let mut returned = BTreeSet::new();
        for entry in morphology.lexicon.entries() {
            for analysis in morphology.analyze(entry.surface()).analyses() {
                returned.insert((
                    analysis.source().source_id(),
                    analysis.analysis_id(),
                    analysis.features(),
                ));
            }
        }
        assert_eq!(returned.len(), 33);
        assert_eq!(
            returned
                .iter()
                .filter(|(_, _, features)| features.is_empty())
                .count(),
            4
        );
        assert_eq!(
            returned,
            morphology
                .lexicon
                .entries()
                .iter()
                .map(|analysis| (
                    analysis.source().source_id(),
                    analysis.analysis_id(),
                    analysis.features()
                ))
                .collect()
        );
    }

    #[test]
    fn unknown_has_no_analysis_payload() {
        let morphology = Morphology::bundled().expect("bundled morphology");
        let lookup = morphology.analyze("FIXTURE_TECNICA.unknown");
        assert!(lookup.is_unknown());
        assert!(lookup.is_empty());
        assert!(lookup.analyses().is_empty());
    }

    #[test]
    fn source_conflict_remains_complete_and_unranked() {
        let morphology = Morphology::bundled().expect("bundled morphology");
        let duplicate = morphology
            .lexicon
            .entries()
            .windows(2)
            .find(|pair| pair[0].surface() == pair[1].surface())
            .expect("source-backed duplicate");
        let MorphologyLookup::Ambiguous(values) = morphology.analyze(duplicate[0].surface()) else {
            panic!("conflict was not ambiguous");
        };
        assert_eq!(values.analyses(), duplicate);
        assert_eq!(
            values
                .analyses()
                .iter()
                .map(LexicalAnalysis::category)
                .collect::<BTreeSet<_>>(),
            BTreeSet::from([LexicalCategory::Noun, LexicalCategory::Verb])
        );
    }

    #[test]
    fn token_analysis_uses_exact_normalized_surface() {
        let morphology = Morphology::bundled().expect("bundled morphology");
        let entry = morphology.lexicon.entries().first().expect("entry");
        let text = NormalizedText::new(entry.surface().to_owned()).expect("normalized text");
        let tokens = text.tokenize().expect("tokenization");
        assert_eq!(tokens.len(), 1);
        assert_eq!(
            morphology
                .analyze_token(&tokens.tokens()[0], &text)
                .expect("token analysis")
                .analyses(),
            morphology.analyze(entry.surface()).analyses()
        );
    }

    #[test]
    fn analysis_cap_accepts_exact_limit_and_rejects_one_over() {
        let exact = vec!["FIXTURE_TECNICA.surface"; MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN];
        validate_surface_runs(exact.iter().copied()).expect("exact cap");
        let over = vec!["FIXTURE_TECNICA.surface"; MAX_MORPHOLOGICAL_ANALYSES_PER_TOKEN + 1];
        assert_eq!(
            validate_surface_runs(over.iter().copied()).expect_err("one over cap"),
            MorphologyError::TooManyAnalyses
        );
    }

    #[test]
    fn debug_output_omits_surface_lemma_and_features() {
        let morphology = Morphology::bundled().expect("bundled morphology");
        let analysis = morphology
            .lexicon
            .entries()
            .iter()
            .find(|entry| entry.surface().len() > 5 && entry.lemma().len() > 5)
            .expect("long source-backed entry");
        let wrapped = LexicalEvidenceAnalysis { analysis };
        let output = format!("{wrapped:?} {morphology:?}");
        assert!(!output.contains(analysis.surface()));
        assert!(!output.contains(analysis.lemma()));
        for feature in analysis.features() {
            assert!(!output.contains(feature));
        }
        assert_eq!(
            MORPHOLOGY_ANALYZER_ID,
            "lang-ptbr-lexical-evidence-morphology-v1"
        );
    }
}
