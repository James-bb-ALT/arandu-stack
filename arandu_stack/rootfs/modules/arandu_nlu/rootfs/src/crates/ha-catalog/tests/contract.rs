use std::sync::Arc;

use ha_catalog::coverage::{
    BUILT_IN_INTENT_CONSTANTS, GENERATED_ENTITY_PLATFORM_DOMAINS, HOME_ASSISTANT_COMMIT,
    HOME_ASSISTANT_TAG, HOME_ASSISTANT_TREE, domain_disposition,
};
use ha_catalog::model::{MAX_DOMAIN_BYTES, MAX_LOCATION_ID_BYTES, MAX_SENSITIVE_TEXT_BYTES};
use ha_catalog::resolve::MAX_QUERY_CONSTRAINTS;
use ha_catalog::snapshot::{
    MAX_ALIASES_PER_RECORD, MAX_CATALOG_AGGREGATE_ITEMS, MAX_CATALOG_TEXT_BYTES,
    MAX_DESCRIPTOR_DOMAINS, MAX_ENTITIES, MAX_RESOLUTION_CANDIDATES,
};
use ha_catalog::{
    AliasProvenance, AreaId, AreaInput, AssociationKind, CapabilityDescriptorInput, CatalogError,
    CatalogSnapshot, CatalogSnapshotInput, CatalogStore, DeviceId, DeviceInput, Domain,
    DuplicateKind, EntityConstraint, EntityInput, EntityInputParts, EntityResolution,
    EntityVisibility, ExplicitAlias, ExternalEntityId, FloorId, FloorInput, LimitKind,
    RankingFactorKind, RegistryEntryId, ResolutionAbstention, ResolutionRank, SensitiveText,
    StateQueryDisposition, resolve_entity,
};
use nlu_core::{CapabilityId, CatalogGeneration, RequestText, Utf8Span};

const FIXTURE_TECNICA_DOMAIN: &str = "fixture_tecnica";
const FIXTURE_TECNICA_FLOOR_ID: &str = "f0000000000000000000000000000001";
const FIXTURE_TECNICA_AREA_ID: &str = "a0000000000000000000000000000001";
const FIXTURE_TECNICA_DEVICE_ID: &str = "d0000000000000000000000000000001";

fn generation(value: u64) -> CatalogGeneration {
    CatalogGeneration::new(value).expect("FIXTURE_TECNICA generation")
}

fn registry_id(index: usize) -> RegistryEntryId {
    RegistryEntryId::new(&format!("{index:032x}")).expect("FIXTURE_TECNICA registry ID")
}

fn sensitive(value: impl Into<String>) -> SensitiveText {
    SensitiveText::new(value).expect("FIXTURE_TECNICA sensitive text")
}

fn alias(value: impl Into<String>, provenance: AliasProvenance) -> ExplicitAlias {
    ExplicitAlias::new(sensitive(value), provenance)
}

fn basic_parts(index: usize, display: impl Into<String>) -> EntityInputParts {
    EntityInputParts {
        generation: generation(1),
        registry_id: registry_id(index),
        external_id: ExternalEntityId::new(&format!("fixture_tecnica.entity_{index}"))
            .expect("FIXTURE_TECNICA external ID"),
        domain: Domain::new(FIXTURE_TECNICA_DOMAIN).expect("FIXTURE_TECNICA domain"),
        display_name: sensitive(display),
        aliases: Vec::new(),
        capabilities: Vec::new(),
        area_id: None,
        floor_id: None,
        device_id: None,
        visibility: EntityVisibility::exposed(),
    }
}

fn basic_entity(index: usize, display: impl Into<String>) -> EntityInput {
    EntityInput::new(basic_parts(index, display))
}

fn input_with_entities(entities: Vec<EntityInput>) -> CatalogSnapshotInput {
    CatalogSnapshotInput {
        generation: generation(1),
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors: Vec::new(),
        entities,
    }
}

fn span_for(source: &RequestText, value: &str) -> Utf8Span {
    let start = source
        .as_str()
        .find(value)
        .expect("FIXTURE_TECNICA evidence text");
    source
        .span(start as u64, (start + value.len()) as u64)
        .expect("FIXTURE_TECNICA evidence span")
}

#[test]
fn fixture_tecnica_identity_text_and_source_contracts_are_exact() {
    let stable = RegistryEntryId::new("a123456789abcdef0123456789abcdef")
        .expect("FIXTURE_TECNICA stable ID");
    assert_eq!(
        stable
            .to_core_entity_id()
            .expect("FIXTURE_TECNICA core ID")
            .as_str(),
        "ha_entity:id_a123456789abcdef0123456789abcdef"
    );
    let digit_leading = RegistryEntryId::new("0123456789abcdef0123456789abcdef")
        .expect("FIXTURE_TECNICA digit-leading stable ID");
    assert_eq!(
        digit_leading
            .to_core_entity_id()
            .expect("FIXTURE_TECNICA compatible core ID")
            .as_str(),
        "ha_entity:id_0123456789abcdef0123456789abcdef"
    );
    assert_eq!(
        AreaId::new("fixture_tecnica_area_1")
            .expect("FIXTURE_TECNICA slug-derived area")
            .as_str(),
        "fixture_tecnica_area_1"
    );
    assert_eq!(
        FloorId::new(&format!("f{}", "0".repeat(MAX_LOCATION_ID_BYTES - 1)))
            .expect("FIXTURE_TECNICA maximum floor ID")
            .as_str()
            .len(),
        MAX_LOCATION_ID_BYTES
    );
    assert_eq!(
        AreaId::new(&format!("a{}", "0".repeat(MAX_LOCATION_ID_BYTES))),
        Err(CatalogError::InvalidAreaId)
    );
    assert_eq!(
        RegistryEntryId::new("0123456789ABCDEF0123456789ABCDEF"),
        Err(CatalogError::InvalidRegistryEntryId)
    );
    assert!(
        ExternalEntityId::new("fixture_tecnica.1_entity").is_ok(),
        "numeric object-ID prefix remains catalogable"
    );
    assert_eq!(
        ExternalEntityId::new("Fixture_tecnica.entity"),
        Err(CatalogError::InvalidExternalEntityId)
    );

    let original = "FIXTURE_TECNICA_a\u{301}";
    let text = sensitive(original);
    let canonical = sensitive("FIXTURE_TECNICA_\u{e1}");
    assert_eq!(text.as_bytes(), original.as_bytes());
    assert!(text.exact_nfc_eq(&canonical));

    assert_eq!(HOME_ASSISTANT_TAG, "2026.8.3");
    assert_eq!(
        HOME_ASSISTANT_COMMIT,
        "759e4658f40b3ccb671d418b8a0ed95224bf4561"
    );
    assert_eq!(
        HOME_ASSISTANT_TREE,
        "f4a72534bb33abf8b5d183910a0c134b968af2f8"
    );
    assert_eq!(GENERATED_ENTITY_PLATFORM_DOMAINS.len(), 45);
    assert_eq!(BUILT_IN_INTENT_CONSTANTS.len(), 20);
    assert!(domain_disposition("light").is_some());
    assert!(domain_disposition("fixture_tecnica_future").is_none());
}

#[test]
fn fixture_tecnica_snapshot_preserves_and_derives_every_association() {
    let floor_id = FloorId::new(FIXTURE_TECNICA_FLOOR_ID).expect("FIXTURE_TECNICA floor");
    let area_id = AreaId::new(FIXTURE_TECNICA_AREA_ID).expect("FIXTURE_TECNICA area");
    let device_id = DeviceId::new(FIXTURE_TECNICA_DEVICE_ID).expect("FIXTURE_TECNICA device");
    let capability =
        CapabilityId::new("fixture_tecnica:state_a").expect("FIXTURE_TECNICA capability");

    let mut parts = basic_parts(1, "FIXTURE_TECNICA_DISPLAY");
    parts.aliases.push(alias(
        "FIXTURE_TECNICA_ENTITY_ALIAS",
        AliasProvenance::EntityRegistry,
    ));
    parts.capabilities.push(capability.clone());
    parts.floor_id = Some(floor_id.clone());
    parts.device_id = Some(device_id.clone());

    let snapshot = CatalogSnapshot::build(CatalogSnapshotInput {
        generation: generation(1),
        floors: vec![FloorInput {
            id: floor_id.clone(),
            name: sensitive("FIXTURE_TECNICA_FLOOR"),
            aliases: vec![alias(
                "FIXTURE_TECNICA_FLOOR_ALIAS",
                AliasProvenance::FloorRegistry,
            )],
        }],
        areas: vec![AreaInput {
            id: area_id.clone(),
            name: sensitive("FIXTURE_TECNICA_AREA"),
            aliases: vec![alias(
                "FIXTURE_TECNICA_AREA_ALIAS",
                AliasProvenance::AreaRegistry,
            )],
            floor_id: Some(floor_id.clone()),
        }],
        devices: vec![DeviceInput {
            id: device_id.clone(),
            name: sensitive("FIXTURE_TECNICA_DEVICE"),
            area_id: Some(area_id.clone()),
        }],
        capability_descriptors: vec![CapabilityDescriptorInput {
            id: capability.clone(),
            enabled: true,
            state_query_domains: vec![
                Domain::new(FIXTURE_TECNICA_DOMAIN).expect("FIXTURE_TECNICA domain"),
            ],
        }],
        entities: vec![EntityInput::new(parts)],
    })
    .expect("FIXTURE_TECNICA associated snapshot");

    let entity = snapshot
        .entity(&registry_id(1))
        .expect("FIXTURE_TECNICA entity");
    assert_eq!(entity.generation(), generation(1));
    assert_eq!(entity.area_id(), Some(&area_id));
    assert_eq!(entity.floor_id(), Some(&floor_id));
    assert_eq!(entity.device_id(), Some(&device_id));
    assert!(entity.capabilities().contains(&capability));
    assert_eq!(
        entity.aliases()[0].provenance(),
        AliasProvenance::EntityRegistry
    );
    assert_eq!(
        snapshot.state_query_disposition(&registry_id(1), &capability),
        Some(StateQueryDisposition::Supported)
    );
}

#[test]
fn fixture_tecnica_visibility_filters_but_does_not_hide_invalid_input() {
    let exposed = basic_entity(1, "FIXTURE_TECNICA_EXPOSED");
    let mut unexposed = basic_parts(2, "FIXTURE_TECNICA_UNEXPOSED");
    unexposed.visibility = EntityVisibility::new(false, true, false);
    let mut disabled = basic_parts(3, "FIXTURE_TECNICA_DISABLED");
    disabled.visibility = EntityVisibility::new(true, false, false);
    let mut hidden = basic_parts(4, "FIXTURE_TECNICA_HIDDEN_BUT_EXPLICITLY_EXPOSED");
    hidden.visibility = EntityVisibility::new(true, true, true);

    let snapshot = CatalogSnapshot::build(input_with_entities(vec![
        EntityInput::new(hidden),
        exposed,
        EntityInput::new(disabled),
        EntityInput::new(unexposed),
    ]))
    .expect("FIXTURE_TECNICA filtered snapshot");
    assert_eq!(
        snapshot.entities().keys().cloned().collect::<Vec<_>>(),
        vec![registry_id(1), registry_id(4)]
    );

    let mut invalid_hidden = basic_parts(5, "FIXTURE_TECNICA_INVALID_HIDDEN");
    invalid_hidden.visibility = EntityVisibility::new(false, true, false);
    invalid_hidden.device_id =
        Some(DeviceId::new("d0000000000000000000000000000002").expect("FIXTURE_TECNICA device"));
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![EntityInput::new(invalid_hidden)]))
            .expect_err("hidden dangling association"),
        CatalogError::Dangling {
            kind: AssociationKind::EntityDevice
        }
    );
}

#[test]
fn fixture_tecnica_duplicate_and_contradiction_mutations_are_rejected() {
    let first = basic_entity(1, "FIXTURE_TECNICA_A");
    let mut duplicate_stable = basic_parts(1, "FIXTURE_TECNICA_B");
    duplicate_stable.external_id =
        ExternalEntityId::new("fixture_tecnica.entity_2").expect("FIXTURE_TECNICA external");
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![
            first.clone(),
            EntityInput::new(duplicate_stable)
        ]))
        .expect_err("duplicate stable ID"),
        CatalogError::Duplicate {
            kind: DuplicateKind::RegistryEntry
        }
    );

    let mut duplicate_external = basic_parts(2, "FIXTURE_TECNICA_B");
    duplicate_external.external_id =
        ExternalEntityId::new("fixture_tecnica.entity_1").expect("FIXTURE_TECNICA external");
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![
            first,
            EntityInput::new(duplicate_external)
        ]))
        .expect_err("duplicate external ID"),
        CatalogError::Duplicate {
            kind: DuplicateKind::ExternalEntityId
        }
    );

    let mut duplicate_alias = basic_parts(3, "FIXTURE_TECNICA_C");
    duplicate_alias.aliases = vec![
        alias("FIXTURE_TECNICA_a\u{301}", AliasProvenance::EntityRegistry),
        alias("FIXTURE_TECNICA_\u{e1}", AliasProvenance::EntityRegistry),
    ];
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![EntityInput::new(duplicate_alias)]))
            .expect_err("canonical duplicate aliases"),
        CatalogError::Duplicate {
            kind: DuplicateKind::Alias
        }
    );

    let capability =
        CapabilityId::new("fixture_tecnica:state_a").expect("FIXTURE_TECNICA capability");
    let mut duplicate_capability = basic_parts(4, "FIXTURE_TECNICA_D");
    duplicate_capability.capabilities = vec![capability.clone(), capability];
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![EntityInput::new(
            duplicate_capability
        )]))
        .expect_err("duplicate capability"),
        CatalogError::Duplicate {
            kind: DuplicateKind::Capability
        }
    );

    let mut mismatched_domain = basic_parts(5, "FIXTURE_TECNICA_E");
    mismatched_domain.domain =
        Domain::new("fixture_tecnica_other").expect("FIXTURE_TECNICA other domain");
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![EntityInput::new(
            mismatched_domain
        )]))
        .expect_err("external/domain mismatch"),
        CatalogError::InconsistentEntityDomain
    );
}

#[test]
fn fixture_tecnica_per_value_and_collection_limits_accept_exact_and_reject_one_over() {
    let prefix = "FIXTURE_TECNICA_";
    let exact_text = format!(
        "{prefix}{}",
        "X".repeat(MAX_SENSITIVE_TEXT_BYTES - prefix.len())
    );
    assert_eq!(sensitive(exact_text).len(), MAX_SENSITIVE_TEXT_BYTES);
    assert_eq!(
        SensitiveText::new(format!(
            "{prefix}{}",
            "X".repeat(MAX_SENSITIVE_TEXT_BYTES + 1 - prefix.len())
        )),
        Err(CatalogError::LimitExceeded {
            kind: LimitKind::SensitiveTextBytes,
            limit: MAX_SENSITIVE_TEXT_BYTES as u32
        })
    );

    let domain_prefix = "fixture_tecnica_";
    assert!(
        Domain::new(&format!(
            "{domain_prefix}{}",
            "x".repeat(MAX_DOMAIN_BYTES - domain_prefix.len())
        ))
        .is_ok()
    );
    assert_eq!(
        Domain::new(&format!(
            "{domain_prefix}{}",
            "x".repeat(MAX_DOMAIN_BYTES + 1 - domain_prefix.len())
        )),
        Err(CatalogError::InvalidDomain)
    );

    let mut exact_aliases = basic_parts(1, "FIXTURE_TECNICA_DISPLAY");
    exact_aliases.aliases = (0..MAX_ALIASES_PER_RECORD)
        .map(|index| {
            alias(
                format!("FIXTURE_TECNICA_ALIAS_{index}"),
                AliasProvenance::EntityRegistry,
            )
        })
        .collect();
    CatalogSnapshot::build(input_with_entities(vec![EntityInput::new(
        exact_aliases.clone(),
    )]))
    .expect("exact alias limit");
    exact_aliases.aliases.push(alias(
        "FIXTURE_TECNICA_ALIAS_ONE_OVER",
        AliasProvenance::EntityRegistry,
    ));
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(vec![EntityInput::new(exact_aliases)]))
            .expect_err("one-over aliases"),
        CatalogError::LimitExceeded {
            kind: LimitKind::AliasesPerRecord,
            limit: MAX_ALIASES_PER_RECORD as u32
        }
    );

    let domains = (0..=MAX_DESCRIPTOR_DOMAINS)
        .map(|index| {
            Domain::new(&format!("fixture_tecnica_domain_{index}"))
                .expect("FIXTURE_TECNICA descriptor domain")
        })
        .collect::<Vec<_>>();
    let mut exact = CatalogSnapshotInput::empty(generation(1));
    exact.capability_descriptors = vec![CapabilityDescriptorInput {
        id: CapabilityId::new("fixture_tecnica:state_a").expect("FIXTURE_TECNICA capability"),
        enabled: true,
        state_query_domains: domains[..MAX_DESCRIPTOR_DOMAINS].to_vec(),
    }];
    CatalogSnapshot::build(exact).expect("exact descriptor-domain limit");
    let mut over = CatalogSnapshotInput::empty(generation(1));
    over.capability_descriptors = vec![CapabilityDescriptorInput {
        id: CapabilityId::new("fixture_tecnica:state_a").expect("FIXTURE_TECNICA capability"),
        enabled: true,
        state_query_domains: domains,
    }];
    assert_eq!(
        CatalogSnapshot::build(over).expect_err("one-over descriptor domains"),
        CatalogError::LimitExceeded {
            kind: LimitKind::DescriptorDomains,
            limit: MAX_DESCRIPTOR_DOMAINS as u32
        }
    );
}

#[test]
fn fixture_tecnica_entity_candidate_and_query_counts_are_bounded() {
    let one_over_entities = (0..=MAX_ENTITIES)
        .map(|index| basic_entity(index + 1, format!("FIXTURE_TECNICA_ENTITY_{index}")))
        .collect();
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(one_over_entities))
            .expect_err("one-over entities"),
        CatalogError::LimitExceeded {
            kind: LimitKind::Entities,
            limit: MAX_ENTITIES as u32
        }
    );

    let shared_alias = "FIXTURE_TECNICA_SHARED_ALIAS";
    let candidate_entities = (0..=MAX_RESOLUTION_CANDIDATES)
        .map(|index| {
            let mut parts = basic_parts(index + 1, format!("FIXTURE_TECNICA_CANDIDATE_{index}"));
            parts
                .aliases
                .push(alias(shared_alias, AliasProvenance::EntityRegistry));
            EntityInput::new(parts)
        })
        .collect::<Vec<_>>();
    let exact_snapshot = CatalogSnapshot::build(input_with_entities(
        candidate_entities[..MAX_RESOLUTION_CANDIDATES].to_vec(),
    ))
    .expect("exact candidate limit");
    let source = RequestText::new(shared_alias.into()).expect("FIXTURE_TECNICA request");
    let query = ha_catalog::EntityQuery::new(
        &source,
        generation(1),
        span_for(&source, shared_alias),
        Vec::new(),
    )
    .expect("FIXTURE_TECNICA query");
    let EntityResolution::Clarification(clarification) =
        resolve_entity(&exact_snapshot, &query).expect("FIXTURE_TECNICA clarification")
    else {
        panic!("expected FIXTURE_TECNICA candidate clarification");
    };
    assert_eq!(clarification.options().len(), MAX_RESOLUTION_CANDIDATES);
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(candidate_entities))
            .expect_err("one-over candidate bucket"),
        CatalogError::LimitExceeded {
            kind: LimitKind::ResolutionCandidates,
            limit: MAX_RESOLUTION_CANDIDATES as u32
        }
    );

    let query_source = RequestText::new("FIXTURE_TECNICA_QUERY".into()).expect("request");
    let query_span = span_for(&query_source, "FIXTURE_TECNICA_QUERY");
    let constraints = (0..=MAX_QUERY_CONSTRAINTS)
        .map(|index| {
            EntityConstraint::capability(
                CapabilityId::new(&format!("fixture_tecnica:query_{index}"))
                    .expect("FIXTURE_TECNICA query capability"),
                query_span.clone(),
            )
        })
        .collect::<Vec<_>>();
    ha_catalog::EntityQuery::new(
        &query_source,
        generation(1),
        query_span.clone(),
        constraints[..MAX_QUERY_CONSTRAINTS].to_vec(),
    )
    .expect("exact query-constraint limit");
    assert_eq!(
        ha_catalog::EntityQuery::new(&query_source, generation(1), query_span, constraints)
            .expect_err("one-over query constraints"),
        CatalogError::LimitExceeded {
            kind: LimitKind::QueryConstraints,
            limit: MAX_QUERY_CONSTRAINTS as u32
        }
    );
}

#[test]
fn fixture_tecnica_aggregate_item_limit_accepts_exact_and_rejects_one_over() {
    let domains = (0..MAX_DESCRIPTOR_DOMAINS)
        .map(|index| {
            Domain::new(&format!("fixture_tecnica_domain_{index}"))
                .expect("FIXTURE_TECNICA descriptor domain")
        })
        .collect::<Vec<_>>();
    let mut descriptors = (0..256)
        .map(|index| CapabilityDescriptorInput {
            id: CapabilityId::new(&format!("fixture_tecnica:aggregate_{index}"))
                .expect("FIXTURE_TECNICA capability"),
            enabled: true,
            state_query_domains: domains[..127].to_vec(),
        })
        .collect::<Vec<_>>();
    assert_eq!(
        descriptors.len() + 127 * descriptors.len(),
        MAX_CATALOG_AGGREGATE_ITEMS
    );

    let mut exact = CatalogSnapshotInput::empty(generation(1));
    exact.capability_descriptors = descriptors.clone();
    CatalogSnapshot::build(exact).expect("exact aggregate-item limit");

    descriptors[0]
        .state_query_domains
        .push(domains[127].clone());
    let mut over = CatalogSnapshotInput::empty(generation(1));
    over.capability_descriptors = descriptors;
    assert_eq!(
        CatalogSnapshot::build(over).expect_err("one-over aggregate items"),
        CatalogError::LimitExceeded {
            kind: LimitKind::AggregateItems,
            limit: MAX_CATALOG_AGGREGATE_ITEMS as u32
        }
    );
}

#[test]
fn fixture_tecnica_aggregate_text_limit_accepts_exact_and_rejects_one_over() {
    let exact_entities = text_bound_entities(false);
    CatalogSnapshot::build(input_with_entities(exact_entities))
        .expect("exact aggregate-text limit");

    let over_entities = text_bound_entities(true);
    assert_eq!(
        CatalogSnapshot::build(input_with_entities(over_entities))
            .expect_err("one-over aggregate text"),
        CatalogError::LimitExceeded {
            kind: LimitKind::AggregateTextBytes,
            limit: MAX_CATALOG_TEXT_BYTES as u32
        }
    );
}

fn text_bound_entities(one_over: bool) -> Vec<EntityInput> {
    let domain = FIXTURE_TECNICA_DOMAIN.to_owned();
    let mut specifications = (0..MAX_ENTITIES)
        .map(|index| {
            (
                format!("{:032x}", index + 1),
                format!("fixture_tecnica.entity_{}", index + 1),
                format!("FIXTURE_TECNICA_TEXT_{index}_"),
            )
        })
        .collect::<Vec<_>>();
    let base_bytes = specifications
        .iter()
        .map(|(registry, external, display)| {
            registry.len() + external.len() + domain.len() + display.len()
        })
        .sum::<usize>();
    let mut remaining = MAX_CATALOG_TEXT_BYTES
        .checked_sub(base_bytes)
        .expect("FIXTURE_TECNICA text-limit capacity");
    for (_, _, display) in &mut specifications {
        let addition = remaining.min(MAX_SENSITIVE_TEXT_BYTES - display.len());
        display.push_str(&"X".repeat(addition));
        remaining -= addition;
    }
    assert_eq!(remaining, 0, "FIXTURE_TECNICA exact text limit");
    if one_over {
        let display = specifications
            .iter_mut()
            .find_map(|(_, _, display)| {
                (display.len() < MAX_SENSITIVE_TEXT_BYTES).then_some(display)
            })
            .expect("FIXTURE_TECNICA one-over capacity");
        display.push('X');
    }

    specifications
        .into_iter()
        .map(|(registry, external, display)| {
            EntityInput::new(EntityInputParts {
                generation: generation(1),
                registry_id: RegistryEntryId::new(&registry).expect("FIXTURE_TECNICA registry"),
                external_id: ExternalEntityId::new(&external).expect("FIXTURE_TECNICA external"),
                domain: Domain::new(&domain).expect("FIXTURE_TECNICA domain"),
                display_name: sensitive(display),
                aliases: Vec::new(),
                capabilities: Vec::new(),
                area_id: None,
                floor_id: None,
                device_id: None,
                visibility: EntityVisibility::exposed(),
            })
        })
        .collect()
}

#[test]
fn fixture_tecnica_resolution_is_complete_deterministic_and_constraint_bound() {
    let mut first = basic_parts(2, "FIXTURE_TECNICA_DISPLAY");
    first.aliases.push(alias(
        "FIXTURE_TECNICA_SHARED",
        AliasProvenance::EntityRegistry,
    ));
    let mut second = basic_parts(1, "FIXTURE_TECNICA_DISPLAY");
    second.aliases.push(alias(
        "FIXTURE_TECNICA_SHARED",
        AliasProvenance::EntityRegistry,
    ));

    let forward = CatalogSnapshot::build(input_with_entities(vec![
        EntityInput::new(first.clone()),
        EntityInput::new(second.clone()),
    ]))
    .expect("FIXTURE_TECNICA forward snapshot");
    let reverse = CatalogSnapshot::build(input_with_entities(vec![
        EntityInput::new(second),
        EntityInput::new(first),
    ]))
    .expect("FIXTURE_TECNICA reverse snapshot");

    let alias_source =
        RequestText::new("FIXTURE_TECNICA_SHARED".into()).expect("FIXTURE_TECNICA request");
    let alias_query = ha_catalog::EntityQuery::new(
        &alias_source,
        generation(1),
        span_for(&alias_source, "FIXTURE_TECNICA_SHARED"),
        Vec::new(),
    )
    .expect("FIXTURE_TECNICA alias query");
    let forward_result = resolve_entity(&forward, &alias_query).expect("forward resolution");
    let reverse_result = resolve_entity(&reverse, &alias_query).expect("reverse resolution");
    assert_eq!(forward_result, reverse_result);
    let EntityResolution::Clarification(alias_options) = forward_result else {
        panic!("expected FIXTURE_TECNICA alias clarification");
    };
    assert_eq!(alias_options.options().len(), 2);
    assert_eq!(alias_options.options()[0].registry_id(), &registry_id(1));
    assert_eq!(
        alias_options.options()[0].explanation().rank(),
        ResolutionRank::ExplicitRegistryAlias
    );

    let display_source =
        RequestText::new("FIXTURE_TECNICA_DISPLAY DOMAIN".into()).expect("request");
    let display_query = ha_catalog::EntityQuery::new(
        &display_source,
        generation(1),
        span_for(&display_source, "FIXTURE_TECNICA_DISPLAY"),
        vec![EntityConstraint::domain(
            Domain::new(FIXTURE_TECNICA_DOMAIN).expect("domain"),
            span_for(&display_source, "DOMAIN"),
        )],
    )
    .expect("qualified display query");
    let EntityResolution::Clarification(display_options) =
        resolve_entity(&forward, &display_query).expect("display clarification")
    else {
        panic!("expected FIXTURE_TECNICA display clarification");
    };
    assert_eq!(display_options.options().len(), 2);
    assert_eq!(
        display_options.options()[0].explanation().rank(),
        ResolutionRank::DisplayNameWithConstraint
    );
    assert_eq!(
        display_options.options()[0].explanation().factors()[0].kind(),
        RankingFactorKind::DisplayName
    );

    let unsupported = ha_catalog::EntityQuery::new(
        &display_source,
        generation(1),
        span_for(&display_source, "FIXTURE_TECNICA_DISPLAY"),
        vec![EntityConstraint::capability(
            CapabilityId::new("fixture_tecnica:missing").expect("capability"),
            span_for(&display_source, "DOMAIN"),
        )],
    )
    .expect("unsupported constraint query");
    assert_eq!(
        resolve_entity(&forward, &unsupported),
        Ok(EntityResolution::Abstained(ResolutionAbstention::NoMatch))
    );
}

#[test]
fn fixture_tecnica_higher_rank_constraint_mismatch_never_falls_through() {
    let mut alias_target = basic_parts(2, "fixture_tecnica.entity_1");
    alias_target.aliases.push(alias(
        "fixture_tecnica.entity_1",
        AliasProvenance::EntityRegistry,
    ));
    alias_target.domain =
        Domain::new("fixture_tecnica_other").expect("FIXTURE_TECNICA other domain");
    alias_target.external_id =
        ExternalEntityId::new("fixture_tecnica_other.entity_2").expect("external ID");
    let snapshot = CatalogSnapshot::build(input_with_entities(vec![
        basic_entity(1, "FIXTURE_TECNICA_EXTERNAL"),
        EntityInput::new(alias_target),
    ]))
    .expect("FIXTURE_TECNICA snapshot");
    let source = RequestText::new("fixture_tecnica.entity_1 DOMAIN".into()).expect("request");
    let query = ha_catalog::EntityQuery::new(
        &source,
        generation(1),
        span_for(&source, "fixture_tecnica.entity_1"),
        vec![EntityConstraint::domain(
            Domain::new("fixture_tecnica_other").expect("other domain"),
            span_for(&source, "DOMAIN"),
        )],
    )
    .expect("query");
    assert_eq!(
        resolve_entity(&snapshot, &query),
        Ok(EntityResolution::Abstained(
            ResolutionAbstention::ConstraintMismatch
        ))
    );
}

#[test]
fn fixture_tecnica_all_typed_constraints_produce_source_owned_factors() {
    let floor_id = FloorId::new(FIXTURE_TECNICA_FLOOR_ID).expect("floor");
    let area_id = AreaId::new(FIXTURE_TECNICA_AREA_ID).expect("area");
    let device_id = DeviceId::new(FIXTURE_TECNICA_DEVICE_ID).expect("device");
    let capability = CapabilityId::new("fixture_tecnica:state_a").expect("capability");
    let mut parts = basic_parts(1, "FIXTURE_TECNICA_DISPLAY");
    parts.area_id = Some(area_id.clone());
    parts.floor_id = Some(floor_id.clone());
    parts.device_id = Some(device_id.clone());
    parts.capabilities.push(capability.clone());

    let snapshot = CatalogSnapshot::build(CatalogSnapshotInput {
        generation: generation(1),
        floors: vec![FloorInput {
            id: floor_id.clone(),
            name: sensitive("FIXTURE_TECNICA_FLOOR"),
            aliases: Vec::new(),
        }],
        areas: vec![AreaInput {
            id: area_id.clone(),
            name: sensitive("FIXTURE_TECNICA_AREA"),
            aliases: Vec::new(),
            floor_id: Some(floor_id.clone()),
        }],
        devices: vec![DeviceInput {
            id: device_id.clone(),
            name: sensitive("FIXTURE_TECNICA_DEVICE"),
            area_id: Some(area_id.clone()),
        }],
        capability_descriptors: vec![CapabilityDescriptorInput {
            id: capability.clone(),
            enabled: true,
            state_query_domains: vec![Domain::new(FIXTURE_TECNICA_DOMAIN).expect("domain")],
        }],
        entities: vec![EntityInput::new(parts)],
    })
    .expect("associated snapshot");

    let source = RequestText::new("FIXTURE_TECNICA_DISPLAY D C A F V".into()).expect("request");
    let query = ha_catalog::EntityQuery::new(
        &source,
        generation(1),
        span_for(&source, "FIXTURE_TECNICA_DISPLAY"),
        vec![
            EntityConstraint::domain(
                Domain::new(FIXTURE_TECNICA_DOMAIN).expect("domain"),
                span_for(&source, "D"),
            ),
            EntityConstraint::capability(capability, span_for(&source, "C")),
            EntityConstraint::area(area_id, span_for(&source, "A")),
            EntityConstraint::floor(floor_id, span_for(&source, "F")),
            EntityConstraint::device(device_id, span_for(&source, "V")),
        ],
    )
    .expect("all-constraint query");
    let EntityResolution::Resolved(result) =
        resolve_entity(&snapshot, &query).expect("all-constraint resolution")
    else {
        panic!("expected FIXTURE_TECNICA resolved entity");
    };
    assert_eq!(result.explanation().factors().len(), 6);
    assert!(
        result
            .explanation()
            .factors()
            .iter()
            .all(|factor| factor.evidence().belongs_to(&source))
    );
}

#[test]
fn fixture_tecnica_descriptors_disable_narrow_and_extend_open_domains() {
    let capability = CapabilityId::new("fixture_tecnica:state_a").expect("capability");
    let future_domain = Domain::new("fixture_tecnica_future").expect("future domain");
    let mut parts = basic_parts(1, "FIXTURE_TECNICA_FUTURE");
    parts.external_id =
        ExternalEntityId::new("fixture_tecnica_future.entity_1").expect("future external");
    parts.domain = future_domain.clone();
    parts.capabilities.push(capability.clone());

    let build = |enabled: bool, domains: Vec<Domain>| {
        CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(1),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: vec![CapabilityDescriptorInput {
                id: capability.clone(),
                enabled,
                state_query_domains: domains,
            }],
            entities: vec![EntityInput::new(parts.clone())],
        })
        .expect("FIXTURE_TECNICA descriptor snapshot")
    };

    let not_reviewed = build(
        true,
        vec![Domain::new(FIXTURE_TECNICA_DOMAIN).expect("domain")],
    );
    assert_eq!(
        not_reviewed.state_query_disposition(&registry_id(1), &capability),
        Some(StateQueryDisposition::AbstainDomainNotReviewed)
    );
    let extended = build(true, vec![future_domain.clone()]);
    assert_eq!(
        extended.state_query_disposition(&registry_id(1), &capability),
        Some(StateQueryDisposition::Supported)
    );
    let disabled = build(false, vec![future_domain]);
    assert_eq!(
        disabled.state_query_disposition(&registry_id(1), &capability),
        Some(StateQueryDisposition::AbstainDescriptorDisabled)
    );
}

#[test]
fn fixture_tecnica_debug_display_and_errors_redact_every_residential_canary() {
    let canary = "fixture_tecnica_private_canary";
    let capability = CapabilityId::new("fixture_tecnica:private_canary").expect("capability");
    let mut parts = basic_parts(1, canary);
    parts.external_id = ExternalEntityId::new("fixture_tecnica.fixture_tecnica_private_canary")
        .expect("canary external");
    parts
        .aliases
        .push(alias(canary, AliasProvenance::EntityRegistry));
    parts.capabilities.push(capability.clone());
    let input = CatalogSnapshotInput {
        generation: generation(1),
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors: vec![CapabilityDescriptorInput {
            id: capability,
            enabled: false,
            state_query_domains: vec![Domain::new(FIXTURE_TECNICA_DOMAIN).expect("domain")],
        }],
        entities: vec![EntityInput::new(parts)],
    };
    let input_debug = format!("{input:?}");
    let snapshot = Arc::new(CatalogSnapshot::build(input).expect("canary snapshot"));
    let source = RequestText::new(canary.into()).expect("canary request");
    let query = ha_catalog::EntityQuery::new(
        &source,
        generation(1),
        span_for(&source, canary),
        Vec::new(),
    )
    .expect("canary query");
    let result = resolve_entity(&snapshot, &query).expect("canary resolution");
    let store = CatalogStore::new(Arc::clone(&snapshot));
    let rendered = format!(
        "{input_debug} {snapshot:?} {:?} {:?} {:?} {query:?} {result:?} {store:?} {:?} {}",
        snapshot.entities(),
        snapshot.capability_descriptors(),
        snapshot.entity(&registry_id(1)),
        CatalogError::InvalidSensitiveText,
        CatalogError::InvalidSensitiveText
    );
    assert!(!rendered.contains(canary));
}
