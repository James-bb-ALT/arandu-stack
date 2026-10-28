use std::collections::BTreeMap;

use ha_catalog::CatalogSnapshot;
use intent_engine::{IntentEngine, RecognitionOutcome};
use nlu_core::{ComposedPlan, EvidenceKind, GraphExecutionClass, Polarity, RequestText, SlotValue};
use plan_engine::{
    CompositionOutcome, PLAN_COMPOSITION_ALGORITHM_ID, PLAN_COMPOSITION_SCHEMA_ID, PlanEngine,
};
use serde_json::{Map, Value};

use crate::{
    Result,
    error::{engine_failure, reconciliation_error},
    oracle::GoldCase,
    schema::{
        SemanticArgumentShare, SemanticEvidence, SemanticIndependentPair, SemanticNode,
        SemanticPlan, SemanticRelation, SemanticSlot, SemanticSpan,
    },
};

pub(crate) const COMPOSER_SCHEMA_ID: &str = PLAN_COMPOSITION_SCHEMA_ID;
pub(crate) const COMPOSER_ALGORITHM_ID: &str = PLAN_COMPOSITION_ALGORITHM_ID;

pub(crate) struct Engines {
    recognizer: IntentEngine,
    composer: PlanEngine,
}

impl Engines {
    pub(crate) fn new() -> Result<Self> {
        Ok(Self {
            recognizer: IntentEngine::bundled()
                .map_err(|_| engine_failure("bundled intent engine"))?,
            composer: PlanEngine::bundled().map_err(|_| engine_failure("bundled plan engine"))?,
        })
    }

    pub(crate) fn evaluate(
        &self,
        case: &GoldCase,
        snapshot: &CatalogSnapshot,
    ) -> Result<Prediction> {
        let source = RequestText::new(case.utterance.clone())
            .map_err(|_| reconciliation_error("evaluation request text"))?;
        let recognition = self.recognizer.recognize(&source);
        let recognition_bytes = match &recognition {
            Ok(outcome) => outcome
                .canonical_bytes(&source)
                .map_err(|_| reconciliation_error("recognizer canonical bytes"))?,
            Err(error) => format!("error:{:?}\n", error.code()).into_bytes(),
        };

        match recognition {
            Ok(RecognitionOutcome::Match(intent_match)) => {
                let intent_exact = intent_match.intent().as_str() == case.expected_intent_id;
                match self.composer.compose(&source, &intent_match, snapshot) {
                    Ok(CompositionOutcome::Plan(plan)) => {
                        let first = plan
                            .canonical_bytes()
                            .map_err(|_| reconciliation_error("plan canonical bytes"))?;
                        let second = plan
                            .canonical_bytes()
                            .map_err(|_| reconciliation_error("plan canonical replay"))?;
                        if first != second {
                            return Err(reconciliation_error("nondeterministic plan bytes"));
                        }
                        let semantic = semantic_plan(&source, &plan)?;
                        let composition_bytes = framed_bytes(b"plan", &first)?;
                        Ok(Prediction {
                            recognizer: RecognizerDisposition::Match,
                            composer: ComposerDisposition::Plan,
                            intent_exact,
                            final_outcome: PredictedOutcome::Plan {
                                semantic,
                                canonical_bytes: first,
                            },
                            digest_bytes: combine_prediction_bytes(
                                &recognition_bytes,
                                &composition_bytes,
                            )?,
                        })
                    }
                    Ok(CompositionOutcome::EntityClarification(clarification)) => {
                        let mut ids = clarification
                            .options()
                            .iter()
                            .map(|option| option.entity().id().as_str())
                            .collect::<Vec<_>>();
                        ids.sort_unstable();
                        let mut composition_bytes = b"entity_clarification\0".to_vec();
                        for id in ids {
                            composition_bytes.extend_from_slice(id.as_bytes());
                            composition_bytes.push(0);
                        }
                        Ok(Prediction {
                            recognizer: RecognizerDisposition::Match,
                            composer: ComposerDisposition::Clarification,
                            intent_exact,
                            final_outcome: PredictedOutcome::Clarification,
                            digest_bytes: combine_prediction_bytes(
                                &recognition_bytes,
                                &composition_bytes,
                            )?,
                        })
                    }
                    Ok(CompositionOutcome::Abstention(reason)) => {
                        let composition_bytes =
                            format!("abstention:{}\n", reason.code()).into_bytes();
                        Ok(Prediction {
                            recognizer: RecognizerDisposition::Match,
                            composer: ComposerDisposition::Abstention,
                            intent_exact,
                            final_outcome: PredictedOutcome::Abstention(reason.code().to_owned()),
                            digest_bytes: combine_prediction_bytes(
                                &recognition_bytes,
                                &composition_bytes,
                            )?,
                        })
                    }
                    Err(error) => {
                        let composition_bytes =
                            format!("error:{}\n", error.code().code()).into_bytes();
                        Ok(Prediction {
                            recognizer: RecognizerDisposition::Match,
                            composer: ComposerDisposition::Error,
                            intent_exact,
                            final_outcome: PredictedOutcome::Error,
                            digest_bytes: combine_prediction_bytes(
                                &recognition_bytes,
                                &composition_bytes,
                            )?,
                        })
                    }
                }
            }
            Ok(RecognitionOutcome::Clarification(_)) => Ok(Prediction {
                recognizer: RecognizerDisposition::Clarification,
                composer: ComposerDisposition::NotRun,
                intent_exact: false,
                final_outcome: PredictedOutcome::Clarification,
                digest_bytes: combine_prediction_bytes(&recognition_bytes, b"not_run\n")?,
            }),
            Ok(RecognitionOutcome::Abstention(reason)) => Ok(Prediction {
                recognizer: RecognizerDisposition::Abstention,
                composer: ComposerDisposition::NotRun,
                intent_exact: false,
                final_outcome: PredictedOutcome::Abstention(format!(
                    "recognizer:{}",
                    reason.code()
                )),
                digest_bytes: combine_prediction_bytes(&recognition_bytes, b"not_run\n")?,
            }),
            Err(_) => Ok(Prediction {
                recognizer: RecognizerDisposition::Error,
                composer: ComposerDisposition::NotRun,
                intent_exact: false,
                final_outcome: PredictedOutcome::Error,
                digest_bytes: combine_prediction_bytes(&recognition_bytes, b"not_run\n")?,
            }),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) enum RecognizerDisposition {
    Match,
    Clarification,
    Abstention,
    Error,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) enum ComposerDisposition {
    Plan,
    Clarification,
    Abstention,
    NotRun,
    Error,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum PredictedOutcome {
    Plan {
        semantic: SemanticPlan,
        canonical_bytes: Vec<u8>,
    },
    Clarification,
    Abstention(String),
    Error,
}

pub(crate) struct Prediction {
    pub(crate) recognizer: RecognizerDisposition,
    pub(crate) composer: ComposerDisposition,
    pub(crate) intent_exact: bool,
    pub(crate) final_outcome: PredictedOutcome,
    pub(crate) digest_bytes: Vec<u8>,
}

fn semantic_plan(source: &RequestText, composed: &ComposedPlan) -> Result<SemanticPlan> {
    let clauses = composed
        .clauses()
        .iter()
        .map(|clause| (clause.node().as_str(), clause))
        .collect::<BTreeMap<_, _>>();
    if clauses.len() != composed.plan().nodes().len() {
        return Err(reconciliation_error("predicted clause inventory"));
    }
    let nodes = composed
        .plan()
        .nodes()
        .iter()
        .map(|node| {
            let clause = clauses
                .get(node.id().as_str())
                .ok_or_else(|| reconciliation_error("predicted node clause"))?;
            let slots = node
                .slots()
                .iter()
                .map(|slot| {
                    let (kind, value) = match slot.value() {
                        SlotValue::EvidenceText(span) => (
                            "text",
                            Value::String(
                                span.slice(source)
                                    .map_err(|_| {
                                        reconciliation_error("predicted text slot source")
                                    })?
                                    .to_owned(),
                            ),
                        ),
                        SlotValue::Integer(value) => ("integer", Value::from(*value)),
                        SlotValue::Boolean(value) => ("boolean", Value::from(*value)),
                        SlotValue::Entity(entity) => {
                            let mut value = Map::new();
                            value.insert(
                                "id".to_owned(),
                                Value::String(entity.id().as_str().to_owned()),
                            );
                            value.insert(
                                "catalog_generation".to_owned(),
                                Value::from(entity.generation().get()),
                            );
                            ("entity", Value::Object(value))
                        }
                    };
                    Ok(SemanticSlot {
                        id: slot.id().as_str().to_owned(),
                        kind: kind.to_owned(),
                        value,
                    })
                })
                .collect::<Result<Vec<_>>>()?;
            let evidence = clause
                .evidence()
                .iter()
                .map(|atom| {
                    let (kind, slot) = match atom.kind() {
                        EvidenceKind::Predicate => ("predicate", None),
                        EvidenceKind::Argument(slot) => {
                            ("argument", Some(slot.as_str().to_owned()))
                        }
                        EvidenceKind::Negation => ("negation", None),
                    };
                    SemanticEvidence {
                        kind: kind.to_owned(),
                        slot,
                        begin_byte: atom.span().start(),
                        end_byte: atom.span().end(),
                    }
                })
                .collect();
            Ok(SemanticNode {
                id: node.id().as_str().to_owned(),
                intent: clause.intent().as_str().to_owned(),
                capability: node.capability().as_str().to_owned(),
                operation: node.operation().as_str().to_owned(),
                polarity: polarity_code(clause.polarity()).to_owned(),
                slots,
                evidence,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let relations = composed
        .relation_evidence()
        .iter()
        .map(|relation| SemanticRelation {
            from: relation.relation().from().as_str().to_owned(),
            to: relation.relation().to().as_str().to_owned(),
            kind: match relation.relation().kind() {
                nlu_core::RelationKind::Precedes => "precedes",
                nlu_core::RelationKind::Requires => "requires",
            }
            .to_owned(),
            evidence: spans(relation.evidence()),
        })
        .collect();
    let independent_pairs = composed
        .independent_pairs()
        .iter()
        .map(|pair| SemanticIndependentPair {
            left: pair.left().as_str().to_owned(),
            right: pair.right().as_str().to_owned(),
        })
        .collect();
    let argument_shares = composed
        .argument_shares()
        .iter()
        .map(|share| SemanticArgumentShare {
            from_node: share.from().node().as_str().to_owned(),
            from_slot: share.from().slot().as_str().to_owned(),
            to_node: share.to().node().as_str().to_owned(),
            to_slot: share.to().slot().as_str().to_owned(),
            evidence: spans(share.evidence()),
        })
        .collect();
    Ok(SemanticPlan {
        schema_version: PLAN_COMPOSITION_SCHEMA_ID.to_owned(),
        catalog_generation: composed.plan().catalog_generation().get(),
        execution_class: execution_class_code(composed.execution_class()).to_owned(),
        nodes,
        relations,
        independent_pairs,
        argument_shares,
    })
}

fn spans(values: &[nlu_core::Utf8Span]) -> Vec<SemanticSpan> {
    values
        .iter()
        .map(|span| SemanticSpan {
            begin_byte: span.start(),
            end_byte: span.end(),
        })
        .collect()
}

const fn execution_class_code(value: GraphExecutionClass) -> &'static str {
    match value {
        GraphExecutionClass::PartialSafe => "partial_safe",
        GraphExecutionClass::AtomicOnly => "atomic_only",
        GraphExecutionClass::NonExecutable => "non_executable",
    }
}

const fn polarity_code(value: Polarity) -> &'static str {
    match value {
        Polarity::Affirmed => "affirmed",
        Polarity::Negated => "negated",
    }
}

fn framed_bytes(tag: &[u8], bytes: &[u8]) -> Result<Vec<u8>> {
    let mut framed = Vec::with_capacity(tag.len().saturating_add(bytes.len()).saturating_add(9));
    framed.extend_from_slice(tag);
    framed.push(0);
    framed.extend_from_slice(
        &u64::try_from(bytes.len())
            .map_err(|_| reconciliation_error("prediction frame length"))?
            .to_be_bytes(),
    );
    framed.extend_from_slice(bytes);
    Ok(framed)
}

fn combine_prediction_bytes(recognition: &[u8], composition: &[u8]) -> Result<Vec<u8>> {
    let mut combined = framed_bytes(b"recognition", recognition)?;
    combined.extend_from_slice(&framed_bytes(b"composition", composition)?);
    Ok(combined)
}
