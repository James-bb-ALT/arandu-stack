use nlu_core::{CatalogGeneration, EntityId, EntityRef, RequestText, SlotValue};

use crate::{
    PlanEngineErrorCode, core_adapter,
    model::{DraftExecutionClass, DraftPolarity, NodeDraft, PlanDraft, ResolvedBinding},
    table::{Shape, TEMPLATES},
};

#[test]
fn closed_table_maps_every_p09_intent_to_the_frozen_p02_contract() {
    let expected = [
        (
            "ha:hass_broadcast",
            "ha:broadcast",
            "ha:broadcast",
            Shape::Single,
        ),
        (
            "ha:hass_cancel_all_timers",
            "ha:timer_control",
            "ha:cancel_all_timers",
            Shape::Single,
        ),
        (
            "ha:hass_cancel_timer",
            "ha:timer_control",
            "ha:cancel_timer",
            Shape::Single,
        ),
        (
            "ha:hass_climate_get_temperature",
            "ha:temperature_query",
            "ha:get_temperature",
            Shape::Single,
        ),
        (
            "ha:hass_decrease_timer",
            "ha:timer_control",
            "ha:decrease_timer",
            Shape::Single,
        ),
        (
            "ha:hass_get_current_date",
            "ha:date_query",
            "ha:get_current_date",
            Shape::Single,
        ),
        (
            "ha:hass_get_current_time",
            "ha:time_query",
            "ha:get_current_time",
            Shape::Single,
        ),
        (
            "ha:hass_get_state",
            "ha:state_query",
            "ha:get_state",
            Shape::Single,
        ),
        (
            "ha:hass_increase_timer",
            "ha:timer_control",
            "ha:increase_timer",
            Shape::Single,
        ),
        (
            "ha:hass_nevermind",
            "ha:conversation_control",
            "ha:nevermind",
            Shape::Single,
        ),
        (
            "ha:hass_pause_timer",
            "ha:timer_control",
            "ha:pause_timer",
            Shape::Single,
        ),
        (
            "ha:hass_respond",
            "ha:response",
            "ha:respond",
            Shape::Single,
        ),
        (
            "ha:hass_set_position",
            "ha:cover_control",
            "ha:set_position",
            Shape::Single,
        ),
        (
            "ha:hass_start_timer",
            "ha:timer_control",
            "ha:start_timer",
            Shape::OrderedTimer,
        ),
        (
            "ha:hass_stop_moving",
            "ha:cover_control",
            "ha:stop_moving",
            Shape::Single,
        ),
        (
            "ha:hass_timer_status",
            "ha:timer_query",
            "ha:timer_status",
            Shape::Single,
        ),
        (
            "ha:hass_toggle",
            "ha:fan_control",
            "ha:toggle",
            Shape::Single,
        ),
        (
            "ha:hass_turn_off",
            "ha:switch_control",
            "ha:turn_off",
            Shape::Single,
        ),
        (
            "ha:hass_turn_on",
            "ha:light_control",
            "ha:turn_on",
            Shape::ParallelTurnOn,
        ),
        (
            "ha:hass_unpause_timer",
            "ha:timer_control",
            "ha:unpause_timer",
            Shape::Single,
        ),
    ];

    assert_eq!(TEMPLATES.len(), expected.len());
    for (template, expected) in TEMPLATES.iter().zip(expected) {
        assert_eq!(
            (
                template.intent,
                template.capability,
                template.operation,
                template.shape,
            ),
            expected
        );
    }
}

#[test]
fn core_adapter_rejects_a_stale_resolved_entity_generation() {
    let source = RequestText::new("FIXTURE_TECNICA".into()).expect("FIXTURE_TECNICA source");
    let predicate = source.span(0, 1).expect("FIXTURE_TECNICA predicate");
    let argument = source.span(1, 2).expect("FIXTURE_TECNICA argument");
    let stale_entity = EntityRef::new(
        EntityId::new("fixture_tecnica:stale_entity").expect("FIXTURE_TECNICA entity ID"),
        CatalogGeneration::new(2).expect("FIXTURE_TECNICA stale generation"),
    );
    let draft = PlanDraft {
        execution_class: DraftExecutionClass::PartialSafe,
        nodes: vec![NodeDraft {
            id: "p11:node_1",
            intent: "ha:hass_turn_off",
            capability: "ha:switch_control",
            operation: "ha:turn_off",
            polarity: DraftPolarity::Affirmed,
            predicate,
            negation: None,
            slots: vec![ResolvedBinding {
                slot_id: "ha:entity",
                value: SlotValue::Entity(stale_entity),
                evidence: argument,
            }],
        }],
        relations: Vec::new(),
        independent_pairs: Vec::new(),
        argument_shares: Vec::new(),
    };

    let error = core_adapter::build(
        &source,
        CatalogGeneration::new(1).expect("FIXTURE_TECNICA current generation"),
        draft,
    )
    .expect_err("stale entity generation must fail closed");
    assert_eq!(error.code(), PlanEngineErrorCode::CoreContract);
}
