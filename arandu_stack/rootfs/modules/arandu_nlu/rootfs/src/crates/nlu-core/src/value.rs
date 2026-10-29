use crate::{
    CollectionKind, CoreError, DuplicateKind, EntityId, IntentId, MAX_EVIDENCE_SPANS,
    MAX_HYPOTHESES, RequestText, SlotId, Utf8Span,
};
use core::num::NonZeroU64;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Confidence(u16);

impl Confidence {
    pub const MAX_BASIS_POINTS: u16 = 10_000;

    pub fn from_basis_points(value: u16) -> Result<Self, CoreError> {
        if value > Self::MAX_BASIS_POINTS {
            return Err(CoreError::ScoreOutOfRange);
        }
        Ok(Self(value))
    }

    #[must_use]
    pub const fn basis_points(self) -> u16 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CatalogGeneration(NonZeroU64);

impl CatalogGeneration {
    pub fn new(value: u64) -> Result<Self, CoreError> {
        NonZeroU64::new(value)
            .map(Self)
            .ok_or(CoreError::InvalidCatalogGeneration)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct EntityRef {
    id: EntityId,
    generation: CatalogGeneration,
}

impl EntityRef {
    #[must_use]
    pub const fn new(id: EntityId, generation: CatalogGeneration) -> Self {
        Self { id, generation }
    }

    #[must_use]
    pub const fn id(&self) -> &EntityId {
        &self.id
    }

    #[must_use]
    pub const fn generation(&self) -> CatalogGeneration {
        self.generation
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SlotValue {
    EvidenceText(Utf8Span),
    Integer(i64),
    Boolean(bool),
    Entity(EntityRef),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Slot {
    id: SlotId,
    value: SlotValue,
}

impl Slot {
    #[must_use]
    pub const fn new(id: SlotId, value: SlotValue) -> Self {
        Self { id, value }
    }

    #[must_use]
    pub const fn id(&self) -> &SlotId {
        &self.id
    }

    #[must_use]
    pub const fn value(&self) -> &SlotValue {
        &self.value
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Hypothesis {
    intent: IntentId,
    confidence: Confidence,
    evidence: Vec<Utf8Span>,
}

impl Hypothesis {
    pub fn new(
        source: &crate::RequestText,
        intent: IntentId,
        confidence: Confidence,
        mut evidence: Vec<Utf8Span>,
    ) -> Result<Self, CoreError> {
        if evidence.is_empty() {
            return Err(CoreError::EmptyCollection {
                kind: CollectionKind::Evidence,
            });
        }
        if evidence.len() > MAX_EVIDENCE_SPANS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::Evidence,
                limit: MAX_EVIDENCE_SPANS as u16,
            });
        }
        if evidence.iter().any(|span| !span.belongs_to(source)) {
            return Err(CoreError::SpanSourceMismatch);
        }
        evidence.sort();
        if evidence.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::Evidence,
            });
        }
        Ok(Self {
            intent,
            confidence,
            evidence,
        })
    }

    #[must_use]
    pub const fn intent(&self) -> &IntentId {
        &self.intent
    }

    #[must_use]
    pub const fn confidence(&self) -> Confidence {
        self.confidence
    }

    #[must_use]
    pub fn evidence(&self) -> &[Utf8Span] {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HypothesisSet {
    hypotheses: Vec<Hypothesis>,
}

impl HypothesisSet {
    pub fn new(source: &RequestText, mut hypotheses: Vec<Hypothesis>) -> Result<Self, CoreError> {
        if hypotheses.len() > MAX_HYPOTHESES {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::Hypotheses,
                limit: MAX_HYPOTHESES as u16,
            });
        }
        if hypotheses.iter().any(|hypothesis| {
            hypothesis
                .evidence()
                .iter()
                .any(|span| !span.belongs_to(source))
        }) {
            return Err(CoreError::SpanSourceMismatch);
        }
        hypotheses.sort_by(|left, right| {
            right
                .confidence()
                .cmp(&left.confidence())
                .then_with(|| left.intent().cmp(right.intent()))
        });
        if hypotheses
            .windows(2)
            .any(|pair| pair[0].intent() == pair[1].intent())
        {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::Hypothesis,
            });
        }
        Ok(Self { hypotheses })
    }

    #[must_use]
    pub fn as_slice(&self) -> &[Hypothesis] {
        &self.hypotheses
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.hypotheses.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::RequestText;

    #[test]
    fn bounds_scores_and_catalog_generations() {
        assert_eq!(
            Confidence::from_basis_points(10_001),
            Err(CoreError::ScoreOutOfRange)
        );
        assert_eq!(
            CatalogGeneration::new(0),
            Err(CoreError::InvalidCatalogGeneration)
        );
    }

    #[test]
    fn canonicalizes_hypothesis_evidence() {
        let source = RequestText::new("FIXTURE_TECNICA_AB".into()).expect("valid source");
        let first = source.span(0, 1).expect("span");
        let second = source.span(1, 2).expect("span");
        let hypothesis = Hypothesis::new(
            &source,
            IntentId::new("fixture_tecnica:intent").expect("ID"),
            Confidence::from_basis_points(5_000).expect("score"),
            vec![second.clone(), first.clone()],
        )
        .expect("hypothesis");
        assert_eq!(hypothesis.evidence(), &[first, second]);
    }

    #[test]
    fn rejects_duplicate_or_foreign_hypothesis_evidence() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("valid source");
        let foreign = RequestText::new("FIXTURE_TECNICA_A".into()).expect("valid source");
        let span = source.span(0, 1).expect("span");
        let intent = IntentId::new("fixture_tecnica:intent").expect("ID");
        let score = Confidence::from_basis_points(5_000).expect("score");

        assert_eq!(
            Hypothesis::new(&source, intent.clone(), score, vec![span.clone(), span]),
            Err(CoreError::Duplicate {
                kind: DuplicateKind::Evidence
            })
        );
        assert_eq!(
            Hypothesis::new(
                &source,
                intent,
                score,
                vec![foreign.span(0, 1).expect("span")]
            ),
            Err(CoreError::SpanSourceMismatch)
        );
    }

    #[test]
    fn hypothesis_sets_are_bounded_unique_and_deterministically_ranked() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("source");
        let span = source.span(0, 1).expect("span");
        let lower = Hypothesis::new(
            &source,
            IntentId::new("fixture_tecnica:intent_b").expect("ID"),
            Confidence::from_basis_points(1).expect("score"),
            vec![span.clone()],
        )
        .expect("hypothesis");
        let higher = Hypothesis::new(
            &source,
            IntentId::new("fixture_tecnica:intent_a").expect("ID"),
            Confidence::from_basis_points(2).expect("score"),
            vec![span],
        )
        .expect("hypothesis");
        let set = HypothesisSet::new(&source, vec![lower, higher.clone()]).expect("set");
        assert_eq!(set.as_slice()[0], higher);

        assert_eq!(
            HypothesisSet::new(&source, vec![higher.clone(), higher]),
            Err(CoreError::Duplicate {
                kind: DuplicateKind::Hypothesis
            })
        );
    }
}
