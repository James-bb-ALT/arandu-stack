use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

pub const ACTIONS: &[&str] = &[
    "query",
    "turn_on",
    "turn_off",
    "percentage",
    "brightness",
    "color",
    "color_temperature",
    "temperature",
    "humidity",
    "hvac_mode",
    "fan_mode",
    "swing_mode",
    "preset",
    "effect",
    "oscillate",
    "direction",
    "play",
    "pause",
    "stop",
    "next",
    "previous",
    "volume",
    "mute",
    "unmute",
    "source",
    "music",
    "transfer",
    "seek",
    "shuffle_on",
    "shuffle_off",
    "repeat_one",
    "repeat_all",
    "repeat_off",
    "open",
    "close",
    "cover_stop",
    "position",
    "lock",
    "unlock",
    "activate",
    "press",
    "number",
    "select",
    "vacuum_start",
    "vacuum_stop",
    "vacuum_pause",
    "vacuum_dock",
    "vacuum_area",
    "mower_start",
    "mower_pause",
    "mower_dock",
    "arm",
    "disarm",
    "timer_start",
    "timer_cancel",
    "timer_pause",
    "timer_resume",
    "timer_finish",
    "timer_change",
    "calendar",
    "calendar_create",
    "forecast",
    "remote_key",
    "channel",
    "camera_view",
    "binding",
    "local_time",
    "local_date",
    "todo_add",
    "todo_list",
    "todo_complete",
    "automation_enable",
    "automation_disable",
    "automation_trigger",
    "todo_remove",
    "todo_update",
    "todo_remove_completed",
    "history_query",
    "contextual_undo",
    "cancel",
    "confirm",
    "repeat_response",
    "clock_alarm_create",
    "clock_alarm_list",
    "clock_alarm_query",
    "clock_alarm_cancel",
    "clock_alarm_snooze",
    "clock_alarm_dismiss",
    "reminder_create",
    "reminder_list",
    "reminder_query",
    "reminder_cancel",
    "reminder_snooze",
    "reminder_complete",
    "timer_query",
    "timer_list",
    "schedule_action",
    "scheduled_action_list",
    "scheduled_action_cancel",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Entity {
    pub registry_id: String,
    pub entity_id: String,
    pub domain: String,
    pub area_id: Option<String>,
    pub device_id: Option<String>,
    pub name: String,
    pub device_name: Option<String>,
    pub aliases: Vec<String>,
    pub device_class: Option<String>,
    pub actions: Vec<String>,
    #[serde(default)]
    pub attributes: BTreeMap<String, Value>,
    #[serde(default)]
    pub preferred: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Area {
    pub area_id: String,
    pub names: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub version: u8,
    pub generation: String,
    pub areas: Vec<Area>,
    pub entities: Vec<Entity>,
    #[serde(default)]
    pub groups: Vec<AreaGroup>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AreaGroup {
    pub group_id: String,
    pub names: Vec<String>,
    pub area_ids: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Parameters {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub old_item: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_item: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub period: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub order: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relative: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metric: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub aggregate: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state_filter: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub threshold: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub area: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub date: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub category: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub secondary_targets: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delay: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recurrence: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_action: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_domain: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub excluded_mentions: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub excluded_targets: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub excluded_device_ids: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Command {
    pub intent: String,
    pub action: String,
    pub domains: Vec<String>,
    pub mention: Option<String>,
    pub area: Option<String>,
    pub device_class: Option<String>,
    #[serde(default)]
    pub plural: bool,
    #[serde(default)]
    pub origin: bool,
    #[serde(default)]
    pub parameters: Parameters,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContextRequest {
    pub version: u8,
    pub generation: String,
    pub text: String,
    #[serde(default)]
    pub origin_area: Option<String>,
    /// Additive v4 provenance; absent means untrusted for implicit commands.
    #[serde(default)]
    pub origin_area_source: Option<String>,
    #[serde(default)]
    pub last_area: Option<String>,
    #[serde(default)]
    pub last_targets: Vec<String>,
    #[serde(default)]
    pub pending: Option<Command>,
    #[serde(default)]
    pub antecedent: Option<Antecedent>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Antecedent {
    pub generation: String,
    pub action: String,
    pub targets: Vec<String>,
    pub parameters: Parameters,
}

impl ContextRequest {
    pub fn valid(&self) -> bool {
        self.version == 4
            && self.generation.len() == 64
            && !self.text.is_empty()
            && self.text.len() <= 2_048
            && self.text.chars().count() <= 512
            && !self.text.chars().any(char::is_control)
            && self.last_targets.len() <= 32
            && self.antecedent.as_ref().is_none_or(|prior| {
                prior.generation == self.generation
                    && prior.targets.len() == 1
                    && prior.targets[0].len() <= 128
                    && matches!(
                        prior.action.as_str(),
                        "turn_on"
                            | "turn_off"
                            | "brightness"
                            | "percentage"
                            | "temperature"
                            | "volume"
                            | "mute"
                            | "unmute"
                    )
                    && prior.parameters.relative.is_none()
                    && serde_json::to_vec(&prior.parameters).is_ok_and(|raw| raw.len() <= 1024)
            })
            && self.origin_area_source.as_deref().is_none_or(|source| {
                matches!(source, "satellite" | "device" | "default" | "unknown")
            })
            && self
                .pending
                .as_ref()
                .is_none_or(|command| ACTIONS.contains(&command.action.as_str()))
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub intent: String,
    pub action: String,
    pub targets: Vec<String>,
    pub parameters: Parameters,
    pub evidence: Vec<String>,
    pub depends_on: Vec<usize>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SelectionOption {
    pub key: String,
    pub targets: Vec<String>,
    pub area: Option<String>,
    pub evidence: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Response {
    pub version: u8,
    pub status: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub operations: Vec<Operation>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<Command>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub intent: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub generation: Option<String>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub timings: BTreeMap<String, f64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub options: Vec<SelectionOption>,
}

impl Response {
    #[must_use]
    pub fn status(status: &str) -> Self {
        Self {
            version: 4,
            status: status.to_owned(),
            operations: Vec::new(),
            candidates: Vec::new(),
            command: None,
            reason: matches!(status, "invalid_request" | "no_match" | "stale")
                .then(|| status.to_owned()),
            intent: None,
            generation: None,
            timings: BTreeMap::new(),
            options: Vec::new(),
        }
    }
    #[must_use]
    pub fn failure(status: &str, intent: &str, reason: &str) -> Self {
        Self {
            intent: Some(intent.to_owned()),
            reason: Some(reason.to_owned()),
            ..Self::status(status)
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct SlotState {
    pub value: Value,
    pub status: String,
    #[serde(default)]
    pub evidence: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<Value>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct ClauseConstraints {
    #[serde(default)]
    pub negated: bool,
    #[serde(default)]
    pub excluded_targets: Vec<String>,
    #[serde(default)]
    pub conditional: bool,
    #[serde(default)]
    pub quoted_content: bool,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct PartialClause {
    pub clause_id: usize,
    pub original_text: String,
    pub slots: BTreeMap<String, SlotState>,
    #[serde(default)]
    pub allowed_actions: Vec<String>,
    #[serde(default)]
    pub constraints: ClauseConstraints,
    #[serde(default)]
    pub unresolved: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct OriginContext {
    #[serde(default)]
    pub area_id: Option<String>,
    pub source: String,
    #[serde(default)]
    pub context_id: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PartialRequest {
    pub version: u8,
    pub request_id: String,
    pub generation: String,
    #[serde(default)]
    pub identity_scope_hash: String,
    pub text: String,
    #[serde(default)]
    pub origin_area: Option<String>,
    #[serde(default)]
    pub origin_area_source: Option<String>,
    #[serde(default)]
    pub origin_context_id: Option<String>,
    #[serde(default)]
    pub last_area: Option<String>,
    #[serde(default)]
    pub last_targets: Vec<String>,
    #[serde(default)]
    pub pending: Option<Command>,
    #[serde(default)]
    pub antecedent: Option<Antecedent>,
}

impl PartialRequest {
    #[must_use]
    pub fn valid(&self) -> bool {
        !self.request_id.is_empty()
            && self.request_id.len() <= 128
            && self.version == 5
            && self.to_v4().valid()
    }

    #[must_use]
    pub fn to_v4(&self) -> ContextRequest {
        ContextRequest {
            version: 4,
            generation: self.generation.clone(),
            text: self.text.clone(),
            origin_area: self.origin_area.clone(),
            origin_area_source: self.origin_area_source.clone(),
            last_area: self.last_area.clone(),
            last_targets: self.last_targets.clone(),
            pending: self.pending.clone(),
            antecedent: self.antecedent.clone(),
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct PartialUnderstanding {
    pub version: u8,
    pub request_id: String,
    pub catalog_generation: String,
    pub identity_scope_hash: String,
    pub origin: OriginContext,
    pub request_kind: String,
    pub clauses: Vec<PartialClause>,
    #[serde(default)]
    pub safety_flags: Vec<String>,
    #[serde(default)]
    pub literal_segments: Vec<String>,
    pub criticality: String,
    pub freshness: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub v4_outcome: Option<Response>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub timings: BTreeMap<String, f64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProposalRepair {
    pub clause_id: usize,
    pub slot: String,
    pub value: Value,
    pub source: String,
    #[serde(default)]
    pub evidence: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReviewProposalRequest {
    pub version: u8,
    pub partial: PartialUnderstanding,
    pub repairs: Vec<ProposalRepair>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReviewProposalResponse {
    pub version: u8,
    pub accepted: bool,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub operations: Vec<Operation>,
    pub requires_confirmation: bool,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub timings: BTreeMap<String, f64>,
}
