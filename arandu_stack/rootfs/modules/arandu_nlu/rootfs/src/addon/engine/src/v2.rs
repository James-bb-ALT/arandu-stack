//! Versioned full interpretation: extraction plus resolution plus atomic plans.
//!
//! Additive v2 path; protocol v1 (`interpret`) is untouched. Effects only:
//! queries stay v1-served. Inherited (ellipsis) mentions abstain because the
//! area-grammar bridging they need has no exact-evidence path in ER
//! snapshots. Any unlinked reference poisons the whole request: absence of a
//! constraint must never stand in for an interpretation failure. Action
//! compatibility travels as a mandatory capability constraint carrying the
//! segment action, mirroring v1 domain checks.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{
    Action, Operation, ResolutionCatalog,
    extraction::{ConstraintKind, extract},
    model::{MAX_OPERATIONS, MAX_TEXT_BYTES, MAX_TEXT_CHARS},
    normalize::{normalize, normalize_with_spans},
    parser::{effect_segments_spanned, percentage_parts, verb_action},
    resolution::{
        ResolutionConstraints, ResolutionOutcome, ResolutionRequest, index_snapshot, resolve_entity,
    },
};

pub const PROTOCOL_VERSION_V2: u8 = 2;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct InterpretRequestV2 {
    pub text: String,
    pub snapshot: ResolutionCatalog,
    pub generation: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum InterpretResponseV2 {
    Ambiguous {
        version: u8,
    },
    NoMatch {
        version: u8,
    },
    Plan {
        operations: Vec<Operation>,
        version: u8,
    },
}

impl InterpretResponseV2 {
    const fn ambiguous() -> Self {
        Self::Ambiguous {
            version: PROTOCOL_VERSION_V2,
        }
    }

    const fn no_match() -> Self {
        Self::NoMatch {
            version: PROTOCOL_VERSION_V2,
        }
    }

    fn plan(operations: Vec<Operation>) -> Self {
        Self::Plan {
            operations,
            version: PROTOCOL_VERSION_V2,
        }
    }
}

#[must_use]
pub fn interpret_v2(request: &InterpretRequestV2) -> InterpretResponseV2 {
    if request.text.is_empty()
        || request.text.len() > MAX_TEXT_BYTES
        || request.text.chars().count() > MAX_TEXT_CHARS
        || request.text.chars().any(char::is_control)
    {
        return InterpretResponseV2::no_match();
    }
    if request.generation != request.snapshot.generation {
        return InterpretResponseV2::no_match();
    }
    if index_snapshot(&request.snapshot).is_none() {
        return InterpretResponseV2::no_match();
    }
    let Some(extraction) = extract(&request.text, &request.snapshot) else {
        return InterpretResponseV2::no_match();
    };
    let (normalized, _) = normalize_with_spans(&request.text);
    let Some(ranges) = effect_segments_spanned(&normalized) else {
        return InterpretResponseV2::no_match();
    };
    if ranges.len() != extraction.operation_segments.len() {
        return InterpretResponseV2::no_match();
    }
    let mut operations = Vec::with_capacity(ranges.len());
    let mut affected = BTreeSet::new();
    for (segment, (segment_start, segment_end)) in extraction.operation_segments.iter().zip(ranges)
    {
        match interpret_segment(
            &request.text,
            &request.snapshot,
            &request.generation,
            segment,
            &normalized[segment_start..segment_end],
            &mut affected,
        ) {
            Ok(operation) => operations.push(operation),
            Err(outcome) => return outcome,
        }
    }
    if operations.is_empty() || operations.len() > MAX_OPERATIONS {
        return InterpretResponseV2::no_match();
    }
    InterpretResponseV2::plan(operations)
}

fn interpret_segment(
    text: &str,
    snapshot: &ResolutionCatalog,
    generation: &str,
    segment: &crate::OperationSegment,
    normalized: &str,
    affected: &mut BTreeSet<String>,
) -> Result<Operation, InterpretResponseV2> {
    let no_match = || InterpretResponseV2::no_match();
    let Some((action, needs_percentage)) =
        verb_action(normalize(segment.verb_text.as_str()).as_str())
    else {
        return Err(no_match());
    };
    let percentage = if needs_percentage {
        let Some((_, value)) = percentage_parts(normalized) else {
            return Err(no_match());
        };
        Some(value)
    } else {
        None
    };
    let mut targets = BTreeSet::new();
    for mention in &segment.mentions {
        if !mention.unlinked.is_empty() || mention.inherits.is_some() {
            return Err(no_match());
        }
        let mut constraints = ResolutionConstraints::default();
        for constraint in &mention.constraints {
            match constraint.kind {
                ConstraintKind::Area => {
                    constraints.area_id = Some(constraint.value.clone());
                }
                ConstraintKind::Domain => {
                    constraints.domain = Some(constraint.value.clone());
                }
                ConstraintKind::Capability => {
                    let action_name = match action {
                        Action::TurnOn => "turn_on",
                        Action::TurnOff => "turn_off",
                        Action::SetFanPercentage => "set_fan_percentage",
                        Action::GetState => "get_state",
                    };
                    if constraint.value != action_name {
                        return Err(no_match());
                    }
                }
            }
        }
        constraints.capability = Some(action);
        let outcome = resolve_entity(&ResolutionRequest {
            text: text.to_owned(),
            catalog: snapshot.clone(),
            generation: generation.to_owned(),
            mention: mention.text.clone(),
            span: Some(mention.span),
            constraints,
        });
        match outcome {
            ResolutionOutcome::Resolved { registry_id, .. } => {
                targets.insert(registry_id);
            }
            ResolutionOutcome::Ambiguous { .. } => {
                return Err(InterpretResponseV2::ambiguous());
            }
            ResolutionOutcome::NoMatch => {
                return Err(no_match());
            }
        }
    }
    if targets.is_empty() {
        return Err(no_match());
    }
    let targets: Vec<String> = targets.into_iter().collect();
    for target in &targets {
        if !affected.insert(target.clone()) {
            return Err(no_match());
        }
    }
    Ok(Operation {
        action,
        percentage,
        targets,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ResolutionArea, ResolutionEntity};

    fn area(area_id: &str, names: &[&str]) -> ResolutionArea {
        ResolutionArea {
            area_id: area_id.to_owned(),
            names: names.iter().map(|name| (*name).to_owned()).collect(),
        }
    }

    fn entity(
        registry_id: &str,
        entity_id: &str,
        domain: &str,
        area_id: &str,
        display: &str,
        aliases: &[&str],
        capabilities: &[Action],
    ) -> ResolutionEntity {
        ResolutionEntity {
            registry_id: registry_id.to_owned(),
            entity_id: entity_id.to_owned(),
            domain: domain.to_owned(),
            area_id: Some(area_id.to_owned()),
            display_name: display.to_owned(),
            aliases: aliases.iter().map(|alias| (*alias).to_owned()).collect(),
            capabilities: capabilities.to_vec(),
        }
    }

    fn snapshot() -> ResolutionCatalog {
        ResolutionCatalog {
            catalog_id: "test-v2".to_owned(),
            generation: "gen-001".to_owned(),
            areas: vec![
                area("area_sala", &["sala"]),
                area("area_quarto", &["quarto"]),
            ],
            entities: vec![
                entity(
                    "main",
                    "light.luz_sala",
                    "light",
                    "area_sala",
                    "luz da sala",
                    &["luz principal"],
                    &[Action::TurnOn, Action::TurnOff],
                ),
                entity(
                    "lamp",
                    "light.abajur_sala",
                    "light",
                    "area_sala",
                    "abajur",
                    &["abajur da sala"],
                    &[Action::TurnOn, Action::TurnOff],
                ),
                entity(
                    "qlamp",
                    "light.abajur_quarto",
                    "light",
                    "area_quarto",
                    "abajur",
                    &["abajur do quarto"],
                    &[Action::TurnOn, Action::TurnOff],
                ),
                entity(
                    "fan",
                    "fan.ventilador_quarto",
                    "fan",
                    "area_quarto",
                    "ventilador",
                    &["ventilador do quarto"],
                    &[Action::TurnOn, Action::TurnOff, Action::SetFanPercentage],
                ),
                entity(
                    "temp",
                    "sensor.t",
                    "sensor",
                    "area_sala",
                    "temperatura",
                    &[],
                    &[Action::GetState],
                ),
            ],
        }
    }

    fn request(text: &str) -> InterpretRequestV2 {
        InterpretRequestV2 {
            text: text.to_owned(),
            snapshot: snapshot(),
            generation: "gen-001".to_owned(),
        }
    }

    fn plan(action: Action, percentage: Option<u8>, targets: Vec<&str>) -> InterpretResponseV2 {
        InterpretResponseV2::Plan {
            operations: vec![crate::Operation {
                action,
                percentage,
                targets: targets.into_iter().map(str::to_owned).collect(),
            }],
            version: PROTOCOL_VERSION_V2,
        }
    }

    #[test]
    fn alias_with_valid_area_resolves_preserving_precedence() {
        assert_eq!(
            interpret_v2(&request("Apague o abajur da sala.")),
            plan(Action::TurnOff, None, vec!["lamp"])
        );
    }

    #[test]
    fn unlinked_area_poisons_alias_and_id_tiers() {
        assert_eq!(
            interpret_v2(&request("Acenda o abajur da copa.")),
            InterpretResponseV2::NoMatch { version: 2 }
        );
        assert_eq!(
            interpret_v2(&request("Acenda light.luz_sala da copa.")),
            InterpretResponseV2::NoMatch { version: 2 }
        );
    }

    #[test]
    fn inherited_ellipsis_abstains_without_grammar_bridging() {
        assert_eq!(
            interpret_v2(&request("Acenda a luz da sala e do quarto.")),
            InterpretResponseV2::NoMatch { version: 2 }
        );
    }

    #[test]
    fn fan_percentage_carries_value_and_domain() {
        assert_eq!(
            interpret_v2(&request("Coloque o ventilador em 50 por cento.")),
            plan(Action::SetFanPercentage, Some(50), vec!["fan"])
        );
    }

    #[test]
    fn action_capability_filters_incompatible_domains() {
        assert_eq!(
            interpret_v2(&request("Acenda a temperatura.")),
            InterpretResponseV2::NoMatch { version: 2 }
        );
    }

    #[test]
    fn mixed_operations_preserve_spoken_order() {
        assert_eq!(
            interpret_v2(&request(
                "Acenda a luz da sala e desligue o ventilador do quarto."
            )),
            InterpretResponseV2::Plan {
                operations: vec![
                    crate::Operation {
                        action: Action::TurnOn,
                        percentage: None,
                        targets: vec!["main".to_owned()],
                    },
                    crate::Operation {
                        action: Action::TurnOff,
                        percentage: None,
                        targets: vec!["fan".to_owned()],
                    },
                ],
                version: 2,
            }
        );
    }

    #[test]
    fn late_ambiguity_abstains_without_partial_plan() {
        assert_eq!(
            interpret_v2(&request("Apague o abajur da sala e o abajur.")),
            InterpretResponseV2::Ambiguous { version: 2 }
        );
    }

    #[test]
    fn contradictory_reuse_and_stale_generation_abstain() {
        assert_eq!(
            interpret_v2(&request(
                "Apague o abajur da sala e ligue o abajur da sala."
            )),
            InterpretResponseV2::NoMatch { version: 2 }
        );
        let mut stale = request("Apague o abajur da sala.");
        stale.generation = "gen-000".to_owned();
        assert_eq!(
            interpret_v2(&stale),
            InterpretResponseV2::NoMatch { version: 2 }
        );
    }

    #[test]
    fn malformed_and_query_inputs_abstain() {
        for text in ["", "Qual e o estado do abajur?", "Acenda a luz e desligue."] {
            assert_eq!(
                interpret_v2(&request(text)),
                InterpretResponseV2::NoMatch { version: 2 },
                "{text}"
            );
        }
    }
}
