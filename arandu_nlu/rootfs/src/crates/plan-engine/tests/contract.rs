use ha_catalog::{
    AliasProvenance, CapabilityDescriptorInput, CatalogError, CatalogSnapshot,
    CatalogSnapshotInput, Domain, EntityInput, EntityInputParts, EntityVisibility, ExplicitAlias,
    ExternalEntityId, RegistryEntryId, SensitiveText,
};
use intent_engine::{IntentEngine, RecognitionOutcome};
use nlu_core::{
    CapabilityId, CatalogGeneration, EvidenceKind, GraphExecutionClass, Polarity, RequestText,
    SlotValue,
};
use plan_engine::{CompositionAbstentionReason, CompositionOutcome, PlanEngine};

struct FixtureEntity<'a> {
    index: usize,
    alias: &'a str,
}

fn generation(value: u64) -> CatalogGeneration {
    CatalogGeneration::new(value).expect("FIXTURE_TECNICA generation")
}

fn sensitive(value: impl Into<String>) -> SensitiveText {
    SensitiveText::new(value).expect("FIXTURE_TECNICA sensitive text")
}

fn snapshot(
    generation_value: u64,
    capability_value: &str,
    domain_value: &str,
    entities: &[FixtureEntity<'_>],
) -> CatalogSnapshot {
    snapshot_with_entity_generation(
        generation_value,
        generation_value,
        capability_value,
        domain_value,
        entities,
    )
    .expect("FIXTURE_TECNICA snapshot")
}

fn snapshot_with_entity_generation(
    snapshot_generation: u64,
    entity_generation: u64,
    capability_value: &str,
    domain_value: &str,
    entities: &[FixtureEntity<'_>],
) -> Result<CatalogSnapshot, CatalogError> {
    let capability = CapabilityId::new(capability_value).expect("FIXTURE_TECNICA capability ID");
    let domain = Domain::new(domain_value).expect("FIXTURE_TECNICA domain");
    let entities = entities
        .iter()
        .map(|entity| {
            EntityInput::new(EntityInputParts {
                generation: generation(entity_generation),
                registry_id: RegistryEntryId::new(&format!("{:032x}", entity.index))
                    .expect("FIXTURE_TECNICA registry ID"),
                external_id: ExternalEntityId::new(&format!(
                    "{domain_value}.fixture_tecnica_{}",
                    entity.index
                ))
                .expect("FIXTURE_TECNICA external entity ID"),
                domain: domain.clone(),
                display_name: sensitive(format!("FIXTURE_TECNICA_DISPLAY_{}", entity.index)),
                aliases: vec![ExplicitAlias::new(
                    sensitive(entity.alias),
                    AliasProvenance::EntityRegistry,
                )],
                capabilities: vec![capability.clone()],
                area_id: None,
                floor_id: None,
                device_id: None,
                visibility: EntityVisibility::exposed(),
            })
        })
        .collect();
    CatalogSnapshot::build(CatalogSnapshotInput {
        generation: generation(snapshot_generation),
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors: vec![CapabilityDescriptorInput {
            id: capability,
            enabled: true,
            state_query_domains: Vec::new(),
        }],
        entities,
    })
}

fn empty_snapshot() -> CatalogSnapshot {
    CatalogSnapshot::build(CatalogSnapshotInput::empty(generation(1)))
        .expect("FIXTURE_TECNICA empty snapshot")
}

fn compose(text: &str, snapshot: &CatalogSnapshot) -> (RequestText, CompositionOutcome) {
    let source = RequestText::new(text.to_owned()).expect("frozen project-authored row");
    let recognition = IntentEngine::bundled()
        .expect("bundled P09 engine")
        .recognize(&source)
        .expect("recognition result");
    let RecognitionOutcome::Match(intent_match) = recognition else {
        panic!("frozen row must produce one P09 match: {recognition:?}");
    };
    let outcome = PlanEngine::new()
        .expect("plan engine")
        .compose(&source, &intent_match, snapshot)
        .expect("composition result");
    (source, outcome)
}

fn plan(outcome: CompositionOutcome) -> nlu_core::ComposedPlan {
    let CompositionOutcome::Plan(plan) = outcome else {
        panic!("expected plan, received {outcome:?}");
    };
    plan
}

#[test]
fn composes_single_affirmed_partial_safe_plan() {
    let text = "desligue interruptor 1 do setor sala";
    let catalog = snapshot(
        1,
        "ha:switch_control",
        "switch",
        &[FixtureEntity {
            index: 1,
            alias: "interruptor 1 do setor sala",
        }],
    );
    let (source, outcome) = compose(text, &catalog);
    let composed = plan(outcome);

    assert_eq!(composed.execution_class(), GraphExecutionClass::PartialSafe);
    assert_eq!(composed.plan().nodes().len(), 1);
    assert_eq!(composed.plan().nodes()[0].id().as_str(), "p11:node_1");
    assert_eq!(
        composed.plan().nodes()[0].operation().as_str(),
        "ha:turn_off"
    );
    assert_eq!(composed.clauses()[0].polarity(), Polarity::Affirmed);
    assert_eq!(composed.clauses()[0].intent().as_str(), "ha:hass_turn_off");
    assert!(
        composed.clauses()[0]
            .evidence()
            .iter()
            .any(|atom| atom.kind() == &EvidenceKind::Predicate)
    );
    assert!(composed.clauses()[0].evidence().iter().any(
        |atom| matches!(atom.kind(), EvidenceKind::Argument(id) if id.as_str() == "ha:entity")
    ));
    assert!(
        composed
            .canonical_bytes()
            .expect("canonical plan")
            .starts_with(b"{\"schema_version\":\"p11-semantic-plan-v1\"")
    );
    assert_eq!(source.as_str(), text);
}

#[test]
fn composes_parallel_atomic_plan_independent_of_catalog_insertion_order() {
    let text = "ligue luz 1 do setor sala e luz 7 do setor sala";
    let forward = snapshot(
        1,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                alias: "luz 1 do setor sala",
            },
            FixtureEntity {
                index: 2,
                alias: "luz 7 do setor sala",
            },
        ],
    );
    let reverse = snapshot(
        1,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 2,
                alias: "luz 7 do setor sala",
            },
            FixtureEntity {
                index: 1,
                alias: "luz 1 do setor sala",
            },
        ],
    );
    let (_, forward_outcome) = compose(text, &forward);
    let (_, reverse_outcome) = compose(text, &reverse);
    let forward_plan = plan(forward_outcome);
    let reverse_plan = plan(reverse_outcome);

    assert_eq!(
        forward_plan.execution_class(),
        GraphExecutionClass::AtomicOnly
    );
    assert_eq!(forward_plan.plan().nodes().len(), 2);
    assert_eq!(forward_plan.independent_pairs().len(), 1);
    assert!(forward_plan.plan().relations().is_empty());
    assert_eq!(
        forward_plan.canonical_bytes().expect("forward bytes"),
        reverse_plan.canonical_bytes().expect("reverse bytes")
    );
}

#[test]
fn composes_ordered_timer_with_relation_and_explicit_argument_share() {
    let text = "inicie temporizador 1 do setor sala por 1 minutos e consulte o estado depois";
    let catalog = snapshot(
        1,
        "ha:timer_control",
        "timer",
        &[FixtureEntity {
            index: 1,
            alias: "temporizador 1 do setor sala",
        }],
    );
    let (source, outcome) = compose(text, &catalog);
    let composed = plan(outcome);

    assert_eq!(composed.execution_class(), GraphExecutionClass::PartialSafe);
    assert_eq!(composed.plan().nodes().len(), 2);
    assert_eq!(composed.plan().relations().len(), 1);
    assert_eq!(composed.relation_evidence().len(), 1);
    assert_eq!(composed.argument_shares().len(), 1);
    assert!(composed.independent_pairs().is_empty());
    assert_eq!(
        composed.clauses()[1].intent().as_str(),
        "ha:hass_timer_status"
    );
    assert_eq!(
        composed.relation_evidence()[0].evidence()[0]
            .slice(&source)
            .expect("relation evidence"),
        "e consulte o estado depois"
    );
    assert_eq!(
        composed.argument_shares()[0].evidence()[0]
            .slice(&source)
            .expect("share evidence"),
        "temporizador 1 do setor sala"
    );
    assert!(
        composed.plan().nodes()[0]
            .slots()
            .iter()
            .any(|slot| matches!(slot.value(), SlotValue::Integer(60)))
    );
}

#[test]
fn composes_both_frozen_clear_negation_scopes_as_non_executable() {
    let cases = [
        (
            "não ligue luz 101 do setor sala e ligue luz 102 do setor cozinha",
            "luz 101 do setor sala",
            "luz 102 do setor cozinha",
            [Polarity::Negated, Polarity::Affirmed],
        ),
        (
            "ligue luz 103 do setor quarto e não ligue luz 104 do setor corredor",
            "luz 103 do setor quarto",
            "luz 104 do setor corredor",
            [Polarity::Affirmed, Polarity::Negated],
        ),
    ];

    for (text, first, second, expected) in cases {
        let catalog = snapshot(
            1,
            "ha:light_control",
            "light",
            &[
                FixtureEntity {
                    index: 1,
                    alias: first,
                },
                FixtureEntity {
                    index: 2,
                    alias: second,
                },
            ],
        );
        let (_, outcome) = compose(text, &catalog);
        let composed = plan(outcome);
        assert_eq!(
            composed.execution_class(),
            GraphExecutionClass::NonExecutable
        );
        assert_eq!(
            [
                composed.clauses()[0].polarity(),
                composed.clauses()[1].polarity()
            ],
            expected
        );
        assert_eq!(
            composed
                .clauses()
                .iter()
                .flat_map(|clause| clause.evidence())
                .filter(|atom| atom.kind() == &EvidenceKind::Negation)
                .count(),
            1
        );
    }
}

#[test]
fn ambiguous_or_non_train_negation_scope_never_returns_a_plan() {
    let ambiguous = "não ligue luz 105 do setor varanda e luz 106 do setor garagem";
    let (_, outcome) = compose(ambiguous, &empty_snapshot());
    assert_eq!(
        outcome,
        CompositionOutcome::Abstention(CompositionAbstentionReason::NegationScope)
    );

    let non_train =
        "por favor não ligue luz 201 do setor biblioteca e ligue luz 202 do setor ateliê";
    let (_, outcome) = compose(non_train, &empty_snapshot());
    assert_eq!(
        outcome,
        CompositionOutcome::Abstention(CompositionAbstentionReason::NegationScope)
    );
}

#[test]
fn rejects_non_train_connective_and_extra_prefix_around_complete_matches() {
    let connective = "por favor ligue luz 1 do setor sala junto com luz 7 do setor sala";
    let catalog = snapshot(
        1,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                alias: "luz 1 do setor sala",
            },
            FixtureEntity {
                index: 2,
                alias: "luz 7 do setor sala",
            },
        ],
    );
    let (_, outcome) = compose(connective, &catalog);
    assert_eq!(
        outcome,
        CompositionOutcome::Abstention(CompositionAbstentionReason::UnsupportedPattern)
    );

    let prefixed = "por favor desligue interruptor 1 do setor sala";
    let catalog = snapshot(
        1,
        "ha:switch_control",
        "switch",
        &[FixtureEntity {
            index: 1,
            alias: "interruptor 1 do setor sala",
        }],
    );
    let (_, outcome) = compose(prefixed, &catalog);
    assert_eq!(
        outcome,
        CompositionOutcome::Abstention(CompositionAbstentionReason::UnsupportedPattern)
    );
}

#[test]
fn admitted_coordination_scope_counterexample_never_returns_a_plan() {
    let text = "ligue a luz e o ventilador da sala";
    let source = RequestText::new(text.to_owned()).expect("admitted P02 counterexample");
    let catalog = snapshot(
        1,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                alias: "a luz",
            },
            FixtureEntity {
                index: 2,
                alias: "o ventilador da sala",
            },
        ],
    );
    let recognition = IntentEngine::bundled()
        .expect("bundled P09 engine")
        .recognize(&source)
        .expect("recognition result");
    if let RecognitionOutcome::Match(intent_match) = recognition {
        let outcome = PlanEngine::new()
            .expect("plan engine")
            .compose(&source, &intent_match, &catalog)
            .expect("composition result");
        assert!(
            !matches!(outcome, CompositionOutcome::Plan(_)),
            "coordination scope must remain unresolved"
        );
    }
}

#[test]
fn one_ambiguous_target_invalidates_the_complete_parallel_graph() {
    let text = "ligue luz 1 do setor sala e luz 7 do setor sala";
    let catalog = snapshot(
        1,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                alias: "luz 1 do setor sala",
            },
            FixtureEntity {
                index: 2,
                alias: "luz 1 do setor sala",
            },
            FixtureEntity {
                index: 3,
                alias: "luz 7 do setor sala",
            },
        ],
    );
    let (_, outcome) = compose(text, &catalog);
    let CompositionOutcome::EntityClarification(clarification) = outcome else {
        panic!("one collision must return only entity clarification: {outcome:?}");
    };
    assert_eq!(clarification.options().len(), 2);
}

#[test]
fn multiple_ambiguities_and_no_match_abstain_without_partial_graphs() {
    let parallel = "ligue luz 1 do setor sala e luz 7 do setor sala";
    let catalog = snapshot(
        1,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                alias: "luz 1 do setor sala",
            },
            FixtureEntity {
                index: 2,
                alias: "luz 1 do setor sala",
            },
            FixtureEntity {
                index: 3,
                alias: "luz 7 do setor sala",
            },
            FixtureEntity {
                index: 4,
                alias: "luz 7 do setor sala",
            },
        ],
    );
    let (_, outcome) = compose(parallel, &catalog);
    assert_eq!(
        outcome,
        CompositionOutcome::Abstention(CompositionAbstentionReason::MultipleEntityClarifications)
    );

    let single = "desligue interruptor 1 do setor sala";
    let (_, outcome) = compose(single, &empty_snapshot());
    assert_eq!(
        outcome,
        CompositionOutcome::Abstention(CompositionAbstentionReason::EntityResolution)
    );
}

#[test]
fn converts_text_binding_without_retaining_it_in_errors_or_debug() {
    let text = "responda confirmacao 1 do setor sala";
    let (source, outcome) = compose(text, &empty_snapshot());
    let composed = plan(outcome);
    let slot = &composed.plan().nodes()[0].slots()[0];
    let SlotValue::EvidenceText(span) = slot.value() else {
        panic!("response binding must remain evidence text");
    };
    assert_eq!(
        span.slice(&source).expect("response evidence"),
        "confirmacao 1 do setor sala"
    );
    let rendered = format!(
        "{:?}",
        CompositionOutcome::Abstention(CompositionAbstentionReason::UnsupportedPattern)
    );
    assert!(!rendered.contains("FIXTURE_TECNICA_PRIVATE_CANARY"));
}

#[test]
fn stale_catalog_entity_generation_is_rejected_before_composition() {
    let result = snapshot_with_entity_generation(
        2,
        1,
        "ha:switch_control",
        "switch",
        &[FixtureEntity {
            index: 1,
            alias: "interruptor 1 do setor sala",
        }],
    );
    assert!(matches!(result, Err(CatalogError::StaleEntityGeneration)));
}
