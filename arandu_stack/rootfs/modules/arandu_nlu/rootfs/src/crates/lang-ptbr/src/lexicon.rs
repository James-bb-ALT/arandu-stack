use core::{fmt, ops::Range};
use std::collections::BTreeMap;

use nlu_data::lexicon::{
    GenerationLineage, LexiconEntry, LexiconSource, TransformationStep, decode_lexicon,
};

use crate::{NormalizedText, TextError, Token};

const BUNDLED_PACKAGE: &[u8] = include_bytes!("../../../data/lexicon/p06/package.bin");
const BUNDLED_MANIFEST: &[u8] = include_bytes!("../../../data/lexicon/p06/package-manifest.json");

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LexicalCategory {
    Adjective,
    Adposition,
    Adverb,
    CoordinatingConjunction,
    Determiner,
    Noun,
    Verb,
}

impl LexicalCategory {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Adjective => "ADJ",
            Self::Adposition => "ADP",
            Self::Adverb => "ADV",
            Self::CoordinatingConjunction => "CCONJ",
            Self::Determiner => "DET",
            Self::Noun => "NOUN",
            Self::Verb => "VERB",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "ADJ" => Some(Self::Adjective),
            "ADP" => Some(Self::Adposition),
            "ADV" => Some(Self::Adverb),
            "CCONJ" => Some(Self::CoordinatingConjunction),
            "DET" => Some(Self::Determiner),
            "NOUN" => Some(Self::Noun),
            "VERB" => Some(Self::Verb),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LexiconError {
    InvalidArtifact,
    InvalidCategory,
    InvalidIndex,
}

impl LexiconError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidArtifact => "lexicon_invalid_artifact",
            Self::InvalidCategory => "lexicon_invalid_category",
            Self::InvalidIndex => "lexicon_invalid_index",
        }
    }
}

impl fmt::Display for LexiconError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for LexiconError {}

#[derive(Clone, Eq, PartialEq)]
pub struct LexicalAnalysis {
    entry: LexiconEntry,
    category: LexicalCategory,
}

impl LexicalAnalysis {
    #[must_use]
    pub fn analysis_id(&self) -> &str {
        self.entry.analysis_id()
    }

    #[must_use]
    pub fn surface(&self) -> &str {
        self.entry.surface()
    }

    #[must_use]
    pub fn lemma(&self) -> &str {
        self.entry.lemma()
    }

    #[must_use]
    pub const fn category(&self) -> LexicalCategory {
        self.category
    }

    #[must_use]
    pub fn features(&self) -> &[String] {
        self.entry.features()
    }

    #[must_use]
    pub fn source(&self) -> &LexiconSource {
        self.entry.source()
    }

    #[must_use]
    pub fn generation(&self) -> &GenerationLineage {
        self.entry.generation()
    }

    #[must_use]
    pub fn transformations(&self) -> &[TransformationStep] {
        self.entry.transformations()
    }

    #[must_use]
    pub fn derivative_license(&self) -> &str {
        self.entry.derivative_license()
    }
}

impl fmt::Debug for LexicalAnalysis {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LexicalAnalysis")
            .field("analysis_id", &self.analysis_id())
            .field("category", &self.category)
            .field("feature_count", &self.features().len())
            .field("source_id", &self.source().source_id())
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Copy)]
pub enum LexicalLookup<'a> {
    Unknown,
    Unique(&'a LexicalAnalysis),
    Conflict(&'a [LexicalAnalysis]),
}

impl LexicalLookup<'_> {
    #[must_use]
    pub const fn is_unknown(self) -> bool {
        matches!(self, Self::Unknown)
    }

    #[must_use]
    pub const fn len(self) -> usize {
        match self {
            Self::Unknown => 0,
            Self::Unique(_) => 1,
            Self::Conflict(values) => values.len(),
        }
    }

    #[must_use]
    pub const fn is_empty(self) -> bool {
        matches!(self, Self::Unknown)
    }
}

impl fmt::Debug for LexicalLookup<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown => formatter.write_str("Unknown"),
            Self::Unique(_) => formatter.write_str("Unique"),
            Self::Conflict(values) => formatter
                .debug_tuple("Conflict")
                .field(&values.len())
                .finish(),
        }
    }
}

pub struct Lexicon {
    entries: Box<[LexicalAnalysis]>,
    by_surface: BTreeMap<Box<str>, Range<usize>>,
    by_identity: BTreeMap<(Box<str>, Box<str>), usize>,
}

impl Lexicon {
    pub fn bundled() -> Result<Self, LexiconError> {
        Self::from_artifact(BUNDLED_PACKAGE, BUNDLED_MANIFEST)
    }

    #[must_use]
    pub fn entries(&self) -> &[LexicalAnalysis] {
        &self.entries
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    #[must_use]
    pub fn lookup(&self, surface: &str) -> LexicalLookup<'_> {
        let Some(range) = self.by_surface.get(surface) else {
            return LexicalLookup::Unknown;
        };
        let values = &self.entries[range.clone()];
        match values {
            [value] => LexicalLookup::Unique(value),
            _ => LexicalLookup::Conflict(values),
        }
    }

    pub fn lookup_token<'a>(
        &'a self,
        token: &Token,
        text: &NormalizedText,
    ) -> Result<LexicalLookup<'a>, TextError> {
        Ok(self.lookup(token.normalized_slice(text)?))
    }

    #[must_use]
    pub fn analysis(&self, source_id: &str, analysis_id: &str) -> Option<&LexicalAnalysis> {
        self.by_identity
            .get(&(Box::from(source_id), Box::from(analysis_id)))
            .map(|index| &self.entries[*index])
    }

    fn from_artifact(package_bytes: &[u8], manifest_bytes: &[u8]) -> Result<Self, LexiconError> {
        let package = decode_lexicon(package_bytes, manifest_bytes)
            .map_err(|_| LexiconError::InvalidArtifact)?;
        let mut entries = Vec::with_capacity(package.entries().len());
        for entry in package.entries() {
            let category =
                LexicalCategory::parse(entry.pos()).ok_or(LexiconError::InvalidCategory)?;
            entries.push(LexicalAnalysis {
                entry: entry.clone(),
                category,
            });
        }
        Self::from_entries(entries)
    }

    fn from_entries(entries: Vec<LexicalAnalysis>) -> Result<Self, LexiconError> {
        let mut by_surface = BTreeMap::new();
        let mut by_identity = BTreeMap::new();
        let mut start = 0_usize;
        while start < entries.len() {
            let surface = entries[start].surface();
            let mut end = start + 1;
            while end < entries.len() && entries[end].surface() == surface {
                end += 1;
            }
            if by_surface.insert(Box::from(surface), start..end).is_some() {
                return Err(LexiconError::InvalidIndex);
            }
            start = end;
        }
        for (index, entry) in entries.iter().enumerate() {
            let identity = (
                Box::from(entry.source().source_id()),
                Box::from(entry.analysis_id()),
            );
            if by_identity.insert(identity, index).is_some() {
                return Err(LexiconError::InvalidIndex);
            }
        }
        Ok(Self {
            entries: entries.into_boxed_slice(),
            by_surface,
            by_identity,
        })
    }
}

impl fmt::Debug for Lexicon {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Lexicon")
            .field("entry_count", &self.entries.len())
            .field("surface_count", &self.by_surface.len())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::{
        BUNDLED_MANIFEST, BUNDLED_PACKAGE, LexicalCategory, LexicalLookup, Lexicon, LexiconError,
    };
    use crate::NormalizedText;

    #[test]
    fn bundled_index_preserves_every_entry_and_conflict() {
        let lexicon = Lexicon::bundled().expect("bundled lexicon");
        assert_eq!(lexicon.len(), 33);
        let duplicate = lexicon
            .entries()
            .windows(2)
            .find(|pair| pair[0].surface() == pair[1].surface())
            .expect("source-backed duplicate surface");
        let lookup = lexicon.lookup(duplicate[0].surface());
        let LexicalLookup::Conflict(values) = lookup else {
            panic!("duplicate surface did not remain a conflict");
        };
        assert!(!lookup.is_empty());
        assert_eq!(values.len(), 2);
        assert_eq!(
            values
                .iter()
                .map(LexicalAnalysis::category)
                .collect::<BTreeSet<_>>(),
            BTreeSet::from([LexicalCategory::Noun, LexicalCategory::Verb])
        );
    }

    #[test]
    fn lookup_is_exact_and_identity_index_is_complete() {
        let lexicon = Lexicon::bundled().expect("bundled lexicon");
        let unknown = lexicon.lookup("FIXTURE_TECNICA.unknown");
        assert!(unknown.is_unknown());
        assert!(unknown.is_empty());
        for entry in lexicon.entries() {
            assert_eq!(
                lexicon
                    .analysis(entry.source().source_id(), entry.analysis_id())
                    .expect("identity lookup"),
                entry
            );
            assert!(!entry.transformations().is_empty());
            assert_eq!(entry.derivative_license(), "Apache-2.0");
        }
    }

    #[test]
    fn token_lookup_uses_the_exact_normalized_surface() {
        let lexicon = Lexicon::bundled().expect("bundled lexicon");
        let unique = lexicon
            .entries()
            .iter()
            .find(|entry| matches!(lexicon.lookup(entry.surface()), LexicalLookup::Unique(_)))
            .expect("unique source-backed surface");
        let text = NormalizedText::new(unique.surface().to_owned()).expect("normalized source");
        let tokenization = text.tokenize().expect("tokenization");
        assert_eq!(tokenization.len(), 1);
        assert_eq!(
            lexicon
                .lookup_token(&tokenization.tokens()[0], &text)
                .expect("token lookup")
                .len(),
            1
        );
    }

    #[test]
    fn corrupt_artifact_never_builds_a_partial_index() {
        let mut package = BUNDLED_PACKAGE.to_vec();
        package[0] ^= 1;
        assert_eq!(
            Lexicon::from_artifact(&package, BUNDLED_MANIFEST).expect_err("corrupt package"),
            LexiconError::InvalidArtifact
        );

        let mut manifest = BUNDLED_MANIFEST.to_vec();
        manifest.push(b' ');
        assert_eq!(
            Lexicon::from_artifact(BUNDLED_PACKAGE, &manifest).expect_err("trailing manifest"),
            LexiconError::InvalidArtifact
        );
    }

    #[test]
    fn debug_output_omits_lexical_text() {
        let lexicon = Lexicon::bundled().expect("bundled lexicon");
        let entry = lexicon
            .entries()
            .iter()
            .find(|entry| entry.surface().len() > 5 && entry.lemma().len() > 5)
            .expect("long source-backed entry");
        let output = format!("{entry:?}");
        assert!(!output.contains(entry.surface()));
        assert!(!output.contains(entry.lemma()));
    }

    use super::LexicalAnalysis;
}
