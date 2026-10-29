use std::collections::BTreeSet;

use nlu_core::{CapabilityId, IntentId, OperationId, SlotId};

use crate::{MAX_COMPOSITION_TEMPLATES, PlanEngineError, PlanEngineErrorCode, Result};

pub(crate) const TEMPLATE_COUNT: usize = 20;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum BindingKind {
    Mention,
    Text,
    Integer { scale: i64 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct BindingSpec {
    pub(crate) slot_id: &'static str,
    pub(crate) role: &'static str,
    pub(crate) occurrence: u16,
    pub(crate) kind: BindingKind,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Shape {
    Single,
    ParallelTurnOn,
    OrderedTimer,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct TemplateSpec {
    pub(crate) intent: &'static str,
    pub(crate) capability: &'static str,
    pub(crate) operation: &'static str,
    pub(crate) shape: Shape,
    pub(crate) bindings: &'static [BindingSpec],
    pub(crate) literals: &'static [&'static str],
}

const ENTITY_TARGET: BindingSpec = BindingSpec {
    slot_id: "ha:entity",
    role: "target",
    occurrence: 0,
    kind: BindingKind::Mention,
};
const ENTITY_TIMER: BindingSpec = BindingSpec {
    slot_id: "ha:entity",
    role: "timer",
    occurrence: 0,
    kind: BindingKind::Mention,
};

const TURN_OFF_BINDINGS: &[BindingSpec] = &[ENTITY_TARGET];
const TURN_OFF_LITERALS: &[&str] = &["desligue ", ""];
const TURN_ON_BINDINGS: &[BindingSpec] = &[
    ENTITY_TARGET,
    BindingSpec {
        occurrence: 1,
        ..ENTITY_TARGET
    },
];
const TURN_ON_LITERALS: &[&str] = &["ligue ", " e ", ""];
const TOGGLE_BINDINGS: &[BindingSpec] = &[ENTITY_TARGET];
const TOGGLE_LITERALS: &[&str] = &["altere ", ""];
const GET_STATE_BINDINGS: &[BindingSpec] = &[ENTITY_TARGET];
const GET_STATE_LITERALS: &[&str] = &["consulte ", ""];
const NEVERMIND_BINDINGS: &[BindingSpec] = &[BindingSpec {
    slot_id: "ha:pending_action",
    role: "pending_action",
    occurrence: 0,
    kind: BindingKind::Text,
}];
const NEVERMIND_LITERALS: &[&str] = &["cancele ", ""];
const SET_POSITION_BINDINGS: &[BindingSpec] = &[
    ENTITY_TARGET,
    BindingSpec {
        slot_id: "ha:position",
        role: "position",
        occurrence: 0,
        kind: BindingKind::Integer { scale: 1 },
    },
];
const SET_POSITION_LITERALS: &[&str] = &["ajuste ", " para ", " por cento"];
const STOP_MOVING_BINDINGS: &[BindingSpec] = &[ENTITY_TARGET];
const STOP_MOVING_LITERALS: &[&str] = &["pare ", ""];
const START_TIMER_BINDINGS: &[BindingSpec] = &[
    BindingSpec {
        slot_id: "ha:timer",
        role: "timer",
        occurrence: 0,
        kind: BindingKind::Mention,
    },
    BindingSpec {
        slot_id: "ha:duration_seconds",
        role: "duration",
        occurrence: 0,
        kind: BindingKind::Integer { scale: 60 },
    },
];
const START_TIMER_LITERALS: &[&str] = &["inicie ", " por ", " minutos e consulte o estado depois"];
const CANCEL_TIMER_BINDINGS: &[BindingSpec] = &[ENTITY_TIMER];
const CANCEL_TIMER_LITERALS: &[&str] = &["cancele ", ""];
const CANCEL_ALL_BINDINGS: &[BindingSpec] = &[BindingSpec {
    slot_id: "ha:area",
    role: "area",
    occurrence: 0,
    kind: BindingKind::Text,
}];
const CANCEL_ALL_LITERALS: &[&str] = &["cancele todos os temporizadores do setor ", ""];
const INCREASE_TIMER_BINDINGS: &[BindingSpec] = &[
    BindingSpec {
        slot_id: "ha:timer",
        role: "timer",
        occurrence: 0,
        kind: BindingKind::Mention,
    },
    BindingSpec {
        slot_id: "ha:duration_delta_seconds",
        role: "duration_delta",
        occurrence: 0,
        kind: BindingKind::Integer { scale: 60 },
    },
];
const INCREASE_TIMER_LITERALS: &[&str] = &["aumente ", " em ", " minutos"];
const DECREASE_TIMER_BINDINGS: &[BindingSpec] = &[
    BindingSpec {
        slot_id: "ha:timer",
        role: "timer",
        occurrence: 0,
        kind: BindingKind::Mention,
    },
    BindingSpec {
        slot_id: "ha:duration_delta_seconds",
        role: "duration_delta",
        occurrence: 0,
        kind: BindingKind::Integer { scale: 60 },
    },
];
const DECREASE_TIMER_LITERALS: &[&str] = &["reduza ", " em ", " minutos"];
const PAUSE_TIMER_BINDINGS: &[BindingSpec] = &[ENTITY_TIMER];
const PAUSE_TIMER_LITERALS: &[&str] = &["pause ", ""];
const UNPAUSE_TIMER_BINDINGS: &[BindingSpec] = &[ENTITY_TIMER];
const UNPAUSE_TIMER_LITERALS: &[&str] = &["continue ", ""];
const TIMER_STATUS_BINDINGS: &[BindingSpec] = &[ENTITY_TIMER];
const TIMER_STATUS_LITERALS: &[&str] = &["consulte ", ""];
const DATE_BINDINGS: &[BindingSpec] = &[BindingSpec {
    slot_id: "ha:entity",
    role: "display",
    occurrence: 0,
    kind: BindingKind::Mention,
}];
const DATE_LITERALS: &[&str] = &["mostre a data em ", ""];
const TIME_BINDINGS: &[BindingSpec] = &[BindingSpec {
    slot_id: "ha:entity",
    role: "display",
    occurrence: 0,
    kind: BindingKind::Mention,
}];
const TIME_LITERALS: &[&str] = &["mostre a hora em ", ""];
const RESPOND_BINDINGS: &[BindingSpec] = &[BindingSpec {
    slot_id: "ha:response_text",
    role: "response",
    occurrence: 0,
    kind: BindingKind::Text,
}];
const RESPOND_LITERALS: &[&str] = &["responda ", ""];
const BROADCAST_BINDINGS: &[BindingSpec] = &[
    BindingSpec {
        slot_id: "ha:message",
        role: "message",
        occurrence: 0,
        kind: BindingKind::Text,
    },
    ENTITY_TARGET,
];
const BROADCAST_LITERALS: &[&str] = &["transmita ", " em ", ""];
const TEMPERATURE_BINDINGS: &[BindingSpec] = &[ENTITY_TARGET];
const TEMPERATURE_LITERALS: &[&str] = &["consulte a temperatura em ", ""];

pub(crate) const TEMPLATES: [TemplateSpec; TEMPLATE_COUNT] = [
    TemplateSpec {
        intent: "ha:hass_broadcast",
        capability: "ha:broadcast",
        operation: "ha:broadcast",
        shape: Shape::Single,
        bindings: BROADCAST_BINDINGS,
        literals: BROADCAST_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_cancel_all_timers",
        capability: "ha:timer_control",
        operation: "ha:cancel_all_timers",
        shape: Shape::Single,
        bindings: CANCEL_ALL_BINDINGS,
        literals: CANCEL_ALL_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_cancel_timer",
        capability: "ha:timer_control",
        operation: "ha:cancel_timer",
        shape: Shape::Single,
        bindings: CANCEL_TIMER_BINDINGS,
        literals: CANCEL_TIMER_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_climate_get_temperature",
        capability: "ha:temperature_query",
        operation: "ha:get_temperature",
        shape: Shape::Single,
        bindings: TEMPERATURE_BINDINGS,
        literals: TEMPERATURE_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_decrease_timer",
        capability: "ha:timer_control",
        operation: "ha:decrease_timer",
        shape: Shape::Single,
        bindings: DECREASE_TIMER_BINDINGS,
        literals: DECREASE_TIMER_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_get_current_date",
        capability: "ha:date_query",
        operation: "ha:get_current_date",
        shape: Shape::Single,
        bindings: DATE_BINDINGS,
        literals: DATE_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_get_current_time",
        capability: "ha:time_query",
        operation: "ha:get_current_time",
        shape: Shape::Single,
        bindings: TIME_BINDINGS,
        literals: TIME_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_get_state",
        capability: "ha:state_query",
        operation: "ha:get_state",
        shape: Shape::Single,
        bindings: GET_STATE_BINDINGS,
        literals: GET_STATE_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_increase_timer",
        capability: "ha:timer_control",
        operation: "ha:increase_timer",
        shape: Shape::Single,
        bindings: INCREASE_TIMER_BINDINGS,
        literals: INCREASE_TIMER_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_nevermind",
        capability: "ha:conversation_control",
        operation: "ha:nevermind",
        shape: Shape::Single,
        bindings: NEVERMIND_BINDINGS,
        literals: NEVERMIND_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_pause_timer",
        capability: "ha:timer_control",
        operation: "ha:pause_timer",
        shape: Shape::Single,
        bindings: PAUSE_TIMER_BINDINGS,
        literals: PAUSE_TIMER_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_respond",
        capability: "ha:response",
        operation: "ha:respond",
        shape: Shape::Single,
        bindings: RESPOND_BINDINGS,
        literals: RESPOND_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_set_position",
        capability: "ha:cover_control",
        operation: "ha:set_position",
        shape: Shape::Single,
        bindings: SET_POSITION_BINDINGS,
        literals: SET_POSITION_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_start_timer",
        capability: "ha:timer_control",
        operation: "ha:start_timer",
        shape: Shape::OrderedTimer,
        bindings: START_TIMER_BINDINGS,
        literals: START_TIMER_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_stop_moving",
        capability: "ha:cover_control",
        operation: "ha:stop_moving",
        shape: Shape::Single,
        bindings: STOP_MOVING_BINDINGS,
        literals: STOP_MOVING_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_timer_status",
        capability: "ha:timer_query",
        operation: "ha:timer_status",
        shape: Shape::Single,
        bindings: TIMER_STATUS_BINDINGS,
        literals: TIMER_STATUS_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_toggle",
        capability: "ha:fan_control",
        operation: "ha:toggle",
        shape: Shape::Single,
        bindings: TOGGLE_BINDINGS,
        literals: TOGGLE_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_turn_off",
        capability: "ha:switch_control",
        operation: "ha:turn_off",
        shape: Shape::Single,
        bindings: TURN_OFF_BINDINGS,
        literals: TURN_OFF_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_turn_on",
        capability: "ha:light_control",
        operation: "ha:turn_on",
        shape: Shape::ParallelTurnOn,
        bindings: TURN_ON_BINDINGS,
        literals: TURN_ON_LITERALS,
    },
    TemplateSpec {
        intent: "ha:hass_unpause_timer",
        capability: "ha:timer_control",
        operation: "ha:unpause_timer",
        shape: Shape::Single,
        bindings: UNPAUSE_TIMER_BINDINGS,
        literals: UNPAUSE_TIMER_LITERALS,
    },
];

pub(crate) fn lookup(intent: &IntentId) -> Option<&'static TemplateSpec> {
    TEMPLATES
        .binary_search_by_key(&intent.as_str(), |template| template.intent)
        .ok()
        .map(|index| &TEMPLATES[index])
}

pub(crate) fn validate() -> Result<()> {
    validate_template_count(TEMPLATES.len())?;
    let mut intents = BTreeSet::new();
    for template in TEMPLATES {
        IntentId::new(template.intent)
            .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))?;
        CapabilityId::new(template.capability)
            .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))?;
        OperationId::new(template.operation)
            .map_err(|_| PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration))?;
        if !intents.insert(template.intent)
            || template.literals.len() != template.bindings.len() + 1
        {
            return Err(PlanEngineError::new(
                PlanEngineErrorCode::InvalidStaticConfiguration,
            ));
        }
        let mut bindings = BTreeSet::new();
        for binding in template.bindings {
            SlotId::new(binding.slot_id).map_err(|_| {
                PlanEngineError::new(PlanEngineErrorCode::InvalidStaticConfiguration)
            })?;
            if binding.role.is_empty()
                || !bindings.insert((binding.slot_id, binding.role, binding.occurrence))
            {
                return Err(PlanEngineError::new(
                    PlanEngineErrorCode::InvalidStaticConfiguration,
                ));
            }
        }
    }
    Ok(())
}

fn validate_template_count(count: usize) -> Result<()> {
    if count > MAX_COMPOSITION_TEMPLATES {
        return Err(PlanEngineError::new(
            PlanEngineErrorCode::InvalidStaticConfiguration,
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn template_limit_accepts_exact_and_rejects_one_over() {
        assert_eq!(validate_template_count(MAX_COMPOSITION_TEMPLATES), Ok(()));
        assert_eq!(
            validate_template_count(MAX_COMPOSITION_TEMPLATES + 1)
                .expect_err("one-over template count must fail")
                .code(),
            PlanEngineErrorCode::InvalidStaticConfiguration
        );
    }
}
