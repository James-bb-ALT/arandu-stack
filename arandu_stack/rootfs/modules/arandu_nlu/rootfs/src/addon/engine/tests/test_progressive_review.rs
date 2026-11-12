use local_nlu::contextual::{
    contract::{Area, Catalog, Entity, PartialRequest, ProposalRepair, ReviewProposalRequest},
    interpret_partial, register_catalog, review_proposal,
};
use serde_json::json;
use std::collections::BTreeMap;

fn test_catalog(generation: &str) -> Catalog {
    Catalog {
        version: 4,
        generation: generation.to_owned(),
        areas: vec![Area {
            area_id: "quarto".into(),
            names: vec!["quarto".into()],
        }],
        entities: vec![
            Entity {
                registry_id: "tv_quarto_1".into(),
                entity_id: "media_player.tv_quarto".into(),
                domain: "media_player".into(),
                name: "Televisão".into(),
                device_name: None,
                aliases: vec!["tv".into()],
                area_id: Some("quarto".into()),
                device_id: None,
                device_class: Some("tv".into()),
                actions: vec!["turn_on".into(), "turn_off".into()],
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

#[test]
fn review_accepts_valid_action_repair_and_requires_confirmation() {
    let generation = "1".repeat(64);
    let _ = register_catalog(test_catalog(&generation));

    let partial = interpret_partial(&PartialRequest {
        version: 5,
        request_id: "rev-1".into(),
        generation,
        identity_scope_hash: "2".repeat(64),
        text: "lingua a tv do quarto".into(),
        origin_area: Some("quarto".into()),
        origin_area_source: Some("satellite".into()),
        origin_context_id: None,
        last_area: None,
        last_targets: Vec::new(),
        pending: None,
        antecedent: None,
    });

    let reviewed = review_proposal(&ReviewProposalRequest {
        version: 5,
        partial,
        repairs: vec![ProposalRepair {
            clause_id: 0,
            slot: "action".into(),
            value: json!("turn_on"),
            source: "decision_ranked".into(),
            evidence: vec!["phonetic_ptbr".into()],
        }],
    });
    assert!(reviewed.accepted);
    assert_eq!(reviewed.status, "plan");
    assert!(reviewed.requires_confirmation);
    assert_eq!(reviewed.operations.len(), 1);
    assert_eq!(reviewed.operations[0].action, "turn_on");
    assert_eq!(reviewed.operations[0].targets, vec!["tv_quarto_1"]);
}

#[test]
fn review_rejects_overwriting_frozen_target_or_unsupported_action() {
    let generation = "3".repeat(64);
    let _ = register_catalog(test_catalog(&generation));

    let partial = interpret_partial(&PartialRequest {
        version: 5,
        request_id: "rev-2".into(),
        generation,
        identity_scope_hash: "4".repeat(64),
        text: "lingua a tv do quarto".into(),
        origin_area: Some("quarto".into()),
        origin_area_source: Some("satellite".into()),
        origin_context_id: None,
        last_area: None,
        last_targets: Vec::new(),
        pending: None,
        antecedent: None,
    });

    let overwrite_target = review_proposal(&ReviewProposalRequest {
        version: 5,
        partial: partial.clone(),
        repairs: vec![ProposalRepair {
            clause_id: 0,
            slot: "target".into(),
            value: json!("ar_quarto_1"),
            source: "llm_proposed".into(),
            evidence: vec![],
        }],
    });
    assert!(!overwrite_target.accepted);
    assert_eq!(overwrite_target.reason.as_deref(), Some("immutable_slot"));

    let unsupported_action = review_proposal(&ReviewProposalRequest {
        version: 5,
        partial,
        repairs: vec![ProposalRepair {
            clause_id: 0,
            slot: "action".into(),
            value: json!("unlock"),
            source: "decision_ranked".into(),
            evidence: vec![],
        }],
    });
    assert!(!unsupported_action.accepted);
    assert_eq!(
        unsupported_action.reason.as_deref(),
        Some("action_not_allowed_for_target")
    );
}

#[test]
fn review_rejects_safety_locked_partial() {
    let generation = "5".repeat(64);
    let _ = register_catalog(test_catalog(&generation));

    let partial = interpret_partial(&PartialRequest {
        version: 5,
        request_id: "rev-3".into(),
        generation,
        identity_scope_hash: "6".repeat(64),
        text: "não liga a tv".into(),
        origin_area: Some("quarto".into()),
        origin_area_source: Some("satellite".into()),
        origin_context_id: None,
        last_area: None,
        last_targets: Vec::new(),
        pending: None,
        antecedent: None,
    });

    let reviewed = review_proposal(&ReviewProposalRequest {
        version: 5,
        partial,
        repairs: vec![ProposalRepair {
            clause_id: 0,
            slot: "action".into(),
            value: json!("turn_on"),
            source: "llm_proposed".into(),
            evidence: vec![],
        }],
    });
    assert!(!reviewed.accepted);
    assert_eq!(reviewed.reason.as_deref(), Some("safety_locked"));
}
