use addon_runtime::{CatalogSeed, SupervisorCommand, SupervisorSnapshot};
use ha_catalog::{AreaId, DeviceId, FloorId, RegistryEntryId};
use serde_json::{Value, json};

const FIXTURE_TECNICA_REGISTRY_LIGHT: &str = "11111111111111111111111111111111";
const FIXTURE_TECNICA_REGISTRY_SWITCH: &str = "22222222222222222222222222222222";
const FIXTURE_TECNICA_CROSS_LANGUAGE_GENERATION: u64 = 5_039_107_199_423_227_829;
const FIXTURE_TECNICA_TOPOLOGY_GENERATION: u64 = 3_570_507_965_339_449_943;

fn snapshot(entities: Value, states: Value, exposed: Value, services: Value) -> SupervisorSnapshot {
    SupervisorSnapshot::from_results([
        (SupervisorCommand::AreaRegistryList, json!([])),
        (SupervisorCommand::DeviceRegistryList, json!([])),
        (SupervisorCommand::EntityRegistryList, entities),
        (SupervisorCommand::FloorRegistryList, json!([])),
        (SupervisorCommand::GetServices, services),
        (SupervisorCommand::GetStates, states),
        (SupervisorCommand::ExposeEntityList, exposed),
    ])
    .expect("FIXTURE_TECNICA snapshot")
}

fn fixture_entities() -> Value {
    json!([
        {
            "aliases": [],
            "disabled_by": null,
            "entity_id": "light.fixture_tecnica_a",
            "id": FIXTURE_TECNICA_REGISTRY_LIGHT,
            "name": "FIXTURE_TECNICA_LIGHT",
            "original_name": null
        },
        {
            "aliases": [],
            "disabled_by": null,
            "entity_id": "switch.fixture_tecnica_b",
            "id": FIXTURE_TECNICA_REGISTRY_SWITCH,
            "name": null,
            "original_name": "FIXTURE_TECNICA_SWITCH"
        },
        {
            "aliases": [],
            "disabled_by": "user",
            "entity_id": "light.fixture_tecnica_disabled",
            "id": "33333333333333333333333333333333",
            "name": "FIXTURE_TECNICA_DISABLED",
            "original_name": null
        }
    ])
}

fn fixture_states() -> Value {
    json!([
        {
            "attributes": {"friendly_name": "FIXTURE_TECNICA_STATE_LIGHT"},
            "entity_id": "light.fixture_tecnica_a",
            "state": "on"
        },
        {
            "attributes": {"friendly_name": "FIXTURE_TECNICA_STATE_SWITCH"},
            "entity_id": "switch.fixture_tecnica_b",
            "state": "off"
        }
    ])
}

fn fixture_exposure() -> Value {
    json!({
        "exposed_entities": {
            "light.fixture_tecnica_a": {"conversation": true},
            "switch.fixture_tecnica_b": {"conversation": true}
        }
    })
}

fn topology_snapshot() -> SupervisorSnapshot {
    let mut entities = fixture_entities();
    let values = entities
        .as_array_mut()
        .expect("FIXTURE_TECNICA entity array");
    values[0]["area_id"] = json!("fixture_area");
    values[0]["device_id"] = json!("33333333333333333333333333333333");
    values[1]["area_id"] = Value::Null;
    values[1]["device_id"] = Value::Null;
    values[2]["area_id"] = Value::Null;
    values[2]["device_id"] = Value::Null;
    SupervisorSnapshot::from_results([
        (
            SupervisorCommand::AreaRegistryList,
            json!([{
                "aliases": ["FIXTURE_TECNICA_AREA_ALIAS"],
                "area_id": "fixture_area",
                "floor_id": "fixture_floor",
                "name": "FIXTURE_TECNICA_AREA"
            }]),
        ),
        (
            SupervisorCommand::DeviceRegistryList,
            json!([{
                "area_id": "fixture_area",
                "disabled_by": null,
                "id": "33333333333333333333333333333333",
                "name": "FIXTURE_TECNICA_DEVICE",
                "name_by_user": null
            }]),
        ),
        (SupervisorCommand::EntityRegistryList, entities),
        (
            SupervisorCommand::FloorRegistryList,
            json!([{
                "aliases": ["FIXTURE_TECNICA_FLOOR_ALIAS"],
                "floor_id": "fixture_floor",
                "name": "FIXTURE_TECNICA_FLOOR"
            }]),
        ),
        (
            SupervisorCommand::GetServices,
            json!({"light": {"turn_on": {}}, "switch": {"turn_off": {}}}),
        ),
        (SupervisorCommand::GetStates, fixture_states()),
        (SupervisorCommand::ExposeEntityList, fixture_exposure()),
    ])
    .expect("FIXTURE_TECNICA topology snapshot")
}

#[test]
fn live_snapshot_builds_only_enabled_exposed_current_entities() {
    let seed = CatalogSeed::from_supervisor_snapshot(&snapshot(
        fixture_entities(),
        fixture_states(),
        fixture_exposure(),
        json!({
            "light": {"turn_on": {}},
            "switch": {"turn_off": {}}
        }),
    ))
    .expect("FIXTURE_TECNICA seed");

    assert_eq!(seed.entities().len(), 2);
    assert_eq!(
        seed.entities()[0].registry_id(),
        FIXTURE_TECNICA_REGISTRY_LIGHT
    );
    assert_eq!(seed.entities()[0].external_id(), "light.fixture_tecnica_a");
    assert_eq!(
        seed.entities()[1].registry_id(),
        FIXTURE_TECNICA_REGISTRY_SWITCH
    );
    assert_eq!(seed.generation(), FIXTURE_TECNICA_CROSS_LANGUAGE_GENERATION);
    assert!(seed.generation() <= i64::MAX as u64);
    assert!(!format!("{seed:?}").contains("FIXTURE_TECNICA_LIGHT"));
}

#[test]
fn generation_is_insertion_stable_and_changes_with_semantic_catalog() {
    let first = CatalogSeed::from_supervisor_snapshot(&snapshot(
        fixture_entities(),
        fixture_states(),
        fixture_exposure(),
        json!({"light": {"turn_on": {}}, "switch": {"turn_off": {}}}),
    ))
    .expect("FIXTURE_TECNICA first");
    let mut reversed = fixture_entities()
        .as_array()
        .expect("FIXTURE_TECNICA array")
        .clone();
    reversed.reverse();
    let second = CatalogSeed::from_supervisor_snapshot(&snapshot(
        Value::Array(reversed),
        fixture_states(),
        fixture_exposure(),
        json!({"light": {"turn_on": {}}, "switch": {"turn_off": {}}}),
    ))
    .expect("FIXTURE_TECNICA second");
    let narrowed = CatalogSeed::from_supervisor_snapshot(&snapshot(
        fixture_entities(),
        fixture_states(),
        fixture_exposure(),
        json!({}),
    ))
    .expect("FIXTURE_TECNICA narrowed");

    assert_eq!(first.generation(), second.generation());
    assert_ne!(first.generation(), narrowed.generation());
}

#[test]
fn explicit_entity_aliases_are_canonical_and_preserved() {
    let mut entities = fixture_entities();
    entities[0]["aliases"] = json!(["FIXTURE_TECNICA_ALIAS_Z", "FIXTURE_TECNICA_ALIAS_A"]);
    let first = CatalogSeed::from_supervisor_snapshot(&snapshot(
        entities.clone(),
        fixture_states(),
        fixture_exposure(),
        json!({}),
    ))
    .expect("FIXTURE_TECNICA aliased seed");
    entities[0]["aliases"] = json!(["FIXTURE_TECNICA_ALIAS_A", "FIXTURE_TECNICA_ALIAS_Z"]);
    let second = CatalogSeed::from_supervisor_snapshot(&snapshot(
        entities,
        fixture_states(),
        fixture_exposure(),
        json!({}),
    ))
    .expect("FIXTURE_TECNICA reordered alias seed");

    assert_eq!(first.generation(), second.generation());
    let catalog = first
        .into_snapshot()
        .expect("FIXTURE_TECNICA aliased catalog");
    let registry_id =
        RegistryEntryId::new(FIXTURE_TECNICA_REGISTRY_LIGHT).expect("FIXTURE_TECNICA registry");
    let aliases = catalog
        .entity(&registry_id)
        .expect("FIXTURE_TECNICA entity")
        .aliases()
        .iter()
        .map(|alias| alias.text().as_str())
        .collect::<Vec<_>>();
    assert_eq!(
        aliases,
        ["FIXTURE_TECNICA_ALIAS_A", "FIXTURE_TECNICA_ALIAS_Z"]
    );
}

#[test]
fn topology_is_preserved_and_changes_the_cross_language_generation() {
    let seed = CatalogSeed::from_supervisor_snapshot(&topology_snapshot())
        .expect("FIXTURE_TECNICA topology seed");
    assert_eq!(seed.generation(), FIXTURE_TECNICA_TOPOLOGY_GENERATION);

    let snapshot = seed
        .into_snapshot()
        .expect("FIXTURE_TECNICA topology catalog");
    let registry_id =
        RegistryEntryId::new(FIXTURE_TECNICA_REGISTRY_LIGHT).expect("FIXTURE_TECNICA registry");
    let entity = snapshot
        .entity(&registry_id)
        .expect("FIXTURE_TECNICA entity");
    assert_eq!(
        entity.area_id(),
        Some(&AreaId::new("fixture_area").expect("FIXTURE_TECNICA area"))
    );
    assert_eq!(
        entity.floor_id(),
        Some(&FloorId::new("fixture_floor").expect("FIXTURE_TECNICA floor"))
    );
    assert_eq!(
        entity.device_id(),
        Some(&DeviceId::new("33333333333333333333333333333333").expect("FIXTURE_TECNICA device"))
    );
}

#[test]
fn malformed_or_stale_live_inputs_fail_closed() {
    let missing_state = CatalogSeed::from_supervisor_snapshot(&snapshot(
        fixture_entities(),
        json!([]),
        fixture_exposure(),
        json!({}),
    ));
    assert!(missing_state.is_err());

    let malformed_exposure = CatalogSeed::from_supervisor_snapshot(&snapshot(
        fixture_entities(),
        fixture_states(),
        json!({"exposed_entities": []}),
        json!({}),
    ));
    assert!(malformed_exposure.is_err());

    let mut missing_aliases = fixture_entities();
    missing_aliases[0]
        .as_object_mut()
        .expect("FIXTURE_TECNICA entity")
        .remove("aliases");
    assert!(
        CatalogSeed::from_supervisor_snapshot(&snapshot(
            missing_aliases,
            fixture_states(),
            fixture_exposure(),
            json!({}),
        ))
        .is_err()
    );

    let mut duplicate_aliases = fixture_entities();
    duplicate_aliases[0]["aliases"] =
        json!(["FIXTURE_TECNICA_DUPLICATE", "FIXTURE_TECNICA_DUPLICATE"]);
    assert!(
        CatalogSeed::from_supervisor_snapshot(&snapshot(
            duplicate_aliases,
            fixture_states(),
            fixture_exposure(),
            json!({}),
        ))
        .is_err()
    );

    let mut null_aliases = fixture_entities();
    null_aliases[0]["aliases"] = Value::Null;
    assert!(
        CatalogSeed::from_supervisor_snapshot(&snapshot(
            null_aliases,
            fixture_states(),
            fixture_exposure(),
            json!({}),
        ))
        .is_err()
    );
}
