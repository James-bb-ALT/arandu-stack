use local_nlu::contextual::{
    contract::{Area, Catalog, ContextRequest, Entity, PartialRequest},
    interpret, interpret_partial, register_catalog,
};
use std::collections::BTreeMap;

fn test_catalog(generation: &str) -> Catalog {
    Catalog {
        version: 4,
        generation: generation.to_owned(),
        areas: vec![
            Area {
                area_id: "quarto".into(),
                names: vec!["quarto".into(), "dormitorio".into()],
            },
            Area {
                area_id: "sala".into(),
                names: vec!["sala".into()],
            },
        ],
        entities: vec![
            Entity {
                registry_id: "tv_quarto_1".into(),
                entity_id: "media_player.tv_quarto".into(),
                domain: "media_player".into(),
                name: "Televisão".into(),
                device_name: None,
                aliases: vec!["tv".into()],
                area_id: Some("quarto".into()),
                device_id: Some("dev_tv_1".into()),
                device_class: Some("tv".into()),
                actions: vec![
                    "turn_on".into(),
                    "turn_off".into(),
                    "play".into(),
                    "pause".into(),
                ],
                attributes: BTreeMap::new(),
                preferred: false,
            },
            Entity {
                registry_id: "luz_quarto_1".into(),
                entity_id: "light.luz_quarto".into(),
                domain: "light".into(),
                name: "Luz do Quarto".into(),
                device_name: None,
                aliases: vec!["luz".into()],
                area_id: Some("quarto".into()),
                device_id: None,
                device_class: None,
                actions: vec!["turn_on".into(), "turn_off".into(), "brightness".into()],
                attributes: BTreeMap::new(),
                preferred: false,
            },
            Entity {
                registry_id: "ar_quarto_1".into(),
                entity_id: "climate.ar_quarto".into(),
                domain: "climate".into(),
                name: "Ar-condicionado".into(),
                device_name: None,
                aliases: vec!["ar".into()],
                area_id: Some("quarto".into()),
                device_id: None,
                device_class: None,
                actions: vec!["turn_on".into(), "turn_off".into()],
                attributes: BTreeMap::new(),
                preferred: false,
            },
        ],
        groups: Vec::new(),
    }
}

fn partial_req(generation: &str, text: &str) -> PartialRequest {
    PartialRequest {
        version: 5,
        request_id: "req-test-1".into(),
        generation: generation.to_owned(),
        identity_scope_hash: "b".repeat(64),
        text: text.into(),
        origin_area: Some("quarto".into()),
        origin_area_source: Some("satellite".into()),
        origin_context_id: Some("ctx-1".into()),
        last_area: None,
        last_targets: Vec::new(),
        pending: None,
        antecedent: None,
    }
}

#[test]
fn partial_extracts_target_without_verb_and_freezes_slots() {
    let generation = "a".repeat(64);
    let reg = register_catalog(test_catalog(&generation));
    assert_eq!(reg.status, "catalog_ready");

    let out = interpret_partial(&partial_req(&generation, "lingua a tv do quarto"));
    assert_eq!(out.version, 5);
    assert_eq!(out.request_kind, "possible_device_action");
    assert_eq!(out.clauses.len(), 1);
    let c = &out.clauses[0];
    assert_eq!(c.slots["target"].status, "resolved");
    assert_eq!(c.slots["target"].value, "tv_quarto_1");
    assert_eq!(c.slots["area"].status, "resolved");
    assert_eq!(c.slots["area"].value, "quarto");
    assert_eq!(c.slots["action"].status, "unknown");
    assert!(c.allowed_actions.contains(&"turn_on".to_string()));
    assert!(c.unresolved.contains(&"action".to_string()));
}

#[test]
fn partial_preserves_safety_refusal_and_constraints() {
    let generation = "c".repeat(64);
    let _ = register_catalog(test_catalog(&generation));

    let neg = interpret_partial(&partial_req(&generation, "não liga a tv"));
    assert_eq!(neg.request_kind, "safety_refusal");
    assert!(!neg.safety_flags.is_empty());
    assert!(neg.clauses[0].constraints.negated);

    let cond = interpret_partial(&partial_req(
        &generation,
        "se passar de 28 graus liga a luz",
    ));
    assert_eq!(cond.request_kind, "safety_refusal");
    assert!(cond.clauses[0].constraints.conditional);
}

#[test]
fn partial_marks_resolved_plan_and_v4_remains_compatible() {
    let generation = "d".repeat(64);
    let _ = register_catalog(test_catalog(&generation));

    let v4 = interpret(&ContextRequest {
        version: 4,
        generation: generation.clone(),
        text: "liga a tv do quarto".into(),
        origin_area: Some("quarto".into()),
        origin_area_source: Some("satellite".into()),
        last_area: None,
        last_targets: Vec::new(),
        pending: None,
        antecedent: None,
    });
    assert_eq!(v4.status, "plan");

    let v5 = interpret_partial(&partial_req(&generation, "liga a tv do quarto"));
    assert_eq!(v5.request_kind, "resolved_plan");
    assert!(v5.clauses[0].unresolved.is_empty());
    assert_eq!(v5.clauses[0].slots["action"].value, "turn_on");
    assert_eq!(v5.clauses[0].slots["target"].value, "tv_quarto_1");
}

#[test]
fn partial_preserves_exclusions_and_multiclause() {
    let generation = "e".repeat(64);
    let _ = register_catalog(test_catalog(&generation));

    let out = interpret_partial(&partial_req(&generation, "lingua o quarto menos o ar"));
    assert_eq!(out.request_kind, "possible_device_action");
    assert_eq!(
        out.clauses[0].constraints.excluded_targets,
        vec!["ar_quarto_1"]
    );
}
