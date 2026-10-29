use crate::{
    CollectionKind, CoreError, DuplicateKind, Hypothesis, MAX_CLARIFICATION_OPTIONS, OptionId,
    Plan, RequestText,
};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AbstentionReason {
    InsufficientEvidence,
    Ambiguous,
    Unsupported,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClarificationOption {
    id: OptionId,
    hypothesis: Hypothesis,
}

impl ClarificationOption {
    #[must_use]
    pub const fn new(id: OptionId, hypothesis: Hypothesis) -> Self {
        Self { id, hypothesis }
    }

    #[must_use]
    pub const fn id(&self) -> &OptionId {
        &self.id
    }

    #[must_use]
    pub const fn hypothesis(&self) -> &Hypothesis {
        &self.hypothesis
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Clarification {
    options: Vec<ClarificationOption>,
}

impl Clarification {
    pub fn new(
        source: &RequestText,
        mut options: Vec<ClarificationOption>,
    ) -> Result<Self, CoreError> {
        if options.is_empty() {
            return Err(CoreError::EmptyCollection {
                kind: CollectionKind::ClarificationOptions,
            });
        }
        if options.len() > MAX_CLARIFICATION_OPTIONS {
            return Err(CoreError::CollectionTooLarge {
                kind: CollectionKind::ClarificationOptions,
                limit: MAX_CLARIFICATION_OPTIONS as u16,
            });
        }
        if options.iter().any(|option| {
            option
                .hypothesis
                .evidence()
                .iter()
                .any(|span| !span.belongs_to(source))
        }) {
            return Err(CoreError::SpanSourceMismatch);
        }

        options.sort_by(|left, right| left.id.cmp(&right.id));
        if options.windows(2).any(|pair| pair[0].id == pair[1].id) {
            return Err(CoreError::Duplicate {
                kind: DuplicateKind::ClarificationOption,
            });
        }
        Ok(Self { options })
    }

    #[must_use]
    pub fn options(&self) -> &[ClarificationOption] {
        &self.options
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SemanticOutcome {
    Plan(Plan),
    Clarification(Clarification),
    Abstention(AbstentionReason),
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Confidence, IntentId};

    fn option(source: &RequestText, suffix: &str) -> ClarificationOption {
        ClarificationOption::new(
            OptionId::new(&format!("fixture_tecnica:option_{suffix}")).expect("ID"),
            Hypothesis::new(
                source,
                IntentId::new(&format!("fixture_tecnica:intent_{suffix}")).expect("ID"),
                Confidence::from_basis_points(5_000).expect("score"),
                vec![source.span(0, 1).expect("span")],
            )
            .expect("hypothesis"),
        )
    }

    #[test]
    fn clarification_options_are_nonempty_unique_and_canonical() {
        let source = RequestText::new("FIXTURE_TECNICA_A".into()).expect("source");
        assert_eq!(
            Clarification::new(&source, Vec::new()),
            Err(CoreError::EmptyCollection {
                kind: CollectionKind::ClarificationOptions
            })
        );

        let first = option(&source, "a");
        let second = option(&source, "b");
        let clarification =
            Clarification::new(&source, vec![second, first.clone()]).expect("clarification");
        assert_eq!(
            clarification.options()[0].id().as_str(),
            "fixture_tecnica:option_a"
        );
        assert_eq!(
            Clarification::new(&source, vec![first.clone(), first]),
            Err(CoreError::Duplicate {
                kind: DuplicateKind::ClarificationOption
            })
        );
    }
}
