use ha_catalog::{
    AliasProvenance, CapabilityDescriptorInput, CatalogSnapshot, CatalogSnapshotInput, Domain,
    EntityInput, EntityInputParts, EntityVisibility, ExplicitAlias, ExternalEntityId,
    RegistryEntryId, SensitiveText,
};
use intent_engine::{IntentEngine, RecognitionOutcome};
use nlu_core::{
    CapabilityId, CatalogGeneration, ComposedPlan, EntityId, EntityRef, RequestText, SlotValue,
};
use plan_engine::{
    CompositionOutcome, PendingEntityComposition, PlanEngine, ResumableCompositionOutcome,
};

struct FixtureEntity<'a> {
    index: usize,
    aliases: &'a [&'a str],
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
    let capability = CapabilityId::new(capability_value).expect("FIXTURE_TECNICA capability ID");
    let domain = Domain::new(domain_value).expect("FIXTURE_TECNICA domain");
    let entities = entities
        .iter()
        .map(|entity| {
            EntityInput::new(EntityInputParts {
                generation: generation(generation_value),
                registry_id: RegistryEntryId::new(&format!("{:032x}", entity.index))
                    .expect("FIXTURE_TECNICA registry ID"),
                external_id: ExternalEntityId::new(&format!(
                    "{domain_value}.fixture_tecnica_{}",
                    entity.index
                ))
                .expect("FIXTURE_TECNICA external entity ID"),
                domain: domain.clone(),
                display_name: sensitive(format!("FIXTURE_TECNICA_DISPLAY_{}", entity.index)),
                aliases: entity
                    .aliases
                    .iter()
                    .map(|alias| {
                        ExplicitAlias::new(sensitive(*alias), AliasProvenance::EntityRegistry)
                    })
                    .collect(),
                capabilities: vec![capability.clone()],
                area_id: None,
                floor_id: None,
                device_id: None,
                visibility: EntityVisibility::exposed(),
            })
        })
        .collect();
    CatalogSnapshot::build(CatalogSnapshotInput {
        generation: generation(generation_value),
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
    .expect("FIXTURE_TECNICA snapshot")
}

fn entity_ref(snapshot: &CatalogSnapshot, domain: &str, index: usize) -> EntityRef {
    let external_id = ExternalEntityId::new(&format!("{domain}.fixture_tecnica_{index}"))
        .expect("FIXTURE_TECNICA external entity ID");
    snapshot
        .entity_by_external_id(&external_id)
        .expect("FIXTURE_TECNICA entity")
        .entity_ref()
}

fn legacy_outcome(text: &str, snapshot: &CatalogSnapshot) -> CompositionOutcome {
    let source = RequestText::new(text.to_owned()).expect("admitted project-authored row");
    let recognition = IntentEngine::bundled()
        .expect("bundled P09 engine")
        .recognize(&source)
        .expect("recognition result");
    let RecognitionOutcome::Match(intent_match) = recognition else {
        panic!("frozen row must produce one P09 match: {recognition:?}");
    };
    PlanEngine::new()
        .expect("plan engine")
        .compose(&source, &intent_match, snapshot)
        .expect("legacy composition")
}

fn resumable_outcome(text: &str, snapshot: &CatalogSnapshot) -> ResumableCompositionOutcome {
    let source = RequestText::new(text.to_owned()).expect("admitted project-authored row");
    let recognition = IntentEngine::bundled()
        .expect("bundled P09 engine")
        .recognize(&source)
        .expect("recognition result");
    let RecognitionOutcome::Match(intent_match) = recognition else {
        panic!("frozen row must produce one P09 match: {recognition:?}");
    };
    PlanEngine::new()
        .expect("plan engine")
        .compose_resumable(&source, &intent_match, snapshot)
        .expect("resumable composition")
}

fn pending(text: &str, snapshot: &CatalogSnapshot) -> PendingEntityComposition {
    let outcome = resumable_outcome(text, snapshot);
    let ResumableCompositionOutcome::Pending(pending) = outcome else {
        panic!("expected pending composition, received {outcome:?}");
    };
    pending
}

fn plan(outcome: CompositionOutcome) -> ComposedPlan {
    let CompositionOutcome::Plan(plan) = outcome else {
        panic!("expected direct plan, received {outcome:?}");
    };
    plan
}

fn node_entity<'a>(plan: &'a ComposedPlan, node: &str) -> &'a EntityRef {
    let node = plan
        .plan()
        .nodes()
        .iter()
        .find(|candidate| candidate.id().as_str() == node)
        .expect("FIXTURE_TECNICA node");
    let SlotValue::Entity(entity) = node
        .slots()
        .iter()
        .find(|slot| slot.id().as_str() == "ha:entity")
        .expect("FIXTURE_TECNICA entity slot")
        .value()
    else {
        panic!("FIXTURE_TECNICA slot must contain an entity");
    };
    entity
}

#[test]
fn legacy_clarification_is_preserved_and_pending_metadata_is_typed() {
    let text = "desligue interruptor 1 do setor sala";
    let catalog = snapshot(
        7,
        "ha:switch_control",
        "switch",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["interruptor 1 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["interruptor 1 do setor sala"],
            },
        ],
    );

    let CompositionOutcome::EntityClarification(clarification) = legacy_outcome(text, &catalog)
    else {
        panic!("legacy route must retain its clarification behavior");
    };
    assert_eq!(clarification.options().len(), 2);

    let pending = pending(text, &catalog);
    assert_eq!(pending.capability().as_str(), "ha:switch_control");
    assert_eq!(pending.catalog_generation(), generation(7));
    assert_eq!(pending.endpoint().node().as_str(), "p11:node_1");
    assert_eq!(pending.endpoint().slot().as_str(), "ha:entity");
    assert_eq!(pending.candidates().len(), 2);
    assert!(
        pending
            .candidates()
            .windows(2)
            .all(|pair| pair[0] < pair[1])
    );
    assert!(
        pending
            .candidates()
            .iter()
            .all(|candidate| candidate.generation() == generation(7))
    );
}

#[test]
fn exact_completion_matches_direct_composition_canonical_bytes() {
    let text = "desligue interruptor 1 do setor sala";
    let ambiguous = snapshot(
        11,
        "ha:switch_control",
        "switch",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["interruptor 1 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["interruptor 1 do setor sala"],
            },
        ],
    );
    let unique = snapshot(
        11,
        "ha:switch_control",
        "switch",
        &[FixtureEntity {
            index: 2,
            aliases: &["interruptor 1 do setor sala"],
        }],
    );
    let selected = entity_ref(&unique, "switch", 2);
    let pending = pending(text, &ambiguous);
    assert!(pending.candidates().binary_search(&selected).is_ok());

    let resumed = pending
        .complete(selected.clone())
        .expect("completion result")
        .expect("stored exact candidate must complete");
    let direct = plan(legacy_outcome(text, &unique));

    assert_eq!(node_entity(&resumed, "p11:node_1"), &selected);
    assert_eq!(
        resumed.canonical_bytes().expect("resumed canonical bytes"),
        direct.canonical_bytes().expect("direct canonical bytes")
    );
}

#[test]
fn unknown_or_mixed_generation_referents_do_not_complete() {
    let text = "desligue interruptor 1 do setor sala";
    let catalog = snapshot(
        13,
        "ha:switch_control",
        "switch",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["interruptor 1 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["interruptor 1 do setor sala"],
            },
        ],
    );

    let unknown = EntityRef::new(
        EntityId::new("fixture_tecnica:unknown").expect("FIXTURE_TECNICA entity ID"),
        generation(13),
    );
    assert!(
        pending(text, &catalog)
            .complete(unknown)
            .expect("unknown completion result")
            .is_none()
    );

    let candidate = pending(text, &catalog).candidates()[0].clone();
    let mixed_generation = EntityRef::new(candidate.id().clone(), generation(14));
    assert!(
        pending(text, &catalog)
            .complete(mixed_generation)
            .expect("mixed-generation completion result")
            .is_none()
    );
}

#[test]
fn parallel_pending_endpoint_identifies_and_fills_only_the_ambiguous_node() {
    let text = "ligue luz 1 do setor sala e luz 7 do setor sala";
    let first_ambiguous = snapshot(
        17,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["luz 1 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["luz 1 do setor sala"],
            },
            FixtureEntity {
                index: 3,
                aliases: &["luz 7 do setor sala"],
            },
        ],
    );
    let selected_first = entity_ref(&first_ambiguous, "light", 2);
    let fixed_second = entity_ref(&first_ambiguous, "light", 3);
    let pending_first = pending(text, &first_ambiguous);
    assert_eq!(pending_first.endpoint().node().as_str(), "p11:node_1");
    let completed_first = pending_first
        .complete(selected_first.clone())
        .expect("first completion result")
        .expect("first endpoint completion");
    assert_eq!(node_entity(&completed_first, "p11:node_1"), &selected_first);
    assert_eq!(node_entity(&completed_first, "p11:node_2"), &fixed_second);

    let second_ambiguous = snapshot(
        17,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["luz 1 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["luz 7 do setor sala"],
            },
            FixtureEntity {
                index: 3,
                aliases: &["luz 7 do setor sala"],
            },
        ],
    );
    let fixed_first = entity_ref(&second_ambiguous, "light", 1);
    let selected_second = entity_ref(&second_ambiguous, "light", 3);
    let pending_second = pending(text, &second_ambiguous);
    assert_eq!(pending_second.endpoint().node().as_str(), "p11:node_2");
    let completed_second = pending_second
        .complete(selected_second.clone())
        .expect("second completion result")
        .expect("second endpoint completion");
    assert_eq!(node_entity(&completed_second, "p11:node_1"), &fixed_first);
    assert_eq!(
        node_entity(&completed_second, "p11:node_2"),
        &selected_second
    );
}

#[test]
fn completion_reuses_the_existing_semantic_conflict_check() {
    let text = "não ligue luz 101 do setor sala e ligue luz 102 do setor cozinha";
    let catalog = snapshot(
        19,
        "ha:light_control",
        "light",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["luz 101 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["luz 101 do setor sala", "luz 102 do setor cozinha"],
            },
        ],
    );
    let conflicting = entity_ref(&catalog, "light", 2);
    let pending = pending(text, &catalog);

    assert!(
        pending
            .complete(conflicting)
            .expect("conflict completion result")
            .is_none()
    );
}

#[test]
fn pending_debug_discloses_counts_but_no_source_or_identifiers() {
    let text = "desligue interruptor 1 do setor sala";
    let catalog = snapshot(
        23,
        "ha:switch_control",
        "switch",
        &[
            FixtureEntity {
                index: 1,
                aliases: &["interruptor 1 do setor sala"],
            },
            FixtureEntity {
                index: 2,
                aliases: &["interruptor 1 do setor sala"],
            },
        ],
    );
    let pending = pending(text, &catalog);
    let candidate_ids = pending
        .candidates()
        .iter()
        .map(|candidate| candidate.id().as_str().to_owned())
        .collect::<Vec<_>>();
    let rendered = format!("{pending:?}");

    assert!(rendered.contains("candidate_count"));
    assert!(!rendered.contains("catalog_generation"));
    assert!(!rendered.contains(text));
    assert!(!rendered.contains("ha:switch_control"));
    assert!(!rendered.contains("p11:node_1"));
    assert!(!rendered.contains("ha:entity"));
    assert!(
        candidate_ids
            .iter()
            .all(|candidate| !rendered.contains(candidate))
    );
}
