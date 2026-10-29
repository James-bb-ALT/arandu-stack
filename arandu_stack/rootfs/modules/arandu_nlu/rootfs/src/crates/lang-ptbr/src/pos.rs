use core::{fmt, slice};
use std::collections::{BTreeMap, BTreeSet};

use nlu_data::{
    pos::{PosEndpoint, PosModelIdentity, decode_pos_transition_package},
    sha256_hex,
};

use crate::{
    LexicalAnalysis, LexicalCategory, Morphology, MorphologyError, MorphologyLookup,
    NormalizedText, TextError, Token, TokenClass, TokenRule,
};

pub const POS_BASELINE_ID: &str = "lang-ptbr-independent-evidence-pos-baseline-v1";
pub const POS_TAGGER_ID: &str = "lang-ptbr-adjacent-transition-pos-v1";
pub const POS_MODEL_ID: &str = "p08-pos-transition-model-v1";
pub const POS_MODEL_PACKAGE_SHA256: &str =
    "23beb6dd464c4bd03d69bb374be210fd3a187661c37673194f466b4270b8020c";

const POS_MODEL_MANIFEST_SHA256: &str =
    "f89eaa786f37456d5e1d47e522ceea7bd96f41b272bdc9a6ca99407d7cc2b0bc";
const BUNDLED_MODEL_PACKAGE: &[u8] = include_bytes!("../../../data/pos/p08/package.bin");
const BUNDLED_MODEL_MANIFEST: &[u8] = include_bytes!("../../../data/pos/p08/package-manifest.json");
const MODEL_ALGORITHM_ID: &str = "adjacent-singleton-presence-noncascading-v1";
const MODEL_COMPILER_ID: &str = "nlu-data-pos-transition-compiler-v1";
const MODEL_CONFIG_ID: &str = "p08-pos-config-v1";
const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const CORPUS_VERSION: &str = "1.0.0";
const GENERATOR_ID: &str = "p02-generator-v1";
const SOURCE_LICENSE: &str = "Apache-2.0";
const LOCALE: &str = "pt-BR";
const SOURCE_MANIFEST_SHA256: &str =
    "d9e1ca32ec0f92aa6b5fc6c1d4f232f93389e0bf8031267d23daddf7287006c5";
const SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
const GENERATOR_SHA256: &str = "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
const ORIGINAL_POS_ARTIFACT_SHA256: &str =
    "85ad18caf3ae749d3ec0135c3c01ce6d754f831d395abdc44ff1c3643a18fac8";
const TRAIN_SLICE_SHA256: &str = "b78d4b79350638da0cacf2c6c027d07b37919a9e3bc82de654b4f1c0f0984461";
const TRAIN_CASE_DIGEST_SHA256: &str =
    "2d19927df47be5f79b98ee6efffa290b512633682b2635dbe7de265653a4f481";
const TRAIN_DOCUMENT_DIGEST_SHA256: &str =
    "31548f0c55c3c66d2560a46b1567a4a197c1cf443ddecbc8d1f502fd3a8e3ce8";
const TRAIN_ORIGIN_DIGEST_SHA256: &str =
    "7c9b3c0943f43dcc2327fcaec2edc61ff2eedc8e5a2c62f714c1c07cb388b757";
const TRAIN_SENTENCES: u64 = 80;
const TRAIN_DOCUMENTS: u64 = 8;
const TRAIN_TOKENS: u64 = 560;
const MODEL_LABELS: [&str; 5] = ["ADP", "DET", "NOUN", "NUM", "VERB"];
const MODEL_TRANSITIONS: u64 = 7;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum PosTag {
    Adjective,
    Adposition,
    Adverb,
    CoordinatingConjunction,
    Determiner,
    Noun,
    Number,
    Verb,
}

impl PosTag {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Adjective => "ADJ",
            Self::Adposition => "ADP",
            Self::Adverb => "ADV",
            Self::CoordinatingConjunction => "CCONJ",
            Self::Determiner => "DET",
            Self::Noun => "NOUN",
            Self::Number => "NUM",
            Self::Verb => "VERB",
        }
    }

    fn from_code(code: &str) -> Option<Self> {
        match code {
            "ADJ" => Some(Self::Adjective),
            "ADP" => Some(Self::Adposition),
            "ADV" => Some(Self::Adverb),
            "CCONJ" => Some(Self::CoordinatingConjunction),
            "DET" => Some(Self::Determiner),
            "NOUN" => Some(Self::Noun),
            "NUM" => Some(Self::Number),
            "VERB" => Some(Self::Verb),
            _ => None,
        }
    }

    const fn from_lexical(category: LexicalCategory) -> Self {
        match category {
            LexicalCategory::Adjective => Self::Adjective,
            LexicalCategory::Adposition => Self::Adposition,
            LexicalCategory::Adverb => Self::Adverb,
            LexicalCategory::CoordinatingConjunction => Self::CoordinatingConjunction,
            LexicalCategory::Determiner => Self::Determiner,
            LexicalCategory::Noun => Self::Noun,
            LexicalCategory::Verb => Self::Verb,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PosEvidenceKind {
    Lexical,
    NumericTokenClass,
}

#[derive(Clone, Eq, PartialEq)]
pub struct PosCandidate<'a> {
    tag: PosTag,
    lexical_analyses: Box<[&'a LexicalAnalysis]>,
    numeric_rule: Option<TokenRule>,
}

impl<'a> PosCandidate<'a> {
    #[must_use]
    pub const fn tag(&self) -> PosTag {
        self.tag
    }

    #[must_use]
    pub const fn evidence_kind(&self) -> PosEvidenceKind {
        if self.numeric_rule.is_some() {
            PosEvidenceKind::NumericTokenClass
        } else {
            PosEvidenceKind::Lexical
        }
    }

    #[must_use]
    pub fn lexical_analyses(&self) -> &[&'a LexicalAnalysis] {
        &self.lexical_analyses
    }

    #[must_use]
    pub const fn numeric_rule(&self) -> Option<TokenRule> {
        self.numeric_rule
    }

    fn lexical(tag: PosTag, analyses: Vec<&'a LexicalAnalysis>) -> Self {
        Self {
            tag,
            lexical_analyses: analyses.into_boxed_slice(),
            numeric_rule: None,
        }
    }

    fn numeric(rule: TokenRule) -> Self {
        Self {
            tag: PosTag::Number,
            lexical_analyses: Box::new([]),
            numeric_rule: Some(rule),
        }
    }
}

impl fmt::Debug for PosCandidate<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PosCandidate")
            .field("tag", &self.tag)
            .field("evidence_kind", &self.evidence_kind())
            .field("lexical_analysis_count", &self.lexical_analyses.len())
            .field("numeric_rule", &self.numeric_rule)
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub enum PosLookup<'a> {
    Unknown,
    Unique(PosCandidate<'a>),
    Ambiguous(Box<[PosCandidate<'a>]>),
}

impl<'a> PosLookup<'a> {
    #[must_use]
    pub const fn is_unknown(&self) -> bool {
        matches!(self, Self::Unknown)
    }

    #[must_use]
    pub fn candidates(&self) -> &[PosCandidate<'a>] {
        match self {
            Self::Unknown => &[],
            Self::Unique(candidate) => slice::from_ref(candidate),
            Self::Ambiguous(candidates) => candidates,
        }
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.candidates().len()
    }

    #[must_use]
    pub const fn is_empty(&self) -> bool {
        matches!(self, Self::Unknown)
    }

    fn unique_tag(&self) -> Option<PosTag> {
        match self {
            Self::Unique(candidate) => Some(candidate.tag()),
            Self::Unknown | Self::Ambiguous(_) => None,
        }
    }
}

impl fmt::Debug for PosLookup<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown => formatter.write_str("Unknown"),
            Self::Unique(candidate) => formatter.debug_tuple("Unique").field(candidate).finish(),
            Self::Ambiguous(candidates) => formatter
                .debug_tuple("Ambiguous")
                .field(&candidates.len())
                .finish(),
        }
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct PosTagging<'a> {
    lookups: Box<[PosLookup<'a>]>,
}

impl<'a> PosTagging<'a> {
    #[must_use]
    pub fn lookups(&self) -> &[PosLookup<'a>] {
        &self.lookups
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.lookups.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.lookups.is_empty()
    }
}

impl fmt::Debug for PosTagging<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PosTagging")
            .field("token_count", &self.lookups.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PosError {
    InvalidMorphology(MorphologyError),
    InvalidModel,
}

impl PosError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidMorphology(_) => "pos_invalid_morphology",
            Self::InvalidModel => "pos_invalid_model",
        }
    }
}

impl fmt::Display for PosError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for PosError {}

impl From<MorphologyError> for PosError {
    fn from(error: MorphologyError) -> Self {
        Self::InvalidMorphology(error)
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum ContextNode {
    Beginning,
    Tag(PosTag),
    End,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct TransitionModel {
    transitions: BTreeSet<(ContextNode, ContextNode)>,
}

impl TransitionModel {
    fn new(
        transitions: impl IntoIterator<Item = (ContextNode, ContextNode)>,
    ) -> Result<Self, PosError> {
        let transitions = transitions.into_iter().collect::<BTreeSet<_>>();
        if transitions.is_empty()
            || transitions.len() > 100
            || transitions.iter().any(|(left, right)| {
                matches!(left, ContextNode::End)
                    || matches!(right, ContextNode::Beginning)
                    || matches!((left, right), (ContextNode::Beginning, ContextNode::End))
            })
        {
            return Err(PosError::InvalidModel);
        }
        Ok(Self { transitions })
    }

    fn supports(&self, left: ContextNode, right: ContextNode) -> bool {
        self.transitions.contains(&(left, right))
    }
}

pub struct PosTagger {
    morphology: Morphology,
    model: TransitionModel,
}

impl PosTagger {
    pub fn bundled() -> Result<Self, PosError> {
        Ok(Self {
            morphology: Morphology::bundled()?,
            model: load_bundled_model()?,
        })
    }

    pub fn baseline<'a>(
        &'a self,
        tokens: &[Token],
        text: &NormalizedText,
    ) -> Result<PosTagging<'a>, TextError> {
        let mut lookups = Vec::with_capacity(tokens.len());
        for token in tokens {
            lookups.push(self.baseline_token(token, text)?);
        }
        Ok(PosTagging {
            lookups: lookups.into_boxed_slice(),
        })
    }

    pub fn tag<'a>(
        &'a self,
        tokens: &[Token],
        text: &NormalizedText,
    ) -> Result<PosTagging<'a>, TextError> {
        let baseline = self.baseline(tokens, text)?;
        let selections = baseline
            .lookups()
            .iter()
            .enumerate()
            .map(|(index, lookup)| self.contextual_selection(index, baseline.lookups(), lookup))
            .collect::<Vec<_>>();
        let mut lookups = Vec::with_capacity(baseline.len());
        for (lookup, selection) in baseline.lookups.into_vec().into_iter().zip(selections) {
            lookups.push(apply_selection(lookup, selection));
        }
        Ok(PosTagging {
            lookups: lookups.into_boxed_slice(),
        })
    }

    fn baseline_token<'a>(
        &'a self,
        token: &Token,
        text: &NormalizedText,
    ) -> Result<PosLookup<'a>, TextError> {
        let morphology = self.morphology.analyze_token(token, text)?;
        if matches!(morphology, MorphologyLookup::Unknown) {
            return Ok(if token.class() == TokenClass::Number {
                PosLookup::Unique(PosCandidate::numeric(token.rule()))
            } else {
                PosLookup::Unknown
            });
        }

        let mut by_tag = BTreeMap::<PosTag, Vec<&LexicalAnalysis>>::new();
        for analysis in morphology.analyses() {
            by_tag
                .entry(PosTag::from_lexical(analysis.category()))
                .or_default()
                .push(analysis);
        }
        let candidates = by_tag
            .into_iter()
            .map(|(tag, analyses)| PosCandidate::lexical(tag, analyses))
            .collect::<Vec<_>>();
        Ok(match candidates.len() {
            0 => PosLookup::Unknown,
            1 => PosLookup::Unique(candidates.into_iter().next().expect("one candidate")),
            _ => PosLookup::Ambiguous(candidates.into_boxed_slice()),
        })
    }

    fn contextual_selection(
        &self,
        index: usize,
        baseline: &[PosLookup<'_>],
        lookup: &PosLookup<'_>,
    ) -> Option<PosTag> {
        let PosLookup::Ambiguous(candidates) = lookup else {
            return None;
        };
        let left = if index == 0 {
            Some(ContextNode::Beginning)
        } else {
            baseline[index - 1].unique_tag().map(ContextNode::Tag)
        };
        let right = if index + 1 == baseline.len() {
            Some(ContextNode::End)
        } else {
            baseline[index + 1].unique_tag().map(ContextNode::Tag)
        };

        let mut supported = BTreeSet::new();
        for candidate in candidates {
            let tag = ContextNode::Tag(candidate.tag());
            if left.is_some_and(|context| self.model.supports(context, tag))
                || right.is_some_and(|context| self.model.supports(tag, context))
            {
                supported.insert(candidate.tag());
            }
        }
        if supported.len() == 1 {
            supported.into_iter().next()
        } else {
            None
        }
    }
}

impl fmt::Debug for PosTagger {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PosTagger")
            .field("transition_count", &self.model.transitions.len())
            .finish()
    }
}

fn apply_selection<'a>(lookup: PosLookup<'a>, selection: Option<PosTag>) -> PosLookup<'a> {
    match (lookup, selection) {
        (PosLookup::Ambiguous(candidates), Some(selection)) => {
            let matches = candidates
                .iter()
                .enumerate()
                .filter_map(|(index, candidate)| (candidate.tag() == selection).then_some(index))
                .collect::<Vec<_>>();
            if matches.len() != 1 {
                return PosLookup::Ambiguous(candidates);
            }
            let candidate = candidates.into_vec().remove(matches[0]);
            PosLookup::Unique(candidate)
        }
        (lookup, _) => lookup,
    }
}

fn load_bundled_model() -> Result<TransitionModel, PosError> {
    if sha256_hex(BUNDLED_MODEL_PACKAGE).map_err(|_| PosError::InvalidModel)?
        != POS_MODEL_PACKAGE_SHA256
        || sha256_hex(BUNDLED_MODEL_MANIFEST).map_err(|_| PosError::InvalidModel)?
            != POS_MODEL_MANIFEST_SHA256
    {
        return Err(PosError::InvalidModel);
    }
    let identity = PosModelIdentity::new(
        POS_MODEL_ID,
        MODEL_ALGORITHM_ID,
        MODEL_COMPILER_ID,
        MODEL_CONFIG_ID,
    );
    let package =
        decode_pos_transition_package(BUNDLED_MODEL_PACKAGE, BUNDLED_MODEL_MANIFEST, &identity)
            .map_err(|_| PosError::InvalidModel)?;
    let source = package.manifest().source();
    let training = package.manifest().training();
    if source.source_id() != SOURCE_ID
        || source.corpus_version() != CORPUS_VERSION
        || source.generator_id() != GENERATOR_ID
        || source.source_license() != SOURCE_LICENSE
        || source.locale() != LOCALE
        || source.source_manifest_sha256() != SOURCE_MANIFEST_SHA256
        || source.specification_sha256() != SPECIFICATION_SHA256
        || source.generator_sha256() != GENERATOR_SHA256
        || source.original_pos_artifact_sha256() != ORIGINAL_POS_ARTIFACT_SHA256
        || training.physical_train_slice_sha256() != TRAIN_SLICE_SHA256
        || training.train_case_digest_sha256() != TRAIN_CASE_DIGEST_SHA256
        || training.train_document_digest_sha256() != TRAIN_DOCUMENT_DIGEST_SHA256
        || training.train_origin_digest_sha256() != TRAIN_ORIGIN_DIGEST_SHA256
        || training.train_sentence_count() != TRAIN_SENTENCES
        || training.train_document_count() != TRAIN_DOCUMENTS
        || training.train_token_count() != TRAIN_TOKENS
        || package.labels().iter().map(String::as_str).ne(MODEL_LABELS)
        || package.manifest().transition_count() != MODEL_TRANSITIONS
    {
        return Err(PosError::InvalidModel);
    }

    let transitions = package
        .transitions()
        .iter()
        .map(|transition| {
            Ok((
                context_node(transition.from())?,
                context_node(transition.to())?,
            ))
        })
        .collect::<Result<Vec<_>, PosError>>()?;
    TransitionModel::new(transitions)
}

fn context_node(endpoint: &PosEndpoint) -> Result<ContextNode, PosError> {
    match endpoint {
        PosEndpoint::Bos => Ok(ContextNode::Beginning),
        PosEndpoint::Eos => Ok(ContextNode::End),
        PosEndpoint::Label(label) => PosTag::from_code(label)
            .map(ContextNode::Tag)
            .ok_or(PosError::InvalidModel),
    }
}

#[cfg(test)]
mod tests {
    use super::{ContextNode, PosEvidenceKind, PosLookup, PosTag, PosTagger, TransitionModel};
    use crate::{Morphology, NormalizedText};

    fn tagger(transitions: &[(ContextNode, ContextNode)]) -> PosTagger {
        PosTagger {
            morphology: Morphology::bundled().expect("bundled morphology"),
            model: TransitionModel::new(transitions.iter().copied()).expect("transition model"),
        }
    }

    fn lookups<'a>(
        tagger: &'a PosTagger,
        text: &NormalizedText,
        selected: bool,
    ) -> super::PosTagging<'a> {
        let tokens = text.tokenize().expect("tokenization");
        if selected {
            tagger.tag(tokens.tokens(), text).expect("selected POS")
        } else {
            tagger
                .baseline(tokens.tokens(), text)
                .expect("baseline POS")
        }
    }

    #[test]
    fn baseline_preserves_lexical_conflict() {
        let tagger = tagger(&[(ContextNode::Beginning, ContextNode::Tag(PosTag::Determiner))]);
        let text = NormalizedText::new("liga".to_owned()).expect("text");
        let tagged = lookups(&tagger, &text, false);
        let PosLookup::Ambiguous(candidates) = &tagged.lookups()[0] else {
            panic!("source conflict was not preserved");
        };
        assert_eq!(
            candidates
                .iter()
                .map(super::PosCandidate::tag)
                .collect::<Vec<_>>(),
            [PosTag::Noun, PosTag::Verb]
        );
        assert!(
            candidates
                .iter()
                .all(|candidate| candidate.evidence_kind() == PosEvidenceKind::Lexical)
        );
    }

    #[test]
    fn source_backed_number_class_maps_to_num() {
        let tagger = tagger(&[(ContextNode::Beginning, ContextNode::Tag(PosTag::Number))]);
        let text = NormalizedText::new("1".to_owned()).expect("text");
        let tagged = lookups(&tagger, &text, false);
        let PosLookup::Unique(candidate) = &tagged.lookups()[0] else {
            panic!("number was not uniquely tagged");
        };
        assert_eq!(candidate.tag(), PosTag::Number);
        assert_eq!(
            candidate.evidence_kind(),
            PosEvidenceKind::NumericTokenClass
        );
        assert!(candidate.lexical_analyses().is_empty());
        assert!(candidate.numeric_rule().is_some());
    }

    #[test]
    fn unique_left_context_narrows_without_cascading() {
        let tagger = tagger(&[(
            ContextNode::Tag(PosTag::Determiner),
            ContextNode::Tag(PosTag::Noun),
        )]);
        let text = NormalizedText::new("a liga liga".to_owned()).expect("text");
        let baseline = lookups(&tagger, &text, false);
        assert!(matches!(baseline.lookups()[1], PosLookup::Ambiguous(_)));
        assert!(matches!(baseline.lookups()[2], PosLookup::Ambiguous(_)));

        let selected = lookups(&tagger, &text, true);
        assert_eq!(selected.lookups()[1].candidates()[0].tag(), PosTag::Noun);
        assert!(matches!(selected.lookups()[2], PosLookup::Ambiguous(_)));
    }

    #[test]
    fn contradictory_sides_preserve_complete_ambiguity() {
        let tagger = tagger(&[
            (
                ContextNode::Tag(PosTag::Determiner),
                ContextNode::Tag(PosTag::Noun),
            ),
            (
                ContextNode::Tag(PosTag::Verb),
                ContextNode::Tag(PosTag::Determiner),
            ),
        ]);
        let text = NormalizedText::new("a liga a".to_owned()).expect("text");
        let selected = lookups(&tagger, &text, true);
        assert_eq!(
            selected.lookups()[1]
                .candidates()
                .iter()
                .map(super::PosCandidate::tag)
                .collect::<Vec<_>>(),
            [PosTag::Noun, PosTag::Verb]
        );
    }

    #[test]
    fn unknown_is_payload_free_and_blocks_context() {
        let tagger = tagger(&[(
            ContextNode::Tag(PosTag::Determiner),
            ContextNode::Tag(PosTag::Noun),
        )]);
        let text =
            NormalizedText::new("FIXTURE_TECNICA liga".to_owned()).expect("technical fixture");
        let selected = lookups(&tagger, &text, true);
        assert!(selected.lookups()[0].is_unknown());
        assert!(selected.lookups()[0].is_empty());
        assert!(selected.lookups()[0].candidates().is_empty());
        assert!(matches!(selected.lookups()[1], PosLookup::Ambiguous(_)));
    }

    #[test]
    fn transition_model_rejects_invalid_boundaries() {
        assert!(
            TransitionModel::new([(ContextNode::End, ContextNode::Tag(PosTag::Noun))]).is_err()
        );
        assert!(TransitionModel::new([(ContextNode::Beginning, ContextNode::End)]).is_err());
    }

    #[test]
    fn debug_output_omits_token_text() {
        let tagger = tagger(&[(
            ContextNode::Tag(PosTag::Determiner),
            ContextNode::Tag(PosTag::Noun),
        )]);
        let text = NormalizedText::new("a liga".to_owned()).expect("text");
        let tagged = lookups(&tagger, &text, true);
        let debug = format!("{tagged:?}");
        assert!(!debug.contains("liga"));
        assert!(debug.contains("token_count"));
    }

    #[test]
    fn supported_tag_codes_are_closed() {
        for tag in [
            PosTag::Adjective,
            PosTag::Adposition,
            PosTag::Adverb,
            PosTag::CoordinatingConjunction,
            PosTag::Determiner,
            PosTag::Noun,
            PosTag::Number,
            PosTag::Verb,
        ] {
            assert_eq!(PosTag::from_code(tag.code()), Some(tag));
        }
        assert_eq!(PosTag::from_code("X"), None);
    }

    #[test]
    fn bundled_model_is_pinned_and_available() {
        let tagger = PosTagger::bundled().expect("bundled POS tagger");
        assert_eq!(tagger.model.transitions.len(), 7);
    }
}
